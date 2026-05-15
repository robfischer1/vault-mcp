---
note_type: folder
---

# Projects

Some intro text.

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
```

Some trailing text.
