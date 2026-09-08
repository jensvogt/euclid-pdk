"""ENS - euclid's notification module: topics, published messages, and the subscriptions that
deliver them onward.

One object, :class:`EuclidEns`, built from a session that has already logged in::

    ens = Euclid.for_server(url).login("jens", "secret").ens()
    topic = ens.create_topic("order-events")

    ens.subscribe(topic.ern, eqs.get_queue_ern("orders"))
    ens.publish_message(topic.ern, '{"order": 17}')

The difference from EQS is what happens to a message once it is there. A queue holds a message
until a consumer takes it; a topic hands each message to every subscriber and keeps it as a record
of having done so. So there is no receive here, and no receipt handle: a subscriber consumes from
its own queue, which is where the message was delivered.
"""

from __future__ import annotations

from typing import Any, Mapping

from ..dto.com import Variant
from ..dto.ens import (CreateTopicResult, ListTopicsResult, MessageAttribute, MessageCount,
                       MessagesResult, SubscribeResult, Subscription, TopicMetadata)
from .base import ModuleClient

__all__ = ["EuclidEns", "TARGET", "QUEUE", "DEFAULT_MAX_MESSAGE_LENGTH"]

TARGET = "ens"

#: The only delivery protocol there is so far: a subscription's target is an EQS queue.
QUEUE = "SQS"

#: The largest message a topic accepts, in bytes.
DEFAULT_MAX_MESSAGE_LENGTH = 1024 * 1024


class EuclidEns(ModuleClient):
    """ENS's operations, on the credentials of the session that created it.

    Built by :meth:`euclid.EuclidSession.ens` rather than directly, so that it shares that
    session's identity, namespace and connection settings - and follows them as they change.
    """

    target = TARGET

    # -- topics ------------------------------------------------------------------------------

    def create_topic(self, name: str,
                     max_message_length: int = DEFAULT_MAX_MESSAGE_LENGTH) -> CreateTopicResult:
        """Creates a topic, and returns the ERN everything else names it by."""
        return CreateTopicResult.from_json(self._call("create-topic", {
            "name": name, "maxMessageLength": max_message_length}))

    def delete_topic(self, ern: str) -> None:
        """Deletes a topic, its messages and its subscriptions."""
        self._call("delete-topic", {"ern": ern})

    def list_topics(self, prefix: str = "", page_size: int = 10, page_index: int = 0,
                    sort_column: str = "name", sort_direction: str = "asc") -> ListTopicsResult:
        """One page of topics, and how many exist in total."""
        return ListTopicsResult.from_json(self._call("list-topics", {
            "prefix": prefix, "pageSize": page_size, "pageIndex": page_index,
            "sortColumn": sort_column, "sortDirection": sort_direction}))

    def get_topic_ern(self, name: str) -> str:
        """The ERN of the topic of this name, in the session's account and namespace."""
        return self._text("get-topic-ern", {"name": name}, "ern")

    def get_topic_metadata(self, ern: str) -> TopicMetadata:
        """Where a topic lives and how much has been published to it."""
        return TopicMetadata.from_json(self._call("get-topic-metadata", {"ern": ern}))

    def purge_topic(self, ern: str) -> None:
        """Deletes every message a topic has kept, leaving the topic and its subscriptions in place.

        It does not un-deliver anything: a message already handed to a subscriber is on that
        subscriber's queue and belongs to it now.
        """
        self._call("purge-topic", {"ern": ern})

    def purge_all_topics(self, region: str = "", account_id: str = "", namespace: str = "") -> None:
        """Purges every topic of an account, which defaults to this session's own.

        As blunt as it sounds, and there is no undo: it exists for a test environment between runs.
        """
        self._call("purge-all-topics", {
            "region": region or self._session.region,
            "accountId": account_id or self._session.account_id,
            "nameSpace": namespace or self._session.namespace})

    def add_topic_tag(self, ern: str, key: str, value: str) -> None:
        """Tags a topic."""
        self._call("add-topic-tag", {"ern": ern, "key": key, "value": value})

    def set_topic_tag(self, ern: str, key: str, value: str) -> None:
        """Sets the value of a tag the topic already has."""
        self._call("set-topic-tag", {"ern": ern, "key": key, "value": value})

    def delete_topic_tag(self, ern: str, key: str) -> None:
        """Removes a tag from a topic."""
        self._call("delete-topic-tag", {"ern": ern, "key": key})

    # -- messages ----------------------------------------------------------------------------

    def publish_message(self, topic_ern: str, body: str, attributes: Mapping[str, Any] | None = None,
                        priority: str = "") -> str:
        """Publishes a message to a topic, and returns the ID the server gave it.

        Every subscription on the topic gets a copy, each on its own queue and each consumed
        independently: a subscriber that is slow or stopped delays nobody else, and a message
        already delivered is not withdrawn if the subscription is later removed.

        ``priority`` is ``"LOW"``, ``"MIDDLE"`` or ``"HIGH"``, and travels with the message onto
        the queues it is delivered to.
        """
        payload: dict[str, Any] = {"ern": topic_ern, "body": body,
                                   "attributes": Variant.map_to_json(attributes)}
        if priority:
            payload["priority"] = priority
        return self._text("publish-message", payload, "messageId")

    def list_messages(self, topic_ern: str, page_size: int = 10, page_index: int = 0,
                      sort_column: str = "created", sort_direction: str = "asc") -> MessagesResult:
        """One page of the messages a topic has kept, and how many it holds in total."""
        return MessagesResult.from_json(self._call("list-messages", {
            "topicErn": topic_ern, "pageSize": page_size, "pageIndex": page_index,
            "sortColumn": sort_column, "sortDirection": sort_direction}))

    def get_message_count(self, ern: str) -> MessageCount:
        """A topic's message counters: what is on it, what went out, and what had to go out again."""
        return MessageCount.from_json(self._call("get-message-count", {"ern": ern}))

    def get_message_attribute(self, message_id: str, key: str) -> MessageAttribute:
        """One attribute of one published message.

        The attribute's name travels as ``key`` throughout ENS and as ``name`` in most of EQS - the
        server's own asymmetry, reproduced rather than papered over.
        """
        return MessageAttribute.from_json(self._call("get-message-attribute", {
            "messageId": message_id, "key": key}))

    def set_message_attribute(self, message_id: str, key: str, value: Any) -> MessageAttribute:
        """Sets one attribute of one published message, creating it if it was not there.

        The value is a :class:`~euclid.dto.com.Variant` or a plain Python value to be tagged as one.
        """
        return MessageAttribute.from_json(self._call("set-message-attribute", {
            "messageId": message_id, "key": key, "value": Variant.of(value).to_json()}))

    # -- subscriptions -----------------------------------------------------------------------

    def subscribe(self, topic_ern: str, target_ern: str, target_type: str = QUEUE) -> SubscribeResult:
        """Delivers a topic's messages onward to a queue from now on.

        Only ``"SQS"`` is a target type so far, so ``target_ern`` names an EQS queue. A message
        published before this call is not delivered retrospectively - a subscription says what
        happens next.

        Not idempotent: a second call registers a second subscription and the queue then receives
        every message twice, so a caller that may run twice checks :meth:`list_subscriptions` first.
        """
        return SubscribeResult.from_json(self._call("subscribe", {
            "sourceErn": topic_ern, "type": target_type, "targetErn": target_ern}))

    def unsubscribe(self, ern: str) -> None:
        """Removes a subscription, by the ERN :meth:`subscribe` returned - not the topic's, and not
        the queue's."""
        self._call("unsubscribe", {"ern": ern})

    def list_subscriptions(self, topic_ern: str) -> list[Subscription]:
        """Every subscription currently registered on a topic."""
        subscriptions = self._call("list-subscriptions", {"topicErn": topic_ern}).get("subscriptions")
        return [Subscription.from_json(s) for s in subscriptions] if isinstance(subscriptions, list) else []
