"""What the page must never say.

Most of these are not "does it render" — they are assertions about claims. A status page's failure
mode is not a crash, it is confidently telling a stranger something untrue: that a day nobody
measured was an outage, that everything is fine according to data from yesterday, or that a
component exists which nothing is watching.
"""

from __future__ import annotations

import datetime as dt
import json

import pytest

from bakobo_status import site
from bakobo_status.incidents import Store

TODAY = dt.date(2026, 9, 5)
BUILT = dt.datetime(2026, 9, 5, 22, 30, tzinfo=dt.timezone.utc)
COMPONENTS = {
    "witness-de": {"display": "DE witness — de.wit.bakobo.com", "kind": "witness"},
    "bakobo-com": {"display": "bakobo.com", "kind": "web"},
}


def day(uptime=1.0, reachability=1.0, intervals=96, executions=288, failures=0):
    return {
        "uptime": uptime, "reachability": reachability, "intervals": intervals,
        "executions": executions, "failures": failures,
    }


@pytest.fixture()
def estate(tmp_path):
    (tmp_path / "history").mkdir()
    (tmp_path / "incidents").mkdir()
    (tmp_path / "history" / "witness-de.json").write_text(json.dumps({
        "component": "witness-de",
        "days": {"2026-09-05": day(), "2026-09-04": day(uptime=0.5, failures=144)},
    }))
    return tmp_path


# --- the claims ---------------------------------------------------------------------------------

def test_a_day_with_no_record_is_not_an_outage():
    """The pixel version of the bug the backfill shipped. A gap must not be coloured as down."""
    assert site.day_state(None) == "no-data"
    assert site.day_state(day(uptime=0.0, intervals=0, executions=0)) == "no-data"
    assert site.day_state(day(uptime=0.0, intervals=96)) == "down"


@pytest.mark.parametrize(
    ("uptime", "expected"),
    [(1.0, "operational"), (0.999, "operational"), (0.99, "degraded"), (0.5, "down")],
)
def test_bar_states_match_the_stored_thresholds(uptime, expected):
    assert site.day_state(day(uptime=uptime)) == expected


def test_the_window_keeps_gaps_as_gaps():
    """A strip that closed up its gaps would compress a week of missing data into nothing and
    read as continuous coverage."""
    rows = site.window({"2026-09-05": day()}, TODAY, span=5)
    assert len(rows) == 5
    assert [d.isoformat() for d, _ in rows][0] == "2026-09-01"
    assert rows[-1][0] == TODAY
    assert [r for _, r in rows].count(None) == 4


def test_the_banner_comes_from_open_incidents_not_from_uptime(estate):
    """The newest recorded day may be up to a day old. A green banner sourced from it would be a
    confident claim about the present made from yesterday's evidence."""
    state, headline = site.banner([])
    assert (state, headline) == ("operational", "All systems operational")


def test_the_banner_takes_the_worst_open_severity(estate):
    store = Store(estate / "incidents")
    store.open(title="Slow", severity="minor", components=["witness-de"], message="m",
               known_components=["witness-de"], now=dt.datetime(2026, 9, 5, 1,
                                                                tzinfo=dt.timezone.utc))
    store.open(title="Gone", severity="critical", components=["witness-de"], message="m",
               known_components=["witness-de"], now=dt.datetime(2026, 9, 5, 2,
                                                                tzinfo=dt.timezone.utc))
    state, headline = site.banner(store.all())
    assert state == "down"
    assert headline == "2 open incidents"


def test_one_open_incident_is_singular(estate):
    store = Store(estate / "incidents")
    store.open(title="Slow", severity="minor", components=["witness-de"], message="m",
               known_components=["witness-de"], now=BUILT)
    assert site.banner(store.all())[1] == "1 open incident"


# --- rendering ----------------------------------------------------------------------------------

def test_the_page_is_self_contained(estate):
    """It is read when other things are broken, so every external request is a way for it to be
    broken too."""
    page = site.render(COMPONENTS, estate / "history", estate / "incidents", BUILT)
    assert "<script" not in page
    assert "<link" not in page          # no stylesheet, no font, no preconnect
    assert "src=" not in page           # no image, no iframe, no embed
    assert "@import" not in page
    assert "http://" not in page

    # The only external URL is the security.txt link, and it is a navigation the reader chooses
    # rather than a resource the page fetches. Nothing has to resolve for the page to render.
    assert page.count("https://") == 1
    assert "https://bakobo.com/.well-known/security.txt" in page


def test_every_component_appears_with_a_text_state(estate):
    """Colour never carries meaning alone -- the house standard's mitigation for the amber step,
    which is below 3:1 on any light surface and cannot be re-stepped."""
    page = site.render(COMPONENTS, estate / "history", estate / "incidents", BUILT)
    for meta in COMPONENTS.values():
        assert meta["display"] in page
    assert "Operational" in page
    assert "No data" in page


def test_a_component_with_no_history_still_renders(estate):
    """bakobo-com has no history file here. A missing file must not omit the component, or the
    page would silently stop mentioning something it is supposed to be watching."""
    page = site.render(COMPONENTS, estate / "history", estate / "incidents", BUILT)
    assert "bakobo.com" in page
    assert "not yet measured" in page


def test_the_page_says_it_is_a_snapshot(estate):
    page = site.render(COMPONENTS, estate / "history", estate / "incidents", BUILT)
    assert "snapshot, not a live view" in page
    assert "2026-09-05 22:30 UTC" in page


def test_the_security_address_is_advertised(estate):
    page = site.render(COMPONENTS, estate / "history", estate / "incidents", BUILT)
    assert "mailto:security@bakobo.com" in page
    assert "/.well-known/security.txt" in page


def test_incident_text_is_escaped(estate):
    """Incident prose is written by a human under time pressure and is published verbatim."""
    store = Store(estate / "incidents")
    store.open(title="<script>alert(1)</script>", severity="major", components=["witness-de"],
               message="a & b <b>bold</b>", known_components=["witness-de"], now=BUILT)
    page = site.render(COMPONENTS, estate / "history", estate / "incidents", BUILT)
    assert "<script>alert(1)</script>" not in page
    assert "&lt;script&gt;" in page
    assert "a &amp; b" in page


def test_resolved_incidents_appear_under_recent_not_current(estate):
    store = Store(estate / "incidents")
    inc = store.open(title="Gone", severity="major", components=["witness-de"], message="m",
                     known_components=["witness-de"], now=BUILT)
    store.append(inc.id, state="resolved", message="Fixed.",
                 now=BUILT + dt.timedelta(hours=1))
    page = site.render(COMPONENTS, estate / "history", estate / "incidents", BUILT)
    assert "No open incidents." in page
    assert "All systems operational" in page
    assert "Fixed." in page


def test_the_numbers_table_is_present_for_a_measured_component(estate):
    page = site.render(COMPONENTS, estate / "history", estate / "incidents", BUILT)
    assert "<table>" in page
    assert "Reachability" in page
    assert "100.00%" in page


def test_a_tooltip_explains_an_empty_day_rather_than_leaving_it_bare(estate):
    """A grey bar with no explanation is read as an outage by anyone not told otherwise."""
    page = site.render(COMPONENTS, estate / "history", estate / "incidents", BUILT)
    assert "No data — nothing was being measured on this day." in page


def test_history_for_an_absent_file_is_empty(tmp_path):
    assert site.load_history(tmp_path, "nope") == {}
