"""Plan freshness — the stored-copy sweep and its helpers.

Split out of server.py under vault-mcp#5294. A REGISTRATION MODULE.

`_plan_sweep` is called back by server.py's reconcile bootstrap, which imports
it lazily inside that function rather than at module scope — the same deferred
pattern the lazy accessors use, and the only way to reference a name living in a
module that server.py itself registers.
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
from vault_mcp.server import mcp

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from vault_mcp.plan_freshness import PlanSweepReport, StoredCopy

#: reconcile skip the store read for plans whose disk clock has not advanced.
_PLAN_CLOCKS: dict[str, str] = {}


def _plan_md_paths(directory: str) -> Iterator[str]:
    """Yield vault-relative POSIX paths of every ``.md`` under *directory*."""
    root = server.VAULT_PATH.resolve()
    base = (root / directory).resolve()
    if not base.is_dir():
        return
    if not base.is_relative_to(root):  # never escape the vault
        return
    for p in sorted(base.rglob("*.md")):
        yield p.relative_to(root).as_posix()


def _read_stored_copy(source_path: str) -> StoredCopy | None:
    """Newest stored copy for *source_path*, as the freshness engine's projection."""
    from vault_mcp.hades_client import read_document_by_source_path
    from vault_mcp.plan_freshness import StoredCopy

    res = read_document_by_source_path(
        source_path, url=f"{server.HADES_URL}/", token=server.HADES_TOKEN
    )
    if res.get("ok") is False:
        raise RuntimeError(str(res.get("error", "read_documents failed")))
    docs = res.get("documents") or []
    if not docs:
        return None
    newest = docs[0]  # Calliope answers newest-first
    body = newest.get("body_text") or ""
    stored_mtime = newest.get("mtime")
    return StoredCopy(
        source_path=source_path,
        raw_hash=str(newest.get("raw_hash") or ""),
        body_bytes=len(body.encode("utf-8")),
        mtime=str(stored_mtime) if stored_mtime else None,
    )


def _build_plan_payload(source_path: str, raw_text: str) -> dict[str, Any]:
    """Build the ``write_document`` payload for one plan.

    This is the seam F2 (``mtime`` from the source file) and F3 (``schema_type``
    from the source's ``note_type``) extend — the comparison engine and the verb
    are untouched by either.
    """
    from vault_mcp.parsers import parse_frontmatter, strip_frontmatter
    from vault_mcp.plan_freshness import file_mtime_iso
    from vault_mcp.translator import DOC_ENDPOINT, note_to_payloads

    frontmatter = parse_frontmatter(raw_text)
    body = strip_frontmatter(raw_text)
    file_path = str(server.VAULT_PATH.resolve() / source_path)
    payloads = note_to_payloads(
        frontmatter,
        body,
        source_path,
        file_path=file_path,
        source_mtime=file_mtime_iso(file_path),
    )
    # Annotated rather than inferred: `payloads` rows are dict[str, Any], so
    # `p["payload"]` is Any and flowed into the dict[str, Any] return unchecked
    # under server.py's no-any-return override. Moving this out of that module
    # exposed it. The isinstance is a real guard — a translator that returned a
    # non-mapping payload would otherwise surface as a confusing error much
    # further downstream.
    doc: object = next(
        (p["payload"] for p in payloads if p["endpoint"] == DOC_ENDPOINT), None
    )
    if doc is None:
        # UNCHANGED semantics: "this note has no document payload" was a
        # ValueError before the split and stays one.
        msg = f"{source_path} does not translate to a document payload"
        raise ValueError(msg)
    if not isinstance(doc, dict):
        # A DIFFERENT failure, and so a different type: the translator produced
        # a payload of the wrong shape. Merging the two into one isinstance
        # check was what made ruff ask for TypeError — it was right that the
        # conditions are not the same thing.
        msg = (
            f"{source_path}: expected a mapping document payload, got "
            f"{type(doc).__name__}"
        )
        raise TypeError(msg)
    return doc


@mcp.tool()
def plan_freshness(
    refresh: bool = False,
    directory: str = "System/Pantheon/WBS",
    source_path: str | None = None,
    include_missing: bool = False,
    backfill: bool = False,
    limit: int | None = None,
) -> dict[str, Any]:
    """Report (and optionally repair) drift between vault plans and their stored copies.

    A master-plan lives in the vault AND as a copy in Calliope's document store,
    which ``athena orchestrate_plan`` reads by reference. The store versions
    insert-only, so a re-write *would* land a new version — but nothing ever
    issued one, and stored copies are frozen at first write. Measured
    2026-08-10: the Aglaia plan's copy was 22 days and 18,584 bytes behind disk,
    which is why its A21/A22 amendment never reached the board.

    **REPORT-ONLY by default.** ``refresh=True`` writes the vault's current bytes
    to the store as a new version for each drifted or missing plan. Unlike
    ``dissolve_sweep`` this is **not destructive and takes no confirm token**: it
    never creates, modifies, moves or deletes a vault file under any argument,
    and the store is insert-only, so a refresh appends and converges. Direction
    is an invariant — vault to store, never the reverse.

    A plan already current is never written. One plan's failure is reported
    against that plan and the sweep continues.

    ``refresh`` repairs **drift only**. The store is not a mirror of the vault —
    ``carve_policy`` keeps ``System/`` in the vault, so a plan reaches the store
    only when something explicitly puts it there. Measured 2026-08-11: 173 of
    175 WBS plans have no stored copy at all, so a refresh that also populated
    would push ~10MB of prose into the store as a side effect of a freshness
    repair. Populating is a separate, deliberate act: ``include_missing=True``.

    Args:
        refresh: Write drifted copies (default False — report only).
        directory: Vault-relative directory to sweep.
        source_path: Sweep exactly this one plan instead of the directory.
        include_missing: Also write plans with no stored copy (populate).
        backfill: Reconcile provenance (mtime / schema_type) on plans whose
            body is already current, without minting a new body version.
        limit: Cap the number of plans acted on.

    Returns:
        The sweep report: ``{ok, dry_run, directory, scanned, refreshed,
        counts, records[]}``.

    """
    return _plan_sweep(
        refresh=refresh,
        directory=directory,
        source_path=source_path,
        include_missing=include_missing,
        backfill=backfill,
        limit=limit,
    ).to_dict()


def _plan_sweep(
    *,
    refresh: bool = False,
    directory: str = "System/Pantheon/WBS",
    source_path: str | None = None,
    include_missing: bool = False,
    backfill: bool = False,
    cheap_gate: bool = False,
    limit: int | None = None,
) -> PlanSweepReport:
    """Configure and run one sweep — shared by the verb and F4's reconcile."""
    from vault_mcp.carve import carve_preflight
    from vault_mcp.hades_client import write_document
    from vault_mcp.plan_freshness import is_probably_stale, sweep_plans

    pf = carve_preflight(hades_url=server.HADES_URL)

    def _list(d: str) -> Iterator[str]:
        if source_path is not None:
            yield source_path
        else:
            yield from _plan_md_paths(d)

    def _read_vault(p: str) -> str:
        return (server.VAULT_PATH.resolve() / p).read_text(encoding="utf-8")

    def _write(payload: dict[str, Any]) -> dict[str, Any]:
        return write_document(
            payload, url=f"{server.HADES_URL}/", token=server.HADES_TOKEN
        )

    root = str(server.VAULT_PATH.resolve())

    def _gate(p: str) -> bool:
        # A path we have never resolved must be checked properly; the sweep
        # then caches its clock for later ticks.
        if p not in _PLAN_CLOCKS:
            return True
        return is_probably_stale(
            p, vault_root=root, stored_mtime=_PLAN_CLOCKS[p]
        )

    gate: Callable[[str], bool] | None = _gate if cheap_gate else None

    report = sweep_plans(
        list_files=_list,
        read_vault=_read_vault,
        read_stored=_read_stored_copy,
        build_payload=_build_plan_payload,
        write_stored=_write,
        directory=directory,
        refresh=refresh,
        include_missing=include_missing,
        backfill=backfill,
        cheap_gate=gate,
        limit=limit,
        preflight=pf.to_dict(),
    )
    # Cache each plan's stored clock so later reconcile ticks can skip the
    # store read entirely. This cache is the ONLY thing that makes the
    # periodic reconcile affordable: calliope has no index read by
    # source_path — read_documents always materialises the body — so without
    # it every tick would pull ~10MB.
    for rec in report.records:
        if not rec.gated and rec.stored_mtime is not None:
            _PLAN_CLOCKS[rec.source_path] = rec.stored_mtime
        elif rec.refreshed or rec.backfilled:
            _PLAN_CLOCKS.pop(rec.source_path, None)
    return report


def _read_dissolved_row(table: str, row_id: int) -> dict[str, Any]:
    """Read a dissolved row for materialize — from Calliope, not the dead phdb (C6).

    Reverse-symmetry: dissolve writes documents to Calliope (over Hades when
    ``server.HADES_URL`` is set), so un-dissolve READS from Calliope too — the retired
    phdb is never the go-forward source. When ``server.HADES_URL`` is set, a
    ``documents`` read routes to Calliope's ``read_documents`` and the row is
    normalized to the phdb documents-row shape the mapper expects. With
    ``server.HADES_URL`` unset (or for the legacy ``plans`` table, which has no Calliope
    home), it falls back to the phdb HTTP read. Returns
    ``{ok, row, paired_body}`` or ``{ok: False, error, detail}``.
    """
    if table == "documents" and server.HADES_URL:
        from vault_mcp.hades_client import read_document
        from vault_mcp.translator import calliope_document_to_row

        res = read_document(
            row_id, url=f"{server.HADES_URL}/", token=server.HADES_TOKEN
        )
        if res.get("ok") is False:
            return {
                "ok": False,
                "error": "calliope_read_failed",
                "detail": str(res.get("error")),
            }
        docs = res.get("documents") or []
        if not docs:
            return {
                "ok": False,
                "error": "row_not_found",
                "detail": f"documents#{row_id} (Calliope)",
            }
        return {
            "ok": True,
            "row": calliope_document_to_row(docs[0]),
            "paired_body": None,
        }

    import httpx

    try:
        resp = httpx.get(
            f"{server.PHDB_HTTP_URL}/read/{table}",
            params={"id": row_id},
            timeout=30.0,
        )
    except httpx.HTTPError as e:
        return {"ok": False, "error": f"phdb unreachable: {e}"}
    if resp.status_code == 404:
        return {
            "ok": False,
            "error": "row_not_found",
            "detail": f"{table}#{row_id}",
        }
    if resp.status_code != 200:
        return {
            "ok": False,
            "error": f"phdb HTTP {resp.status_code}",
            "detail": resp.text,
        }
    data = resp.json()
    return {
        "ok": True,
        "row": data.get("row") or {},
        "paired_body": data.get("paired_body"),
    }
