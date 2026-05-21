# 006 Tasks: Map View Support

## Tests (Test-First)
- [x] Create `tests/fixtures/bases/map-view.md` with a valid map view configuration.
- [x] Add `test_parse_map_view` to `tests/test_bases.py` to verify map properties are parsed.
- [x] Add `test_execute_map_view` to `tests/test_bases.py` to verify the informational warning is returned.
- [x] Add `test_round_trip_map_view` to `tests/test_bases.py` to verify map properties are preserved upon serialization.

## Implementation
- [x] Update `execute_base` in `src/vault_mcp/bases.py` to handle `type: map` views and return the specific warning.
- [x] Update `_serialize_base` in `src/vault_mcp/bases.py` to promote map-specific properties from `extra` to the top-level view configuration during YAML serialization.
