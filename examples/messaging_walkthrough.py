#!/usr/bin/env python3
"""EQS and ENS end to end: a topic, a queue subscribed to it, and a message that travels.

    python examples/messaging_walkthrough.py https://euclid.example.com jens secret

Works in a queue and a topic of its own, named after the moment it started, and deletes both again
at the end - so it is safe to point at a running server, and a run that dies halfway leaves two
obviously disposable resources behind rather than touching anything of yours.
"""

from __future__ import annotations

import sys
import time

from euclid import Euclid, EuclidAuthenticationError
from euclid.dto.com import PRIORITY_HIGH


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
        eqs, ens = session.eqs(), session.ens()
        name = f"pdk-walkthrough-{int(time.time())}"

        queue = eqs.create_queue(name, visibility=30)
        topic = ens.create_topic(name)
        print(f"created queue {queue.name}\n  {queue.ern}")
        print(f"created topic {topic.name}\n  {topic.ern}")

        try:
            walk(eqs, ens, queue.ern, topic.ern)
        finally:
            ens.delete_topic(topic.ern)
            eqs.purge_queue(queue.ern)
            eqs.delete_queue(queue.ern)
            print(f"\ndeleted queue and topic {name} again")

    return 0


def walk(eqs, ens, queue_ern: str, topic_ern: str) -> None:
    """Everything between creating the two and deleting them."""
    # Sent straight to the queue: one message, one consumer, and the lease below is what makes it
    # exactly one.
    message_id = eqs.send_message(queue_ern, '{"order": 17}', attributes={"tenant": "acme"},
                                  priority=PRIORITY_HIGH)
    print(f"\nsent    {message_id} to the queue")

    # Published to the topic instead, which delivers a copy to every subscription on it.
    subscription = ens.subscribe(topic_ern, queue_ern)
    print(f"subscribed the queue to the topic as {subscription.ern}")
    ens.publish_message(topic_ern, '{"order": 18}', attributes={"tenant": "acme"})
    print("published one message to the topic, which delivers it to the queue")

    counts = eqs.get_message_count(queue_ern)
    print(f"\nqueue holds {counts.total}: {counts.available} available, {counts.delayed} delayed, "
          f"{counts.invisible} in flight")

    # A long poll: the server holds this open until something lands or the window runs out, so the
    # delivery from the topic is waited for rather than polled for.
    received = eqs.receive_messages(queue_ern, max_messages=10, wait_time=10)
    print(f"\nreceived {len(received.messages)} message(s):")
    for message in received.messages:
        attributes = {name: variant.value for name, variant in message.attributes.items()}
        print(f"  {message.message_id:<38} {message.priority:<7} {message.body}")
        print(f"      attributes: {attributes}")
        # After the work, not before: a consumer that dies instead simply stops holding the lease,
        # and the message comes back for somebody else.
        eqs.delete_message(message.receipt_handle)
        print("      deleted with its receipt handle")

    print(f"\ntopic counters: {ens.get_message_count(topic_ern)}")
    print(f"subscriptions:  {[s.target_ern for s in ens.list_subscriptions(topic_ern)]}")

    ens.unsubscribe(subscription.ern)
    print(f"unsubscribed {subscription.ern}")

    print(f"\nqueue now holds {eqs.get_message_count(queue_ern).total} message(s)")


if __name__ == "__main__":
    sys.exit(main(sys.argv))
