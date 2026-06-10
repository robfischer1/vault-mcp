```base
filters:
  and:
    - file.folder == "Projects"
    - file.name != "Projects"
formulas:
  StatusIcon: |
    if(status == "active", "🟢",
    if(status == "complete", "✅", "⚪"))
  Failing: "if(1, if(2, if(3, if(4, if(5, if(6, if(7, if(8, if(9, if(10, if(11, 1, 2), 2), 2), 2), 2), 2), 2), 2), 2), 2), 2)"
views:
  - name: IconGroup
    type: table
    groupBy:
      property: formula.StatusIcon
      direction: DESC
  - name: ErrorGroup
    type: table
    groupBy:
      property: formula.Failing
```
