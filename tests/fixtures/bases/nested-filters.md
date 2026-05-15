---
note_type: folder
---

# Nested Filters

```base
filters:
  and:
    - file.folder == "Projects"
    - or:
        - note["status"] == "active"
        - note["status"] == "draft"
    - not:
        - file.name == "Archive"
formulas:
  Status: note["status"]
views:
  - type: table
    name: Table
```
