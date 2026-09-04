"""The incident record: what one is, how it is stored, and how it is read back.

An incident is a file in this repository (infra this.i @52ehw3d7). Everything else here follows
from that one decision, so it is worth being explicit about what the file has to be good at.

It is read by three audiences with different needs. A **generator** turns it into the public page,
so it must parse without ambiguity. A **reviewer** reads it as a diff in a pull request, so a
correction to one update must not reflow the rest. And a **person during an outage** appends to it
under time pressure, possibly by hand when the tooling is what is broken, so the format has to be
obvious from looking at one example.

Hence: a small fixed header of ``key: value`` lines, then the updates as markdown sections. NOT
YAML, and that is a decision rather than an omission. A YAML header would admit block scalars,
anchors, implicit typing and eight ways to write the same thing, so two tools would eventually
disagree about a file that looked fine -- and it would add a dependency to a program whose entire
value is that it still runs when other things do not. The header here is one syntax with one
meaning, and a line that does not match it is an error rather than a surprise.

Times are UTC and second-resolution, written with a trailing Z. An operator comparing an update
against a probe series should not have to reason about an offset at an hour when they are not
sharp.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, replace
from pathlib import Path

from . import errors
from .errors import StatusError

# The lifecycle a reader already knows from every hosted status page. Adopted rather than invented
# (@52ehw3d7): familiarity is most of what a status page is for, and a stranger should not have to
# learn our vocabulary during our outage.
STATES = ("investigating", "identified", "monitoring", "resolved")

# How much of the estate is affected, which is the only thing a hurrying reader actually reads.
# Four levels because three collapses the distinction between "some of it is slow" and "some of it
# is gone", and that distinction is the one a controller operator acts on differently.
SEVERITIES = ("maintenance", "minor", "major", "critical")

_ID = re.compile(r"\A\d{4}-\d{2}-\d{2}-[a-z0-9]+(?:-[a-z0-9]+)*\Z")
_HEADER_LINE = re.compile(r"\A([a-z][a-z-]*): (.*)\Z")
_UPDATE_HEADING = re.compile(r"\A## (\S+) (\w+)\Z")
_SLUG_STRIP = re.compile(r"[^a-z0-9]+")

_FENCE = "---"


def utcnow() -> dt.datetime:
    """The clock, in one place so tests can pass their own instead of monkeypatching a module.

    Second resolution and no microseconds: the extra digits are noise in a file a person reads,
    and they make two updates in the same second look different when the ordering is what matters.
    """
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0)


def stamp(moment: dt.datetime) -> str:
    """Render a moment the way the file stores it: UTC, seconds, trailing Z."""
    return moment.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def slugify(title: str) -> str:
    """A filename-safe, URL-safe fragment of a title.

    The slug ends up in the incident's permalink on the public page, so it is worth it being
    readable: a stranger who was sent a link should be able to tell what it is about before it
    loads.
    """
    slug = _SLUG_STRIP.sub("-", title.strip().lower()).strip("-")
    if not slug:
        raise StatusError(
            errors.EMPTY_TITLE,
            "An incident needs a title.",
            "The title given contains no letters or digits, so it produces an empty identifier. "
            "Give a short phrase naming what is not working, as a reader would say it.",
        )
    return slug


@dataclass(frozen=True)
class Update:
    """One thing said about an incident at one moment."""

    at: dt.datetime
    state: str
    message: str


@dataclass(frozen=True)
class Incident:
    """One incident, as stored.

    ``resolved_at`` is derived from the updates rather than stored twice. A separate header field
    would be a second copy of a fact the updates already carry, and the two would eventually
    disagree -- which on a status page means the banner and the timeline contradicting each other
    in front of the people least inclined to be charitable about it.
    """

    id: str
    title: str
    severity: str
    components: tuple[str, ...]
    updates: tuple[Update, ...]

    @property
    def opened_at(self) -> dt.datetime:
        return self.updates[0].at

    @property
    def state(self) -> str:
        return self.updates[-1].state

    @property
    def is_resolved(self) -> bool:
        return self.state == "resolved"

    @property
    def resolved_at(self) -> dt.datetime | None:
        return self.updates[-1].at if self.is_resolved else None

    @property
    def duration(self) -> dt.timedelta | None:
        """How long it ran, or None while it is still running.

        Deliberately None rather than "time so far": a duration on an open incident would be a
        number that changes every time the page is generated, which makes every rebuild a diff.
        """
        return None if not self.is_resolved else self.resolved_at - self.opened_at


def validate_severity(severity: str) -> str:
    if severity not in SEVERITIES:
        raise StatusError(
            errors.UNKNOWN_SEVERITY,
            "That is not a severity this status page publishes.",
            f"'{severity}' is not one of {', '.join(SEVERITIES)}. Severity says how much of the "
            "estate is affected, not how urgent it feels.",
        )
    return severity


def validate_state(state: str) -> str:
    if state not in STATES:
        raise StatusError(
            errors.UNKNOWN_STATE,
            "That is not a state an incident moves through.",
            f"'{state}' is not one of {', '.join(STATES)}. These are the states a reader already "
            "knows from other status pages, and this page does not invent its own.",
        )
    return state


def validate_components(components, known) -> tuple[str, ...]:
    """Every named component must be one the estate actually measures.

    This is @rue5lgob's guarantee reaching the other end. The component list is generated from the
    monitoring root's outputs, so a typo here would put a component on the public page that no
    probe is behind -- a row that can never go green, next to rows that mean something. Failing
    closed on an unknown name is cheaper than discovering that during an outage.
    """
    named = tuple(dict.fromkeys(c.strip() for c in components if c.strip()))
    if not named:
        raise StatusError(
            errors.UNKNOWN_COMPONENT,
            "An incident has to say what is affected.",
            "No components were named. An incident that names nothing tells a reader that "
            "something is wrong and gives them no way to know whether it is their something.",
        )
    unknown = [c for c in named if c not in known]
    if unknown:
        raise StatusError(
            errors.UNKNOWN_COMPONENT,
            "That component is not one this status page measures.",
            f"Unknown component(s): {', '.join(sorted(unknown))}. Known: "
            f"{', '.join(sorted(known))}. The list is generated from the monitoring root's "
            "outputs, so a name missing here usually means the check was never declared.",
        )
    return named


def validate_message(message: str) -> str:
    text = message.strip()
    if not text:
        raise StatusError(
            errors.EMPTY_MESSAGE,
            "Every update has to say something.",
            "The message was empty. An update with no text is a timestamp claiming progress was "
            "made, which is worse than not posting one.",
        )
    return text


def validate_id(incident_id: str) -> str:
    if not _ID.match(incident_id):
        raise StatusError(
            errors.MALFORMED_ID,
            "That is not an incident identifier.",
            f"'{incident_id}' is not shaped YYYY-MM-DD-slug. The identifier is also the filename "
            "and the permalink, which is why its shape is fixed.",
        )
    return incident_id


def new_id(opened: dt.datetime, title: str) -> str:
    """Date first so the directory sorts chronologically without any tooling.

    An `ls` of the incident directory should read as a history. That is worth more than a shorter
    identifier, because the moment somebody needs it is the moment they have no tooling.
    """
    return f"{stamp(opened)[:10]}-{slugify(title)}"


def render(incident: Incident) -> str:
    """The file, exactly as it is stored.

    Round-trips with :func:`parse`, which is asserted by test rather than assumed: a format that
    only survives its own writer is a format that loses an update the first time someone edits a
    file by hand, which is precisely the situation it exists for.
    """
    lines = [
        _FENCE,
        f"id: {incident.id}",
        f"title: {incident.title}",
        f"severity: {incident.severity}",
        f"components: {', '.join(incident.components)}",
        _FENCE,
    ]
    for update in incident.updates:
        lines += ["", f"## {stamp(update.at)} {update.state}", "", update.message]
    return "\n".join(lines) + "\n"


def _parse_header(lines, source):
    if not lines or lines[0].strip() != _FENCE:
        raise StatusError(
            errors.CORRUPT_INCIDENT,
            "That incident file cannot be read.",
            f"{source} does not begin with a '{_FENCE}' header fence. Either it is not an "
            "incident file, or its header was lost in an edit.",
        )
    header = {}
    for index, line in enumerate(lines[1:], start=2):
        if line.strip() == _FENCE:
            return header, index
        match = _HEADER_LINE.match(line)
        if not match:
            raise StatusError(
                errors.CORRUPT_INCIDENT,
                "That incident file cannot be read.",
                f"{source} line {index} is not a 'key: value' header line: {line!r}. The header "
                "is deliberately one syntax with one meaning, so anything else is an error rather "
                "than a guess.",
            )
        header[match.group(1)] = match.group(2).strip()
    raise StatusError(
        errors.CORRUPT_INCIDENT,
        "That incident file cannot be read.",
        f"{source} has an unterminated header: no closing '{_FENCE}' was found.",
    )


def _parse_updates(lines, source):
    updates, at, state, body = [], None, None, []

    def flush():
        if at is not None:
            updates.append(Update(at=at, state=state, message="\n".join(body).strip()))

    for index, line in enumerate(lines, start=1):
        match = _UPDATE_HEADING.match(line.rstrip())
        if not match:
            body.append(line)
            continue
        flush()
        moment, state = match.groups()
        body = []
        try:
            at = dt.datetime.strptime(moment, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)
        except ValueError as exc:
            raise StatusError(
                errors.CORRUPT_INCIDENT,
                "That incident file cannot be read.",
                f"{source} carries an update timestamped {moment!r}, which is not a UTC instant "
                "shaped YYYY-MM-DDTHH:MM:SSZ. Times are UTC so that an update can be compared "
                "against a probe series without anyone reasoning about an offset.",
            ) from exc
        validate_state(state)
    flush()

    if not updates:
        raise StatusError(
            errors.CORRUPT_INCIDENT,
            "That incident file cannot be read.",
            f"{source} contains no updates. An incident is its updates; a file with a header and "
            "nothing else records that something happened and not what.",
        )
    return tuple(updates)


def parse(text: str, source: str = "the incident") -> Incident:
    """Read a stored incident back, refusing anything ambiguous.

    Fails closed throughout, per org principle 8: a file this cannot understand is an error, never
    a best guess. A status page built from a guess is confidently wrong, which is the one failure
    mode a status page must not have.
    """
    lines = text.splitlines()
    header, body_starts = _parse_header(lines, source)

    missing = [k for k in ("id", "title", "severity", "components") if not header.get(k)]
    if missing:
        raise StatusError(
            errors.CORRUPT_INCIDENT,
            "That incident file cannot be read.",
            f"{source} is missing header field(s): {', '.join(missing)}.",
        )

    return Incident(
        id=validate_id(header["id"]),
        title=header["title"],
        severity=validate_severity(header["severity"]),
        components=tuple(c.strip() for c in header["components"].split(",") if c.strip()),
        updates=_parse_updates(lines[body_starts:], source),
    )


class Store:
    """The incident directory, and the only thing in this package that touches a filesystem.

    Kept separate from the model so that every rule above is testable without a temporary
    directory, and so that the one place capable of losing an operator's words is small enough to
    read in a sitting.
    """

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def path_for(self, incident_id: str) -> Path:
        return self.root / f"{validate_id(incident_id)}.md"

    def exists(self, incident_id: str) -> bool:
        return self.path_for(incident_id).is_file()

    def read(self, incident_id: str) -> Incident:
        path = self.path_for(incident_id)
        if not path.is_file():
            raise StatusError(
                errors.NO_SUCH_INCIDENT,
                "There is no incident with that identifier.",
                f"No incident '{incident_id}' in {self.root}. Run `bakobo-status list` to see "
                "what is there; an identifier is the date and the slug, not the title.",
            )
        return parse(path.read_text(encoding="utf-8"), source=str(path))

    def write(self, incident: Incident) -> Path:
        path = self.path_for(incident.id)
        self.root.mkdir(parents=True, exist_ok=True)
        path.write_text(render(incident), encoding="utf-8")
        return path

    def all(self) -> list[Incident]:
        """Every incident, newest first.

        Sorted by identifier rather than by mtime: the identifier carries the date the incident
        opened, and mtime carries the last time git happened to touch the file -- which after a
        fresh clone is the same instant for all of them.
        """
        if not self.root.is_dir():
            return []
        found = [self.read(p.stem) for p in sorted(self.root.glob("*.md"))]
        return sorted(found, key=lambda i: (i.opened_at, i.id), reverse=True)

    def open(self, *, title, severity, components, message, known_components, now=None) -> Incident:
        """Declare an incident. Refuses to reuse an identifier."""
        moment = now or utcnow()
        incident = Incident(
            id=new_id(moment, title),
            title=title.strip(),
            severity=validate_severity(severity),
            components=validate_components(components, known_components),
            updates=(Update(at=moment, state="investigating", message=validate_message(message)),),
        )
        if self.exists(incident.id):
            raise StatusError(
                errors.ID_TAKEN,
                "An incident with that identifier already exists.",
                f"'{incident.id}' is taken, which means a second incident today has the same "
                "title. Say what distinguishes them in the title -- a reader looking at the "
                "history later will need the same distinction.",
            )
        self.write(incident)
        return incident

    def append(self, incident_id, *, state, message, now=None) -> Incident:
        """Add an update. Refuses to reopen a resolved incident.

        Reopening is refused rather than allowed because a resolved incident is a claim already
        made to everyone watching. Walking it back inside the same record rewrites what was said;
        a fresh incident that references this one leaves both statements standing, which is what
        the record is for.
        """
        incident = self.read(incident_id)
        if incident.is_resolved:
            raise StatusError(
                errors.ALREADY_RESOLVED,
                "That incident is already resolved.",
                f"'{incident_id}' was resolved at {stamp(incident.resolved_at)}. Open a new "
                "incident referencing it rather than reopening this one: the resolution was "
                "published, and amending it would rewrite something people have already read.",
            )
        moment = now or utcnow()
        updated = replace(
            incident,
            updates=incident.updates
            + (Update(at=moment, state=validate_state(state), message=validate_message(message)),),
        )
        self.write(updated)
        return updated
