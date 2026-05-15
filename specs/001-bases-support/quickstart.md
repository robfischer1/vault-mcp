# Quickstart: Obsidian Bases Support

**Feature**: [spec.md](spec.md) | **Date**: 2026-05-15

## What This Feature Does

vault-mcp gains 4 new MCP tools that let agents work with Obsidian Bases — the YAML-based folder view configurations embedded in markdown notes. Instead of reimplementing filter logic to answer "which Plans are active?", an agent can execute the base that already declares the answer.

## Tools at a Glance

| Tool | Purpose | When to Use |
|------|---------|-------------|
| `parse_base` | Read a base's structure | "What filters and views does this folder note define?" |
| `execute_base` | Run a base query | "Give me the notes that match this base, with computed columns" |
| `write_base` | Create or update a base | "Add a new view" or "Update the filter on this folder note" |
| `validate_base` | Check a base for errors | "Will this base render correctly in Obsidian?" |

## Example: Execute a Folder Note's Base

Given `Outputs/Plans/Plans.md` contains:

````markdown
```base
filters:
  and:
    - file.folder == "Outputs/Plans"
    - file.name != "Plans"
formulas:
  Status: note["status"]
  Phase: note["phase"]
views:
  - type: table
    name: Active
    filters:
      and:
        - note["status"] != "complete"
    sort:
      - property: formula.Status
        direction: ASC
```
````

An agent calls:

```
execute_base(path="Outputs/Plans/Plans.md", view="Active")
```

And receives:

```json
{
  "total": 2,
  "view": "Active",
  "notes": [
    {
      "path": "Outputs/Plans/phdb Records Layer.md",
      "frontmatter": {"note_type": "plan", "status": "active", "phase": 1},
      "formulas": {"Status": "active", "Phase": "1"}
    },
    {
      "path": "Outputs/Plans/Spec-Kit Pilot.md",
      "frontmatter": {"note_type": "plan", "status": "draft"},
      "formulas": {"Status": "draft", "Phase": null}
    }
  ],
  "warnings": []
}
```

## Example: Parse Before Writing

```
parse_base(path="Outputs/Self/Self.md")
```

Returns the structural view — filter tree, formula definitions, view configurations — that the agent can inspect before deciding whether to add a new view or modify an existing one.

## Example: Write a New Base

```
write_base(
    path="Outputs/Dialogues/Dialogues.md",
    base={
        "filters": {"and": ['file.folder == "Outputs/Dialogues"', 'file.name != "Dialogues"']},
        "formulas": {"Links Out": 'file.links.filter(value.asFile().ext == "md").length'},
        "views": [{"type": "table", "name": "Table", "order": ["file.name", "formula.Links Out"]}]
    }
)
```

The tool validates the base, generates YAML, and inserts it into the file as an inline code block. Existing markdown content is preserved byte-for-byte.

## Limitations (Phase 1)

- **Tier 2 formulas** (`html()`, `if()`, `.map()`, `.join()`, `.replace()`) return null with a warning.
- **Cards views** are parsed but not executed.
- **Aggregations** (`count`, `average`, `sum`) are not supported.
- **Base embeds** (`![[some.base#View]]`) are not resolved.

## Development

```bash
uv pip install -e ".[dev]"    # install with new PyYAML dependency
uv run pytest tests/test_bases.py -xvs   # run bases tests
```
