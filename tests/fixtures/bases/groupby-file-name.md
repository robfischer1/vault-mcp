```base
filters:
  and:
    - file.folder == "Projects"
    - file.name != "Projects"
views:
  - name: ByFileName
    type: table
    groupBy:
      property: file.name
      direction: ASC
```
