---
note_type: folder
up: "[[Root]]"
---

# Projects

Folder note for Projects.

```base
filters:
  and:
    - file.folder == "Projects"
    - file.name != "Projects"
formulas:
  Status: note["status"]
  Phase: note["phase"]
views:
  - type: table
    name: Active
    filters:
      and:
        - note["status"] != "complete"
    order:
      - file.name
      - formula.Status
      - formula.Phase
    sort:
      - property: formula.Status
        direction: ASC
  - type: table
    name: All
    order:
      - file.name
      - formula.Status
      - formula.Phase
    sort:
      - property: file.name
        direction: ASC
```
