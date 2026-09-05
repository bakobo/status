"""Roll a day of probe results into a record git keeps forever (infra this.i @2oxu5757).

Grafana Cloud's free tier retains metrics for 14 days, so the 90-day bars on the status page cannot
be read out of it — not slowly, not with a bigger query, not at all. A day that is not rolled up
before its fourteenth birthday is gone. That is the whole reason this exists, and it is why the job
that runs it wants a dead-man's switch of its own: silence here is indistinguishable from a quiet
month until someone looks at the page in three weeks and finds a hole.

Two numbers per component per day, because they answer different questions and the difference is
the interesting part.

**Uptime** is interval-based: the fraction of time points in which *at least one* probe succeeded.
That is the number the coloured bar shows, and the "at least one" is the point — three probe
locations were chosen so that one probe's own network trouble is not published as an outage.

**Reachability** is execution-based: the fraction of all probe executions that succeeded. When
uptime is 1.0 and reachability is 0.67, the service was up throughout and one region could not
reach it, which is a real thing to know and is invisible in the bar.

Both definitions are Grafana's own rather than ours, so the numbers on the page mean what they mean
in the Synthetics UI beside them.
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import asdict, dataclass
from pathlib import Path

from . import errors
from .errors import StatusError

# The step at which uptime is sampled. Must match the checks' frequency: sampling faster than the
# checks run would count intervals in which no probe was due, and every one of those is a gap that
# would read as downtime.
DEFAULT_STEP_SECONDS = 900


@dataclass(frozen=True)
class DayRecord:
    """One component, one UTC day.

    Deliberately small. It carries what a 90-day bar and its hover need, and nothing else --
    latency percentiles and per-probe detail are not here and cannot be added retroactively, since
    the source data is gone at fourteen days. If those are ever wanted, the decision has to be made
    before the fortnight, not after; that asymmetry is worth stating where someone might otherwise
    assume a richer record could be backfilled.
    """

    uptime: float
    reachability: float
    intervals: int
    executions: int
    failures: int

    @property
    def measured(self) -> bool:
        """Whether anything was actually observed. Callers must not record a day that was not."""
        return self.intervals > 0

    @property
    def state(self) -> str:
        """What the bar is coloured, from the day's uptime.

        Thresholds rather than "any failure is red": a single failed interval in a day is 99.0%
        with a 15-minute period, and colouring that red would train the reader to ignore red. The
        boundaries are a judgement and are meant to be argued with, not derived.

        A day with no intervals is NOT down, and that distinction is the difference between a
        status page and a liar. Uptime is 0.0 both for a day nothing was measured and for a day
        everything failed; only `intervals` tells them apart. The first real backfill got this
        wrong in exactly the predictable way -- it recorded thirteen days from before the checks
        existed, every one a red bar for an outage that never happened.
        """
        if not self.intervals:
            return "no-data"
        if self.uptime >= 0.999:
            return "operational"
        if self.uptime >= 0.95:
            return "degraded"
        return "down"


def uptime_query(job: str) -> str:
    """Grafana's own uptime expression, narrowed to one check.

    `max by ()` collapses the probe dimension, so the sample is 1 when any probe succeeded. Taken
    from Grafana's Uptime and Reachability documentation rather than invented, so the number on our
    page agrees with the number in the Synthetics UI a reader may be looking at beside it.
    """
    return f'max by () (probe_success{{job="{job}"}})'


def reachability_query(job: str, window: str) -> str:
    """Successful executions over all executions, across the window.

    `probe_all_success_*` rather than counting `probe_success` samples, because the agent keeps its
    own running total: an execution that ran but could not report its sample is still counted once
    reporting resumes. During a write-blocking incident that is the difference between recording a
    degraded service and recording a fabricated outage.
    """
    return (
        f'sum(increase(probe_all_success_sum{{job="{job}"}}[{window}])) '
        f'/ sum(increase(probe_all_success_count{{job="{job}"}}[{window}]))'
    )


def executions_query(job: str, window: str) -> str:
    return f'sum(increase(probe_all_success_count{{job="{job}"}}[{window}]))'


def successes_query(job: str, window: str) -> str:
    return f'sum(increase(probe_all_success_sum{{job="{job}"}}[{window}]))'


def day_bounds(day: dt.date) -> tuple[dt.datetime, dt.datetime]:
    """The UTC day, half-open: midnight inclusive to next midnight exclusive.

    Half-open so that consecutive days neither overlap nor leave a seam. A closed interval would
    count the midnight sample in both days, which inflates one and is invisible in the output.
    """
    start = dt.datetime.combine(day, dt.time.min, tzinfo=dt.timezone.utc)
    return start, start + dt.timedelta(days=1)


def _samples(matrix) -> list[float]:
    """Every value from a Prometheus matrix result, as floats."""
    return [float(v) for series in matrix for _, v in series.get("values", [])]


def _scalar(vector, default=0.0) -> float:
    """The single value from a Prometheus vector result, or a default when the result is empty.

    An empty vector means the series does not exist in the window -- a check created midway through
    the day, or a day before the check existed at all. That is legitimately zero rather than an
    error, and distinguishing the two is the caller's job via `intervals`.
    """
    if not vector:
        return default
    value = float(vector[0]["value"][1])
    # A NaN reaches here when a ratio divides by zero, which Prometheus reports rather than
    # refuses. Left as the default, because "no executions" is not "0% reachable".
    return default if value != value else value


def summarize(uptime_matrix, reachability_vector, executions_vector, successes_vector) -> DayRecord:
    """Turn four Prometheus results into one day's record.

    Separated from the fetching so every arithmetic decision here is testable without a network or
    a credential, which matters because these numbers are published and are never recomputable.
    """
    samples = _samples(uptime_matrix)
    intervals = len(samples)
    uptime = (sum(samples) / intervals) if intervals else 0.0

    executions = round(_scalar(executions_vector))
    successes = round(_scalar(successes_vector))
    reachability = _scalar(reachability_vector)

    return DayRecord(
        uptime=round(uptime, 6),
        reachability=round(reachability, 6),
        intervals=intervals,
        executions=executions,
        # From the difference rather than from a fifth query. One query fewer, and the two numbers
        # cannot disagree about the same day.
        failures=max(executions - successes, 0),
    )


class History:
    """The per-component history files, which are the durable record.

    One file per component rather than one file for everything: a day's rollup then touches seven
    small files instead of rewriting one large one, so a review shows which components changed, and
    two components' histories cannot be lost to one bad write.
    """

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def path_for(self, component: str) -> Path:
        if "/" in component or component in ("", ".", ".."):
            raise StatusError(
                errors.UNKNOWN_COMPONENT,
                "That is not a component id.",
                f"'{component}' cannot be a filename. Component ids come from the monitoring "
                "root's `components` output and are plain slugs.",
            )
        return self.root / f"{component}.json"

    def read(self, component: str) -> dict:
        path = self.path_for(component)
        if not path.is_file():
            return {"component": component, "days": {}}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise StatusError(
                errors.CORRUPT_HISTORY,
                "That history file cannot be read.",
                f"{path} is not valid JSON: {exc}. It is generated and committed nightly, so this "
                "is a bad merge or a partial write rather than a hand edit — and the days in it "
                "cannot be recomputed, so restore it from git rather than starting a new one.",
            ) from exc
        return data

    def record(self, component: str, day: dt.date, entry: DayRecord) -> Path:
        """Write one day, leaving every other day untouched.

        Idempotent: re-running a date overwrites that date only, which is what makes a failed job
        safe to simply run again. Keys are sorted and the file is indented, so adding a day is a
        one-hunk diff a reviewer can actually read.
        """
        data = self.read(component)
        data.setdefault("days", {})[day.isoformat()] = asdict(entry)
        data["days"] = dict(sorted(data["days"].items()))
        data["component"] = component

        path = self.path_for(component)
        self.root.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2, sort_keys=False) + "\n", encoding="utf-8")
        return path

    def missing_days(self, component: str, through: dt.date, span: int = 14) -> list[dt.date]:
        """Days inside the retention window that have no record yet.

        The retention window is the deadline, so this is the only gap worth reporting: a day older
        than the window is unrecoverable and listing it would suggest otherwise. Used by the
        nightly job to backfill after an outage of its own, which is the failure this whole file
        exists to survive.
        """
        have = set(self.read(component).get("days", {}))
        return [
            d
            for d in (through - dt.timedelta(days=n) for n in range(span))
            if d.isoformat() not in have
        ]


def roll_day(client, job: str, day: dt.date, step: int = DEFAULT_STEP_SECONDS) -> DayRecord:
    """Four queries against one component's day, summarized into one record.

    The window given to `increase()` is the day itself rather than a fixed `[1d]`, so a partial day
    -- the one the job runs on, or a check created this morning -- reports what actually happened
    rather than dividing by a period nothing was measured in.
    """
    start, end = day_bounds(day)
    window = f"{int((end - start).total_seconds())}s"
    return summarize(
        client.query_range(uptime_query(job), start, end, step),
        client.query(reachability_query(job, window), end),
        client.query(executions_query(job, window), end),
        client.query(successes_query(job, window), end),
    )
