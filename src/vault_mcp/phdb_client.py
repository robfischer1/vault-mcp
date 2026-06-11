"""phdb write client — the atom-emit write surface into personal-history-db.

The ``atom`` lifecycle verb records an AI-observed *atom* (a decision,
reversal, tension, or pushback) into phdb's ``session_events`` table, skipping
the vault filesystem entirely. vault-mcp is a *pure client*: it POSTs the atom
to the running phdb service's HTTP ``/emit`` route (#720), so phdb owns the
write and its backend (PG via ``PHDB_BACKEND``) — vault-mcp never opens phdb's
DB directly. Degrades gracefully when the route is unreachable, exactly like
the REST-backed tools degrade without Obsidian (Constitution II).

Boundary discipline:

* **Write-only, over HTTP.** This module only emits ``session_events`` rows,
  through phdb's ``/emit`` route. All reads, identity resolution, and the
  typed-graph live in phdb.
* **Pure core, injected edge.** Validation + ts-resolution are pure; the
  poster (the HTTP transport) is injected, so the logic is unit-tested with a
  fake and the MCP layer wires the real ``_phdb_post`` adapter.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

# The four AI-observed atom types and the payload fields each carries (#140).
# REQUIRED is the load-bearing minimum; ALLOWED is the full permitted set —
# unknown keys are rejected so a caller typo surfaces immediately rather than
# silently landing in the JSON blob.
_ATOM_REQUIRED: dict[str, frozenset[str]] = {
    "decision": frozenset({"polarity"}),
    "reversal": frozenset({"reverses", "position_before", "position_after"}),
    "tension": frozenset({"position_a", "position_b"}),
    "pushback": frozenset({"from", "challenge", "response"}),
}
_ATOM_ALLOWED: dict[str, frozenset[str]] = {
    "decision": frozenset({"polarity", "reversed_by"}),
    "reversal": frozenset(
        {
            "reverses",
            "trigger",
            "position_before",
            "position_after",
            "captured_when",
        }
    ),
    "tension": frozenset(
        {
            "position_a",
            "position_b",
            "held_since",
            "resolution",
            "captured_when",
        }
    ),
    "pushback": frozenset(
        {"from", "challenge", "response", "position_changed", "captured_when"}
    ),
}

ATOM_TYPES: frozenset[str] = frozenset(_ATOM_REQUIRED)

# The write transport: a poster (endpoint, payload) -> result. Injected so this
# module never opens phdb's DB — atom writes go to the phdb service's HTTP
# /emit route, which owns the backend (PG via PHDB_BACKEND) (#720). The MCP
# layer wires the real adapter (server._phdb_post); tests pass a fake.
Poster = Callable[[str, dict[str, Any]], dict[str, Any]]


class AtomError(Exception):
    """An atom type is unknown or its payload violates the per-type contract."""


class PhdbUnavailableError(Exception):
    """phdb's HTTP /emit route is unreachable or returned a non-success result."""


@dataclass(frozen=True)
class AtomResult:
    """The structured echo returned for a successful atom emit."""

    atom_type: str
    event_id: int
    ts: str | None

    def to_dict(self) -> dict[str, Any]:
        """Return this result as a JSON-serializable dict."""
        return {
            "ok": True,
            "atom_type": self.atom_type,
            "event_id": self.event_id,
            "ts": self.ts,
        }


def validate_atom(atom_type: str, payload: object) -> dict[str, Any]:
    """Validate an atom payload against its per-type contract; return it intact.

    Raises ``AtomError`` for an unknown type, a non-mapping payload, a missing
    required field, or any field outside the type's allowed set.
    """
    if atom_type not in _ATOM_REQUIRED:
        raise AtomError(
            f"unknown atom type {atom_type!r}; expected one of {sorted(ATOM_TYPES)}"
        )
    if not isinstance(payload, dict):
        raise AtomError(f"{atom_type} payload must be a mapping")

    keys = set(payload)
    allowed = _ATOM_ALLOWED[atom_type]
    unknown = keys - allowed
    if len(unknown) > 0:
        raise AtomError(
            f"{atom_type} payload has unknown field(s) {sorted(unknown)}; "
            f"allowed: {sorted(allowed)}"
        )
    missing = _ATOM_REQUIRED[atom_type] - keys
    if len(missing) > 0:
        raise AtomError(
            f"{atom_type} payload missing required field(s): {sorted(missing)}"
        )
    return dict(payload)


def _event_ts(payload: dict[str, Any], ts: str | None) -> str | None:
    """Resolve the event timestamp: explicit ``ts`` wins, else ``captured_when``."""
    if ts is not None:
        return ts
    captured = payload.get("captured_when")
    return captured if isinstance(captured, str) and captured != "" else None


def emit_atom(
    atom_type: str,
    payload: object,
    *,
    ts: str | None = None,
    post: Poster,
) -> AtomResult:
    """Emit an atom by POSTing it to phdb's HTTP ``/emit`` route (#720).

    vault-mcp is a pure client: the running phdb service owns the write and the
    backend (PG via ``PHDB_BACKEND``), so this no longer opens phdb's DB directly
    — which broke once SQLite went read-only at cutover, and pinned writes to
    SQLite besides. ``post`` is injected (the real adapter is the server's
    ``_phdb_post``), so the logic is unit-testable with a fake.

    Validation runs first, so a bad payload is rejected (``AtomError``) before any
    network call. Raises ``PhdbUnavailableError`` when the route is unreachable or
    returns a non-success envelope.
    """
    validated = validate_atom(atom_type, payload)  # fail fast, before the POST
    event_ts = _event_ts(validated, ts)
    result = post(
        "/emit",
        {
            "event_type": atom_type,
            "payload": validated,
            "ts": event_ts,
        },
    )
    if not result.get("ok"):
        raise PhdbUnavailableError(
            str(result.get("error", "phdb /emit returned no success flag"))
        )
    event_id = result.get("event_id")
    if not isinstance(event_id, int):
        raise PhdbUnavailableError(f"phdb /emit returned no event_id: {result}")
    return AtomResult(atom_type=atom_type, event_id=event_id, ts=event_ts)
