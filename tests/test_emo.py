"""EMO, end to end against a fake euclid server.

Two things to check, and they are different in kind. The three actions are one request each, so what
those tests assert is the wire: that a push carries the fields the server reads, and that a query
leaves out what it does not narrow by.

The registry is the other half, and it is not about the wire at all - it is arithmetic over a step.
What matters there is that a counter reports what accumulated and starts again, that a gauge does
not, that a timer turns into the three series euclid-jdk publishes, and that a push which fails is
counted rather than raised at the application. Those run with ``step=0``, which starts no thread:
a test that waited for one would be a test that sometimes did not.
"""

from __future__ import annotations

import math
import threading

import pytest

from euclid import Euclid, EuclidServiceError
from euclid.dto.emo import (ABOVE, BELOW, CRITICAL, DAY, GAUGE, HOUR, MAX, OK, RATE,
                            STORED_RATE, AlertRule, Metric, MetricQuery)
from euclid.modules.emo import MeterRegistry
from test_eam import prepared


@pytest.fixture
def emo(gateway):
    """An EMO client on a logged-in session, closed with it."""
    prepared(gateway)
    with Euclid.for_server(gateway.base_url).login("jens", "secret") as session:
        yield session.emo()


@pytest.fixture
def metrics(gateway, emo):
    """A registry that publishes only when a test says so - no thread, no waiting."""
    gateway.answer("emo", "push-metrics", {})
    with emo.registry("invoice-parser", step=0) as registry:
        yield registry


# -- the actions -----------------------------------------------------------------------------


def test_pushing_sends_the_type_that_decides_the_rollup(gateway, emo):
    """Not decoration: a rate is summed into an hourly row and a gauge averaged, so a counter
    pushed as a gauge is averaged into nonsense."""
    gateway.answer("emo", "push-metrics", {})

    emo.push_metrics("invoice-parser", [Metric.rate("invoices.parsed", 41),
                                        Metric.gauge("queue.depth", 7, {"queue": "orders"})])

    assert gateway.last().json() == {"module": "invoice-parser", "items": [
        {"name": "invoices.parsed", "labels": {}, "value": 41.0, "type": RATE},
        {"name": "queue.depth", "labels": {"queue": "orders"}, "value": 7.0, "type": GAUGE}]}


def test_an_empty_batch_is_not_a_request(gateway, emo):
    """A push runs on a timer forever, so a round trip that records nothing is one worth not
    making."""
    gateway.answer("emo", "push-metrics", {})

    emo.push_metrics("invoice-parser", [])

    assert [r for r in gateway.requests if r.target == "emo"] == []


def test_a_label_is_a_string_whatever_it_arrived_as(gateway, emo):
    """The server takes them as strings because that is what a dimension is - a number here would
    split one series in two on nothing but its JSON spelling."""
    gateway.answer("emo", "push-metrics", {})

    emo.push_metrics("invoice-parser", [Metric.gauge("queue.depth", 7, {"shard": 3})])

    assert gateway.last().json()["items"][0]["labels"] == {"shard": "3"}


def test_a_query_leaves_out_what_it_does_not_narrow_by(gateway, emo):
    """An absent field means "do not narrow by this"; an empty string would be a name matching
    nothing."""
    gateway.answer("emo", "list", {"items": []})

    emo.list_metrics()
    assert gateway.last().json() == {}

    emo.list_metrics(MetricQuery(name="invoices.parsed", labels={"host": "box-1"}, limit=50,
                                 since="2026-09-01T00:00:00Z", resolution=HOUR))
    # ``from`` on the wire, ``since`` in Python - the one is a keyword.
    assert gateway.last().json() == {"name": "invoices.parsed", "labels": {"host": "box-1"},
                                     "limit": 50, "from": "2026-09-01T00:00:00Z",
                                     "resolution": HOUR}


def test_listing_parses_a_row_and_what_survives_a_rollup(gateway, emo):
    """An hourly row still knows the worst second inside it, which is what min and max are for."""
    gateway.answer("emo", "list", {"items": [
        {"name": "invoices.parsed", "labels": {"host": "box-1"}, "value": 1234.5,
         "minValue": 0.5, "maxValue": 88.25, "samples": 12, "type": "RATE", "resolution": "HOUR",
         "timestamp": "2026-09-17T10:00:00Z"},
        {"name": "queue.depth"},
    ]})

    rows = emo.list_metrics(MetricQuery(resolution=DAY))

    assert len(rows) == 2
    assert (rows[0].value, rows[0].min_value, rows[0].max_value) == (1234.5, 0.5, 88.25)
    assert (rows[0].samples, rows[0].type, rows[0].resolution) == (12, STORED_RATE, HOUR)
    assert rows[0].labels == {"host": "box-1"}
    # A field the server did not send reads as empty rather than raising.
    assert (rows[1].value, rows[1].labels, rows[1].type) == (0.0, {}, "")


def test_an_average_is_one_number(gateway, emo):
    gateway.answer("emo", "average", {"average": 12.5})

    assert emo.average(MetricQuery(name="invoices.parsed")) == 12.5
    assert gateway.last().json() == {"name": "invoices.parsed"}

    # And a value that is not a number reads as zero, as everywhere else in this SDK.
    gateway.answer("emo", "average", {})
    assert emo.average() == 0.0


def test_reading_is_administrator_only_and_says_so(gateway, emo):
    gateway.answer("emo", "list", {"error": "Administrator privileges required"}, status=403)

    with pytest.raises(EuclidServiceError) as raised:
        emo.list_metrics()

    assert (raised.value.target, raised.value.status) == ("emo", 403)
    assert raised.value.reason.startswith("Administrator")


# -- the registry ----------------------------------------------------------------------------


def test_a_registry_has_to_say_what_is_reporting(emo):
    with pytest.raises(ValueError, match="what is reporting"):
        emo.registry("")


def test_a_counter_reports_what_accumulated_and_starts_again(metrics):
    """Which is what makes it a rate: the step that is ending owns what it counted, and the next
    one starts from nothing."""
    parsed = metrics.counter("invoices.parsed")
    parsed.increment()
    parsed.increment(40)

    batch = metrics.collect()
    assert [(m.name, m.value, m.type) for m in batch] == [("invoices.parsed", 41.0, RATE)]

    # Registered, so it is published again - as a zero, because a zero is a fact and a gap in a
    # graph is not.
    assert [(m.name, m.value) for m in metrics.collect()] == [("invoices.parsed", 0.0)]


def test_asking_twice_for_a_meter_answers_the_same_one(metrics):
    assert metrics.counter("invoices.parsed") is metrics.counter("invoices.parsed")
    # But the labels are part of what identifies it, so these are two series.
    assert metrics.counter("invoices.parsed") is not metrics.counter("invoices.parsed",
                                                                     {"outcome": "failed"})


def test_a_gauge_is_what_it_is_rather_than_what_happened(metrics):
    """Not reset by a publish: a depth of nine is still nine after somebody has looked at it."""
    depth = metrics.gauge("queue.depth")
    depth.set(9)

    assert [(m.name, m.value, m.type) for m in metrics.collect()] == [("queue.depth", 9.0, GAUGE)]
    assert [m.value for m in metrics.collect()] == [9.0]


def test_a_gauge_can_read_itself_at_every_publish(metrics):
    """For what something already knows, where setting a gauge would mean remembering to."""
    queue = [1, 2, 3]
    metrics.gauge_from("queue.depth", lambda: float(len(queue)))

    assert [m.value for m in metrics.collect()] == [3.0]
    queue.pop()
    assert [m.value for m in metrics.collect()] == [2.0]


def test_a_gauge_that_cannot_read_itself_does_not_lose_the_batch(metrics):
    metrics.counter("invoices.parsed").increment()
    metrics.gauge_from("broken", lambda: 1 / 0)

    assert [m.name for m in metrics.collect()] == ["invoices.parsed"]


def test_a_timer_is_the_three_series_the_jdk_publishes(metrics):
    """Under the same names, so a Python application's timings graph beside a Java one's."""
    duration = metrics.timer("invoice.parse")
    duration.record(0.010)
    duration.record(0.030)

    batch = {m.name: m for m in metrics.collect()}

    assert set(batch) == {"invoice.parse.count", "invoice.parse.total", "invoice.parse.max"}
    assert batch["invoice.parse.count"].value == 2.0
    # Seconds in, milliseconds out - euclid-jdk's base unit, and what euclid's own modules time in.
    assert batch["invoice.parse.total"].value == pytest.approx(40.0)
    assert batch["invoice.parse.max"].value == pytest.approx(30.0)
    # The count and the total are rates; the worst case is where it stood, so a gauge.
    assert (batch["invoice.parse.count"].type, batch["invoice.parse.max"].type) == (RATE, GAUGE)


def test_a_timer_times_a_block_however_it_is_left(metrics):
    """A timing written by hand at the end of a function is one that is not taken when the function
    raises - which is exactly the call worth having timed."""
    duration = metrics.timer("invoice.parse")

    with pytest.raises(RuntimeError):
        with duration:
            raise RuntimeError("parse failed")

    assert duration.count == 1

    @duration
    def parse() -> str:
        return "parsed"

    assert parse() == "parsed"
    assert duration.count == 2


def test_a_sub_millisecond_timing_is_not_no_time_at_all(metrics):
    """Rounded to an integer it would be, and a timer that reports nothing for everything fast is
    a timer that hides the fast path."""
    duration = metrics.timer("invoice.parse")
    duration.record(0.0004)

    totals = {m.name: m.value for m in metrics.collect()}
    assert totals["invoice.parse.total"] == pytest.approx(0.4)


def test_a_meters_own_label_wins_over_the_registrys(gateway, emo):
    gateway.answer("emo", "push-metrics", {})

    with emo.registry("invoice-parser", step=0,
                      common_labels={"host": "box-1", "region": "eu"}) as registry:
        registry.counter("invoices.parsed", {"host": "box-2"}).increment()
        batch = registry.collect()

    assert batch[0].labels == {"host": "box-2", "region": "eu"}


def test_a_value_that_is_not_a_number_is_not_published(metrics):
    """A NaN is what a gauge over an empty collection reads, and it would poison every rollup that
    averaged it afterwards."""
    metrics.gauge("empty.mean").set(math.nan)
    metrics.gauge_from("infinite", lambda: math.inf)
    metrics.counter("invoices.parsed").increment()

    assert [m.name for m in metrics.collect()] == ["invoices.parsed"]


def test_publishing_sends_one_batch_under_the_registrys_module(gateway, metrics):
    metrics.counter("invoices.parsed").increment(41)
    metrics.gauge("queue.depth").set(7)

    metrics.publish()

    sent = gateway.last().json()
    assert sent["module"] == "invoice-parser"
    assert {item["name"] for item in sent["items"]} == {"invoices.parsed", "queue.depth"}
    assert (metrics.publishes, metrics.failed_publishes) == (1, 0)


def test_publishing_nothing_is_not_a_request(gateway, metrics):
    metrics.publish()

    assert [r for r in gateway.requests if r.target == "emo"] == []
    assert metrics.publishes == 0


def test_a_failed_push_is_counted_rather_than_raised(gateway, emo):
    """A process does not stop because it could not say how it was doing - and it is not retried,
    because a rate sent twice is counted twice."""
    gateway.answer("emo", "push-metrics", {"error": "EMO is down"}, status=503)

    with emo.registry("invoice-parser", step=0) as registry:
        registry.counter("invoices.parsed").increment()
        registry.publish()

        assert (registry.publishes, registry.failed_publishes) == (0, 1)
        # And what it held is gone rather than waiting to be sent again: the counter was taken when
        # the batch was collected, so the next step starts from nothing whatever became of it.
        assert [m.value for m in registry.collect()] == [0.0]


def test_closing_publishes_the_step_in_hand(gateway, emo):
    gateway.answer("emo", "push-metrics", {})

    registry = emo.registry("invoice-parser", step=0)
    registry.counter("invoices.parsed").increment(3)
    registry.close()

    assert gateway.last().json()["items"][0]["value"] == 3.0
    # Twice is not twice as much: closing again sends nothing.
    registry.close()
    assert registry.publishes == 1


def test_closing_can_be_told_to_drop_it_instead(gateway, emo):
    gateway.answer("emo", "push-metrics", {})

    with emo.registry("invoice-parser", step=0, publish_on_stop=False) as registry:
        registry.counter("invoices.parsed").increment()

    assert [r for r in gateway.requests if r.target == "emo"] == []


def test_a_step_starts_a_thread_that_stops_with_the_registry(gateway, emo):
    """The one test that does use the thread, and it waits on the publish rather than on a clock."""
    pushed = threading.Event()
    gateway.on("emo", "push-metrics", lambda _request: (pushed.set(), (200, {}))[1])

    with emo.registry("invoice-parser", step=0.05) as registry:
        registry.counter("invoices.parsed").increment()
        assert pushed.wait(5), "the registry's thread never published"

    assert registry.publishes >= 1
    # Stopping is not waited out: the thread is asked to stop rather than left to finish its step.
    assert not any(t.name.startswith("euclid-metrics-") and t.is_alive()
                   for t in threading.enumerate())


def test_emo_is_one_client_that_follows_the_session(gateway):
    prepared(gateway)
    gateway.answer("eam", "change-namespace", {})
    gateway.answer("emo", "push-metrics", {})

    with Euclid.for_server(gateway.base_url).login("jens", "secret") as session:
        emo = session.emo()
        emo.push_metrics("invoice-parser", [Metric.gauge("queue.depth", 1)])
        assert gateway.last().headers["x-euclid-target"] == "emo"
        assert gateway.last().auth == "sigv4"

        session.change_namespace("development")
        emo.push_metrics("invoice-parser", [Metric.gauge("queue.depth", 1)])
        assert gateway.last().headers["x-euclid-namespace"] == "development"

        assert session.emo() is emo


def test_call_reaches_an_emo_action_this_sdk_does_not_wrap(gateway, emo):
    gateway.answer("emo", "some-future-action", {"ok": True})

    assert emo.call("some-future-action", {"x": 1}) == {"ok": True}
    assert gateway.last().json() == {"x": 1}


def test_a_registry_built_by_hand_is_the_same_thing(gateway, emo):
    """The constructor is public; ``emo.registry(...)`` is only the way in."""
    gateway.answer("emo", "push-metrics", {})

    with MeterRegistry(emo, "invoice-parser", step=0) as registry:
        registry.counter("invoices.parsed").increment()
        registry.publish()

    assert gateway.last().json()["module"] == "invoice-parser"


# -- the catalogue and alerting -------------------------------------------------------------------


def test_describing_the_metrics_is_the_schema_not_the_data(gateway, emo):
    """What exists, how it is rolled up and which labels split it - what somebody needs before an
    alert rule can name a threshold."""
    gateway.answer("emo", "list-metrics", {"total": 2, "items": [
        {"name": "invoices.parsed", "type": "RATE", "series": 4,
         "labelNames": ["host", "outcome"], "lastSeen": "2026-11-06T09:00:00Z"},
        {"name": "queue.depth"}]})

    result = emo.describe_metrics(prefix="invoices", hours=72)

    assert gateway.last().json() == {"prefix": "invoices", "hours": 72}
    assert result.total == 2
    assert result.items[0].label_names == ["host", "outcome"]
    assert result.items[0].series == 4
    # A field the server did not send reads as empty rather than raising.
    assert (result.items[1].type, result.items[1].label_names) == ("", [])


def test_a_catalogue_window_of_less_than_an_hour_says_so_first(gateway, emo):
    with pytest.raises(ValueError, match="at least 1"):
        emo.describe_metrics(hours=0)

    assert [r for r in gateway.requests if r.action == "list-metrics"] == []


def test_creating_an_alert_rule(gateway, emo):
    gateway.answer("emo", "create-alert-rule", {
        "ruleId": "queue-backlog", "name": "eqs.messages.available", "labels": {"queue": "orders"},
        "statistic": "MAX", "comparison": "ABOVE", "warning": 1000.0, "critical": 10000.0,
        "forPeriods": 3, "clearPeriods": 2, "noDataAfterPeriods": 0, "enabled": True})

    rule = emo.create_alert_rule(AlertRule(
        rule_id="queue-backlog", name="eqs.messages.available", labels={"queue": "orders"},
        statistic=MAX, comparison=ABOVE, warning=1000, critical=10000,
        for_periods=3, clear_periods=2))

    sent = gateway.last().json()
    assert sent["ruleId"] == "queue-backlog" and sent["statistic"] == "MAX"
    assert (sent["warning"], sent["critical"]) == (1000, 10000)
    assert sent["forPeriods"] == 3
    assert (rule.warning, rule.critical) == (1000.0, 10000.0)


def test_a_rule_with_no_threshold_can_never_fire_and_is_refused_here(gateway, emo):
    """enabled=False is how a rule is suspended; no threshold at all is a mistake."""
    with pytest.raises(ValueError, match="can never fire"):
        emo.create_alert_rule(AlertRule(rule_id="r", name="m"))

    with pytest.raises(ValueError, match="needs a rule_id"):
        emo.create_alert_rule(AlertRule(name="m", warning=1))

    assert [r for r in gateway.requests if r.action == "create-alert-rule"] == []


def test_a_threshold_of_zero_is_a_threshold(gateway, emo):
    """Which is why None and 0 have to stay apart: a rate that should never drop to nothing is a
    rule somebody means."""
    gateway.answer("emo", "create-alert-rule", {"ruleId": "idle", "critical": 0.0})

    rule = emo.create_alert_rule(AlertRule(rule_id="idle", name="orders.placed",
                                           comparison=BELOW, critical=0))

    sent = gateway.last().json()
    assert sent["critical"] == 0 and "warning" not in sent
    assert rule.critical == 0.0 and rule.warning is None


def test_updating_and_deleting_an_alert_rule(gateway, emo):
    gateway.answer("emo", "update-alert-rule", {"ruleId": "queue-backlog", "warning": 2000.0})
    gateway.answer("emo", "delete-alert-rule", {})

    assert emo.update_alert_rule(AlertRule(rule_id="queue-backlog", name="m",
                                           warning=2000)).warning == 2000.0
    emo.delete_alert_rule("queue-backlog")
    assert gateway.last().json() == {"ruleId": "queue-backlog"}


def test_listing_rules_alerts_and_the_history(gateway, emo):
    gateway.answer("emo", "list-alert-rules", {"items": [
        {"ruleId": "queue-backlog", "name": "eqs.messages.available", "warning": 1000.0}]})
    gateway.answer("emo", "list-alerts", {"items": [
        {"ruleId": "queue-backlog", "labels": {"queue": "orders"}, "state": "CRITICAL",
         "severity": 2, "since": "2026-11-06T08:00:00Z", "value": 12000.0, "critical": 10000.0}]})
    gateway.answer("emo", "list-alert-history", {"items": [
        {"ruleId": "queue-backlog", "previous": "OK", "state": "CRITICAL", "value": 12000.0,
         "threshold": 10000.0, "reason": "above critical for 3 periods",
         "timestamp": "2026-11-06T08:00:00Z"}]})

    assert emo.list_alert_rules()[0].rule_id == "queue-backlog"
    assert gateway.last().json() == {}

    alerts = emo.list_alerts(active_only=True)
    assert gateway.last().json() == {"active": True}
    # One entry per label combination per rule, which is the granularity an alert fires at.
    assert alerts[0].labels == {"queue": "orders"}
    assert alerts[0].state == CRITICAL and alerts[0].is_firing
    assert alerts[0].value == 12000.0

    history = emo.list_alert_history(limit=50)
    assert gateway.last().json() == {"limit": 50}
    assert (history[0].previous, history[0].state) == (OK, CRITICAL)
    assert history[0].threshold == 10000.0


def test_nodata_is_firing_because_a_silent_series_is_not_a_healthy_one(gateway, emo):
    gateway.answer("emo", "list-alerts", {"items": [
        {"ruleId": "r", "state": "NODATA"}, {"ruleId": "r2", "state": "OK"}]})

    alerts = emo.list_alerts()

    assert [a.is_firing for a in alerts] == [True, False]
