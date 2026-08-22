"""Atom write client — the atom-emit write surface for ``session_events``.

The ``atom`` lifecycle verb records an AI-observed *atom* (a decision,
reversal, tension, or pushback) as a ``session_events`` row, skipping the vault
filesystem entirely. vault-mcp is a *pure client*: it hands the atom to the
injected poster and never opens a store directly. Degrades gracefully when the
write path is unreachable, exactly like the REST-backed tools degrade without
Obsidian (Constitution II).

Transport (PHDB dissolution C1, 2026-08-03): the poster the MCP layer wires
(``server._phdb_post``) routes ``/emit`` to **Terpsichore's ``fleet_emit``**
over Hades when ``HADES_URL`` is set — the R9 CQRS write path. That was the
last route keeping the retired monolith's :8101 surface load-bearing. With
``HADES_URL`` unset it still falls back to phdb's HTTP ``/emit`` (#720), the
same symmetry the entity- and document-write routers use.

This module keeps the per-type payload contract (``validate_atom``) that the
raw ``fleet_emit`` verb does not enforce — a caller typo surfaces here rather
than landing silently in the event blob.

Boundary discipline:

* **Write-only.** This module only emits ``session_events`` rows. All reads,
  identity resolution, and the typed-graph live in the sovereign stars.
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
    """The structured echo returned for a successful atom emit.

    Exactly one identifier is populated, depending on which write path ran:
    ``event_id`` on the legacy phdb ``/emit`` route (a synchronous row id), or
    ``born_token`` on the Terpsichore fleet plane (a content-derived handle —
    the async CQRS path has no row id to return). The other stays ``None``.
    """

    atom_type: str
    event_id: int | None
    ts: str | None
    born_token: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Return this result as a JSON-serializable dict."""
        return {
            "ok": True,
            "atom_type": self.atom_type,
            "event_id": self.event_id,
            "born_token": self.born_token,
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
    if unknown:
        raise AtomError(
            f"{atom_type} payload has unknown field(s) {sorted(unknown)}; "
            f"allowed: {sorted(allowed)}"
        )
    missing = _ATOM_REQUIRED[atom_type] - keys
    if missing:
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
    """Emit an atom through the injected ``/emit`` poster.

    vault-mcp is a pure client and never opens a store directly — which broke
    once phdb's SQLite went read-only at cutover, and pinned writes to SQLite
    besides. ``post`` is injected (the real adapter is the server's
    ``_phdb_post``), so the logic is unit-testable with a fake and the
    transport can be restrangled without touching this logic. Since C1 that
    adapter routes to Terpsichore's ``fleet_emit``.

    Validation runs first, so a bad payload is rejected (``AtomError``) before any
    network call. Raises ``PhdbUnavailableError`` when the route is unreachable,
    returns a non-success envelope, or confirms the write with neither an
    ``event_id`` nor a ``born_token``.
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
            str(result.get("error", "/emit returned no success flag"))
        )
    event_id = result.get("event_id")
    born_token = result.get("born_token")
    # Either identifier proves the write landed: phdb answered with a row id,
    # or Terpsichore answered with a content-derived token. Neither means the
    # write is unconfirmed, so refuse rather than report a phantom success.
    if not isinstance(event_id, int) and not isinstance(born_token, str):
        raise PhdbUnavailableError(
            f"/emit returned neither event_id nor born_token: {result}"
        )
    return AtomResult(
        atom_type=atom_type,
        event_id=event_id if isinstance(event_id, int) else None,
        ts=event_ts,
        born_token=born_token if isinstance(born_token, str) else None,
    )
