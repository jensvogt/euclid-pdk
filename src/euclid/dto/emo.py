"""The shapes EMO takes and sends back.

The type of a metric is the one field here worth reading twice. It is not decoration: EMO rolls
five-minute rows into hourly ones and hourly into daily, and the type is what says whether that is
a sum or a mean. A counter pushed as a gauge is averaged into nonsense, and a gauge pushed as a
rate is summed into more of it - which is why :meth:`Metric.rate` and :meth:`Metric.gauge` are how
a metric is built rather than filling the field in by hand.

The same two types are spelled two ways, which is the server's doing rather than this SDK's: a push
sends ``gauge`` or ``rate`` in lower case - and reads anything that is not exactly ``rate`` as a
gauge - while a listing answers with ``GAUGE`` or ``RATE`` in upper case. Both spellings are named
below, and neither is converted into the other, so what this SDK says matches what euclid-cli sees.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from . import _json

__all__ = [
    "GAUGE",
    "RATE",
    "STORED_GAUGE",
    "STORED_RATE",
    "RAW",
    "HOUR",
    "DAY",
    "Metric",
    "MetricSample",
    "MetricQuery",
]


#: What a push calls a value that stands on its own - a queue depth, a heap size. Rolled up by
#: averaging.
GAUGE = "gauge"

#: What a push calls a value accumulated over the interval it covers - requests served, bytes
#: written. Rolled up by summing.
RATE = "rate"

#: The same two, as a listing spells them back.
STORED_GAUGE = "GAUGE"
STORED_RATE = "RATE"

#: How coarse the rows a listing reads are: as they were pushed...
RAW = "RAW"
#: ...rolled into hours, or into days.
HOUR = "HOUR"
DAY = "DAY"


def _labels(values: Mapping[str, str] | None) -> dict[str, str]:
    """Labels as the server reads them: an object of strings.

    Values are stringified rather than passed through, because that is what a dimension is. A number
    here would split one series in two on nothing but its JSON spelling.
    """
    return {str(name): str(value) for name, value in (values or {}).items()}


@dataclass
class Metric:
    """One measurement, on its way to EMO.

    Built through :meth:`gauge` and :meth:`rate` rather than by naming the type, because those are
    the only two values the server reads and the difference between them is what a rollup is.
    """

    name: str = ""
    labels: dict[str, str] = field(default_factory=dict)
    value: float = 0.0
    #: :data:`GAUGE` or :data:`RATE`.
    type: str = GAUGE

    @staticmethod
    def gauge(name: str, value: float, labels: Mapping[str, str] | None = None) -> "Metric":
        """A value that stands on its own at the moment it was read."""
        return Metric(name, _labels(labels), float(value), GAUGE)

    @staticmethod
    def rate(name: str, value: float, labels: Mapping[str, str] | None = None) -> "Metric":
        """A value accumulated over the interval this push covers."""
        return Metric(name, _labels(labels), float(value), RATE)

    def to_json(self) -> dict[str, Any]:
        """This metric as one item of a push."""
        # The map rather than the older labelName/labelValue pair, which the server still accepts
        # and reads as one more dimension. Sending both would only repeat the first.
        return {"name": self.name, "labels": _labels(self.labels), "value": self.value,
                "type": self.type}


@dataclass
class MetricSample:
    """One row a listing answered with: what a series read, and over how many samples.

    ``value`` is the mean for a gauge and the sum for a rate, which is what ``type`` on the row is
    for. ``min_value`` and ``max_value`` are the extremes those samples reached and survive a
    rollup, so an hourly row still knows the worst second inside it.
    """

    name: str = ""
    labels: dict[str, str] = field(default_factory=dict)
    value: float = 0.0
    min_value: float = 0.0
    max_value: float = 0.0
    #: How many pushed samples this row was made of.
    samples: int = 0
    #: :data:`STORED_GAUGE` or :data:`STORED_RATE` - upper case, unlike what a push sends.
    type: str = ""
    #: :data:`RAW`, :data:`HOUR` or :data:`DAY`.
    resolution: str = ""
    timestamp: str = ""

    @staticmethod
    def from_json(document: Any) -> "MetricSample":
        return MetricSample(
            _json.text(document, "name"), _json.string_map(document, "labels"),
            _json.real(document, "value"), _json.real(document, "minValue"),
            _json.real(document, "maxValue"), _json.number(document, "samples"),
            _json.text(document, "type"), _json.text(document, "resolution"),
            _json.text(document, "timestamp"))


@dataclass
class MetricQuery:
    """Which rows a listing or an average reads.

    Everything is optional and narrows what is read: a query that names nothing takes the most
    recent rows of every series, which is what a first look at an installation wants and not what a
    graph does.
    """

    #: The series' name, matched exactly.
    name: str = ""
    #: The dimensions a row has to carry. A row may carry more.
    labels: dict[str, str] = field(default_factory=dict)
    #: The most rows to answer with; the server's own default is 100.
    limit: int = 0
    #: The window, as ISO 8601 timestamps. Empty for "as far back as there is" and "up to now".
    since: str = ""
    until: str = ""
    #: :data:`RAW`, :data:`HOUR` or :data:`DAY`. Empty leaves the choice to the server.
    resolution: str = ""

    def to_json(self) -> dict[str, Any]:
        """This query as a request body.

        Each field is left out entirely when it says nothing: the server reads an absent field as
        "do not narrow by this", and an empty string as a name that matches nothing.
        """
        document: dict[str, Any] = {}
        if self.name:
            document["name"] = self.name
        if self.labels:
            document["labels"] = _labels(self.labels)
        if self.limit > 0:
            document["limit"] = self.limit
        # ``from`` on the wire, ``since`` here - the one is a Python keyword, so a field of that
        # name could not be passed to this dataclass by keyword at all.
        if self.since:
            document["from"] = self.since
        if self.until:
            document["to"] = self.until
        if self.resolution:
            document["resolution"] = self.resolution
        return document
