# Implementation Plan: Re-dissolve the stale plan copies

**Branch**: `feat/plan-freshness` | **Date**: 2026-08-11 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/012-plan-drift-sweep/spec.md`, plus
the master-plan F1 planning-context block (authoritative substrate).

## Summary

Add a **one-directional, non-destructive vault → store refresh** for master-plans:
a sweep that hashes each plan in `System/Pantheon/WBS/`, compares against the
store's newest copy for that source path, reports the drift, and — only when asked
— writes the vault's current bytes back as a new stored version. Report-only by
default; the vault file is never touched.

The mechanism does not exist today. The only existing route to the store's
`write_document` is `dissolve_note`, which **deletes the vault source after
writing** — correct for carving prose out of the vault, catastrophic for a
master-plan that must stay on disk. That absence is the root cause the master-plan
measured: nothing has ever re-written a plan copy, so every stored plan is frozen
at whatever moment it was first written.

## Planning-context reconcile (Forge override)

The F1 planning-context block is decided substrate. Carried verbatim:

| Planning-context item | Disposition |
| :--- | :--- |
| **Scope** — disk newer than stored → new version lands + delta reported; already current → no-op | **decided** → FR-001/003/005/006; the two Scope clauses are the acceptance spine |
| **Prerequisites** — none | **decided** → root feature, built first |
| **Decisions-slice** — a live bug, fixed independent of design [Claude, TURN 239] | **decided** → no design gate; ships standalone |
| **Inter-feature seams** — gates F2 and F4 (they generalise this one-off into a mechanism) | **decided** → the sweep is built as a reusable engine with a pluggable payload builder, so F2/F3 enrich it and F4 triggers it without a rewrite |
| **Gaps surfaced** — how many other plans are stale is unmeasured beyond Aglaia | **open** → the sweep answers it as output, not a build-time assumption; no count is hardcoded |
| **Size basis** — a sweep plus a report → S | **decided** → one module + one verb + tests; no new dependency |
| **Touches (RR)** — `repo:calliope apps/calliope/src/document-store.ts` (read path); `vault:System/Pantheon/WBS/` (read) | **DIVERGENCE — see Open & risk** |

## Open & risk

- **[DIVERGENCE] The RR pointer is stale and the feature lands in a different repo
  than the master-plan states.** F1's Touches names `repo:calliope
  apps/calliope/src/document-store.ts`; the master-plan's Tech-stack & Placement
  puts F1–F4 in `calliope`. That is not buildable as written:
  - `PgDocumentStore` and the `documents` table it points at were **retired** by
    the Blocks plan's F7; the live store is `NotesDocumentStore`, where
    `mtime` / `schema_type` are graph attributes whose values arrive from the
    caller, not columns with defaults to fix.
  - Calliope runs without the vault mounted, so it cannot read a plan file or
    stat it.
  - `dissolve_note` strips frontmatter before sending, so calliope never receives
    `note_type` and cannot derive anything from it.

  The sweep therefore lands in **vault-mcp**, which owns the vault→calliope write
  seam and has both filesystem access and the payload builder. Calliope needs **no
  change** for F1: it already exposes `write_document` (insert-only, dedup on
  `(source_path, raw_hash)`, idempotent) and `read_documents`. Ruled by Rob
  2026-08-11 in-session. The calliope read path named in the RR is consumed over
  the Hades wire, not edited.

- **[OPEN] Orphan handling is report-only.** A stored copy whose vault file has
  vanished is reported as `orphaned` and left alone. The store is insert-only and
  this feature does not delete history; whether orphans should ever be reaped is
  not decided by the planning context.

- **[OPEN] Comparison basis.** The store holds the **frontmatter-stripped** body
  (`dissolve_note` does `strip_frontmatter`), so equality is tested on the
  stripped body, and reported sizes are stripped-body sizes. The master-plan's
  109,593-byte figure is the **full file including frontmatter**; the stored copy
  will therefore land slightly under that number by exactly the frontmatter's
  length. SC-003 is verified against the stripped-body size, with the full-file
  size reported alongside so the two are never confused.

## Technical Context

**Language/Version**: Python 3.13 (`requires-python >=3.13`)

**Primary Dependencies**: none added. Uses stdlib `hashlib`/`pathlib` plus the
repo's existing `hades_client` transport and `translator` payload builder.

**Storage**: Calliope's note-native document store, reached over the Hades
gateway. Insert-only, versioned, dedup key `(source_path, raw_hash)`.

**Testing**: pytest. Unit tests over an injected fake transport and a tmp_path
vault — no live network, matching `tests/test_carve.py`'s pattern.

**Target Platform**: Linux; the vault-mcp MCP server process.

**Project Type**: single project (MCP server + library).

**Performance Goals**: bounded by the plan count (~130 WBS files). One store read
per plan; sequential is acceptable.

**Constraints**: MUST NOT delete or modify any vault file. MUST default to
report-only. MUST NOT abort the sweep on a single plan's failure.

**Scale/Scope**: one directory, ~130 markdown plans, ~110KB largest file.

## Constitution Check

*GATE: must pass before Phase 0. Re-checked after Phase 1.*

| Principle | Status | Evidence |
| :--- | :--- | :--- |
| **I. Spec-Is-Law** | PASS | Every FR traces to the spec or the planning context; the two divergences/opens are surfaced above, not silently resolved. |
| **II. Deferral-Terminates** | PASS | The three deferrals name their terminator: provenance correctness → F2/F3, automatic triggering → F4, orphan reaping → explicitly undecided and reported. |
| **III. Contracts-Named** | PASS | `PlanDriftRecord` / `PlanSweepReport` named in data-model.md; the verb contract in contracts/. |
| **IV. Conformance-Checkable** | PASS | Each FR maps to a named test in tasks.md; SC-002/003 are checkable against the live store after the run. |
| **V. Verify-Before-Done** | PASS | Done requires a live sweep whose post-state re-read reports zero drifted, plus the byte count read back. |

No violations. Complexity Tracking omitted.

## Project Structure

### Documentation (this feature)

```text
specs/012-plan-drift-sweep/
├── plan.md              # This file
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/
│   └── plan_freshness.md
└── tasks.md             # Phase 2 output
```

### Source Code (repository root)

```text
src/vault_mcp/
├── plan_freshness.py    # NEW — the drift engine: compare, sweep, refresh
├── hades_client.py      # EXTEND — read a stored doc by source_path
├── translator.py        # (untouched by F1; F2/F3 land here)
└── server.py            # EXTEND — register the `plan_freshness` verb

tests/
└── test_plan_freshness.py   # NEW — unit tests over fake transport + tmp vault
```

**Structure Decision**: single project, matching the existing flat
`src/vault_mcp/` module layout. The engine is a pure module taking injected
callables (`list_files`, `read_stored`, `write_stored`) exactly as `carve.py`'s
`bulk_carve` takes `list_files` / `dissolve_one` — the established precedent in
this repo for a sweep that must be testable without a network. The MCP verb in
`server.py` is a thin wiring layer over it.

The injected-callable seam is also what satisfies the "gates F2 and F4" contract:
F2/F3 enrich the payload the writer sends, and F4 supplies a different trigger,
neither requiring a change to the comparison engine.
