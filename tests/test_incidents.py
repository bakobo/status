"""The incident record's invariants.

The tests worth having here are the ones about the FORMAT surviving contact with a human, because
that is the claim the whole design rests on: an incident is a file, and a file is only a good
record if it can be hand-edited during an outage and still read back. So the round-trip is asserted
in both directions, and every way a file can be malformed is asserted to fail loudly rather than to
be guessed at.
"""

from __future__ import annotations

import datetime as dt

import pytest

from bakobo_status import errors
from bakobo_status.errors import StatusError
from bakobo_status.incidents import (
    Incident,
    Store,
    Update,
    new_id,
    parse,
    render,
    slugify,
    stamp,
    utcnow,
    validate_components,
    validate_id,
    validate_message,
    validate_severity,
    validate_state,
)

T0 = dt.datetime(2026, 9, 4, 18, 22, 11, tzinfo=dt.timezone.utc)
T1 = dt.datetime(2026, 9, 4, 19, 5, 0, tzinfo=dt.timezone.utc)
KNOWN = ("witness-de", "witness-ca", "bakobo-com")


def an_incident(**over) -> Incident:
    base = dict(
        id="2026-09-04-de-witness-unreachable",
        title="DE witness unreachable",
        severity="major",
        components=("witness-de",),
        updates=(Update(at=T0, state="investigating", message="No OOBI response from any probe."),),
    )
    return Incident(**{**base, **over})


# --- the format survives a round trip ------------------------------------------------------

def test_a_rendered_incident_parses_back_to_itself():
    incident = an_incident(
        updates=(
            Update(at=T0, state="investigating", message="No OOBI response from any probe."),
            Update(at=T1, state="resolved", message="The host was restarted.\n\nTwo paragraphs."),
        )
    )
    assert parse(render(incident)) == incident


def test_a_multi_paragraph_message_keeps_its_blank_lines():
    incident = an_incident(
        updates=(Update(at=T0, state="investigating", message="First.\n\nSecond."),)
    )
    assert parse(render(incident)).updates[0].message == "First.\n\nSecond."


def test_a_hand_added_update_is_read_without_the_tool_having_written_it():
    """The situation the format exists for: the tooling is what is broken, so somebody types."""
    text = render(an_incident()) + "\n## 2026-09-04T19:05:00Z resolved\n\nRestarted by hand.\n"
    incident = parse(text)
    assert incident.is_resolved
    assert incident.updates[-1].message == "Restarted by hand."


# --- derived facts -------------------------------------------------------------------------

def test_state_and_opened_come_from_the_updates_not_from_a_header():
    incident = an_incident(
        updates=(
            Update(at=T0, state="investigating", message="a"),
            Update(at=T1, state="monitoring", message="b"),
        )
    )
    assert incident.state == "monitoring"
    assert incident.opened_at == T0
    assert not incident.is_resolved
    assert incident.resolved_at is None


def test_an_open_incident_has_no_duration():
    """A duration that grew on every rebuild would make every rebuild a diff."""
    assert an_incident().duration is None


def test_a_resolved_incident_measures_from_first_update_to_last():
    incident = an_incident(
        updates=(
            Update(at=T0, state="investigating", message="a"),
            Update(at=T1, state="resolved", message="b"),
        )
    )
    assert incident.duration == T1 - T0
    assert incident.resolved_at == T1


# --- what the validators refuse --------------------------------------------------------------

def test_an_unknown_severity_is_refused():
    with pytest.raises(StatusError) as caught:
        validate_severity("catastrophic")
    assert caught.value.code == errors.UNKNOWN_SEVERITY


def test_an_unknown_state_is_refused():
    with pytest.raises(StatusError) as caught:
        validate_state("fixed")
    assert caught.value.code == errors.UNKNOWN_STATE


def test_every_declared_severity_and_state_is_accepted():
    for severity in ("maintenance", "minor", "major", "critical"):
        assert validate_severity(severity) == severity
    for state in ("investigating", "identified", "monitoring", "resolved"):
        assert validate_state(state) == state


def test_an_empty_message_is_refused():
    with pytest.raises(StatusError) as caught:
        validate_message("   \n ")
    assert caught.value.code == errors.EMPTY_MESSAGE


def test_a_message_is_stripped_but_kept():
    assert validate_message("  something happened\n") == "something happened"


def test_an_unknown_component_is_refused_with_the_known_ones_named():
    with pytest.raises(StatusError) as caught:
        validate_components(["witness-dr"], KNOWN)
    assert caught.value.code == errors.UNKNOWN_COMPONENT
    assert "witness-de" in caught.value.detail


def test_naming_no_component_at_all_is_refused():
    with pytest.raises(StatusError) as caught:
        validate_components(["  ", ""], KNOWN)
    assert caught.value.code == errors.UNKNOWN_COMPONENT


def test_components_are_deduplicated_and_keep_their_order():
    assert validate_components(["witness-de", "witness-ca", "witness-de"], KNOWN) == (
        "witness-de",
        "witness-ca",
    )


def test_a_malformed_identifier_is_refused():
    for bad in ("nope", "2026-9-4-x", "2026-09-04-", "2026-09-04-Caps"):
        with pytest.raises(StatusError) as caught:
            validate_id(bad)
        assert caught.value.code == errors.MALFORMED_ID


def test_a_title_with_no_letters_or_digits_cannot_make_an_identifier():
    with pytest.raises(StatusError) as caught:
        slugify("—  ??  —")
    assert caught.value.code == errors.EMPTY_TITLE


def test_an_identifier_is_the_date_then_a_readable_slug():
    assert new_id(T0, "DE witness unreachable!") == "2026-09-04-de-witness-unreachable"


def test_stamp_normalises_to_utc():
    elsewhere = T0.astimezone(dt.timezone(dt.timedelta(hours=9)))
    assert stamp(elsewhere) == "2026-09-04T18:22:11Z"


def test_the_clock_is_utc_and_carries_no_microseconds():
    now = utcnow()
    assert now.tzinfo == dt.timezone.utc
    assert now.microsecond == 0


# --- a file this cannot understand is an error, never a guess --------------------------------

@pytest.mark.parametrize(
    "text",
    [
        "no fence at all\n",
        "---\nid: 2026-09-04-x\nnot a header line\n---\n\n## 2026-09-04T18:22:11Z investigating\n\nx\n",
        "---\nid: 2026-09-04-x\ntitle: T\n",
        "---\nid: 2026-09-04-x\ntitle: T\nseverity: major\ncomponents: witness-de\n---\n",
    ],
    ids=["no-fence", "bad-header-line", "unterminated-header", "no-updates"],
)
def test_a_malformed_file_is_refused(text):
    with pytest.raises(StatusError) as caught:
        parse(text)
    assert caught.value.code == errors.CORRUPT_INCIDENT


def test_a_missing_header_field_is_named_in_the_error():
    text = "---\nid: 2026-09-04-x\ntitle: T\nseverity: major\ncomponents: \n---\n\n## 2026-09-04T18:22:11Z investigating\n\nx\n"
    with pytest.raises(StatusError) as caught:
        parse(text)
    assert "components" in caught.value.detail


def test_an_update_timestamp_that_is_not_a_utc_instant_is_refused():
    text = (
        "---\nid: 2026-09-04-x\ntitle: T\nseverity: major\ncomponents: witness-de\n---\n"
        "\n## yesterday investigating\n\nx\n"
    )
    with pytest.raises(StatusError) as caught:
        parse(text)
    assert caught.value.code == errors.CORRUPT_INCIDENT


def test_an_update_with_an_invented_state_is_refused():
    text = (
        "---\nid: 2026-09-04-x\ntitle: T\nseverity: major\ncomponents: witness-de\n---\n"
        "\n## 2026-09-04T18:22:11Z fixed\n\nx\n"
    )
    with pytest.raises(StatusError) as caught:
        parse(text)
    assert caught.value.code == errors.UNKNOWN_STATE


def test_a_source_name_is_carried_into_the_error_so_a_reader_knows_which_file():
    with pytest.raises(StatusError) as caught:
        parse("garbage\n", source="incidents/2026-09-04-x.md")
    assert "incidents/2026-09-04-x.md" in caught.value.detail


def test_an_error_points_at_its_catalog_entry():
    err = StatusError("e.state.missing.incident.f", "Title.", "Detail.")
    assert err.type_url == "https://errors.bakobo.com/e.state.missing.incident.f"
    assert str(err) == "Detail."


# --- the store -------------------------------------------------------------------------------

def test_opening_writes_a_file_that_reads_back(tmp_path):
    store = Store(tmp_path)
    incident = store.open(
        title="DE witness unreachable",
        severity="major",
        components=["witness-de"],
        message="No OOBI response.",
        known_components=KNOWN,
        now=T0,
    )
    assert store.path_for(incident.id).is_file()
    assert store.read(incident.id) == incident
    assert incident.state == "investigating"


def test_a_second_incident_with_the_same_title_on_the_same_day_is_refused(tmp_path):
    store = Store(tmp_path)
    args = dict(
        title="DE witness unreachable",
        severity="major",
        components=["witness-de"],
        message="m",
        known_components=KNOWN,
        now=T0,
    )
    store.open(**args)
    with pytest.raises(StatusError) as caught:
        store.open(**args)
    assert caught.value.code == errors.ID_TAKEN


def test_reading_an_incident_that_is_not_there_says_so(tmp_path):
    with pytest.raises(StatusError) as caught:
        Store(tmp_path).read("2026-09-04-nothing")
    assert caught.value.code == errors.NO_SUCH_INCIDENT


def test_appending_an_update_keeps_the_earlier_ones(tmp_path):
    store = Store(tmp_path)
    incident = store.open(
        title="DE witness unreachable",
        severity="major",
        components=["witness-de"],
        message="No OOBI response.",
        known_components=KNOWN,
        now=T0,
    )
    updated = store.append(incident.id, state="identified", message="Disk full.", now=T1)
    assert len(updated.updates) == 2
    assert updated.updates[0].message == "No OOBI response."
    assert updated.state == "identified"


def test_a_resolved_incident_cannot_be_reopened(tmp_path):
    """A resolution was published. Amending it rewrites something people have already read."""
    store = Store(tmp_path)
    incident = store.open(
        title="DE witness unreachable",
        severity="major",
        components=["witness-de"],
        message="m",
        known_components=KNOWN,
        now=T0,
    )
    store.append(incident.id, state="resolved", message="Fixed.", now=T1)
    with pytest.raises(StatusError) as caught:
        store.append(incident.id, state="investigating", message="Actually not.", now=T1)
    assert caught.value.code == errors.ALREADY_RESOLVED


def test_listing_is_newest_first(tmp_path):
    store = Store(tmp_path)
    older = store.open(
        title="One", severity="minor", components=["witness-de"], message="m",
        known_components=KNOWN, now=T0,
    )
    newer = store.open(
        title="Two", severity="minor", components=["witness-de"], message="m",
        known_components=KNOWN, now=T0 + dt.timedelta(days=1),
    )
    assert [i.id for i in store.all()] == [newer.id, older.id]


def test_listing_an_absent_directory_is_empty_rather_than_an_error(tmp_path):
    assert Store(tmp_path / "not-there").all() == []


def test_exists_reports_both_ways(tmp_path):
    store = Store(tmp_path)
    incident = store.open(
        title="One", severity="minor", components=["witness-de"], message="m",
        known_components=KNOWN, now=T0,
    )
    assert store.exists(incident.id)
    assert not store.exists("2026-09-04-other")


def test_opening_defaults_to_the_real_clock_when_none_is_given(tmp_path):
    store = Store(tmp_path)
    incident = store.open(
        title="One", severity="minor", components=["witness-de"], message="m",
        known_components=KNOWN,
    )
    assert incident.opened_at.tzinfo == dt.timezone.utc


def test_appending_defaults_to_the_real_clock_when_none_is_given(tmp_path):
    store = Store(tmp_path)
    incident = store.open(
        title="One", severity="minor", components=["witness-de"], message="m",
        known_components=KNOWN, now=T0,
    )
    updated = store.append(incident.id, state="monitoring", message="m")
    assert updated.updates[-1].at.tzinfo == dt.timezone.utc
