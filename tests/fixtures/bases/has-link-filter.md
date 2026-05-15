---
note_type: folder
---

# Topics

```base
filters:
  and:
    - file.hasLink("Topics")
formulas:
  Name: file.name
views:
  - type: table
    name: Table
    order:
      - file.name
      - formula.Name
```
