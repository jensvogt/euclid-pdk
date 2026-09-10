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
from euclid.modules.eap import DEBUG, JAVA, PYTHON
from test_eam import prepared

APPLICATION = {
    "applicationId": "order-service", "ern": "ern:eap:application/order-service",
    "accountId": "000000000000", "region": "eu-central-1", "runtime": "JAVA",
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
