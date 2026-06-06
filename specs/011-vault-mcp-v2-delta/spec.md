---
title: "vault-mcp v2 Completion — Type Registry, Gate Hardening, Lifecycle Verbs"
parent: "vault-mcp#44"
repo: "vault-mcp"
bucket: "V2D:"
---

# vault-mcp v2 Completion — RFC

## Summary
Complete the Convention Gate by encoding the full per-@type governance catalog into the schema engine, hardening the Gate with value enforcement / deprecated-key cleanup / body validation / write-mode enforcement, refactoring provenance to the 3-property model (author_type / author_level / ai_model), and shipping two lifecycle verbs (materialize + atom emit). The foundation (Schema Engine, Gate, Provenance, Compute Receiver) shipped in PR #80; this RFC covers the `[DELTA]` identified in MCP-requirements.md (FR-1 through FR-49) and MCP-schema-catalog.md.

## Goal
A caller that knows none of the vault's filing rules can create any of the ~25 governed @types through the Gate, and the Gate deterministically stamps, validates, routes, and protects every field — including per-type required fields, value-constrained enums, write-mode rejection, deprecated-key migration, and provenance attribution — with zero governance prose needed in the caller's context. Lifecycle verbs (materialize, atom emit) provide the remaining write paths beyond create/update.

## Non-goals
- **Enforcement Integration** (FR-32) — direct-edit blocking hooks + governance prose retirement. Parked until Gate is proven stable in production.
- **dissolve verb** (FR-47) — v2 non-goal; MCP-destined later.
- **Bases-over-Dataview rule** (FR-34 subset) — retires with the PCW move. Drop the dataview-rejection check.
- **Drift-sweep** (FR-49 follow-on) — batch validation of existing files against TypeConfig. Separate future initiative; this RFC builds write-mode enforcement (block agent-create) and the materialize verb.
- **phdb pipeline constraints** — FR-41 (sender_name immutability), FR-42 (sentTo triples), FR-43 (typed columns per @type) are phdb-side data-layer responsibilities. vault-mcp informs but does not enforce. Only FR-44 (predicate triple emission on Gate write) is vault-mcp's job.
- **Template folder binding** (FR-40) — Web Clipper templates land in Inbox via Obsidian plugin, not via vault-mcp. The Gate routes Inbox writes by @type (FR-21); the template → @type mapping is Obsidian-side config.

---

## Component architecture

### 1. Schema Catalog Extension
Expand `vault-mcp.schema.yml` and `schema.py` to hold per-@type configs, routing discriminators, pillar visual defaults, and value vocabularies.

- **Type Registry**
  - TypeConfig dataclass + YAML loader — acceptance: every @type in the catalog (Person, Place, Organization, Area, Project, CreativeWork, Product, DietarySupplement, SoftwareApplication, HowTo/Routine, Recipe, DefinedTerm, Report, CollectionPage, Artifact/Dataset, Atom subtypes, Handoff, Inventory, Web Content subtypes) has a parseable TypeConfig with required fields, value constraints, freeform fields, and write-mode
  - Per-type value enforcement integration — acceptance: the Gate rejects a note missing a required field or carrying an off-vocabulary constrained value, citing the specific type and field
  - Write-mode per type — acceptance: each TypeConfig carries write-mode (agent / materialize-only / pure-DB); the Gate reads this on create to determine admission

- **Routing Completion**
  - Discriminator routing — acceptance: Product with category=Supplement routes to Supplements/; Product with category=Tools routes to Things/; Periodical routes to Organizations/; Place subtypes (Restaurant, Store, LocalBusiness, CafeOrCoffeeShop, HealthClub, Residence) route to Places/
  - Consumed-media redirect routing — acceptance: Book, Movie, TVSeries, VideoGame, PodcastSeries, WebSite[YouTube], WebSite[Twitch] resolve to Atlas/Indexes/{Type}
  - Work-doc + content-doc + misc routing — acceptance: Routine→Outputs/Rob Inc/Routines, DefinedTerm→Tao/Definitions, ShortStory→Tao/Stories, Recipe→References/Recipes (external) or Responsibilities/Cooking (personal), Review/Question→Journal, Report→Journal or Atlas/Periodic (by level), Garden Concept→Garden, Web Content→by source

- **Pillar Defaults**
  - PillarDefaults config + auto-stamp — acceptance: a note created in any pillar receives the correct nn_color (Catppuccin Frappe) and nn_icon (Lucide) without the caller specifying them

- **Value Vocabularies**
  - Status enum + repairs — acceptance: status validated against {Active, Completed, Archived, Abandoned, Stub, Pending}; "" repaired to Pending; "Archive" repaired to Archived; singleton lists unwrapped
  - Per-type constrained enums — acceptance: level (day/week/month/quarter/year), additionalType (MentalModel/DomainKnowledge/PhilosophicalFramework for Garden; SocialContact/PublicFigure for Person), captured_when (realtime/retro), resolution (open/resolved), polarity (done/not-done), geo format ("lat,long"), suitableForDiet (Schema.org diet enum) all validated on write

### 2. Provenance Refactor
Migrate from single-axis `provenance` to the 3-property model: `author_type` (category), `author_level` (gradient), `ai_model` (attribution).

- **Three-Property Model**
  - AuthorType enum + schema update — acceptance: author_type validated against {human, ai, external}; author_level retains the existing 7-level Provenance spectrum; ai_model accepted as caller-declared string
  - stamp/transition produce 3 fields — acceptance: every Gate write stamps author_type + author_level + ai_model in frontmatter (ai_model only when actor=AGENT)
  - No-downgrade rule extended — acceptance: author_type transitions follow the same monotonic rule as the existing provenance spectrum (AI-touched content never silently reverts to human)

- **Backwards Compatibility**
  - API migration — acceptance: existing callers that pass actor + mode continue to work; WriteResult includes author_type, author_level, ai_model
  - Frontmatter emission — acceptance: Gate emits author_type, author_level, ai_model (stops emitting single `provenance` key); existing notes with `provenance:` are read and mapped to author_level on update

### 3. Gate Hardening
Additive validation, transformation, and enforcement logic in the Gate.

- **Frontmatter Stamping Expansion**
  - identifier autogen — acceptance: `identifier` defaults to kebab-case slug of `name` on create; caller can override
  - status default + enforcement — acceptance: `status` defaults to Pending on create; invalid values rejected with suggestion
  - note_type Title-Case normalization — acceptance: `note_type: plan` → `note_type: Plan` on write
  - @-prefixed key quoting — acceptance: `'@type'` normalized to `"@type"` in emitted frontmatter (FR-7)
  - Forensic layer permitted keys — acceptance: origin_date, date_precision, source, predicate accepted as valid extra_fields (FR-6)

- **Deprecated Key Migration**
  - Rename map on write — acceptance: when the Gate encounters a deprecated key (title→name, id→identifier, published→datePublished, personal_rating→ratingValue, genres→genre, actors→actor, cost→price, GEO→geo, topics→keywords, organization→worksFor, street_address→address, data_source→source, aired→startDate/datePublished, length→duration), it renames preserving the value
  - Dead-key deletion on write — acceptance: keys in the dead set (type, sub_type, year, subtitle, online_rating, viewers, recommended_by, recommendedBy, template_type, legacy_type, sent) are unconditionally removed; `project` is NOT deprecated

- **Body Validation**
  - Angle-bracket placeholder rejection — acceptance: bare `<Name>`, `<topic>`, etc. outside code fences/backticks rejected with "use {Name} instead" guidance (FR-33)
  - Consumed-media body-empty — acceptance: consumed-media Entity files reject non-whitespace body content (FR-30)
  - Template fence validation — acceptance: System/Templates/ files reject literal leading `---`; require Templater fence (FR-31)

- **Tag Enhancements**
  - Reserved tag advisory — acceptance: agent writes introducing #todo, #starred, #try, #review, #follow-up emit a warning (not a rejection) in the WriteResult (FR-13)
  - Inline tag escaping — acceptance: imported body text (References/, Records/) has bare `#tags` escaped to `\#tag`; Archives/, System/Templates/, functional URLs, and #activity/processed are exempt (FR-15)

- **Link Validation**
  - prev/next resolution — acceptance: `prev`/`next` frontmatter values that don't resolve to an existing file are rejected (FR-35)
  - Advisory: missing recommended_links — acceptance: warning (not rejection) when `isBasedOn` missing for Artifacts/Records-derived analyses, or `up:` missing (with exception list for folder-note/pillar-root) (FR-36)

- **Write-Mode Enforcement**
  - Block agent-create for materialize-only types — acceptance: Gate.create_note with actor=AGENT for Handoff, Task, Plan, consumed-media types raises a specific rejection ("this type is materialize-only; use the materialize verb")
  - Block vault-write for pure-DB types — acceptance: Gate.create_note for AI-observed atoms (decision, reversal, tension, pushback), Conversation, Event raises a specific rejection ("this type is pure-DB; use atom emit or phdb directly")

- **Write-Protection Expansion**
  - Provenance-based body protection — acceptance: notes with author_type human or external block AI body changes (metadata-only OK); exception: Outputs/ Articles with @type=Article remain human-mutable despite AI body edits (FR-29)

- **Filename Conventions**
  - Atom slug autogen — acceptance: atom creates auto-generate filename YYYY-MM-DD-{type}.{seq|unixts} (FR-24)
  - Forbidden patterns — acceptance: numeric folder prefixes and "Pillar -- " filename prefixes rejected (FR-25)

- **Non-Pillar Write Rejection**
  - Directory guard — acceptance: note creation in non-pillars (attachments/, .obsidian/, System/) and dead Digital-Kitchen dirs is rejected (FR-22)

### 4. Lifecycle Verbs
New write paths beyond create/update.

- **Materialize**
  - Materialize verb contract — acceptance: `vault-mcp materialize` accepts a structured payload (title, @type, directory, frontmatter fields, body) and writes through the Gate with mode=COMPUTE, bypassing write-mode rejection for materialize-only types
  - Template rendering — acceptance: materialize uses a `note.stub` template for consistent rendering; same payload produces byte-identical output

- **Atom Emit**
  - AI-observed atom verb — acceptance: `vault-mcp atom --type {type} {slug}` (and MCP tool equivalent) accepts decision/reversal/tension/pushback payloads and routes to phdb session_events, skipping the vault filesystem entirely
  - Payload contract per type — acceptance: decision carries polarity + reversed_by; reversal carries reverses + trigger + position_before + position_after + captured_when; tension carries position_a + position_b + held_since + resolution + captured_when; pushback carries from + challenge + response + position_changed + captured_when

- **Predicate Triple Emission**
  - Triple emission on write — acceptance: when a note with `predicate:` frontmatter is created or updated, the Gate emits typed-graph triples to the phdb triple store; predicate names validated against the phdb `predicates` table (FR-44)

---

## Dependencies
- **Gate Hardening** depends on **Schema Catalog Extension** (reads TypeConfig for per-type validation, write-mode lookup, value constraints)
- **Gate Hardening** (write-protection expansion, FR-29) depends on **Provenance Refactor** (uses author_type for provenance-based body protection)
- **Lifecycle Verbs** depends on **Schema Catalog Extension** (write-mode determines which types are materialize-only vs pure-DB)
- **Lifecycle Verbs** (atom emit + predicate triples) introduces a new dependency on **phdb** (SQLite read/write)
- **Provenance Refactor** is independent of Schema Catalog Extension (different YAML section, different code modules)

## Impact
| Component | Impact (0-10) | Rationale |
| :--- | :--- | :--- |
| Schema Catalog Extension | 7 | Every Gate caller inherits new type-aware validation; schema YAML structure changes; all routes recalculated |
| Provenance Refactor | 5 | Cross-cutting: touches schema, provenance.py, gate.py, frontmatter emission; existing `provenance` field renamed |
| Gate Hardening | 4 | Additive validation may reject previously-accepted writes; deprecated key migration changes stored frontmatter |
| Lifecycle Verbs | 3 | New capabilities; no existing code changes; introduces phdb dependency |

## Decision Log
| Decision | Resolution | Rationale | Alternatives considered |
| :--- | :--- | :--- | :--- |
| phdb integration boundary | vault-mcp gets a thin write-only phdb client for atom-emit (write session_events) and predicate triples (write). Materialize stays payload-only (caller passes data, matching existing CR pattern). FR-41/42/43 stay phdb-side. | Atom emit's purpose is to skip the vault filesystem — the caller shouldn't need to know phdb's schema. Materialize follows the proven CR pattern. phdb I/O is data access, not LLM — doesn't violate "No LLM in vault-mcp." | Full phdb client (adds read dependency for materialize — caller already has the data); No phdb client (atom emit becomes a no-op, defeats unified write API) |
| Provenance migration strategy | Additive: author_level = existing Provenance enum; add AuthorType enum + ai_model string. Stop emitting single `provenance` key. Map existing `provenance:` values to author_level on read. | Backwards-compatible; no bulk migration needed. Existing notes with `provenance:` continue to work on update. | Breaking rename (bulk frontmatter migration — high blast radius, no immediate user value) |
| Deprecated key handling | On write (Gate normalizes on every create/update). No read-time migration or batch backfill. | Gate writes are the chokepoint — every future write gets clean keys. Batch backfill is a separate scope with different risk profile. | On read (transparent remapping — complex, two representations coexist indefinitely); Batch migration (one-time script — blast radius, doesn't prevent future occurrences) |
| Write-mode enforcement location | Gate checks TypeConfig.write_mode on create_note. Schema stores the config; Gate enforces at write time. | Single enforcement point. TypeConfig is already loaded for per-type validation — write-mode is one more field. | Schema-level route rejection (too early — can't distinguish agent-create from materialize); Separate middleware (unnecessary layer) |
| materialize verb architecture | New module (lifecycle.py). Accepts structured payload, writes through Gate with mode=COMPUTE. Does NOT query phdb itself in v2 — receives payload from caller. | Matches the existing CR pattern (payload in, note out). Keeps vault-mcp phdb-free for materialize. Atom-emit is the only phdb-write path. | Gate method (conflates create/update semantics with lifecycle); phdb-reading materializer (adds read dependency; caller already has the data) |
| Atom emit phdb write | vault-mcp writes directly to session_events via thin SQLite client (PHDB_PATH env var). | Atom emit's whole purpose is to skip the vault filesystem. The caller (Claude Code hook) shouldn't need to know phdb's schema. | Caller writes phdb (vault-mcp becomes a no-op for atoms — defeats the purpose of a unified write API) |
| Drift-sweep scope | Deferred follow-on, not in this build. | Write-mode enforcement blocks new bad files. Validating existing files is a batch job with different testing/rollout needs. | Include in this build (scope creep — drift-sweep touches every existing file, not just new writes) |

## Clarifications
1. **phdb integration boundary** — Resolved: thin write-only client. Materialize stays payload-only (no phdb read); atom emit writes session_events; predicate triples write to triples table. FR-41/42/43 stay phdb-side.
2. **Drift-sweep scope** — Resolved: deferred entirely. This build ships write-mode enforcement (blocks new agent-creates for materialize-only + pure-DB types). Batch validation of existing files is a separate initiative.

## Open follow-ons
- **Drift-sweep** — batch validation of existing files against TypeConfig for materialize-only types (FR-49 "a future drift-sweep validates existing files for schema-consistency")
- **Enforcement Integration** — direct-edit blocking hooks (FR-32) + governance prose retirement, once Gate is production-proven
- **dissolve verb** (FR-47) — vault note → commit → diff → push to phdb → delete file
- **Bases retirement** — rethink CollectionPage/Report body rule once Bases is gone (PCW move)
- **Consumed-media body-content retirement** — once all consumed-media entities are DB-canonical stubs, the body-empty enforcement (FR-30) may simplify to "body is always the redirect notice"
