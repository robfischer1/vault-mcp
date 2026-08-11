# Data Model: Re-dissolve the stale plan copies

> **Reconcile note.** F1's planning-context block carries **no Shared-data-model
> slice** — that field is present on F8/F9 but absent on F1. Per the override's
> reconcile rule, the entities below are therefore generated from the spec's Key
> Entities (the planning context is silent here), while every field that *is*
> decided by the planning context — the comparison basis, the no-op condition, the
> delta — is carried verbatim from its Scope clause.

## Entities

### `PlanDriftRecord` (frozen dataclass)

One plan's comparison outcome. The unit the report is made of.

| Field | Type | Meaning |
| :--- | :--- | :--- |
| `source_path` | `str` | Vault-relative path, the store's dedup anchor (e.g. `System/Pantheon/WBS/Aglaia — The Block-Native Editor — Master-plan.md`) |
| `state` | `PlanDriftState` | `current` · `drifted` · `missing` · `orphaned` · `error` |
| `vault_bytes` | `int \| None` | Size of the frontmatter-stripped vault body; `None` when the file is absent (orphaned) |
| `stored_bytes` | `int \| None` | Size of the newest stored body; `None` when the store has no copy (missing) |
| `delta_bytes` | `int` | `vault_bytes - stored_bytes`, `0` when either side is absent |
| `vault_hash` | `str \| None` | sha256 of the stripped vault body |
| `stored_hash` | `str \| None` | The stored copy's `raw_hash` |
| `vault_file_bytes` | `int \| None` | Full file size **including** frontmatter — reported so the stripped-body number is never mistaken for the file size (see plan.md Open & risk) |
| `refreshed` | `bool` | Did this run write a new version for this plan? |
| `error` | `str \| None` | Per-plan failure text; set only when `state == error` |

**Validation rules**

- `state == current` ⟺ `vault_hash == stored_hash`. Equality is by hash, never by
  timestamp — decided by FR-002 and by the planning context's Scope clause.
- `state == missing` ⟺ the vault file exists and the store has no copy for its
  source path.
- `state == orphaned` ⟺ a stored copy exists and the vault file does not.
- `refreshed` may be `True` only when `state` was `drifted` or `missing` **and**
  the run was invoked with writes enabled.
- A `current` plan is never written — the planning context's second Scope clause
  ("already current → no-op") is a hard invariant, not an optimisation.

### `PlanDriftState` (str enum)

`current` · `drifted` · `missing` · `orphaned` · `error`. Closed set; FR-008.

### `PlanSweepReport` (dataclass)

| Field | Type | Meaning |
| :--- | :--- | :--- |
| `records` | `list[PlanDriftRecord]` | One per plan seen, in path order |
| `counts` | `dict[str, int]` | Per-state totals |
| `scanned` | `int` | Files considered |
| `refreshed` | `int` | Versions written this run |
| `dry_run` | `bool` | True when report-only (the default) |
| `directory` | `str` | The swept directory |

**Derived**: `counts` and `refreshed` are computed from `records`, never tracked
independently — one source of truth for the totals.

`to_dict()` returns the MCP wire shape (mirrors `CarveReport.to_dict()`).

## State transitions

```text
                   ┌─ hash equal ──────────────→ current   (never written)
vault file ────────┤
                   └─ hash differs ────────────→ drifted ──┐
                                                            │ writes enabled
store has no copy ─────────────────────────────→ missing ──┤
                                                            ↓
                                                     write_document
                                                            ↓
                                                   refreshed = True
                                                   (re-read → current)

store copy, no vault file ─────────────────────→ orphaned  (reported, untouched)
unreadable file / store error ─────────────────→ error     (sweep continues)
```

## Relationships

- A `PlanSweepReport` has many `PlanDriftRecord`; a record belongs to exactly one
  report.
- A `PlanDriftRecord.source_path` is the join key to the store's stored copy and
  to the vault file. It is the store's dedup anchor, so it is the only identifier
  that is stable across versions (document ids are not — the note-native store
  returns `id: null` for fresh writes).

## Non-entities (deliberate)

- **No new persisted table or file.** The report is computed per run and returned;
  nothing about drift is stored. Making freshness *queryable* from the store is
  F2's job (the `mtime` clock), not F1's.
- **No version record.** The store already versions insert-only; this feature adds
  a version by writing, and reads only the newest.
