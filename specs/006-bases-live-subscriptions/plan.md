# Plan: Bases Live Subscriptions

## Step 1: Dependency Extraction in `bases.py`
- Implement `extract_dependencies(base: Base, view_name: str | None = None) -> dict[str, Any]`.
- This function will return:
    - `keys`: `set[str]` of frontmatter keys (prefixed with `note.` if internal).
    - `links`: `bool` if links/backlinks are used.
    - `structure`: `bool` if file path/name/folder are used.
- Add unit tests in `tests/test_bases.py` for this extraction.

## Step 2: Implement `SubscriptionManager` in `src/vault_mcp/bases.py`
- Create the `Subscription` dataclass.
- Create the `SubscriptionManager` class with `register`, `unregister`, and `notify` logic.
- The manager should be a singleton or managed by the `VaultIndex`.
- Preference: Keep it in `bases.py` as it is tightly coupled with Base parsing/execution.

## Step 3: Integrate with `VaultIndex` in `src/vault_mcp/index.py`
- Add an optional `on_invalidate` callback or listener list to `VaultIndex`.
- In `invalidate_file`, call the listener after the file has been re-indexed.
- The callback should receive the `Path` and the new frontmatter.

## Step 4: Implement Subscription Tools in `server.py`
- `@mcp.tool()` `subscribe_to_base(path: str, view: str | None = None, base_index: int = 0) -> dict`.
    - Returns a `subscription_handle`.
- `@mcp.tool()` `unsubscribe_from_base(handle: str) -> dict`.
- Initialize `SubscriptionManager` in `_get_index()` and link it to the `VaultIndex`.

## Step 5: Implement Notification Logic
- Use `mcp.publish_resource_change` or a custom notification if FastMCP supports it.
- The notification should include the `subscription_handle` and potentially the updated results.
- **Optimization**: Initially, just notify that "something changed" and let the client re-call `execute_base`. In a later pass, we can include the delta or full result.

## Step 6: Validation & Tests
- Create `tests/test_subscriptions.py`.
- Mock filesystem events to verify `SubscriptionManager` notifies correctly.
- Test edge cases:
    - Base definition changes.
    - Note matching a filter is added.
    - Note matching a filter is removed.
    - Note property changes but still matches (or stops matching).
    - Session disconnect / cleanup.
