---
note_type: folder
---

# First Base

```base
filters:
  and:
    - file.folder == "Projects"
formulas:
  Status: note["status"]
views:
  - type: table
    name: Table
```

Some text between bases.

# Second Base

```base
filters:
  and:
    - file.folder == "Archive"
formulas:
  Archived: file.mtime
views:
  - type: table
    name: Old
```
