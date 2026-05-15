# Feature Specification: Obsidian Bases Support

**Feature Branch**: `001-bases-support`

**Created**: 2026-05-15

**Status**: Draft

**Input**: Add Obsidian Bases support to vault-mcp — parse, execute, write, and validate the YAML "Bases" configurations used by Obsidian folder views, exposed as MCP tools for agents and scripts.

## Clarifications

### Session 2026-05-15

- Q: Should Phase 1 support complex HTML-rendering formula expressions (html/if/map/join/replace-regex) or defer them? → A: Simple-first. Phase 1 covers Tier 1 (property access, filter/length, hasLink). Tier 2 (html/if/map/join/replace) deferred to Phase 2 with a clear "unsupported expression" error.
- Q: Should the `cards` view type (1 instance in vault, depends on Tier 2 formulas) be included in Phase 1? → A: Defer cards entirely to Phase 2. Parser recognizes `cards` as valid YAML but execute returns an "unsupported view type" message.
- Q: When the formula evaluator encounters an unsupported expression, what behavior? → A: Return null for that formula column, plus a warning entry in result metadata listing which formulas couldn't be evaluated and why. Query still succeeds.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Execute a Base Query (Priority: P1)

An agent working in the vault needs to answer "which Plans are active?" Instead of hand-coding filter logic against the vault index, the agent calls an MCP tool with a reference to the base defined in the Plans folder note. The tool executes the base's filters and formulas against the current vault state and returns the matching notes with their computed columns, restricted to the "Active" view.

**Why this priority**: This is the core value proposition — replacing ad-hoc reimplementation of filter logic with direct execution of the declared bases. Every other capability (parse, write, validate) is secondary if the query path doesn't work.

**Independent Test**: Can be fully tested by pointing the tool at a folder note containing an inline base code block and verifying the returned notes match what Obsidian renders for the same view.

**Acceptance Scenarios**:

1. **Given** a markdown file contains an inline base code block with filters and a named view, **When** an agent calls the execute tool with that file path and view name, **Then** the tool returns a list of matching notes with their computed formula columns.
2. **Given** a base filters on `file.folder == "Outputs/Plans"` and a view filters on `note["status"] != "complete"`, **When** executed, **Then** only notes in that folder whose status is not "complete" are returned.
3. **Given** a base defines formulas using `note["property"]` and `file.mtime`, **When** executed, **Then** the result includes those computed values alongside each matching note.
4. **Given** no view name is specified, **When** the tool executes the base, **Then** the base-level filters are applied and all defined formula columns are returned (no view-specific filtering or column restriction).
5. **Given** an Atlas folder note uses `file.hasLink("Topic Name")` as its filter, **When** executed, **Then** only notes that contain a wikilink to that topic are returned.

---

### User Story 2 - Parse and Introspect a Base (Priority: P2)

An agent needs to understand what a base declares — its filters, formulas, and views — without executing it. The agent calls a parse tool that returns the structured representation: the filter tree, the named formulas, and the list of views with their configurations.

**Why this priority**: Introspection enables agents to reason about bases (e.g., "does this folder note already have a view for status?"), which is a prerequisite for intelligent writes. It also lets agents display base structure to users without running queries.

**Independent Test**: Can be tested by parsing a known base and asserting the returned structure matches the expected filter tree, formula definitions, and view configurations.

**Acceptance Scenarios**:

1. **Given** a markdown file with an inline base code block, **When** an agent calls the parse tool, **Then** the tool returns the filter tree, formula definitions, and view list as structured data.
2. **Given** a file contains multiple inline base code blocks, **When** the agent calls the parse tool, **Then** all bases in the file are returned, each with its position in the document.
3. **Given** a base uses nested `and:`/`or:`/`not:` filter combinations, **When** parsed, **Then** the filter tree preserves the logical nesting structure.
4. **Given** a base contains a `cards` view type, **When** parsed, **Then** the parser recognizes and returns the view structure without error, even though execution of `cards` is deferred.

---

### User Story 3 - Write or Update a Base (Priority: P3)

An agent generates a new base from a folder's frontmatter conventions (e.g., creating a view that shows all notes with `note_type: plan` sorted by status) and writes it into a folder note. If the folder note already has an inline base code block, the agent updates it in place; the surrounding markdown is preserved byte-for-byte.

**Why this priority**: Write capability closes the loop — agents can not only consume existing bases but create and evolve them. However, read/query without write still delivers substantial value, so this is P3.

**Independent Test**: Can be tested by writing a base into a markdown file and then reading the file back to verify: (a) the base YAML is valid and parseable, (b) all markdown outside the code block is byte-identical to the original.

**Acceptance Scenarios**:

1. **Given** a markdown file with no existing base code block, **When** an agent writes a new base, **Then** a properly formatted inline base code block is appended to the file.
2. **Given** a markdown file with an existing inline base code block, **When** an agent updates that base, **Then** the code block contents are replaced and all surrounding markdown (before and after the code block) is byte-identical to the original.
3. **Given** a file contains multiple inline base code blocks, **When** an agent specifies which base to update (by index or by view name), **Then** only the targeted code block is modified.

---

### User Story 4 - Validate a Base Before Writing (Priority: P4)

Before writing a base, an agent submits it for validation. The validator catches common error classes that cause Obsidian to refuse to render: unquoted special characters in YAML strings, mismatched quotes inside formula expressions, and references to formulas that are not defined in the base.

**Why this priority**: Validation is a safety net for writes — important for reliability but not independently valuable without the write path.

**Independent Test**: Can be tested by submitting known-bad bases and verifying the validator returns specific, actionable diagnostics for each error class.

**Acceptance Scenarios**:

1. **Given** a base with a formula reference in a view's `order` list that doesn't match any defined formula name, **When** validated, **Then** the validator returns an error identifying the undefined formula reference.
2. **Given** a base with unquoted special characters in a filter string value, **When** validated, **Then** the validator returns a warning identifying the problematic value.
3. **Given** a valid base, **When** validated, **Then** the validator returns success with no errors.

---

### Edge Cases

- What happens when a base's filter references a folder path that doesn't exist in the vault? The query returns an empty result set, not an error — the folder condition simply matches zero notes.
- How does the system handle a base with no `views:` key? The base is still valid — execution applies only the top-level filters and returns all formula columns.
- What happens when a formula references a frontmatter property that doesn't exist on some notes? The computed column returns a null/empty value for those notes, not an error.
- How does the system handle a base code block with invalid YAML? Parse returns a structured error with line number and diagnostic message rather than silently failing.
- What if a file has zero inline base code blocks but the agent asks to parse it? The parse tool returns an empty list of bases, not an error.
- How are `or:` filters with a sibling `not:` at the same level handled? Both conditions are applied — `or:` produces a union of its children, `not:` excludes its children, and the final result is the intersection of both at that nesting level (matching Obsidian's behavior).
- What happens when a formula uses an unsupported expression (Tier 2 or future Obsidian syntax)? The formula column returns null for all notes, and the query result includes a `warnings` list identifying which formulas could not be evaluated and why. The query still succeeds — unsupported formulas degrade gracefully, they do not fail the query.
- What happens when a base uses the `cards` view type? Parse returns the view structure normally (cards is valid YAML). Execute returns an "unsupported view type" warning and skips that view. Other `table` views in the same base execute normally.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST parse inline base code blocks (triple-backtick `base` fenced blocks) from markdown files into a structured representation containing filters, formulas, and views.
- **FR-002**: System MUST handle files containing multiple inline base code blocks, returning each with its position (byte offset or line number) in the source file.
- **FR-003**: System MUST parse the filter tree including `and:`, `or:`, and `not:` logical operators, comparison predicates (`==`, `!=`) against `file.*` properties and `note["property"]` accessors, and the `file.hasLink("name")` function predicate used in Atlas folder notes.
- **FR-004**: System MUST evaluate Tier 1 formula expressions: frontmatter property access via `note["key"]`, file metadata via `file.mtime`, `file.name`, `file.folder`, `file.path`, and `file.ext`.
- **FR-005**: System MUST evaluate Tier 1 formula expressions that use chained methods: `file.links`, `file.backlinks`, `.filter()`, `.length`, and `value.asFile().ext` — the link-counting patterns present in the vault's existing bases.
- **FR-006**: System MUST execute a base's filters against the current vault index and return matching notes with all computed formula columns.
- **FR-007**: System MUST support restricting execution to a named view, applying the view's additional filters, sort order, and column selection on top of the base-level filters. Only `table` views are executable in Phase 1.
- **FR-008**: System MUST write a new inline base code block into a markdown file, producing valid YAML that Obsidian renders without errors.
- **FR-009**: System MUST update an existing inline base code block in a markdown file while preserving all surrounding markdown byte-for-byte.
- **FR-010**: System MUST validate a base before writing, detecting: (a) undefined formula references in view `order` lists, (b) unquoted special characters in YAML string values, (c) syntactically invalid YAML.
- **FR-011**: System MUST return query results as structured data (list of matching notes, each with file path, frontmatter, and computed formula values) suitable for programmatic consumption by agents. Results MUST include a `warnings` list for any formulas that could not be evaluated, with the formula name and reason.
- **FR-012**: System MUST apply view-level `sort` directives (property + direction) to order the result set when a view is specified.
- **FR-013**: System MUST return null for formula columns whose expressions are outside the supported Tier 1 subset, and include a warning in the result metadata identifying the unsupported expression. The query MUST still succeed.

### Key Entities

- **Base**: The parsed representation of an Obsidian Bases configuration — contains a filter tree, a set of named formulas, and zero or more named views.
- **Filter**: A tree of logical operators (`and`, `or`, `not`) whose leaves are comparison predicates against file properties or frontmatter values, or function predicates like `file.hasLink("name")`.
- **Formula**: A named expression that computes a value from note metadata, file properties, or link graph data. Identified by name and referenced in views as `formula.<Name>`. Classified as Tier 1 (supported in Phase 1) or Tier 2 (deferred).
- **View**: A named presentation configuration within a base — specifies additional filters, column order, sort directives, and display options (column sizing, markers). Phase 1 supports `table` views only; `cards` views are parsed but not executed.
- **QueryResult**: The output of executing a base — a list of matched notes, each carrying its file path, resolved frontmatter, and computed formula values. Includes a `warnings` list for any unsupported or failed formula evaluations.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: An agent can replace a hand-written "which notes match X?" vault query with a base execution call and receive equivalent results — verified against the four existing folder notes in Outputs/Plans, Outputs/Personal History, Outputs/Self, and Outputs/Dialogues, plus representative Atlas folder notes that use `file.hasLink()`.
- **SC-002**: Query results match Obsidian's own rendering of the same base for every `table` view the vault actually uses today — 100% agreement on which notes appear and in what order.
- **SC-003**: A base written by vault-mcp and then opened in Obsidian renders without YAML or display errors — verified by round-trip testing (write, then open in Obsidian and visually confirm rendering).
- **SC-004**: When a base is written into a markdown file that already contains other content, the markdown outside the base code block is byte-identical before and after the write — verified by diffing the non-base portions.
- **SC-005**: The pre-write validator rejects 100% of the defined error classes (undefined formula references, unquoted special characters, invalid YAML) and returns a diagnostic message specific enough for the caller to fix the problem without guessing.
- **SC-006**: Base execution completes within the same order of magnitude as the existing vault index query tools — no more than 2x the wall-clock time of a comparable `find_notes_by_frontmatter` call on the same vault.

## Assumptions

- The existing vault index (`VaultIndex` class) provides sufficient note metadata (frontmatter, file path, file modification time) for filter evaluation. Formula expressions that need link graph data (e.g., `file.links`, `file.backlinks`) can use the index's existing outbound/inbound link maps. The `file.hasLink()` predicate can be evaluated against the same link graph.
- The comparison operators used in filters are `==` and `!=`, plus the `file.hasLink("name")` function predicate. If additional operators (contains, startsWith, `>`, `<`) appear in future bases, they can be added incrementally without redesigning the filter evaluator.
- The vault does not currently contain standalone `.base` files (all bases are inline code blocks). Support for `.base` files is included in the spec for completeness and forward compatibility, but inline code blocks are the primary transport to optimize for.
- Formula expressions are classified into two tiers. **Tier 1** (Phase 1): property access (`note["key"]`, `file.*`), chained link-counting methods (`.filter()`, `.length`, `.asFile().ext`), and string/numeric comparisons inside `.filter()`. **Tier 2** (Phase 2): `html()`, `if(cond, true, false)`, `.map()`, `.join()`, `.replace()` (including regex), `.toString()`, and string concatenation with `+`. Tier 2 covers one file in the vault (Core Concepts.md cards formula).
- Phase 1 supports `table` views only. The `cards` view type (one instance in the vault) is deferred alongside Tier 2 formulas — the single cards instance depends on Tier 2 expression features. The `list` view type, if encountered, can be treated as a table without column configuration.
- The `markers` and `columnSize` view properties are display-only hints that don't affect query results. They are preserved in round-trip writes but not interpreted during execution. Similarly, card-specific properties (`cardSize`, `image`, `imageAspectRatio`, `indentProperties`) are preserved in writes but not interpreted.

## Out of Scope

- **Tier 2 formula expressions**: `html()`, `if()`, `.map()`, `.join()`, `.replace()` (including regex), `.toString()`, and string concatenation — deferred to Phase 2. One vault file (Core Concepts.md) uses these; all other bases use Tier 1 only.
- **Cards view type**: Depends on Tier 2 formulas; deferred to Phase 2. Parser recognizes `cards` as valid YAML; execute returns an unsupported-view-type warning.
- **Map views**: Depend on a community plugin; deferred to Phase 2.
- **Summary/aggregation evaluation**: `count`, `average`, and other aggregation functions; deferred to Phase 2.
- **Base embed resolution**: `![[some.base#View]]` transclusion syntax; deferred to Phase 2.
- **groupBy with computed keys**: Computed grouping; deferred to Phase 2.
- **Real-time file-watcher integration**: Live-updating query results as files change; deferred to Phase 2.
