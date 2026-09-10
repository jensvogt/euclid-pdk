#!/usr/bin/env python3
"""EKV end to end: a table, some items, and the two ways of reading them back.

    python examples/table_walkthrough.py https://euclid.example.com jens secret

Works in a table of its own, named after the moment it started, and deletes it at the end - so it is
safe to point at a running server, and a run that dies halfway leaves one obviously disposable table
behind rather than touching anything of yours.
"""

from __future__ import annotations

import sys
import time

from euclid import Euclid, EuclidAuthenticationError
from euclid.modules.ekv import BEGINS_WITH, BETWEEN, GE, NUMBER


def main(argv: list[str]) -> int:
    if len(argv) < 4:
        print(__doc__)
        return 2
    base_url, username, password = argv[1], argv[2], argv[3]

    try:
        session = (Euclid.for_server(base_url)
                   .access()
                   .credentials(username, password)
                   # A development server's certificate is usually its own; drop this line, or
                   # point ca_cert_path() at the real CA, anywhere it matters.
                   .verify(False)
                   .login())
    except EuclidAuthenticationError as error:
        print(f"login refused: {error.reason or error}")
        return 1

    with session:
        ekv = session.ekv()
        name = f"pdk-walkthrough-{int(time.time())}"

        # Two keys: the partition key identifies a session's owner, the sort key orders that
        # owner's sessions in time - which is what makes the partition readable as a range.
        table = ekv.create_table(name, "userId", sort_key="startedAt", sort_key_type=NUMBER)
        print(f"created table {table.name}")
        print(f"  ern: {table.ern}")
        print(f"  keyed on {table.partition_key} ({table.partition_key_type}) "
              f"+ {table.sort_key} ({table.sort_key_type})")

        try:
            walk(ekv, name)
        finally:
            print(f"\ndeleted table {name} and the {ekv.delete_table(name)} item(s) in it")

    return 0


def walk(ekv, table: str) -> None:
    """Everything between creating the table and deleting it."""
    started = int(time.time())
    for offset, host in enumerate(["laptop", "desktop", "phone", "tablet"]):
        ekv.put_item(table, {"userId": "jens", "startedAt": started + offset, "host": host,
                             "tags": ["walkthrough"], "meta": {"agent": "euclid-pdk"}})
    ekv.put_item(table, {"userId": "alice", "startedAt": started, "host": "workstation"})
    print(f"\nwrote 5 items into {table}")

    # Scalars, lists and nested maps, stored as themselves - EKV holds documents rather than the
    # typed attribute maps a queue message carries.
    item = ekv.get_item(table, {"userId": "jens", "startedAt": started})
    print(f"\nread one item by key: {item.attributes}")
    print(f"  written {item.created}, last changed {item.modified} - kept out of the attributes, "
          f"so writing this item back does not add them to it")

    # The ordinary way to change one field: read, change, write the whole thing back. put-item
    # replaces rather than merges, so anything left out here would be gone.
    ekv.put_item(table, dict(item.attributes, host="laptop-2"))
    print(f"  changed one field: {ekv.get_item(table, {'userId': 'jens', 'startedAt': started})['host']}")

    missing = ekv.find_item(table, {"userId": "nobody", "startedAt": 0})
    print(f"\nfind_item on a key that names nothing: {missing}  "
          f"(get_item would raise - a miss and an empty item are different)")

    whole = ekv.query(table, "jens")
    print(f"\nquery, whole partition: {[i['host'] for i in whole.items]}")

    recent = ekv.query(table, "jens", GE, started + 2)
    print(f"query, sort key >= {started + 2}: {[i['host'] for i in recent.items]}")

    window = ekv.query(table, "jens", BETWEEN, started, started + 1)
    print(f"query, between {started} and {started + 1}: {[i['host'] for i in window.items]}")

    newest = ekv.query(table, "jens", forward=False, page_size=1)
    print(f"query, newest first, one item: {[i['host'] for i in newest.items]}")

    # begins-with is the one operator that is not a comparison, so it needs a string sort key -
    # this table's is a number, and the server says so rather than answering with nonsense.
    try:
        ekv.query(table, "jens", BEGINS_WITH, "2026-")
    except Exception as error:  # noqa: BLE001 - the point is what the server says
        print(f"query, begins-with on a number sort key: refused - {error}")

    scanned = ekv.scan(table, page_size=3)
    print(f"\nscan, first {len(scanned.items)} of {scanned.total}: "
          f"{[(i['userId'], i['host']) for i in scanned.items]}")
    print("  a scan reads the table rather than a partition: fine for an export, wrong for a lookup")

    described = ekv.describe_table(table)
    print(f"\n{described.name} holds {described.item_count} item(s)")

    tables = ekv.list_tables(page_size=5)
    print(f"{tables.total} table(s) in this namespace; first {len(tables.tables)}:")
    for listed in tables.tables:
        sort_key = f" + {listed.sort_key}" if listed.sort_key else ""
        print(f"  {listed.name:<32} {listed.partition_key}{sort_key:<16} {listed.item_count:>6} item(s)")


if __name__ == "__main__":
    sys.exit(main(sys.argv))
