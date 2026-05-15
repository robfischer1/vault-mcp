# Data Model: Obsidian Bases Support

**Feature**: [spec.md](spec.md) | **Date**: 2026-05-15

## Entities

All entities are defined as Python dataclasses in `src/vault_mcp/bases.py`. They are internal representations — MCP tool boundaries serialize to/from plain dicts.

### FilterNode

Recursive tree representing a base's filter logic.

| Field | Type | Description |
|-------|------|-------------|
| `op` | `str` | One of: `"and"`, `"or"`, `"not"`, `"eq"`, `"neq"`, `"hasLink"` |
| `field` | `str \| None` | Property path for comparison ops (e.g., `"file.folder"`, `"note.status"`). None for logical ops. Input forms `note["<key>"]` and bare `<key>` are normalized to `note.<key>` in serialized MCP responses. |
| `value` | `str \| None` | Comparison target for `eq`/`neq` (e.g., `"Outputs/Plans"`). Link target for `hasLink` (e.g., `"Writing"`). None for logical ops. |
| `children` | `list[FilterNode] \| None` | Child nodes for `and`/`or`/`not`. None for leaf ops. |

**Relationships**: A `Base` has zero or one top-level `FilterNode`. Each `ViewConfig` has zero or one additional `FilterNode`. During execution, a view's filter is logically ANDed with the base-level filter.

**Identity**: FilterNodes are value objects — no identity beyond structural equality.

**Validation rules**:
- Logical ops (`and`/`or`/`not`) must have non-empty `children`.
- Leaf ops (`eq`/`neq`/`hasLink`) must have non-None `field` and `value`.
- `field` must match one of: `file.folder`, `file.name`, `file.ext`, `file.path`, `note["<key>"]`, `<bare_key>` (shorthand for note property).

### Formula

Named computed expression.

| Field | Type | Description |
|-------|------|-------------|
| `name` | `str` | Display name (e.g., `"Status"`, `"Links Out"`). Referenced in views as `formula.<Name>`. |
| `expression` | `str` | Raw expression string from YAML (e.g., `'note["status"]'`, `'file.links.filter(value.asFile().ext == "md").length'`). |
| `tier` | `int` | `1` if evaluable in Phase 1, `2` if deferred. Classified at parse time by pattern matching. |

**Identity**: Unique by `name` within a `Base`.

**Tier classification rules**:
- Tier 1: `note["key"]`, `file.*` property access, `file.links.filter(...).length`, `file.backlinks.filter(...).length`, bare frontmatter property names
- Tier 2: anything containing `html(`, `if(`, `.map(`, `.join(`, `.replace(`, `.toString(`, or `+` string concatenation

### SortDirective

Single sort criterion.

| Field | Type | Description |
|-------|------|-------------|
| `property` | `str` | Property to sort on (e.g., `"file.name"`, `"formula.Status"`, `"formula.Updated"`). |
| `direction` | `str` | `"ASC"` or `"DESC"`. |

### ViewConfig

Named view within a base.

| Field | Type | Description |
|-------|------|-------------|
| `name` | `str` | View tab name (e.g., `"Active"`, `"All"`, `"Table"`). |
| `type` | `str` | View type. Phase 1 executes `"table"` only; `"cards"` parsed but not executed. |
| `filters` | `FilterNode \| None` | View-specific filter (ANDed with base-level filter during execution). |
| `order` | `list[str]` | Column order for display (e.g., `["file.name", "formula.Status"]`). |
| `sort` | `list[SortDirective]` | Sort criteria, applied in order. |
| `markers` | `str \| None` | Display hint (e.g., `"none"`). Preserved, not interpreted. |
| `column_sizes` | `dict[str, int]` | Column width hints (e.g., `{"file.name": 370}`). Preserved, not interpreted. |
| `extra` | `dict[str, Any]` | Card-specific or future properties preserved from YAML. Not interpreted. |

**Identity**: Unique by `name` within a `Base`.

### Base

Parsed representation of one Obsidian Bases configuration (one code block).

| Field | Type | Description |
|-------|------|-------------|
| `filters` | `FilterNode \| None` | Top-level filter tree. None if base has no `filters:` key. |
| `formulas` | `dict[str, Formula]` | Named formulas, keyed by name. |
| `views` | `list[ViewConfig]` | Ordered list of views. May be empty. |
| `raw_yaml` | `str` | Original YAML text (for round-trip writes). |
| `line_number` | `int` | 1-based line number of the opening `` ```base `` fence in the source file. |

**Relationships**: A `Base` contains 0-1 `FilterNode`, 0-N `Formula`, 0-N `ViewConfig`.

### ParsedFile

Result of parsing a markdown file for base code blocks.

| Field | Type | Description |
|-------|------|-------------|
| `path` | `str` | Vault-relative file path. |
| `bases` | `list[Base]` | All base code blocks found, in document order. |
| `errors` | `list[dict]` | Parse errors (invalid YAML, etc.) with `line_number` and `message`. |

### QueryResult

Output of executing a base against the vault index.

| Field | Type | Description |
|-------|------|-------------|
| `notes` | `list[dict]` | Matched notes. Each: `{"path": str, "frontmatter": dict, "formulas": dict[str, Any]}`. |
| `warnings` | `list[dict]` | Evaluation warnings. Each: `{"formula": str, "reason": str}`. |
| `view_name` | `str \| None` | Name of the view used, or None for base-level execution. |
| `total` | `int` | Count of matched notes. |

### ValidationResult

Output of validating a base before writing.

| Field | Type | Description |
|-------|------|-------------|
| `valid` | `bool` | True if no errors (warnings are non-blocking). |
| `errors` | `list[dict]` | Blocking issues. Each: `{"type": str, "message": str, "location": str \| None}`. |
| `warnings` | `list[dict]` | Non-blocking issues. Each: `{"type": str, "message": str, "location": str \| None}`. |

**Error types**: `"invalid_yaml"`, `"undefined_formula_ref"`, `"unquoted_special_char"`.

## State Transitions

Bases have no lifecycle state. They are static YAML configurations — parsed, evaluated, written, validated. No create/update/delete state machine.

## Data Flow

```
Markdown file  →  extract_bases()  →  list[Base]     (parsing)
Base + VaultIndex  →  execute()    →  QueryResult     (execution)
Base dict/YAML  →  validate()      →  ValidationResult (validation)
Base dict/YAML  →  write_base()    →  modified file   (writing)
```
