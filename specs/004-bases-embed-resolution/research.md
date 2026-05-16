# Research: Bases Embed Resolution

## Decision: Embed Detection & Resolution Strategy

### Rationale
We will extend `VaultIndex.read_note` in `src/vault_mcp/index.py` to detect and resolve `![[...]]` embeds. This ensures that when a tool (like `read_note`) is called, the agent receives the structured results of any embedded bases.

### Approach
1.  **Regex Detection**: Use a regex similar to `IMAGE_EMBED_RE` but specifically looking for `.base` extensions or links with `#View` targets that resolve to files containing `bases` code blocks.
2.  **Target Location**: Use `VaultIndex.by_name` to find the target file (standalone `.base` or `.md` note).
3.  **Execution**: Use `bases.parse_file` and `bases.execute_base` to get the results.
4.  **Inlining**: The `read_note` response will include a new `resolved_embeds` list, where each entry contains the original token, the target path, and the `QueryResult` (or an error).

## Decision: Supporting Standalone vs. Inline Bases

### Rationale
Obsidian Bases supports both. We must check the file extension. If it's `.base`, we parse the whole file as YAML. If it's `.md`, we extract `bases` code blocks.

### Approach
`bases.parse_file` already handles this distinction by looking for code blocks. We may need to ensure it handles standalone `.base` files correctly (they might not have code blocks, just raw YAML).

*Self-correction*: Looking at `bases.parse_file`, it uses `extract_base_blocks`. A standalone `.base` file might NOT have code blocks. I need to verify if standalone `.base` files are just raw YAML or if they also use code blocks.
*Update*: The `speckit` instructions for 001/002 imply standalone `.base` files are supported. I'll check `tests/test_bases.py` to see how they are handled.

## Decision: Error Handling

### Rationale
Matches existing error patterns in `vault-mcp`.

### Approach
- If file missing: `not_found`.
- If view missing: `view_not_found`.
- If parse fails: `parse_error`.

## Alternatives Considered
- **Resolving during indexing**: Rejected. This would bloat the index and might lead to stale data if the target base changes. Better to resolve on-demand during `read_note`.
- **Recursive resolution**: Rejected per scope. First-level only.
