# Summaries Base

```base
filters: null
formulas:
  Phase: note["phase"]
summaries:
  Total: count
views:
  - name: Active
    filters:
      and:
        - note["status"] == "active"
    summaries:
      Active Count: count
      Average Phase: average(formula.Phase)
```
