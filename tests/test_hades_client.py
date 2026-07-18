"""Tests for the Hades write client (the strangled-concern seam).

All offline — the transport is injected. Covers the pure result parsing
(structured content, text fallback, isError, rpc error), the never-raise
transport boundary, and the entity-payload -> verb-args mapping.
"""

from __future__ import annotations

import json
from typing import Any

from vault_mcp.hades_client import (
    call_verb,
    parse_tool_result,
    read_document,
    write_entity_typed,
)


def _rpc_result(result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": 2, "result": result}


# -- parse_tool_result (pure) --------------------------------------------------


def test_parse_prefers_structured_content() -> None:
    out = parse_tool_result(
        _rpc_result(
            {
                "content": [{"type": "text", "text": "{}"}],
                "structuredContent": {"ok": True, "id": 7, "table": "books"},
                "isError": False,
            }
        )
    )
    assert out == {"ok": True, "id": 7, "table": "books"}


def test_parse_falls_back_to_text_json() -> None:
    body = {"ok": True, "id": 3}
    out = parse_tool_result(
        _rpc_result({"content": [{"type": "text", "text": json.dumps(body)}]})
    )
    assert out == body


def test_parse_is_error_maps_to_ok_false() -> None:
    out = parse_tool_result(
        _rpc_result(
            {
                "content": [
                    {"type": "text", "text": "no star serves verb 'x'"}
                ],
                "isError": True,
            }
        )
    )
    assert out["ok"] is False
    assert "no star serves" in out["error"]


def test_parse_rpc_error_maps_to_ok_false() -> None:
    out = parse_tool_result(
        {"jsonrpc": "2.0", "id": 2, "error": {"code": -32600, "message": "bad"}}
    )
    assert out["ok"] is False
    assert "rpc error" in out["error"]


def test_parse_unparseable_text_maps_to_ok_false() -> None:
    out = parse_tool_result(
        _rpc_result({"content": [{"type": "text", "text": "not json"}]})
    )
    assert out["ok"] is False


# -- call_verb transport boundary ----------------------------------------------


class FakeTransport:
    """Scriptable transport: records calls, returns queued (status, text)."""

    def __init__(self, responses: list[tuple[int, str]]) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def __call__(
        self, url: str, headers: dict[str, str], body: dict[str, Any]
    ) -> tuple[int, str]:
        self.calls.append((url, body))
        assert headers["Authorization"].startswith("Bearer ")
        return self.responses.pop(0)


def _ok_pair(result: dict[str, Any]) -> list[tuple[int, str]]:
    return [
        (200, json.dumps({"jsonrpc": "2.0", "id": 1, "result": {}})),
        (200, "data:" + json.dumps(_rpc_result(result))),
    ]


def test_call_verb_initializes_then_calls() -> None:
    transport = FakeTransport(
        _ok_pair({"structuredContent": {"ok": True}, "content": []})
    )
    out = call_verb(
        "harmonia_ping",
        {},
        url="http://h/mcp/",
        token="tok",
        transport=transport,
    )
    assert out == {"ok": True}
    assert [b["method"] for _, b in transport.calls] == [
        "initialize",
        "tools/call",
    ]


def test_call_verb_transport_fault_never_raises() -> None:
    def boom(
        url: str, headers: dict[str, str], body: dict[str, Any]
    ) -> tuple[int, str]:
        raise OSError("connection refused")

    out = call_verb("x", {}, url="http://h/mcp/", token="t", transport=boom)
    assert out["ok"] is False
    assert "unreachable" in out["error"]


def test_call_verb_non_200_maps_to_ok_false() -> None:
    transport = FakeTransport([(200, "{}"), (503, "down")])
    out = call_verb(
        "x", {}, url="http://h/mcp/", token="t", transport=transport
    )
    assert out["ok"] is False
    assert "503" in out["error"]


# -- entity payload mapping ------------------------------------------------------


def test_write_entity_typed_maps_the_phdb_payload() -> None:
    transport = FakeTransport(
        _ok_pair(
            {
                "structuredContent": {
                    "ok": True,
                    "table": "books",
                    "id": 1,
                    "deduped": False,
                    "source_file_id": 2,
                },
                "content": [],
            }
        )
    )
    payload = {
        "schema_type": "Book",
        "source_path": "Entities/Books/Dune.md",
        "fields": {"name": "Dune", "author": "Frank Herbert"},
        "file_path": "Entities/Books/Dune.md",
    }
    out = write_entity_typed(
        payload, url="http://h/mcp/", token="t", transport=transport
    )
    assert out["ok"] is True
    assert out["table"] == "books"
    _, call_body = transport.calls[1]
    assert call_body["params"]["name"] == "harmonia_write_entity_typed"
    assert call_body["params"]["arguments"]["schema_type"] == "Book"
    assert (
        call_body["params"]["arguments"]["fields"]["author"] == "Frank Herbert"
    )


# -- read_document (the C6 reverse / materialize leg) ---------------------------


def test_read_document_calls_calliope_read_documents_by_id() -> None:
    transport = FakeTransport(
        _ok_pair(
            {
                "structuredContent": {
                    "documents": [
                        {"id": 42, "title": "Idea", "body_text": "the prose"}
                    ]
                },
                "content": [],
            }
        )
    )
    out = read_document(42, url="http://h/mcp/", token="t", transport=transport)
    assert out["documents"][0]["id"] == 42
    _, call_body = transport.calls[1]
    assert call_body["params"]["name"] == "read_documents"
    assert call_body["params"]["arguments"] == {"id": 42}


def test_read_document_transport_fault_never_raises() -> None:
    def boom(
        url: str, headers: dict[str, str], body: dict[str, Any]
    ) -> tuple[int, str]:
        raise OSError("connection refused")

    out = read_document(1, url="http://h/mcp/", token="t", transport=boom)
    assert out["ok"] is False
