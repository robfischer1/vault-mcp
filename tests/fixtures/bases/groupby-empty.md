```base
filters:
  and:
    - file.folder == "Nonexistent"
views:
  - name: EmptyGroup
    type: table
    groupBy:
      property: status
      direction: ASC
```
