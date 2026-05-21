```base
filters:
  and:
    - file.folder == "Projects"
    - file.name != "Projects"
views:
  - name: StatusGroupDesc
    type: table
    groupBy:
      property: status
      direction: DESC
```
