# Cards View Fixture

This note contains a Base with a cards view for testing purposes.

## Base 1: Cards

```base
formulas:
  summary: 'description.replace(/\n.*/, "")'
views:
  - name: "Project Cards"
    type: cards
    cardSize: medium
    image: cover
    imageAspectRatio: "16/9"
    indentProperties: true
    filters:
      type: project
    order:
      - file.name
      - formula.summary
      - status
```
