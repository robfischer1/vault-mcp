"""Plan freshness — the one-directional vault → store refresh (F1).

Master-plans live in two places: the vault ``.md`` on disk and a copy in
Calliope's note-native document store, which ``athena orchestrate_plan`` reads
by reference. Nothing has ever re-written that copy. The store versions
insert-only on ``(source_path, raw_hash)``, so a re-write *would* land a new
version — but no code path ever issues one, and the copy is frozen at whatever
moment it was first written. Measured 2026-08-10: the Aglaia master-plan's
stored copy was 22 days and 18,584 bytes behind disk, which is why its A21/A22
amendment never reached the board.

**Why this is not ``dissolve_note``.** The only existing route to the store's
``write_document`` is :func:`vault_mcp.lifecycle_verbs.dissolve_note`, whose
contract is write → verify → **delete the vault source**. That is correct for
carving prose *out* of the vault; it is catastrophic for a master-plan, which
must stay on disk as the thing Rob edits. This module is the missing
non-destructive half: it reads the vault and appends to the store, and it never
creates, modifies, moves or deletes a vault file under any argument.

**Direction is an invariant, not a default.** vault → store, always. There is no
argument that makes this module write to the vault.

The engine takes injected callables (the pattern :func:`vault_mcp.carve.bulk_carve`
established here) so it is testable without a network — and so the features that
build on it do not have to touch it:

* **F2** (``mtime`` from the source file) and **F3** (``schema_type`` from the
  source's ``note_type``) change ``build_payload`` alone.
* **F4** (automatic re-dissolve) supplies a different *caller* — a watcher or a
  scheduled reconcile invoking ``sweep_plans(refresh=True)``. It adds a trigger,
  not a second comparison.

Equality is by content hash, never by timestamp: the store's dedup key is the
body hash, and a timestamp comparison would call a touched-but-unchanged file
stale. The compared body is the **frontmatter-stripped** body, because that is
what ``dissolve_note`` sends and therefore what the store holds; the full file
size is reported alongside so the two numbers are never confused.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from hashlib import sha256
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

#: The vault directory holding master-plans — the sweep's default scope.
DEFAULT_PLAN_DIRECTORY = "System/Pantheon/WBS"


class PlanDriftState(StrEnum):
    """The closed set of per-plan outcomes (FR-008)."""

    CURRENT = "current"
    DRIFTED = "drifted"
    MISSING = "missing"
    ORPHANED = "orphaned"
    ERROR = "error"


def body_hash(body: str) -> str:
    """Hash a stripped body the way the store does — sha256 over UTF-8."""
    return sha256(body.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class StoredCopy:
    """The minimal projection of a stored document the engine needs.

    Deliberately *not* the full ``DocumentRow``: the engine compares hashes and
    sizes, so it must never depend on the store handing back a body it does not
    need. ``body_bytes`` is the stored body's length when the store reports it.
    """

    source_path: str
    raw_hash: str
    body_bytes: int | None = None


@dataclass(frozen=True)
class PlanDriftRecord:
    """One plan's comparison outcome."""

    source_path: str
    state: PlanDriftState
    vault_bytes: int | None = None
    stored_bytes: int | None = None
    delta_bytes: int = 0
    vault_hash: str | None = None
    stored_hash: str | None = None
    #: Full file size INCLUDING frontmatter — reported so the stripped-body
    #: number above is never mistaken for the size of the file on disk.
    vault_file_bytes: int | None = None
    refreshed: bool = False
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize, with the enum flattened to its string value."""
        d = asdict(self)
        d["state"] = self.state.value
        return d


@dataclass
class PlanSweepReport:
    """The structured sweep report (the MCP result)."""

    dry_run: bool
    directory: str
    records: list[PlanDriftRecord] = field(default_factory=list)
    preflight: dict[str, Any] | None = None

    @property
    def scanned(self) -> int:
        """How many plans were considered."""
        return len(self.records)

    @property
    def refreshed(self) -> int:
        """How many stored versions this run wrote."""
        return sum(1 for r in self.records if r.refreshed)

    @property
    def counts(self) -> dict[str, int]:
        """Per-state totals, derived from ``records`` — one source of truth."""
        out = {s.value: 0 for s in PlanDriftState}
        for r in self.records:
            out[r.state.value] += 1
        return out

    def to_dict(self) -> dict[str, Any]:
        """Serialize the sweep report to a plain dict."""
        return {
            "ok": self.preflight is None or bool(self.preflight.get("ok")),
            "dry_run": self.dry_run,
            "directory": self.directory,
            "scanned": self.scanned,
            "refreshed": self.refreshed,
            "counts": self.counts,
            **({"preflight": self.preflight} if self.preflight else {}),
            "records": [r.to_dict() for r in self.records],
        }


def compare_plan(
    source_path: str,
    *,
    vault_text: str | None,
    stored: StoredCopy | None,
) -> PlanDriftRecord:
    """Compare one plan's vault body against its stored copy.

    Pure: no IO, no writes. ``vault_text`` is the RAW file text (frontmatter
    included) or ``None`` when the file is absent; the stripped body is derived
    here so callers cannot strip it inconsistently.
    """
    from vault_mcp.parsers import strip_frontmatter

    if vault_text is None:
        if stored is None:
            # Neither side exists: nothing to say. Callers do not produce this.
            return PlanDriftRecord(
                source_path=source_path,
                state=PlanDriftState.ERROR,
                error="neither a vault file nor a stored copy exists",
            )
        return PlanDriftRecord(
            source_path=source_path,
            state=PlanDriftState.ORPHANED,
            stored_bytes=stored.body_bytes,
            stored_hash=stored.raw_hash,
        )

    body = strip_frontmatter(vault_text)
    vault_bytes = len(body.encode("utf-8"))
    vault_file_bytes = len(vault_text.encode("utf-8"))
    vhash = body_hash(body)

    if stored is None:
        return PlanDriftRecord(
            source_path=source_path,
            state=PlanDriftState.MISSING,
            vault_bytes=vault_bytes,
            vault_hash=vhash,
            vault_file_bytes=vault_file_bytes,
        )

    if stored.raw_hash == vhash:
        state = PlanDriftState.CURRENT
        delta = 0
    else:
        state = PlanDriftState.DRIFTED
        delta = (
            vault_bytes - stored.body_bytes
            if stored.body_bytes is not None
            else 0
        )

    return PlanDriftRecord(
        source_path=source_path,
        state=state,
        vault_bytes=vault_bytes,
        stored_bytes=stored.body_bytes,
        delta_bytes=delta,
        vault_hash=vhash,
        stored_hash=stored.raw_hash,
        vault_file_bytes=vault_file_bytes,
    )


def sweep_plans(
    *,
    list_files: Callable[[str], Iterable[str]],
    read_vault: Callable[[str], str],
    read_stored: Callable[[str], StoredCopy | None],
    build_payload: Callable[[str, str], dict[str, Any]],
    write_stored: Callable[[dict[str, Any]], dict[str, Any]],
    directory: str = DEFAULT_PLAN_DIRECTORY,
    refresh: bool = False,
    include_missing: bool = False,
    limit: int | None = None,
    preflight: dict[str, Any] | None = None,
) -> PlanSweepReport:
    """Compare every plan under *directory*; optionally refresh the stale ones.

    Report-only unless ``refresh=True``. A ``current`` plan is never written,
    even when refreshing — the master-plan's second Scope clause ("already
    current → no-op") is an invariant here, not an optimisation. A single plan's
    failure becomes an ``error`` record and the sweep continues (FR-007).

    ``refresh`` repairs **drift only** — a plan whose stored copy exists and has
    fallen behind. It deliberately does NOT write plans in the ``missing``
    state, because the store is not a mirror of the vault: ``carve_policy``
    keeps ``System/`` in the vault, so a plan reaches the store only when
    something explicitly puts it there. Measured 2026-08-11: 173 of 175 WBS
    plans have no stored copy, so a refresh that also populated would push
    ~10MB of prose into the store as a side effect of a freshness repair.
    Populating is a separate, deliberate act — ``include_missing=True``.

    ``build_payload(source_path, raw_text)`` produces the ``write_document``
    payload; it is the seam F2 and F3 extend. ``read_vault`` returns the raw
    file text.
    """
    report = PlanSweepReport(
        dry_run=not refresh, directory=directory, preflight=preflight
    )
    if preflight is not None and not preflight.get("ok"):
        return report

    acted = 0
    for source_path in list_files(directory):
        if limit is not None and acted >= limit:
            break
        acted += 1
        try:
            raw = read_vault(source_path)
        except Exception as exc:  # noqa: BLE001 - one plan must not abort the sweep
            report.records.append(
                PlanDriftRecord(
                    source_path=source_path,
                    state=PlanDriftState.ERROR,
                    error=f"vault read failed: {exc}",
                )
            )
            continue

        try:
            stored = read_stored(source_path)
        except Exception as exc:  # noqa: BLE001 - one plan must not abort the sweep
            report.records.append(
                PlanDriftRecord(
                    source_path=source_path,
                    state=PlanDriftState.ERROR,
                    error=f"store read failed: {exc}",
                )
            )
            continue

        record = compare_plan(source_path, vault_text=raw, stored=stored)

        writable = {PlanDriftState.DRIFTED}
        if include_missing:
            writable.add(PlanDriftState.MISSING)
        if refresh and record.state in writable:
            record = _refresh_one(
                record,
                raw=raw,
                build_payload=build_payload,
                write_stored=write_stored,
            )

        report.records.append(record)

    return report


def _refresh_one(
    record: PlanDriftRecord,
    *,
    raw: str,
    build_payload: Callable[[str, str], dict[str, Any]],
    write_stored: Callable[[dict[str, Any]], dict[str, Any]],
) -> PlanDriftRecord:
    """Write one plan's current bytes to the store; never touches the vault."""
    from dataclasses import replace

    try:
        payload = build_payload(record.source_path, raw)
        result = write_stored(payload)
    except Exception as exc:  # noqa: BLE001 - one plan must not abort the sweep
        return replace(
            record,
            state=PlanDriftState.ERROR,
            error=f"store write raised: {exc}",
        )

    if not result.get("ok"):
        return replace(
            record,
            state=PlanDriftState.ERROR,
            error=f"store write failed: {result.get('error', 'unknown')}",
        )

    # The write landed (or deduped, which means the store already had these
    # exact bytes). Either way the stored copy now matches the vault.
    return replace(
        record,
        state=PlanDriftState.CURRENT,
        stored_bytes=record.vault_bytes,
        stored_hash=record.vault_hash,
        delta_bytes=0,
        refreshed=True,
    )
