# Tasks: Bases Live Subscriptions

Phase 4 of Spec 006: Implementing push-based notifications for Obsidian Bases queries.

## Phase 1: Preparation & Research

- [ ] **Research**: Confirm `FastMCP` notification patterns for custom events. If `publish_resource_change` is insufficient, identify how to access the underlying session for `send_notification`.
- [ ] **Analysis**: Audit `bases.py` AST evaluator for potential reuse in `extract_dependencies`. We need to collect all names and property accesses from formulas.

## Phase 2: Test Creation (Test-First) 🧪

- [ ] **Unit Tests (Dependencies)**: Add tests to `tests/test_bases.py` for `extract_dependencies`.
    - Verify filter key extraction (e.g., `status`, `type`).
    - Verify formula dependency extraction (Tier 1 and Tier 2).
    - Verify folder/path/link dependency flags.
- [ ] **Unit Tests (Manager)**: Create `tests/test_subscription_manager.py`.
    - Test `register`/`unregister` lifecycle.
    - Test `get_affected_subscriptions` with various file changes (matching/not matching).
    - Mock `VaultIndex` to simulate file invalidation events.
- [ ] **Integration Tests (End-to-End)**: Create `tests/test_live_subscriptions.py`.
    - Test `subscribe_to_base` tool returns a handle.
    - Test `unsubscribe_from_base` tool.
    - **Crucial**: Verify that modifying a file in a temporary vault triggers a notification sent to the client.

## Phase 3: Core Logic (Dependency Analysis)

- [ ] **Implementation**: Implement `extract_dependencies(base: Base, view_name: str | None = None)` in `src/vault_mcp/bases.py`.
    - Traverse `base.filters` (and view-specific filters).
    - Parse `base.formulas` using a visitor to collect property names.
    - Handle special cases like `file.links`, `file.folder`, etc.
- [ ] **Verification**: Run `extract_dependencies` unit tests.

## Phase 4: Subscription Management

- [ ] **Data Model**: Define `Subscription` dataclass in `src/vault_mcp/bases.py`.
- [ ] **Manager Implementation**: Implement `SubscriptionManager` in `src/vault_mcp/bases.py`.
    - Maintain internal maps for fast lookups (handle -> sub, key -> handles).
    - Implement `notify_file_changed(path, new_fm)` logic.
    - Ensure thread-safety for concurrent registration and notification.
- [ ] **Verification**: Run `SubscriptionManager` unit tests.

## Phase 5: VaultIndex & Server Integration

- [ ] **VaultIndex Hook**: Add `on_change` listener list to `VaultIndex` in `src/vault_mcp/index.py`.
    - Update `invalidate_file` to trigger listeners after re-parsing.
- [ ] **Server Tools**: Register `subscribe_to_base` and `unsubscribe_from_base` tools in `server.py`.
    - Link `SubscriptionManager` to the `VaultIndex` instance.
- [ ] **Notification Delivery**: Implement the push mechanism in `server.py`.
    - Map `SubscriptionManager` notifications to MCP protocol messages.
    - Ensure notifications contain the `subscription_handle` and (optionally) the updated `QueryResult`.
- [ ] **Session Management**: Ensure subscriptions are cleaned up if a client session terminates (if possible with FastMCP).

## Phase 6: Validation & Polish

- [ ] **Documentation**: Update `docs/architecture.md` or `GEMINI.md` to reflect the new subscription capability.
- [ ] **Full Suite**: Run `uv run pytest` and ensure all 70+ existing tests pass alongside new ones.
- [ ] **Linting**: Run `uv run ruff check src/ tests/` and fix any issues.
- [ ] **Type Checking**: Run `uv run mypy src/` and ensure zero errors in new code.

## Success Criteria Verification

- [ ] **SC-001**: Client receives notification within 1s of file change (manual or automated test).
- [ ] **SC-002**: No notifications sent for irrelevant changes (verified by `test_subscription_manager.py`).
- [ ] **SC-003**: Handle churn: Subscribe/Unsubscribe 100x without leak.
- [ ] **SC-004**: Multi-client support: Two "clients" receive notifications for the same base change.
