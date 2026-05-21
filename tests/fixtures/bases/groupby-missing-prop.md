```base
filters:
  and:
    - file.folder == "Projects"
    - file.name != "Projects"
views:
  - name: PhaseGroup
    type: table
    groupBy:
      property: phase
      direction: ASC
```
