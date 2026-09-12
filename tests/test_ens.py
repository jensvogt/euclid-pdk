"""ENS, end to end against a fake euclid server.

Topics have no receive and no lease, so there is nothing here that needs a stateful stand-in the way
EQS's long poll does: every action is one request, and what these check is that it carries the
fields the server reads and parses the ones it answers with.
"""

from __future__ import annotations

import pytest

from euclid import Euclid, EuclidServiceError, Variant
from euclid.dto.com import PRIORITY_HIGH
from euclid.modules import ens as ens_module
from euclid.modules.ens import EVERY_NAMESPACE, INSTALLATION_RETENTION, RETENTION_FOREVER, RUNNING, STOPPED
from fake_queues import queue_ern
from test_eam import prepared

TOPIC = "ern:euclid:ens:eu-central-1:000000000000:topic/order-events"
QUEUE = queue_ern("orders")


@pytest.fixture
def ens(gateway):
    """An ENS client on a logged-in session, closed with it."""
    prepared(gateway)
    with Euclid.for_server(gateway.base_url).login("jens", "secret") as session:
        yield session.ens()


# -- topics ------------------------------------------------------------------------------------


def test_create_and_list_topics(gateway, ens):
    gateway.answer("ens", "create-topic", {"name": "order-events", "ern": TOPIC})
    gateway.answer("ens", "list-topics", {"total": 2, "topics": [
        {"name": "order-events", "ern": TOPIC, "owner": "jens", "tags": {"team": "sales"},
         "size": 4096, "messages": 12, "maxMessageLength": 262144, "created": "2026-01-01"},
        {"name": "audit"},
    ]})

    created = ens.create_topic("order-events", max_message_length=262144)
    assert (created.name, created.ern) == ("order-events", TOPIC)
    assert gateway.last().json() == {"name": "order-events", "maxMessageLength": 262144}

    listed = ens.list_topics(prefix="order", page_size=25, sort_direction="desc")
    assert gateway.last().json() == {"prefix": "order", "pageSize": 25, "pageIndex": 0,
                                     "sortColumn": "name", "sortDirection": "desc"}
    assert listed.total == 2
    assert [topic.name for topic in listed.topics] == ["order-events", "audit"]
    assert listed.topics[0].tags == {"team": "sales"} and listed.topics[0].messages == 12
    # A field the server did not send reads as empty rather than raising.
    assert listed.topics[1].owner == "" and listed.topics[1].max_message_length == 0


def test_topic_ern_metadata_and_tags(gateway, ens):
    gateway.answer("ens", "get-topic-ern", {"name": "order-events", "ern": TOPIC})
    gateway.answer("ens", "get-topic-metadata", {"region": "eu-central-1", "accountId": "000000000000",
                                                 "owner": "jens", "nameSpace": "development",
                                                 "name": "order-events", "ern": TOPIC, "size": 4096,
                                                 "messages": 12})
    gateway.answer("ens", "add-topic-tag", {})
    gateway.answer("ens", "set-topic-tag", {})
    gateway.answer("ens", "delete-topic-tag", {})

    assert ens.get_topic_ern("order-events") == TOPIC
    assert gateway.last().json() == {"name": "order-events"}

    metadata = ens.get_topic_metadata(TOPIC)
    assert (metadata.namespace, metadata.messages, metadata.size) == ("development", 12, 4096)

    ens.add_topic_tag(TOPIC, "team", "sales")
    assert gateway.last().json() == {"ern": TOPIC, "key": "team", "value": "sales"}
    ens.set_topic_tag(TOPIC, "team", "ops")
    ens.delete_topic_tag(TOPIC, "team")
    assert gateway.last().json() == {"ern": TOPIC, "key": "team"}


def test_stopping_a_topic_holds_delivery_rather_than_refusing_publishers(gateway, ens):
    """A subscriber being redeployed is a reason to hold what arrives, not to lose it."""
    gateway.answer("ens", "stop-topic", {"ern": TOPIC, "status": "STOPPED", "released": 0})

    stopped = ens.stop_topic(TOPIC)

    assert gateway.last().json() == {"ern": TOPIC}
    assert (stopped.ern, stopped.status, stopped.released) == (TOPIC, STOPPED, 0)


def test_starting_a_topic_delivers_what_it_held(gateway, ens):
    """The backlog goes out as part of the call, oldest first, and the count says how much did."""
    gateway.answer("ens", "start-topic", {"ern": TOPIC, "status": "RUNNING", "released": 137})

    started = ens.start_topic(TOPIC)

    assert gateway.last().json() == {"ern": TOPIC}
    assert (started.status, started.released) == (RUNNING, 137)


def test_starting_a_topic_that_was_never_stopped_releases_nothing(gateway, ens):
    """Not an error: there is nothing held, so there is nothing to hand over."""
    gateway.answer("ens", "start-topic", {"ern": TOPIC, "status": "RUNNING", "released": 0})

    assert ens.start_topic(TOPIC).released == 0


def test_a_topic_says_whether_it_is_delivering_and_how_much_is_held(gateway, ens):
    gateway.answer("ens", "get-topic-metadata", {"region": "eu-central-1", "accountId": "000000000000",
                                                 "owner": "jens", "nameSpace": "development",
                                                 "name": "order-events", "ern": TOPIC, "size": 4096,
                                                 "messages": 12, "status": "STOPPED",
                                                 "retentionPeriod": 604800, "held": 137})
    gateway.answer("ens", "list-topics", {"total": 1, "topics": [
        {"name": "order-events", "ern": TOPIC, "status": "RUNNING", "retentionPeriod": 0}]})

    metadata = ens.get_topic_metadata(TOPIC)
    assert (metadata.status, metadata.held, metadata.retention_period) == (STOPPED, 137, 604800)

    # And a listing carries the same two, so "which of these is stopped" is one call.
    topic = ens.list_topics().topics[0]
    assert topic.status == RUNNING
    # Zero is not "no retention" but "whatever the installation says".
    assert topic.retention_period == INSTALLATION_RETENTION


def test_setting_how_long_a_topic_keeps_its_messages(gateway, ens):
    """Nothing consumes a topic's messages - it is fanned out at publish time - so without this the
    collection only grows, and every topic shares it."""
    gateway.answer("ens", "set-topic-retention", {"ern": TOPIC, "retentionPeriod": 604800})

    result = ens.set_topic_retention(TOPIC, 604800)

    assert gateway.last().json() == {"ern": TOPIC, "retentionPeriod": 604800}
    assert (result.ern, result.retention_period) == (TOPIC, 604800)


def test_a_retention_of_zero_hands_the_topic_back_to_the_installation(gateway, ens):
    gateway.answer("ens", "set-topic-retention", {"ern": TOPIC, "retentionPeriod": 0})

    assert ens.set_topic_retention(TOPIC, INSTALLATION_RETENTION).retention_period == 0
    assert gateway.last().json()["retentionPeriod"] == 0


def test_a_retention_of_minus_one_keeps_everything(gateway, ens):
    """The one negative that means something: the server stores such a message with no expiry at
    all rather than with a very distant one, so nothing ever removes it."""
    gateway.answer("ens", "set-topic-retention", {"ern": TOPIC, "retentionPeriod": -1})

    assert ens.set_topic_retention(TOPIC, RETENTION_FOREVER).retention_period == RETENTION_FOREVER
    assert gateway.last().json()["retentionPeriod"] == -1


def test_a_retention_below_minus_one_says_so_before_the_round_trip(gateway, ens):
    with pytest.raises(ValueError, match="keep messages forever"):
        ens.set_topic_retention(TOPIC, -2)

    assert [r for r in gateway.requests if r.target == "ens"] == []


def test_setting_the_largest_message_a_topic_takes(gateway, ens):
    gateway.answer("ens", "set-topic-max-message-length", {"ern": TOPIC, "maxMessageLength": 262144})

    result = ens.set_topic_max_message_length(TOPIC, 262144)
    assert gateway.last().json() == {"ern": TOPIC, "maxMessageLength": 262144}
    assert (result.ern, result.max_message_length) == (TOPIC, 262144)


def test_a_topic_will_not_take_the_zero_a_queue_does(gateway, ens):
    """Not the same rule as EQS's. A queue reads zero as "follow the installation's default"; a
    topic would read it as one that accepts nothing, and stop_topic is how that is asked for -
    reversibly, and without losing what is published meanwhile."""
    for refused in (0, -1):
        with pytest.raises(ValueError, match="positive number of bytes"):
            ens.set_topic_max_message_length(TOPIC, refused)

    assert [r for r in gateway.requests if r.target == "ens"] == []


def test_purging_and_deleting(gateway, ens):
    gateway.answer("ens", "purge-topic", {})
    gateway.answer("ens", "purge-all-topics", {})
    gateway.answer("ens", "delete-topic", {})

    ens.purge_topic(TOPIC)
    assert gateway.last().json() == {"ern": TOPIC}

    # Defaults to the session's own account, region and namespace.
    ens.purge_all_topics()
    assert gateway.last().json() == {"region": "eu-central-1", "accountId": "000000000000",
                                     "nameSpace": ""}

    # Emptying the namespace deliberately is how every namespace of the account is asked for.
    ens.purge_all_topics(namespace=EVERY_NAMESPACE)
    assert gateway.last().json()["nameSpace"] == ""

    ens.delete_topic(TOPIC)
    assert gateway.last().action == "delete-topic"


# -- messages ------------------------------------------------------------------------------------


def test_publishing_returns_the_id_the_server_gave_the_message(gateway, ens):
    gateway.answer("ens", "publish-message", {"messageId": "message-1"})

    message_id = ens.publish_message(TOPIC, '{"order": 17}', attributes={"tenant": "acme", "retries": 3},
                                     priority=PRIORITY_HIGH)

    assert message_id == "message-1"
    assert gateway.last().json() == {
        "ern": TOPIC, "body": '{"order": 17}', "priority": "HIGH",
        "attributes": {"tenant": {"type": "string", "value": "acme"},
                       "retries": {"type": "long", "value": 3}}}


def test_publishing_without_a_priority_leaves_the_field_out(gateway, ens):
    """So the topic's own default applies rather than an empty string the server would refuse."""
    gateway.answer("ens", "publish-message", {"messageId": "message-1"})

    ens.publish_message(TOPIC, "body")

    assert gateway.last().json() == {"ern": TOPIC, "body": "body", "attributes": {}}


def test_listing_a_topics_messages(gateway, ens):
    gateway.answer("ens", "list-messages", {"total": 1, "messages": [
        {"ern": f"{TOPIC}/message/1", "topicErn": TOPIC, "messageId": "message-1", "status": "SENT",
         "body": '{"order": 17}', "contentType": "application/json",
         "attributes": {"tenant": {"type": "string", "value": "acme"}},
         "created": "2026-01-01"}]})

    listed = ens.list_messages(TOPIC, page_size=50)

    assert gateway.last().json() == {"topicErn": TOPIC, "pageSize": 50, "pageIndex": 0,
                                     "sortColumn": "created", "sortDirection": "asc"}
    assert listed.total == 1
    assert listed.messages[0].topic_ern == TOPIC
    assert listed.messages[0].attributes["tenant"] == Variant("string", "acme")


def test_a_topics_counters_are_not_a_queues(gateway, ens):
    """A topic does not hold a backlog, so it counts delivery: what is on it, what went out, and
    what had to go out again."""
    gateway.answer("ens", "get-message-count", {"ern": TOPIC, "available": 12, "send": 30, "resend": 2})

    count = ens.get_message_count(TOPIC)

    assert (count.available, count.send, count.resend) == (12, 30, 2)


def test_message_attributes_travel_under_the_key_ens_uses(gateway, ens):
    """``key`` throughout ENS, where EQS mostly says ``name`` - the server's own asymmetry."""
    gateway.answer("ens", "get-message-attribute", {"messageId": "message-1", "key": "tenant",
                                                    "value": {"type": "string", "value": "acme"}})
    gateway.answer("ens", "set-message-attribute", {"messageId": "message-1", "key": "retries",
                                                    "value": {"type": "long", "value": 3}})

    attribute = ens.get_message_attribute("message-1", "tenant")
    assert gateway.last().json() == {"messageId": "message-1", "key": "tenant"}
    assert (attribute.key, attribute.value) == ("tenant", Variant("string", "acme"))

    updated = ens.set_message_attribute("message-1", "retries", 3)
    assert gateway.last().json() == {"messageId": "message-1", "key": "retries",
                                     "value": {"type": "long", "value": 3}}
    assert updated.value == Variant("long", 3)


# -- subscriptions ----------------------------------------------------------------------------------


def test_subscribing_a_queue_to_a_topic(gateway, ens):
    gateway.answer("ens", "subscribe", {"ern": "ern:ens:subscription/1", "sourceErn": TOPIC,
                                        "type": "SQS", "targetErn": QUEUE})
    gateway.answer("ens", "list-subscriptions", {"total": 1, "subscriptions": [
        {"ern": "ern:ens:subscription/1", "sourceErn": TOPIC, "type": "SQS", "targetErn": QUEUE,
         "created": "2026-01-01"}]})
    gateway.answer("ens", "unsubscribe", {})

    created = ens.subscribe(TOPIC, QUEUE)

    assert gateway.last().json() == {"sourceErn": TOPIC, "type": "SQS", "targetErn": QUEUE}
    assert created.target_ern == QUEUE

    assert [s.target_ern for s in ens.list_subscriptions(TOPIC)] == [QUEUE]
    assert gateway.last().json() == {"topicErn": TOPIC}

    # The subscription's own ERN, not the topic's and not the queue's.
    ens.unsubscribe(created.ern)
    assert gateway.last().json() == {"ern": "ern:ens:subscription/1"}


def test_the_delivery_protocol_can_be_named(gateway, ens):
    gateway.answer("ens", "subscribe", {"ern": "ern:ens:subscription/1", "type": "SQS"})

    ens.subscribe(TOPIC, QUEUE, target_type=ens_module.QUEUE)

    assert gateway.last().json()["type"] == "SQS"


# -- everything else ----------------------------------------------------------------------------------


def test_ens_is_signed_and_follows_the_session(gateway):
    prepared(gateway)
    gateway.answer("eam", "change-namespace", {})
    gateway.answer("ens", "get-topic-ern", {"ern": TOPIC})

    with Euclid.for_server(gateway.base_url).login("jens", "secret") as session:
        ens = session.ens()
        ens.get_topic_ern("order-events")
        assert gateway.last().auth == "sigv4"
        assert gateway.last().headers["x-euclid-target"] == "ens"

        session.change_namespace("development")
        ens.get_topic_ern("order-events")
        assert gateway.last().headers["x-euclid-namespace"] == "development"

        assert session.ens() is ens


def test_the_modules_of_one_session_are_separate_clients(gateway):
    """Three modules, three clients, one session - and one call each proves they route to their own
    target rather than to whichever was asked for first."""
    prepared(gateway)
    gateway.answer("ens", "get-topic-ern", {"ern": TOPIC})
    gateway.answer("eqs", "get-queue-ern", {"ern": QUEUE})
    gateway.answer("esm", "get-bucket-ern", {"ern": "ern:esm:bucket/reports"})

    with Euclid.for_server(gateway.base_url).login("jens", "secret") as session:
        session.ens().get_topic_ern("order-events")
        session.eqs().get_queue_ern("orders")
        session.esm().get_bucket_ern("reports")

    assert [r.target for r in gateway.requests if r.target != "eam"] == ["ens", "eqs", "esm"]
    # Each signed for its own module: the target is signed, so a client signing for another one
    # would have been refused by the gateway rather than answered.
    assert {r.auth for r in gateway.requests if r.target != "eam"} == {"sigv4"}


def test_a_refusal_carries_the_servers_reason(gateway, ens):
    gateway.answer("ens", "publish-message", {"error": "Message too long"}, status=400)

    with pytest.raises(EuclidServiceError) as raised:
        ens.publish_message(TOPIC, "x" * 10)

    assert (raised.value.target, raised.value.action, raised.value.status) == ("ens", "publish-message", 400)
    assert raised.value.reason == "Message too long"


def test_call_reaches_an_ens_action_this_sdk_does_not_wrap(gateway, ens):
    gateway.answer("ens", "some-future-action", {"ok": True})

    assert ens.call("some-future-action", {"x": 1}) == {"ok": True}
    assert gateway.last().json() == {"x": 1}
