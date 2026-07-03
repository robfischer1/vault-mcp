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
