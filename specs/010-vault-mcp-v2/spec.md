---
title: "vault-mcp v2 — Convention Gate + Compute Receiver"
parent: ""
repo: "vault-mcp"
bucket: "VMCP2"
---

# vault-mcp v2 — Convention Gate + Compute Receiver — RFC

## Summary

vault-mcp v2 relaunches the server as the **Create** layer of the MCP triumvirate
(Create / Consume / Do = vault-mcp / phdb / board-mcp). It stops enforcing the
vault's programmatic governance with prose loaded into every AI session and starts
enforcing it with deterministic code. Two co-equal capabilities carry this: a
**Convention Gate** — a write API that generates correct frontmatter, validates
tags against a closed glossary, routes files to the right pillar, enforces
per-directory write-protection, and stamps provenance — and a **Compute Receiver**
that turns structured payloads from phdb's compute jobs into vault notes via pure
template rendering. Governance rules move out of prose docs into a single
machine-readable schema config resolved by environment variable, so the same
deterministic server works for any Obsidian-compatible vault and for public release.
No LLM runs inside vault-mcp.

## Goal

Done looks like: **every AI write to the vault routes through vault-mcp, and an
agent that knows none of the filing rules cannot produce a non-compliant note.**
Concretely and falsifiably:

- A create request supplying only semantic fields (title, type, body, intent)
  produces a fully schema-compliant note — correct frontmatter, validated tags,
  correct pillar directory, stamped provenance — with zero governance prose in the
  agent's context.
- An invalid write (unknown tag, missing required field, protected directory) is
  rejected with a structured, actionable error and **no file is written**.
- All governance rules consumed by the Gate come from one external config file
  named by `VAULT_MCP_SCHEMA`; none are hardcoded in `src/vault_mcp/`.
- The Compute Receiver renders a conforming payload into a note deterministically
  (same payload + template → byte-identical body) with no LLM call in the path.

## Non-goals

- **No LLM inside vault-mcp.** All model calls stay in phdb (Consume layer);
  vault-mcp is deterministic validation + template rendering only.
- **No vault-wide author_type → provenance backfill** in this initiative
  (forward-only; backfill is a parked follow-on).
- **No infrastructure convergence** (nas01 HTTP transport, Postgres migration) —
  parked.
- **No phdb plugin recompute architecture** (`compute_periodic()`,
  `periodic_rollups` table, incremental guards) — phdb-side, parked. vault-mcp
  only publishes the payload contract it will consume.
- **No re-implementation of read tools** — already retired; the Obsidian CLI and
  existing parser/index tools handle reads.
- **No bidirectional provenance feedback loop, consolidation gate, or PCW /
  identity-based provenance** — all parked.
- **No vault-specific schema content shipped in the repo** — the schema document
  lives outside `src/`, supplied by config.

## Component architecture

Component → Epic, Capability → Feature, Slice → Story. No Tasks.

### Schema Engine

The deterministic foundation: one external machine-readable governance document,
loaded and validated once, queried by every other component.

- **Schema config loading**
  - Load governance schema from an external config path — _acceptance: the server
    resolves its schema from the path named by `VAULT_MCP_SCHEMA`; a missing or
    unreadable path fails startup with an error naming the variable and the path._
  - Validate the schema document on load — _acceptance: an internally inconsistent
    schema (a route referencing an unknown pillar, a duplicate tag entry) is
    rejected at startup with an error identifying the offending entry._
- **Tag glossary lookup**
  - Closed-glossary tag validation — _acceptance: a candidate tag is reported
    valid/invalid against the loaded glossary; an unknown tag returns the nearest
    known matches._
- **Pillar routing table**
  - Resolve target directory from note attributes — _acceptance: given a note's
    type/pillar attributes the engine returns exactly one canonical target
    directory, or a structured error when no route matches._

### Convention Gate

The deterministic write API. Wraps the Obsidian CLI so Obsidian's own indexing
and plugins fire on every write. Every vault write passes through here.

- **Note creation**
  - Generate compliant frontmatter from caller inputs — _acceptance: a create
    request supplying only semantic fields yields a note whose frontmatter contains
    every schema-required field, correctly typed; caller-supplied governance fields
    are never trusted verbatim._
  - Route and write a new note through Obsidian — _acceptance: a valid create
    request produces a note at the schema-resolved directory, visible in Obsidian's
    index without a manual reindex; the tool returns the canonical path._
  - Structured success/echo contract — _acceptance: every successful write returns
    the final path, the resolved frontmatter, and the stamped provenance so the
    caller can verify exactly what was written._
- **Write validation & rejection**
  - Reject unknown tags — _acceptance: a write carrying a tag outside the glossary
    is rejected with a structured error listing the offending tag(s) and nearest
    valid alternatives; no file is written._
  - Reject missing or invalid required fields — _acceptance: a request missing a
    schema-required field, or supplying a malformed value, is rejected with a
    field-level error; no partial note is created._
- **Write-protection enforcement**
  - Per-directory protection rules — _acceptance: a write to a protected directory
    is rejected with that directory's specific message; the four protection classes
    (body-immutable, fully-immutable, compute-only, voice-only) each return their
    distinct error._
  - Update with protection awareness — _acceptance: an update to an allowed field
    on an editable note succeeds, while an update touching a protected region
    (e.g. a Records body) is rejected; metadata-only updates may be permitted per
    schema._
- **Note update**
  - Update allowed fields preserving lineage — _acceptance: an update changes only
    the requested fields, preserves untouched content byte-for-byte, and advances
    provenance per the transition rules._
- **Write observability**
  - Emit write diffs to the Consume layer — _acceptance: each successful write
    produces a structured diff record (path, fields changed, provenance) for
    downstream ingestion; emission failure never blocks or reverts the write._

### Provenance System

Replaces the coarse `author_type` field with a single-axis spectrum that becomes
ground truth once direct edits are blocked.

```
human ← human-edited ← human-revised ← [collaboration] → ai-assisted → ai-compiled → ai-metadata → ai-computed
```

- **Provenance taxonomy**
  - Define the single-axis provenance spectrum — _acceptance: the seven ordered
    levels are encoded as a closed enumeration; any value outside the set is
    rejected by the Gate._
- **Provenance stamping**
  - Stamp provenance on every write — _acceptance: no note exits the Gate without a
    provenance value, and the value is derived from the write's context (session
    vs. compute, human vs. agent), not trusted from the caller verbatim._
  - Provenance transition on handoff — _acceptance: when a write modifies a note,
    the resulting level follows the defined transition (a human edit to an
    `ai-assisted` note becomes `human-edited`) and never silently downgrades
    AI-touched content to `human`._

### Compute Receiver

Accepts structured payloads from phdb's periodic compute jobs and templates them
into notes. Pure rendering — no LLM.

- **Compute payload contract**
  - Publish the structured compute-payload contract — _acceptance: vault-mcp
    exposes a documented payload schema; a conforming payload is accepted, a
    non-conforming one is rejected with a schema-level error, and no LLM call occurs
    anywhere in the path._
- **Template rendering**
  - Render a payload into a note via template — _acceptance: a conforming payload
    plus a named template produces a deterministic note (same payload + template →
    byte-identical body), stamped `ai-computed`._
  - Compute-only write path to protected directories — _acceptance: the compute
    path is the only writer permitted into compute-only directories (e.g. Atlas); a
    session-originated write to the same target is still rejected._

### Enforcement Integration

Closes the loop: block direct AI edits so the Gate is the only write path, then
retire the prose the Gate replaces. Cross-repo — artifacts land in the vault, not
in `src/vault_mcp/`.

- **Direct-edit blocking** _(vault-side: `.claude/settings.json`)_
  - Hooks block direct AI edits to vault markdown — _acceptance: with enforcement
    active, a direct Edit/Write to a vault `.md` by an AI agent is blocked and
    redirected to the Gate; writes through the Gate succeed._
- **Governance prose retirement** _(vault-side: governance docs)_
  - Retire mechanical schema prose superseded by the Gate — _acceptance: the
    mechanical `SCHEMA-*.md` rules, the tag-glossary-as-prose, and path-scoped
    schema rules named in the pitch are removed or reduced to a pointer at the Gate;
    cold-start governance load for vault writes drops from the current ~30K-token
    baseline, with the post-retirement figure recorded in `DECISIONS.md`._

## Dependencies

- **Convention Gate** depends on **Schema Engine** (rules, glossary, routes).
- **Provenance System** depends on **Schema Engine** (taxonomy + transition rules
  live in the schema config).
- **Convention Gate** depends on **Provenance System** (stamps on every write).
- **Compute Receiver** depends on **Schema Engine**, **Provenance System**, and
  **Convention Gate** (it writes through the same protected write path).
- **Enforcement Integration** depends on **Convention Gate** — direct-edit blocking
  cannot land until the replacement write path is functional and proven.
- **Cross-initiative:** Compute Receiver's payload contract is consumed *from*
  phdb's parked plugin-recompute work. vault-mcp publishes the contract and is
  built/tested against recorded fixtures, so it is **not blocked** by live phdb.
- **Cross-repo:** Enforcement Integration artifacts (`.claude/settings.json` hooks,
  governance-doc retirement) land in the vault repo, sequenced after the Gate.

## Impact

| Component | Impact (0-10) | Rationale |
| :--- | :--- | :--- |
| Schema Engine | 9 | Foundation every other component reads; replaces the prose governance layer. |
| Convention Gate | 9 | Changes how every AI agent on every platform writes to the vault. |
| Enforcement Integration | 8 | Blocks all direct edits across agents; high behavioral change, cross-repo lockout risk if mis-sequenced. |
| Provenance System | 6 | Schema/frontmatter change replacing `author_type`; forward-only limits blast radius, but seeds a vault-wide backfill. |
| Compute Receiver | 5 | Additive new path; depends on phdb contract but isolated behind fixtures. |

## Decision Log

| Decision | Resolution | Rationale | Alternatives considered |
| :--- | :--- | :--- | :--- |
| Where does the governance schema live? | One external config document at the Forge root, path resolved via `VAULT_MCP_SCHEMA` env var. | Constitution I forbids vault-specific paths in `src/`; public release needs externalized, instance-specific config. | In-vault note (it is not a note); in the public repo (leaks instance config). |
| Schema document format | Single YAML document. | Nested glossary / routes / protection rules read clearly; comments supported; ubiquitous. | TOML (awkward nesting); JSON (no comments). |
| How does the Gate physically write? | Wrap the Obsidian CLI (`cli_client.py`). | Rob's explicit choice — guarantees Obsidian's indexing and plugins fire on every write. | Direct filesystem (deterministic but bypasses Obsidian index); hybrid. **Constitution III mitigation:** the CLI client is mocked in tests (the same rule the constitution already mandates for REST-backed tools — never a live instance), so the write path stays deterministically testable. |
| Constitution II nuance (writes require live Obsidian) | Accept: the Gate's write path is an Obsidian-augmented capability, not part of the headless parser-layer guarantee. The read/parser layer remains fully functional without Obsidian. | Principle II scopes the "works without Obsidian" guarantee to the parser layer; writes are a new augmentation, consistent with how REST tools are treated. | Filesystem-direct writes to preserve headless writes (rejected per write-path decision above). |
| Provenance rollout reach | New `provenance` field, forward-only; existing `author_type` values left in place. | Smallest blast radius, ships fastest, no risky vault-wide migration on the critical path. | Full migration now (parked as backfill follow-on); dual-write transition. |
| Provenance field naming | New `provenance` field replacing `author_type` going forward. | Single-axis 7-level spectrum is clearer and machine-checkable; avoids colliding with legacy `author_type` values. | Overload `author_type` (rejected — coarse, value collisions). |
| Compute Receiver scope | Ships in this initiative alongside the Convention Gate as a co-equal component. | Rob's choice; shared template + protected-write infrastructure; isolated from live phdb via fixtures. | Phase behind the Gate; split into a separate RFC. |
| LLM placement | No LLM in vault-mcp; phdb (Consume) owns all model calls; vault-mcp does deterministic validation + pure template rendering. | Determinism, testability, clean Create/Consume separation. | Embed light LLM in vault-mcp (rejected — breaks determinism and the triumvirate split). |
| Module structure vs. constitution's 4-module discipline | Add new deterministic modules (schema, gate/writer, provenance, templates) beyond the current shape; the implementation plan must itemize and justify each per the constitution. | Distinct responsibilities; `server.py` already exposes 48 tools and cannot absorb more. | Cram into `server.py` (rejected — already oversized). |
| Enforcement sequencing | Direct-edit hooks and governance-prose retirement land in the vault repo **after** the Gate is functional and proven. | Cannot block direct edits until the replacement path works — otherwise agents are locked out. | Simultaneous cutover (rejected — lockout risk). |

## Open follow-ons

- **author_type → provenance vault-wide backfill** (deferred by the forward-only decision).
- **phdb plugin recompute architecture**: plugin-declared `compute_periodic()`,
  centralized `periodic_rollups` table, mtime-like incremental guards, per-plugin
  formatting with global granularity config.
- **Infrastructure convergence**: all three MCPs on nas01, HTTP transport, Postgres migration.
- **Bidirectional provenance feedback loop**: track human reclassification of
  AI-set tags/status as a learnable correction signal.
- **Consolidation gate in forge-pipeline**: projecter-layer supersession/absorption
  surfacing when new top-level work is created.
- **Obsidian as render surface / PCW**: vault becomes a materialization surface;
  identity-based provenance (API keys / PGP) replaces convention-based stamps.

---
Definition of Ready gate:
[x] every decision resolved
[x] architecture complete (Component → Capability → Slice)
[x] acceptance derivable per slice
[x] dependencies stated
[x] impact estimated
