"""EES, end to end against a fake euclid server.

The subscription actions are checked the way the other modules' are. Receiving is checked against a
stand-in that really claims and acknowledges (``FakeEvents`` below), because the two things an event
consumer can get wrong - taking an event twice, and acknowledging one it never claimed - are
invisible to a test whose server hands the same list back every time.
"""

from __future__ import annotations

import time

import pytest

from euclid import Euclid, EuclidServiceError
from euclid.modules.ees import DURABLE, LIVE, OBJECT_CREATED, OBJECT_DELETED
from fake_gateway import RecordedRequest
from test_eam import prepared

SUBSCRIBER = "invoice-indexer"

PAYLOAD = {"ern": "ern:esm:object/1", "bucketErn": "ern:esm:bucket/invoices",
           "bucketName": "invoices", "key": "invoices/2026/q3.pdf", "prefix": "invoices/2026/",
           "directory": False, "size": 12032, "contentType": "application/pdf",
           "md5Sum": "d41d8", "owner": "jens", "userId": "jens", "accountId": "000000000000",
           "region": "eu-central-1", "namespace": "development", "eventTime": "2026-09-10T10:00:00Z"}


class FakeEvents:
    """An event bus that keeps events rather than pretending to.

    One subscriber's worth: events go in with :meth:`publish`, come out claimed, and go away when
    they are acknowledged. A claimed event stays claimed here - the visibility timeout is the
    server's business, and what this exercises is that the client claims and acknowledges the same
    IDs.
    """

    def __init__(self) -> None:
        #: event ID -> (the event as it goes on the wire, whether it is claimed).
        self.events: dict[str, tuple[dict, bool]] = {}
        self.subscriptions: list[dict] = []
        self.received: list[dict] = []

    def install(self, gateway):
        gateway.on("ees", "subscribe-events", self.subscribe)
        gateway.on("ees", "receive-events", self.receive)
        gateway.on("ees", "ack-events", self.ack)
        gateway.on("ees", "list-subscriptions", self.list_subscriptions)
        return self

    def publish(self, event_id: str, event_type: str = OBJECT_CREATED, **payload) -> "FakeEvents":
        self.events[event_id] = ({"eventId": event_id, "eventType": event_type,
                                  "sourceModule": "esm", "payload": dict(PAYLOAD, **payload),
                                  "attempts": 0, "created": "2026-09-10T10:00:00Z"}, False)
        return self

    @property
    def waiting(self) -> int:
        return len([1 for _, claimed in self.events.values() if not claimed])

    def subscribe(self, request: RecordedRequest) -> tuple[int, dict]:
        body = request.json()
        for event_type in body["eventTypes"]:
            self.subscriptions.append({"subscriber": body["name"], "eventType": event_type,
                                       "filter": body.get("filter", {}),
                                       "accountId": "000000000000",
                                       "mode": body.get("mode", DURABLE),
                                       "created": "2026-09-10", "lastSeen": ""})
        return 200, {"subscriptions": self.subscriptions}

    def receive(self, request: RecordedRequest) -> tuple[int, dict]:
        body = request.json()
        self.received.append(body)
        claimed = []
        for event_id, (event, was_claimed) in list(self.events.items()):
            if was_claimed or len(claimed) >= body.get("maxEvents", 10):
                continue
            self.events[event_id] = (event, True)
            claimed.append(event)
        return 200, {"events": claimed, "total": len(claimed)}

    def ack(self, request: RecordedRequest) -> tuple[int, dict]:
        body = request.json()
        acknowledged = 0
        for event_id in body["eventIds"]:
            # Only a claimed event can be acknowledged, which is what makes the receipt mean
            # something.
            if self.events.get(event_id, (None, False))[1]:
                del self.events[event_id]
                acknowledged += 1
        return 200, {"subscriber": body["name"], "acknowledged": acknowledged,
                     "waiting": self.waiting}

    def list_subscriptions(self, request: RecordedRequest) -> tuple[int, dict]:
        name = request.json()["name"]
        return 200, {"subscriptions": [s for s in self.subscriptions if s["subscriber"] == name],
                     "waiting": self.waiting}


@pytest.fixture
def events(gateway):
    """A gateway that answers a login, with an event bus behind it."""
    prepared(gateway)
    return FakeEvents().install(gateway)


@pytest.fixture
def ees(gateway, events):
    """An EES client on a logged-in session, closed with it."""
    with Euclid.for_server(gateway.base_url).login("jens", "secret") as session:
        yield session.ees()


# -- subscriptions ---------------------------------------------------------------------------------


def test_subscribing_names_the_types_and_the_filter(gateway, ees, events):
    """The filter is evaluated where the event is published, so a subscriber accumulates what it
    asked for rather than everything of that type in the installation."""
    subscriptions = ees.subscribe_events(SUBSCRIBER, [OBJECT_CREATED, OBJECT_DELETED],
                                         {"prefix": "invoices/2026/", "directory": False})

    assert gateway.last().json() == {
        "name": SUBSCRIBER, "eventTypes": ["esm.object.created", "esm.object.deleted"],
        "filter": {"prefix": "invoices/2026/", "directory": False}, "mode": "durable"}

    assert [s.event_type for s in subscriptions] == ["esm.object.created", "esm.object.deleted"]
    assert subscriptions[0].filter == {"prefix": "invoices/2026/", "directory": False}
    assert subscriptions[0].subscriber == SUBSCRIBER
    assert subscriptions[0].mode == DURABLE


def test_an_empty_filter_means_every_event_of_those_types(gateway, ees, events):
    ees.subscribe_events(SUBSCRIBER, [OBJECT_CREATED])

    assert gateway.last().json()["filter"] == {}


def test_a_live_subscription_says_so_and_an_ephemeral_one_is_left_out_unless_asked_for(gateway, ees, events):
    """``ephemeral`` is what a websocket connection sets for itself; an ordinary consumer never
    sends it."""
    ees.subscribe_events(SUBSCRIBER, [OBJECT_CREATED], mode=LIVE)
    assert gateway.last().json()["mode"] == "live"
    assert "ephemeral" not in gateway.last().json()

    ees.subscribe_events(SUBSCRIBER, [OBJECT_CREATED], ephemeral=True)
    assert gateway.last().json()["ephemeral"] is True


def test_unsubscribing_one_type_or_all_of_them(gateway, ees):
    gateway.answer("ees", "unsubscribe-events", {"subscriber": SUBSCRIBER, "removed": 2})

    result = ees.unsubscribe_events(SUBSCRIBER)
    assert gateway.last().json() == {"name": SUBSCRIBER}
    assert (result.subscriber, result.removed) == (SUBSCRIBER, 2)

    ees.unsubscribe_events(SUBSCRIBER, OBJECT_DELETED)
    assert gateway.last().json() == {"name": SUBSCRIBER, "eventType": "esm.object.deleted"}


def test_listing_says_what_is_waiting(gateway, ees, events):
    """The backlog is the useful half: a subscriber whose waiting climbs is one that is not keeping
    up, or one nobody is reading any more."""
    ees.subscribe_events(SUBSCRIBER, [OBJECT_CREATED])
    events.publish("event-1").publish("event-2")

    result = ees.list_subscriptions(SUBSCRIBER)

    assert gateway.last().json() == {"name": SUBSCRIBER}
    assert [s.event_type for s in result.subscriptions] == ["esm.object.created"]
    assert result.waiting == 2


# -- events ------------------------------------------------------------------------------------------


def test_claiming_and_acknowledging_events(gateway, ees, events):
    events.publish("event-1", key="invoices/2026/q3.pdf").publish("event-2")

    result = ees.receive_events(SUBSCRIBER, wait_time=20)

    assert gateway.last().json() == {"name": SUBSCRIBER, "maxEvents": 10, "waitTime": 20,
                                     "visibilityTimeout": 300}
    assert result.total == 2
    event = result.events[0]
    assert (event.event_id, event.event_type, event.source_module) == ("event-1",
                                                                       "esm.object.created", "esm")
    # The payload is the event type's own shape, handed over as it arrived.
    assert event["key"] == "invoices/2026/q3.pdf"
    assert event.payload["bucketName"] == "invoices"
    assert event.get("nothing") is None

    # A claim is not a read: nothing else takes these while they are out.
    assert ees.receive_events(SUBSCRIBER).events == []

    acknowledged = ees.ack_events(SUBSCRIBER, [e.event_id for e in result.events])
    assert gateway.last().json() == {"name": SUBSCRIBER, "eventIds": ["event-1", "event-2"]}
    assert (acknowledged.acknowledged, acknowledged.waiting) == (2, 0)


def test_acknowledging_one_event_at_a_time(gateway, ees, events):
    events.publish("event-1")

    claimed = ees.receive_events(SUBSCRIBER)
    result = ees.ack_event(SUBSCRIBER, claimed.events[0].event_id)

    assert gateway.last().json() == {"name": SUBSCRIBER, "eventIds": ["event-1"]}
    assert result.acknowledged == 1


def test_an_event_that_was_never_claimed_cannot_be_acknowledged(gateway, ees, events):
    """Which is what makes the receipt mean something: acknowledging deletes what this consumer
    actually took."""
    events.publish("event-1")

    result = ees.ack_event(SUBSCRIBER, "event-1")

    assert (result.acknowledged, result.waiting) == (0, 1)


def test_asking_for_no_wait_says_so(gateway, ees, events):
    ees.receive_events(SUBSCRIBER, max_events=50, visibility_timeout=30)

    assert gateway.last().json() == {"name": SUBSCRIBER, "maxEvents": 50, "waitTime": 0,
                                     "visibilityTimeout": 30}


def test_a_long_poll_outlives_the_sessions_ordinary_timeout(gateway, events):
    """The request is meant to take as long as the server was asked to hold it, so it gets its own
    deadline; the client-wide one is sized for an answer that comes straight back."""
    prepared(gateway)

    def slow_receive(request: RecordedRequest) -> tuple[int, dict]:
        time.sleep(1.0)
        return 200, {"events": [], "total": 0}

    gateway.on("ees", "receive-events", slow_receive)
    session = Euclid.for_server(gateway.base_url).login("jens", "secret", timeout=0.3)

    with session:
        assert session.ees().receive_events(SUBSCRIBER, wait_time=2).events == []


# -- everything else ------------------------------------------------------------------------------------


def test_ees_is_signed_and_follows_the_session(gateway, events):
    gateway.answer("eam", "change-namespace", {})

    with Euclid.for_server(gateway.base_url).login("jens", "secret") as session:
        ees = session.ees()
        ees.list_subscriptions(SUBSCRIBER)
        assert gateway.last().auth == "sigv4"
        assert gateway.last().headers["x-euclid-target"] == "ees"

        session.change_namespace("development")
        ees.list_subscriptions(SUBSCRIBER)
        assert gateway.last().headers["x-euclid-namespace"] == "development"

        assert session.ees() is ees


def test_a_refusal_carries_the_servers_reason(gateway, ees):
    gateway.answer("ees", "subscribe-events", {"error": 'mode must be "durable" or "live"'},
                   status=400)

    with pytest.raises(EuclidServiceError) as raised:
        ees.subscribe_events(SUBSCRIBER, [OBJECT_CREATED], mode="whenever")

    assert (raised.value.target, raised.value.action, raised.value.status) == ("ees",
                                                                               "subscribe-events", 400)
    assert raised.value.reason == 'mode must be "durable" or "live"'


def test_metrics_and_call(gateway, ees):
    gateway.answer("ees", "get-metrics", {"items": [{"name": "ees-events", "value": 3}]})
    gateway.answer("ees", "some-future-action", {"ok": True})

    assert ees.metrics() == {"items": [{"name": "ees-events", "value": 3}]}
    assert ees.call("some-future-action", {"x": 1}) == {"ok": True}
    assert gateway.last().json() == {"x": 1}
