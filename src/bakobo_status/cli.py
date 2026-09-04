"""``bakobo-status`` — declare, update and close an incident on the public status page.

The command is the primitive rather than a convenience over one (infra this.i @52ehw3d7). A
playbook can call a command and an agent can call a command; neither can click a console. So this
is what OpenTofu and Ansible reach for when an action they take is worth telling strangers about,
and it is also what a person uses at two in the morning.

Design consequences of that second audience, which are the ones that shaped this file:

Nothing here talks to a network or a service. It writes a file. The status tool must work when the
things it reports on do not, and every dependency is a way for that to stop being true.

Every failure prints a code, a title and a detail, and the detail says what to do next. A tool used
during an outage is used by someone who has no attention to spare for a stack trace.

Nothing commits. This writes the file and prints the path; the caller decides whether that becomes
a commit, a pull request, or a change somebody looks at first. Separating the two means a mistyped
severity is an edit rather than a revert of something already published.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .errors import StatusError
from .incidents import SEVERITIES, STATES, Store, stamp

DEFAULT_ROOT = Path("incidents")
DEFAULT_COMPONENTS = Path("components.json")


def load_components(path: Path) -> tuple[str, ...]:
    """The components the estate measures, as generated from the monitoring root's outputs.

    Fails closed if the file is absent. The alternative -- accepting any component name when the
    list cannot be found -- would let a typo put a row on the public page that no probe is behind,
    and it would do so precisely when something else is already wrong.
    """
    if not path.is_file():
        raise StatusError(
            "e.self.config.components.f",
            "The component list is missing.",
            f"No component list at {path}. It is generated from the monitoring root's `components` "
            "output; without it there is no way to tell a real component from a typo, and a status "
            "page showing a component nothing measures is worse than one showing none.",
        )
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise StatusError(
            "e.self.corrupt.components.f",
            "The component list cannot be read.",
            f"{path} is not valid JSON: {exc}. It is generated rather than written, so this "
            "usually means a partial write or a bad merge.",
        ) from exc
    return tuple(sorted(data))


def _split(value: str) -> list[str]:
    return [part for part in value.replace(",", " ").split() if part]


def _describe(incident) -> str:
    age = "open" if not incident.is_resolved else f"{incident.duration}"
    return (
        f"{incident.id}\n"
        f"  {incident.severity} · {incident.state} · {age}\n"
        f"  {incident.title}\n"
        f"  affects: {', '.join(incident.components)}"
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bakobo-status",
        description="Declare, update and close incidents on the Bakobo status page.",
    )
    parser.add_argument(
        "--root", type=Path, default=DEFAULT_ROOT, help="the incident directory (default: incidents)"
    )
    parser.add_argument(
        "--components-file",
        type=Path,
        default=DEFAULT_COMPONENTS,
        help="the generated list of measurable components (default: components.json)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    opener = sub.add_parser("open", help="declare a new incident")
    opener.add_argument("--title", required=True, help="a short phrase naming what is not working")
    opener.add_argument("--severity", required=True, choices=SEVERITIES)
    opener.add_argument(
        "--components", required=True, help="comma- or space-separated component ids"
    )
    opener.add_argument("--message", required=True, help="what is known right now")

    updater = sub.add_parser("update", help="add an update to an open incident")
    updater.add_argument("id")
    # No default state. An update whose state defaults to the previous one lets an incident sit in
    # `investigating` for a day because nobody typed the word, and the state is the part a reader
    # actually reacts to.
    updater.add_argument("--state", required=True, choices=STATES)
    updater.add_argument("--message", required=True)

    closer = sub.add_parser("resolve", help="close an incident")
    closer.add_argument("id")
    closer.add_argument("--message", required=True, help="what was wrong, and what fixed it")

    lister = sub.add_parser("list", help="list incidents, newest first")
    lister.add_argument("--open", action="store_true", help="only those not yet resolved")

    shower = sub.add_parser("show", help="print one incident as stored")
    shower.add_argument("id")

    return parser


def run(argv, out) -> int:
    args = build_parser().parse_args(argv)
    store = Store(args.root)

    if args.command == "open":
        known = load_components(args.components_file)
        incident = store.open(
            title=args.title,
            severity=args.severity,
            components=_split(args.components),
            message=args.message,
            known_components=known,
        )
        print(f"opened {incident.id}", file=out)
        print(store.path_for(incident.id), file=out)
        return 0

    if args.command in ("update", "resolve"):
        state = "resolved" if args.command == "resolve" else args.state
        incident = store.append(args.id, state=state, message=args.message)
        print(f"{incident.id} is now {incident.state} ({stamp(incident.updates[-1].at)})", file=out)
        print(store.path_for(incident.id), file=out)
        return 0

    if args.command == "list":
        found = [i for i in store.all() if not (args.open and i.is_resolved)]
        if not found:
            # Said rather than left blank. Silence from a status tool reads as "it did not run",
            # which during an outage is the wrong thing to be wondering about.
            print("no incidents" + (" open" if args.open else ""), file=out)
            return 0
        print("\n\n".join(_describe(i) for i in found), file=out)
        return 0

    # Read through the store rather than off the path, so an absent file is the typed
    # "no such incident" with an instruction attached, not a traceback about a filename.
    store.read(args.id)
    print(store.path_for(args.id).read_text(encoding="utf-8"), end="", file=out)
    return 0


def main(argv=None) -> int:
    """Entry point. Turns a typed failure into a message a hurrying person can act on.

    The code is printed alongside the sentence rather than instead of it. The sentence is for the
    person; the code is what they can search for, and what a machine reading stderr can match on.
    """
    try:
        return run(sys.argv[1:] if argv is None else argv, sys.stdout)
    except StatusError as exc:
        print(f"{exc.code}: {exc.title}", file=sys.stderr)
        print(f"  {exc.detail}", file=sys.stderr)
        print(f"  {exc.type_url}", file=sys.stderr)
        return 1
