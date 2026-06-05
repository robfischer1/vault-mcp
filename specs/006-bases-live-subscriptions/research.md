# Research: Bases Live Subscriptions

## Watcher Event Flow

Currently, `watcher.py` uses `watchdog` to monitor the filesystem. When a `.md` or `.base` file changes, the following happens:
1.  `_VaultEventHandler._handle(event)` is called.
2.  It calls `VaultIndex.invalidate_file(path)`.
3.  `VaultIndex.invalidate_file` re-parses the file and updates internal maps (`_content`, `_by_name`, `_outbound`, `_inbound`).

To support subscriptions:
- We need a hook in `VaultIndex.invalidate_file` or a wrapper around it to notify a `SubscriptionManager`.
- The `SubscriptionManager` must know which files are affected by a change.
- A change to `Note A` might affect a Base query if:
    - `Note A` itself matches (or stops matching) the Base filters.
    - `Note A` is a dependency of the Base (e.g., the Base uses `file.links` and `Note A` was linked).
    - `Note A` is the file containing the Base itself (the query definition changed).

## FastMCP Notifications

`FastMCP` provides a way to send notifications to clients. We should use `mcp.publish_resource_change` or a custom notification if the client is subscribed to a specific "Base handle". Since Bases are often inline, we might need a virtual resource URI for them.

## Dependency Tracking

To avoid over-notifying, we should track which properties/notes a Base depends on:
- **Filter dependencies**: Any frontmatter key used in filters (e.g., `status`, `type`).
- **Formula dependencies**: Any frontmatter key or file attribute used in formulas.
- **Link dependencies**: If the query uses `file.links` or `file.backlinks`, changes to the link graph should trigger updates.
- **Global dependencies**: Some queries might depend on the entire vault (e.g., "all orphans").

## Subscription Lifecycle

1.  **Subscribe**: Client calls a tool (or resource subscription) providing the file path and optionally a view name.
2.  **Register**: `SubscriptionManager` parses the Base, extracts dependencies, and stores the subscription.
3.  **Notify**: When `VaultIndex` invalidates a file, `SubscriptionManager` checks all active subscriptions. If the changed file matches the subscription's dependencies, a notification is sent.
4.  **Unsubscribe**: Client explicitly unsubscribes or the session ends.
