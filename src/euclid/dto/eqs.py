"""The shapes EQS sends back.

Plain dataclasses, parsed defensively, exactly as :mod:`euclid.dto.eam` and :mod:`euclid.dto.esm`
do and for the same reasons. Field names are the server's (``dto/include/euclid/dto/eqs``),
converted to snake_case; where the two differ, the JSON name is the one on the wire.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from . import _json
from .com import Variant

__all__ = [
    "Queue",
    "Message",
    "MessageAttribute",
    "CreateQueueResult",
    "ListQueuesResult",
    "MessagesResult",
    "QueueMetadata",
    "MessageCount",
    "MessageMetadata",
    "QueueStatusResult",
    "RedriveTarget",
    "RedriveDlqResult",
]


# -- resources -----------------------------------------------------------------------------------


@dataclass
class Queue:
    """A queue, and the counters that say what is in it.

    The three counts are the states a message can be in: ``available`` is waiting to be received,
    ``invisible`` is out with a consumer whose visibility timeout has not expired, and ``delayed``
    is not yet due. They are what a consumer's backlog actually looks like - ``size`` is bytes.
    """

    name: str = ""
    owner: str = ""
    ern: str = ""
    tags: dict[str, str] = field(default_factory=dict)
    size: int = 0
    delay: int = 0
    available: int = 0
    delayed: int = 0
    invisible: int = 0
    visibility: int = 0
    max_message_length: int = 0
    max_receive_count: int = 0
    #: The dead letter queue messages go to once they have been received ``max_receive_count``
    #: times. Sent as ``deadLetterQueueArn`` - an "arn" the server has never renamed.
    dead_letter_queue_ern: str = ""
    priority: str = ""
    #: ``AVAILABLE`` or ``STOPPED``; a stopped queue hands nothing out - see
    #: :meth:`euclid.modules.eqs.EuclidEqs.stop_queue`.
    status: str = ""
    #: One of euclid's own queues rather than somebody's. Left out of a listing unless asked for.
    internal: bool = False
    created: str = ""
    modified: str = ""

    @staticmethod
    def from_json(document: Any) -> "Queue":
        return Queue(
            _json.text(document, "name"), _json.text(document, "owner"), _json.text(document, "ern"),
            _json.string_map(document, "tags"), _json.number(document, "size"),
            _json.number(document, "delay"), _json.number(document, "available"),
            _json.number(document, "delayed"), _json.number(document, "invisible"),
            _json.number(document, "visibility"), _json.number(document, "maxMessageLength"),
            _json.number(document, "maxReceiveCount"), _json.text(document, "deadLetterQueueArn"),
            _json.text(document, "priority"), _json.text(document, "status"),
            _json.flag(document, "internal"), _json.text(document, "created"),
            _json.text(document, "modified"))


@dataclass
class Message:
    """One message on a queue.

    ``receipt_handle`` is the lease a receive hands out: it is what
    :meth:`~euclid.modules.eqs.EuclidEqs.delete_message` takes, and it stops working once the
    visibility timeout expires and the message goes back on the queue.

    Two attribute maps, as everywhere in euclid: ``attributes`` are the sender's own, and
    ``system_attributes`` are euclid's envelope, which is how a message that has hopped through a
    bucket or a topic still carries what it was sent with.
    """

    ern: str = ""
    queue_ern: str = ""
    message_id: str = ""
    status: str = ""
    priority: str = ""
    body: str = ""
    receipt_handle: str = ""
    size: int = 0
    received_count: int = 0
    content_type: str = ""
    attributes: dict[str, Variant] = field(default_factory=dict)
    system_attributes: dict[str, Variant] = field(default_factory=dict)
    last_received: str = ""
    created: str = ""
    modified: str = ""

    @staticmethod
    def from_json(document: Any) -> "Message":
        attributes = document.get("attributes") if isinstance(document, dict) else None
        system = document.get("systemAttributes") if isinstance(document, dict) else None
        return Message(
            _json.text(document, "ern"), _json.text(document, "queueErn"),
            _json.text(document, "messageId"), _json.text(document, "status"),
            _json.text(document, "priority"), _json.text(document, "body"),
            _json.text(document, "receiptHandle"), _json.number(document, "size"),
            _json.number(document, "receivedCount"), _json.text(document, "contentType"),
            Variant.map_from_json(attributes), Variant.map_from_json(system),
            _json.text(document, "lastReceived"), _json.text(document, "created"),
            _json.text(document, "modified"))


@dataclass
class MessageAttribute:
    """One attribute of one message, as the server stored it."""

    message_id: str = ""
    name: str = ""
    value: Variant = field(default_factory=lambda: Variant("string", ""))

    @staticmethod
    def from_json(document: Any) -> "MessageAttribute":
        value = document.get("value") if isinstance(document, dict) else None
        return MessageAttribute(_json.text(document, "messageId"), _json.text(document, "name"),
                                Variant.from_json(value))


# -- what the actions answer with ------------------------------------------------------------------


@dataclass
class CreateQueueResult:
    """A newly created queue: its name, and the ERN everything else names it by."""

    name: str = ""
    ern: str = ""

    @staticmethod
    def from_json(document: Any) -> "CreateQueueResult":
        return CreateQueueResult(_json.text(document, "name"), _json.text(document, "ern"))


@dataclass
class ListQueuesResult:
    """One page of queues, and how many exist in total."""

    queues: list[Queue] = field(default_factory=list)
    total: int = 0

    @staticmethod
    def from_json(document: Any) -> "ListQueuesResult":
        return ListQueuesResult([Queue.from_json(q) for q in _json.documents(document, "queues")],
                                _json.number(document, "total"))


@dataclass
class MessagesResult:
    """Messages, and how many there were.

    What both ways of getting them answer with: :meth:`~euclid.modules.eqs.EuclidEqs.list_messages`
    reads a queue without touching it, :meth:`~euclid.modules.eqs.EuclidEqs.receive_messages` takes
    them out of it on a lease. One shape, because the difference is in what the call does rather
    than in what it hands back.
    """

    messages: list[Message] = field(default_factory=list)
    total: int = 0

    @staticmethod
    def from_json(document: Any) -> "MessagesResult":
        return MessagesResult([Message.from_json(m) for m in _json.documents(document, "messages")],
                              _json.number(document, "total"))


@dataclass
class QueueMetadata:
    """Where a queue lives and how much is in it."""

    region: str = ""
    account_id: str = ""
    owner: str = ""
    namespace: str = ""
    name: str = ""
    ern: str = ""
    size: int = 0
    messages: int = 0

    @staticmethod
    def from_json(document: Any) -> "QueueMetadata":
        return QueueMetadata(
            _json.text(document, "region"), _json.text(document, "accountId"),
            _json.text(document, "owner"), _json.text(document, "nameSpace"),
            _json.text(document, "name"), _json.text(document, "ern"), _json.number(document, "size"),
            _json.number(document, "messages"))


@dataclass
class MessageCount:
    """How many messages a queue holds, by the state they are in."""

    ern: str = ""
    available: int = 0
    delayed: int = 0
    invisible: int = 0
    total: int = 0

    @staticmethod
    def from_json(document: Any) -> "MessageCount":
        return MessageCount(
            _json.text(document, "ern"), _json.number(document, "available"),
            _json.number(document, "delayed"), _json.number(document, "invisible"),
            _json.number(document, "total"))


@dataclass
class MessageMetadata:
    """Everything about one message except its body.

    ``received_count`` against the queue's ``max_receive_count`` is what decides when a message is
    moved to the dead letter queue, so this is where a message that keeps coming back explains
    itself.
    """

    message_id: str = ""
    queue_ern: str = ""
    receipt_handle: str = ""
    status: str = ""
    priority: str = ""
    size: int = 0
    received_count: int = 0
    visibility_timeout: int = 0
    content_type: str = ""
    created: str = ""
    modified: str = ""

    @staticmethod
    def from_json(document: Any) -> "MessageMetadata":
        return MessageMetadata(
            _json.text(document, "messageId"), _json.text(document, "queueErn"),
            _json.text(document, "receiptHandle"), _json.text(document, "status"),
            _json.text(document, "priority"), _json.number(document, "size"),
            _json.number(document, "receivedCount"), _json.number(document, "visibilityTimeout"),
            _json.text(document, "contentType"), _json.text(document, "created"),
            _json.text(document, "modified"))


@dataclass
class QueueStatusResult:
    """A queue's status after starting or stopping it, and how many messages are waiting on it."""

    ern: str = ""
    status: str = ""
    available: int = 0

    @staticmethod
    def from_json(document: Any) -> "QueueStatusResult":
        return QueueStatusResult(_json.text(document, "ern"), _json.text(document, "status"),
                                 _json.number(document, "available"))


@dataclass
class RedriveTarget:
    """One queue a redrive put messages back on, and how many went there."""

    queue_ern: str = ""
    messages: int = 0

    @staticmethod
    def from_json(document: Any) -> "RedriveTarget":
        return RedriveTarget(_json.text(document, "queueErn"), _json.number(document, "messages"))


@dataclass
class RedriveDlqResult:
    """What a redrive moved, where it went, and what it left behind.

    ``remaining`` is not a failure: several queues can share a dead letter queue, and a message that
    predates the recording of its origin has no answer to the question of where it came from. It is
    left alone rather than guessed at, and ``note`` is the server saying so.
    """

    ern: str = ""
    messages: int = 0
    remaining: int = 0
    targets: list[RedriveTarget] = field(default_factory=list)
    note: str = ""

    @staticmethod
    def from_json(document: Any) -> "RedriveDlqResult":
        return RedriveDlqResult(
            _json.text(document, "ern"), _json.number(document, "messages"),
            _json.number(document, "remaining"),
            [RedriveTarget.from_json(t) for t in _json.documents(document, "targets")],
            _json.text(document, "note"))
