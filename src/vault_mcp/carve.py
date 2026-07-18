"""The vault carve bulk-sweep driver (C6).

Takes vault-mcp's one-note, Calliope-wired ``dissolve`` (write -> verify ->
delete, idempotent on ``(source_path, raw_hash)``, fail-safe) to a **bulk sweep
over the whole vault**, gated by a :class:`~vault_mcp.carve_policy.ScopePolicy`.

Safety is the whole point, so the driver is built to be *proven without ever
running live*:

* **Dependency-injected edges.** The file lister, the per-file ``dissolve_one``,
  and the pre-flight are all injected, so the driver is unit-tested with fakes
  and a fixture vault — no live filesystem, no live Calliope.
* **Dry-run first.** ``dry_run=True`` is the default: it classifies every file
  and reports what *would* dissolve, touching nothing. A live sweep needs
  ``dry_run=False`` explicitly.
* **Pre-flight or refuse.** The sweep will not proceed unless the pre-flight
  passes — the ``HADES_URL``-unset trap (a silent fall-back to the retired phdb
  ``/write/document`` that halts mid-dissolve) is caught here, before any file
  is touched.
* **Fail-safe per file.** A per-file failure is recorded and the sweep
  continues; because ``dissolve_one`` only deletes after a verified write, a
  failure leaves the file in place — content is never lost mid-run, and a re-run
  is idempotent.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Any

from vault_mcp.carve_policy import DEFAULT_SCOPE_POLICY, ScopePolicy

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable


@dataclass(frozen=True)
class PreflightResult:
    """Whether the sweep may proceed, and why not when it may not."""

    ok: bool
    error: str | None = None
    detail: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize the pre-flight verdict to a plain dict."""
        return {"ok": self.ok, "error": self.error, "detail": self.detail}


def carve_preflight(
    *,
    hades_url: str,
    calliope_check: Callable[[], bool] | None = None,
) -> PreflightResult:
    """Refuse the sweep unless Calliope is the wired write target.

    An unset ``HADES_URL`` is the documented trap: the dissolve leg falls back
    to the retired phdb ``/write/document`` (a 404 that halts the dissolve), so
    a bulk sweep against it would stall on the first file. Catch it here.

    ``calliope_check`` (optional, injected) does a live doc-verb smoke — a
    Calliope ``read_documents``/``write_document`` round-trip — so the pre-flight
    proves the reverse (materialize) also has a live Calliope to read from.
    """
    if not hades_url.strip():
        return PreflightResult(
            ok=False,
            error="hades_url_unset",
            detail=(
                "HADES_URL is unset — the dissolve leg would fall back to the "
                "retired phdb /write/document (404) and halt mid-sweep. "
                "Refusing the bulk carve until Calliope is the wired target."
            ),
        )
    if calliope_check is not None:
        try:
            if not calliope_check():
                return PreflightResult(
                    ok=False,
                    error="calliope_unreachable",
                    detail="Calliope doc verbs did not answer the pre-flight smoke.",
                )
        except Exception as exc:  # noqa: BLE001 - pre-flight must never raise across the sweep
            return PreflightResult(
                ok=False,
                error="calliope_unreachable",
                detail=f"Calliope pre-flight smoke raised: {exc}",
            )
    return PreflightResult(ok=True)


@dataclass(frozen=True)
class CarveFileResult:
    """The outcome for one file in the sweep."""

    path: str
    pillar: str
    action: str  # would-dissolve | dissolved | skipped | failed
    reason: str
    detail: str | None = None


@dataclass
class CarveReport:
    """The structured sweep report."""

    dry_run: bool
    preflight: dict[str, Any]
    scanned: int = 0
    selected: int = 0  # would-dissolve (dry-run) or dissolved (live)
    dissolved: int = 0
    skipped: int = 0
    failed: int = 0
    open_decisions: tuple[str, ...] = ()
    files: list[CarveFileResult] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Serialize the sweep report to a plain dict (the MCP result)."""
        return {
            "ok": bool(self.preflight.get("ok")),
            "dry_run": self.dry_run,
            "preflight": self.preflight,
            "scanned": self.scanned,
            "selected": self.selected,
            "dissolved": self.dissolved,
            "skipped": self.skipped,
            "failed": self.failed,
            "open_decisions": list(self.open_decisions),
            "files": [asdict(f) for f in self.files],
        }


def bulk_carve(
    *,
    list_files: Callable[[], Iterable[str]],
    dissolve_one: Callable[[str], dict[str, Any]],
    policy: ScopePolicy = DEFAULT_SCOPE_POLICY,
    dry_run: bool = True,
    preflight: PreflightResult | None = None,
    limit: int | None = None,
) -> CarveReport:
    """Sweep the vault under ``policy``; write->verify->delete each dissolvable file.

    ``list_files`` yields vault-relative paths; ``dissolve_one(path)`` is the
    one-note dissolve (returns the ``{ok, ...}`` result contract). With
    ``dry_run`` (the default) no file is written or deleted — the driver only
    reports what *would* dissolve. A missing/failed ``preflight`` short-circuits
    the whole sweep before any file is touched.

    ``limit`` caps the number of *acted-on* (would-dissolve / dissolved) files —
    a live sweep can be run in bounded batches.
    """
    pf = preflight if preflight is not None else PreflightResult(ok=True)
    report = CarveReport(
        dry_run=dry_run,
        preflight=pf.to_dict(),
        open_decisions=policy.open_decisions,
    )
    if not pf.ok:
        return report

    acted = 0
    for path in list_files():
        report.scanned += 1
        decision = policy.classify(path)
        if not decision.dissolve:
            report.skipped += 1
            report.files.append(
                CarveFileResult(
                    path=decision.path,
                    pillar=decision.pillar,
                    action="skipped",
                    reason=decision.reason,
                )
            )
            continue

        if limit is not None and acted >= limit:
            # Cap reached — record as skipped-by-limit, keep scanning the count.
            report.skipped += 1
            report.files.append(
                CarveFileResult(
                    path=decision.path,
                    pillar=decision.pillar,
                    action="skipped",
                    reason="limit-reached",
                )
            )
            continue

        if dry_run:
            acted += 1
            report.selected += 1
            report.files.append(
                CarveFileResult(
                    path=decision.path,
                    pillar=decision.pillar,
                    action="would-dissolve",
                    reason=decision.reason,
                )
            )
            continue

        # Live: dissolve one file (write -> verify -> delete). Fail-safe — a
        # failure is recorded and the sweep continues; the file stays put.
        acted += 1
        try:
            res = dissolve_one(decision.path)
        except Exception as exc:  # noqa: BLE001 - a driver must survive one bad file
            report.failed += 1
            report.files.append(
                CarveFileResult(
                    path=decision.path,
                    pillar=decision.pillar,
                    action="failed",
                    reason="dissolve-raised",
                    detail=str(exc),
                )
            )
            continue
        if res.get("ok"):
            report.selected += 1
            report.dissolved += 1
            report.files.append(
                CarveFileResult(
                    path=decision.path,
                    pillar=decision.pillar,
                    action="dissolved",
                    reason=decision.reason,
                )
            )
        else:
            report.failed += 1
            report.files.append(
                CarveFileResult(
                    path=decision.path,
                    pillar=decision.pillar,
                    action="failed",
                    reason=str(res.get("error", "dissolve-failed")),
                    detail=str(res.get("stage") or res.get("detail") or ""),
                )
            )
    return report


def documents_to_waves(documents: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Project Calliope ``documents`` rows into the dissolution-wave view.

    The dissolution-bridge is retired (C5); the go-forward record of *what
    dissolved when* is the Calliope documents table (``source_path`` +
    ``created_at`` + dedup). This is the repoint target for the stale
    ``list_dissolution_waves`` — one wave per stored document, newest first.
    Pure, so it is unit-tested without a live Calliope.
    """
    waves: list[dict[str, Any]] = []
    for doc in documents:
        waves.append(
            {
                "id": doc.get("id"),
                "source_path": doc.get("source_path"),
                "schema_type": doc.get("schema_type"),
                "target_schemas": [doc.get("schema_type") or "DigitalDocument"],
                "target_tables": ["documents"],
                "dissolved_at": doc.get("created_at"),
                "linked_files": 1,
            }
        )
    return waves
