"""Hades write client — the constellation seam for strangled phdb concerns.

As phdb concerns strangle onto sovereign stars, their write paths leave
phdb's plain-HTTP surface and become MCP verbs behind the Hades gateway.
This module is the minimal MCP-over-streamable-HTTP caller vault-mcp uses
to reach them: one ``initialize`` + one ``tools/call`` per write (stateless
by design — entity writes are dissolve-time rare, so a per-call handshake
costs nothing that matters and holds no session to go stale).

Boundary discipline mirrors ``phdb_client``:

* **Pure core, injected edge.** Result parsing (``parse_tool_result``) and
  payload mapping are pure; the HTTP transport is injected so the logic is
  unit-tested with fakes and the MCP layer wires the real ``httpx`` poster.
* **Never raises across the verb boundary.** An unreachable gateway, a
  non-200, or an ``isError`` tool result all become ``{"ok": False,
  "error": ...}`` so a dissolve halts before deleting anything.

First strangled concern routed here: **entities-write** — ``/write/entity``
payloads become ``harmonia_write_entity_typed`` calls (2026-07-02).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

#: POST (url, headers, json_body) -> (status_code, response_text)
Transport = Callable[[str, dict[str, str], dict[str, Any]], tuple[int, str]]

_PROTOCOL_VERSION = "2024-11-05"


def _default_transport(
    url: str, headers: dict[str, str], body: dict[str, Any]
) -> tuple[int, str]:
    """POST one JSON-RPC message; return (status, text). Edge — not unit-tested."""
    import httpx

    resp = httpx.post(url, headers=headers, json=body, timeout=30.0)
    return resp.status_code, resp.text


def _decode_rpc(text: str) -> dict[str, Any]:
    """Decode a streamable-HTTP response body — bare JSON or SSE ``data:`` lines."""
    for line in text.splitlines():
        if line.startswith("data:"):
            decoded: dict[str, Any] = json.loads(line[5:])
            return decoded
    if not text.strip():
        return {}
    decoded_body: dict[str, Any] = json.loads(text)
    return decoded_body


def parse_tool_result(rpc: dict[str, Any]) -> dict[str, Any]:
    """Map one ``tools/call`` JSON-RPC response onto the ``{ok, ...}`` contract.

    Pure. A JSON-RPC ``error`` or an ``isError`` tool result becomes
    ``{"ok": False, "error": ...}``; otherwise the tool's structured content
    (or its JSON text content) is returned verbatim — the star's own result
    contract passes through.
    """
    if "error" in rpc:
        return {"ok": False, "error": f"hades rpc error: {rpc['error']}"}
    result = rpc.get("result", {})
    content = result.get("content") or []
    text = content[0].get("text", "") if content else ""
    if result.get("isError"):
        return {"ok": False, "error": text or "tool call failed"}
    structured = result.get("structuredContent")
    if structured is not None:
        return dict(structured)
    try:
        return dict(json.loads(text))
    except (ValueError, TypeError):
        return {"ok": False, "error": f"unparseable tool result: {text[:200]}"}


def call_verb(
    verb: str,
    arguments: dict[str, Any],
    *,
    url: str,
    token: str,
    transport: Transport | None = None,
) -> dict[str, Any]:
    """Call one Hades verb (initialize + tools/call); structured result.

    Never raises: transport faults and non-200s map to ``{"ok": False,
    "error": ...}``.
    """
    post = transport or _default_transport
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "Authorization": f"Bearer {token}",
    }
    init = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": _PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "vault-mcp", "version": "0"},
        },
    }
    call = {
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/call",
        "params": {"name": verb, "arguments": arguments},
    }
    try:
        status, _ = post(url, headers, init)
        if status != 200:
            return {"ok": False, "error": f"hades initialize HTTP {status}"}
        status, text = post(url, headers, call)
    except Exception as e:  # noqa: BLE001 — transport faults must not cross the verb boundary.
        return {"ok": False, "error": f"hades unreachable: {e}"}
    if status != 200:
        return {"ok": False, "error": f"hades HTTP {status}: {text[:200]}"}
    try:
        return parse_tool_result(_decode_rpc(text))
    except ValueError as e:
        return {"ok": False, "error": f"hades response undecodable: {e}"}


def write_entity_typed(
    payload: dict[str, Any],
    *,
    url: str,
    token: str,
    transport: Transport | None = None,
) -> dict[str, Any]:
    """Route one ``/write/entity`` payload to ``harmonia_write_entity_typed``.

    The payload shape is the phdb HTTP contract the translator already
    emits — ``{schema_type, source_path, fields, file_path?}`` — and the
    Harmonia verb takes exactly those parameters, so this is a passthrough.
    """
    args = {
        "schema_type": payload.get("schema_type"),
        "source_path": payload.get("source_path"),
        "fields": payload.get("fields") or {},
        "file_path": payload.get("file_path"),
    }
    return call_verb(
        "harmonia_write_entity_typed",
        args,
        url=url,
        token=token,
        transport=transport,
    )


def write_document(
    payload: dict[str, Any],
    *,
    url: str,
    token: str,
    transport: Transport | None = None,
) -> dict[str, Any]:
    """Route one ``/write/document`` payload to Calliope's ``write_document``.

    The payload shape is the phdb HTTP contract the translator already
    emits — ``{source_path, body_text, schema_type?, subject?, file_path?,
    mtime?, ctime?}`` — and the Calliope verb takes exactly those parameters
    (C3, the prose strangle), so this is a passthrough. ``None`` values are
    dropped: the verb's optional fields are absent-or-string.
    """
    args = {k: v for k, v in payload.items() if v is not None}
    return call_verb(
        "write_document",
        args,
        url=url,
        token=token,
        transport=transport,
    )


def read_document(
    doc_id: int,
    *,
    url: str,
    token: str,
    transport: Transport | None = None,
) -> dict[str, Any]:
    """Read one dissolved document back from Calliope's ``read_documents`` (C6).

    The reverse (materialize / un-dissolve) leg: it reads the go-forward prose
    store — **Calliope**, not the retired phdb — so un-dissolve is symmetric
    with dissolve. Returns the star's ``{documents: [...]}`` contract on success
    (a miss is an empty list) or ``{ok: False, error}`` on any transport / tool
    failure. ``call_verb`` never raises across the boundary.
    """
    return call_verb(
        "read_documents",
        {"id": doc_id},
        url=url,
        token=token,
        transport=transport,
    )


def emit_session_event(
    payload: dict[str, Any],
    *,
    url: str,
    token: str,
    transport: Transport | None = None,
) -> dict[str, Any]:
    """Route one ``/emit`` payload to Terpsichore's ``fleet_emit`` (C1).

    The payload shape is the phdb HTTP contract ``phdb_client`` already emits
    — ``{event_type, payload, ts}`` — and the Terpsichore verb takes exactly
    those parameters, so this is a passthrough like its sibling routers.

    ``fleet_emit`` is the R9 CQRS write path: it lands the ``session_events``
    row on the fleet plane and returns a content-derived ``born_token`` in
    place of phdb's synchronous ``event_id`` (the async plane has no row id to
    hand back). ``session_uuid`` is left unset, exactly as the phdb ``/emit``
    path did — vault-mcp is a service principal, not a session.
    """
    args = {
        "event_type": payload.get("event_type"),
        "payload": payload.get("payload") or {},
        "ts": payload.get("ts"),
    }
    return call_verb(
        "fleet_emit",
        args,
        url=url,
        token=token,
        transport=transport,
    )
