"""What is true right now, which is a different kind of claim from what the history holds.

Kept in its own module rather than folded into :mod:`bakobo_status.rollup` because the two files
hold opposite promises and the difference is easy to lose. A day record is permanent and can never
be recomputed once Grafana forgets the metrics at fourteen days. A snapshot is the opposite: it is
overwritten every quarter hour, nothing downstream may depend on yesterday's copy, and losing every
one of them costs nothing. Writing a snapshot into `history/` would eventually get one of those
promises applied to the wrong file.

**Why this exists at all.** The strip on the page is a history, and its newest cell was grey every
day of the page's life, because the rollup only captures completed UTC days. That grey cell is the
one a reader actually came for -- "are you up now" is the question, and "here is last Tuesday" is
the consolation prize. The measurement was never missing; the probes run every fifteen minutes from
three locations. It was only ever being harvested once a day.

**Projection: today is scored as if the rest of the day looks like right now.** Not as if the rest
of the day succeeds, which was the first proposal and is wrong in the case that matters. With an
outage active, optimistic completion would take five failed intervals -- seventy-five minutes -- to
colour the day red, while a reader watched a page that said amber during an outage they were
experiencing. Assuming the current state persists puts the cell where it belongs within one
interval, and the two rules are identical whenever nothing is down, which is almost always.

Two properties fall out of that and are worth stating because they look like bugs otherwise.

Today can never return to green once an interval has failed, but it can improve from red to amber
when an outage ends -- red while it is happening, amber once it is over, and that is the day's true
final colour arriving early rather than a cell that cannot make up its mind.

The cell's ability to shout decays through the day. An outage at 00:15 projects to 0% and is
violently red; the same outage at 23:50 projects to 97.9% and is amber, because there is not enough
day left for it to matter. So the strip is not the thing a reader should rely on to learn the
estate is down -- the banner is, and it is sourced from the same snapshot for exactly this reason.
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import asdict, dataclass
from pathlib import Path

from . import errors
from .errors import StatusError
from .rollup import DEFAULT_STEP_SECONDS, day_bounds, uptime_query

# How many intervals a full day holds, from the step the checks actually run at. Derived rather
# than written as 96, so a change to the probe frequency moves both the sampling and the
# denominator together; a projection dividing by a hardcoded 96 after a move to ten-minute checks
# would understate every partial day by a third and look plausible doing it.
INTERVALS_PER_DAY = 86400 // DEFAULT_STEP_SECONDS

# How old the newest interval may be before the snapshot stops being evidence about *now*.
#
# Three intervals rather than one or two. A probe runs every fifteen minutes, Grafana's ingestion is
# not instantaneous, and the harvest job itself runs on a schedule GitHub delays under load -- so
# two of those three are ordinary slack rather than generosity. Past this, forward-projection would
# be asserting the estate is fine on the strength of a measurement from another hour, which is the
# stale-confident-green failure the page is built to refuse.
STALE_AFTER_INTERVALS = 3


@dataclass(frozen=True)
class Snapshot:
    """One component: what it is doing right now, and what today has held so far.

    `current` is the newest interval alone, deliberately, and a single bad interval is enough to
    call it down. The uptime expression collapses the probe dimension with `max by ()`, so one
    failed interval means all three probe locations failed in the same quarter hour -- corroborated
    across continents, which is a great deal more than a flap. Requiring two consecutive would buy
    a quieter cell at the price of fifteen minutes of silence during a real outage.
    """

    current: int
    as_of: str
    intervals: int
    up: int

    def age(self, now: dt.datetime) -> dt.timedelta:
        return now - dt.datetime.fromisoformat(self.as_of)

    def is_stale(self, now: dt.datetime, step: int = DEFAULT_STEP_SECONDS) -> bool:
        return self.age(now).total_seconds() > STALE_AFTER_INTERVALS * step

    def projected_uptime(self, expected: int = INTERVALS_PER_DAY) -> float:
        """Today's uptime if every interval still to come matches the one just measured.

        Clamped at zero remaining, because a probe frequency raised mid-day would otherwise measure
        more intervals than the denominator expects and project above 100%.
        """
        remaining = max(expected - self.intervals, 0)
        return (self.up + remaining * self.current) / expected


def probe_now(client, job: str, now: dt.datetime, step: int = DEFAULT_STEP_SECONDS):
    """One range query over today so far, which answers both questions at once.

    The same expression the rollup samples a finished day with, over midnight-to-now instead. Its
    last point is the current state and its timestamp is how fresh that claim is; the whole series
    is today's tally. Reusing `uptime_query` is not tidiness -- a second expression here would
    eventually disagree with the bars beside it about what "up" means.

    The window reaches back two intervals BEFORE midnight, and the two halves of the result are
    used differently: the newest sample anywhere in it is the current state, while only samples
    from midnight onward count toward today's tally. Without that overhang, every UTC day would
    open with fifteen minutes in which nothing had been measured yet, the projection had nothing to
    project from, and the whole page went grey nightly for a quarter of an hour -- reporting the
    calendar as an outage. The 23:45 sample is perfectly good evidence about 00:05.

    Returns None only when the window holds nothing at all: a check created this morning, or a
    harvest running against a stack that has stopped ingesting.
    """
    start, _ = day_bounds(now.date())
    series = client.query_range(uptime_query(job), start - dt.timedelta(seconds=2 * step), now, step)

    # Collapsed by timestamp rather than concatenated. `max by ()` yields one series, so this is
    # defensive -- but if a labelled series ever slipped through, concatenating would count each
    # interval more than once and inflate today's denominator invisibly.
    by_time: dict[float, float] = {}
    for one in series:
        for at, value in one.get("values", []):
            at = float(at)
            by_time[at] = max(by_time.get(at, 0.0), float(value))

    if not by_time:
        return None

    newest = max(by_time)
    midnight = start.timestamp()
    today = [value for at, value in by_time.items() if at >= midnight]
    return Snapshot(
        current=1 if by_time[newest] > 0 else 0,
        as_of=dt.datetime.fromtimestamp(newest, dt.timezone.utc).isoformat(),
        intervals=len(today),
        up=sum(1 for value in today if value > 0),
    )


class Now:
    """The rolling snapshot file: every component's current state, as of one moment.

    One file for the whole estate, where the history is one file per component. The histories are
    split so that a day's rollup is a readable diff and one bad write cannot take two components
    down with it; neither argument applies to a file that is replaced wholesale every fifteen
    minutes and whose diffs nobody reads.
    """

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def write(self, taken_at: dt.datetime, components: dict) -> Path:
        payload = {
            "taken_at": taken_at.isoformat(),
            "components": {k: asdict(v) for k, v in sorted(components.items())},
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        return self.path

    def read(self) -> dict:
        """The snapshot, or an empty one when there is no file.

        An absent file is not an error: the page has to build before the first harvest has ever
        run, and it must build during an incident when the harvest may be exactly what is broken.
        A corrupt file IS an error, because silently treating a truncated write as "no data" would
        turn the whole estate grey with no explanation anywhere.
        """
        if not self.path.is_file():
            return {"taken_at": None, "components": {}}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise StatusError(
                errors.CORRUPT_HISTORY,
                "The current-status snapshot cannot be read.",
                f"{self.path} is not valid JSON: {exc}. It is rewritten every quarter hour, so "
                "this is a partial write rather than a hand edit — delete it and the next harvest "
                "will replace it. Nothing durable is lost; it is not the history.",
            ) from exc
        return data

    def snapshots(self) -> dict:
        return {k: Snapshot(**v) for k, v in self.read().get("components", {}).items()}
