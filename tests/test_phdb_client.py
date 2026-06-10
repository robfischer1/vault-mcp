"""Unit tests for vault_mcp.phdb_client — the atom-emit write surface.

Atom Emit (#138): per-type payload contracts (#140) and the emit envelope
POSTed to phdb's HTTP ``/emit`` route (#720). vault-mcp is a pure client — it
no longer opens phdb's DB — so the poster is injected and faked here: no
SQLite, no live phdb. The session_events INSERT + column contract now live in
phdb's emit_event (tested there).
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from vault_mcp.phdb_client import (
    AtomError,
    AtomResult,
    PhdbUnavailableError,
    emit_atom,
    validate_atom,
)


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


class _FakePoster:
    """Records each (endpoint, payload) call; returns a canned result."""

    def __init__(self, result: dict[str, Any]) -> None:
        self.result = result
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def __call__(self, endpoint: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((endpoint, payload))
        return self.result


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


class TestEmit:
    def test_posts_emit_envelope(self):
        poster = _FakePoster({"ok": True, "event_id": 7, "deduped": False})
        result = emit_atom("decision", {"polarity": "for"}, post=poster)
        assert result == AtomResult(atom_type="decision", event_id=7, ts=None)
        assert len(poster.calls) == 1
        endpoint, body = poster.calls[0]
        assert endpoint == "/emit"
        assert body["event_type"] == "decision"
        assert body["payload"] == {"polarity": "for"}
        assert body["ts"] is None

    def test_captured_when_becomes_ts(self):
        poster = _FakePoster({"ok": True, "event_id": 1})
        result = emit_atom("pushback", _valid_payload("pushback"), post=poster)
        assert result.ts == "2026-06-01T12:00:00Z"
        assert poster.calls[0][1]["ts"] == "2026-06-01T12:00:00Z"

    def test_explicit_ts_overrides_captured_when(self):
        poster = _FakePoster({"ok": True, "event_id": 2})
        result = emit_atom(
            "tension", _valid_payload("tension"), ts="2026-06-02T00:00:00Z", post=poster
        )
        assert result.ts == "2026-06-02T00:00:00Z"
        assert poster.calls[0][1]["ts"] == "2026-06-02T00:00:00Z"

    def test_decision_has_no_ts(self):
        poster = _FakePoster({"ok": True, "event_id": 3})
        result = emit_atom("decision", {"polarity": "against"}, post=poster)
        assert result.ts is None

    def test_poster_failure_raises_unavailable(self):
        poster = _FakePoster({"ok": False, "error": "phdb unreachable: boom"})
        with pytest.raises(PhdbUnavailableError) as exc:
            emit_atom("decision", {"polarity": "for"}, post=poster)
        assert "boom" in str(exc.value)

    def test_missing_event_id_raises_unavailable(self):
        poster = _FakePoster({"ok": True})  # success flag but no id
        with pytest.raises(PhdbUnavailableError):
            emit_atom("decision", {"polarity": "for"}, post=poster)

    def test_bad_payload_raises_before_post(self):
        poster = _FakePoster({"ok": True, "event_id": 9})
        with pytest.raises(AtomError):
            emit_atom("decision", {"bad": 1}, post=poster)
        assert poster.calls == []  # validation fails before the POST
