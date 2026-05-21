# Research: Bases Live Updates

## MCP Notifications in FastMCP

Research indicates that `FastMCP` tools can send notifications using the injected `Context` object. For background updates (not triggered directly by a tool call), we need to access the underlying session(s).

### Proposed Mechanism

1.  **Subscription Storage**: A global `SubscriptionManager` (or a dict in `server.py`) will map `subscription_handle` to a `Subscription` object.
2.  **Session Tracking**: To send notifications, we need a reference to the active `mcp.server.Server` and the `session_id` of the subscriber.
3.  **Watcher Integration**: `VaultIndex` already has an `invalidate_file` method called by `watcher.py`. We can add an `on_invalidate` callback to `VaultIndex`.
4.  **Relevance Evaluation**: When a file is invalidated:
    *   The `SubscriptionManager` iterates through all active subscriptions.
    *   For each subscription, it re-executes the base query.
    *   If the result set (notes paths + formula hashes) has changed, it pushes a notification.

### Notification API

FastMCP's `Context` provides:
*   `ctx.session.send_notification(method, params)`

In the background (watcher thread), we won't have the `Context` object. We will need to access the `FastMCP` instance's underlying server:
`mcp._server.request_context.sessions` (or similar, depending on the transport).

For STDIO (most common), there is usually only one session.

## Relevance Detection Optimization

Re-executing every subscribed base on every file change might be expensive if many bases are subscribed.

**Refinement**:
*   A base usually filters on a `scope` (folder) or specific `frontmatter`.
*   We can check if the invalidated file's path starts with the base's folder scope (if any).
*   However, since bases can have arbitrary filters, re-execution is the most reliable way.
*   For V1, we will re-execute all active subscriptions on any `.md` or `.base` file change.

## Throttling

If a user makes many rapid changes, we might send too many notifications.
**Decision**: V1 will not implement throttling. Push-on-change is the requirement. If performance becomes an issue, we will add a 500ms debounce in a later phase.
