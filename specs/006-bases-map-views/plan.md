# 006: Map View Support Plan

## Objective
Implement parser-level recognition and round-trip preservation of map view configurations (`type: map`) in `vault-mcp`'s Bases support. Ensure that execution gracefully returns an informational warning regarding the dependency on the Maps community plugin.

## Implementation Steps

### 1. Update Data Model and Parser
- Map properties will be parsed into the `extra` dictionary by default.
- Update `_serialize_base` in `src/vault_mcp/bases.py` to promote map-specific properties from `extra` to the top-level view configuration during YAML serialization.
- Map-specific keys: `latProperty`, `lngProperty`, `defaultZoom`, `markerConfig`, `lat property`, `lng property`, `default zoom`, `marker config`.

### 2. Update Evaluator (`execute_base`)
- Modify the view type check in `execute_base`.
- Add a specific branch for `selected_view.type == "map"`.
- Return a `QueryResult` with an informational warning: `"Execution of 'map' views is not supported by vault-mcp. It requires the Obsidian Maps community plugin."`

### 3. Add Tests
- Create `tests/fixtures/bases/map-view.md`.
- Add tests to `tests/test_bases.py` for parsing, execution, and round-trip.
