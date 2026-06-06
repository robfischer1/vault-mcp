# Feature Specification: Bases Live Subscriptions

**Feature Branch**: `006-bases-live-subscriptions`

**Created**: 2026-05-21

**Status**: Draft

**Input**: Add subscription-based live updates for base query results. Phase 1 executes queries on demand. This feature extends the model so that query results can be subscribed-to: an agent registers interest in a base's results and gets pushed updates when underlying files change.

## Problem Statement

Currently, to get the latest results from an Obsidian Base query, a client (agent or UI) must repeatedly poll the `execute_base` tool. This creates unnecessary CPU/network overhead, increases latency between a file change and the consumer receiving the update, and doesn't scale well for multiple concurrent consumers or complex dashboards.

## User Value

Users and agents can subscribe to a base query and receive immediate, push-based notifications when the result set changes. 
- **Live Dashboards**: A status-tracker UI can display up-to-date project statuses without polling.
- **Reactive Agents**: An agent can "watch" a base (e.g., "High Priority Tasks") and react immediately when a new task matches the filter or an existing task is updated.
- **Efficiency**: Reduces churn and latency for any consumer that wants up-to-date base results.

## Proposed Solution

Extend `vault-mcp` with an in-memory subscription registry and integration with the existing `watcher.py` filesystem observer.
1. **Tools**: Implement `subscribe_to_base` and `unsubscribe_from_base` MCP tools.
2. **Registry**: Maintain a server-side mapping of subscription handles to (base_path, view_name, client_session).
3. **Watcher Integration**: When `watcher.py` detects a file change, the system identifies affected subscriptions.
4. **Relevance Detection**: A subscription is affected if:
    - The changed file matches the base's filters (it was already in the set or should now be added).
    - The changed file *previously* matched the filters but now doesn't (it should be removed).
    - The changed file's frontmatter properties that are referenced in the base's formulas have changed (the computed values for an existing row have changed).
5. **Notification**: Re-evaluate the query for affected subscriptions and push the new result set to the client via MCP's notification channel.

## Detailed Scope

### In-Scope
- **`subscribe_to_base` tool**: Accepts `path` (to markdown file), optional `view` name, and optional `base_index`. Returns a unique `subscription_handle`.
- **`unsubscribe_from_base` tool**: Accepts `subscription_handle` and cancels the subscription.
- **Live Updates**: Pushing full result sets (or diffs if architectural complexity allows, but brief implies re-evaluation) when a relevant file changes.
- **Relevance Detection**:
    - Filter-based relevance (folder, tags, frontmatter comparisons).
    - Formula-based relevance (detecting if a modified property is used in any formula in the view).
- **Session Lifecycle**: Subscriptions are tied to the active MCP session. If the session ends or the server restarts, subscriptions are dropped.

### Out-of-Scope
- **Cross-process persistence**: Subscriptions do not survive server restarts. Consumers must re-subscribe.
- **Throttling/Coalescing**: Initial implementation will push updates immediately. Throttling for rapid-fire changes (e.g., bulk file moves) is deferred to a future version if churn is observed.
- **Delta Updates**: Initial scope pushes the full result set on every change, rather than just the changed rows.
- **Persistence of subscription state**: No local database or config file for active subscriptions.

## User Scenarios & Testing

### User Story 1 - Subscribe to a Project Tracker (Priority: P1)

An agent is monitoring a "Active Projects" base. It calls `subscribe_to_base` for the `Projects.md` file. Whenever a project's status changes from "In Progress" to "Complete" in the vault, the agent receives an updated list of active projects without having to poll.

**Acceptance Scenarios**:
1. **Given** an active subscription to a base, **When** a file matching the base's filters is modified in the vault, **Then** the server pushes an update notification with the new query results.
2. **Given** an active subscription, **When** a new file is created that matches the filters, **Then** the server pushes an update.
3. **Given** an active subscription, **When** a file is modified such that it no longer matches the filters, **Then** the server pushes an update with the file removed from the set.

---

### User Story 2 - Unsubscribe from Updates (Priority: P2)

When an agent finishes its task or the user closes a dashboard, it calls `unsubscribe_from_base` with the handle. The server stops sending updates and cleans up the registration.

**Acceptance Scenarios**:
1. **Given** a valid subscription handle, **When** `unsubscribe_from_base` is called, **Then** the server confirms success and no further notifications are sent for that handle.
2. **Given** an invalid or expired handle, **When** `unsubscribe_from_base` is called, **Then** the server returns an appropriate error.

---

### Edge Cases
- **File Deletion**: If the markdown file containing the `base` definition is deleted, all associated subscriptions should be cancelled and a final "error" notification sent to subscribers.
- **Broken YAML**: If a file change makes the `base` YAML invalid, subscribers should receive a warning/error notification.
- **Irrelevant Changes**: Modifying a file that doesn't match the base's filter and whose properties aren't used in formulas should NOT trigger a notification.
- **Server Shutdown**: Clients must handle the connection closing as an implicit unsubscribe.

## Requirements

### Functional Requirements
- **FR-001**: System MUST provide a `subscribe_to_base` tool.
- **FR-002**: System MUST provide an `unsubscribe_from_base` tool.
- **FR-003**: System MUST uniquely identify subscriptions per client session.
- **FR-004**: System MUST integrate with `watcher.py` to receive file-change events.
- **FR-005**: System MUST implement a `SubscriptionManager` to track active subscriptions and their criteria.
- **FR-006**: System MUST re-evaluate queries only when a "relevant" change is detected (optimization).
- **FR-007**: System MUST push notifications via the MCP `notifications/resources/updated` method or a custom `notifications/base/updated` if appropriate. *Decision: Use a custom notification for tool-based results to avoid confusion with resource-based subscriptions.*

### Key Entities
- **SubscriptionHandle**: A unique string identifying a specific subscription.
- **Subscription**: A record containing the base reference (path, view), the filter/formula criteria for relevance detection, and the client session ID.
- **UpdateNotification**: The payload sent to the client, containing the `subscription_handle` and the new `QueryResult`.

## Success Criteria

### Measurable Outcomes
- **SC-001**: An update notification is received by the client within 1 second of a relevant file being saved to disk (measured from the time the OS notifies the watcher).
- **SC-002**: Zero notifications are sent for file changes that are demonstrably irrelevant (e.g., changes to files in a different folder that don't match any filter).
- **SC-003**: A client can subscribe and unsubscribe 100 times in a row without leaking memory or causing server instability.
- **SC-004**: Multiple clients can subscribe to the same base and receive independent notifications.

## Architectural Considerations

- **Watcher Extension**: `watcher.py` currently invalidates the `VaultIndex`. It needs a hook or a subscriber pattern so `SubscriptionManager` can react to changes.
- **Relevance Logic**: This is the most complex part. The manager needs to analyze the `Base` object to find all referenced frontmatter keys and folder paths to avoid over-notifying.
- **MCP Notification Channel**: Implementation must ensure that notifications are sent only to the session that created the subscription.
- **Concurrency**: The `SubscriptionManager` must be thread-safe as watcher events and tool calls may occur simultaneously.
