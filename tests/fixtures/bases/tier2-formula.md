---
note_type: folder
---

# Tier 2

```base
filters:
  and:
    - file.folder == "Projects"
formulas:
  Label: note["status"].replace("active", "🟢").replace("draft", "🟡")
  Display: if(note["phase"], note["phase"].toString() + " - " + note["status"], note["status"])
views:
  - type: table
    name: Table
```
