# Feature Specification: Bases Tier 2 Expressions and Cards View

**Feature Branch**: `002-bases-tier-2`

**Created**: 2026-05-16

**Status**: Draft

**Input**: User description: "Add Tier 2 formula expression evaluation and `cards` view execution to vault-mcp's Bases support. Tier 1 (shipped in 001) covers property access, file metadata, and link-counting chains. Tier 2 covers the expression patterns one file in the vault currently uses (Core Concepts.md) and that future bases are likely to reach for: conditionals via `if()`, HTML rendering via `html()`, list-shaping via `.map()` / `.join()` / `.replace()` (including regex), `.toString()` coercion, and string concatenation with `+`. The `cards` view type also lands in this brief because the one cards-using file in the vault depends on Tier 2 expressions, making the two a practical bundle."

## Clarifications

### Session 2026-05-16

- Q: How should "safe HTML data" be enforced for the html() helper? → A: Pass-through: Return the raw string; assume the client handles rendering safety.
- Q: How should the system handle long-running or resource-intensive regex evaluations? → A: Time-out: Set a strict execution limit (e.g., 100ms) for each regex evaluation.
- Q: Should we impose a hard limit on the nesting depth of if() expressions? → A: Limit: Enforce a maximum nesting depth of 10 levels.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Executing Complex Bases (Priority: P1)

As a user with a "Core Concepts" or similar knowledge-base vault, I want to execute Bases that use advanced formulas (Tier 2) so that I can see my cards and computed data within the MCP interface without receiving "Unsupported expression" warnings.

**Why this priority**: This is the primary goal of the feature. It closes the gap for existing vault files that use these common advanced formulas.

**Independent Test**: Can be tested by querying the `Core Concepts.md` base through the MCP. It should return results with all formula-based properties correctly evaluated instead of warnings.

**Acceptance Scenarios**:

1. **Given** a Base with a formula using `if()`, **When** the base is executed, **Then** the condition is correctly evaluated and the appropriate branch value is returned.
2. **Given** a Base with a list-shaping formula (e.g., `.map()`, `.join()`), **When** executed, **Then** the list is transformed and returned as a formatted string or collection.
3. **Given** a Base with string concatenation or coercion, **When** executed, **Then** the resulting string matches the expected output.

---

### User Story 2 - Cards View Execution (Priority: P2)

As a user, I want to execute Bases defined as `cards` views so that I can retrieve the cards' structured data, including card-specific layout properties like image aspect ratios and sizes.

**Why this priority**: The `cards` view is a popular way to organize information in Obsidian, and the one cards-using file in the project depends on Tier 2 expressions.

**Independent Test**: Query a `cards` type Base. The response should include card-specific properties (`cardSize`, `image`, etc.) alongside the standard record data.

**Acceptance Scenarios**:

1. **Given** a Base defined with `type: cards`, **When** executed, **Then** the response includes `cardSize`, `image`, `imageAspectRatio`, and `indentProperties` if they are defined in the base.
2. **Given** a Cards view, **When** executed, **Then** it correctly applies all filters and formulas defined in the view.

---

### User Story 3 - Graceful Handling of Unsupported Expressions (Priority: P3)

As a user, I want the system to identify and warn me about truly unsupported formula functions so that I understand why certain data might be missing without the entire query failing.

**Why this priority**: Ensures robustness and provides clear feedback when the system encounters something outside its current capabilities (e.g., custom user plugins).

**Independent Test**: Execute a base containing a non-existent or unsupported function (e.g., `myCustomFunc()`). The system should return a specific warning for that expression while still processing the rest of the query.

**Acceptance Scenarios**:

1. **Given** a formula with an unknown function, **When** executed, **Then** the specific field returns a warning value (e.g., `null` with a warning log) instead of crashing the query.

---

### Edge Cases

- **Regex in `.replace()`**: Handling invalid regex patterns provided in the formula and enforcing the 100ms timeout.
- **Deeply nested `if()`**: Ensuring the evaluator handles up to 10 levels of conditional nesting and gracefully errors beyond that.
- **Null/Undefined values in list operations**: Handling `.map()` or `.join()` on properties that are missing in some files.
- **Malformed HTML in `html()`**: Handled as pass-through; ensuring malformed strings don't crash the evaluator.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST evaluate the `if(condition, trueVal, falseVal)` expression.
- **FR-002**: System MUST evaluate the `html(content)` expression, returning the content as-is (pass-through) for the client to handle safely.
- **FR-003**: System MUST support list-shaping methods: `.map()`, `.join()`, and `.replace()`.
- **FR-004**: System MUST support regex patterns within the `.replace(regex, replacement)` method.
- **FR-005**: System MUST support `.toString()` coercion and string concatenation using the `+` operator.
- **FR-006**: System MUST execute Bases with `type: cards`.
- **FR-007**: System MUST extract and return Card-specific metadata: `cardSize`, `image`, `imageAspectRatio`, and `indentProperties`.
- **FR-008**: System MUST maintain the warning path for genuine Tier 3+ unsupported expressions.
- **FR-009**: System MUST NOT regress on existing Tier 1 evaluation (property access, file metadata, link-counting).
- **FR-010**: System MUST enforce a 100ms time-out for regular expression evaluation to prevent resource exhaustion.
- **FR-011**: System MUST support up to 10 levels of nested `if()` expressions.

### Key Entities *(include if feature involves data)*

- **Tier 2 Formula**: An expression involving logic (`if`), string/list manipulation (`map`, `join`, `replace`), or formatting (`html`, `+`).
- **Cards View**: A specific Base configuration that dictates how records should be presented as cards, including layout and image properties.
- **Card Metadata**: Properties specific to the visual layout of a card (e.g., `cardSize`, `imageAspectRatio`).

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: The `Core Concepts.md` cards view executes with 100% success rate and no Tier 2 related warnings.
- **SC-002**: All Tier 2 formula columns in test bases are populated with values that match Obsidian's own rendering (1:1 parity for supported expressions).
- **SC-003**: Query execution time for Tier 2 bases is within 20% of Tier 1 bases of similar size (no significant performance penalty).
- **SC-004**: 0% regression on existing Tier 1 test cases.

## Assumptions

- **Obsidians Parity**: We assume the implementation of `if()`, `.map()`, etc., should mimic the behavior of the Dataview/Bases plugin in Obsidian.
- **Input Quality**: We assume the Bases YAML in the vault is generally valid, but we handle parser errors gracefully.
- **Execution Environment**: We assume the environment has standard string and regular expression processing capabilities available for evaluation.
- **Data Scope**: We assume the cards view primarily targets local vault files and their metadata.
