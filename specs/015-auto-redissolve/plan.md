# Implementation Plan: Automatic re-dissolve on change

**Branch**: `feat/plan-freshness` | **Date**: 2026-08-11 | **Spec**: [spec.md](./spec.md)

## Summary

A daemon thread in the vault-mcp server runs `sweep_plans(refresh=True)` every
15 minutes, gated by F2's clock so unchanged plans cost one string comparison
instead of a stored-body fetch. Writes only what F1's hash comparison authorises;
logs every write; survives a failing pass.

## Planning-context reconcile (Forge override)

| Planning-context item | Disposition |
| :--- | :--- |
| **Scope** — disk mtime exceeds stored mtime → new version lands; no change → nothing written | **decided** → and directly buildable *because* F2 made the stored mtime the file's clock. Implemented as `is_probably_stale()`. |
| **Prerequisites** — F1, F2 | **decided** → consumes F1's sweep and F2's clock; adds no third comparison |
| **Inter-feature seams** — consumes F1 and F2 | **decided** → F4 is a *caller*, exactly as F1's contract promised. `sweep_plans` gained one optional argument (`cheap_gate`); its comparison logic is untouched. |
| **Decisions-slice** — a cache with no invalidation is the root cause [Claude, TURN 239] | **decided** → this is the invalidation |
| **Gaps surfaced** — *watcher vs scheduled sweep* | **RESOLVED → scheduled.** See Decision below. |
| **Gaps surfaced** — *whether this survives F9 (may be transitional)* | **open** → not resolved. Kept as one small self-contained module so retiring it is a deletion. |
| **Size basis** — a trigger with an observable log → M | **decided** |
| **Touches (RR)** — write `repo:calliope .../document-store.ts`, `apps/calliope/src/mcp/` (the trigger's home) | **DIVERGENCE** — the trigger's home is vault-mcp, per the F1–F4 placement ruling. Calliope has no vault filesystem to watch and no clock to compare against. **Calliope needs no diff.** |

## Decision: scheduled reconcile, not a watcher hook

The master-plan left this open. Resolved on evidence:

vault-mcp **already runs a watchdog observer** (`watcher.py`, wired to index
invalidation), so hanging the store-write off it was the cheap-looking move. It
is the wrong one: the watcher fires on *every* `.md` save. A plan edited across
ten minutes would land ten stored versions; an editor autosaving would land
dozens. Because the store is insert-only, that history is permanent — the
version log would become a keystroke log, and the "one document, block-level
supersedes" end-state F9 is aiming at would be buried under it.

The periodic sweep debounces by construction, and F2's clock made it cheap
enough to afford. Its cost is fixed per interval and does not scale with typing
speed.

## Open & risk

- **[MEASURED] There is no cheap index read.** The gate wants each plan's stored
  clock without its body — calliope cannot serve that: `read_documents` always
  materialises the body (`NotesDocumentStore.#rowOf` reads it even when
  `omit_body` is set, and an unfiltered list call **timed out** at 25s in
  testing). So the gate is backed by an **in-process cache** populated by each
  sweep: the first pass pays full reads, every later pass skips unchanged plans.
  A server restart re-pays it once. A genuine `list_blocks`-style index over
  documents would remove this; that is a **calliope** feature and is not built
  here.

- **[OPEN] Enabled by default.** FR-001 says "without human action", so the loop
  defaults on (`VAULT_MCP_PLAN_RECONCILE=0` disables,
  `VAULT_MCP_PLAN_RECONCILE_SECONDS` retunes). It is an autonomous writer
  starting in a shared process, which is a real consideration — bounded by: it
  writes only plans whose *content hash* differs, it is idempotent, the store is
  insert-only so nothing is destroyed, and it refuses to start without
  `HADES_URL`.

- **[BOUNDARY] The gate can never suppress a write.** It only decides what to
  *look at*. A false "stale" costs one wasted read; a false "current" would be a
  correctness bug, which is why the gate answers `True` whenever it is unsure
  (unknown clock, unstattable file). Pinned by test.

## Technical Context

**Language/Version**: Python 3.13. **Dependencies**: none added (stdlib
`threading`; `watchdog` already present but deliberately not used here).

**Testing**: pytest. No threads in the decision tests — `reconcile_once` takes an
injected sweep, so the logic is pure. The one thread test asserts only
daemon-ness and that no pass runs at startup.

**Constraints**: must not busy-loop on a bad interval; must not run at startup;
must not start unwired.

**Scale/Scope**: 175 plans; after the first pass, ~175 string comparisons per
tick and zero store reads in the steady state.

## Constitution Check

| Principle | Status | Evidence |
| :--- | :--- | :--- |
| **I. Spec-Is-Law** | PASS | The one open gap the plan named (watcher vs sweep) is *resolved with evidence and recorded*; the transitional question is left open, not invented away. |
| **II. Deferral-Terminates** | PASS | The index-read deferral names its owner (calliope) and its cost (one re-warm per restart). |
| **III. Contracts-Named** | PASS | `cheap_gate`, `is_probably_stale`, `reconcile_once` named with their guarantees. |
| **IV. Conformance-Checkable** | PASS | Each FR has a test; the gate's live verdicts were checked against the real store. |
| **V. Verify-Before-Done** | PASS | Gate verified live on both stored plans plus both conservative directions. |

No violations.

## Project Structure

```text
src/vault_mcp/
├── plan_reconcile.py    # NEW — interval, enablement, one pass, the thread
├── plan_freshness.py    # EXTEND — is_probably_stale(), cheap_gate, `gated`
└── server.py            # EXTEND — _plan_sweep() shared by verb + loop; clock cache

tests/test_plan_reconcile.py   # NEW
```

**Structure Decision**: `reconcile_once` takes the sweep as an argument, so the
pass's decisions and logging are testable with no thread, no clock and no
network. The thread wrapper holds no logic worth testing beyond "daemon" and
"waits first".
