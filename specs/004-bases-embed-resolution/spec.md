# Feature Specification: Bases Embed Resolution

**Feature Branch**: `004-featurename-bases-embed-resolution`

**Created**: 2026-05-16

**Status**: Draft

**Input**: User description: "Add resolution of base embeds to vault-mcp's Bases support. Obsidian supports embedding a base view inside another markdown file via the `![[some.base#View]]` syntax (or `![[some.base]]` for the full base). When such an embed is encountered during a query that traverses a file, the embedded base view should be resolved and its results inlined. This brief adds the resolution path so that an agent reading a folder note that embeds a base gets the resolved query results, not just the unresolved embed token. The user value is that agents traversing a folder note for synthesis or reporting can see the actual content of any embedded base views instead of stopping at the embed boundary. Closes the "what does this folder note actually show?" gap. ### Initial scope - Detection of `![[*.base#View]]` and `![[*.base]]` embed syntax in markdown. - Resolution: locate the target base (either a standalone `.base` file or an inline base code block inside the target file), execute the specified view (or the base's default if no view name), return resolved results as inline data accompanying the source file's parse. - Support for embeds targeting both inline code-block bases and standalone `.base` files. ### Out of scope - Recursive embed resolution (an embedded base whose results contain another embed). First-level only for v1. - Embed rendering or formatting -- structured data only. - Embeds with view-name typos -- return an error/warning, not silent fallback. ### Success criteria - A markdown file containing `![[Plans.base#Active]]` (hypothetical) returns resolved Active-view results when parsed. - An embed pointing at a missing file returns a structured error matching the existing `not_found` error shape. - An embed pointing at a valid file but missing view returns a `view_not_found` error matching the existing `execute_base` error shape."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Resolve Standalone Base Embed (Priority: P1)

An agent reads a folder note that embeds a specific view from a `.base` file. The agent expects to see the actual query results from that base view inlined in the response.

**Why this priority**: Core value of the feature. Allows agents to "see through" embeds to the actual data.

**Independent Test**: Create a `.base` file with a view, create a `.md` file embedding it, and verify the `vault-mcp` tool output contains the resolved base data.

**Acceptance Scenarios**:

1. **Given** a file `Projects.md` containing `![[ActiveProjects.base#CardView]]`, **When** the file is parsed by `vault-mcp`, **Then** the results for the `CardView` from `ActiveProjects.base` are inlined in the metadata.
2. **Given** a file `Projects.md` containing `![[ActiveProjects.base]]`, **When** the file is parsed, **Then** the results for the default view of `ActiveProjects.base` are inlined.

---

### User Story 2 - Resolve Inline Code-Block Base Embed (Priority: P2)

An agent reads a file that embeds a base defined as a code block in another `.md` file.

**Why this priority**: Users often define bases inline within relevant notes rather than in standalone files.

**Independent Test**: Create a note with a `bases` code block, another note embedding it, and verify resolution.

**Acceptance Scenarios**:

1. **Given** `Database.md` has a code block ` ```bases ... ``` ` defining a view `Inventory`, **When** parsing `Report.md` which has `![[Database#Inventory]]`, **Then** the Inventory results are inlined.

---

### User Story 3 - Handle Missing Targets Gracefully (Priority: P3)

The system should provide clear error information when an embed target is missing or invalid.

**Why this priority**: Debugging broken links is essential for vault health.

**Independent Test**: Embed a non-existent file or a non-existent view and check the error shape in the output.

**Acceptance Scenarios**:

1. **Given** an embed `![[Missing.base]]`, **When** parsed, **Then** the inlined result contains a `not_found` error.
2. **Given** an embed `![[Existing.base#MissingView]]`, **When** parsed, **Then** the inlined result contains a `view_not_found` error.

### Edge Cases

- **Recursive Embeds**: `Base A` embeds `Base B`. The resolution should show the raw embed token for `Base B` inside the results of `Base A`, as recursion is out of scope.
- **Multiple Embeds**: A single file embedding multiple bases should have all of them resolved independently.
- **Malformed Syntax**: Syntax like `![[some.base#]]` or `![[some.base|alias]]` (Obsidian aliases) should be handled or reported as invalid if unsupported.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST detect `![[path/to/target.base#ViewName]]` syntax in Markdown files.
- **FR-002**: System MUST detect `![[path/to/target.base]]` syntax and default to the first or primary view.
- **FR-003**: System MUST support targets that are either standalone `.base` files or `.md` files containing `bases` code blocks.
- **FR-004**: System MUST execute the target base/view using the existing `Bases` evaluator logic.
- **FR-005**: System MUST inline the structured results of the executed base into the file's parse/metadata response.
- **FR-006**: System MUST return a structured error (matching existing patterns) if the target file is not found.
- **FR-007**: System MUST return a structured error if the specified view is not found in the target.
- **FR-008**: System MUST NOT resolve embeds recursively (one level deep only).

### Key Entities *(include if feature involves data)*

- **BaseEmbed**: Represents a detected embed in a Markdown file, containing the target path and optional view name.
- **ResolvedEmbed**: The result of executing a `BaseEmbed`, containing either the list of query results or an error object.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of valid `![[*.base#View]]` embeds in a file are resolved to their structured results when the file is queried.
- **SC-002**: Resolution of a single embed adds less than 100ms overhead to the file parse operation (excluding vault index latency).
- **SC-003**: Error responses for missing files/views match the existing `execute_base` and `get_file` error schemas exactly.

## Assumptions

- **Syntax**: Only the `![[...]]` embed syntax is supported; standard links `[[...]]` do not trigger resolution.
- **Execution Context**: The embedded base is executed within the same vault context as the source file.
- **Format**: Resolved results are returned as structured data (JSON), not as rendered Markdown text.
- **Scope**: Internal Obsidian links to specific headers or blocks (e.g. `![[Note#Header]]`) are NOT handled by this feature, only `.base` files or `#View` targets in notes with bases.
