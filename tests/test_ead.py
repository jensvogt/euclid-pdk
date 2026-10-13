"""EAD, end to end against a fake euclid server.

Three actions, each one request, so what these check is the wire. The one shape worth more than a
field assertion is the listing's total: it is capped rather than exact, and ``total_exact`` is what
makes the capped answer honest instead of wrong.
"""

from __future__ import annotations

import pytest

from euclid import Euclid, EuclidServiceError
from euclid.modules.ead import LIST_TOTAL_LIMIT
from test_eam import prepared

EVENT = {"accountId": "000000000000", "namespace": "development", "userId": "jens",
         "module": "esm", "command": "delete-bucket", "parameters": "--bucket reports",
         "status": "OK", "created": "2026-10-13T09:15:00Z"}


@pytest.fixture
def ead(gateway):
    """An EAD client on a logged-in session, closed with it."""
    prepared(gateway)
    with Euclid.for_server(gateway.base_url).login("jens", "secret") as session:
        yield session.ead()


def test_listing_the_trail_sends_every_filter_it_was_given(gateway, ead):
    gateway.answer("ead", "list-events", {"events": [EVENT, {"userId": "alice"}],
                                          "total": 2, "totalExact": True})

    result = ead.list_events(user_id="jens", module="esm", command="delete-bucket", page_size=50)

    assert gateway.last().json() == {"userId": "jens", "module": "esm",
                                     "command": "delete-bucket", "pageSize": 50, "pageIndex": 0}
    assert (result.total, result.total_exact) == (2, True)
    event = result.events[0]
    assert (event.module, event.command, event.status) == ("esm", "delete-bucket", "OK")
    assert event.parameters == "--bucket reports"
    # A field the server did not send reads as empty rather than raising.
    assert (result.events[1].module, result.events[1].created) == ("", "")


def test_a_listing_with_no_filters_reads_the_whole_account(gateway, ead):
    gateway.answer("ead", "list-events", {"events": [], "total": 0, "totalExact": True})

    ead.list_events()

    assert gateway.last().json() == {"userId": "", "module": "", "command": "",
                                     "pageSize": 10, "pageIndex": 0}


def test_a_capped_total_says_it_is_capped(gateway, ead):
    """False does not mean the count is wrong - it means "at least this many", which is all a pager
    needs to know there is a next page. count_events is the real number."""
    gateway.answer("ead", "list-events", {"events": [EVENT], "total": LIST_TOTAL_LIMIT,
                                          "totalExact": False})

    result = ead.list_events()

    assert result.total == LIST_TOTAL_LIMIT
    assert result.total_exact is False


def test_counting_is_the_exact_number_a_listing_will_not_pay_for(gateway, ead):
    gateway.answer("ead", "count-events", {"count": 1_284_991})

    assert ead.count_events(module="esm") == 1_284_991
    assert gateway.last().json() == {"userId": "", "module": "esm", "command": ""}


def test_purging_needs_a_window_and_says_so_before_the_round_trip(gateway, ead):
    """A purge with no window would remove the whole trail, so neither end accepts one."""
    for refused in (0, -1):
        with pytest.raises(ValueError, match="whole trail"):
            ead.purge_events(refused)

    assert [r for r in gateway.requests if r.target == "ead"] == []


def test_purging_answers_with_how_many_went(gateway, ead):
    gateway.answer("ead", "purge-events", {"removed": 4096})

    assert ead.purge_events(90) == 4096
    assert gateway.last().json() == {"olderThanDays": 90}


def test_a_refusal_carries_the_servers_reason(gateway, ead):
    gateway.answer("ead", "purge-events", {"error": "Administrator privileges required"},
                   status=403)

    with pytest.raises(EuclidServiceError) as raised:
        ead.purge_events(90)

    assert (raised.value.target, raised.value.action, raised.value.status) == (
        "ead", "purge-events", 403)


def test_ead_is_signed_and_follows_the_session(gateway):
    prepared(gateway)
    gateway.answer("eam", "change-namespace", {})
    gateway.answer("ead", "count-events", {"count": 0})

    with Euclid.for_server(gateway.base_url).login("jens", "secret") as session:
        ead = session.ead()
        ead.count_events()
        assert gateway.last().headers["x-euclid-target"] == "ead"
        assert gateway.last().auth == "sigv4"

        session.change_namespace("development")
        ead.count_events()
        assert gateway.last().headers["x-euclid-namespace"] == "development"

        assert session.ead() is ead


def test_metrics_and_call(gateway, ead):
    gateway.answer("ead", "get-metrics", {"items": [{"name": "ead-events", "value": 7}]})
    gateway.answer("ead", "some-future-action", {"ok": True})

    assert ead.metrics() == {"items": [{"name": "ead-events", "value": 7}]}
    assert ead.call("some-future-action", {"x": 1}) == {"ok": True}
    assert gateway.last().json() == {"x": 1}
