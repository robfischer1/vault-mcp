"""Materialize / compute-receiver / atom — the lifecycle write verbs.

Split out of server.py under vault-mcp#5294. A REGISTRATION MODULE.

`_read_dissolved_row` comes from verbs_plan rather than server.py: it moved
there with the plan-freshness helpers it was written beside, and this module is
its only other reader.
"""

from __future__ import annotations

import logging
from typing import Any

# IMPORTED AS A MODULE, NOT AS NAMES — deliberately.
#
# `from vault_mcp.server import HADES_URL` binds the VALUE at import time, so a
# test doing `monkeypatch.setattr(server, "HADES_URL", ...)` would patch a name
# this module never reads again. That is not hypothetical: it broke three
# test_carve_materialize tests the moment _read_dissolved_row moved out of
# server.py, and it fails as a confusing runtime error ("phdb unreachable")
# rather than as an import error.
#
# Qualifying every server-owned name keeps the indirection the tests rely on and
# makes the coupling visible at each use site.
from vault_mcp import server

# `mcp` IS imported directly, unlike everything else above. It is a singleton
# built once at server import and never rebound, so there is nothing for a test
# to patch — and qualifying it as `server.mcp` costs real type safety: mypy
# cannot resolve an attribute on a module that is still mid-import, so every
# decorated verb became "Cannot determine type of mcp" plus "Untyped decorator
# makes function untyped". 99 errors, entirely from that one indirection.
from vault_mcp.server import mcp
from vault_mcp.verbs_plan import _read_dissolved_row

log = logging.getLogger(__name__)


@mcp.tool()
def materialize(table: str, row_id: int) -> dict[str, Any]:
    """Materialize a dissolved row back into a Convention-Gate note (VDV F3).

    The inverse of ``dissolve``: reads a typed row from the go-forward prose
    store — **Calliope** (C6 reverse-symmetry), not the retired phdb — and writes
    it back through the shared ``Materializer`` with ``mode=COMPUTE``, the
    sanctioned path for materialize-only ``@type``s (e.g. ``Plan``) that ordinary
    agent-create rejects. The target directory is resolved from the note_type's
    schema route. Returns the Gate write result or a structured error.

    Shares the ``Materializer`` backend with ``compute_receiver`` (which renders a
    fresh compute payload); this verb builds the payload from a dissolved row.
    """
    from vault_mcp.translator import row_to_payload

    if table not in ("documents", "plans"):
        return {"ok": False, "error": "bad_table", "detail": table}

    # 1. Read the row from Calliope (documents) / phdb fallback (plans).
    read = _read_dissolved_row(table, row_id)
    if not read.get("ok"):
        return read
    row = read["row"]
    paired_body: str | None = read.get("paired_body")

    note_type = (
        "Plan"
        if table == "plans"
        else (row.get("schema_type") or "DigitalDocument")
    )

    try:
        # 2. Resolve the target directory from the note_type's schema route.
        directory = server._get_gate()._schema.resolve_directory(note_type)
        # 3. Build the Materializer payload and write via mode=COMPUTE.
        payload = row_to_payload(
            row, table, directory=directory, paired_body=paired_body
        )
        result = server._get_materializer().materialize(payload)
        return server._commit_write(result.to_dict(), "materialize")
    except Exception as exc:
        log.exception("materialize failed")
        return server._gate_error_envelope(exc)


@mcp.tool()
def compute_receive(
    payload: dict[str, Any], created: str | None = None
) -> dict[str, Any]:
    """Render a structured compute payload into an ai-computed vault note.

    Accepts a payload (template, title, directory, data, frontmatter) from a
    periodic compute job, renders it through a named template (pure
    substitution, no LLM), and writes it via the Gate's compute-only path.

    Returns:
        {"ok": True, "path", "frontmatter", "provenance"} or a structured error.

    """
    from vault_mcp.compute import ComputePayloadError

    try:
        result = server._get_compute_receiver().receive(
            payload, created=created
        )
        return server._commit_write(result.to_dict(), "compute")
    except ComputePayloadError as exc:
        return {"ok": False, "error": "bad_payload", "detail": str(exc)}
    except Exception as exc:
        log.exception("compute_receive failed")
        return server._gate_error_envelope(exc)


@mcp.tool()
def compute_receiver(
    payload: dict[str, Any], created: str | None = None
) -> dict[str, Any]:
    """Receive a compute result and write it as a durable note (Compute Receiver).

    Accepts a structured payload (title, note_type, directory, body, frontmatter,
    optional template/data) and writes it through the Gate with mode=COMPUTE —
    the sanctioned path for materialize-only @types, which the ordinary
    agent-create path rejects. Rendering is deterministic (no LLM): the same
    payload yields a byte-identical note.

    (Renamed from ``materialize`` — that verb now belongs to the VDV dissolve
    inverse, which rehydrates a dissolved phdb row back into a note.)

    Returns:
        {"ok": True, "path", "frontmatter", "provenance"} or a structured error.

    """
    from vault_mcp.lifecycle import MaterializePayloadError

    try:
        result = server._get_materializer().materialize(
            payload, created=created
        )
        return server._commit_write(result.to_dict(), "compute")
    except MaterializePayloadError as exc:
        return {"ok": False, "error": "bad_payload", "detail": str(exc)}
    except Exception as exc:
        log.exception("compute_receiver failed")
        return server._gate_error_envelope(exc)


@mcp.tool()
def atom(
    atom_type: str,
    payload: dict[str, Any],
    ts: str | None = None,
) -> dict[str, Any]:
    """Emit an AI-observed atom (decision/reversal/tension/pushback) to phdb.

    Records the atom directly into phdb's session_events table, skipping the
    vault filesystem entirely (lifecycle verb). The payload contract is
    per-type. When phdb is not configured (PHDB_DB_PATH unset or the DB file is
    missing), returns a structured 'phdb_unavailable' error rather than failing.

    Args:
        atom_type: One of 'decision', 'reversal', 'tension', 'pushback'.
        payload: Type-specific fields — decision: {polarity, reversed_by?};
            reversal: {reverses, position_before, position_after, trigger?,
            captured_when?}; tension: {position_a, position_b, held_since?,
            resolution?, captured_when?}; pushback: {from, challenge, response,
            position_changed?, captured_when?}.
        ts: Optional ISO-8601 event time; defaults to the payload's
            captured_when when present.

    Returns:
        {"ok": True, "atom_type", "event_id", "born_token", "ts"} or a
        structured error. Exactly one identifier is populated: ``born_token``
        on the Terpsichore fleet plane (the C1 default), ``event_id`` on the
        legacy phdb route when ``HADES_URL`` is unset.

    """
    from vault_mcp.phdb_client import (
        AtomError,
        PhdbUnavailableError,
        emit_atom,
    )

    try:
        result = emit_atom(atom_type, payload, ts=ts, post=server._phdb_post)
        return result.to_dict()
    except AtomError as exc:
        return {"ok": False, "error": "bad_payload", "detail": str(exc)}
    except PhdbUnavailableError as exc:
        return {"ok": False, "error": "phdb_unavailable", "detail": str(exc)}
