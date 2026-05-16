# Data Model: Bases Embed Resolution

## Entities

### BaseEmbed
Represents a detected Obsidian Base embed in a Markdown note.

- `token`: The original string (e.g., `![[Active.base#CardView]]`).
- `target_stem`: The stem of the target file (e.g., `Active.base`).
- `view_name`: Optional name of the view to execute (e.g., `CardView`).

### ResolvedEmbed
The result of resolving and executing a `BaseEmbed`.

- `token`: The original string.
- `path`: The vault-relative path of the target file.
- `results`: The structured `QueryResult` from `execute_base`, or an error dictionary.

## Relationships
- A `Note` (via `read_note`) can contain multiple `BaseEmbed`s.
- Each `BaseEmbed` resolves to exactly one `ResolvedEmbed`.
- `ResolvedEmbed` contains data derived from the target `Base` (standalone or inline).
