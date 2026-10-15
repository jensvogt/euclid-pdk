"""EAP, end to end against a fake euclid server.

Every action is one request, so what these check is that it carries the fields the server reads and
parses the ones it answers with. Two of those are worth more attention than the rest: a deployment
names a bucket and an artifact while the application that comes back describes ERNs, and an update
sends only what it was given - where sending an empty list of buckets does not mean "leave them
alone" but "revoke them".
"""

from __future__ import annotations

import pytest

from euclid import Euclid, EuclidServiceError
from euclid.modules.eap import (BINARY, DEBUG, JAVA, JAVA21, JAVA25, JOB, NODEJS, PROCESS,
                                PYTHON)
from test_eam import prepared

APPLICATION = {
    "applicationId": "order-service", "ern": "ern:eap:application/order-service",
    "accountId": "000000000000", "region": "eu-central-1", "namespace": "development",
    "runtimeName": "order-service-7f3a", "runtime": "JAVA",
    "bucketErn": "ern:esm:bucket/artifacts", "artifactKey": "order-service-1.4.0.jar",
    "version": "1.4.0", "md5Sum": "d41d8cd98f00b204e9800998ecf8427e", "command": "",
    "arguments": ["--server.port=0"], "environment": {"TZ": "Europe/Berlin"},
    "resources": ["ern:eqs:queue/orders"], "userId": "app-order-service", "logLevel": "",
    "minInstances": 2, "maxInstances": 5, "readyTimeoutMs": 30000,
    "desiredState": "RUNNING", "state": "RUNNING", "instances": 2,
    "endpoints": [{"instanceId": "i-1", "pid": 4711, "httpPort": 34567},
                  {"instanceId": "i-2", "pid": 4712, "httpPort": 34568}],
    "created": "2026-09-01", "modified": "2026-09-10"}


@pytest.fixture
def eap(gateway):
    """An EAP client on a logged-in session, closed with it."""
    prepared(gateway)
    with Euclid.for_server(gateway.base_url).login("jens", "secret") as session:
        yield session.eap()


# -- deploying ------------------------------------------------------------------------------------


def test_deploying_names_the_bucket_and_the_artifact(gateway, eap):
    """Names are what an operator has in hand; the application that comes back describes the ERNs
    euclid resolved them into."""
    gateway.answer("eap", "create-application", dict(APPLICATION, desiredState="STOPPED",
                                                     state="STOPPED", instances=0, endpoints=[]))

    application = eap.create_application(
        "order-service", JAVA, bucket="artifacts", artifact="order-service-1.4.0.jar",
        version="1.4.0", arguments=["--server.port=0"], environment={"TZ": "Europe/Berlin"},
        queues=["orders"], min_instances=2, max_instances=5)

    assert gateway.last().json() == {
        "applicationId": "order-service", "runtime": "JAVA", "bucket": "artifacts",
        "artifact": "order-service-1.4.0.jar", "version": "1.4.0", "command": "",
        "arguments": ["--server.port=0"], "environment": {"TZ": "Europe/Berlin"},
        "buckets": [], "queues": ["orders"], "user": "", "minInstances": 2, "maxInstances": 5,
        "readyTimeoutMs": 30000}

    assert application.bucket_ern == "ern:esm:bucket/artifacts"
    assert application.artifact_key == "order-service-1.4.0.jar"
    assert application.resources == ["ern:eqs:queue/orders"]
    # Nothing runs yet: a new application is stopped until somebody starts it.
    assert (application.desired_state, application.state) == ("STOPPED", "STOPPED")
    assert not application.is_running


def test_an_application_without_a_named_user_runs_as_one_euclid_made(gateway, eap):
    """No password, no login, one access key - so nothing an application leaks is a person's."""
    gateway.answer("eap", "create-application", APPLICATION)

    application = eap.create_application("order-service", PYTHON, "artifacts", "app.py")

    assert gateway.last().json()["user"] == ""
    assert application.user_id == "app-order-service"


def test_a_copy_names_the_target_namespace(gateway, eap):
    gateway.answer("eap", "copy-application", APPLICATION)

    eap.copy_application("order-service", "production")
    assert gateway.last().json() == {"applicationId": "order-service",
                                     "targetNamespace": "production"}

    # Absent rather than empty when unnamed: the server reads an absent targetApplicationId as
    # "the original's name", so sending "" would be asking for an application with no name.
    eap.copy_application("order-service", "development",
                         target_application_id="order-service-next")
    assert gateway.last().json() == {"applicationId": "order-service",
                                     "targetNamespace": "development",
                                     "targetApplicationId": "order-service-next"}


def test_scaling_sends_only_the_bound_it_was_given(gateway, eap):
    gateway.answer("eap", "scale-application", APPLICATION)

    # A ceiling raised on its own must not carry a floor with it - an absent bound is what tells
    # the server to leave that one as it stands.
    eap.scale_application("order-service", max_instances=16)
    assert gateway.last().json() == {"applicationId": "order-service", "maxInstances": 16}

    eap.scale_application("order-service", min_instances=4)
    assert gateway.last().json() == {"applicationId": "order-service", "minInstances": 4}

    # Both together pins the pool, which is a normal thing to ask for.
    eap.scale_application("order-service", min_instances=2, max_instances=2)
    assert gateway.last().json() == {"applicationId": "order-service",
                                     "minInstances": 2, "maxInstances": 2}


def test_an_update_sends_only_what_it_was_given(gateway, eap):
    gateway.answer("eap", "update-application", APPLICATION)

    eap.update_application("order-service", min_instances=3)
    assert gateway.last().json() == {"applicationId": "order-service", "minInstances": 3}

    # An empty command is a value here - it hands the artifact back to the runtime's interpreter -
    # so it has to be sendable, which None is what says "leave it alone".
    eap.update_application("order-service", command="")
    assert gateway.last().json() == {"applicationId": "order-service", "command": ""}

    eap.update_application("order-service", runtime=JAVA, artifact="order-service-1.5.0.jar",
                           version="1.5.0", arguments=["-Xmx512m"], environment={},
                           ready_timeout_ms=60000, namespace="production")
    assert gateway.last().json() == {
        "applicationId": "order-service", "runtime": "JAVA", "artifact": "order-service-1.5.0.jar",
        "version": "1.5.0", "arguments": ["-Xmx512m"], "environment": {},
        "readyTimeoutMs": 60000, "namespace": "production"}


def test_resources_are_left_out_of_an_update_that_does_not_mention_them(gateway, eap):
    """The server re-resolves buckets and queues together whenever either is named, so sending an
    empty pair by default would revoke everything the application was granted."""
    gateway.answer("eap", "update-application", APPLICATION)

    eap.update_application("order-service", min_instances=3)
    assert "buckets" not in gateway.last().json()
    assert "queues" not in gateway.last().json()

    eap.update_application("order-service", buckets=["artifacts"], queues=["orders"])
    assert gateway.last().json()["buckets"] == ["artifacts"]
    assert gateway.last().json()["queues"] == ["orders"]

    # Naming them empty is how they are revoked deliberately.
    eap.update_application("order-service", buckets=[], queues=[])
    assert gateway.last().json() == {"applicationId": "order-service", "buckets": [], "queues": []}


def test_redeploying_defaults_to_the_artifact_already_deployed(gateway, eap):
    """Which is what a rebuilt artifact stored under the same key wants."""
    gateway.answer("eap", "redeploy-application", dict(APPLICATION, version="1.5.0"))

    eap.redeploy_application("order-service")
    assert gateway.last().json() == {"applicationId": "order-service"}

    redeployed = eap.redeploy_application("order-service", artifact="order-service-1.5.0.jar",
                                          version="1.5.0")
    assert gateway.last().json() == {"applicationId": "order-service",
                                     "artifact": "order-service-1.5.0.jar", "version": "1.5.0"}
    assert redeployed.version == "1.5.0"


def test_a_redeploy_that_would_change_nothing_is_refused(gateway, eap):
    """It would restart the instances for nothing, and usually means the new artifact never reached
    the bucket."""
    gateway.answer("eap", "redeploy-application",
                   {"error": "Already at version 1.4.0 with the same artifact"}, status=409)

    with pytest.raises(EuclidServiceError) as raised:
        eap.redeploy_application("order-service")

    assert raised.value.status == 409
    assert raised.value.reason.startswith("Already at version")


# -- running --------------------------------------------------------------------------------------


def test_an_application_is_identified_by_its_namespace_as_well_as_its_id(gateway, eap):
    """An applicationId is unique within an account and a namespace rather than across the
    installation, so the ID alone does not say which application this is."""
    gateway.answer("eap", "get-application", APPLICATION)

    application = eap.get_application("order-service")

    assert application.namespace == "development"
    # What the process, its socket and its log channel are named after - which is not the ID,
    # because none of those has a namespace to live in.
    assert application.runtime_name == "order-service-7f3a"


def test_moving_an_application_to_another_namespace(gateway, eap):
    """A namespace is part of what identifies an application, so naming one is a move - and it is
    where the buckets and queues it may reach are resolved."""
    gateway.answer("eap", "update-application", dict(APPLICATION, namespace="production"))

    moved = eap.update_application("order-service", namespace="production")

    assert gateway.last().json() == {"applicationId": "order-service", "namespace": "production"}
    assert moved.namespace == "production"


def test_starting_asks_rather_than_waits(gateway, eap):
    """The desired state changes here and the manager acts on it, so what comes back says what was
    asked for rather than what has happened."""
    gateway.answer("eap", "start-application", dict(APPLICATION, desiredState="RUNNING",
                                                    state="STOPPED", instances=0, endpoints=[]))
    gateway.answer("eap", "stop-application", dict(APPLICATION, desiredState="STOPPED"))

    started = eap.start_application("order-service")
    assert gateway.last().json() == {"applicationId": "order-service"}
    assert (started.desired_state, started.state) == ("RUNNING", "STOPPED")
    assert not started.is_running

    stopped = eap.stop_application("order-service")
    assert stopped.desired_state == "STOPPED"
    # Still answering, which is the ordinary picture of an application on its way down.
    assert stopped.is_running


def test_restarting_cycles_the_pool_without_changing_the_desired_state(gateway, eap):
    """The one way to have every instance start again that does not leave the application stopped
    if the caller goes away between two calls."""
    gateway.answer("eap", "restart-application",
                   {"applicationId": "order-service", "restarting": True, "instances": 3})

    restarted = eap.restart_application("order-service")

    assert gateway.last().json() == {"applicationId": "order-service"}
    assert restarted.restarting
    assert restarted.application_id == "order-service"
    # What is running when the request is answered: the manager has not stopped anything yet.
    assert restarted.instances == 3


def test_applying_a_declaration_names_what_it_created_and_what_it_removed(gateway, eap):
    gateway.answer("eap", "apply-infrastructure", {
        "applicationId": "order-service", "declared": True,
        "created": ["ern:eqs:eu-central-1:000000000000:development:queue:orders"],
        "deleted": ["ern:eqs:eu-central-1:000000000000:development:queue:retired"],
        "granted": ["access-queue-consume"], "revoked": ["access-queue-produce"]})

    applied = eap.apply_infrastructure("order-service")

    assert gateway.last().json() == {"applicationId": "order-service"}
    assert applied.declared
    assert applied.created == ["ern:eqs:eu-central-1:000000000000:development:queue:orders"]
    # The half worth reading before trusting a declaration: a reconcile is full, so a resource this
    # application created and the file no longer names is gone, and took its messages with it. Named
    # rather than counted, so a removal nobody intended is visible in the answer.
    assert applied.deleted == ["ern:eqs:eu-central-1:000000000000:development:queue:retired"]
    assert applied.granted == ["access-queue-consume"]
    assert applied.revoked == ["access-queue-produce"]


def test_an_application_with_no_declaration_is_answered_rather_than_refused(gateway, eap):
    """Not an error: an application that provisions its resources by hand reads this way every time,
    and the four lists come back empty rather than absent."""
    gateway.answer("eap", "apply-infrastructure", {"applicationId": "order-service", "declared": False})

    applied = eap.apply_infrastructure("order-service")

    assert not applied.declared
    assert applied.created == []
    assert applied.deleted == []
    assert applied.granted == []
    assert applied.revoked == []


def test_an_application_reports_the_instances_answering_for_it(gateway, eap):
    gateway.answer("eap", "get-application", APPLICATION)

    application = eap.get_application("order-service")

    assert gateway.last().json() == {"applicationId": "order-service"}
    assert application.instances == 2
    assert [(e.instance_id, e.http_port) for e in application.endpoints] == [("i-1", 34567),
                                                                             ("i-2", 34568)]
    assert application.endpoints[0].pid == 4711
    assert application.environment == {"TZ": "Europe/Berlin"}


def test_listing_and_deleting_applications(gateway, eap):
    gateway.answer("eap", "list-applications", {"applications": [APPLICATION,
                                                                 {"applicationId": "reports"}]})
    gateway.answer("eap", "delete-application", {})

    applications = eap.list_applications("order")
    assert gateway.last().json() == {"prefix": "order"}
    assert [a.application_id for a in applications] == ["order-service", "reports"]
    # A field the server did not send reads as empty rather than raising.
    assert applications[1].endpoints == [] and applications[1].min_instances == 0

    assert eap.list_applications() == applications
    assert gateway.last().json() == {"prefix": ""}

    eap.delete_application("order-service")
    assert gateway.last().json() == {"applicationId": "order-service"}


# -- logging --------------------------------------------------------------------------------------


def test_setting_and_taking_back_a_log_level(gateway, eap):
    gateway.answer("eap", "set-log-level", {"applicationId": "order-service", "logLevel": "debug",
                                            "channel": "application.order-service"})

    result = eap.set_log_level("order-service", DEBUG)
    assert gateway.last().json() == {"applicationId": "order-service", "level": "debug"}
    assert (result.log_level, result.channel) == ("debug", "application.order-service")

    # An empty level removes the override rather than setting one, so the application follows the
    # installation's configuration as it changes from here on.
    gateway.answer("eap", "set-log-level", {"applicationId": "order-service", "logLevel": "",
                                            "channel": "application.order-service"})
    reset = eap.reset_log_level("order-service")
    assert gateway.last().json() == {"applicationId": "order-service", "level": ""}
    assert reset.log_level == ""


def test_an_unrecognised_level_is_refused_by_the_server(gateway, eap):
    """Refused rather than defaulted: "warnign" quietly meaning "info" is an application logging
    more than somebody asked for."""
    gateway.answer("eap", "set-log-level",
                   {"error": 'level must be "trace", "debug", "info", "warning", "error", '
                             '"fatal" or "off": warnign'}, status=400)

    with pytest.raises(EuclidServiceError) as raised:
        eap.set_log_level("order-service", "warnign")

    assert raised.value.status == 400


# -- everything else ----------------------------------------------------------------------------------


def test_eap_is_signed_and_follows_the_session(gateway):
    prepared(gateway)
    gateway.answer("eam", "change-namespace", {})
    gateway.answer("eap", "list-applications", {"applications": []})

    with Euclid.for_server(gateway.base_url).login("jens", "secret") as session:
        eap = session.eap()
        eap.list_applications()
        assert gateway.last().auth == "sigv4"
        assert gateway.last().headers["x-euclid-target"] == "eap"

        session.change_namespace("development")
        eap.list_applications()
        assert gateway.last().headers["x-euclid-namespace"] == "development"

        assert session.eap() is eap


def test_an_administrator_only_action_says_who_refused_it(gateway, eap):
    gateway.answer("eap", "create-application", {"error": "Administrator rights required"},
                   status=403)

    with pytest.raises(EuclidServiceError) as raised:
        eap.create_application("order-service", JAVA, "artifacts", "app.jar")

    assert (raised.value.target, raised.value.action, raised.value.status) == ("eap",
                                                                               "create-application", 403)
    assert raised.value.reason == "Administrator rights required"


def test_metrics_and_call(gateway, eap):
    gateway.answer("eap", "get-metrics", {"items": [{"name": "eap-instances", "value": 2}]})
    gateway.answer("eap", "some-future-action", {"ok": True})

    assert eap.metrics() == {"items": [{"name": "eap-instances", "value": 2}]}
    assert eap.call("some-future-action", {"x": 1}) == {"ok": True}
    assert gateway.last().json() == {"x": 1}


def test_the_runtime_constants_are_the_strings_eap_accepts():
    # Spelled by hand rather than derived, because the server matches them exactly and refuses
    # anything else with a 400. A constant that drifted to "JAVA-21" would still read perfectly in
    # calling code and fail only against a running installation.
    assert (JAVA, JAVA21, JAVA25, PYTHON, NODEJS, BINARY) == (
        "JAVA", "JAVA21", "JAVA25", "PYTHON", "NODEJS", "BINARY")

    # Three distinct runtimes, not one with aliases: a jar built for 25 does not start on 21, so
    # asking for one and getting the other is the failure these exist to prevent.
    assert len({JAVA, JAVA21, JAVA25}) == 3


# -- nodes ----------------------------------------------------------------------------------
#
# A node is not configured; it registers itself, renews, and is forgotten when it stops renewing.
# So what these check is mostly that the two ways of being unavailable stay apart: "live" is
# whether the lease is current, "drained" is whether somebody asked it to take nothing new.


NODE = {"name": "box-1", "address": "10.0.0.7:9100", "principal": "ern:eam:user/worker-1",
        "labels": {"zone": "a", "class": "compute"}, "cpuCount": 16, "version": "1.0.73",
        "os": "linux", "arch": "x86_64", "drained": False, "live": True,
        "lastSeen": "2026-10-13T09:15:00Z"}


def test_listing_nodes_leaves_out_what_each_is_running(gateway, eap):
    """That is get_node's question: what a node holds is read back out of every pool's instance
    list, which is a pass worth making for one host and not for all of them."""
    gateway.answer("eap", "list-nodes", {"nodes": [NODE, {"name": "box-2"}], "total": 2})

    result = eap.list_nodes()

    assert gateway.last().json() == {}
    assert result.total == 2
    assert [node.name for node in result.nodes] == ["box-1", "box-2"]
    assert result.nodes[0].labels == {"zone": "a", "class": "compute"}
    assert result.nodes[0].cpu_count == 16
    assert result.nodes[0].applications == []
    # A field the server did not send reads as empty rather than raising.
    assert (result.nodes[1].address, result.nodes[1].live) == ("", False)


def test_a_node_says_what_it_holds_and_what_is_serving(gateway, eap):
    """Both counts, because the gap is the thing worth seeing: four slots and nothing running is a
    node whose instances will not start, and one number cannot say so."""
    gateway.answer("eap", "get-node", dict(NODE, applications=[
        {"applicationId": "order-service", "runtimeName": "order-service-7f3a",
         "namespace": "development", "runtime": "JAVA21", "instances": 4, "running": 0},
    ]))

    node = eap.get_node("box-1")

    assert gateway.last().json() == {"node": "box-1"}
    assert len(node.applications) == 1
    held = node.applications[0]
    assert (held.application_id, held.instances, held.running) == ("order-service", 4, 0)
    assert held.runtime_name == "order-service-7f3a"


def test_live_and_drained_are_different_questions(gateway, eap):
    """A drained node is still there and still running what it has; it simply takes nothing new."""
    gateway.answer("eap", "get-node", dict(NODE, live=True, drained=True))

    assert eap.get_node("box-1").is_available is False

    # And a node that stopped renewing is unavailable without anybody having asked for it.
    gateway.answer("eap", "get-node", dict(NODE, live=False, drained=False))
    node = eap.get_node("box-1")
    assert (node.live, node.drained, node.is_available) == (False, False, False)


def test_draining_a_node_and_putting_it_back(gateway, eap):
    gateway.answer("eap", "drain-node", {"node": "box-1", "drained": True})

    assert eap.drain_node("box-1").drained is True
    assert gateway.last().json() == {"node": "box-1", "drained": True}

    # Sent rather than left out, because false is the instruction to take it back into service.
    gateway.answer("eap", "drain-node", {"node": "box-1", "drained": False})
    assert eap.drain_node("box-1", drained=False).drained is False
    assert gateway.last().json() == {"node": "box-1", "drained": False}


def test_deleting_a_node_frees_the_name_without_stopping_anything(gateway, eap):
    """A worker still running finds itself unregistered at its next renewal and registers again -
    which is why drain_node, not this, is how a host is taken out of service."""
    gateway.answer("eap", "delete-node", {"node": "box-1", "deleted": True})

    result = eap.delete_node("box-1")

    assert gateway.last().json() == {"node": "box-1"}
    assert (result.node, result.deleted) == ("box-1", True)
    # One shape for both node answers, so the field the server did not send reads false.
    assert result.drained is False


def test_deleting_a_node_is_administrator_only(gateway, eap):
    """Unlike the other node actions. A worker able to delete another's registration could
    re-register under its name and be handed its assignments and credentials."""
    gateway.answer("eap", "delete-node", {"error": "Administrator privileges required"}, status=403)

    with pytest.raises(EuclidServiceError) as raised:
        eap.delete_node("box-1")

    assert raised.value.status == 403


def test_a_node_that_was_never_registered(gateway, eap):
    gateway.answer("eap", "get-node", {"error": "Node is not registered, node: box-9"}, status=404)

    with pytest.raises(EuclidServiceError) as raised:
        eap.get_node("box-9")

    assert raised.value.status == 404
    assert "not registered" in raised.value.reason


# -- jobs -----------------------------------------------------------------------------------------
#
# What an application *is*, as opposed to what it is written in. A job runs once and finishes, so
# the two things worth checking are that the fields are left out when nothing was said about them -
# an older server must not be handed one - and that a schedule cannot be set on a process.


def test_deploying_a_job_on_a_schedule(gateway, eap):
    gateway.answer("eap", "create-application", dict(
        APPLICATION, applicationId="nightly-report", type="JOB", schedule="0 2 * * *",
        nextRunAt="2026-10-16T02:00:00Z", state="STOPPED", instances=0, endpoints=[]))

    application = eap.create_application("nightly-report", JAVA21, "artifacts", "report.jar",
                                         type=JOB, schedule="0 2 * * *")

    sent = gateway.last().json()
    assert (sent["type"], sent["schedule"]) == ("JOB", "0 2 * * *")
    assert (application.type, application.schedule) == ("JOB", "0 2 * * *")
    assert application.next_run_at == "2026-10-16T02:00:00Z"
    assert application.is_job and application.is_scheduled
    # A finished job is STOPPED, which on a process would mean something had gone wrong.
    assert application.is_running is False


def test_a_deployment_that_says_nothing_about_the_type_sends_nothing(gateway, eap):
    """So that an installation older than these fields is not handed one, and the type it already
    defaults to is the one that applies."""
    gateway.answer("eap", "create-application", APPLICATION)

    eap.create_application("order-service", JAVA, "artifacts", "order-service.jar")

    sent = gateway.last().json()
    assert "type" not in sent and "schedule" not in sent


def test_a_schedule_without_a_job_says_so_before_the_round_trip(gateway, eap):
    """A schedule on something that stays up is a setting that would never fire."""
    with pytest.raises(ValueError, match="only valid on a JOB"):
        eap.create_application("order-service", JAVA, "artifacts", "o.jar",
                               schedule="0 2 * * *")

    assert [r for r in gateway.requests if r.action == "create-application"] == []


def test_an_update_sends_an_empty_schedule_because_that_unschedules(gateway, eap):
    """Unlike a create, where an empty one means "nothing to say"."""
    gateway.answer("eap", "update-application", dict(APPLICATION, type="JOB", schedule=""))

    updated = eap.update_application("nightly-report", schedule="")

    assert gateway.last().json() == {"applicationId": "nightly-report", "schedule": ""}
    assert updated.is_job and not updated.is_scheduled


def test_an_update_can_turn_a_process_into_a_job(gateway, eap):
    gateway.answer("eap", "update-application", dict(APPLICATION, type="JOB"))

    assert eap.update_application("order-service", type=JOB).is_job

    assert gateway.last().json() == {"applicationId": "order-service", "type": "JOB"}


def test_a_type_the_server_does_not_recognise_is_refused_there(gateway, eap):
    """Rather than read as PROCESS: "job" in the wrong case would otherwise deploy something that
    stays up forever, which is the opposite of what was asked for."""
    gateway.answer("eap", "create-application",
                   {"error": "type must be PROCESS or JOB, not 'job'"}, status=400)

    with pytest.raises(EuclidServiceError) as raised:
        eap.create_application("nightly", JAVA, "artifacts", "n.jar", type="job")

    assert raised.value.status == 400
    assert "PROCESS or JOB" in raised.value.reason


def test_an_application_with_no_schedule_reports_no_next_run(gateway, eap):
    """Empty rather than the epoch, which would read as a job fifty years overdue."""
    gateway.answer("eap", "get-application", dict(APPLICATION, type="PROCESS"))

    application = eap.get_application("order-service")

    assert (application.schedule, application.next_run_at) == ("", "")
    assert not application.is_job and not application.is_scheduled


def test_the_application_type_constants_are_the_strings_eap_accepts():
    assert (PROCESS, JOB) == ("PROCESS", "JOB")
