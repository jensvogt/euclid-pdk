"""EES - euclid's event service: the event bus, for consumers that are not euclid modules.

One object, :class:`EuclidEes`, built from a session that has already logged in::

    ees = Euclid.for_server(url).login("jens", "secret").ees()

    ees.subscribe_events("invoice-indexer", [OBJECT_CREATED], {"prefix": "invoices/2026/"})
    while True:
        result = ees.receive_events("invoice-indexer", wait_time=20)
        for event in result.events:
            index(event["bucketErn"], event["key"])
            ees.ack_event("invoice-indexer", event.event_id)

A subscriber registers a durable *name* and pulls. Nothing is pushed and no queue has to be created:
:meth:`~EuclidEes.receive_events` claims what is waiting and :meth:`~EuclidEes.ack_events` deletes
it, and an event claimed but never acknowledged becomes claimable again when its visibility timeout
runs out - so a consumer that dies mid-work loses nothing.

The name decides fan-out. Two *instances* of one application share a name, so whichever claims an
event first processes it; two *different* applications use different names and each receive their
own copy of the same event.

A subscription carries a filter, and it is evaluated where the event is published rather than where
it is read - so a subscriber accumulates what it asked for rather than everything of that type in
the installation.

**ESM object events** are the ones a storage consumer usually wants: :data:`OBJECT_CREATED`,
:data:`OBJECT_UPDATED` and :data:`OBJECT_DELETED`, all three carrying the same flat payload -
``ern``, ``bucketErn``, ``bucketName``, ``key``, ``prefix``, ``directory``, ``size``,
``contentType``, ``md5Sum``, ``owner``, ``userId``, ``accountId``, ``region``, ``namespace`` and
``eventTime``. Those fields are what make the useful subscriptions expressible:
``{"bucketErn": ...}`` for one bucket, ``{"prefix": "invoices/2026/"}`` for one "directory" - a key
is a path by convention only, and ``prefix`` is that convention spelled out by the server rather
than by every subscriber - and ``{"directory": False}`` to skip directory markers. ``owner`` is who
uploaded the object and ``userId`` who made this change; a move performed by an operator differs in
the two.

ESM also publishes ``esm.subscription.delivery`` and ``esm.subscription.publication``. Those are not
domain events: each is addressed to the one module that can act on it, and they are the plumbing
behind :meth:`euclid.modules.esm.EuclidEsm.subscribe`. A consumer that wants that path reads the
delivered message and parses it with :func:`~euclid.modules.esm.parse_bucket_event` instead.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping, Sequence

from ..dto.ees import (AckEventsResult, EventSubscription, ReceiveEventsResult, SubscriptionsResult,
                       UnsubscribeResult)
from .base import ModuleClient
from .esm import OBJECT_CREATED, OBJECT_DELETED, OBJECT_UPDATED

__all__ = ["EuclidEes", "TARGET", "DURABLE", "LIVE",
           "OBJECT_CREATED", "OBJECT_UPDATED", "OBJECT_DELETED",
           "DEFAULT_MAX_EVENTS", "MAX_WAIT_SECONDS", "DEFAULT_VISIBILITY_SECONDS"]

TARGET = "ees"

#: Events are kept until they are claimed, so a subscriber that was not connected when one arrived
#: finds it waiting. The default, because anything else has to be chosen deliberately.
DURABLE = "durable"
#: Events are delivered only while the subscriber is there to take them, and are not kept.
LIVE = "live"

#: How many events one claim takes unless told otherwise.
DEFAULT_MAX_EVENTS = 10

#: The longest the server will hold a receive open, in seconds. A larger ``wait_time`` is clamped to
#: this rather than refused: a gateway worker thread is held for the duration, and the bound is what
#: stops consumers waiting for events from taking the last thread from a client with work to do.
MAX_WAIT_SECONDS = 20

#: How long a claimed event stays invisible before it becomes claimable again, in seconds.
DEFAULT_VISIBILITY_SECONDS = 300

#: Added to a long poll's wait to give the response time to travel: the server answers at the end of
#: the window it was asked for, so a timeout of exactly that window would race the network.
LONG_POLL_RESPONSE_MARGIN = 10.0


class EuclidEes(ModuleClient):
    """EES's operations, on the credentials of the session that created it.

    Built by :meth:`euclid.EuclidSession.ees` rather than directly, so that it shares that
    session's identity, namespace and connection settings - and follows them as they change.
    """

    target = TARGET

    # -- subscriptions -----------------------------------------------------------------------

    def subscribe_events(self, name: str, event_types: Iterable[str],
                         filter: Mapping[str, Any] | None = None, mode: str = DURABLE,
                         ephemeral: bool = False) -> list[EventSubscription]:
        """Registers an interest in one or more event types, and returns everything ``name`` is now
        subscribed to.

        One subscription per event type, all under the same name, so subscribing again for another
        type adds rather than replaces. Subscribing for a type twice is not an error - the same name
        and type is one subscription however often it is asked for.

        :param name: the durable subscriber name, which is what decides fan-out and what
            :meth:`receive_events` reads from.
        :param event_types: the types to receive, e.g. :data:`OBJECT_CREATED`.
        :param filter: field-equality conditions the event's payload has to satisfy. An empty
            filter really does mean every event of these types.
        :param mode: :data:`DURABLE` or :data:`LIVE`.
        :param ephemeral: whether the subscription should not outlive the gateway process it was
            made through. This is what a websocket connection sets for itself; an ordinary consumer
            leaves it alone.
        """
        payload: dict[str, Any] = {"name": name, "eventTypes": list(event_types),
                                   "filter": dict(filter or {}), "mode": mode}
        if ephemeral:
            payload["ephemeral"] = True
        return self._subscriptions(self._call("subscribe-events", payload))

    def unsubscribe_events(self, name: str, event_type: str = "") -> UnsubscribeResult:
        """Removes a subscriber's subscriptions, and says how many went.

        One event type, or - with none named - every type this name was subscribed to. Events
        already waiting for the name are not delivered afterwards, so this is how a consumer that is
        being retired stops accumulating a backlog nobody will read.
        """
        payload: dict[str, Any] = {"name": name}
        if event_type:
            payload["eventType"] = event_type
        return UnsubscribeResult.from_json(self._call("unsubscribe-events", payload))

    def list_subscriptions(self, name: str) -> SubscriptionsResult:
        """What one subscriber name is subscribed to, and how many events are waiting for it.

        The backlog is the useful half: a subscriber whose ``waiting`` climbs is one that is not
        keeping up, or one nobody is reading any more.
        """
        return SubscriptionsResult.from_json(self._call("list-subscriptions", {"name": name}))

    # -- events ------------------------------------------------------------------------------

    def receive_events(self, name: str, max_events: int = DEFAULT_MAX_EVENTS, wait_time: int = 0,
                       visibility_timeout: int = DEFAULT_VISIBILITY_SECONDS) -> ReceiveEventsResult:
        """Claims up to ``max_events`` of a subscriber's waiting events, waiting for one to arrive.

        The waiting is the server's: it holds the request open until something is claimable or the
        window runs out, so an idle subscriber costs one request per window rather than one per poll
        tick. That request gets a timeout of its own here, since the session's is sized for an
        answer that comes straight back.

        Two bounds are the server's rather than this client's. ``wait_time`` is clamped to
        :data:`MAX_WAIT_SECONDS`, and a server with no long-poll slot free answers at once with
        whatever is there rather than queueing behind the waiters - which comes back empty with time
        still on the clock. A consumer loop asks again, which is its shape anyway.

        Claiming is not reading: each event comes back invisible to other consumers of this name for
        ``visibility_timeout`` seconds, and becomes claimable again after that unless
        :meth:`ack_events` has deleted it in the meantime.
        """
        timeout = wait_time + LONG_POLL_RESPONSE_MARGIN if wait_time > 0 else None
        return ReceiveEventsResult.from_json(self._call(
            "receive-events", {"name": name, "maxEvents": max_events, "waitTime": wait_time,
                               "visibilityTimeout": visibility_timeout}, timeout))

    def ack_event(self, name: str, event_id: str) -> AckEventsResult:
        """Acknowledges one event, which is what deletes it."""
        return self.ack_events(name, [event_id])

    def ack_events(self, name: str, event_ids: Sequence[str]) -> AckEventsResult:
        """Acknowledges events, and says how many went and what is still waiting.

        After the work rather than before it: an event that is claimed and never acknowledged comes
        back when its visibility timeout runs out, which is what makes a consumer that dies
        mid-work harmless.
        """
        return AckEventsResult.from_json(self._call("ack-events", {
            "name": name, "eventIds": list(event_ids)}))

    # -- monitoring --------------------------------------------------------------------------

    def metrics(self) -> dict[str, Any]:
        """EES's own metrics, as the server collects them. Returned unparsed - the shape belongs to
        the monitoring module rather than to EES."""
        return self._call("get-metrics")

    @staticmethod
    def _subscriptions(document: Mapping[str, Any]) -> list[EventSubscription]:
        subscriptions = document.get("subscriptions")
        return ([EventSubscription.from_json(s) for s in subscriptions]
                if isinstance(subscriptions, list) else [])
