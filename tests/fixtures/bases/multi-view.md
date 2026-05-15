---
note_type: folder
---

# Multi View

```base
filters:
  and:
    - file.folder == "Projects"
    - file.name != "Projects"
formulas:
  Status: note["status"]
  Updated: file.mtime
views:
  - type: table
    name: Active
    filters:
      and:
        - note["status"] != "complete"
    order:
      - file.name
      - formula.Status
      - formula.Updated
    sort:
      - property: formula.Updated
        direction: DESC
  - type: table
    name: All
    order:
      - file.name
      - formula.Status
      - formula.Updated
    sort:
      - property: formula.Updated
        direction: DESC
```
