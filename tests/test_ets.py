"""ETS, end to end against a fake euclid server.

Every action is one request, so what these check is that it carries the fields the server reads and
parses the ones it answers with - and, as in EAP, that an update sends only what it names, since a
list that is sent replaces the stored one rather than adding to it.
"""

from __future__ import annotations

import inspect

import pytest

from euclid import Euclid, EuclidServiceError
from euclid.modules.ets import EVERY_INTERFACE, FTP, SFTP
from test_eam import prepared

SERVER = {"serverId": "partner-drop", "ern": "ern:ets:server/partner-drop",
          "accountId": "000000000000", "region": "eu-central-1", "namespace": "development",
          "runtimeName": "partner-drop-91b2", "protocol": "SFTP",
          "address": "0.0.0.0", "port": 2222, "bucketName": "invoices",
          "bucketErn": "ern:esm:bucket/invoices", "homeDirectory": "incoming/",
          "userIds": ["jens"], "userGroups": ["partners"], "directories": ["incoming/2026/"],
          "desiredState": "RUNNING", "state": "RUNNING", "hostKey": "/etc/euclid/partner-drop.key",
          "pasvMin": 6000, "pasvMax": 6100, "created": "2026-09-01", "modified": "2026-09-10"}


@pytest.fixture
def ets(gateway):
    """An ETS client on a logged-in session, closed with it."""
    prepared(gateway)
    with Euclid.for_server(gateway.base_url).login("jens", "secret") as session:
        yield session.ets()


# -- definitions ----------------------------------------------------------------------------------


def test_defining_a_server_names_the_bucket_by_name(gateway, ets):
    """A name is what an operator has in hand; the definition that comes back describes the ERN
    euclid resolved it into, exactly as a deployment does."""
    gateway.answer("ets", "create-server", dict(SERVER, desiredState="STOPPED", state="STOPPED"))

    server = ets.create_server("partner-drop", bucket="invoices", port=2222,
                               home_directory="incoming/", user_groups=["partners"],
                               user_ids=["jens"], directories=["incoming/2026/"])

    assert gateway.last().json() == {
        "serverId": "partner-drop", "protocol": "SFTP", "port": 2222, "bucket": "invoices",
        "address": "0.0.0.0", "homeDirectory": "incoming/", "userIds": ["jens"],
        "userGroups": ["partners"], "directories": ["incoming/2026/"], "hostKey": "",
        "pasvMin": 6000, "pasvMax": 6100}

    assert (server.bucket_name, server.bucket_ern) == ("invoices", "ern:esm:bucket/invoices")
    assert server.home_directory == "incoming/"
    # Nothing listens yet: a new definition is stopped until somebody starts it.
    assert (server.desired_state, server.state) == ("STOPPED", "STOPPED")
    assert not server.is_running


def test_an_ftp_server_carries_a_passive_port_range(gateway, ets):
    """Whatever sits in front of euclid has to let those through as well as the control port, which
    is what makes the range worth naming rather than leaving to chance."""
    gateway.answer("ets", "create-server", dict(SERVER, protocol="FTP", pasvMin=7000, pasvMax=7099))

    server = ets.create_server("drop", bucket="invoices", port=2121, protocol=FTP,
                               address="10.0.0.5", pasv_min=7000, pasv_max=7099)

    assert gateway.last().json()["protocol"] == "FTP"
    assert gateway.last().json()["address"] == "10.0.0.5"
    assert (gateway.last().json()["pasvMin"], gateway.last().json()["pasvMax"]) == (7000, 7099)
    assert (server.pasv_min, server.pasv_max) == (7000, 7099)


def test_an_sftp_host_key_is_left_for_the_server_to_generate(gateway, ets):
    gateway.answer("ets", "create-server", dict(SERVER, hostKey=""))

    ets.create_server("partner-drop", bucket="invoices", port=2222, protocol=SFTP)
    assert gateway.last().json()["hostKey"] == ""

    # Unless a key clients already trust has to be kept.
    ets.create_server("partner-drop", bucket="invoices", port=2222,
                      host_key="/etc/euclid/partner-drop.key")
    assert gateway.last().json()["hostKey"] == "/etc/euclid/partner-drop.key"


def test_an_update_sends_only_what_it_was_given(gateway, ets):
    gateway.answer("ets", "update-server", SERVER)

    ets.update_server("partner-drop", port=2223)
    assert gateway.last().json() == {"serverId": "partner-drop", "port": 2223}

    # A named list replaces the stored one rather than adding to it.
    ets.update_server("partner-drop", user_groups=["partners", "auditors"])
    assert gateway.last().json() == {"serverId": "partner-drop",
                                     "userGroups": ["partners", "auditors"]}

    # An empty home directory is a value - it puts logins back at the root of the bucket - so it
    # has to be sendable, which is what None being "leave it alone" is for.
    ets.update_server("partner-drop", home_directory="")
    assert gateway.last().json() == {"serverId": "partner-drop", "homeDirectory": ""}

    ets.update_server("partner-drop", address=EVERY_INTERFACE, bucket="archive",
                      user_ids=[], directories=["incoming/"], host_key="/etc/euclid/new.key",
                      pasv_min=7000, pasv_max=7099)
    assert gateway.last().json() == {
        "serverId": "partner-drop", "address": "0.0.0.0", "bucket": "archive", "userIds": [],
        "directories": ["incoming/"], "hostKey": "/etc/euclid/new.key", "pasvMin": 7000,
        "pasvMax": 7099}


def test_the_protocol_is_not_updatable(gateway, ets):
    """Which one a server speaks decides which process runs it, so changing it would be a different
    server - and the SDK has no parameter for it rather than sending one the server ignores."""
    assert "protocol" not in inspect.signature(ets.update_server).parameters


def test_getting_listing_and_deleting(gateway, ets):
    gateway.answer("ets", "get-server", SERVER)
    gateway.answer("ets", "list-servers", {"servers": [SERVER, {"serverId": "archive-drop"}]})
    gateway.answer("ets", "delete-server", {})

    server = ets.get_server("partner-drop")
    assert gateway.last().json() == {"serverId": "partner-drop"}
    assert server.user_groups == ["partners"] and server.directories == ["incoming/2026/"]

    servers = ets.list_servers("partner")
    assert gateway.last().json() == {"prefix": "partner"}
    assert [s.server_id for s in servers] == ["partner-drop", "archive-drop"]
    # A field the server did not send reads as empty rather than raising.
    assert servers[1].port == 0 and servers[1].user_ids == []

    assert ets.list_servers() == servers
    assert gateway.last().json() == {"prefix": ""}

    ets.delete_server("partner-drop")
    assert gateway.last().json() == {"serverId": "partner-drop"}


# -- running --------------------------------------------------------------------------------------


def test_a_server_is_identified_by_its_namespace_as_well_as_its_id(gateway, ets):
    """A serverId is unique within an account and a namespace; the process, its socket and its log
    channel are named after the runtime name instead, since none of those has a namespace."""
    gateway.answer("ets", "get-server", SERVER)

    server = ets.get_server("partner-drop")

    assert server.namespace == "development"
    assert server.runtime_name == "partner-drop-91b2"


def test_starting_asks_rather_than_waits(gateway, ets):
    """The desired state changes here and the manager acts on it, so what comes back says what was
    asked for rather than what has happened."""
    gateway.answer("ets", "start-server", dict(SERVER, desiredState="RUNNING", state="STOPPED"))
    gateway.answer("ets", "stop-server", dict(SERVER, desiredState="STOPPED", state="RUNNING"))

    started = ets.start_server("partner-drop")
    assert gateway.last().json() == {"serverId": "partner-drop"}
    assert (started.desired_state, started.state) == ("RUNNING", "STOPPED")
    assert not started.is_running

    stopped = ets.stop_server("partner-drop")
    assert stopped.desired_state == "STOPPED"
    # Still listening, which is the ordinary picture of a server on its way down.
    assert stopped.is_running


def test_a_port_that_is_taken_is_refused_with_the_servers_reason(gateway, ets):
    gateway.answer("ets", "create-server",
                   {"error": "Port 2222 is already used by transfer server: partner-drop"},
                   status=409)

    with pytest.raises(EuclidServiceError) as raised:
        ets.create_server("second-drop", bucket="invoices", port=2222)

    assert (raised.value.target, raised.value.action, raised.value.status) == ("ets",
                                                                               "create-server", 409)
    assert raised.value.reason.startswith("Port 2222 is already used")


# -- everything else ----------------------------------------------------------------------------------


def test_ets_is_signed_and_follows_the_session(gateway):
    prepared(gateway)
    gateway.answer("eam", "change-namespace", {})
    gateway.answer("ets", "list-servers", {"servers": []})

    with Euclid.for_server(gateway.base_url).login("jens", "secret") as session:
        ets = session.ets()
        ets.list_servers()
        assert gateway.last().auth == "sigv4"
        assert gateway.last().headers["x-euclid-target"] == "ets"

        session.change_namespace("development")
        ets.list_servers()
        assert gateway.last().headers["x-euclid-namespace"] == "development"

        assert session.ets() is ets


def test_an_administrator_only_action_says_who_refused_it(gateway, ets):
    gateway.answer("ets", "create-server", {"error": "Administrator rights required"}, status=403)

    with pytest.raises(EuclidServiceError) as raised:
        ets.create_server("partner-drop", bucket="invoices", port=2222)

    assert raised.value.status == 403
    assert raised.value.reason == "Administrator rights required"


def test_metrics_and_call(gateway, ets):
    gateway.answer("ets", "get-metrics", {"items": [{"name": "ets-servers", "value": 1}]})
    gateway.answer("ets", "some-future-action", {"ok": True})

    assert ets.metrics() == {"items": [{"name": "ets-servers", "value": 1}]}
    assert ets.call("some-future-action", {"x": 1}) == {"ok": True}
    assert gateway.last().json() == {"x": 1}
