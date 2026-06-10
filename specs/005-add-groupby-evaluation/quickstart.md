# Quickstart: Bases Tier 5 — Grouping

## Defining a Grouped View

Add a `groupBy` section to your view configuration in a `.base` file or a markdown code block.

```yaml
views:
  - name: "By Status"
    type: table
    groupBy:
      property: status
      direction: ASC
    sort:
      - property: file.name
        direction: ASC
```

## Grouping by Formula

You can group by any computed formula. This is powerful for derived categories.

```yaml
formulas:
  Phase: |
    if(tags.contains("#idea"), "1 - Discovery",
    if(tags.contains("#active"), "2 - Execution", "3 - Other"))
views:
  - name: "Dashboard"
    type: cards
    groupBy:
      property: formula.Phase
      direction: ASC
```

## Accessing via MCP

When calling `execute_base`, the returned object contains a `groups` list.

```python
# Example result processing
result = mcp.call_tool("execute_base", {"path": "Projects.base"})
for group in result["groups"]:
    print(f"## {group['label']} ({group['count']})")
    for note in group["notes"]:
        print(f"- {note['file.name']}")
```
