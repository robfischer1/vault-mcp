"""Materialize reverse-symmetry (C6) — un-dissolve READS from Calliope, not phdb.

Proves the ``_read_dissolved_row`` repoint: with ``HADES_URL`` set, a
``documents`` read routes to Calliope's ``read_documents`` and normalizes the
row (title->subject); a miss / tool-failure map to structured errors; and with
``HADES_URL`` unset the legacy phdb HTTP read is used (never Calliope).
"""

from __future__ import annotations

import os
from typing import Any

import pytest

pytest.importorskip("mcp", reason="requires the 'server' extra (mcp)")

os.environ.setdefault("VAULT_MCP_PATH", ".")
from vault_mcp import hades_client, server


def test_documents_read_routes_to_calliope_when_hades_set(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    def fake_read_document(doc_id: int, **_kwargs: Any) -> dict[str, Any]:
        captured["doc_id"] = doc_id
        return {
            "documents": [
                {
                    "id": doc_id,
                    "title": "The Dissolved Note",
                    "schema_type": "DigitalDocument",
                    "body_text": "verbatim prose",
                    "source_path": "Brain Soup/Note.md",
                }
            ]
        }

    monkeypatch.setattr(server, "HADES_URL", "http://nas01:8101")
    monkeypatch.setattr(hades_client, "read_document", fake_read_document)

    out = server._read_dissolved_row("documents", 42)
    assert out["ok"] is True
    assert captured["doc_id"] == 42
    # Normalized to the phdb documents-row shape the mapper expects.
    assert out["row"]["subject"] == "The Dissolved Note"
    assert out["row"]["body_text"] == "verbatim prose"


def test_calliope_miss_maps_to_row_not_found(monkeypatch) -> None:
    monkeypatch.setattr(server, "HADES_URL", "http://nas01:8101")
    monkeypatch.setattr(
        hades_client, "read_document", lambda *_a, **_k: {"documents": []}
    )
    out = server._read_dissolved_row("documents", 7)
    assert out["ok"] is False
    assert out["error"] == "row_not_found"


def test_calliope_tool_failure_maps_to_structured_error(monkeypatch) -> None:
    monkeypatch.setattr(server, "HADES_URL", "http://nas01:8101")
    monkeypatch.setattr(
        hades_client,
        "read_document",
        lambda *_a, **_k: {"ok": False, "error": "no star serves verb"},
    )
    out = server._read_dissolved_row("documents", 7)
    assert out["ok"] is False
    assert out["error"] == "calliope_read_failed"


def test_hades_unset_falls_back_to_phdb_not_calliope(monkeypatch) -> None:
    # With HADES_URL unset, documents must NOT route to Calliope.
    def boom(*_a: Any, **_k: Any) -> dict[str, Any]:
        raise AssertionError(
            "read_document must not be called when HADES_URL unset"
        )

    monkeypatch.setattr(server, "HADES_URL", "")
    monkeypatch.setattr(hades_client, "read_document", boom)

    class _Resp:
        status_code = 200

        @staticmethod
        def json() -> dict[str, Any]:
            return {"row": {"subject": "phdb row"}, "paired_body": None}

    import httpx

    monkeypatch.setattr(httpx, "get", lambda *_a, **_k: _Resp())
    out = server._read_dissolved_row("documents", 1)
    assert out["ok"] is True
    assert out["row"]["subject"] == "phdb row"
