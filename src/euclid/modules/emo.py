"""EMO - euclid's monitoring module: the metrics an installation keeps, and the way an application
adds its own to them.

One object, :class:`EuclidEmo`, built from a session that has already logged in::

    emo = Euclid.for_server(url).login("jens", "secret").emo()

    emo.push_metrics("invoice-parser", [Metric.rate("invoices.parsed", 41),
                                        Metric.gauge("queue.depth", 7)])

euclid's own modules push their samples here on their own schedule rather than being polled,
because a module the autoscaler is tearing down cannot answer a poll - it simply stops pushing. An
application is in the same position, and pushing puts the decision about what is worth publishing
where it belongs: in the application, which is the only thing that knows.

What lands here lands in the same rows EMO's own collectors write, and therefore in the same
rollups, the same retention and the same graphs as CPU, memory and the module gauges.

**Measuring rather than pushing.** :meth:`~EuclidEmo.push_metrics` takes numbers that are already
final. An application that wants to count requests and time them wants :class:`MeterRegistry`
instead, which accumulates meters in the process and pushes them on a step - what a Micrometer
registry does in euclid-jdk, and what euclid's own C++ modules do with ``Core::Monitoring``.

:meth:`~EuclidEmo.list_metrics` and :meth:`~EuclidEmo.average` are administrator-only, server-side:
an application publishes its own numbers without special rights, and reading everybody's is a
different question.
"""

from __future__ import annotations

import functools
import math
import threading
import time
from typing import Any, Callable, Iterable, Mapping

from ..dto.emo import Metric, MetricQuery, MetricSample
from .base import ModuleClient

__all__ = ["EuclidEmo", "TARGET", "DEFAULT_STEP", "Counter", "Gauge", "Timer", "MeterRegistry"]

TARGET = "emo"

#: How long a step is unless one is given, in seconds - what euclid-jdk's registry defaults to.
DEFAULT_STEP = 60.0


class EuclidEmo(ModuleClient):
    """EMO's operations, on the credentials of the session that created it.

    Built by :meth:`euclid.EuclidSession.emo` rather than directly, so that it shares that
    session's identity, namespace and connection settings - and follows them as they change.
    """

    target = TARGET

    def push_metrics(self, module: str, metrics: Iterable[Metric]) -> None:
        """Pushes a batch of measurements, reported under one name.

        An empty batch is not sent at all: there is nothing to record, and a request that says so is
        a round trip for nothing - which matters here more than elsewhere, since a push usually runs
        on a timer forever.

        :param module: what is reporting. An application's own id is the useful value: it is how a
            reader tells one pool's numbers from another's, and it is what a listing filters on.
        :param metrics: the batch. Every metric's type decides how it is rolled up - a rate is
            summed and a gauge averaged - so a counter pushed as a gauge is averaged into nonsense.
        """
        items = [metric.to_json() for metric in metrics]
        if not items:
            return
        self._call("push-metrics", {"module": module, "items": items})

    def list_metrics(self, query: MetricQuery | None = None) -> list[MetricSample]:
        """The rows a query matches, most recent first. Administrator-only, server-side.

        Sent as ``list``, which is the action's name on the wire; spelled out here because a listing
        of metrics is what it is, and because :meth:`call` is there for the wire name.
        """
        answer = self._call("list", (query or MetricQuery()).to_json())
        items = answer.get("items")
        return [MetricSample.from_json(item) for item in items] if isinstance(items, list) else []

    def average(self, query: MetricQuery | None = None) -> float:
        """The mean of the values a query matches, over the window it names.

        One number rather than the rows behind it, for the question a dashboard tile asks. It is
        weighted by how many samples each row was made of, so an hourly row of six hundred counts
        for more than a raw one of one. Administrator-only, as :meth:`list_metrics` is.
        """
        value = self._call("average", (query or MetricQuery()).to_json()).get("average")
        return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0.0

    def registry(self, module: str, step: float = DEFAULT_STEP,
                 common_labels: Mapping[str, str] | None = None,
                 publish_on_stop: bool = True) -> "MeterRegistry":
        """A :class:`MeterRegistry` publishing through this client.

        The way in, rather than building one by hand: an application that measures anything wants
        meters and a step, not a batch of final numbers.
        """
        return MeterRegistry(self, module, step, common_labels, publish_on_stop)


# -- meters ----------------------------------------------------------------------------------
#
# What an application records into, between one publish and the next. Each holds its own lock
# rather than relying on the interpreter: ``+=`` on a float is a read, an add and a store, and two
# threads counting at once can lose one of them however briefly the window is.


class Counter:
    """A count of things that happened, reported per step and then started again.

    A handle rather than the meter itself: the registry owns what this points at, so asking for the
    same name and labels twice gives two handles onto one count. Incrementing is safe from any
    thread.
    """

    __slots__ = ("_lock", "_value")

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._value = 0.0

    def increment(self, amount: float = 1.0) -> None:
        """Counts one more, or ``amount`` more."""
        with self._lock:
            self._value += amount

    @property
    def count(self) -> float:
        """What has accumulated since the last publish. For a test or a log line - the registry
        reads and resets this itself."""
        with self._lock:
            return self._value

    def _take(self) -> float:
        """What accumulated, and start again - which is what makes a counter a rate."""
        with self._lock:
            value, self._value = self._value, 0.0
            return value


class Gauge:
    """A value that stands on its own whenever it is read - a queue depth, a pool size.

    Not reset by a publish, because a gauge is what it is rather than what happened: a depth of nine
    is still nine after somebody has looked at it.
    """

    __slots__ = ("_lock", "_value")

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._value = 0.0

    def set(self, value: float) -> None:
        """Sets what this gauge reads now."""
        with self._lock:
            self._value = float(value)

    @property
    def value(self) -> float:
        """What it last read."""
        with self._lock:
            return self._value


class Timer:
    """How long something took, and how often it was done.

    Three ways to record one, and the last two are why this is worth having over a stopwatch written
    by hand - a timing taken at the end of a function is a timing that is not taken when the
    function returns early or raises::

        timer.record(elapsed_seconds)          # a duration something else measured

        with timer:                            # this block, however it is left
            parse(invoice)

        @timer                                 # every call of this function
        def parse(invoice): ...

    Published as three series, named the way euclid-jdk's Micrometer registry names them so that a
    Python application's timings graph beside a Java one's: ``<name>.count`` and ``<name>.total`` as
    rates, and ``<name>.max`` as a gauge.

    **Durations are given in seconds and published in milliseconds.** Seconds because that is what
    :func:`time.perf_counter` and :meth:`~datetime.timedelta.total_seconds` deal in, so a duration
    measured anywhere in Python can be passed straight here; milliseconds because that is the base
    unit euclid-jdk publishes in and the one euclid's own modules time in, and two SDKs reporting
    the same operation in different units would not be comparable.

    There are no percentiles, which is a property of where this goes rather than an omission: EMO
    stores one value per series per interval, so a p99 would have to be computed here and pushed as
    a series of its own. The count, the total and the worst case are what the JDK registry publishes
    too.
    """

    __slots__ = ("_lock", "_count", "_total", "_max", "_started")

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._count = 0
        self._total = 0.0
        self._max = 0.0
        self._started: list[float] = []

    def record(self, elapsed: float) -> None:
        """Records one timing, in **seconds**."""
        milliseconds = float(elapsed) * 1000.0
        with self._lock:
            self._count += 1
            self._total += milliseconds
            self._max = max(self._max, milliseconds)

    def __enter__(self) -> "Timer":
        # A stack rather than one field: the same timer may be entered by several threads at once,
        # and by one thread recursively.
        with self._lock:
            self._started.append(time.perf_counter())
        return self

    def __exit__(self, *exc_info: object) -> None:
        with self._lock:
            started = self._started.pop() if self._started else time.perf_counter()
        # Recorded whichever way the block was left. A call that failed slowly is exactly the one
        # worth having timed, so an exception is timed and then goes on - nothing is swallowed here.
        self.record(time.perf_counter() - started)

    def __call__(self, function: Callable[..., Any]) -> Callable[..., Any]:
        """Times every call of a function, as a decorator."""

        @functools.wraps(function)
        def timed(*args: Any, **kwargs: Any) -> Any:
            with self:
                return function(*args, **kwargs)

        return timed

    @property
    def count(self) -> int:
        """How many timings since the last publish."""
        with self._lock:
            return self._count

    def _take(self) -> tuple[float, float, float]:
        """Count, total and worst case, and start again."""
        with self._lock:
            taken = (float(self._count), self._total, self._max)
            self._count, self._total, self._max = 0, 0.0, 0.0
            return taken


def _publishable(value: float) -> bool:
    """Whether a value is worth pushing.

    A NaN or an infinity is what a gauge over an empty collection reads, and it would poison every
    rollup that averaged it afterwards. Skipped here rather than stored.
    """
    return math.isfinite(value)


def _key(name: str, labels: Mapping[str, str] | None) -> tuple[str, tuple[tuple[str, str], ...]]:
    """What identifies a meter: its name and the labels it was created with, in a form that can be
    a dictionary key."""
    return name, tuple(sorted((str(k), str(v)) for k, v in (labels or {}).items()))


class MeterRegistry:
    """The meters an application keeps, and the thread that pushes them to EMO.

    ::

        with session.emo().registry("invoice-parser", common_labels={"host": hostname}) as metrics:
            parsed = metrics.counter("invoices.parsed")
            failed = metrics.counter("invoices.parsed", {"outcome": "failed"})
            duration = metrics.timer("invoice.parse")
            metrics.gauge_from("queue.depth", lambda: float(len(queue)))

            for invoice in incoming:
                with duration:
                    parsed.increment() if parse(invoice) else failed.increment()

    **Why this exists.** euclid-jdk publishes an application's metrics through Micrometer, which
    Python has no one equivalent of. What Micrometer actually provides is two things: meters an
    application records into, and a registry that accumulates them over a step and publishes the
    result. Neither needs Micrometer, and euclid's own C++ modules have done both for years with
    ``Core::Monitoring`` - this is that, pushed through an authenticated session, and named the way
    the JDK registry names things so both land in the same series.

    **What a step means.** A counter and a timer report what accumulated since the last publish and
    start again, which is what makes them rates to EMO - the thing a rollup sums. A gauge reports
    what it reads at the moment of publishing and is not reset. Every registered meter is published
    every step, including the ones that did not move: a zero is a fact, and a gap in a graph is not.

    **What it costs.** Every meter is one stored row per step, forever - so the number of label
    combinations is the number of series, and a label carrying a request id or a customer name is
    how a monitoring database is filled up. Decide the labels where the meter is created, which is
    the one place that can.

    **Failure.** A push that fails is counted in :attr:`failed_publishes` and the batch is dropped.
    It is not retried: a rate sent twice is counted twice, and a monitoring system that lies about
    throughput is worse than one with a gap in it. Nothing here raises at the application - a
    process does not stop because it could not say how it was doing.

    Safe to use from any thread.
    """

    def __init__(self, emo: EuclidEmo, module: str, step: float = DEFAULT_STEP,
                 common_labels: Mapping[str, str] | None = None,
                 publish_on_stop: bool = True) -> None:
        """
        :param emo: the client the batches are pushed through.
        :param module: what this reports under - an application's own id.
        :param step: how long a step is, in seconds: how much a counter accumulates before it is
            published and started again. Zero starts no thread at all and leaves publishing to
            whoever calls :meth:`publish` - for an application with a loop of its own, and for a
            test.
        :param common_labels: labels added to every metric this registry publishes - the host, the
            instance, the version. A label on a meter of the same name wins.
        :param publish_on_stop: whether stopping publishes the step in hand rather than dropping it.
        :raises ValueError: if no module name is given. A batch has to say what is reporting, and
            the server refuses one that does not.
        """
        if not module:
            raise ValueError("a metrics registry has to say what is reporting - give it a module name")

        self._emo = emo
        self._module = module
        self._step = float(step)
        self._common_labels = {str(k): str(v) for k, v in (common_labels or {}).items()}
        self._publish_on_stop = publish_on_stop

        self._lock = threading.Lock()
        self._counters: dict[Any, Counter] = {}
        self._gauges: dict[Any, Gauge] = {}
        self._timers: dict[Any, Timer] = {}
        self._suppliers: dict[Any, Callable[[], float]] = {}

        self._publishes = 0
        self._failed = 0

        self._stopping = threading.Event()
        self._thread: threading.Thread | None = None
        if self._step > 0:
            # A daemon thread: publishing metrics is not a reason for an interpreter to stay up, and
            # an application that wants the last batch sent closes the registry.
            self._thread = threading.Thread(target=self._run, name=f"euclid-metrics-{module}",
                                            daemon=True)
            self._thread.start()

    # -- the meters, by name -----------------------------------------------------------------

    def counter(self, name: str, labels: Mapping[str, str] | None = None) -> Counter:
        """The counter of this name and these labels, creating it the first time.

        Asking again for the same pair answers the same meter, so a handle need not be passed
        around - though keeping one is cheaper than looking it up on a hot path.
        """
        return self._meter(self._counters, name, labels, Counter)

    def gauge(self, name: str, labels: Mapping[str, str] | None = None) -> Gauge:
        """The gauge of this name and these labels, creating it the first time."""
        return self._meter(self._gauges, name, labels, Gauge)

    def timer(self, name: str, labels: Mapping[str, str] | None = None) -> Timer:
        """The timer of this name and these labels, creating it the first time."""
        return self._meter(self._timers, name, labels, Timer)

    def gauge_from(self, name: str, supplier: Callable[[], float],
                   labels: Mapping[str, str] | None = None) -> None:
        """A gauge that reads itself, by asking at every publish.

        For what something already knows - a queue's depth, a pool's size - where setting a gauge
        would mean remembering to. The supplier is called on the publishing thread, so it should
        answer quickly and not raise; one that raises is skipped for that step and the rest of the
        batch goes.
        """
        with self._lock:
            self._suppliers[_key(name, labels)] = supplier

    def _meter(self, meters: dict[Any, Any], name: str, labels: Mapping[str, str] | None,
               factory: Callable[[], Any]) -> Any:
        key = _key(name, labels)
        with self._lock:
            meter = meters.get(key)
            if meter is None:
                meter = meters[key] = factory()
            return meter

    # -- publishing --------------------------------------------------------------------------

    def collect(self) -> list[Metric]:
        """The step in hand, as metrics - which takes counters and timers and starts them again.

        :meth:`publish` is this plus the push. Public because it is what a test asserts on, and what
        an application that would rather send the batch itself asks for.
        """
        batch: list[Metric] = []

        with self._lock:
            for (name, labels), counter in self._counters.items():
                value = counter._take()
                if _publishable(value):
                    batch.append(Metric.rate(name, value, self._labels_for(labels)))

            for (name, labels), timer in self._timers.items():
                count, total, worst = timer._take()
                merged = self._labels_for(labels)
                # The three series euclid-jdk's Micrometer registry publishes for a timer, under the
                # same names, so that the two SDKs' timings are the same shape in a graph.
                if _publishable(count):
                    batch.append(Metric.rate(f"{name}.count", count, merged))
                if _publishable(total):
                    batch.append(Metric.rate(f"{name}.total", total, merged))
                if _publishable(worst):
                    batch.append(Metric.gauge(f"{name}.max", worst, merged))

            # Read rather than taken: a gauge is what it is, and a publish is somebody looking.
            for (name, labels), gauge in self._gauges.items():
                value = gauge.value
                if _publishable(value):
                    batch.append(Metric.gauge(name, value, self._labels_for(labels)))

            # Copied out, and called below with the lock released: a supplier that reaches back into
            # this registry would otherwise deadlock on a lock it cannot see.
            suppliers = dict(self._suppliers)

        for (name, labels), supplier in suppliers.items():
            try:
                value = float(supplier())
            except Exception:
                # One gauge that could not read itself is not a reason to lose the batch it was in.
                continue
            if _publishable(value):
                batch.append(Metric.gauge(name, value, self._labels_for(labels)))

        return batch

    def publish(self) -> None:
        """Collects the step in hand and pushes it. Never raises: a failure is counted and dropped."""
        batch = self.collect()
        if not batch:
            return
        try:
            self._emo.push_metrics(self._module, batch)
        except Exception:
            self._failed += 1
        else:
            self._publishes += 1

    @property
    def publishes(self) -> int:
        """How many batches have been pushed.

        With :attr:`failed_publishes`, the answer to "is the monitoring working" - which nothing
        else here can give, since a metric about pushing metrics cannot be pushed.
        """
        return self._publishes

    @property
    def failed_publishes(self) -> int:
        """How many batches failed to be pushed."""
        return self._failed

    # -- lifetime ----------------------------------------------------------------------------

    def close(self) -> None:
        """Stops the thread, publishing the step in hand unless told not to.

        Safe to call twice, and called for you by the ``with`` form.
        """
        thread, self._thread = self._thread, None
        if thread is not None:
            # Set and waited on rather than slept through: a process shutting down should not have
            # to sit out a step before its thread notices.
            self._stopping.set()
            thread.join()
        if self._publish_on_stop:
            self._publish_on_stop = False
            self.publish()

    def __enter__(self) -> "MeterRegistry":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def _run(self) -> None:
        while not self._stopping.wait(self._step):
            self.publish()

    def _labels_for(self, labels: Iterable[tuple[str, str]]) -> dict[str, str]:
        """The common labels, with a meter's own laid over them, so a meter that names a label the
        registry also names keeps its own answer."""
        merged = dict(self._common_labels)
        merged.update(labels)
        return merged
