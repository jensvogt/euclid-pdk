"""EAD - euclid's audit module: the trail of what was run, by whom, and how it ended.

One object, :class:`EuclidEad`, built from a session that has already logged in::

    ead = Euclid.for_server(url).login("jens", "secret").ead()

    for event in ead.list_events(module="esm", page_size=50).events:
        print(event.created, event.user_id, event.command, event.status)

Nothing writes through this client. euclid's own modules record what they were asked to do as they
do it, and EAD is the way back to those records - so the three actions here read, count and prune,
and there is no way to add an entry.

**A read is confined to the caller's own account**, always, whatever the request says. An audit one
account could read of another would hand over the shape of somebody else's installation - which
buckets they keep, who works there, what they do - through the one module built to be read by
people looking for exactly that.
"""

from __future__ import annotations

from typing import Any

from ..dto.ead import ListEventsResult
from .base import ModuleClient

__all__ = ["EuclidEad", "TARGET", "LIST_TOTAL_LIMIT"]

TARGET = "ead"

#: Where the server stops counting for a listing's ``total`` - see
#: :attr:`~euclid.dto.ead.ListEventsResult.total_exact`. Not a limit on what can be read: paging
#: past it works, it is only the total that is capped.
LIST_TOTAL_LIMIT = 100_000


class EuclidEad(ModuleClient):
    """EAD's operations, on the credentials of the session that created it.

    Built by :meth:`euclid.EuclidSession.ead` rather than directly, so that it shares that
    session's identity, namespace and connection settings - and follows them as they change.
    """

    target = TARGET

    def list_events(self, user_id: str = "", module: str = "", command: str = "",
                    page_size: int = 10, page_index: int = 0) -> ListEventsResult:
        """One page of the trail, newest first.

        Every filter is optional and narrows what is read; giving none reads the whole trail of the
        caller's account. They combine, so ``module="esm", command="delete-bucket"`` is the
        question an audit is usually opened to ask.

        :param user_id: whose commands.
        :param module: which module they were aimed at - ``esm``, ``eqs``, and so on.
        :param command: which action, named exactly as it travels on the wire.
        """
        return ListEventsResult.from_json(self._call("list-events", {
            "userId": user_id, "module": module, "command": command,
            "pageSize": page_size, "pageIndex": page_index}))

    def count_events(self, user_id: str = "", module: str = "", command: str = "") -> int:
        """How many events match, counted exactly.

        The same filters :meth:`list_events` takes. Separate from a listing because the exact count
        is what a listing deliberately does not pay for - ask this when the number is the answer
        rather than a scrollbar.
        """
        return self._number("count-events", {
            "userId": user_id, "module": module, "command": command}, "count")

    def purge_events(self, older_than_days: int) -> int:
        """Removes events older than a number of days, and returns how many went.

        :param older_than_days: the window to keep, in days. Must be positive - a purge with no
            window would remove the whole trail, so the server refuses it and so does this.
        :raises ValueError: if the window is not positive.

        Unlike the reads, this is **not** confined to the caller's account: the server prunes by
        age across the installation. An operator trimming a trail is trimming all of it.

        The purge is itself recorded. A command that removes audit records is exactly the kind of
        thing an audit is kept for, and its entry lands in the trail it just pruned.
        """
        if older_than_days <= 0:
            raise ValueError("older_than_days must be positive; a purge with no window would "
                             "remove the whole trail")
        return self._number("purge-events", {"olderThanDays": older_than_days}, "removed")

    def metrics(self) -> dict[str, Any]:
        """EAD's own metrics, as the server collects them. Returned unparsed - the shape belongs to
        the monitoring module rather than to EAD."""
        return self._call("get-metrics")
