# Tier 2 Formula Fixture

This note contains Bases with Tier 2 formulas for testing purposes.

## Base 1: Conditionals

```base
formulas:
  label: 'if(status == "active", "ACTIVE", "INACTIVE")'
  nested: 'if(status == "active", if(priority == "high", "URGENT", "NORMAL"), "OFF")'
views:
  - name: "Status View"
    type: table
    order:
      - file.name
      - formula.label
      - formula.nested
```

## Base 2: List Shaping

```base
formulas:
  tags_str: 'tags.map(t => "#" + t).join(", ")'
  first_tag: 'tags.map(t => t).join(", ").replace(/,.*/, "")'
views:
  - name: "Tags View"
    type: table
    order:
      - file.name
      - formula.tags_str
      - formula.first_tag
```

## Base 3: Concatenation and Coercion

```base
formulas:
  full_path: 'file.folder + "/" + file.name + "." + file.ext'
  count_str: 'count.toString() + " items"'
views:
  - name: "Path View"
    type: table
    order:
      - formula.full_path
      - formula.count_str
```
