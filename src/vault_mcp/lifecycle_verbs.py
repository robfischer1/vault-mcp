"""dissolve / materialize orchestration (VDV F3).

The verbs that sequence the translator (F2) and phdb's typed-write HTTP routes
(F1) into the vault-DB lifecycle. vault-mcp owns orchestration because it owns
note-shape and the local filesystem (the delete is a local op); phdb is reached
only as a note-ignorant sink over plain HTTP.

dissolve ordering is **write -> verify -> declare -> delete**, deliberately:
the vault original is removed only after every typed write and the dissolution
declaration have succeeded. A mid-transaction failure therefore leaves the file
in place (content never lost) and, because the writes dedup on
(source_file_id, raw_hash), a re-run is idempotent — the same class of bug that
produced the historical drift rows is structurally impossible here.

Both functions are dependency-injected (the HTTP poster, the file reader, the
deleter, the gate writer are passed in) so the logic is unit-testable with fakes
and the MCP layer wires the real adapters.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

from vault_mcp.parsers import parse_frontmatter, strip_frontmatter
from vault_mcp.plan_freshness import file_mtime_iso
from vault_mcp.translator import (
    ENTITY_ENDPOINT,
    PLAN_ENDPOINT,
    note_to_payloads,
)

if TYPE_CHECKING:
    from collections.abc import Callable


class Poster(Protocol):
    """A callable that POSTs a payload to a phdb endpoint and returns the response."""

    def __call__(
        self, endpoint: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        """POST `payload` to `endpoint` and return the response."""
        ...


def _target_schemas(payloads: list[dict[str, Any]]) -> list[str]:
    """Return the distinct Schema.org @types a payload set lands as (the wave's target_schemas).

    Document payloads carry schema_type; a plan payload adds Plan; entity
    payloads carry schema_type directly.
    """
    schemas: list[str] = []
    for p in payloads:
        if p["endpoint"] == PLAN_ENDPOINT:
            st = "Plan"
        elif p["endpoint"] == ENTITY_ENDPOINT:
            st = p["payload"].get("schema_type", "Entity")
        else:
            st = p["payload"].get("schema_type", "DigitalDocument")
        if st not in schemas:
            schemas.append(st)
    return schemas


def dissolve_note(
    *,
    source_path: str,
    raw_text: str,
    file_path: str | None,
    vault_rel_path: str | None = None,
    plan_slug: str,
    rationale: str,
    post: Poster,
    delete_file: Callable[[], None],
    declared_by: str = "code",
    repo: str = "vault",
) -> dict[str, Any]:
    """Dissolve one note: write its content to phdb, declare the wave, then delete the original.

    Returns {ok, written, dissolution_id, deleted}. On any write/declare failure
    returns {ok: False, error, stage, written} WITHOUT deleting the file.
    """
    # The wave-declaration inputs are RETIRED with the bridge (C5) but stay
    # in the verb signature for caller compatibility.
    _ = (vault_rel_path, plan_slug, rationale, declared_by, repo)
    frontmatter = parse_frontmatter(raw_text)
    body = strip_frontmatter(raw_text)
    # F2 — the store's clock is the source file's mtime, not the note's
    # hand-maintained frontmatter `updated`. Resolved here (the caller holds
    # the path); never raises, so a stat failure cannot fail the dissolve.
    payloads = note_to_payloads(
        frontmatter,
        body,
        source_path,
        file_path=file_path,
        source_mtime=file_mtime_iso(file_path) if file_path else None,
    )

    # 1. Write every payload; verify each. Stop before delete on any failure.
    written: list[dict[str, Any]] = []
    for p in payloads:
        res = post(p["endpoint"], p["payload"])
        if not res.get("ok"):
            return {
                "ok": False,
                "stage": "write",
                "endpoint": p["endpoint"],
                "error": res.get("error", "write failed"),
                "written": written,
            }
        written.append(
            {
                "table": res.get("table"),
                "id": res.get("id"),
                "deduped": res.get("deduped"),
            }
        )

    # 2. (RETIRED 2026-07-04, C5) The dissolution-bridge's wave declaration is
    # gone with the bridge — its registries are archived on Calliope
    # (archive_dissolutions et al.) and the document store itself is the
    # go-forward record of what dissolved when (source_path + created_at +
    # dedup). The dissolve is now write → delete.

    # 3. Only now remove the vault original.
    delete_file()
    return {
        "ok": True,
        "written": written,
        "dissolution_id": None,
        "deleted": True,
    }
