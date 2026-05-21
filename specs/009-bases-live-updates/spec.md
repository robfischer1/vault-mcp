# Feature Specification: Bases Live Updates (Subscriptions)

**Feature Branch**: `009-bases-live-updates`

**Created**: 2026-05-21

**Status**: Draft

**Input**: Add subscription-based live updates for base query results. Allow clients to register interest in a base and receive push notifications when relevant files change, eliminating the need for polling.

## Clarifications

### Session 2026-05-21

- **Q: Use MCP resource subscriptions?** -> **A: No.** Use custom MCP tools (`subscribe_base` and `unsubscribe_base`) and custom notification pushes. Existing tools use the tool pattern, push-with-data avoids re-fetch round-trip, and not all MCP clients support resource subscriptions.
- **Q: Should subscription state persist?** -> **A: No.** Cross-process subscriptions are out of scope. Server restart drops all subscriptions; consumers must re-subscribe.
- **Q: How to handle rapid-fire changes?** -> **A: Throttling/coalescing is out of scope for V1.** If churn is observed, it will be addressed in a subsequent phase.
- **Q: What is the relevance detection logic?** -> **A: A file change affects a subscription if the file matches the base filter, OR if the file frontmatter could affect a formula in the subscribed view.**
- **Q: What data is sent in the notification?** -> **A: The full re-evaluated `QueryResult`.**

## User Scenarios & Testing

### User Story 1 - Subscribe to Live Project Dashboard (Priority: P1)

An agent or a dashboard UI wants to display a live list of "Active Projects". It calls `subscribe_base(path="Projects.md", view="Active")`. The tool returns a subscription handle. Whenever a project note is updated (e.g., status changed to "Complete" or a new project created), `vault-mcp` re-evaluates the query and pushes the new result set to the client.

**Why this priority**: This is the primary use case — enabling live UIs and reactive agents without polling overhead.

**Acceptance Scenarios**:

1. **Given** a subscription to a base view, **When** a matching file is modified in a way that changes its formula values, **Then** a notification is pushed with updated results.
2. **Given** a subscription to a base view, **When** a new file is created that matches the base filters, **Then** a notification is pushed with updated results.
3. **Given** a subscription to a base view, **When** a file that previously matched the filters is modified so it no longer matches, **Then** a notification is pushed with updated results (the note is removed).
4. **Given** a subscription to a base view, **When** a file that is outside the base scope/filters is modified, **Then** no notification is pushed.

---

### User Story 2 - Unsubscribe from Updates (Priority: P2)

A client no longer needs live updates for a specific base. it calls `unsubscribe_base(handle="...")`. The server stops sending updates for that subscription and cleans up internal state.

**Acceptance Scenarios**:

1. **Given** an active subscription, **When** the client calls `unsubscribe_base` with the correct handle, **Then** the server returns success and no further notifications are sent for that handle.
2. **Given** an invalid handle, **When** calling `unsubscribe_base`, **Then** the server returns an error.

---

### User Story 3 - Multiple Subscriptions (Priority: P2)

Multiple clients (or one client for multiple bases) can subscribe independently. The server manages them correctly without cross-talk or missing updates.

**Acceptance Scenarios**:

1. **Given** two different subscriptions to two different bases, **When** a file changes that affects only one of them, **Then** only that subscriber receives a notification.
2. **Given** two different subscriptions to the same base, **When** a file changes that affects that base, **Then** both subscribers receive a notification.

## Requirements

### Functional Requirements

- **FR-001**: System MUST provide a `subscribe_base` tool that accepts `path`, `view`, and optional `base_index`.
- **FR-002**: System MUST return a unique `subscription_handle` upon successful subscription.
- **FR-003**: System MUST provide an `unsubscribe_base` tool that accepts a `subscription_handle`.
- **FR-004**: System MUST detect when a file change (via `watcher.py`) affects a subscribed base.
- **FR-005**: Relevance detection MUST trigger re-evaluation if:
    - The file matches the base/view filters (new or existing).
    - The file *used to* match the filters but no longer does.
    - The file matches the filters and its frontmatter/metadata has changed (potentially affecting formula results).
- **FR-006**: System MUST push notifications using the MCP `notifications/` channel (FastMCP doesn't directly expose this, so we may need to use the underlying server or a custom mechanism if FastMCP allows it, or switch to a standard MCP server implementation if necessary). *Correction: FastMCP supports notifications via `context.info` or similar, but the user requested custom push. I need to verify how to send notifications in FastMCP.*
- **FR-007**: Notifications MUST contain the `subscription_handle` and the full `QueryResult`.

### Non-Functional Requirements

- **NFR-001**: Notification latency SHOULD be under 1 second from file change detection.
- **NFR-002**: Subscription state MUST be held in memory only.
- **NFR-003**: Relevance detection MUST be efficient to avoid excessive re-evaluations (e.g., don't re-evaluate all bases for every .md change if possible).

## Success Criteria

- **SC-001**: A client receives a push notification within 1 second of a relevant file change.
- **SC-002**: No notifications are received for changes to files that do not affect the base results.
- **SC-003**: `unsubscribe_base` successfully stops all future notifications for that handle.
- **SC-004**: System handles multiple concurrent subscriptions correctly.

## Assumptions

- We are using `FastMCP`. I need to ensure `FastMCP` supports sending arbitrary notifications. If not, I may need to access the underlying `mcp.server.Server` instance.
- The `VaultIndex` is the central point where file invalidations are handled. This is the natural place to trigger subscription checks.

## Out of Scope

- Persistent subscriptions (durable across restarts).
- Throttling/coalescing (V1 is "push as it happens").
- Subscription to non-Bases queries (e.g., `find_notes_by_frontmatter`).
