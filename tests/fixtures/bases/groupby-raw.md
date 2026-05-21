```base
filters:
  and:
    - file.folder == "Projects"
    - file.name != "Projects"
views:
  - name: StatusGroup
    type: table
    groupBy:
      property: status
      direction: ASC
```
