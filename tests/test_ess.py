"""ESS, end to end against a fake euclid server.

The one thing worth more than a shape check here is the update: the server distinguishes a field
being sent from a field being empty, so ``description=""`` clears a description while leaving it
alone means not sending it at all. A client that always sent all three would silently wipe two of
them on every rotation, and the only test that catches that is one that reads the request body.
"""

from __future__ import annotations

import pytest

from euclid import Euclid, EuclidServiceError
from test_eam import prepared

SECRET = {"name": "db-password", "ern": "ern:ess:secret/db-password",
          "description": "the reporting database", "encryptionKeyErn": "ern:ekm:key/key-1",
          "version": 3, "rotated": "2026-09-01T10:00:00Z", "tags": {"team": "finance"},
          "created": "2026-01-01", "modified": "2026-09-01"}


@pytest.fixture
def ess(gateway):
    """An ESS client on a logged-in session, closed with it."""
    prepared(gateway)
    with Euclid.for_server(gateway.base_url).login("jens", "secret") as session:
        yield session.ess()


# -- secrets -------------------------------------------------------------------------------------


def test_creating_a_secret_answers_with_metadata_only(gateway, ess):
    """The value went in; what comes back is everything about it except the value, so this call can
    be logged without being the thing that leaks it."""
    gateway.answer("ess", "create-secret", {"secret": SECRET})

    secret = ess.create_secret("db-password", "hunter2", description="the reporting database",
                               key_ern="ern:ekm:key/key-1")

    assert gateway.last().json() == {"name": "db-password", "value": "hunter2",
                                     "description": "the reporting database",
                                     "keyErn": "ern:ekm:key/key-1"}
    assert (secret.name, secret.version) == ("db-password", 3)
    assert secret.encryption_key_ern == "ern:ekm:key/key-1"
    assert not hasattr(secret, "value")


def test_getting_a_secret_is_the_one_call_that_returns_a_value(gateway, ess):
    gateway.answer("ess", "get-secret", {"secret": SECRET, "value": "hunter2"})

    fetched = ess.get_secret("db-password")

    assert gateway.last().json() == {"name": "db-password"}
    assert fetched.value == "hunter2"
    assert fetched.secret.version == 3


def test_listing_secrets(gateway, ess):
    gateway.answer("ess", "list-secrets", {"total": 2, "secrets": [SECRET, {"name": "api-token"}]})

    listed = ess.list_secrets(prefix="db", page_size=25, sort_direction="desc")

    assert gateway.last().json() == {"prefix": "db", "pageSize": 25, "pageIndex": 0,
                                     "sortColumn": "name", "sortDirection": "desc"}
    assert (listed.total, [s.name for s in listed.secrets]) == (2, ["db-password", "api-token"])
    assert listed.secrets[0].tags == {"team": "finance"}
    # A field the server did not send reads as empty rather than raising.
    assert listed.secrets[1].version == 0 and listed.secrets[1].tags == {}


def test_rotating_sends_the_value_and_nothing_else(gateway, ess):
    """Not the description, and not the key: a rotation that also cleared the description would be
    a rotation nobody could audit afterwards."""
    gateway.answer("ess", "update-secret", {"secret": SECRET})

    ess.rotate_secret("db-password", "hunter3")

    assert gateway.last().json() == {"name": "db-password", "value": "hunter3"}


def test_an_update_sends_only_what_it_was_given(gateway, ess):
    gateway.answer("ess", "update-secret", {"secret": SECRET})

    ess.update_secret("db-password", description="now the analytics database")
    assert gateway.last().json() == {"name": "db-password", "description": "now the analytics database"}

    # An empty description is a description: it clears the stored one, which is why it has to be
    # sent rather than treated as "nothing to say".
    ess.update_secret("db-password", description="")
    assert gateway.last().json() == {"name": "db-password", "description": ""}

    # And an empty value is a value somebody may legitimately store.
    ess.update_secret("db-password", value="")
    assert gateway.last().json() == {"name": "db-password", "value": ""}

    ess.update_secret("db-password", key_ern="ern:ekm:key/key-2")
    assert gateway.last().json() == {"name": "db-password", "keyErn": "ern:ekm:key/key-2"}

    ess.update_secret("db-password", value="hunter3", description="rotated after the audit",
                      key_ern="ern:ekm:key/key-2")
    assert gateway.last().json() == {"name": "db-password", "value": "hunter3",
                                     "description": "rotated after the audit",
                                     "keyErn": "ern:ekm:key/key-2"}


def test_an_update_that_changes_nothing_says_so_before_the_round_trip(gateway, ess):
    with pytest.raises(ValueError, match="value, a description or a key_ern"):
        ess.update_secret("db-password")

    assert [r for r in gateway.requests if r.target == "ess"] == []


def test_deleting_a_secret(gateway, ess):
    gateway.answer("ess", "delete-secret", {"name": "db-password", "ern": "ern:ess:secret/db-password"})

    deleted = ess.delete_secret("db-password")

    assert gateway.last().json() == {"name": "db-password"}
    assert (deleted.name, deleted.ern) == ("db-password", "ern:ess:secret/db-password")


def test_secret_tags_come_back_as_the_secret_now_reads(gateway, ess):
    gateway.answer("ess", "add-secret-tag", {"secret": dict(SECRET, tags={"team": "finance"})})
    gateway.answer("ess", "delete-secret-tag", {"secret": dict(SECRET, tags={})})

    tagged = ess.add_secret_tag("db-password", "team", "finance")
    assert gateway.last().json() == {"name": "db-password", "key": "team", "value": "finance"}
    assert tagged.tags == {"team": "finance"}

    untagged = ess.delete_secret_tag("db-password", "team")
    assert gateway.last().json() == {"name": "db-password", "key": "team"}
    assert untagged.tags == {}


# -- everything else -------------------------------------------------------------------------------


def test_ess_is_signed_and_follows_the_session(gateway):
    prepared(gateway)
    gateway.answer("eam", "change-namespace", {})
    gateway.answer("ess", "list-secrets", {"secrets": [], "total": 0})

    with Euclid.for_server(gateway.base_url).login("jens", "secret") as session:
        ess = session.ess()
        ess.list_secrets()
        assert gateway.last().auth == "sigv4"
        assert gateway.last().headers["x-euclid-target"] == "ess"

        session.change_namespace("development")
        ess.list_secrets()
        assert gateway.last().headers["x-euclid-namespace"] == "development"

        assert session.ess() is ess


def test_a_missing_secret_carries_the_servers_reason(gateway, ess):
    gateway.answer("ess", "get-secret", {"error": "Secret not found, name: nothing"}, status=404)

    with pytest.raises(EuclidServiceError) as raised:
        ess.get_secret("nothing")

    assert (raised.value.target, raised.value.action, raised.value.status) == ("ess", "get-secret", 404)
    assert raised.value.reason == "Secret not found, name: nothing"


def test_metrics_and_call(gateway, ess):
    gateway.answer("ess", "get-metrics", {"items": [{"name": "ess-secrets", "value": 3}]})
    gateway.answer("ess", "some-future-action", {"ok": True})

    assert ess.metrics() == {"items": [{"name": "ess-secrets", "value": 3}]}
    assert ess.call("some-future-action", {"x": 1}) == {"ok": True}
    assert gateway.last().json() == {"x": 1}
