"""The arithmetic behind the one cell a reader actually came for.

Thorough out of proportion to the code's size for a different reason than the rollup's. A day
record is wrong permanently; a snapshot is wrong for fifteen minutes. But the snapshot is the thing
a stranger reads during an outage to decide whether the problem is ours, and a projection that says
green while the estate is down is the single worst sentence this repository can produce.

The Prometheus shapes below are matrix results with `values`, as Grafana actually returns them.
"""

from __future__ import annotations

import datetime as dt
import json

import pytest

from bakobo_status.errors import StatusError
from bakobo_status.snapshot import (
    INTERVALS_PER_DAY,
    Now,
    Snapshot,
    probe_now,
)

NOON = dt.datetime(2026, 9, 5, 12, 0, tzinfo=dt.timezone.utc)
MIDNIGHT = dt.datetime(2026, 9, 5, 0, 0, tzinfo=dt.timezone.utc)


def at(hour, minute=0):
    return dt.datetime(2026, 9, 5, hour, minute, tzinfo=dt.timezone.utc).timestamp()


class Series:
    """A client that returns one matrix series built from (timestamp, value) pairs."""

    def __init__(self, *points, fail=False):
        self.points = points
        self.fail = fail
        self.asked = []

    def query_range(self, promql, start, end, step):
        if self.fail:
            raise StatusError("e.env.metrics.unavailable.r", "Down.", "Grafana is unreachable.")
        self.asked.append((start, end, step))
        return [{"metric": {}, "values": [[t, str(v)] for t, v in self.points]}]


def snap(current=1, as_of=NOON, intervals=48, up=48):
    return Snapshot(current=current, as_of=as_of.isoformat(), intervals=intervals, up=up)


# --- the projection ------------------------------------------------------------------------------

def test_a_clean_day_so_far_projects_to_green():
    assert snap(intervals=48, up=48).projected_uptime() == 1.0


def test_the_two_rules_agree_whenever_nothing_is_down():
    """Assume-the-rest-succeeds and assume-the-rest-matches-now are the same statement while the
    current state is up, which is almost always. The rules only diverge during an outage."""
    resolved = snap(current=1, intervals=48, up=46)
    optimistic = (46 + (INTERVALS_PER_DAY - 48)) / INTERVALS_PER_DAY
    assert resolved.projected_uptime() == pytest.approx(optimistic)
    assert resolved.projected_uptime() == pytest.approx(94 / 96)


def test_an_active_outage_goes_red_within_one_interval():
    """The case the rule exists for. Optimistic completion would call this 98.96% -- amber, during
    an outage the reader is experiencing -- and would need five failed intervals to reach red."""
    live = snap(current=0, intervals=48, up=47)
    assert live.projected_uptime() == pytest.approx(47 / 96)
    assert live.projected_uptime() < 0.95


def test_the_projection_is_stable_across_the_day_once_an_outage_ends():
    """A resolved twenty-minute dip reads the same at 00:31 as at 23:59. This is what kills the
    thrash the naive ratio has, where one blip at 00:30 reads 50%."""
    morning = snap(current=1, intervals=2, up=1).projected_uptime()
    evening = snap(current=1, intervals=95, up=94).projected_uptime()
    assert morning == pytest.approx(evening) == pytest.approx(95 / 96)


def test_today_cannot_return_to_green_after_a_failed_interval():
    for measured in range(2, INTERVALS_PER_DAY + 1):
        assert snap(current=1, intervals=measured, up=measured - 1).projected_uptime() < 0.999


def test_an_outage_late_in_the_day_moves_the_cell_very_little():
    """Not a defect, and documented on the page: there is not enough day left for it to matter.
    It is why the banner rather than the strip is what answers "are you up"."""
    late = snap(current=0, intervals=95, up=94)
    assert late.projected_uptime() == pytest.approx(94 / 96)


def test_more_intervals_than_a_day_holds_cannot_project_above_one():
    """A probe frequency raised mid-day would otherwise divide by a denominator it has passed."""
    assert snap(current=1, intervals=200, up=200).projected_uptime() == pytest.approx(200 / 96)
    assert snap(current=1, intervals=96, up=96).projected_uptime() == 1.0


# --- freshness -----------------------------------------------------------------------------------

def test_a_reading_from_this_quarter_hour_is_fresh():
    assert not snap(as_of=NOON).is_stale(NOON + dt.timedelta(minutes=14))


def test_a_reading_older_than_three_intervals_is_stale():
    """Two of the three are ordinary slack -- ingestion lag and a schedule GitHub delays under
    load. Past that, projecting forward asserts the estate is fine from another hour's evidence."""
    assert not snap(as_of=NOON).is_stale(NOON + dt.timedelta(minutes=44))
    assert snap(as_of=NOON).is_stale(NOON + dt.timedelta(minutes=46))


# --- the query -----------------------------------------------------------------------------------

def test_the_snapshot_reads_the_newest_sample_as_the_current_state():
    client = Series((at(11, 30), 1), (at(11, 45), 1), (at(12, 0), 0))
    reading = probe_now(client, "witness-de", NOON)
    assert reading.current == 0
    assert reading.as_of == "2026-09-05T12:00:00+00:00"
    assert (reading.intervals, reading.up) == (3, 2)


def test_the_window_reaches_back_before_midnight_but_the_tally_does_not():
    """Without the overhang every UTC day would open with fifteen minutes of grey -- the calendar
    reported as an outage. The 23:45 sample is good evidence about 00:05, and is not part of today.
    """
    yesterday = dt.datetime(2026, 9, 4, 23, 45, tzinfo=dt.timezone.utc).timestamp()
    client = Series((yesterday, 1))
    reading = probe_now(client, "witness-de", MIDNIGHT + dt.timedelta(minutes=5))

    assert reading.current == 1
    assert reading.intervals == 0
    assert reading.up == 0
    assert reading.projected_uptime() == 1.0

    start, _, _ = client.asked[0]
    assert start == MIDNIGHT - dt.timedelta(minutes=30)


def test_nothing_measured_at_all_is_none_rather_than_down():
    """There is no probe result that means "down" and no probe result at all. Conflating them puts
    a red row on the page for a check that was deleted."""
    assert probe_now(Series(), "witness-de", NOON) is None


def test_duplicate_timestamps_across_series_are_collapsed_not_counted_twice():
    """`max by ()` yields one series, so this is defensive -- but concatenating would inflate
    today's denominator invisibly, and a wrong denominator is a wrong published number."""
    client = Series()
    client.query_range = lambda *a, **k: [
        {"metric": {"probe": "frankfurt"}, "values": [[at(11, 45), "0"]]},
        {"metric": {"probe": "singapore"}, "values": [[at(11, 45), "1"]]},
    ]
    reading = probe_now(client, "witness-de", NOON)
    assert reading.intervals == 1
    assert reading.current == 1  # at least one probe succeeded, which is what uptime means


def test_the_snapshot_query_is_the_rollup_s_own_expression():
    """A second expression here would eventually disagree with the bars beside it about "up"."""
    client = Series((at(12, 0), 1))
    seen = []
    client.query_range = lambda q, *a, **k: seen.append(q) or [
        {"metric": {}, "values": [[at(12, 0), "1"]]}
    ]
    probe_now(client, "witness-de", NOON)
    assert seen == ['max by () (probe_success{job="witness-de"})']


# --- the file ------------------------------------------------------------------------------------

def payload(**components):
    """The shape site.publish emits: readings plus their rendering, in one file."""
    return {
        "taken_at": NOON.isoformat(),
        "stale_after_seconds": 2700,
        "banner": {"state": "operational", "colour": "#0ca30c", "headline": "ok", "evidence": ""},
        "components": components,
    }


def rendered(reading, **extra):
    """One component's entry: the four Snapshot fields, plus presentation the reader never parses."""
    from dataclasses import asdict
    return {**asdict(reading), "state": "operational", "colour": "#0ca30c",
            "glyph": "\u25cf", "label": "Operational", "tip": "...", **extra}


def test_the_snapshot_round_trips(tmp_path):
    now = Now(tmp_path / "now.json")
    now.write(payload(**{"witness-de": rendered(snap()),
                         "bakobo-com": rendered(snap(current=0))}))

    assert now.read()["taken_at"] == NOON.isoformat()
    back = now.snapshots()
    assert back["witness-de"] == snap()
    assert back["bakobo-com"].current == 0


def test_the_presentation_half_of_the_payload_is_ignored_when_reading():
    """One artifact serves the build and the browser. The reader takes the four fields a reading
    has and is indifferent to everything the renderer added beside them."""
    from bakobo_status.snapshot import Now as N
    import json as j, tempfile, pathlib as pl
    path = pl.Path(tempfile.mkdtemp()) / "now.json"
    path.write_text(j.dumps(payload(**{"a": rendered(snap(), extra_field_from_the_future=1)})))
    assert N(path).snapshots()["a"] == snap()


def test_a_component_with_no_reading_is_skipped_rather_than_constructed(tmp_path):
    """publish() emits an entry for every component the page shows, including ones it could not
    measure -- as_of null. A Snapshot with no timestamp cannot say whether it is stale and would
    raise at the moment the page most needs to render."""
    path = tmp_path / "now.json"
    Now(path).write(payload(**{
        "measured": rendered(snap()),
        "unmeasured": {"current": None, "as_of": None, "intervals": 0, "up": 0,
                       "state": "no-data", "colour": "#eee", "glyph": "\u00b7",
                       "label": "No data", "tip": "..."},
    }))
    assert list(Now(path).snapshots()) == ["measured"]


def test_an_absent_snapshot_is_not_an_error(tmp_path):
    """The page must build before the first harvest has run, and during an incident in which the
    harvest is what is broken."""
    now = Now(tmp_path / "never-written.json")
    assert now.read() == {"taken_at": None, "components": {}}
    assert now.snapshots() == {}


def test_a_corrupt_snapshot_is_an_error(tmp_path):
    """Treating a truncated write as "no data" would turn the estate grey with no explanation."""
    path = tmp_path / "now.json"
    path.write_text("{oh no")
    with pytest.raises(StatusError) as caught:
        Now(path).read()
    assert caught.value.code == "e.self.corrupt.history.f"
    assert "not the history" in caught.value.detail


def test_the_snapshot_is_written_exactly_as_composed(tmp_path):
    """write() is deliberately dumb: this file is PUT into KV and served to browsers verbatim, so
    anything it rearranged would be a difference between what was decided and what is read."""
    given = payload(**{"z": rendered(snap()), "a": rendered(snap())})
    path = Now(tmp_path / "now.json").write(given)
    assert json.loads(path.read_text()) == given
    assert path.read_text().endswith("\n")
