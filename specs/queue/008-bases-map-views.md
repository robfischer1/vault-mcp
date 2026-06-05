Add parser-level recognition and round-trip preservation of map view configurations to vault-mcp's Bases support. Bases support `type: map` views that render matching notes as map pins using lat/lng frontmatter properties; execution requires the Maps community plugin. Phase 1 parses map views as valid YAML but `execute_base` returns "unsupported view type" warning. This brief adds parser-level structure preservation; actual map rendering remains Obsidian's job and depends on the community plugin.

The user value is low. No current vault base uses map views. Adding parser support is mostly about completeness -- agents would receive the structured map-view config (lat/lng property names, zoom level, etc.) without execution, which lets them introspect bases that include map views.

### Initial scope

- Recognize `type: map` views during parse without flagging as error.
- Preserve map-specific properties in the view structure (lat property, lng property, default zoom, marker config).
- `execute_base` returns a structured "unsupported view type" response specific to map (informational, not error).

### Out of scope

- Map view evaluation or rendering (community plugin dependency).
- Lat/lng coordinate validation.

### Success criteria

- A base with a map view parses without errors and round-trip writes preserve all map-specific properties.
- `execute_base` returns a recognizable "unsupported, see Maps plugin" response for map views.

### Context

Phase 2 of vault-mcp Bases support. Lowest priority because no current vault base uses map views and execution requires a third-party plugin. Should be reconsidered or dropped if no real demand emerges.
