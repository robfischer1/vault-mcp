# Research: Obsidian Bases Support

**Feature**: [spec.md](spec.md) | **Date**: 2026-05-15

## R1: YAML Parsing Library

**Decision**: Use PyYAML (`pyyaml`) with `yaml.safe_load` for parsing and `yaml.dump` for generation.

**Rationale**: The existing hand-rolled frontmatter parser (`parsers.parse_frontmatter`) handles flat key-value pairs with inline/block lists — sufficient for frontmatter but cannot handle the nested dict-of-list-of-dict structures in base YAML (filters with `and:`/`or:`/`not:` nesting, views with sort/order arrays). PyYAML is the standard Python YAML library, has type stubs (`types-PyYAML`) for mypy --strict, and `safe_load` avoids arbitrary code execution.

**Alternatives considered**:
- **`ruamel.yaml`**: Preserves comments and formatting on round-trip. Not needed — we don't round-trip YAML content itself (we round-trip the surrounding markdown and replace the YAML block wholesale). Heavier dependency, less common.
- **Hand-rolled parser**: Would duplicate PyYAML's tested edge-case handling for nested structures. The frontmatter parser works because frontmatter is flat; bases are not.
- **Standard library only**: Python has no YAML parser in stdlib.

## R2: Base Code Block Extraction

**Decision**: Regex-based extraction using `re.compile(r'^```base\s*\n(.*?)^```', re.DOTALL | re.MULTILINE)`.

**Rationale**: Markdown fenced code blocks have well-defined delimiters. Regex extraction is how the existing codebase handles markdown structure (see `WIKILINK_RE`, `IMAGE_EMBED_RE`, `BODY_TAG_RE` in parsers.py and index.py). Single pattern, no external parser needed.

**Alternatives considered**:
- **Markdown AST parser** (e.g., `markdown-it-py`): Overkill — we only need fenced code block positions, not a full document tree. Adds a dependency for one regex worth of work.
- **Line-by-line scanner**: More code, same result, harder to maintain.

## R3: Filter Expression Parser

**Decision**: Parse filter YAML into a recursive `FilterNode` tree. Leaf nodes are comparison predicates; internal nodes are logical operators (`and`, `or`, `not`). The `file.hasLink("name")` predicate is a special leaf type.

**Rationale**: The filter YAML is already structured — `yaml.safe_load` gives us nested dicts and lists. The "parser" is really a tree-builder that walks the YAML structure. No need for a string expression parser at the filter level because filter predicates arrive as YAML string values (e.g., `file.folder == "Outputs/Plans"`) that need simple regex splitting on `==` / `!=` / `.hasLink(`.

**Filter predicate patterns found in vault** (exhaustive from 250+ file survey):
- `file.folder == "path"` / `file.folder != "path"`
- `file.name == "stem"` / `file.name != "stem"`
- `note["key"] == "value"` / `note["key"] != "value"`
- `note_type != "value"` (bare property — equivalent to `note["note_type"]`)
- `file.hasLink("name")` (function predicate on outbound links)

No other operators or patterns found. The predicate parser handles these 3 forms (property comparison, note property comparison, hasLink function).

**Alternatives considered**:
- **Full expression grammar** (PEG, Lark): Massive overkill for 3 predicate forms. Would be needed if Tier 2 expressions were in scope.
- **eval()-based**: Security risk; forbidden.

## R4: Formula Expression Evaluator

**Decision**: Pattern-matching evaluator for Tier 1 expressions. Each formula string is classified by matching against known patterns, then evaluated by a dedicated handler function. Unknown patterns return null + warning.

**Rationale**: Tier 1 covers exactly these patterns (from vault survey):
1. `note["key"]` → frontmatter property lookup
2. `file.mtime` / `file.name` / `file.folder` / `file.path` / `file.ext` → file metadata
3. `file.links.filter(value.asFile().ext == "md").length` → count outbound .md links
4. `file.backlinks.filter(value.asFile().ext == "md").length` → count inbound .md links
5. `categories` / `tags` → bare frontmatter property (shorthand for `note["categories"]`)

That's 5 patterns, each with a dedicated handler. Pattern matching via regex is sufficient and avoids building a general-purpose interpreter.

**Alternatives considered**:
- **AST-based expression evaluator**: Required for Tier 2 (map/join/replace/if/html). Not needed for the 5 Tier 1 patterns. Can be added in Phase 2 as an upgrade path without changing the pattern-match layer (just add more patterns, or replace with AST evaluator).
- **Delegating to Dataview DQL**: Would require Obsidian running (violates Constitution §II).

## R5: YAML Output Formatting

**Decision**: Use `yaml.dump` with `default_flow_style=False`, `sort_keys=False`, and `allow_unicode=True` for generating base YAML on writes.

**Rationale**: Obsidian's Bases renderer accepts standard YAML. The existing bases in the vault use block style (not flow style). `sort_keys=False` preserves the logical ordering (filters → formulas → views). No custom formatting needed beyond PyYAML's defaults.

**Alternatives considered**:
- **String templating**: Error-prone for nested structures. YAML quoting rules are subtle.
- **`ruamel.yaml` round-trip**: Only needed if we were patching YAML in-place. We replace the entire code block content.

## R6: New Dependency Impact

**Decision**: Add `pyyaml>=6.0` and `types-PyYAML>=6.0` (dev) to `pyproject.toml`.

**Rationale**: PyYAML is the only new runtime dependency. It has no transitive dependencies itself. `types-PyYAML` provides mypy stubs for `--strict` mode. Both are well-maintained, widely used, and MIT-compatible.

**Impact on existing code**: None. PyYAML is imported only in `bases.py`. No existing modules are modified (except `server.py` which adds tool registrations).
