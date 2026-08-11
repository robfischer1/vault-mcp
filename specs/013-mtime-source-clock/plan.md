# Implementation Plan: Track mtime on dissolved document rows

**Branch**: `feat/plan-freshness` | **Date**: 2026-08-11 | **Spec**: [spec.md](./spec.md)

## Summary

Make `mtime` on a stored copy mean **the source file's modification time at
capture**, instead of the note's hand-maintained frontmatter `updated`. Add a
backfill pass over existing copies. The result is that staleness becomes one
timestamp comparison rather than F1's read-and-hash of every body.

## Planning-context reconcile (Forge override)

| Planning-context item | Disposition |
| :--- | :--- |
| **Scope** — dissolve writes the source file's modification time; existing rows backfill from a live source | **decided** → FR-001, FR-006 |
| **Prerequisites** — F1 | **decided** → built on F1's `build_payload` seam and its sweep |
| **Inter-feature seams** — consumes F1's sweep; gates F4 | **decided** → backfill rides `sweep_plans`; F4 consumes the resulting clock |
| **Decisions-slice** — the store must hold a clock for its own copy [Claude, TURN 239] | **decided** → the motivating invariant |
| **Gaps surfaced** — what `mtime` means for a document dissolved from a non-file source | **open** → resolved narrowly and explicitly as a *named fallback chain* (FR-003), not by inventing a value. Surfaced rather than silently defaulted. |
| **Size basis** — one column, one backfill → S | **decided** |
| **Touches (RR)** — `repo:calliope .../document-store.ts` (`WriteDocumentInput`, the DDL, `read_documents`) | **DIVERGENCE — see Open & risk** |

## Open & risk

- **[DIVERGENCE] The named calliope work does not exist to be done.** F2's RR
  asks for a DDL column and a `read_documents` change. Verified live:
  - `mtime` is **already** a first-class field — declared on `WriteDocumentInput`,
    written by `notes-sink.provenanceAttrs`, reconstructed by
    `NotesDocumentStore.#rowOf`, and **already returned by `read_documents`**
    (measured: the Aglaia row answers `mtime: '2026-07-24'`).
  - The `documents` DDL the RR names belongs to `PgDocumentStore`, retired by
    Blocks F7. There is no column to add.

  The defect was never a missing column — it is that **the value supplied is the
  wrong one**. Calliope faithfully stores whatever the caller sends; the caller
  sends frontmatter `updated`. So F2 is a one-line change of *which value is
  sent*, in `vault-mcp/translator.py`, plus a backfill. **Calliope needs no
  diff.** FR-005 (expose on the read path) is therefore already satisfied and is
  verified, not built.

- **[MEASURED] The premise "`mtime` is null" was true but is no longer the whole
  story.** Pre-F1 the Aglaia row had no `mtime`, because that row was written by
  a direct `write_document` call that bypassed the translator. After F1's
  refresh it carries `2026-07-24` — the frontmatter value — while the file was
  actually modified `2026-08-11 17:22`. The clock exists and is wrong, which is
  a quieter failure than null: a staleness check would have compared against it
  and concluded "current."

- **[OPEN] `dissolved_at` does not advance on a new version.** Measured: after
  F1 wrote a new version of the Aglaia plan, `created_at` still reads
  `2026-07-19T22:00:14.422Z`. So the store records when a note was *first*
  captured, never when it was *last* captured. FR-001's "at capture" is
  satisfied through `mtime` (which does advance, being reconciled per version),
  and that is sufficient for the staleness comparison. Making `dissolved_at`
  advance is a **calliope-side** change and is **not** in this feature's scope —
  surfaced here rather than resolved.

## Technical Context

**Language/Version**: Python 3.13

**Primary Dependencies**: stdlib only (`os.stat`, `datetime`).

**Storage**: unchanged — an existing provenance attribute, new value.

**Testing**: pytest, over `note_to_payloads` directly and the sweep engine.

**Target Platform**: Linux; the vault-mcp server process.

**Project Type**: single project.

**Constraints**: a stat failure MUST NOT fail a dissolve (FR-007). The format
MUST be timezone-explicit so comparisons are unambiguous (FR-004).

**Scale/Scope**: one translator parameter, two callers, one backfill mode.

## Constitution Check

| Principle | Status | Evidence |
| :--- | :--- | :--- |
| **I. Spec-Is-Law** | PASS | The one open point (non-file sources) is resolved as an explicit, tested fallback chain, and the two divergences are surfaced, not silently reconciled. |
| **II. Deferral-Terminates** | PASS | The `dissolved_at` deferral names its owner (calliope) and why it is out of scope. |
| **III. Contracts-Named** | PASS | `source_mtime` parameter + the fallback order named in contracts/. |
| **IV. Conformance-Checkable** | PASS | Each FR has a named test; SC-004 is checkable against the live Aglaia row. |
| **V. Verify-Before-Done** | PASS | Done requires re-reading the live row and seeing the file's mtime, not the frontmatter's. |

No violations.

## Project Structure

```text
src/vault_mcp/
├── translator.py        # EXTEND — note_to_payloads takes source_mtime; fallback chain
├── plan_freshness.py    # EXTEND — file_mtime_iso() helper + backfill state
└── server.py            # EXTEND — build_payload stats the file; backfill mode

tests/
├── test_translator.py       # EXTEND — the fallback chain
└── test_plan_freshness.py   # EXTEND — mtime carried, stat failure tolerated
```

**Structure Decision**: the value is chosen where the file is known. `translator.note_to_payloads`
stays a pure function — it *accepts* `source_mtime` rather than statting, so it
remains testable without a filesystem and the "non-file source" case is a plain
argument rather than a special path. The two callers that hold a path
(`lifecycle_verbs.dissolve_note`, `server._build_plan_payload`) do the stat.

This keeps F1's seam intact exactly as contracted: F2 changes `build_payload`
and nothing else in the sweep.
