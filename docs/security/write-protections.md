# Convention Gate — write-protection / deny surface

Security model for the vault-mcp Convention Gate. The Gate is the single write
chokepoint over an Obsidian-compatible vault; this document enumerates every
rule that **denies or constrains** a write, its trigger, and the caller-facing
error. It is the MSL-applicability + obligations record for the deny surface
(V2D Epic #104, Feature #124 and related).

## Threat model (scope)

The Gate is a deterministic, local, single-user write API (no network listener
of its own; the optional REST augmentation binds loopback). The protections
below are **integrity / governance** controls — they prevent an automated
caller (an AI agent) from corrupting vault structure, overwriting human-authored
content, or writing records that belong in the database rather than the vault.
They are not authentication controls; the trust boundary is "any caller that can
invoke the Gate is already inside the user's machine."

## Deny rules

| Control | Trigger | Caller-facing error | Source |
|---|---|---|---|
| **Write-mode: pure-DB** | create of a `@type` whose `write_mode: pure-DB` (AI-observed atoms, Conversation, Event) | "{type} is pure-DB; use atom emit or phdb directly" | `_check_write_mode` (#126) |
| **Write-mode: materialize-only** | agent-create (mode ≠ COMPUTE) of a `materialize-only` type (Handoff, Task, Plan, consumed-media) | "{type} is materialize-only; use the materialize verb" | `_check_write_mode` (#125) |
| **Directory write-protection: fully-immutable** | any write into an `fully-immutable` directory (e.g. Artifacts) | schema-supplied error | `check_protection` |
| **body-immutable** | body-touching write into a `body-immutable` directory (e.g. Records) | schema-supplied error | `check_protection` |
| **compute-only** | non-COMPUTE write into a `compute-only` directory (e.g. Atlas) | schema-supplied error | `check_protection` |
| **voice-only** | non-human write into a `voice-only` directory (Tao / Hammer / Diuniverse / Garden) | schema-supplied error | `check_protection` |
| **Provenance body-protection** | AI body edit to a note whose `author_type` is human/external (metadata-only OK; Outputs/ Article exception) | "human/external-authored; AI may edit metadata only" | `update_note` (#128) |
| **Non-pillar guard** | create into a non-pillar / forbidden directory | "writes are not allowed outside the vault pillars: {dir}" | `_check_directory` (#133) |
| **Required / value enforcement** | missing required field, off-vocabulary value, bad format, invalid status | field-and-type-specific `FieldError` | `_enforce_type_rules` |
| **Body rules** | bare `<placeholder>`, non-empty consumed-media body, literal template fence | `BodyError` | `_validate_body` |
| **Link resolution** | `prev`/`next` that does not resolve to an existing note | `LinkError` | `_validate_links` |
| **Unknown tag** | tag outside the closed glossary | `TagError` (with nearest-match hint) | `_validate_tags` |

## Advisory (non-blocking) signals

Surfaced in `WriteResult.warnings`, never block the write: reserved-tag use by an
agent (#todo/#starred/…), and missing recommended links (`isBasedOn`, `up:`).

## Obligations / notes

- All deny rules are deterministic and unit-tested against single-file fixtures
  (`tests/test_gate.py`); no live Obsidian instance is required.
- The `write_mode` and directory-protection vocabularies are schema-driven
  (`vault-mcp.schema.yml`), so the policy is auditable as config, not buried in code.
- COMPUTE mode is the sole bypass for materialize-only — it is reachable only via
  the Compute Receiver / materialize verb, not the agent create path.
