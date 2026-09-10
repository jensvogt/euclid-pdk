#!/usr/bin/env python3
"""EES end to end: subscribe to storage events, make some happen, and consume them.

    python examples/events_walkthrough.py https://euclid.example.com jens secret

Works in a bucket and under a subscriber name of its own, both named after the moment it started,
and removes both at the end - so it is safe to point at a running server. The events it consumes are
the ones it caused itself, which is what makes this runnable without waiting for somebody else's
work to show up.
"""

from __future__ import annotations

import sys
import time

from euclid import Euclid, EuclidAuthenticationError, EuclidServiceError
from euclid.modules.ees import OBJECT_CREATED, OBJECT_DELETED


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
        esm, ees = session.esm(), session.ees()
        name = f"pdk-walkthrough-{int(time.time())}"

        bucket = esm.create_bucket(name)
        print(f"created bucket {bucket.name}")

        try:
            walk(esm, ees, bucket.ern, name)
        finally:
            removed = ees.unsubscribe_events(name)
            print(f"\nunsubscribed {name}: {removed.removed} subscription(s) removed")
            esm.purge_bucket(bucket.ern)
            esm.delete_bucket(bucket.ern)
            print(f"deleted bucket {name}")

    return 0


def walk(esm, ees, bucket_ern: str, name: str) -> None:
    """Everything between creating the bucket and taking it all down again."""
    # The filter is evaluated where the event is published, so this subscriber only ever
    # accumulates objects written under invoices/2026/ in this one bucket - not every object
    # written anywhere in the installation.
    subscriptions = ees.subscribe_events(name, [OBJECT_CREATED, OBJECT_DELETED],
                                         {"bucketErn": bucket_ern, "prefix": "invoices/2026/"})
    print(f"\nsubscribed {name} to:")
    for subscription in subscriptions:
        print(f"  {subscription.event_type:<22} {subscription.mode:<8} filter={subscription.filter}")

    # Three writes: two the subscription asked for, one it did not.
    esm.put_object(bucket_ern, "invoices/2026/q3.pdf", b"a third-quarter invoice")
    esm.put_object(bucket_ern, "invoices/2026/q4.pdf", b"a fourth-quarter invoice")
    esm.put_object(bucket_ern, "notes/hello.txt", b"not an invoice")
    print("\nwrote 3 objects, two of which match the filter")

    waiting = ees.list_subscriptions(name).waiting
    print(f"waiting for {name}: {waiting} event(s)")

    consumed = 0
    while True:
        # The wait is the server's: an idle subscriber costs one request per window rather than one
        # per poll tick. Twenty seconds is the server's own cap.
        result = ees.receive_events(name, max_events=10, wait_time=5)
        if not result.events:
            break

        for event in result.events:
            # The payload is the event type's own shape - flat, so a filter can match it.
            print(f"\n  {event.event_type}  (attempt {event.attempts}, from {event.source_module})")
            print(f"      {event['key']}  {event.get('size')} bytes  {event.get('contentType')}")
            print(f"      uploaded by {event.get('owner')}, changed by {event.get('userId')}")

            # After the work, not before: an event claimed and never acknowledged comes back when
            # its visibility timeout runs out, which is what makes a consumer that dies harmless.
            receipt = ees.ack_event(name, event.event_id)
            print(f"      acknowledged; {receipt.waiting} still waiting")
            consumed += 1

    print(f"\nconsumed {consumed} event(s)")

    # Deleting one of them produces an event of the other type this subscriber asked for.
    esm.delete_objects(bucket_ern, ["invoices/2026/q3.pdf"])
    deleted = ees.receive_events(name, wait_time=5)
    print(f"after deleting an object: {[e.event_type for e in deleted.events]}")
    if deleted.events:
        ees.ack_events(name, [event.event_id for event in deleted.events])

    try:
        print(f"\nEES metrics: {list(ees.metrics())}")
    except EuclidServiceError as error:
        print(f"\nEES metrics unavailable: {error.reason or error}")


if __name__ == "__main__":
    sys.exit(main(sys.argv))
