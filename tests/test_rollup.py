"""The arithmetic behind numbers that get published and can never be recomputed.

That is the whole reason this file is thorough out of proportion to the code's size. A wrong
incident can be corrected in the next update; a wrong uptime figure is wrong permanently, because
the metrics it was derived from are gone at fourteen days. There is no "recompute from source"
here, so the source of truth for these sums is this test file.

Every Prometheus shape asserted below is the shape Grafana actually returns — matrix results with
`values`, vector results with `value` — rather than a convenient invention.
"""

from __future__ import annotations

import datetime as dt
import json

import pytest

from bakobo_status import errors
from bakobo_status.errors import StatusError
from bakobo_status.rollup import (
    DayRecord,
    History,
    day_bounds,
    executions_query,
    reachability_query,
    roll_day,
    successes_query,
    summarize,
    uptime_query,
)

DAY = dt.date(2026, 9, 5)


def matrix(*values):
    return [{"metric": {}, "values": [[0, str(v)] for v in values]}]


def vector(value):
    return [{"metric": {}, "value": [0, str(value)]}]


# --- the queries are Grafana's, not ours -----------------------------------------------------

def test_uptime_collapses_the_probe_dimension():
    """`max by ()` is what makes an interval count as up when ANY probe succeeded."""
    q = uptime_query("witness-de")
    assert q == 'max by () (probe_success{job="witness-de"})'


def test_reachability_uses_the_agent_s_own_counters():
    """probe_all_success_* survives a write-blocking incident; counting samples would not."""
    q = reachability_query("witness-de", "86400s")
    assert "probe_all_success_sum" in q and "probe_all_success_count" in q
    assert "86400s" in q


def test_execution_and_success_queries_name_the_right_counters():
    assert "probe_all_success_count" in executions_query("schema", "86400s")
    assert "probe_all_success_sum" in successes_query("schema", "86400s")


def test_the_day_is_half_open_utc():
    start, end = day_bounds(DAY)
    assert start == dt.datetime(2026, 9, 5, tzinfo=dt.timezone.utc)
    assert end == dt.datetime(2026, 9, 6, tzinfo=dt.timezone.utc)
    assert (end - start) == dt.timedelta(days=1)


# --- summarizing -------------------------------------------------------------------------------

def test_a_perfect_day():
    record = summarize(matrix(1, 1, 1, 1), vector(1.0), vector(288), vector(288))
    assert record.uptime == 1.0
    assert record.reachability == 1.0
    assert record.intervals == 4
    assert record.executions == 288
    assert record.failures == 0
    assert record.state == "operational"


def test_uptime_is_the_mean_of_the_interval_samples():
    record = summarize(matrix(1, 1, 0, 1), vector(0.75), vector(4), vector(3))
    assert record.uptime == 0.75
    assert record.failures == 1


def test_uptime_and_reachability_diverge_when_one_region_fails():
    """The case the two numbers exist to separate: up throughout, unreachable from one probe."""
    record = summarize(matrix(1, 1, 1), vector(0.6667), vector(9), vector(6))
    assert record.uptime == 1.0
    assert record.state == "operational"
    assert record.reachability == pytest.approx(0.6667)
    assert record.failures == 3


def test_a_day_with_no_data_is_zero_intervals_rather_than_a_crash():
    """A day before the check existed. Legitimately empty, and the caller tells it apart by
    `intervals` rather than by uptime, which is 0.0 either way."""
    record = summarize([], [], [], [])
    assert record.intervals == 0
    assert record.uptime == 0.0
    assert record.executions == 0
    assert record.failures == 0


def test_a_nan_ratio_becomes_zero_rather_than_nan():
    """Prometheus reports 0/0 as NaN. NaN would serialize to invalid JSON and poison the file."""
    record = summarize(matrix(1), vector("NaN"), vector(0), vector(0))
    assert record.reachability == 0.0
    assert json.dumps({"r": record.reachability})


def test_failures_never_go_negative():
    """Counters are estimates under `increase()`; successes can exceed executions by a hair."""
    record = summarize(matrix(1), vector(1.0), vector(10), vector(11))
    assert record.failures == 0


@pytest.mark.parametrize(
    ("uptime", "expected"),
    [(1.0, "operational"), (0.9995, "operational"), (0.998, "degraded"),
     (0.95, "degraded"), (0.9499, "down"), (0.0, "down")],
)
def test_the_bar_colour_thresholds(uptime, expected):
    """A single failed interval in a day is 99.0% at a 15-minute period. Colouring that red would
    train the reader to ignore red, which is why the boundary is not 'any failure'."""
    assert DayRecord(uptime, 1.0, 96, 288, 0).state == expected


# --- roll_day wiring ---------------------------------------------------------------------------

class FakeClient:
    def __init__(self, matrix_result, vectors):
        self.matrix_result, self.vectors, self.calls = matrix_result, list(vectors), []

    def query_range(self, promql, start, end, step):
        self.calls.append(("range", promql, start, end, step))
        return self.matrix_result

    def query(self, promql, at):
        self.calls.append(("instant", promql, at))
        return self.vectors.pop(0)


def test_roll_day_asks_four_questions_about_one_day():
    client = FakeClient(matrix(1, 1), [vector(1.0), vector(2), vector(2)])
    record = roll_day(client, "witness-de", DAY, step=900)
    assert record.uptime == 1.0

    kinds = [c[0] for c in client.calls]
    assert kinds == ["range", "instant", "instant", "instant"]

    _, _, start, end, step = client.calls[0]
    assert (start, end) == day_bounds(DAY)
    assert step == 900


def test_the_increase_window_is_the_day_itself_not_a_fixed_1d():
    """A partial day must divide by what was measured, not by a period nothing ran in."""
    client = FakeClient(matrix(1), [vector(1.0), vector(1), vector(1)])
    roll_day(client, "schema", DAY)
    assert "[86400s]" in client.calls[1][1]


# --- the durable files -------------------------------------------------------------------------

def test_a_missing_history_reads_as_empty_rather_than_failing(tmp_path):
    assert History(tmp_path).read("witness-de") == {"component": "witness-de", "days": {}}


def test_recording_a_day_writes_a_readable_sorted_file(tmp_path):
    history = History(tmp_path)
    history.record("witness-de", dt.date(2026, 9, 6), DayRecord(1.0, 1.0, 96, 288, 0))
    history.record("witness-de", DAY, DayRecord(0.5, 0.5, 96, 288, 144))

    data = json.loads((tmp_path / "witness-de.json").read_text())
    assert list(data["days"]) == ["2026-09-05", "2026-09-06"]
    assert data["component"] == "witness-de"
    assert data["days"]["2026-09-05"]["failures"] == 144


def test_rerunning_a_date_overwrites_only_that_date(tmp_path):
    """What makes a failed job safe to simply run again."""
    history = History(tmp_path)
    history.record("schema", DAY, DayRecord(0.5, 0.5, 96, 288, 144))
    history.record("schema", dt.date(2026, 9, 6), DayRecord(1.0, 1.0, 96, 288, 0))
    history.record("schema", DAY, DayRecord(1.0, 1.0, 96, 288, 0))

    days = json.loads((tmp_path / "schema.json").read_text())["days"]
    assert days["2026-09-05"]["uptime"] == 1.0
    assert days["2026-09-06"]["uptime"] == 1.0


def test_a_corrupt_history_is_refused_rather_than_replaced(tmp_path):
    """Refused because the days in it cannot be recomputed. Starting a fresh file would silently
    discard history whose source data has expired."""
    (tmp_path / "schema.json").write_text("{not json")
    with pytest.raises(StatusError) as caught:
        History(tmp_path).read("schema")
    assert caught.value.code == errors.CORRUPT_HISTORY
    assert "restore it from git" in caught.value.detail


@pytest.mark.parametrize("bad", ["../etc/passwd", "a/b", "", ".", ".."])
def test_a_component_id_cannot_escape_the_history_directory(tmp_path, bad):
    with pytest.raises(StatusError) as caught:
        History(tmp_path).path_for(bad)
    assert caught.value.code == errors.UNKNOWN_COMPONENT


def test_missing_days_reports_only_what_is_still_recoverable(tmp_path):
    """Days past the retention window are gone, so listing them would imply they could be filled."""
    history = History(tmp_path)
    history.record("schema", DAY, DayRecord(1.0, 1.0, 96, 288, 0))
    missing = history.missing_days("schema", through=DAY, span=3)
    assert missing == [dt.date(2026, 9, 4), dt.date(2026, 9, 3)]


def test_missing_days_on_an_untouched_component_is_the_whole_window(tmp_path):
    assert len(History(tmp_path).missing_days("new-thing", through=DAY, span=14)) == 14
