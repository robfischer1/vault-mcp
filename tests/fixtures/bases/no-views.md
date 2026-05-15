---
note_type: folder
---

# No Views

```base
filters:
  and:
    - file.folder == "Notes"
    - file.name != "Notes"
formulas:
  Status: note["status"]
```
