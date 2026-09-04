"""The status tool's error taxonomy (infra this.i @52ehw3d7).

Every failure is a typed :class:`StatusError` carrying a stable code shaped
``<sorter>.<descriptor>[.<sub>...].<disposition>``, per dev/standards/error-codes.md. The code is
classified by what the OBSTACLE was rather than by which function raised it, so a caller can
prefix-match a branch of meaning: ``e.input.*`` is "what you sent", ``e.state.*`` is "the condition
of the target", and ``e.*.r`` is "retrying could help" whatever the descriptor.

Codes are declared as module-scope literals rather than assembled, so they can be extracted by
static analysis and reconciled against the catalog in bakobo/errors.

Every code here is final (``.f``). That is not an oversight: this tool's failures are all
disagreements between what the operator typed and what is on disk, and none of them resolve by
waiting. A retryable code in this module would be a lie about a filesystem.
"""

from __future__ import annotations

_TYPE_BASE = "https://errors.bakobo.com"


class StatusError(Exception):
    """A failure with a stable code, a static title, and a detail about this occurrence.

    ``title`` never varies with the occurrence and ``detail`` always does; keeping that split is
    what lets a reader recognise a class of failure without reading the specifics, and what lets
    the catalog carry a useful sentence for every code.
    """

    def __init__(self, code: str, title: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.title = title
        self.detail = detail

    @property
    def type_url(self) -> str:
        """The catalog URL for this code, which is what a log line points a reader at."""
        return f"{_TYPE_BASE}/{self.code}"

    def __str__(self) -> str:
        return self.detail


# --- what you sent -------------------------------------------------------------------------

UNKNOWN_SEVERITY = "e.input.format.severity.f"
UNKNOWN_STATE = "e.input.format.state.f"
UNKNOWN_COMPONENT = "e.input.format.component.f"
MALFORMED_ID = "e.input.format.incident-id.f"
EMPTY_MESSAGE = "e.input.missing.message.f"
EMPTY_TITLE = "e.input.missing.title.f"

# --- the condition of the target -----------------------------------------------------------

NO_SUCH_INCIDENT = "e.state.missing.incident.f"
ALREADY_RESOLVED = "e.state.conflict.resolved.f"
ID_TAKEN = "e.state.conflict.incident-id.f"

# --- our own house -------------------------------------------------------------------------

CORRUPT_INCIDENT = "e.self.corrupt.incident.f"
