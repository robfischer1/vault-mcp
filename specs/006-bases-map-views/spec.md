# 006: Bases Map View Support

## 1. Overview
Add parser-level recognition and round-trip preservation of map view configurations to vault-mcp's Bases support. This is a "type: map" view that renders matching notes as map pins using lat/lng frontmatter properties.

Execution requires the Maps community plugin and remains unsupported by vault-mcp. The goal of this spec is to allow agents to introspect map view configurations by preserving the structured map-view config (lat/lng property names, zoom level, etc.) without attempting execution.

## 2. Scope & Boundaries

### In Scope
- Recognize `type: map` views during parse without flagging as error.
- Preserve map-specific properties in the view structure:
  - lat property
  - lng property
  - default zoom
  - marker config
- `execute_base` must return a structured "unsupported view type" response specific to map (informational, not an error), pointing to the Maps plugin requirement.
- Round-trip writing of the base must preserve all map-specific properties.

### Out of Scope
- Map view evaluation or rendering.
- Lat/lng coordinate validation.

## 3. Success Criteria
- A base with a map view parses without errors.
- Round-trip writes preserve all map-specific properties in the output YAML.
- `execute_base` returns a recognizable informational response stating that execution requires the Maps plugin.

## 4. Context
- Phase 2 of vault-mcp Bases support.
- Low priority. Adding parser support is mostly about completeness.
- No current vault base uses map views. Should be reconsidered or dropped if no real demand emerges.
