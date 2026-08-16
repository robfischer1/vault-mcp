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

#: Calliope's document verbs, STAR-PREFIXED.
#:
#: The U6 data.fleet cutover namespaced every gateway verb by its star, and
#: these three call sites were never migrated — so every vault->store write
#: from this process has failed since, with ``unknown verb "read_documents"``.
#: Measured 2026-08-13: a `plan_freshness` sweep of 176 WBS plans returned 176
#: errors and zero reads, and Calliope holds copies of exactly 3 plans, all of
#: them written before the cutover. The failure was invisible because
#: ``call_verb`` never raises across the boundary — it maps the refusal to
#: ``{ok: False, error}``, which the callers record per-plan and continue past.
#:
#: Named constants rather than literals so the next rename greps to one place.
#:
#: F10 (Git for Ideas): the document family is GONE from the live surface —
#: the vault rides the ``_note`` container family instead. The write is
#: ``dissolve_note`` (calliope's non-destructive store write; the vault-side
#: delete stays vault-mcp's own step in lifecycle_verbs), the read is
#: ``materialize_note`` (one note by source_path: blocks + provenance).
_VERB_DISSOLVE_NOTE = "calliope_dissolve_note"
_VERB_MATERIALIZE_NOTE = "calliope_materialize_note"


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
    """Route one ``/write/document`` payload onto ``dissolve_note`` (F10).

    The payload keeps the phdb HTTP contract the translator emits —
    ``{source_path, body_text, schema_type?, subject?, file_path?, mtime?,
    ctime?}`` — and maps onto the ``_note`` container write: the whole body
    is ONE block (blocks are authored, never inferred — the 0.14
    de-inference rule), ``subject`` is the display title, and the
    provenance fields cross verbatim. The verb is non-destructive by
    construction; the vault-side delete stays :mod:`lifecycle_verbs`' own
    step, which is exactly the split the plan-freshness reconcile needs.

    The result is normalized back to the shape this seam always answered:
    ``{ok: True, table, id, deduped}`` on success (``deduped`` = the
    container-grain no-op — identical content wrote nothing), or the
    untouched ``{ok: False, error}``.
    """
    args: dict[str, Any] = {
        "source_path": payload.get("source_path"),
        "blocks": [{"text": payload.get("body_text") or ""}],
        "title": payload.get("subject"),
        "schema_type": payload.get("schema_type"),
        "file_path": payload.get("file_path"),
        "mtime": payload.get("mtime"),
        "ctime": payload.get("ctime"),
        "raw_hash": payload.get("raw_hash"),
    }
    args = {k: v for k, v in args.items() if v is not None}
    res = call_verb(
        _VERB_DISSOLVE_NOTE,
        args,
        url=url,
        token=token,
        transport=transport,
    )
    if res.get("ok") is False:
        return res
    return {
        "ok": True,
        "table": "notes",
        "id": res.get("node_id"),
        "deduped": res.get("generation") == "nooped",
        "generation": res.get("generation"),
    }


def _note_to_document_row(res: dict[str, Any]) -> dict[str, Any]:
    """Project one ``materialize_note`` answer onto the documents-row shape.

    The consumers (StoredCopy, the un-dissolve mapper) read
    ``{body_text, raw_hash, mtime, schema_type, subject, source_path}`` —
    all served by the note's blocks (joined on the markdown separator, the
    sink's own projection rule) and provenance attributes.
    """
    blocks = res.get("blocks") or []
    provenance = res.get("provenance") or {}
    return {
        "id": res.get("container_id"),
        "source_path": provenance.get("source_path"),
        "body_text": "\n\n".join(
            str(b.get("text") or "") for b in blocks if isinstance(b, dict)
        ),
        "raw_hash": provenance.get("raw_hash"),
        "mtime": provenance.get("mtime"),
        "ctime": provenance.get("ctime"),
        "schema_type": provenance.get("schema_type"),
        "subject": provenance.get("title"),
        "source_kind": provenance.get("source_kind"),
    }


def read_document(
    doc_id: int,
    *,
    url: str,
    token: str,
    transport: Transport | None = None,
) -> dict[str, Any]:
    """Read one dissolved note back by container id (F10: ``materialize_note``).

    The reverse (materialize / un-dissolve) leg, on the ``_note`` container
    surface. Answers the ``{documents: [...]}`` projection the consumers
    always read (a miss is an empty list); ``{ok: False, error}`` on any
    transport / tool failure. Never raises across the boundary.
    """
    res = call_verb(
        _VERB_MATERIALIZE_NOTE,
        {"container_id": str(doc_id)},
        url=url,
        token=token,
        transport=transport,
    )
    if res.get("ok") is False:
        if "container_not_found" in str(res.get("error", "")):
            return {"documents": []}
        return res
    return {"documents": [_note_to_document_row(res)]}


def read_document_by_source_path(
    source_path: str,
    *,
    url: str,
    token: str,
    transport: Transport | None = None,
) -> dict[str, Any]:
    """Read the stored copy for one ``source_path`` (F10: ``materialize_note``).

    The freshness leg's read, on the ``_note`` container surface.
    ``source_path`` is the durable handle (the note's identity name). The
    answer keeps the ``{documents: [...]}`` projection the caller reads —
    the newest (and only) materialized state first; a
    ``container_not_found`` miss is an empty list. Never raises across the
    boundary.
    """
    res = call_verb(
        _VERB_MATERIALIZE_NOTE,
        {"source_path": source_path},
        url=url,
        token=token,
        transport=transport,
    )
    if res.get("ok") is False:
        if "container_not_found" in str(res.get("error", "")):
            return {"documents": []}
        return res
    return {"documents": [_note_to_document_row(res)]}


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
