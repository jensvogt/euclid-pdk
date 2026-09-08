"""The shapes ENS sends back.

Parsed the same defensive way as every other module's. Where a type here looks like one in
:mod:`euclid.dto.eqs`, it is not the same type: a topic's message has no receipt handle and no
visibility, because nothing leases it - it is delivered to the topic's subscribers and kept as a
record of that. The server keeps them apart for the same reason, so this does too.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from . import _json
from .com import Variant

__all__ = [
    "Topic",
    "Message",
    "MessageAttribute",
    "Subscription",
    "CreateTopicResult",
    "ListTopicsResult",
    "MessagesResult",
    "TopicMetadata",
    "MessageCount",
    "SubscribeResult",
]


# -- resources -----------------------------------------------------------------------------------


@dataclass
class Topic:
    """A topic: what a publisher publishes to, and what subscriptions hang off."""

    name: str = ""
    owner: str = ""
    ern: str = ""
    tags: dict[str, str] = field(default_factory=dict)
    size: int = 0
    messages: int = 0
    max_message_length: int = 0
    created: str = ""
    modified: str = ""

    @staticmethod
    def from_json(document: Any) -> "Topic":
        return Topic(
            _json.text(document, "name"), _json.text(document, "owner"), _json.text(document, "ern"),
            _json.string_map(document, "tags"), _json.number(document, "size"),
            _json.number(document, "messages"), _json.number(document, "maxMessageLength"),
            _json.text(document, "created"), _json.text(document, "modified"))


@dataclass
class Message:
    """One message published to a topic."""

    ern: str = ""
    topic_ern: str = ""
    message_id: str = ""
    status: str = ""
    body: str = ""
    content_type: str = ""
    attributes: dict[str, Variant] = field(default_factory=dict)
    last_received: str = ""
    created: str = ""
    modified: str = ""

    @staticmethod
    def from_json(document: Any) -> "Message":
        attributes = document.get("attributes") if isinstance(document, dict) else None
        return Message(
            _json.text(document, "ern"), _json.text(document, "topicErn"),
            _json.text(document, "messageId"), _json.text(document, "status"),
            _json.text(document, "body"), _json.text(document, "contentType"),
            Variant.map_from_json(attributes), _json.text(document, "lastReceived"),
            _json.text(document, "created"), _json.text(document, "modified"))


@dataclass
class MessageAttribute:
    """One attribute of one message.

    The wire field is ``key`` here and ``name`` in EQS - the same thing under two names, which this
    SDK reproduces rather than papers over, so that a request built from this documentation matches
    what the server and euclid-cli exchange.
    """

    message_id: str = ""
    key: str = ""
    value: Variant = field(default_factory=lambda: Variant("string", ""))

    @staticmethod
    def from_json(document: Any) -> "MessageAttribute":
        value = document.get("value") if isinstance(document, dict) else None
        return MessageAttribute(_json.text(document, "messageId"), _json.text(document, "key"),
                                Variant.from_json(value))


@dataclass
class Subscription:
    """A standing instruction to deliver a topic's messages onward to a queue."""

    ern: str = ""
    source_ern: str = ""
    type: str = ""
    target_ern: str = ""
    created: str = ""
    modified: str = ""

    @staticmethod
    def from_json(document: Any) -> "Subscription":
        return Subscription(
            _json.text(document, "ern"), _json.text(document, "sourceErn"), _json.text(document, "type"),
            _json.text(document, "targetErn"), _json.text(document, "created"),
            _json.text(document, "modified"))


# -- what the actions answer with ------------------------------------------------------------------


@dataclass
class CreateTopicResult:
    """A newly created topic: its name, and the ERN everything else names it by."""

    name: str = ""
    ern: str = ""

    @staticmethod
    def from_json(document: Any) -> "CreateTopicResult":
        return CreateTopicResult(_json.text(document, "name"), _json.text(document, "ern"))


@dataclass
class ListTopicsResult:
    """One page of topics, and how many exist in total."""

    topics: list[Topic] = field(default_factory=list)
    total: int = 0

    @staticmethod
    def from_json(document: Any) -> "ListTopicsResult":
        return ListTopicsResult([Topic.from_json(t) for t in _json.documents(document, "topics")],
                                _json.number(document, "total"))


@dataclass
class MessagesResult:
    """One page of a topic's messages, and how many it holds in total."""

    messages: list[Message] = field(default_factory=list)
    total: int = 0

    @staticmethod
    def from_json(document: Any) -> "MessagesResult":
        return MessagesResult([Message.from_json(m) for m in _json.documents(document, "messages")],
                              _json.number(document, "total"))


@dataclass
class TopicMetadata:
    """Where a topic lives and how much has been published to it."""

    region: str = ""
    account_id: str = ""
    owner: str = ""
    namespace: str = ""
    name: str = ""
    ern: str = ""
    size: int = 0
    messages: int = 0

    @staticmethod
    def from_json(document: Any) -> "TopicMetadata":
        return TopicMetadata(
            _json.text(document, "region"), _json.text(document, "accountId"),
            _json.text(document, "owner"), _json.text(document, "nameSpace"),
            _json.text(document, "name"), _json.text(document, "ern"), _json.number(document, "size"),
            _json.number(document, "messages"))


@dataclass
class MessageCount:
    """A topic's message counters - the server's own three, which are not a queue's.

    A topic does not hold a backlog the way a queue does, so these count delivery rather than
    state: what is on the topic, what has gone out to subscribers, and what had to go out again.
    """

    ern: str = ""
    available: int = 0
    send: int = 0
    resend: int = 0

    @staticmethod
    def from_json(document: Any) -> "MessageCount":
        return MessageCount(
            _json.text(document, "ern"), _json.number(document, "available"),
            _json.number(document, "send"), _json.number(document, "resend"))


@dataclass
class SubscribeResult:
    """A new subscription. ``ern`` is the subscription's own - what ``unsubscribe`` takes."""

    ern: str = ""
    source_ern: str = ""
    type: str = ""
    target_ern: str = ""

    @staticmethod
    def from_json(document: Any) -> "SubscribeResult":
        return SubscribeResult(_json.text(document, "ern"), _json.text(document, "sourceErn"),
                               _json.text(document, "type"), _json.text(document, "targetErn"))
