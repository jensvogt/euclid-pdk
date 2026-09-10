"""The shapes EES sends back.

Parsed the same defensive way as every other module's, with one deliberate exception: an event's
payload and a subscription's filter are handed over as plain dictionaries. Their shape is decided by
the event type rather than by this SDK, so parsing them into anything would mean this package
knowing what every module publishes - and going out of date the first time one of them adds a field.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from . import _json

__all__ = ["Event", "EventSubscription", "ReceiveEventsResult", "SubscriptionsResult",
           "AckEventsResult", "UnsubscribeResult"]


def _document(document: Any, name: str) -> dict[str, Any]:
    """A sub-object, as it arrived. Empty when absent or not an object."""
    if not isinstance(document, dict):
        return {}
    value = document.get(name)
    return dict(value) if isinstance(value, dict) else {}


@dataclass
class Event:
    """One event, as it was published.

    ``payload`` is the event type's own shape - flat, and made of strings, numbers and booleans
    precisely so that a subscription's filter can match it by equality.

    ``attempts`` is how many times this event has been claimed. Above one it means an earlier
    consumer took it and never acknowledged it, so its visibility timeout ran out and it came back.
    """

    event_id: str = ""
    event_type: str = ""
    #: The module that published it - ``esm``, ``eqs`` and so on.
    source_module: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    attempts: int = 0
    created: str = ""

    @staticmethod
    def from_json(document: Any) -> "Event":
        return Event(
            _json.text(document, "eventId"), _json.text(document, "eventType"),
            _json.text(document, "sourceModule"), _document(document, "payload"),
            _json.number(document, "attempts"), _json.text(document, "created"))

    def __getitem__(self, name: str) -> Any:
        """A payload field, for the common case - ``event["key"]`` rather than
        ``event.payload["key"]``."""
        return self.payload[name]

    def get(self, name: str, default: Any = None) -> Any:
        """A payload field, or ``default`` when this event does not carry it."""
        return self.payload.get(name, default)


@dataclass
class EventSubscription:
    """One standing interest in one event type.

    ``subscriber`` is the durable name that decides fan-out: two instances of one application share
    a name and compete for each event, while two different applications use different names and each
    get their own copy.

    ``filter`` is evaluated where the event is published rather than where it is read, so a
    subscriber only ever accumulates what it asked for.
    """

    subscriber: str = ""
    event_type: str = ""
    filter: dict[str, Any] = field(default_factory=dict)
    account_id: str = ""
    #: ``durable`` or ``live`` - see :mod:`euclid.modules.ees`.
    mode: str = ""
    created: str = ""
    #: When this subscriber last claimed anything, which is what says whether it is still there.
    last_seen: str = ""

    @staticmethod
    def from_json(document: Any) -> "EventSubscription":
        return EventSubscription(
            _json.text(document, "subscriber"), _json.text(document, "eventType"),
            _document(document, "filter"), _json.text(document, "accountId"),
            _json.text(document, "mode"), _json.text(document, "created"),
            _json.text(document, "lastSeen"))


@dataclass
class ReceiveEventsResult:
    """The events this call claimed, and how many that was.

    Claimed rather than read: each one is invisible to other consumers of the same subscriber name
    until it is acknowledged or its visibility timeout runs out.
    """

    events: list[Event] = field(default_factory=list)
    total: int = 0

    @staticmethod
    def from_json(document: Any) -> "ReceiveEventsResult":
        events = [Event.from_json(e) for e in _json.documents(document, "events")]
        return ReceiveEventsResult(events, _json.number(document, "total") or len(events))


@dataclass
class SubscriptionsResult:
    """What a subscriber is subscribed to, and how much is waiting for it.

    ``waiting`` is the backlog: events claimable right now, which is what says whether a consumer is
    keeping up.
    """

    subscriptions: list[EventSubscription] = field(default_factory=list)
    waiting: int = 0

    @staticmethod
    def from_json(document: Any) -> "SubscriptionsResult":
        return SubscriptionsResult(
            [EventSubscription.from_json(s) for s in _json.documents(document, "subscriptions")],
            _json.number(document, "waiting"))


@dataclass
class AckEventsResult:
    """How many events were acknowledged, and what is still waiting afterwards."""

    subscriber: str = ""
    acknowledged: int = 0
    waiting: int = 0

    @staticmethod
    def from_json(document: Any) -> "AckEventsResult":
        return AckEventsResult(_json.text(document, "subscriber"),
                               _json.number(document, "acknowledged"),
                               _json.number(document, "waiting"))


@dataclass
class UnsubscribeResult:
    """How many subscriptions were removed - one per event type the name was subscribed to."""

    subscriber: str = ""
    removed: int = 0

    @staticmethod
    def from_json(document: Any) -> "UnsubscribeResult":
        return UnsubscribeResult(_json.text(document, "subscriber"), _json.number(document, "removed"))
