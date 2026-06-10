# Research: Bases Tier 2 Expressions and Cards View

## Decisions

### 1. Formula Evaluation Strategy
- **Decision**: Implement a restricted expression evaluator that supports the specific vocabulary of Tier 2 (logic, list-shaping, string manipulation).
- **Rationale**: The current evaluator uses simple regex matching for property access. Tier 2 requires actual logic (`if`) and method chaining (`.map().join()`). We will use a safe evaluation approach rather than a full Python `eval()` to prevent unauthorized code execution.
- **Alternatives Considered**:
    - Full `eval()` with limited globals: Rejected due to security risks and the difficulty of truly sandboxing Python.
    - Custom recursive descent parser: Better for security but higher implementation complexity. A hybrid approach using a subset of Python's AST (via `ast.literal_eval` or similar restricted parser) is preferred.

### 2. Cards View Execution
- **Decision**: Remove the `type != "table"` check in `execute_base` and extend the `QueryResult` to include card-specific metadata.
- **Rationale**: The `cards` view is functionally similar to a table (it filters and computes formulas) but requires additional metadata for the card layout (images, sizes).
- **Implementation**: The evaluator will extract these properties from the `ViewConfig.extra` dictionary which already stores unknown keys from the YAML.

### 3. Regex and Nesting Safety
- **Decision**: Implement a `timeout` decorator or a specialized regex engine wrapper for `.replace()`, and a depth counter for nested `if()` calls.
- **Rationale**: Directly addresses the "ReDoS" and stack overflow concerns identified in the clarification phase.

## Technical Findings

- `src/vault_mcp/bases.py` already has a `_TIER2_PATTERNS` constant and a `_classify_formula_tier` function.
- `evaluate_formula` currently returns an error immediately if `formula.tier == 2`.
- `execute_base` explicitly blocks non-table views with a warning.
- `ViewConfig` already has an `extra` field that captures card-specific properties like `cardSize`.

## Dependencies
- Standard Python `ast`, `re`, and `concurrent.futures` (for timeouts) libraries. No new external dependencies required.
