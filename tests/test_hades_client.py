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
    emit_session_event,
    parse_tool_result,
    read_document,
    read_document_by_source_path,
    write_document,
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


# -- read_document (the materialize leg — F10: the _note container surface) ----


def test_read_document_materializes_by_container_id() -> None:
    transport = FakeTransport(
        _ok_pair(
            {
                "structuredContent": {
                    "container_id": "42",
                    "blocks": [
                        {"id": "s1", "text": "the prose", "orderKey": "a0"}
                    ],
                    "tags": [],
                    "provenance": {
                        "source_path": "Ideas/Idea.md",
                        "raw_hash": "h1",
                        "mtime": "2026-08-10T00:00:00Z",
                        "schema_type": "Note",
                        "title": "Idea",
                    },
                },
                "content": [],
            }
        )
    )
    out = read_document(42, url="http://h/mcp/", token="t", transport=transport)
    doc = out["documents"][0]
    assert doc["id"] == "42"
    assert doc["body_text"] == "the prose"
    assert doc["raw_hash"] == "h1"
    assert doc["schema_type"] == "Note"
    assert doc["subject"] == "Idea"
    _, call_body = transport.calls[1]
    assert call_body["params"]["name"] == "calliope_materialize_note"
    assert call_body["params"]["arguments"] == {"container_id": "42"}


def test_read_document_joins_blocks_on_the_markdown_separator() -> None:
    transport = FakeTransport(
        _ok_pair(
            {
                "structuredContent": {
                    "container_id": "7",
                    "blocks": [
                        {"id": "s1", "text": "one", "orderKey": "a0"},
                        {"id": "s2", "text": "two", "orderKey": "a1"},
                    ],
                    "tags": [],
                    "provenance": {"source_path": "a.md"},
                },
                "content": [],
            }
        )
    )
    out = read_document(7, url="http://h/mcp/", token="t", transport=transport)
    assert out["documents"][0]["body_text"] == "one\n\ntwo"


def test_read_document_miss_is_an_empty_list() -> None:
    transport = FakeTransport(
        _ok_pair(
            {
                "structuredContent": {
                    "error": "container_not_found",
                    "detail": "nope.md",
                },
                # The wire's isError text carries the error prefix — this is
                # what parse_tool_result maps into {ok: False, error}.
                "content": [
                    {"type": "text", "text": "container_not_found: nope.md"}
                ],
                "isError": True,
            }
        )
    )
    out = read_document_by_source_path(
        "nope.md", url="http://h/mcp/", token="t", transport=transport
    )
    assert out == {"documents": []}


def test_every_calliope_call_site_uses_the_star_prefixed_verb() -> None:
    """The U6 cutover's names, pinned at all three call sites.

    This is the regression that shipped: the gateway namespaced its verbs by
    star and these callers kept the bare ones, so every vault->store read and
    write returned ``unknown verb`` — invisibly, because ``call_verb`` maps a
    refusal to ``{ok: False, error}`` rather than raising. Measured 2026-08-13:
    a sweep of 176 WBS plans produced 176 errors and zero reads.

    The old test asserted ``"read_documents"`` while being NAMED for the
    prefixed verb, so the suite was green the entire time. Asserting every call
    site here is what makes that failure mode impossible to reintroduce one
    function at a time.
    """
    seen: dict[str, str] = {}

    def record() -> FakeTransport:
        return FakeTransport(_ok_pair({"structuredContent": {}, "content": []}))

    t_write = record()
    write_document(
        {"source_path": "a.md", "body_text": "x"},
        url="http://h/mcp/",
        token="t",
        transport=t_write,
    )
    seen["write_document"] = t_write.calls[1][1]["params"]["name"]

    t_by_id = record()
    read_document(1, url="http://h/mcp/", token="t", transport=t_by_id)
    seen["read_document"] = t_by_id.calls[1][1]["params"]["name"]

    t_by_path = record()
    read_document_by_source_path(
        "a.md", url="http://h/mcp/", token="t", transport=t_by_path
    )
    seen["read_document_by_source_path"] = t_by_path.calls[1][1]["params"]["name"]

    assert seen == {
        "write_document": "calliope_dissolve_note",
        "read_document": "calliope_materialize_note",
        "read_document_by_source_path": "calliope_materialize_note",
    }


def test_read_document_transport_fault_never_raises() -> None:
    def boom(
        url: str, headers: dict[str, str], body: dict[str, Any]
    ) -> tuple[int, str]:
        raise OSError("connection refused")

    out = read_document(1, url="http://h/mcp/", token="t", transport=boom)
    assert out["ok"] is False


# -- emit_session_event (the C1 atom leg) --------------------------------------


def test_emit_session_event_calls_fleet_emit() -> None:
    transport = FakeTransport(
        _ok_pair(
            {
                "structuredContent": {
                    "ok": True,
                    "born_token": "bt-1",
                    "event_type": "decision",
                    "session_uuid": None,
                },
                "content": [],
            }
        )
    )
    payload = {
        "event_type": "decision",
        "payload": {"polarity": "for"},
        "ts": "2026-08-03T00:00:00Z",
    }
    out = emit_session_event(
        payload, url="http://h/mcp/", token="t", transport=transport
    )
    assert out["ok"] is True
    assert out["born_token"] == "bt-1"
    _, call_body = transport.calls[1]
    assert call_body["params"]["name"] == "fleet_emit"
    args = call_body["params"]["arguments"]
    assert args["event_type"] == "decision"
    assert args["payload"] == {"polarity": "for"}
    assert args["ts"] == "2026-08-03T00:00:00Z"


def test_emit_session_event_defaults_missing_payload_to_empty() -> None:
    """A payload-less emit must send {}, never None, to the verb."""
    transport = FakeTransport(
        _ok_pair({"structuredContent": {"ok": True}, "content": []})
    )
    emit_session_event(
        {"event_type": "probe"},
        url="http://h/mcp/",
        token="t",
        transport=transport,
    )
    _, call_body = transport.calls[1]
    assert call_body["params"]["arguments"]["payload"] == {}
    assert call_body["params"]["arguments"]["ts"] is None


def test_emit_session_event_transport_fault_never_raises() -> None:
    def boom(
        url: str, headers: dict[str, str], body: dict[str, Any]
    ) -> tuple[int, str]:
        raise OSError("connection refused")

    out = emit_session_event(
        {"event_type": "decision"}, url="http://h/mcp/", token="t", transport=boom
    )
    assert out["ok"] is False
