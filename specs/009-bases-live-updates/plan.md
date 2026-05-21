# Implementation Plan: Bases Live Updates

**Branch**: `009-bases-live-updates` | **Date**: 2026-05-21 | **Spec**: [spec.md](spec.md)

## Summary

Enable live updates for Bases query results via a subscription model. Clients can subscribe to a specific base view and receive push notifications whenever relevant files change. This leverages the existing `watcher.py` and `VaultIndex` invalidation mechanism.

## Technical Strategy

1.  **Hook into VaultIndex**: Add an `on_invalidate` callback to `VaultIndex`. This callback will be triggered whenever a file is invalidated in the index.
2.  **Subscription Management**: Implement a `SubscriptionManager` in `src/vault_mcp/index.py` (or as a separate module if it gets too large, but keeping it in `index.py` or `server.py` is simpler for now). Given the "Sibling, Not Subordinate" principle, I'll put the core logic in a new `subscriptions.py` or keep it in `server.py` since it involves MCP-specific details like sessions.
    *   *Decision*: Put it in `server.py` for now as it's the bridge between MCP and the domain. If it grows, move to `src/vault_mcp/subscriptions.py`.
3.  **FastMCP Session Access**: Use the underlying `mcp._server` to find active sessions and send raw notifications.
4.  **Relevance Detection**: On file invalidation, re-run all active subscriptions. Use hashing of the `QueryResult` to suppress redundant notifications.

## Project Structure Changes

*   `server.py`: Add `subscribe_base` and `unsubscribe_base` tools. Add `SubscriptionManager` class and watcher callback.
*   `src/vault_mcp/index.py`: Add `on_invalidate` callback support to `VaultIndex.invalidate_file`.

## Constitution Check

| Principle | Status | Notes |
|-----------|--------|-------|
| I. Sibling, Not Subordinate | ✅ PASS | Core logic remains vault-agnostic. |
| II. REST API Augments | ✅ PASS | No dependency on Obsidian REST API. |
| III. Test-First, Single-File Fixtures | ✅ PASS | Will add tests for subscribe/unsubscribe and notification triggers. |
| IV. Publish-Ready Hygiene | ✅ PASS | No PII or personal data. |
| V. Strict Quality Tooling | ✅ PASS | Mypy, Ruff, Pytest. |

## Risks & Mitigations

*   **FastMCP Async/Sync Mix**: `FastMCP` handles both, but the watcher runs in a background thread (watchdog). We need to ensure thread safety when accessing the subscription map and sending notifications.
*   **Notification Delivery**: Some MCP clients might not handle custom notifications well. However, this is a requirement for the feature.
*   **Performance**: Re-evaluating many subscriptions on every file change. *Mitigation*: Only re-evaluate if the changed file is a `.md` or `.base` file (already handled by `VaultIndex.invalidate_file`).
