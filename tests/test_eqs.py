"""EQS, end to end against a fake euclid server.

The queue actions are checked the way EAM's and ESM's are: what went on the wire, and what came
back off it. Receiving is checked against a queue stand-in that really leases messages out
(``fake_queues.py``), because the two things a queue client can get wrong - taking a message twice
and abandoning a long poll the server is still honouring - are invisible to a test whose server
always answers immediately.
"""

from __future__ import annotations

import pytest

from euclid import Euclid, EuclidServiceError, Variant
from euclid.dto.com import PRIORITY_HIGH
from euclid.modules.eqs import (EVERY_NAMESPACE, INSTALLATION_MAX_MESSAGE_LENGTH, MAX_DELAY,
                                MAX_VISIBILITY)
from euclid.modules import eqs as eqs_module
from fake_queues import FakeQueues, queue_ern
from test_eam import prepared

QUEUE = queue_ern("orders")


@pytest.fixture
def queues(gateway):
    """A gateway that answers a login, with a queue module behind it."""
    prepared(gateway)
    return FakeQueues().install(gateway)


@pytest.fixture
def eqs(gateway, queues):
    """An EQS client on a logged-in session, closed with it."""
    with Euclid.for_server(gateway.base_url).login("jens", "secret") as session:
        yield session.eqs()


# -- queues ------------------------------------------------------------------------------------


def test_create_and_list_queues(gateway, eqs):
    gateway.answer("eqs", "create-queue", {"name": "orders", "ern": QUEUE})
    gateway.answer("eqs", "list-queues", {"total": 2, "queues": [
        {"name": "orders", "ern": QUEUE, "owner": "jens", "tags": {"team": "sales"}, "size": 2048,
         "available": 3, "delayed": 1, "invisible": 2, "visibility": 60, "maxMessageLength": 262144,
         "maxReceiveCount": 5, "deadLetterQueueArn": queue_ern("orders-dlq"), "priority": "MIDDLE",
         "status": "AVAILABLE", "created": "2026-01-01"},
        {"name": "euclid-delivery", "internal": True},
    ]})

    created = eqs.create_queue("orders", visibility=60, max_retries=5, max_message_length=262144,
                               dlq_name="orders-dlq", delay=5, priority=PRIORITY_HIGH)
    assert (created.name, created.ern) == ("orders", QUEUE)
    assert gateway.last().json() == {"name": "orders", "visibility": 60, "maxRetries": 5,
                                     "maxMessageLength": 262144, "dlqName": "orders-dlq",
                                     "delay": 5, "priority": "HIGH", "internal": False}

    listed = eqs.list_queues(prefix="or", page_size=25, include_internal=True)
    assert gateway.last().json() == {"prefix": "or", "pageSize": 25, "pageIndex": 0,
                                     "sortColumn": "name", "sortDirection": "asc",
                                     "includeInternal": True}
    assert listed.total == 2
    assert [queue.name for queue in listed.queues] == ["orders", "euclid-delivery"]
    queue = listed.queues[0]
    assert (queue.available, queue.delayed, queue.invisible) == (3, 1, 2)
    assert queue.dead_letter_queue_ern == queue_ern("orders-dlq")
    assert queue.status == "AVAILABLE" and not queue.internal
    assert listed.queues[1].internal
    # A field the server did not send reads as empty rather than raising.
    assert listed.queues[1].tags == {} and listed.queues[1].visibility == 0


def test_queue_ern_metadata_and_tags(gateway, eqs):
    gateway.answer("eqs", "get-queue-ern", {"name": "orders", "ern": QUEUE})
    gateway.answer("eqs", "get-queue-metadata", {"region": "eu-central-1", "accountId": "000000000000",
                                                 "owner": "jens", "nameSpace": "development",
                                                 "name": "orders", "ern": QUEUE, "size": 2048,
                                                 "messages": 6})
    gateway.answer("eqs", "add-queue-tag", {})
    gateway.answer("eqs", "set-queue-tag", {})
    gateway.answer("eqs", "delete-queue-tag", {})

    assert eqs.get_queue_ern("orders") == QUEUE
    assert gateway.last().json() == {"name": "orders"}

    metadata = eqs.get_queue_metadata(QUEUE)
    assert (metadata.namespace, metadata.messages, metadata.size) == ("development", 6, 2048)

    eqs.add_queue_tag(QUEUE, "team", "sales")
    assert gateway.last().json() == {"ern": QUEUE, "key": "team", "value": "sales"}
    eqs.set_queue_tag(QUEUE, "team", "ops")
    eqs.delete_queue_tag(QUEUE, "team")
    assert gateway.last().json() == {"ern": QUEUE, "key": "team"}


def test_stopping_and_starting_a_queue(gateway, eqs):
    gateway.answer("eqs", "stop-queue", {"ern": QUEUE, "status": "STOPPED", "available": 4})
    gateway.answer("eqs", "start-queue", {"ern": QUEUE, "status": "AVAILABLE", "available": 4})

    stopped = eqs.stop_queue(QUEUE)
    assert (stopped.status, stopped.available) == ("STOPPED", 4)
    assert gateway.last().json() == {"ern": QUEUE}

    assert eqs.start_queue(QUEUE).status == "AVAILABLE"


def test_queue_visibility_comes_back_as_the_value_it_now_has(gateway, eqs):
    gateway.answer("eqs", "set-queue-visibility", {"ern": QUEUE, "visibility": 120})

    assert eqs.set_queue_visibility(QUEUE, 120) == 120
    assert gateway.last().json() == {"ern": QUEUE, "visibility": 120}


def test_a_visibility_no_message_could_take_says_so_before_the_round_trip(gateway, eqs):
    """One range for both setters, because a queue default outside what a single message may be
    given would be a figure no message could ever actually take."""
    gateway.answer("eqs", "set-queue-visibility", {"ern": QUEUE, "visibility": MAX_VISIBILITY})
    gateway.answer("eqs", "set-message-visibility", {})

    assert eqs.set_queue_visibility(QUEUE, MAX_VISIBILITY) == MAX_VISIBILITY
    eqs.set_message_visibility("message-1", MAX_VISIBILITY)

    for refused in (-1, MAX_VISIBILITY + 1):
        with pytest.raises(ValueError, match="between 0 and 43200"):
            eqs.set_queue_visibility(QUEUE, refused)
        with pytest.raises(ValueError, match="between 0 and 43200"):
            eqs.set_message_visibility("message-1", refused)

    # None of the four refusals reached the server.
    assert gateway.last().action == "set-message-visibility"


def test_queue_delay_comes_back_as_the_value_it_now_has(gateway, eqs):
    gateway.answer("eqs", "set-queue-delay", {"ern": QUEUE, "delay": 30})

    assert eqs.set_queue_delay(QUEUE, 30) == 30
    assert gateway.last().json() == {"ern": QUEUE, "delay": 30}


def test_a_delay_outside_what_a_queue_holds_says_so_before_the_round_trip(gateway, eqs):
    """Zero and the bound itself are a delay; anything past the bound is a schedule."""
    gateway.answer("eqs", "set-queue-delay", {"ern": QUEUE, "delay": 0})

    assert eqs.set_queue_delay(QUEUE, 0) == 0
    assert eqs.set_queue_delay(QUEUE, MAX_DELAY) == 0

    for refused in (-1, MAX_DELAY + 1):
        with pytest.raises(ValueError, match="between 0 and 900"):
            eqs.set_queue_delay(QUEUE, refused)

    # Nothing of the two refusals reached the server.
    assert gateway.last().json() == {"ern": QUEUE, "delay": MAX_DELAY}


def test_a_queues_message_limit_comes_back_as_stored_and_as_enforced(gateway, eqs):
    gateway.answer("eqs", "set-queue-max-message-length", {
        "ern": QUEUE, "maxMessageLength": 262144, "effectiveMaxMessageLength": 262144})

    result = eqs.set_queue_max_message_length(QUEUE, 262144)
    assert gateway.last().json() == {"ern": QUEUE, "maxMessageLength": 262144}
    assert (result.max_message_length, result.effective_max_message_length) == (262144, 262144)


def test_a_queue_with_no_limit_of_its_own_is_measured_against_the_installations(gateway, eqs):
    """Where the two numbers come apart: the queue stores nothing, and a send is still measured
    against something - reporting the stored zero alone would read as a queue that accepts nothing."""
    gateway.answer("eqs", "set-queue-max-message-length", {
        "ern": QUEUE, "maxMessageLength": 0, "effectiveMaxMessageLength": 1048576})

    result = eqs.set_queue_max_message_length(QUEUE, INSTALLATION_MAX_MESSAGE_LENGTH)
    assert gateway.last().json()["maxMessageLength"] == 0
    assert (result.max_message_length, result.effective_max_message_length) == (0, 1048576)


def test_a_negative_message_limit_says_so_before_the_round_trip(gateway, eqs):
    with pytest.raises(ValueError, match="cannot be negative"):
        eqs.set_queue_max_message_length(QUEUE, -1)


def test_purging_defaults_to_the_sessions_own_namespace(gateway, eqs):
    """The namespace the caller can see, rather than every namespace of the account - which is the
    same request with one field emptied, and a much larger thing to have asked for by accident."""
    gateway.answer("eam", "change-namespace", {})
    gateway.answer("eqs", "purge-all-queues", {})

    eqs.purge_all_queues()
    assert gateway.last().json() == {"region": "eu-central-1", "accountId": "000000000000",
                                     "nameSpace": ""}

    eqs.session.change_namespace("development")
    eqs.purge_all_queues()
    assert gateway.last().json()["nameSpace"] == "development"

    # Naming one reaches past the session's scope, which is what an administrator tidying up wants.
    eqs.purge_all_queues(namespace="staging")
    assert gateway.last().json()["nameSpace"] == "staging"

    # And emptying it deliberately is how every namespace of the account is asked for.
    eqs.purge_all_queues(namespace=EVERY_NAMESPACE)
    assert gateway.last().json()["nameSpace"] == ""


def test_redriving_a_dead_letter_queue(gateway, eqs):
    """What it moved, where it went, and what it left behind - which is not a failure: a message
    whose origin was never recorded is left alone rather than guessed at."""
    gateway.answer("eqs", "redrive-dlq", {
        "ern": queue_ern("orders-dlq"), "messages": 7, "remaining": 2,
        "targets": [{"queueErn": QUEUE, "messages": 5}, {"queueErn": queue_ern("refunds"), "messages": 2}],
        "note": "Messages remain in the dead letter queue because no source queue is recorded for them."})

    result = eqs.redrive_dlq(queue_ern("orders-dlq"))

    assert gateway.last().json() == {"ern": queue_ern("orders-dlq"), "targetErn": ""}
    assert (result.messages, result.remaining) == (7, 2)
    assert [(t.queue_ern, t.messages) for t in result.targets] == [(QUEUE, 5), (queue_ern("refunds"), 2)]
    assert result.note.startswith("Messages remain")


# -- messages ------------------------------------------------------------------------------------


def test_send_receive_and_delete_round_trip(gateway, eqs, queues):
    message_id = eqs.send_message(QUEUE, '{"order": 17}', attributes={"tenant": "acme", "retries": 3},
                                  priority=PRIORITY_HIGH)

    assert message_id == "message-1"
    assert gateway.last().json() == {
        "ern": QUEUE, "body": '{"order": 17}', "priority": "HIGH",
        "attributes": {"tenant": {"type": "string", "value": "acme"},
                       "retries": {"type": "long", "value": 3}}}

    received = eqs.receive_messages(QUEUE)
    assert [m.body for m in received.messages] == ['{"order": 17}']
    message = received.messages[0]
    assert message.receipt_handle and message.received_count == 1
    assert message.attributes["tenant"] == Variant("string", "acme")

    # The lease is what makes a second consumer see nothing while the first is still working.
    assert eqs.receive_messages(QUEUE).messages == []

    eqs.delete_message(message.receipt_handle)
    assert eqs.get_message_count(QUEUE).total == 0


def test_the_envelope_travels_separately_from_the_senders_attributes(gateway, eqs, queues):
    eqs.send_message(QUEUE, "body", attributes={"tenant": "acme"},
                     system_attributes={"priority": "LOW", "origin": "esm"})

    assert gateway.last().json()["attributes"] == {"tenant": {"type": "string", "value": "acme"}}
    assert gateway.last().json()["systemAttributes"] == {
        "priority": {"type": "string", "value": "LOW"}, "origin": {"type": "string", "value": "esm"}}


def test_a_message_with_nothing_to_say_says_nothing(gateway, eqs, queues):
    """No priority and no envelope: the fields are left out rather than sent empty, so the queue's
    own default is what applies."""
    eqs.send_message(QUEUE, "body")

    assert gateway.last().json() == {"ern": QUEUE, "body": "body", "attributes": {}}


def test_deleting_a_message_that_was_never_received(gateway, eqs, queues):
    """By ID rather than by receipt handle - a euclid extension, and the only way to remove a
    message nobody has taken."""
    message_id = eqs.send_message(QUEUE, "body")

    eqs.delete_message_by_id(message_id)

    assert gateway.last().json() == {"messageId": message_id}
    assert eqs.get_message_count(QUEUE).total == 0


def test_listing_messages_does_not_lease_them(gateway, eqs, queues):
    eqs.send_message(QUEUE, "body")

    listed = eqs.list_messages(QUEUE, page_size=50, sort_direction="desc")

    assert gateway.last().json() == {"queueErn": QUEUE, "pageSize": 50, "pageIndex": 0,
                                     "sortColumn": "created", "sortDirection": "desc"}
    assert [m.body for m in listed.messages] == ["body"]
    # Still there to be received, which is the whole difference between listing and receiving.
    assert eqs.get_message_count(QUEUE).available == 1


def test_receive_all_messages_drains_the_queue_in_batches(gateway, eqs, queues):
    for _ in range(5):
        eqs.send_message(QUEUE, "body")

    messages = eqs.receive_all_messages(QUEUE, batch_size=2)

    assert len(messages) == 5
    assert len({m.receipt_handle for m in messages}) == 5


def test_message_metadata_and_visibility(gateway, eqs):
    gateway.answer("eqs", "get-message-metadata", {"messageId": "message-1", "queueErn": QUEUE,
                                                   "receiptHandle": "receipt-1", "status": "INVISIBLE",
                                                   "priority": "MIDDLE", "size": 13, "receivedCount": 2,
                                                   "visibilityTimeout": 30,
                                                   "contentType": "application/json"})
    gateway.answer("eqs", "set-message-visibility", {})

    metadata = eqs.get_message_metadata("message-1")
    assert (metadata.received_count, metadata.visibility_timeout, metadata.status) == (2, 30, "INVISIBLE")

    eqs.set_message_visibility("message-1", 120)
    assert gateway.last().action == "set-message-visibility"
    assert gateway.last().json() == {"messageId": "message-1", "visibility": 120}


def test_message_attributes_are_typed_and_use_the_servers_own_field_names(gateway, eqs):
    """``name`` on the way in, ``key`` on the way out: the server's asymmetry, reproduced rather
    than papered over."""
    gateway.answer("eqs", "get-message-attribute", {"messageId": "message-1", "name": "tenant",
                                                    "value": {"type": "string", "value": "acme"}})
    gateway.answer("eqs", "set-message-attribute", {"messageId": "message-1", "name": "retries",
                                                    "value": {"type": "long", "value": 3}})

    attribute = eqs.get_message_attribute("message-1", "tenant")
    assert gateway.last().json() == {"messageId": "message-1", "name": "tenant"}
    assert attribute.value == Variant("string", "acme")

    updated = eqs.set_message_attribute("message-1", "retries", 3)
    assert gateway.last().json() == {"messageId": "message-1", "key": "retries",
                                     "value": {"type": "long", "value": 3}}
    assert updated.value == Variant("long", 3)


# -- long polling ----------------------------------------------------------------------------------


def test_an_empty_queue_costs_no_receive_at_all(gateway, eqs, queues):
    """A receive is a write; one that takes nothing is work the server did for nothing, so with no
    wait asked for the depth is checked first."""
    assert eqs.receive_messages(QUEUE).messages == []

    assert [r.action for r in gateway.requests if r.target == "eqs"] == ["get-message-count"]


def test_a_long_poll_the_server_honours_is_one_request(gateway, eqs, queues):
    result = eqs.receive_messages(QUEUE, wait_time=1)

    assert result.messages == []
    assert queues.waits == [1]
    assert len([r for r in gateway.requests if r.action == "receive-messages"]) == 1


def test_a_long_poll_the_server_declines_is_asked_again(gateway, eqs, queues, monkeypatch):
    """With no slot free the server answers at once rather than waiting, which comes back empty with
    time still on the clock. The answer is to pause and ask again - asking again immediately is what
    a server short of threads does not need."""
    monkeypatch.setattr(eqs_module, "SLOTS_BUSY_BACKOFF", 0.01)
    queues.decline_waits = 2
    eqs.send_message(QUEUE, "body")

    result = eqs.receive_messages(QUEUE, wait_time=5)

    assert [m.body for m in result.messages] == ["body"]
    assert len(queues.waits) == 3
    # Each attempt asks for what is left of the caller's window, not for the whole of it again.
    assert queues.waits[0] == 5 and queues.waits[-1] <= 5


def test_a_long_poll_outlives_the_sessions_ordinary_timeout(gateway, queues):
    """The request is meant to take as long as the server was asked to hold it, so it gets its own
    deadline; the client-wide one is sized for an answer that comes straight back."""
    prepared(gateway)
    session = Euclid.for_server(gateway.base_url).login("jens", "secret", timeout=0.3)
    queues.max_hold = 1.0

    with session:
        # Held for a second, which is more than three times the timeout every other call gets.
        assert session.eqs().receive_messages(QUEUE, wait_time=2).messages == []


# -- everything else --------------------------------------------------------------------------------


def test_internal_traffic_is_marked_as_such(gateway, eqs, queues):
    """The same get-message-count is a user's question one moment and a metric collector's poll the
    next, so the caller says which it is rather than the server guessing from a rate."""
    internal = eqs.as_internal()

    internal.get_message_count(QUEUE)
    assert gateway.last().headers["x-euclid-internal"] == "true"

    # The view is separate, so nothing has to remember to set the flag back.
    eqs.get_message_count(QUEUE)
    assert "x-euclid-internal" not in gateway.last().headers


def test_eqs_is_signed_and_follows_the_session(gateway, queues):
    gateway.answer("eam", "change-namespace", {})

    with Euclid.for_server(gateway.base_url).login("jens", "secret") as session:
        eqs = session.eqs()
        eqs.get_message_count(QUEUE)
        assert gateway.last().auth == "sigv4"
        assert gateway.last().headers["x-euclid-target"] == "eqs"
        assert "x-euclid-namespace" not in gateway.last().headers

        session.change_namespace("development")
        eqs.get_message_count(QUEUE)
        assert gateway.last().headers["x-euclid-namespace"] == "development"

        assert session.eqs() is eqs


def test_a_refusal_carries_the_servers_reason(gateway, eqs):
    gateway.answer("eqs", "receive-messages", {"error": "Queue is stopped, ern: " + QUEUE}, status=409)

    with pytest.raises(EuclidServiceError) as raised:
        eqs.receive_messages(QUEUE, wait_time=1)

    assert (raised.value.target, raised.value.action, raised.value.status) == ("eqs", "receive-messages", 409)
    assert raised.value.reason.startswith("Queue is stopped")


def test_call_and_metrics(gateway, eqs):
    gateway.answer("eqs", "get-metrics", {"items": [{"name": "eqs-messages", "value": 3}]})
    gateway.answer("eqs", "some-future-action", {"ok": True})

    assert eqs.metrics() == {"items": [{"name": "eqs-messages", "value": 3}]}
    assert eqs.call("some-future-action", {"x": 1}) == {"ok": True}
    assert gateway.last().json() == {"x": 1}
