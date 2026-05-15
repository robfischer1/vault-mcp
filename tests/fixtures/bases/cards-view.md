---
note_type: folder
---

# Cards

```base
filters:
  and:
    - file.folder == "Concepts"
formulas:
  Summary: note["summary"]
views:
  - type: cards
    name: Cards
    cardSize: medium
    order:
      - file.name
      - formula.Summary
  - type: table
    name: Table
    order:
      - file.name
      - formula.Summary
```
