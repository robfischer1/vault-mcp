# Clarification: Bases Live Subscriptions

This document addresses critical ambiguities in the feature specification for live subscriptions.

## 1. Notification Protocol
**Question**: Should the server use standard MCP resource notifications (`notifications/resources/updated`) or a custom notification type?

**Clarification**: Since the subscription is initiated via a tool and returns a result set that matches the `execute_base` output, we will use a custom notification: `notifications/vault/base_updated`. This notification will include the `subscription_handle` and the updated `result` (the same structure as `execute_base` output). This avoids overloading the resource system, which typically deals with raw file contents.

## 2. Subscription Handle Generation
**Question**: How should handles be generated?

**Clarification**: Handles will be opaque strings (e.g., `sub_` + random UUID fragment). They must be unique within the server's lifecycle.

## 3. Session Lifecycle Management
**Question**: How will the server detect client disconnection to clean up subscriptions?

**Clarification**: The `vault-mcp` server uses `FastMCP`. We will hook into the session context if available, or rely on the transport layer's connection status. For Phase 1, we will provide a robust `unsubscribe` tool and, if possible, a cleanup task that runs when a client session terminates (if FastMCP provides such a hook).

## 4. Relevance Detection for Formulas
**Question**: How deep should the relevance detection go? If a formula uses `file.mtime`, should every file update trigger a notification?

**Clarification**: Yes. If a property referenced in a formula (or a built-in like `file.mtime`) changes, the subscription is considered affected. We will extract all property names used in `if()`, `.map()`, and other formula parts during subscription registration to build a "dependency map".

## 5. Non-Existent Bases
**Question**: Can a client subscribe to a path that does not currently contain a base?

**Clarification**: No. `subscribe_to_base` will return an error if no base is found at the specified path/index at the time of subscription. If the base is later removed or the file is deleted, the server will send a final "cancellation" notification to the subscriber.

## 6. Notification Payload
**Question**: Should the notification contain the full results or just a "ping" to re-fetch?

**Clarification**: To maximize value and minimize latency, the notification will contain the FULL updated result set. This allows the consumer to update its UI immediately without an additional round-trip.
