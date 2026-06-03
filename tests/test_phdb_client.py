"""Unit tests for vault_mcp.phdb_client — the atom-emit write surface.

Covers Atom Emit (#138): per-type payload contracts (#140), the
session_events INSERT (#139), graceful degradation when phdb is unreachable
(Constitution II), and a guard against phdb session_events column drift.
Per Constitution III, the DB is always a temp/in-memory SQLite — never a
live phdb instance.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

# Make the sibling phdb package importable when present (mirrors server.py's
# runtime injection), so the drift guard + real-lock e2e actually run here.
# importorskip still skips them gracefully where phdb is absent (CI / publish).
_phdb_src = ROOT.parent / "personal-history-db" / "src"
if _phdb_src.is_dir() and str(_phdb_src) not in sys.path:
    sys.path.insert(0, str(_phdb_src))

from vault_mcp.phdb_client import (  # noqa: E402
    AtomError,
    PhdbUnavailableError,
    emit_atom,
    insert_atom,
    validate_atom,
)

# Faithful to phdb migration 0032_session_tables.sql. FK REFERENCES are kept
# for fidelity; with foreign_keys off (sqlite default) and NULL FK columns,
# they are inert.
SESSION_EVENTS_DDL = """
CREATE TABLE session_events (
    id              INTEGER PRIMARY KEY,
    schema_type     TEXT NOT NULL DEFAULT 'SessionEvent',
    session_id      INTEGER REFERENCES sessions(id),
    event_type      TEXT NOT NULL,
    ts              TEXT,
    payload         TEXT,
    file_path       TEXT,
    commit_sha      TEXT,
    raw_hash        TEXT,
    source_file_id  INTEGER REFERENCES source_files(id),
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
"""


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.executescript(SESSION_EVENTS_DDL)
    return conn


def _valid_payload(atom_type: str) -> dict[str, object]:
    return {
        "decision": {"polarity": "for", "reversed_by": None},
        "reversal": {
            "reverses": "decision-42",
            "trigger": "new evidence",
            "position_before": "ship it",
            "position_after": "hold",
            "captured_when": "2026-06-01T12:00:00Z",
        },
        "tension": {
            "position_a": "speed",
            "position_b": "safety",
            "held_since": "2026-05-01",
            "resolution": None,
            "captured_when": "2026-06-01T12:00:00Z",
        },
        "pushback": {
            "from": "Rob",
            "challenge": "is this the right layer?",
            "response": "moved to phdb",
            "position_changed": True,
            "captured_when": "2026-06-01T12:00:00Z",
        },
    }[atom_type]


class TestValidation:
    @pytest.mark.parametrize("atom_type", ["decision", "reversal", "tension", "pushback"])
    def test_valid_payload_passes(self, atom_type):
        out = validate_atom(atom_type, _valid_payload(atom_type))
        assert out == _valid_payload(atom_type)

    def test_unknown_atom_type_rejected(self):
        with pytest.raises(AtomError) as exc:
            validate_atom("musing", {"polarity": "for"})
        assert "musing" in str(exc.value)

    def test_non_mapping_payload_rejected(self):
        with pytest.raises(AtomError):
            validate_atom("decision", ["polarity"])

    def test_missing_required_field_rejected(self):
        with pytest.raises(AtomError) as exc:
            validate_atom("tension", {"position_a": "speed"})  # missing position_b
        assert "position_b" in str(exc.value)

    def test_unknown_field_rejected(self):
        with pytest.raises(AtomError) as exc:
            validate_atom("decision", {"polarity": "for", "typo_field": 1})
        assert "typo_field" in str(exc.value)


class TestInsert:
    def test_inserts_session_event_row(self):
        conn = _conn()
        result = insert_atom(conn, "decision", {"polarity": "for"})
        row = conn.execute(
            "SELECT event_type, ts, payload FROM session_events WHERE id = ?",
            (result.event_id,),
        ).fetchone()
        assert row[0] == "decision"
        assert json.loads(row[2]) == {"polarity": "for"}

    def test_captured_when_becomes_ts(self):
        conn = _conn()
        result = insert_atom(conn, "pushback", _valid_payload("pushback"))
        assert result.ts == "2026-06-01T12:00:00Z"
        row = conn.execute(
            "SELECT ts FROM session_events WHERE id = ?", (result.event_id,)
        ).fetchone()
        assert row[0] == "2026-06-01T12:00:00Z"

    def test_explicit_ts_overrides_captured_when(self):
        conn = _conn()
        result = insert_atom(
            conn, "tension", _valid_payload("tension"), ts="2026-06-02T00:00:00Z"
        )
        assert result.ts == "2026-06-02T00:00:00Z"

    def test_decision_has_no_ts(self):
        conn = _conn()
        result = insert_atom(conn, "decision", {"polarity": "against"})
        assert result.ts is None

    def test_payload_json_is_deterministic(self):
        conn = _conn()
        r1 = insert_atom(conn, "pushback", _valid_payload("pushback"))
        r2 = insert_atom(conn, "pushback", _valid_payload("pushback"))
        rows = conn.execute(
            "SELECT payload FROM session_events WHERE id IN (?, ?)",
            (r1.event_id, r2.event_id),
        ).fetchall()
        assert rows[0][0] == rows[1][0]  # sorted-keys JSON is byte-stable

    def test_invalid_payload_inserts_nothing(self):
        conn = _conn()
        with pytest.raises(AtomError):
            insert_atom(conn, "decision", {"wrong": 1})
        count = conn.execute("SELECT COUNT(*) FROM session_events").fetchone()[0]
        assert count == 0


class TestEmitGracefulDegradation:
    def test_unset_path_raises_unavailable(self, monkeypatch):
        monkeypatch.delenv("PHDB_DB_PATH", raising=False)
        with pytest.raises(PhdbUnavailableError):
            emit_atom("decision", {"polarity": "for"})

    def test_missing_file_raises_unavailable(self, tmp_path, monkeypatch):
        monkeypatch.delenv("PHDB_DB_PATH", raising=False)
        ghost = tmp_path / "nope.db"
        with pytest.raises(PhdbUnavailableError):
            emit_atom("decision", {"polarity": "for"}, db_path=ghost)

    def test_bad_payload_fails_before_path_check(self, monkeypatch):
        # Validation runs first: a bad payload is an AtomError, not Unavailable,
        # even with no phdb configured.
        monkeypatch.delenv("PHDB_DB_PATH", raising=False)
        with pytest.raises(AtomError):
            emit_atom("decision", {"bad": 1})


class TestEmitEndToEnd:
    def test_emit_against_temp_db_uses_real_lock(self, tmp_path, monkeypatch):
        # Exercises the real phdb write_lock + connect path against a TEMP db
        # (never live phdb). Skips when phdb isn't importable.
        pytest.importorskip("phdb.writelock")
        pytest.importorskip("phdb.core.db")
        db = tmp_path / "phdb.db"
        conn = sqlite3.connect(str(db))
        # phdb's connect() sets foreign_keys=ON, so the referenced parent
        # tables must exist (live phdb has them via migrations).
        conn.executescript(
            "CREATE TABLE sessions (id INTEGER PRIMARY KEY);\n"
            "CREATE TABLE source_files (id INTEGER PRIMARY KEY);\n"
            + SESSION_EVENTS_DDL
        )
        conn.commit()
        conn.close()

        monkeypatch.setenv("PHDB_DB_PATH", str(db))
        result = emit_atom("decision", {"polarity": "for"})
        assert result.atom_type == "decision"

        check = sqlite3.connect(str(db))
        row = check.execute(
            "SELECT event_type, payload FROM session_events WHERE id = ?",
            (result.event_id,),
        ).fetchone()
        check.close()
        assert row[0] == "decision"
        assert json.loads(row[1]) == {"polarity": "for"}
        assert not (db.with_suffix(db.suffix + ".phdb.lock")).exists()  # lock released


class TestSchemaDriftGuard:
    def test_write_columns_subset_of_phdb_session_events(self):
        # Guards against phdb session_events column drift (cf. the Core Data
        # column-drift lesson): the columns we INSERT must exist in phdb's
        # canonical schema-as-code. Static check — no live DB.
        canonical = pytest.importorskip("phdb.schemas.canonical")
        names = {f.name for f in canonical.SessionEvent.fields}
        assert {"event_type", "ts", "payload"} <= names
