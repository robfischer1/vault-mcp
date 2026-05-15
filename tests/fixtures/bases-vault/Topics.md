---
note_type: folder
---

# Topics

A folder note that finds all notes linking to it.

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
