"""Dissolve and dissolve-sweep — the vault-carve write verbs.

Split out of server.py under vault-mcp#5294. A REGISTRATION MODULE.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

# IMPORTED AS A MODULE, NOT AS NAMES — deliberately.
#
# `from vault_mcp.server import server.HADES_URL` binds the VALUE at import time, so a
# test doing `monkeypatch.setattr(server, "server.HADES_URL", ...)` would patch a name
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
from vault_mcp.phdb_client import _phdb_post
from vault_mcp.server import mcp

if TYPE_CHECKING:
    from collections.abc import Iterator


@mcp.tool()
def dissolve(
    path: str,
    plan_slug: str,
    rationale: str,
    declared_by: str = "code",
    repo: str = "vault",
) -> dict[str, Any]:
    """Dissolve a vault note into phdb and remove the original (VDV F3).

    Writes the note's full prose verbatim to phdb's documents table (and, for a
    ``note_type: Plan``, its metadata to plans) over HTTP, declares a dissolution
    wave, then deletes the vault file — ordering is write -> verify -> declare ->
    delete, so a failure never deletes the file and a re-run is idempotent (phdb
    dedups on content). The body is passed through verbatim (no fence-extraction).

    Args:
        path: Vault-relative path to the note (e.g. 'System/Plans/Foo.md').
        plan_slug: The dissolution wave's driving plan identifier.
        rationale: Why this content is dissolved (recorded on the wave).
        declared_by: 'code' / 'cowork' / 'backfill'.
        repo: Registry repo scope (default 'vault').

    Returns:
        {"ok": True, "written": [...], "dissolution_id", "deleted": True}, or a
        structured error naming the failing stage (the file is left in place).

    """
    from vault_mcp.lifecycle_verbs import dissolve_note

    abs_path = (server.VAULT_PATH / path).resolve()
    try:
        abs_path.relative_to(server.VAULT_PATH.resolve())
    except ValueError:
        return {"ok": False, "error": "outside_vault", "detail": path}
    if not abs_path.is_file():
        return {"ok": False, "error": "not_found", "detail": str(abs_path)}

    raw_text = abs_path.read_text(encoding="utf-8")
    res = dissolve_note(
        source_path=str(abs_path),
        raw_text=raw_text,
        file_path=abs_path.name,
        vault_rel_path=path,
        plan_slug=plan_slug,
        rationale=rationale,
        post=_phdb_post,
        delete_file=abs_path.unlink,
        declared_by=declared_by,
        repo=repo,
    )
    # Commit the deletion through the single writer (the file is already gone, so
    # skip the disk-landing poll); attach the sha for the checkpoint handshake.
    if res.get("ok"):
        committer = server._get_committer()
        committer.begin_write()
        try:
            res["commit_sha"] = committer.commit_paths(
                [path], f"vault: dissolve {path}", wait_for_create=False
            )
        finally:
            committer.end_write()
        res["dirs_pruned"] = server._prune_empty_parents(
            abs_path, server.VAULT_PATH
        )
    return res


def _vault_md_paths() -> Iterator[str]:
    """Yield vault-relative POSIX paths of every ``.md`` in the vault."""
    root = server.VAULT_PATH.resolve()
    for p in sorted(root.rglob("*.md")):
        yield p.relative_to(root).as_posix()


@mcp.tool()
def dissolve_sweep(
    dry_run: bool = True,
    limit: int | None = None,
    confirm: str = "",
) -> dict[str, Any]:
    """Bulk-carve the vault into Calliope under the scope policy (C6).

    Takes the one-note ``dissolve`` (write -> verify -> delete, idempotent,
    fail-safe) to a whole-vault sweep gated by a scope policy — governance /
    System stays; the named prose/records/entities pillars dissolve.

    **DRY-RUN by default.** A dry run classifies every ``.md`` and reports what
    *would* dissolve, touching nothing. A LIVE sweep (``dry_run=False``) is
    DESTRUCTIVE — it deletes each dissolvable file after a verified Calliope
    write — and additionally requires ``confirm="DISSOLVE"``.

    Pre-flight refuses the sweep when ``server.HADES_URL`` is unset (the documented
    trap: the dissolve leg would fall back to the retired phdb
    ``/write/document`` and halt). The scope policy is CONFIG
    (``carve_policy.DEFAULT_SCOPE_POLICY``); the exact pillar cut and the
    Obsidian vestigial-vs-retired end-state are OPEN Rob-decisions, surfaced in
    the report's ``open_decisions``. ``limit`` caps acted-on files (bounded
    live batches).

    Returns the structured :class:`~vault_mcp.carve.CarveReport`.
    """
    from vault_mcp.carve import bulk_carve, carve_preflight
    from vault_mcp.carve_policy import DEFAULT_SCOPE_POLICY

    pf = carve_preflight(hades_url=server.HADES_URL)
    if not pf.ok:
        return bulk_carve(
            list_files=_vault_md_paths,
            dissolve_one=lambda _p: {"ok": False, "error": "preflight"},
            policy=DEFAULT_SCOPE_POLICY,
            dry_run=dry_run,
            preflight=pf,
            limit=limit,
        ).to_dict()

    if not dry_run and confirm != "DISSOLVE":
        return {
            "ok": False,
            "error": "confirm_required",
            "detail": (
                "A live sweep DELETES vault files after writing them to "
                "Calliope. Re-run with dry_run=False and confirm='DISSOLVE'."
            ),
        }

    def _dissolve_one(rel_path: str) -> dict[str, Any]:
        # cast, not a bare return: `dissolve` is @mcp.tool()-decorated, and
        # FastMCP's decorator is untyped, so mypy sees its result as Any. The
        # verb's own signature declares dict[str, Any]; this restates that at
        # the one call site where the decorator erases it.
        result: dict[str, Any] = dissolve(
            path=rel_path,
            plan_slug="vault-carve",
            rationale="C6 bulk vault carve into Calliope",
        )
        return result

    report = bulk_carve(
        list_files=_vault_md_paths,
        dissolve_one=_dissolve_one,
        policy=DEFAULT_SCOPE_POLICY,
        dry_run=dry_run,
        preflight=pf,
        limit=limit,
    )
    return report.to_dict()
