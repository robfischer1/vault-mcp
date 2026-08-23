"""The Convention Gate write surface — write_note, delete, move, lint, query, audit.

Split out of server.py under vault-mcp#5294. A REGISTRATION MODULE.

Every verb here goes through the Gate, which is why the group travels together
and why it reaches back into server.py for `server._get_gate` rather than building one:
the Gate is a lazy singleton and tests monkeypatch that global.
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

log = logging.getLogger(__name__)


@mcp.tool()
def write_note(
    title: str,
    note_type: str | None = None,
    pillar: str | None = None,
    body: str | None = None,
    tags: list[str] | None = None,
    fields: dict[str, Any] | None = None,
    actor: str = "agent",
    mode: str = "upsert",
    commit_message: str | None = None,
) -> dict[str, Any]:
    """Create-or-update a vault note through the Convention Gate — the write surface.

    Resolves the target path from title + routing, then creates a missing note or
    updates an existing one (the caller need not know which). The Gate generates
    correct frontmatter, validates against the closed tag glossary and per-type
    rules, enforces write-protection, and stamps provenance. On update, body/tags/
    fields left null are untouched; a pre-existing invalid value in a field this
    write does not touch surfaces as a warning, not a rejection.

    Args:
        title: Note title (also the filename).
        note_type: Schema note type used for routing (e.g., 'note').
        pillar: Schema pillar used for routing (e.g., 'Knowledge').
        body: Markdown body. Null on update leaves the existing body untouched.
        tags: Tags; each must be in the closed glossary. Null on update = untouched.
        fields: Extra frontmatter fields (required / constrained values, etc.).
        actor: 'agent' (default) or 'human' — drives the provenance stamp.
        mode: 'upsert' (default), 'create' (refuse if exists), 'update' (refuse if missing).
        commit_message: Commit message recording this write; auto-generated when omitted.

    Returns:
        {"ok": True, "path", "frontmatter", "created", "warnings", ...} or a structured error.

    """
    from vault_mcp.provenance import Actor

    try:
        gate = server._get_gate()
        result = gate.write_note(
            title=title,
            note_type=note_type,
            pillar=pillar,
            body=body,
            tags=tags,
            fields=fields,
            actor=Actor.HUMAN if actor == "human" else Actor.AGENT,
            mode=mode,
        )
        return server._commit_write(
            result.to_dict(),
            "create" if result.created else "update",
            commit_message,
        )
    except Exception as exc:
        log.exception("write_note failed")
        return server._gate_error_envelope(exc)


@mcp.tool()
def delete(
    path: str, actor: str = "agent", commit_message: str | None = None
) -> dict[str, Any]:
    """Move a vault note to Obsidian's .trash/ (reversible) through the Gate.

    Enforces the same write-protection as a write — an agent cannot trash a
    voice-only, compute-only, fully-immutable, or body-immutable note. The note
    remains recoverable from Obsidian's trash.

    Returns:
        {"ok": True, "path", "deleted": True} or a structured error.

    """
    from vault_mcp.provenance import Actor

    try:
        gate = server._get_gate()
        result = gate.delete(
            path, actor=Actor.HUMAN if actor == "human" else Actor.AGENT
        )
        committed = server._commit_write(
            result, "delete", commit_message, is_delete=True
        )
        if committed.get("ok"):
            abs_path = (server.VAULT_PATH / path).resolve()
            committed["dirs_pruned"] = server._prune_empty_parents(
                abs_path, server.VAULT_PATH
            )
        return committed
    except Exception as exc:
        log.exception("delete failed")
        return server._gate_error_envelope(exc)


@mcp.tool()
def move_note(
    src: str,
    dst: str,
    actor: str = "agent",
    commit_message: str | None = None,
) -> dict[str, Any]:
    """Move a vault note from src to dst, preserving content and frontmatter.

    Reads the source note, writes it to the destination path, then deletes the
    source. Checks write-protection on both directories. Does NOT update
    backlinks — use ``backlinks_to`` to enumerate inbound references and update
    them separately via ``write_note``.

    Args:
        src: Vault-relative source path (e.g. 'Software/README.md').
        dst: Vault-relative destination path (e.g. 'References/README.md').
        actor: 'agent' (default) or 'human'.
        commit_message: Optional git commit message.

    Returns:
        {"ok": True, "src", "dst", "moved": True} or a structured error.

    """
    from vault_mcp.provenance import Actor

    try:
        gate = server._get_gate()
        result = gate.move_note(
            src,
            dst,
            actor=Actor.HUMAN if actor == "human" else Actor.AGENT,
        )
        committed = server._commit_write(result, "move", commit_message)
        if committed.get("ok"):
            abs_src = (server.VAULT_PATH / src).resolve()
            committed["dirs_pruned"] = server._prune_empty_parents(
                abs_src, server.VAULT_PATH
            )
        return committed
    except Exception as exc:
        log.exception("move_note failed")
        return server._gate_error_envelope(exc)


@mcp.tool()
def lint(
    title: str = "",
    note_type: str | None = None,
    pillar: str | None = None,
    body: str = "",
    tags: list[str] | None = None,
    fields: dict[str, Any] | None = None,
    actor: str = "agent",
) -> dict[str, Any]:
    """Dry-run a note payload through the Convention Gate's validator — no write.

    Runs the full validation pipeline and returns every finding at once
    (collect-all), so a caller can assemble -> lint -> fix -> write without
    touching disk. Each finding carries a code, the offending field/value, a
    message, and a severity ('error' blocks a write; 'warning' is advisory).

    Args:
        title: Note title (also the filename).
        note_type: Schema note type used for routing.
        pillar: Schema pillar used for routing.
        body: Markdown body.
        tags: Tags to validate against the closed glossary.
        fields: Extra frontmatter fields (e.g. required/constrained values).
        actor: 'agent' (default) or 'human'.

    Returns:
        {"ok": bool, "errors": [...], "warnings": [...]} or a structured error.

    """
    from vault_mcp.provenance import Actor

    try:
        gate = server._get_gate()
        return gate.lint_payload(
            title=title,
            note_type=note_type,
            pillar=pillar,
            body=body,
            tags=tags or [],
            fields=fields,
            actor=Actor.HUMAN if actor == "human" else Actor.AGENT,
        )
    except Exception as exc:
        log.exception("lint failed")
        return server._gate_error_envelope(exc)


@mcp.tool()
def list_types() -> dict[str, Any]:
    """List every schema @type with its write-mode and required-field summary.

    Returns:
        {"ok": True, "types": [{"name", "write_mode", "required", "body_empty", "atom_slug"}, ...]}

    """
    try:
        return {"ok": True, "types": server._get_gate()._schema.list_types()}
    except Exception as exc:
        log.exception("list_types failed")
        return server._gate_error_envelope(exc)


@mcp.tool()
def list_tags() -> dict[str, Any]:
    """List the closed tag glossary, grouped by prefix.

    Returns:
        {"ok": True, "tags": {"<prefix>": ["<prefix>/<leaf>", ...], ...}}

    """
    try:
        return {"ok": True, "tags": server._get_gate()._schema.list_tags()}
    except Exception as exc:
        log.exception("list_tags failed")
        return server._gate_error_envelope(exc)


@mcp.tool()
def list_keys() -> dict[str, Any]:
    """List the global union of frontmatter property keys across all @types.

    Returns:
        {"ok": True, "keys": ["<key>", ...]}

    """
    try:
        return {"ok": True, "keys": server._get_gate()._schema.list_keys()}
    except Exception as exc:
        log.exception("list_keys failed")
        return server._gate_error_envelope(exc)


@mcp.tool()
def query(note_type: str) -> dict[str, Any]:
    """Describe a note type's authoring contract — the spec sheet for a @type.

    Returns the fields a caller may set, their value constraints and formats,
    freeform fields, where the type routes (including discriminators), and body
    guidance (schema ``body_guidance`` plus a Templater template pointer when one
    exists). Use it to assemble a compliant note without reading governance prose.

    Args:
        note_type: The schema @type to describe (e.g., 'Person').

    Returns:
        {"ok": True, ...spec...} or {"ok": False, "error": "unknown_type"}.

    """
    try:
        spec = server._get_gate()._schema.describe_type(note_type)
        if spec is None:
            return {
                "ok": False,
                "error": "unknown_type",
                "detail": f"no @type {note_type!r} in the schema",
            }
        templates = (
            server._load_templates()
        )  # the one vault-read: locate a matching template
        spec["body_template"] = note_type if note_type in templates else None
        return {"ok": True, **spec}
    except Exception as exc:
        log.exception("query failed")
        return server._gate_error_envelope(exc)


@mcp.tool()
def audit(
    directory: str = "", resolve: bool = False, all_dirs: bool = False
) -> dict[str, Any]:
    """Scan a vault directory for schema drift; optionally auto-correct it.

    resolve=False reports every drifted note (missing required fields, unknown
    tags, deprecated/dead keys, off-vocabulary values, routing misplacement,
    untyped notes) and writes nothing. resolve=True applies deterministic
    corrections (key renames, dead-key drops, status repairs, routing
    relocation), re-lints the corrected note, and rewrites it only when clean —
    uncorrectable faults are reported for manual handling, never guessed.
    Healed writes land through the Gate (Obsidian) and are captured by the
    git-sweep daemon.

    Args:
        directory: Pillar/directory to scan (e.g., 'Inbox'). Ignored if all_dirs.
        resolve: False = report only; True = heal what is deterministically fixable.
        all_dirs: Scan the whole vault instead of a single directory.

    Returns:
        {"ok": True, "scanned", "drifted", "corrected", "notes": [...]} or an error.

    """
    try:
        return server._get_gate().audit(
            directory, resolve=resolve, all_dirs=all_dirs
        )
    except Exception as exc:
        log.exception("audit failed")
        return server._gate_error_envelope(exc)
