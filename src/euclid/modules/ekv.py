"""EKV - euclid's key-value store: tables of items, read by key rather than searched.

One object, :class:`EuclidEkv`, built from a session that has already logged in::

    ekv = Euclid.for_server(url).login("jens", "secret").ekv()
    ekv.create_table("sessions", "userId", sort_key="startedAt", sort_key_type=NUMBER)

    ekv.put_item("sessions", {"userId": "jens", "startedAt": 1757462400, "host": "laptop"})
    for item in ekv.query("sessions", "jens", GE, 1757462400).items:
        print(item["host"])

A table is keyed on one attribute, or on two: a partition key that identifies an item, and
optionally a sort key that orders the items sharing a partition key - which is what makes a
partition readable as a range. The key attributes have declared types, and those are what make a
range mean what it should: a :data:`NUMBER` sort key orders 2, 9, 10, 100 rather than putting "10"
before "9". They cannot be changed after the table is created.

An item's other attributes are free-form documents - scalars, lists, nested maps - and are not
declared anywhere. They are also not typed the way a queue message's attributes are: EKV stores
what JSON can express, so nothing here takes a :class:`~euclid.dto.com.Variant`.
"""

from __future__ import annotations

from typing import Any, Mapping

from ..dto.ekv import Item, ListTablesResult, QueryResult, ScanResult, TableDescription
from ..exceptions import EuclidServiceError
from .base import ModuleClient

__all__ = ["EuclidEkv", "TARGET", "STRING", "NUMBER", "BINARY",
           "WHOLE_PARTITION", "EQ", "LT", "LE", "GT", "GE", "BETWEEN", "BEGINS_WITH"]

TARGET = "ekv"

#: The types a key attribute can have. A table's keys are the only attributes with a declared type,
#: and the type is what a comparison is made under.
STRING = "string"
NUMBER = "number"
BINARY = "binary"

#: How :meth:`EuclidEkv.query` narrows by sort key. The default takes the whole partition; the rest
#: need a table that declares a sort key, and asking one that does not is refused with HTTP 400.
WHOLE_PARTITION = ""
EQ = "eq"
LT = "lt"
LE = "le"
GT = "gt"
GE = "ge"
#: Takes a lower and an upper bound, both inclusive - see :meth:`EuclidEkv.query`.
BETWEEN = "between"
#: The one operator that is not a comparison, and so the one that applies to a string sort key only:
#: a prefix of a number or of a blob is not a thing.
BEGINS_WITH = "begins-with"

#: HTTP 404, which is how a read says the item is not there - see :meth:`EuclidEkv.find_item`.
NOT_FOUND = 404


class EuclidEkv(ModuleClient):
    """EKV's operations, on the credentials of the session that created it.

    Built by :meth:`euclid.EuclidSession.ekv` rather than directly, so that it shares that
    session's identity, namespace and connection settings - and follows them as they change.
    """

    target = TARGET

    # -- tables ------------------------------------------------------------------------------

    def create_table(self, name: str, partition_key: str, partition_key_type: str = STRING,
                     sort_key: str = "", sort_key_type: str = STRING) -> TableDescription:
        """Creates a table, and returns it as it was created - with an item count of zero.

        Refused with HTTP 409 if a table of that name already exists, and with HTTP 400 if a key
        attribute is empty, starts with ``$``, contains ``.``, or if the sort key names the same
        attribute as the partition key.

        :param partition_key: the attribute every item is identified by.
        :param partition_key_type: :data:`STRING`, :data:`NUMBER` or :data:`BINARY`.
        :param sort_key: the attribute items sharing a partition key are ordered by, which is what
            makes a partition readable as a range. Left empty, the table has none and
            :meth:`query` can only take whole partitions.
        :param sort_key_type: the sort key's type, which decides what its ordering means.
        """
        return TableDescription.from_json(self._call("create-table", {
            "name": name, "partitionKey": partition_key, "partitionKeyType": partition_key_type,
            "sortKey": sort_key, "sortKeyType": sort_key_type}))

    def describe_table(self, name: str) -> TableDescription:
        """A table's key, and how many items it holds.

        The count is counted rather than looked up, so this is not free on a large table.
        """
        return TableDescription.from_json(self._call("describe-table", {"name": name}))

    def list_tables(self, prefix: str = "", page_size: int = 10, page_index: int = 0,
                    sort_column: str = "name", sort_direction: str = "asc") -> ListTablesResult:
        """One page of tables, each described as :meth:`describe_table` would describe it."""
        return ListTablesResult.from_json(self._call("list-tables", {
            "prefix": prefix, "pageSize": page_size, "pageIndex": page_index,
            "sortColumn": sort_column, "sortDirection": sort_direction}))

    def delete_table(self, name: str) -> int:
        """Deletes a table and every item in it, and returns how many items went with it.

        There is no confirmation and nothing is kept.
        """
        return self._number("delete-table", {"name": name}, "deletedItems")

    # -- items -------------------------------------------------------------------------------

    def put_item(self, table: str, item: Mapping[str, Any]) -> Item:
        """Writes an item, replacing whatever was stored under its key.

        It replaces rather than merges: an item written with two attributes has two attributes
        afterwards, whatever it had before. So changing one field means reading the item, changing
        it and writing the whole thing back - which is why :class:`~euclid.dto.ekv.Item` keeps the
        server's timestamps out of :attr:`~euclid.dto.ekv.Item.attributes`, where they would
        otherwise be written back as two attributes of the caller's own.

        The item has to carry the table's key attributes with the types the table declared for
        them, and no attribute name may be empty, start with ``$`` or contain ``.``.
        """
        return Item.from_json(self._call("put-item", {"table": table, "item": dict(item)}))

    def get_item(self, table: str, key: Mapping[str, Any]) -> Item:
        """Reads one item by its key.

        The key names the table's key attributes and only those - the partition key alone where the
        table has no sort key, both where it has one.

        An item that is not there is HTTP 404, and so an
        :class:`~euclid.exceptions.EuclidServiceError` rather than an empty item: "there is no such
        item" and "here is an item with nothing in it" are different, and a caller should not have
        to tell them apart. Where a miss is an ordinary outcome, use :meth:`find_item`.
        """
        return Item.from_json(self._call("get-item", {"table": table, "key": dict(key)}))

    def find_item(self, table: str, key: Mapping[str, Any]) -> Item | None:
        """The same read as :meth:`get_item`, answering None rather than raising when there is no
        such item - ``dict.get`` to its ``dict[...]``.

        Only a 404 is turned into None. A refusal, a malformed key or a table that does not exist
        still raises, because none of those mean "not there".
        """
        try:
            return self.get_item(table, key)
        except EuclidServiceError as error:
            if error.status == NOT_FOUND:
                return None
            raise

    def delete_item(self, table: str, key: Mapping[str, Any]) -> bool:
        """Removes one item by its key, and says whether there was one to remove.

        False rather than an error for a key that names nothing: deleting what is not there has
        already achieved what the caller asked for.
        """
        result = self._call("delete-item", {"table": table, "key": dict(key)})
        return bool(result.get("deleted"))

    # -- reading many ------------------------------------------------------------------------

    def query(self, table: str, partition_key: Any, sort_operator: str = WHOLE_PARTITION,
              sort_value: Any = None, sort_upper: Any = None, forward: bool = True,
              page_size: int = 0, page_index: int = 0) -> QueryResult:
        """Reads the items of one partition, in sort-key order.

        This is the lookup EKV is for: it addresses a partition by key rather than reading the
        table. Narrowing by sort key needs a table that declares one, and asking one that does not
        for anything but :data:`WHOLE_PARTITION` is refused with HTTP 400.

        :param partition_key: the partition key's value, of the type the table declared for it.
        :param sort_operator: :data:`EQ`, :data:`LT`, :data:`LE`, :data:`GT`, :data:`GE`,
            :data:`BETWEEN` or :data:`BEGINS_WITH`, or :data:`WHOLE_PARTITION` for all of it.
        :param sort_value: what to compare the sort key against - the lower bound for
            :data:`BETWEEN`.
        :param sort_upper: the upper bound, for :data:`BETWEEN` only.
        :param forward: whether to read in ascending sort-key order. Always sent, because the
            server reads an absent flag as descending rather than as "unspecified".
        :param page_size: the most items to return; 0 means no limit.
        :param page_index: the zero-based page, applied when ``page_size`` is set.
        :raises ValueError: if :data:`BETWEEN` was asked for without both bounds, which the server
            refuses anyway - this just says so before the round trip.
        """
        if sort_operator == BETWEEN and (sort_value is None or sort_upper is None):
            raise ValueError("between needs both a sort_value and a sort_upper")

        return QueryResult.from_json(self._call("query", {
            "table": table, "partitionKey": partition_key, "sortOperator": sort_operator,
            "sortValue": sort_value, "sortUpper": sort_upper, "forward": forward,
            "pageSize": page_size, "pageIndex": page_index}))

    def scan(self, table: str, page_size: int = 0, page_index: int = 0) -> ScanResult:
        """Reads a table's items without regard to their key.

        This reads the table rather than an index: fine for a small table or an export, the wrong
        tool for a lookup - :meth:`query` is that. ``page_size`` of 0 means no limit, so a scan of
        a large table with no paging brings all of it back.
        """
        return ScanResult.from_json(self._call("scan", {
            "table": table, "pageSize": page_size, "pageIndex": page_index}))

    # -- monitoring --------------------------------------------------------------------------

    def metrics(self) -> dict[str, Any]:
        """EKV's own metrics, as the server collects them. Returned unparsed - the shape belongs to
        the monitoring module rather than to EKV."""
        return self._call("get-metrics")
