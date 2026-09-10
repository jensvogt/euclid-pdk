"""EKV, end to end against a fake euclid server.

The table actions are checked the way the other modules' are: what went on the wire, and what came
back off it. The item actions are checked against a stand-in that really stores what it is sent
(``FakeTables`` below), because the two things this client rearranges - lifting an item's timestamps
out of its attributes, and a write replacing rather than merging - only show themselves when an item
is read back after being written.
"""

from __future__ import annotations

from typing import Any

import pytest

from euclid import Euclid, EuclidServiceError
from euclid.dto.ekv import CREATED_ATTRIBUTE, MODIFIED_ATTRIBUTE, Item
from euclid.modules.ekv import BEGINS_WITH, BETWEEN, GE, NUMBER, WHOLE_PARTITION
from fake_gateway import RecordedRequest
from test_eam import prepared

TABLE = "sessions"


class FakeTables:
    """A key-value store that keeps items rather than pretending to.

    One table, keyed on ``userId`` and ordered by ``startedAt``, which is enough for the round trips
    that matter. Timestamps are stored as the server stores them - two ordinary attributes - because
    the client's job of taking them back out is the thing being tested.
    """

    def __init__(self) -> None:
        #: (partition key, sort key) -> the item's attributes as stored.
        self.items: dict[tuple[Any, Any], dict] = {}
        self.queries: list[dict] = []

    def install(self, gateway):
        gateway.on("ekv", "put-item", self.put_item)
        gateway.on("ekv", "get-item", self.get_item)
        gateway.on("ekv", "delete-item", self.delete_item)
        gateway.on("ekv", "query", self.query)
        gateway.on("ekv", "scan", self.scan)
        return self

    @staticmethod
    def _key(attributes: dict) -> tuple:
        return attributes.get("userId"), attributes.get("startedAt")

    def put_item(self, request: RecordedRequest) -> tuple[int, dict]:
        item = dict(request.json()["item"])
        stored = self.items.get(self._key(item))
        item[CREATED_ATTRIBUTE] = stored[CREATED_ATTRIBUTE] if stored else "2026-09-10T09:00:00Z"
        item[MODIFIED_ATTRIBUTE] = "2026-09-10T10:00:00Z"
        # Replaces rather than merges, exactly as the server does.
        self.items[self._key(item)] = item
        return 200, item

    def get_item(self, request: RecordedRequest) -> tuple[int, dict]:
        item = self.items.get(self._key(request.json()["key"]))
        if item is None:
            return 404, {"error": "Item not found"}
        return 200, item

    def delete_item(self, request: RecordedRequest) -> tuple[int, dict]:
        return 200, {"deleted": self.items.pop(self._key(request.json()["key"]), None) is not None}

    def query(self, request: RecordedRequest) -> tuple[int, dict]:
        body = request.json()
        self.queries.append(body)
        matching = [item for key, item in self.items.items() if key[0] == body.get("partitionKey")]
        matching.sort(key=lambda item: item.get("startedAt", 0), reverse=not body.get("forward"))
        return 200, {"items": matching, "count": len(matching)}

    def scan(self, request: RecordedRequest) -> tuple[int, dict]:
        items = list(self.items.values())
        page_size = int(request.json().get("pageSize") or 0)
        page = items[:page_size] if page_size else items
        return 200, {"items": page, "count": len(page), "total": len(items)}


@pytest.fixture
def tables(gateway):
    """A gateway that answers a login, with a key-value store behind it."""
    prepared(gateway)
    return FakeTables().install(gateway)


@pytest.fixture
def ekv(gateway, tables):
    """An EKV client on a logged-in session, closed with it."""
    with Euclid.for_server(gateway.base_url).login("jens", "secret") as session:
        yield session.ekv()


# -- tables ---------------------------------------------------------------------------------------


def test_creating_a_table_names_its_key_and_the_types_of_it(gateway, ekv):
    """The types are what make a range mean what it should: a number sort key orders 2, 9, 10, 100
    rather than putting "10" before "9"."""
    gateway.answer("ekv", "create-table", {
        "name": TABLE, "ern": "ern:ekv:table/sessions", "partitionKey": "userId",
        "partitionKeyType": "string", "sortKey": "startedAt", "sortKeyType": "number",
        "itemCount": 0, "created": "2026-09-10"})

    table = ekv.create_table(TABLE, "userId", sort_key="startedAt", sort_key_type=NUMBER)

    assert gateway.last().json() == {"name": TABLE, "partitionKey": "userId",
                                     "partitionKeyType": "string", "sortKey": "startedAt",
                                     "sortKeyType": "number"}
    assert (table.partition_key, table.sort_key_type, table.item_count) == ("userId", "number", 0)


def test_a_table_without_a_sort_key_says_so_by_leaving_it_empty(gateway, ekv):
    gateway.answer("ekv", "create-table", {"name": "profiles", "partitionKey": "userId",
                                           "partitionKeyType": "string", "sortKey": "",
                                           "sortKeyType": ""})

    table = ekv.create_table("profiles", "userId")

    assert gateway.last().json()["sortKey"] == ""
    assert table.sort_key == "" and table.sort_key_type == ""


def test_describing_listing_and_deleting_tables(gateway, ekv):
    gateway.answer("ekv", "describe-table", {"name": TABLE, "partitionKey": "userId",
                                             "itemCount": 42})
    gateway.answer("ekv", "list-tables", {"total": 2, "tables": [
        {"name": TABLE, "partitionKey": "userId", "sortKey": "startedAt", "itemCount": 42},
        {"name": "profiles"}]})
    gateway.answer("ekv", "delete-table", {"deletedItems": 42})

    assert ekv.describe_table(TABLE).item_count == 42
    assert gateway.last().json() == {"name": TABLE}

    listed = ekv.list_tables(prefix="ses", page_size=25, sort_direction="desc")
    assert gateway.last().json() == {"prefix": "ses", "pageSize": 25, "pageIndex": 0,
                                     "sortColumn": "name", "sortDirection": "desc"}
    assert (listed.total, [t.name for t in listed.tables]) == (2, [TABLE, "profiles"])
    # A field the server did not send reads as empty rather than raising.
    assert listed.tables[1].item_count == 0

    assert ekv.delete_table(TABLE) == 42
    assert gateway.last().json() == {"name": TABLE}


# -- items ----------------------------------------------------------------------------------------


def test_writing_and_reading_an_item(gateway, ekv, tables):
    written = ekv.put_item(TABLE, {"userId": "jens", "startedAt": 1757462400, "host": "laptop",
                                   "tags": ["work", "eu"], "meta": {"agent": "pdk"}})

    assert gateway.last().json() == {"table": TABLE, "item": {
        "userId": "jens", "startedAt": 1757462400, "host": "laptop", "tags": ["work", "eu"],
        "meta": {"agent": "pdk"}}}
    # Scalars, lists and nested maps, stored as themselves - no typed variant in sight.
    assert written["tags"] == ["work", "eu"] and written["meta"]["agent"] == "pdk"

    read = ekv.get_item(TABLE, {"userId": "jens", "startedAt": 1757462400})
    assert gateway.last().json() == {"table": TABLE, "key": {"userId": "jens",
                                                             "startedAt": 1757462400}}
    assert read.attributes == written.attributes
    assert read.get("nothing") is None


def test_the_timestamps_are_lifted_out_of_the_attributes(gateway, ekv, tables):
    """Where the server keeps them - and where they would become the caller's own attributes on the
    next write, since a write replaces rather than merges."""
    item = ekv.put_item(TABLE, {"userId": "jens", "startedAt": 1, "host": "laptop"})

    assert (item.created, item.modified) == ("2026-09-10T09:00:00Z", "2026-09-10T10:00:00Z")
    assert CREATED_ATTRIBUTE not in item and MODIFIED_ATTRIBUTE not in item
    assert set(item.attributes) == {"userId", "startedAt", "host"}


def test_an_item_read_changed_and_written_back_does_not_grow(gateway, ekv, tables):
    """The whole reason the timestamps are lifted: this is the ordinary way to change one field of
    an item, and it has to be a no-op for every other field."""
    ekv.put_item(TABLE, {"userId": "jens", "startedAt": 1, "host": "laptop"})

    item = ekv.get_item(TABLE, {"userId": "jens", "startedAt": 1})
    changed = dict(item.attributes, host="desktop")
    ekv.put_item(TABLE, changed)

    stored = ekv.get_item(TABLE, {"userId": "jens", "startedAt": 1})
    assert stored.attributes == {"userId": "jens", "startedAt": 1, "host": "desktop"}
    # The write replaced the item, so nothing carried over that the caller did not send.
    assert stored.created == "2026-09-10T09:00:00Z"


def test_a_write_replaces_rather_than_merges(gateway, ekv, tables):
    ekv.put_item(TABLE, {"userId": "jens", "startedAt": 1, "host": "laptop", "agent": "pdk"})
    ekv.put_item(TABLE, {"userId": "jens", "startedAt": 1, "host": "desktop"})

    stored = ekv.get_item(TABLE, {"userId": "jens", "startedAt": 1})
    assert "agent" not in stored


def test_a_missing_item_raises_and_find_item_answers_none(gateway, ekv, tables):
    """"There is no such item" and "here is an item with nothing in it" are different, so the read
    that cannot tell them apart is the one that has to say which it meant."""
    key = {"userId": "nobody", "startedAt": 1}

    with pytest.raises(EuclidServiceError) as raised:
        ekv.get_item(TABLE, key)
    assert raised.value.status == 404

    assert ekv.find_item(TABLE, key) is None
    assert ekv.find_item(TABLE, {"userId": "jens", "startedAt": 1}) is None


def test_find_item_only_swallows_a_miss(gateway, ekv):
    """A malformed key or a table that does not exist is not "not there", and still raises."""
    gateway.answer("ekv", "get-item", {"error": "'sessions' is keyed on userId"}, status=400)

    with pytest.raises(EuclidServiceError) as raised:
        ekv.find_item(TABLE, {"wrong": "key"})

    assert raised.value.status == 400


def test_deleting_an_item_says_whether_there_was_one(gateway, ekv, tables):
    ekv.put_item(TABLE, {"userId": "jens", "startedAt": 1})

    assert ekv.delete_item(TABLE, {"userId": "jens", "startedAt": 1}) is True
    assert gateway.last().json() == {"table": TABLE, "key": {"userId": "jens", "startedAt": 1}}
    # Deleting what is not there has already achieved what the caller asked for.
    assert ekv.delete_item(TABLE, {"userId": "jens", "startedAt": 1}) is False


# -- reading many ------------------------------------------------------------------------------------


def test_query_reads_a_partition_in_sort_key_order(gateway, ekv, tables):
    for started in (3, 1, 2):
        ekv.put_item(TABLE, {"userId": "jens", "startedAt": started})
    ekv.put_item(TABLE, {"userId": "alice", "startedAt": 1})

    result = ekv.query(TABLE, "jens")

    assert [item["startedAt"] for item in result.items] == [1, 2, 3]
    assert result.count == 3


def test_query_always_says_which_direction_it_wants(gateway, ekv, tables):
    """The server reads an absent ``forward`` as descending rather than as unspecified, so leaving
    it out would silently reverse every query this SDK makes."""
    ekv.query(TABLE, "jens")

    assert gateway.last().json() == {"table": TABLE, "partitionKey": "jens", "sortOperator": "",
                                     "sortValue": None, "sortUpper": None, "forward": True,
                                     "pageSize": 0, "pageIndex": 0}

    ekv.query(TABLE, "jens", forward=False)
    assert gateway.last().json()["forward"] is False


def test_query_narrows_by_sort_key(gateway, ekv, tables):
    ekv.query(TABLE, "jens", GE, 1757462400, page_size=50, page_index=1)

    assert gateway.last().json() == {"table": TABLE, "partitionKey": "jens", "sortOperator": "ge",
                                     "sortValue": 1757462400, "sortUpper": None, "forward": True,
                                     "pageSize": 50, "pageIndex": 1}

    ekv.query(TABLE, "jens", BETWEEN, 1, 10)
    assert gateway.last().json()["sortUpper"] == 10

    ekv.query(TABLE, "jens", BEGINS_WITH, "2026-")
    assert gateway.last().json()["sortValue"] == "2026-"

    assert ekv.query(TABLE, "jens", WHOLE_PARTITION).count == 0


def test_between_without_both_bounds_says_so_before_the_round_trip(gateway, ekv, tables):
    with pytest.raises(ValueError, match="both a sort_value and a sort_upper"):
        ekv.query(TABLE, "jens", BETWEEN, 1)

    assert [r for r in gateway.requests if r.action == "query"] == []


def test_scan_reads_the_table_and_says_how_much_there_is(gateway, ekv, tables):
    for started in range(5):
        ekv.put_item(TABLE, {"userId": "jens", "startedAt": started})

    scanned = ekv.scan(TABLE, page_size=2)

    assert gateway.last().json() == {"table": TABLE, "pageSize": 2, "pageIndex": 0}
    assert (len(scanned.items), scanned.count, scanned.total) == (2, 2, 5)
    # No paging at all means the whole table, which is what a page size of zero says.
    assert ekv.scan(TABLE).count == 5


# -- everything else ------------------------------------------------------------------------------------


def test_ekv_is_signed_and_follows_the_session(gateway, tables):
    gateway.answer("eam", "change-namespace", {})
    gateway.answer("ekv", "list-tables", {"tables": [], "total": 0})

    with Euclid.for_server(gateway.base_url).login("jens", "secret") as session:
        ekv = session.ekv()
        ekv.list_tables()
        assert gateway.last().auth == "sigv4"
        assert gateway.last().headers["x-euclid-target"] == "ekv"

        session.change_namespace("development")
        ekv.list_tables()
        assert gateway.last().headers["x-euclid-namespace"] == "development"

        assert session.ekv() is ekv


def test_metrics_and_call(gateway, ekv):
    gateway.answer("ekv", "get-metrics", {"items": [{"name": "ekv-items", "value": 3}]})
    gateway.answer("ekv", "some-future-action", {"ok": True})

    assert ekv.metrics() == {"items": [{"name": "ekv-items", "value": 3}]}
    assert ekv.call("some-future-action", {"x": 1}) == {"ok": True}
    assert gateway.last().json() == {"x": 1}


def test_an_item_reads_like_a_mapping():
    """For the common case; the attributes dictionary itself is what goes back into put_item."""
    item = Item.from_json({"userId": "jens", "host": "laptop", CREATED_ATTRIBUTE: "2026-09-10"})

    assert item["host"] == "laptop"
    assert "userId" in item and "nothing" not in item
    assert sorted(item) == ["host", "userId"]
    assert item.get("nothing", "fallback") == "fallback"
