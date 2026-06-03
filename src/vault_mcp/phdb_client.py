"""phdb write client — the atom-emit write surface into personal-history-db.

The ``atom`` lifecycle verb records an AI-observed *atom* (a decision,
reversal, tension, or pushback) directly into phdb's ``session_events`` table,
skipping the vault filesystem entirely. phdb is a *sibling* package (not a
dependency of the core server): every phdb touch resolves through
``PHDB_DB_PATH`` and degrades gracefully when unset/missing, exactly like the
REST-backed tools degrade without Obsidian (Constitution II).

Boundary discipline:

* **Write-only.** This module only inserts ``session_events`` rows. All
  reads, identity resolution, and the typed-graph live in phdb.
* **Lock-coordinated.** Writes acquire phdb's own ``write_lock`` (the real
  one, imported from phdb — a *replicated* lock would not mutually exclude
  with phdb's ingest/embed writers and would reintroduce ``SQLITE_BUSY``).
* **Pure core, coupled edge.** Validation + the INSERT (``insert_atom``) take
  a plain ``sqlite3.Connection`` and are unit-tested against a temp DB; only
  ``emit_atom`` imports phdb to open + lock the live connection.
"""

from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path
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
        {"reverses", "trigger", "position_before", "position_after", "captured_when"}
    ),
    "tension": frozenset(
        {"position_a", "position_b", "held_since", "resolution", "captured_when"}
    ),
    "pushback": frozenset(
        {"from", "challenge", "response", "position_changed", "captured_when"}
    ),
}

ATOM_TYPES: frozenset[str] = frozenset(_ATOM_REQUIRED)

PHDB_PATH_ENV_VAR = "PHDB_DB_PATH"


class AtomError(Exception):
    """An atom type is unknown or its payload violates the per-type contract."""


class PhdbUnavailableError(Exception):
    """phdb is not reachable (path unset/missing, or the package is absent)."""


class PhdbBusyError(Exception):
    """Another phdb writer holds the write lock; the emit was not performed."""


@dataclass(frozen=True)
class AtomResult:
    """The structured echo returned for a successful atom emit."""

    atom_type: str
    event_id: int
    ts: str | None

    def to_dict(self) -> dict[str, Any]:
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
        raise AtomError(f"{atom_type} payload missing required field(s): {sorted(missing)}")
    return dict(payload)


def _event_ts(payload: dict[str, Any], ts: str | None) -> str | None:
    """Resolve the event timestamp: explicit ``ts`` wins, else ``captured_when``."""
    if ts is not None:
        return ts
    captured = payload.get("captured_when")
    return captured if isinstance(captured, str) and captured != "" else None


def insert_atom(
    conn: sqlite3.Connection,
    atom_type: str,
    payload: object,
    *,
    ts: str | None = None,
) -> AtomResult:
    """Validate ``payload`` and INSERT one ``session_events`` row via ``conn``.

    The validated payload is stored as a deterministic JSON blob in the
    ``payload`` column; ``event_type`` is the atom type and ``ts`` is the event
    time (explicit arg or the payload's ``captured_when``). The caller owns the
    transaction (``commit``) and connection lifecycle.
    """
    validated = validate_atom(atom_type, payload)
    event_ts = _event_ts(validated, ts)
    blob = json.dumps(validated, sort_keys=True, ensure_ascii=False)
    cur = conn.execute(
        "INSERT INTO session_events (event_type, ts, payload) VALUES (?, ?, ?)",
        (atom_type, event_ts, blob),
    )
    event_id = cur.lastrowid
    assert event_id is not None  # INSERT always yields a rowid on this table
    return AtomResult(atom_type=atom_type, event_id=event_id, ts=event_ts)


def _resolve_db_path(db_path: str | Path | None) -> Path | None:
    """Resolve the phdb path from an explicit arg or ``PHDB_DB_PATH``.

    The env var is read at call time (never bound as a default), so runtime and
    test overrides take effect. Returns ``None`` when no usable path exists.
    """
    raw = db_path if db_path is not None else os.environ.get(PHDB_PATH_ENV_VAR)
    if raw is None or str(raw) == "":
        return None
    path = Path(raw)
    return path if path.exists() else None


def emit_atom(
    atom_type: str,
    payload: object,
    *,
    ts: str | None = None,
    db_path: str | Path | None = None,
) -> AtomResult:
    """Emit an atom into the live phdb ``session_events`` table, lock-coordinated.

    Resolves ``PHDB_DB_PATH``, acquires phdb's cross-process ``write_lock``,
    opens a pragma-configured read-write connection (busy_timeout=30s,
    foreign_keys=ON, WAL), inserts the atom, and commits. Validation happens
    first, so a bad payload is rejected (``AtomError``) before any lock or
    connection is taken.

    Raises ``PhdbUnavailableError`` when phdb is unreachable and
    ``PhdbBusyError`` when another phdb writer holds the lock.
    """
    validate_atom(atom_type, payload)  # fail fast, before touching phdb
    path = _resolve_db_path(db_path)
    if path is None:
        raise PhdbUnavailableError(
            f"{PHDB_PATH_ENV_VAR} is not set or the database file does not exist"
        )

    try:
        from phdb.core.db import connect
        from phdb.writelock import WriteLockError, write_lock
    except ImportError as exc:  # phdb package not on the path
        raise PhdbUnavailableError(f"phdb package is not importable: {exc}") from exc

    try:
        with write_lock(path), connect(path) as conn:
            result = insert_atom(conn, atom_type, payload, ts=ts)
            conn.commit()
    except WriteLockError as exc:
        raise PhdbBusyError(str(exc)) from exc
    return result
