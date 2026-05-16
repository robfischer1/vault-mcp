# Quickstart: Bases Tier 2 Expressions and Cards View

## Summary
This feature extends the Obsidian-compatible Bases support in `vault-mcp` to include advanced formula evaluation (Tier 2) and the `cards` view execution.

## Key Changes
1. **Tier 2 Formulas**: Support for `if()`, `html()`, `.map()`, `.join()`, `.replace()`, `.toString()`, and `+`.
2. **Cards View**: Execution of `type: cards` views with extraction of card layout metadata.
3. **Safety Limits**: 100ms regex timeout and 10-level nesting limit for `if()`.

## Usage Example

### 1. Define a Base with Tier 2 Formulas and Cards View
In a markdown file (e.g., `Projects.md`):

````markdown
```base
formulas:
  status_label: 'if(status == "active", "🚀 Active", "💤 Paused")'
  tags_list: 'tags.map(t => "#" + t).join(", ")'
views:
  - name: "Active Projects"
    type: cards
    cardSize: small
    image: cover_image
    filters:
      status: active
    order:
      - file.name
      - formula.status_label
      - formula.tags_list
```
````

### 2. Execute via MCP
Use the `execute_base` tool:

```json
{
  "path": "Projects.md",
  "view_name": "Active Projects"
}
```

## Developer Notes
- Testing: Use `tests/test_bases.py` as a template for adding Tier 2 test cases.
- Safety: Evaluation is performed in a restricted environment to prevent arbitrary code execution.
