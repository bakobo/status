"""The command as an operator meets it.

These exercise the argument surface and the failure reporting rather than the model, which
tests/test_incidents.py already covers. The thing being asserted is that a person under time
pressure gets a code, a sentence, and an instruction — and that a machine calling this from a
playbook gets an exit status it can branch on.
"""

from __future__ import annotations

import datetime as dt
import io
import json

import pytest

from bakobo_status import cli
from bakobo_status.errors import StatusError

KNOWN = ["witness-de", "witness-ca", "bakobo-com"]


@pytest.fixture()
def estate(tmp_path):
    """An incident directory and the generated component list beside it."""
    (tmp_path / "components.json").write_text(json.dumps(KNOWN), encoding="utf-8")
    return tmp_path


def invoke(estate, *argv):
    out = io.StringIO()
    code = cli.run(
        ["--root", str(estate / "incidents"),
         "--components-file", str(estate / "components.json"), *argv],
        out,
    )
    return code, out.getvalue()


def open_one(estate, title="DE witness unreachable"):
    return invoke(
        estate, "open", "--title", title, "--severity", "major",
        "--components", "witness-de", "--message", "No OOBI response.",
    )


def test_opening_prints_the_identifier_and_the_path(estate):
    code, out = open_one(estate)
    assert code == 0
    assert "opened " in out
    assert "de-witness-unreachable.md" in out


def test_components_accept_commas_or_spaces(estate):
    code, _ = invoke(
        estate, "open", "--title", "Two down", "--severity", "critical",
        "--components", "witness-de, witness-ca", "--message", "Both silent.",
    )
    assert code == 0
    _, shown = invoke(estate, "list")
    assert "witness-de, witness-ca" in shown


def test_updating_reports_the_new_state(estate):
    _, out = open_one(estate)
    incident_id = out.splitlines()[0].removeprefix("opened ")
    code, message = invoke(estate, "update", incident_id, "--state", "identified",
                           "--message", "Disk full.")
    assert code == 0
    assert "is now identified" in message


def test_resolve_does_not_make_the_operator_type_the_state(estate):
    """`resolve` is its own verb precisely so nobody has to remember the word under pressure."""
    _, out = open_one(estate)
    incident_id = out.splitlines()[0].removeprefix("opened ")
    code, message = invoke(estate, "resolve", incident_id, "--message", "Restarted.")
    assert code == 0
    assert "is now resolved" in message


def test_listing_says_so_rather_than_printing_nothing(estate):
    code, out = invoke(estate, "list")
    assert code == 0
    assert out.strip() == "no incidents"


def test_listing_open_only_hides_the_resolved(estate):
    _, out = open_one(estate)
    incident_id = out.splitlines()[0].removeprefix("opened ")
    invoke(estate, "resolve", incident_id, "--message", "Restarted.")
    open_one(estate, title="CA witness slow")

    _, everything = invoke(estate, "list")
    assert "de-witness-unreachable" in everything

    _, only_open = invoke(estate, "list", "--open")
    assert "de-witness-unreachable" not in only_open
    assert "ca-witness-slow" in only_open


def test_listing_only_open_when_there_are_none_says_which_kind(estate):
    _, out = open_one(estate)
    incident_id = out.splitlines()[0].removeprefix("opened ")
    invoke(estate, "resolve", incident_id, "--message", "Restarted.")
    _, message = invoke(estate, "list", "--open")
    assert message.strip() == "no incidents open"


def test_show_prints_the_file_as_stored(estate):
    _, out = open_one(estate)
    incident_id = out.splitlines()[0].removeprefix("opened ")
    code, shown = invoke(estate, "show", incident_id)
    assert code == 0
    assert shown.startswith("---\n")
    assert "## " in shown


def test_show_of_an_absent_incident_is_the_typed_error(estate):
    with pytest.raises(StatusError) as caught:
        invoke(estate, "show", "2026-09-04-nothing")
    assert caught.value.code == "e.state.missing.incident.f"


def test_a_missing_component_list_fails_closed(tmp_path):
    out = io.StringIO()
    with pytest.raises(StatusError) as caught:
        cli.run(
            ["--root", str(tmp_path / "incidents"),
             "--components-file", str(tmp_path / "absent.json"),
             "open", "--title", "T", "--severity", "minor",
             "--components", "witness-de", "--message", "m"],
            out,
        )
    assert caught.value.code == "e.self.config.components.f"


def test_a_corrupt_component_list_says_so_rather_than_accepting_anything(tmp_path):
    (tmp_path / "components.json").write_text("{not json", encoding="utf-8")
    out = io.StringIO()
    with pytest.raises(StatusError) as caught:
        cli.run(
            ["--root", str(tmp_path / "incidents"),
             "--components-file", str(tmp_path / "components.json"),
             "open", "--title", "T", "--severity", "minor",
             "--components", "witness-de", "--message", "m"],
            out,
        )
    assert caught.value.code == "e.self.corrupt.components.f"


def test_an_unknown_severity_is_refused_by_the_parser_before_anything_is_written(estate):
    with pytest.raises(SystemExit):
        invoke(estate, "open", "--title", "T", "--severity", "catastrophic",
               "--components", "witness-de", "--message", "m")
    assert not (estate / "incidents").exists()


def test_no_subcommand_is_an_error_rather_than_a_no_op(estate):
    with pytest.raises(SystemExit):
        invoke(estate)


# --- what main() adds on top of run() --------------------------------------------------------

def test_main_prints_the_code_the_sentence_and_the_catalog_url(estate, capsys):
    code = cli.main([
        "--root", str(estate / "incidents"),
        "--components-file", str(estate / "components.json"),
        "show", "2026-09-04-nothing",
    ])
    assert code == 1
    err = capsys.readouterr().err
    assert "e.state.missing.incident.f" in err
    assert "https://errors.bakobo.com/e.state.missing.incident.f" in err
    assert "bakobo-status list" in err


def test_main_returns_zero_on_success(estate, capsys):
    code = cli.main([
        "--root", str(estate / "incidents"),
        "--components-file", str(estate / "components.json"),
        "list",
    ])
    assert code == 0
    assert capsys.readouterr().out.strip() == "no incidents"


def test_main_reads_argv_when_given_none(estate, capsys, monkeypatch):
    monkeypatch.setattr(
        "sys.argv",
        ["bakobo-status", "--root", str(estate / "incidents"),
         "--components-file", str(estate / "components.json"), "list"],
    )
    assert cli.main() == 0
    assert capsys.readouterr().out.strip() == "no incidents"


# --- rollup ------------------------------------------------------------------------------------

class StubClient:
    """Answers the four queries roll_day asks, or raises for a named component."""

    def __init__(self, fail_for=()):
        self.fail_for = set(fail_for)

    def _guard(self, promql):
        for job in self.fail_for:
            if f'job="{job}"' in promql:
                raise StatusError("e.env.metrics.unavailable.r", "Down.", "Grafana is unreachable.")

    def query_range(self, promql, start, end, step):
        self._guard(promql)
        return [{"metric": {}, "values": [[0, "1"], [0, "1"]]}]

    def query(self, promql, at):
        self._guard(promql)
        return [{"metric": {}, "value": [0, "2"]}]


def rollup_args(estate, date="2026-09-05"):
    return cli.build_parser().parse_args([
        "--root", str(estate / "incidents"),
        "--components-file", str(estate / "components.json"),
        "rollup", "--date", date, "--history", str(estate / "history"),
    ])


def test_rollup_writes_a_day_for_every_component(estate):
    out = io.StringIO()
    code = cli._rollup(rollup_args(estate), out, client=StubClient())
    assert code == 0

    written = sorted(p.name for p in (estate / "history").glob("*.json"))
    assert written == ["bakobo-com.json", "witness-ca.json", "witness-de.json"]
    assert "uptime 1.0000" in out.getvalue()
    assert "operational" in out.getvalue()


class EmptyClient:
    """A stack with no data for the day -- before the checks existed."""

    def query_range(self, promql, start, end, step):
        return []

    def query(self, promql, at):
        return []


def test_a_day_with_no_data_is_not_written_at_all(estate):
    """It would otherwise be a red bar for an outage that never happened -- which is exactly what
    the first real backfill published, before this was caught."""
    out = io.StringIO()
    code = cli._rollup(rollup_args(estate), out, client=EmptyClient())
    assert code == 0
    assert "no data, not recorded" in out.getvalue()
    assert not list((estate / "history").glob("*.json"))


def test_backfill_fills_the_window_and_plain_rollup_does_not(estate):
    """A week of failed runs must be repairable by one successful run; the window is finite, so a
    repair delayed past fourteen days is a repair that never happens."""
    plain = rollup_args(estate)
    cli._rollup(plain, io.StringIO(), client=StubClient())
    assert len(json.loads((estate / "history" / "witness-de.json").read_text())["days"]) == 1

    filling = rollup_args(estate)
    filling.backfill = True
    cli._rollup(filling, io.StringIO(), client=StubClient())
    days = json.loads((estate / "history" / "witness-de.json").read_text())["days"]
    assert len(days) == 14
    assert "2026-09-05" in days and "2026-08-23" in days


def test_backfill_does_not_redo_a_day_already_recorded(estate):
    filling = rollup_args(estate)
    filling.backfill = True
    cli._rollup(filling, io.StringIO(), client=StubClient())
    out = io.StringIO()
    cli._rollup(filling, out, client=StubClient())
    # Only the named day is re-rolled; the other thirteen are already present.
    assert out.getvalue().count("witness-de ") == 1


def test_rollup_is_idempotent(estate):
    for _ in range(2):
        cli._rollup(rollup_args(estate), io.StringIO(), client=StubClient())
    data = json.loads((estate / "history" / "witness-de.json").read_text())
    assert list(data["days"]) == ["2026-09-05"]


def test_one_component_failing_does_not_abandon_the_others(estate):
    """A day is recoverable for fourteen days; giving up on six because one failed spends five
    of those recoveries for nothing."""
    out = io.StringIO()
    code = cli._rollup(rollup_args(estate), out, client=StubClient(fail_for={"witness-ca"}))

    assert code == 1
    assert "witness-ca 2026-09-05: FAILED" in out.getvalue()
    assert "1 not rolled up: witness-ca@2026-09-05" in out.getvalue()
    assert (estate / "history" / "witness-de.json").is_file()
    assert not (estate / "history" / "witness-ca.json").is_file()


def test_a_malformed_date_is_refused_before_anything_is_queried(estate):
    with pytest.raises(StatusError) as caught:
        cli._rollup(rollup_args(estate, date="yesterday"), io.StringIO(), client=StubClient())
    assert caught.value.code == "e.input.format.incident-id.f"
    assert "silently roll up a partial one" in caught.value.detail


def test_missing_credentials_name_every_variable_that_is_absent(estate):
    with pytest.raises(StatusError) as caught:
        cli._rollup(rollup_args(estate), io.StringIO(), environ={"GRAFANA_PROM_URL": "https://p"})
    assert caught.value.code == "e.self.config.metrics-credentials.f"
    # The "Missing:" list names only what is absent; the sentence after it explains all three,
    # because a reader who has one wrong usually needs to be told what the other two are.
    missing = caught.value.detail.split("Missing: ")[1].split(".")[0]
    assert missing == "GRAFANA_PROM_USER, GRAFANA_PROM_TOKEN"


def test_credentials_come_from_the_environment_not_a_flag(estate, monkeypatch):
    """A token in a flag lands in shell history and in the CI line that echoes the command."""
    monkeypatch.setattr(cli.Prometheus, "query_range", lambda *a, **k: [])
    monkeypatch.setattr(cli.Prometheus, "query", lambda *a, **k: [])
    code = cli._rollup(rollup_args(estate), io.StringIO(), environ={
        "GRAFANA_PROM_URL": "https://p", "GRAFANA_PROM_USER": "1", "GRAFANA_PROM_TOKEN": "t"})
    assert code == 0


def test_rollup_reaches_the_command_through_run(estate, monkeypatch):
    monkeypatch.setattr(cli.Prometheus, "query_range", lambda *a, **k: [])
    monkeypatch.setattr(cli.Prometheus, "query", lambda *a, **k: [])
    monkeypatch.setenv("GRAFANA_PROM_URL", "https://p")
    monkeypatch.setenv("GRAFANA_PROM_USER", "1")
    monkeypatch.setenv("GRAFANA_PROM_TOKEN", "t")
    out = io.StringIO()
    assert cli.run([
        "--root", str(estate / "incidents"),
        "--components-file", str(estate / "components.json"),
        "rollup", "--date", "2026-09-05", "--history", str(estate / "history"),
    ], out) == 0


# --- build ---------------------------------------------------------------------------------------

def build_args(estate, **over):
    args = cli.build_parser().parse_args([
        "--root", str(estate / "incidents"),
        "--components-file", str(estate / "components.json"),
        "build", "--out", str(estate / "_site"), "--history", str(estate / "history"),
    ])
    for k, v in over.items():
        setattr(args, k, v)
    return args


def test_build_writes_one_self_contained_page(estate):
    out = io.StringIO()
    assert cli._build(build_args(estate), out) == 0
    page = (estate / "_site" / "index.html").read_text()
    assert page.startswith("<!doctype html>")
    assert "Bakobo status" in page
    assert "wrote " in out.getvalue()


def test_build_accepts_the_bare_id_list_the_other_commands_use(estate):
    """components.json is a map in production, but the incident commands accept a bare list. The
    build degrades to using the id as its own label rather than refusing, because a page that
    will not render is worse than one with plain labels."""
    cli._build(build_args(estate), io.StringIO())
    page = (estate / "_site" / "index.html").read_text()
    for component in KNOWN:
        assert component in page


def test_build_reaches_no_network(estate, monkeypatch):
    """It must succeed while the estate is on fire -- that is when someone needs it republished."""
    def forbidden(*a, **k):
        raise AssertionError("the build opened a socket")

    monkeypatch.setattr(cli.Prometheus, "query", forbidden)
    monkeypatch.setattr(cli.Prometheus, "query_range", forbidden)
    assert cli._build(build_args(estate), io.StringIO()) == 0


def test_build_reaches_the_command_through_run(estate):
    out = io.StringIO()
    assert cli.run([
        "--root", str(estate / "incidents"),
        "--components-file", str(estate / "components.json"),
        "build", "--out", str(estate / "_site"), "--history", str(estate / "history"),
    ], out) == 0
    assert (estate / "_site" / "index.html").is_file()


def test_build_uses_display_names_when_components_json_is_the_generated_map(estate):
    """The production file is the monitoring root's `components` output — a map carrying display
    names, so the page reads 'DE witness — de.wit.bakobo.com' rather than 'witness-de'."""
    (estate / "components.json").write_text(json.dumps({
        "witness-de": {"display": "DE witness — de.wit.bakobo.com", "kind": "witness"},
    }), encoding="utf-8")
    cli._build(build_args(estate), io.StringIO())
    page = (estate / "_site" / "index.html").read_text()
    assert "DE witness — de.wit.bakobo.com" in page


# --- now ---------------------------------------------------------------------------------------

class NowClient:
    """Answers the one range query `now` asks, or raises for a named component."""

    def __init__(self, value="1", fail_for=(), empty_for=()):
        self.value = value
        self.fail_for = set(fail_for)
        self.empty_for = set(empty_for)

    def query_range(self, promql, start, end, step):
        for job in self.fail_for:
            if f'job="{job}"' in promql:
                raise StatusError("e.env.metrics.unavailable.r", "Down.", "Grafana is unreachable.")
        for job in self.empty_for:
            if f'job="{job}"' in promql:
                return []
        return [{"metric": {}, "values": [[end.timestamp(), self.value]]}]


def now_args(estate):
    return cli.build_parser().parse_args([
        "--root", str(estate / "incidents"),
        "--components-file", str(estate / "components.json"),
        "now", "--snapshot", str(estate / "now.json"),
    ])


AT = dt.datetime(2026, 9, 5, 12, 0, tzinfo=dt.timezone.utc)


def test_now_writes_every_component_into_one_snapshot(estate):
    out = io.StringIO()
    code = cli._now(now_args(estate), out, client=NowClient(), at=AT)
    assert code == 0

    written = json.loads((estate / "now.json").read_text())
    assert written["taken_at"] == AT.isoformat()
    assert sorted(written["components"]) == ["bakobo-com", "witness-ca", "witness-de"]
    assert "up as of" in out.getvalue()


def test_now_records_a_component_that_is_not_answering(estate):
    out = io.StringIO()
    cli._now(now_args(estate), out, client=NowClient(value="0"), at=AT)
    written = json.loads((estate / "now.json").read_text())
    assert written["components"]["witness-de"]["current"] == 0
    assert "DOWN as of" in out.getvalue()


def test_a_component_with_no_samples_is_omitted_not_written_down(estate):
    """No probe result is not a probe result meaning down. Writing it as down would put a red row
    on the page for a check that was deleted."""
    out = io.StringIO()
    code = cli._now(now_args(estate), out, client=NowClient(empty_for={"witness-ca"}), at=AT)
    assert code == 0
    written = json.loads((estate / "now.json").read_text())
    assert "witness-ca" not in written["components"]
    assert "witness-ca: nothing measured, omitted" in out.getvalue()


def test_one_refused_component_still_writes_the_other_six(estate):
    """Unlike the rollup, nothing here expires -- but six components measured beats seven grey."""
    out = io.StringIO()
    code = cli._now(now_args(estate), out, client=NowClient(fail_for={"witness-ca"}), at=AT)
    assert code == 1
    written = json.loads((estate / "now.json").read_text())
    assert sorted(written["components"]) == ["bakobo-com", "witness-de"]
    assert "1 not measured: witness-ca" in out.getvalue()


def test_now_needs_the_same_credentials_the_rollup_does(estate):
    with pytest.raises(StatusError) as caught:
        cli._now(now_args(estate), io.StringIO(), environ={})
    assert caught.value.code == "e.self.config.metrics-credentials.f"


def test_now_reaches_the_command_through_run(estate, monkeypatch):
    monkeypatch.setattr(cli, "_now", lambda args, out: 0)
    assert cli.run([
        "--root", str(estate / "incidents"),
        "--components-file", str(estate / "components.json"),
        "now", "--snapshot", str(estate / "now.json"),
    ], io.StringIO()) == 0
