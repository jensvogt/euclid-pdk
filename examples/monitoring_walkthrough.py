#!/usr/bin/env python3
"""EMO end to end: meters recorded, published on a step, and read back.

    python examples/monitoring_walkthrough.py https://euclid.example.com jens secret

Reports under a module name of its own, named after the moment it started. Metrics are the one thing
these walkthroughs cannot clean up after themselves - there is no delete for a row, and they go when
EMO's retention takes them - so reporting under a name nothing else uses is how this stays out of
your dashboards.

Reading rows back is administrator-only. A login without those rights does everything but the last
step, and says so rather than failing.
"""

from __future__ import annotations

import sys
import time

from euclid import Euclid, EuclidAuthenticationError, EuclidServiceError
from euclid.dto.emo import MetricQuery


def parse(invoice: int) -> bool:
    """Work to be measured: parses an invoice, slowly and not always successfully."""
    time.sleep(0.020 + invoice % 7 * 0.010)
    return invoice % 5 != 0


def do_work(metrics, backlog: list[int]) -> None:
    """Records a run of work through the meters, the way an application would."""
    # Kept rather than looked up per use: asking the registry answers the same meter every time, but
    # a handle costs nothing to hold and a dictionary lookup on a hot path is a dictionary lookup.
    parsed = metrics.counter("invoices.parsed", {"outcome": "ok"})
    rejected = metrics.counter("invoices.parsed", {"outcome": "rejected"})
    duration = metrics.timer("invoice.parse")

    print(f"\nparsing {len(backlog)} invoices, publishing every 2s")

    # Counted here as well, only so that the two numbers can be shown side by side below.
    ok_total = rejected_total = 0

    while backlog:
        invoice = backlog.pop()

        # Recorded however the block is left - the returned-early and the raised-out-of cases
        # included, which is what a timing written at the end of a function misses.
        with duration:
            ok = parse(invoice)

        if ok:
            parsed.increment()
            ok_total += 1
        else:
            rejected.increment()
            rejected_total += 1

    print(f"  parsed   {ok_total} over the whole run")
    print(f"  rejected {rejected_total}")

    # The meters hold less than the run did, and that is the point of a step: the publishing thread
    # took what had accumulated and started them again, so each batch carries its own interval
    # rather than an ever-growing total. It is what makes these rates, and what lets EMO sum them.
    print("\nthe meters now hold what has accumulated since the last publish:")
    print(f"  invoices.parsed{{outcome=ok}} {parsed.count:g} of the {ok_total} above")
    print(f"  invoice.parse               {duration.count} timings")


def read_back(emo, module: str) -> None:
    """Reads back what was just published, which only an administrator may do."""
    print("\nreading the rows back (administrator only)")

    try:
        rows = emo.list_metrics(MetricQuery(name="invoices.parsed", limit=10))
    except EuclidServiceError as error:
        if error.status != 403:
            raise
        print(f"  refused: {error.reason}")
        print("  publishing needs no special rights; reading everybody's numbers does")
        return

    if not rows:
        # EMO writes what it is pushed into bucket-aligned rows on a flush of its own, so a listing
        # a moment after a push can legitimately still be empty.
        print("  nothing yet - EMO flushes on its own period, so give it a moment")
        return

    for row in rows:
        labels = "  ".join(f"{name}={value}" for name, value in sorted(row.labels.items()))
        print(f"  {row.name:<20} {row.value:>10.2f}  {row.type:<6} {row.resolution:<5} "
              f"over {row.samples} sample(s)  {row.timestamp}  {labels}")

    mean = emo.average(MetricQuery(name="invoice.parse.total"))
    print(f"\nmean time to parse one invoice: {mean:.2f} ms")
    print("  (the timer's total is a rate, so a mean over the rows is a mean per step)")
    print(f"\nthe module to look for in a dashboard is {module}")


def main(argv: list[str]) -> int:
    arguments = [a for a in argv[1:] if not a.startswith("--")]
    if len(arguments) < 3:
        print(__doc__)
        return 2
    base_url, username, password = arguments[0], arguments[1], arguments[2]
    module = f"pdk-walkthrough-{int(time.time())}"

    try:
        session = (Euclid.for_server(base_url)
                   .access()
                   .credentials(username, password)
                   # A development server's certificate is usually its own; drop this line, or
                   # point ca_cert_path() at the real CA, anywhere it matters.
                   .verify(False)
                   .login())
    except EuclidAuthenticationError as error:
        print(f"login refused: {error.reason or error}")
        return 1

    with session:
        emo = session.emo()
        print(f"logged in to {session.base_url} as {session.user_id} "
              f"({session.account_id}, {session.region})")
        print(f"reporting as module {module}")

        # What is left of the backlog at any moment, which the gauge below reads for itself rather
        # than being told about.
        backlog = list(range(1, 41))

        # A two-second step so that a walkthrough shows the thread working; a minute is the default,
        # and what an application would leave it at. The with form stops the thread at the end and
        # publishes the step in hand rather than losing it.
        with emo.registry(module, step=2, common_labels={"sdk": "euclid-pdk"}) as metrics:

            # Read at every publish, which is what a gauge is for: nobody has to remember to set it.
            metrics.gauge_from("invoices.backlog", lambda: float(len(backlog)))

            do_work(metrics, backlog)

            # The thread would do this on the next step, and does it once more on the way out; asked
            # for here so that what follows has something to read.
            metrics.publish()
            print(f"\npublished {metrics.publishes} batch(es), {metrics.failed_publishes} failed")

        read_back(emo, module)

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
