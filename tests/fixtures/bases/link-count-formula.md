---
note_type: folder
---

# Link Count

```base
filters:
  and:
    - file.folder == "Projects"
formulas:
  LinksOut: file.links.filter(value.asFile().ext == "md").length
  LinksIn: file.backlinks.filter(value.asFile().ext == "md").length
views:
  - type: table
    name: Table
    order:
      - file.name
      - formula.LinksOut
      - formula.LinksIn
```
