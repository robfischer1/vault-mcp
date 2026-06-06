Add subscription-based live updates for base query results. Phase 1 executes queries on demand against the in-memory vault index, which is refreshed via the existing `watcher.py` infrastructure when files change. This brief extends the model so that query results can be subscribed-to: an agent registers interest in a base's results and gets pushed updates when underlying files change in ways that affect the result set. Differs from a request-response model in that the agent doesn't have to poll.

The user value is live dashboards. A status-tracker UI consuming vault-mcp doesn't have to poll on a timer; it gets pushed updates when status frontmatter changes. Reduces churn and latency for any consumer that wants up-to-date base results.

### Initial scope

- Subscribe tool returning a subscription handle from `(path, view?, base_index?)`.
- Update push: when the watcher detects a relevant file change, re-evaluate affected subscriptions and notify subscribers via MCP's notification channel.
- Unsubscribe tool: cancel a subscription by handle.
- Relevance detection: a file change affects a subscription only if the file matches the base's filter, OR if the file's frontmatter could affect a formula in the subscribed view.

### Out of scope

- Cross-process subscriptions (server restart drops all subscriptions; consumers must re-subscribe).
- Persistence of subscription state.
- Throttling or coalescing logic for rapid-fire changes (v2 if churn is observed).

### Success criteria

- A subscription to a base view receives a push notification within 1 second of a file change that affects results.
- Files outside the base's scope do not trigger spurious pushes.
- subscribe / unsubscribe round-trip works correctly under typical agent workflow timing.

### Context

Phase 2 of vault-mcp Bases support. The architectural ramifications (MCP subscription model, watcher extension, in-memory subscription state) are substantial enough that this brief is likely to surface several clarification questions. May warrant splitting into "subscription model" and "live-query semantics" as separate features.
