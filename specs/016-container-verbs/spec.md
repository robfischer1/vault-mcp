# Feature Specification: The Vault on Container Verbs

**Status**: Draft | **Input**: Master-plan feature F10 — "Git for Ideas" (vault)

## User Story
The document verb family is GONE from the live surface — measured
2026-08-13: a plan-freshness sweep of 176 WBS plans returned 176
`unknown verb` errors and zero reads, invisibly, because the seam maps
refusals to per-plan records. The vault's write path moves onto the
`_note` container family, which has existed with no caller since
2026-08-10 — this feature is where the family acquires its behavioural
contract.

**Acceptance**: dissolve and read route through the container surface
(write → `dissolve_note`, read → `materialize_note`); the reconcile loop
writes WITHOUT touching the vault file (structural: calliope's
dissolve_note never deletes — the vault-side delete stays
lifecycle_verbs' own step, which only the interactive dissolve tool
takes); a re-dissolve of unchanged content writes nothing (the sink's
container-grain no-op, surfaced as `deduped`); documents remain queryable
by their provenance attributes (schema_type + file_path now CROSS the
verb — calliope#142); a store miss is an empty documents list, never an
error.

## Requirements
- FR-001: `write_document(payload)` maps the phdb HTTP contract onto
  `dissolve_note` — the whole body is ONE block (blocks are authored,
  never inferred), subject→title, provenance verbatim — and normalizes
  the answer back to `{ok, table, id, deduped}`.
- FR-002: both reads ride `materialize_note` (by container_id / by
  source_path), projected onto the documents-row shape the consumers
  read; blocks join on the markdown separator (the sink's own rule).
- FR-003: `container_not_found` → `{documents: []}` (a miss, not a
  fault); every other refusal passes through untouched.
- FR-004: the bulk dissolution browse (de-tooled since C5) answers the
  surfaced enumeration gap honestly — the graph query needs a shape
  [OPEN, carried from the master plan] — instead of a phantom empty list.

## Success Criteria
The verb-pin test asserts all three call sites name the `_note` verbs
(the U6 regression's guard, retargeted); projection tests cover id read,
multi-block join, and the miss; the freshness + reconcile suites pass
unchanged (the engine never knew the verb).
