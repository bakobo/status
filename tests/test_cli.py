"""The command as an operator meets it.

These exercise the argument surface and the failure reporting rather than the model, which
tests/test_incidents.py already covers. The thing being asserted is that a person under time
pressure gets a code, a sentence, and an instruction — and that a machine calling this from a
playbook gets an exit status it can branch on.
"""

from __future__ import annotations

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
