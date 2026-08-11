# Contract: `plan_freshness` verb + engine seam

## MCP verb — `plan_freshness`

Report (and optionally repair) drift between the vault's master-plans and the
store's copies.

**Report-only by default.** A refresh is a write to the store only; no vault file
is read-modified-written, moved, or deleted by this verb under any argument.

### Input

| Arg | Type | Default | Meaning |
| :--- | :--- | :--- | :--- |
| `refresh` | `bool` | `False` | When `True`, write a new stored version for each `drifted` / `missing` plan. When `False`, compute and report only. |
| `directory` | `str` | `"System/Pantheon/WBS"` | Vault-relative directory to sweep (FR-009). |
| `source_path` | `str \| None` | `None` | Sweep exactly one plan instead of the directory. |
| `limit` | `int \| None` | `None` | Cap the number of plans acted on (bounded live batches, mirroring `dissolve_sweep`). |

`refresh=True` requires **no** confirm token. This is deliberate and differs from
`dissolve_sweep`, which requires `confirm="DISSOLVE"`: that verb is destructive
(it deletes vault files), this one is not (it appends a store version and the
store is insert-only). Guarding a non-destructive, idempotent, converging write
behind a confirm token would train the token to be noise.

### Output — `PlanSweepReport.to_dict()`

```json
{
  "ok": true,
  "dry_run": true,
  "directory": "System/Pantheon/WBS",
  "scanned": 130,
  "refreshed": 0,
  "counts": {"current": 128, "drifted": 1, "missing": 1, "orphaned": 0, "error": 0},
  "records": [
    {
      "source_path": "System/Pantheon/WBS/Aglaia — The Block-Native Editor — Master-plan.md",
      "state": "drifted",
      "vault_bytes": 109000,
      "stored_bytes": 91009,
      "delta_bytes": 17991,
      "vault_file_bytes": 109593,
      "vault_hash": "…", "stored_hash": "…",
      "refreshed": false,
      "error": null
    }
  ]
}
```

On pre-flight failure: `{"ok": false, "error": "preflight", "detail": "..."}`.
Never raises across the verb boundary — a per-plan failure becomes an `error`
record, a global failure becomes `ok: false` (the `_phdb_post` house rule).

### Guarantees

| ID | Guarantee |
| :--- | :--- |
| G-1 | `refresh=False` performs zero writes. |
| G-2 | No vault file is created, modified, moved, or deleted under any argument. |
| G-3 | A `current` plan is never written, even with `refresh=True`. |
| G-4 | Running the verb twice with `refresh=True` writes on the first run and no-ops on the second (idempotent/converging — the store's `(source_path, raw_hash)` dedup makes a duplicate a no-op even if the guard were bypassed). |
| G-5 | One plan's failure does not abort the sweep. |

## Engine seam (the F2/F4 gate)

`plan_freshness.sweep_plans()` takes injected callables — the pattern
`carve.bulk_carve` established in this repo:

```python
def sweep_plans(
    *,
    list_files: Callable[[str], Iterable[str]],
    read_stored: Callable[[str], StoredCopy | None],
    build_payload: Callable[[str, str], dict[str, Any]],
    write_stored: Callable[[dict[str, Any]], dict[str, Any]],
    read_vault: Callable[[str], str],
    directory: str,
    refresh: bool = False,
    limit: int | None = None,
) -> PlanSweepReport: ...
```

**This signature is the contract F1 owes F2, F3 and F4** (planning context:
"gates F2 and F4 — they generalise this one-off into a mechanism"):

- **F2** (`mtime` from the source file) and **F3** (`schema_type` from
  `note_type`) change **`build_payload` only**. The comparison engine, the report
  and the verb are untouched by them.
- **F4** (automatic re-dissolve) supplies a different **caller** — a watcher or a
  scheduled reconcile invoking `sweep_plans(refresh=True)`. It adds a trigger, not
  a second comparison implementation.

`StoredCopy` is the minimal projection the engine needs from the store —
`{source_path, raw_hash, body_bytes}` — deliberately *not* the full `DocumentRow`,
so the engine never depends on the store handing back a body it does not need.

## Consumed contract — calliope (unchanged)

| Verb | Use | Change needed |
| :--- | :--- | :--- |
| `write_document` | append a version; dedup `(source_path, raw_hash)`; idempotent | **none** |
| `read_documents(source_path=…)` | newest stored copy for a path | **none** |

F1 requires **no calliope diff**. (See plan.md Open & risk for why the RR pointer
at `document-store.ts` is not actionable.)
