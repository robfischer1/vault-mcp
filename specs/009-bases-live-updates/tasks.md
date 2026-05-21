# Tasks: Bases Live Updates

## Phase 1: Infrastructure

- [x] Add `on_invalidate` callback support to `VaultIndex.invalidate_file` in `src/vault_mcp/index.py`. `[FR-004]`
- [x] Implement `SubscriptionManager` in `server.py` to hold subscription state and handle notifications. `[FR-005, FR-006, FR-007]`
- [x] Add a `hash_query_result` helper function to `SubscriptionManager` for relevance detection. `[FR-005]`

## Phase 2: MCP Tools

- [x] Add `subscribe_base` tool to `server.py`. `[FR-001, FR-002]`
- [x] Add `unsubscribe_base` tool to `server.py`. `[FR-003]`

## Phase 3: Integration & Testing

- [x] Create `tests/test_live_updates.py` with mock MCP session to verify notification flow. `[SC-001, SC-004]`
- [x] Verify that file changes trigger notifications for subscribed bases. `[SC-001]`
- [x] Verify that irrelevant file changes do NOT trigger notifications. `[SC-002]`
- [x] Verify that `unsubscribe_base` stops notifications. `[SC-003]`
- [x] Ensure `mypy` and `ruff` are clean. `[V. Strict Quality Tooling]`
