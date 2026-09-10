"""The shapes EKV sends back.

Parsed the same defensive way as every other module's. One thing is not just parsed but rearranged:
the server stores an item's timestamps as two ordinary attributes, ``_created`` and ``_modified``,
and :class:`Item` lifts them out. Leaving them in would mean an item read, changed and written back
acquires two attributes it never had - and since a write replaces rather than merges, they would
stick.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterator

from . import _json

__all__ = ["Item", "TableDescription", "ListTablesResult", "QueryResult", "ScanResult",
           "CREATED_ATTRIBUTE", "MODIFIED_ATTRIBUTE"]

#: The attributes the server keeps an item's timestamps in, and which :class:`Item` takes back out.
CREATED_ATTRIBUTE = "_created"
MODIFIED_ATTRIBUTE = "_modified"


@dataclass
class Item:
    """One stored item: its attributes, and when it was written.

    The attributes are plain Python values - strings, numbers, booleans, lists, nested dicts - not
    the tagged :class:`~euclid.dto.com.Variant` that EQS, ENS and ESM attributes use. EKV stores
    documents rather than typed attribute maps, and only a table's key attributes have a declared
    type.

    Reads like a mapping for the common case, so ``item["email"]`` and ``"email" in item`` work;
    :attr:`attributes` is the dictionary itself, which is what to pass back to
    :meth:`~euclid.modules.ekv.EuclidEkv.put_item`.
    """

    attributes: dict[str, Any] = field(default_factory=dict)
    created: str = ""
    modified: str = ""

    @staticmethod
    def from_json(document: Any) -> "Item":
        """One item, with its two timestamp attributes lifted out of the rest."""
        if not isinstance(document, dict):
            return Item()
        attributes = dict(document)
        created = attributes.pop(CREATED_ATTRIBUTE, "")
        modified = attributes.pop(MODIFIED_ATTRIBUTE, "")
        return Item(attributes, created if isinstance(created, str) else "",
                    modified if isinstance(modified, str) else "")

    def __getitem__(self, name: str) -> Any:
        return self.attributes[name]

    def __contains__(self, name: object) -> bool:
        return name in self.attributes

    def __iter__(self) -> Iterator[str]:
        return iter(self.attributes)

    def get(self, name: str, default: Any = None) -> Any:
        """An attribute's value, or ``default`` when the item does not carry it."""
        return self.attributes.get(name, default)


@dataclass
class TableDescription:
    """A table: what it is keyed on, and how many items it holds.

    ``sort_key`` is empty for a table that has none, which is also what says that
    :meth:`~euclid.modules.ekv.EuclidEkv.query` cannot narrow by sort key on this table.

    ``item_count`` is counted rather than looked up, so describing a large table is not free.
    """

    name: str = ""
    ern: str = ""
    partition_key: str = ""
    #: ``string``, ``number`` or ``binary`` - see :mod:`euclid.modules.ekv`.
    partition_key_type: str = ""
    sort_key: str = ""
    sort_key_type: str = ""
    item_count: int = 0
    created: str = ""
    modified: str = ""

    @staticmethod
    def from_json(document: Any) -> "TableDescription":
        return TableDescription(
            _json.text(document, "name"), _json.text(document, "ern"),
            _json.text(document, "partitionKey"), _json.text(document, "partitionKeyType"),
            _json.text(document, "sortKey"), _json.text(document, "sortKeyType"),
            _json.number(document, "itemCount"), _json.text(document, "created"),
            _json.text(document, "modified"))


@dataclass
class ListTablesResult:
    """One page of tables, and how many exist in total.

    Each is described as :meth:`~euclid.modules.ekv.EuclidEkv.describe_table` would describe it,
    item count included - which is counted per table, so a large page of large tables costs what
    those counts cost.
    """

    tables: list[TableDescription] = field(default_factory=list)
    total: int = 0

    @staticmethod
    def from_json(document: Any) -> "ListTablesResult":
        return ListTablesResult(
            [TableDescription.from_json(t) for t in _json.documents(document, "tables")],
            _json.number(document, "total"))


@dataclass
class QueryResult:
    """The items of one partition that matched, in the order they were asked for."""

    items: list[Item] = field(default_factory=list)
    count: int = 0

    @staticmethod
    def from_json(document: Any) -> "QueryResult":
        items = [Item.from_json(i) for i in _json.documents(document, "items")]
        return QueryResult(items, _json.number(document, "count") or len(items))


@dataclass
class ScanResult:
    """A page of a table's items, and how many the table holds in total.

    ``count`` is what came back and ``total`` what there is - the pair a caller pages against.
    """

    items: list[Item] = field(default_factory=list)
    count: int = 0
    total: int = 0

    @staticmethod
    def from_json(document: Any) -> "ScanResult":
        items = [Item.from_json(i) for i in _json.documents(document, "items")]
        return ScanResult(items, _json.number(document, "count") or len(items),
                          _json.number(document, "total"))
