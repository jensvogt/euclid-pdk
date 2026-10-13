"""The shapes EAD sends back.

Parsed the same defensive way as every other module's. One resource and two counts: an audit event
is a record of a command somebody ran, and the only questions asked of it are which ones and how
many.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from . import _json

__all__ = ["AuditEvent", "ListEventsResult"]


@dataclass
class AuditEvent:
    """One command somebody ran, as the trail recorded it.

    ``parameters`` is the command's arguments as one string, the way the command was given rather
    than a parsed structure - which is what makes the trail readable years later, when the module
    that wrote it may take different arguments.

    ``status`` is how the command ended. An audit trail that only kept the successes would be the
    wrong half: a refused delete is more interesting than a granted one.
    """

    account_id: str = ""
    namespace: str = ""
    user_id: str = ""
    #: The module the command was aimed at - ``esm``, ``eqs``, and so on.
    module: str = ""
    command: str = ""
    parameters: str = ""
    status: str = ""
    created: str = ""

    @staticmethod
    def from_json(document: Any) -> "AuditEvent":
        return AuditEvent(
            _json.text(document, "accountId"), _json.text(document, "namespace"),
            _json.text(document, "userId"), _json.text(document, "module"),
            _json.text(document, "command"), _json.text(document, "parameters"),
            _json.text(document, "status"), _json.text(document, "created"))


@dataclass
class ListEventsResult:
    """One page of the trail, and how long it is - as far as the server was willing to count.

    ``total_exact`` is what makes the count honest rather than wrong. An exact total means reading
    an index entry per matching event, and on a trail of tens of millions that is seconds of
    database time spent beside a page of fifty - paid again on every page somebody turns. So the
    server stops counting at a cap: ``total_exact`` false means "at least this many", which is all
    a pager needs in order to know there is a next page.

    :meth:`~euclid.modules.ead.EuclidEad.count_events` is the real number, for when it is the
    answer rather than a scrollbar.
    """

    events: list[AuditEvent] = field(default_factory=list)
    total: int = 0
    #: Whether ``total`` is the count or the cap the server stopped at.
    total_exact: bool = False

    @staticmethod
    def from_json(document: Any) -> "ListEventsResult":
        return ListEventsResult(
            [AuditEvent.from_json(e) for e in _json.documents(document, "events")],
            _json.number(document, "total"), _json.flag(document, "totalExact"))
