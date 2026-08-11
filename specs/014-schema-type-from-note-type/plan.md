# Implementation Plan: Correct schema_type tagging for dissolved plans

**Branch**: `feat/plan-freshness` | **Date**: 2026-08-11 | **Spec**: [spec.md](./spec.md)

## Summary

`translator._schema_type()` reads only `@type` / `type` and filters them against
a Schema.org allowlist. Vault master-plans declare `note_type: Master-plan` and
no `@type`, so **every plan falls through to the `DigitalDocument` default** —
which is why `read_documents(schema_type="Master-plan")` returns `[]`. Make
`note_type` the authoritative source, passed through verbatim; re-tag existing
rows via F2's provenance-reconcile mode.

One function, plus a re-tag pass.

## Planning-context reconcile (Forge override)

| Planning-context item | Disposition |
| :--- | :--- |
| **Scope** — source declares `note_type: Master-plan` → `schema_type` matches; existing rows re-tagged → the filter returns them | **decided** → FR-001, FR-006, SC-002 |
| **Prerequisites** — none (independent of F1/F2/F4) | **decided** → but the re-tag *mechanism* reuses F2's backfill rather than adding a second traversal |
| **Decisions-slice** — tag from source frontmatter, not a caller argument [Claude] | **decided** → the value is derived from the note's own frontmatter inside the payload builder. Note this decision is only *satisfiable* caller-side: `dissolve_note` strips frontmatter before the wire, so the store can never derive it. That is the F1–F4 placement finding, already ruled. |
| **Inter-feature seams** — none | **decided** |
| **Gaps surfaced** — **the same defect exists fleet-wide, not just for plans**; the general fix is unscoped | **open** → NOT resolved here. This feature fixes the derivation for every note vault-mcp dissolves, which is the whole of vault-mcp's contribution; other writers to the store are untouched and unaudited. Surfaced, not closed. |
| **Size basis** — one field derivation plus a re-tag → S | **decided** |
| **Touches (RR)** — write `repo:calliope .../document-store.ts`, `apps/calliope/src/mcp/tools.ts` | **DIVERGENCE — see Open & risk** |

## Open & risk

- **[DIVERGENCE] Calliope is not where this is fixable.** The RR names calliope's
  store and tools. Verified: calliope stores `schema_type` faithfully — it
  writes exactly what the caller sends (`notes-sink.provenanceAttrs`:
  `attrs.set("schema_type", input.schema_type ?? "DigitalDocument")`) and returns
  it on read. The `DigitalDocument` it stores is not calliope's default winning;
  it is **the caller explicitly sending `DigitalDocument`**, because
  `_schema_type()` computed that. Calliope cannot do better: `dissolve_note`
  calls `strip_frontmatter` before the payload is built, so `note_type` never
  crosses the wire. **Calliope needs no diff.**

- **[BOUNDARY] Routing is deliberately untouched.** `note_to_payloads` routes to
  the entity store when `_raw_type()` (i.e. `@type`/`type`) names an entity type.
  Notes declaring `note_type: Book` and no `@type` therefore route to documents
  today. Teaching `_raw_type` about `note_type` would **re-route** those notes to
  entity tables — a data-movement change well outside "tag the type correctly",
  with no decision licensing it. So `_schema_type` learns `note_type` and
  `_raw_type` does not. The asymmetry is intentional and is recorded here rather
  than quietly removed. FR-005 pins it with a test.

- **[OPEN] The allowlist is bypassed for `note_type`, on purpose.**
  `_DOC_SCHEMA_TYPES` exists to stop an arbitrary `@type` — which any note may
  set to anything — becoming a stored label. `note_type` is Gate-controlled
  (validated on write, driven by `vault-mcp.schema.yml`'s `type_config`), so the
  same filter would only re-flatten the vault-native types this feature exists to
  preserve. Passing it through verbatim is the decision; the risk accepted is
  that a note hand-written around the Gate could store an odd label.

- **[MEASURED] The round trip is currently lossy.** `translator.row_to_payload`
  maps `note_type: row.get("schema_type") or "DigitalDocument"` — so a
  master-plan restored from the store today comes back as
  `note_type: DigitalDocument`. Fixing the capture side fixes the restore for
  free; US3 pins it.

## Technical Context

**Language/Version**: Python 3.13. **Dependencies**: none added.

**Storage**: unchanged — an existing attribute, correct value.

**Testing**: pytest over `note_to_payloads` / `_schema_type`, plus a round-trip
test through `row_to_payload`, plus a routing-unchanged regression test.

**Constraints**: routing MUST NOT change (FR-005). Re-tag MUST NOT mint a body
version (SC-004).

**Scale/Scope**: 135 `Master-plan` + 18 `CreativeWork` + 5 others in WBS alone.

## Constitution Check

| Principle | Status | Evidence |
| :--- | :--- | :--- |
| **I. Spec-Is-Law** | PASS | The fleet-wide gap is left explicitly open, not quietly widened into; the routing boundary is named. |
| **II. Deferral-Terminates** | PASS | The fleet-wide defect names why it is not closed here (other writers are outside vault-mcp). |
| **III. Contracts-Named** | PASS | The derivation order is named in contracts/. |
| **IV. Conformance-Checkable** | PASS | SC-002 is checkable live against the store. |
| **V. Verify-Before-Done** | PASS | Done requires the live filter returning a non-empty set where it returned `[]`. |

No violations.

## Project Structure

```text
src/vault_mcp/translator.py   # _schema_type() consults note_type first
tests/test_translator.py      # derivation order, routing unchanged, round trip
```

**Structure Decision**: the change is one function. The re-tag needs no new code
— F2's `backfill` mode already re-sends the payload and calliope reconciles
attributes without minting a generation, so a corrected `schema_type` lands
through the same path that corrected `mtime`. This is the F1 seam paying off a
third time: F3 changes `build_payload`'s output and nothing else moves.
