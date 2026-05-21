```base
filters:
  and:
    - file.folder == "Projects"
    - file.name != "Projects"
views:
  - name: TypeGroup
    type: table
    groupBy:
      property: note_type
    sort:
      - property: file.name
        direction: DESC
```
