import os
from unittest.mock import AsyncMock, MagicMock

import pytest

os.environ["VAULT_MCP_PATH"] = "."
from server import SubscriptionManager
from vault_mcp.index import VaultIndex


@pytest.fixture
def mock_mcp():
    return MagicMock()


@pytest.fixture
def sub_mgr(mock_mcp):
    return SubscriptionManager(mock_mcp)


@pytest.mark.asyncio
async def test_subscription_lifecycle(sub_mgr):
    handle = sub_mgr.add("Projects.md", "Active", 0)
    assert handle.startswith("sub_")
    assert handle in sub_mgr.subscriptions

    success = sub_mgr.remove(handle)
    assert success is True
    assert handle not in sub_mgr.subscriptions


@pytest.mark.asyncio
async def test_notification_triggered(sub_mgr, tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    project_file = vault / "ProjectA.md"
    project_file.write_text("---\nstatus: Active\n---\n", encoding="utf-8")

    base_file = vault / "Projects.md"
    base_file.write_text('```base\nfilters: \'note["status"] == "Active"\'\n```', encoding="utf-8")

    idx = VaultIndex(vault)
    idx.reindex()

    session = AsyncMock()
    from server import _active_sessions
    _active_sessions.clear()
    _active_sessions.add(session)

    handle = sub_mgr.add("Projects.md", None, 0)

    import server
    original_get_index = server._get_index
    server._get_index = lambda: idx

    try:
        from vault_mcp.bases import execute_base, parse_file
        pf = parse_file(base_file)
        result = execute_base(pf.bases[0], idx)
        sub_mgr.subscriptions[handle].last_result_hash = sub_mgr._hash_result(result)

        project_file.write_text("---\nstatus: Completed\n---\n", encoding="utf-8")
        idx.invalidate_file(project_file)

        await sub_mgr.notify_all(project_file)

        assert session.send_notification.called
        call_args = session.send_notification.call_args
        notification = call_args[0][0]
        assert notification.method == "notifications/bases/update"
        params = notification.params
        if hasattr(params, "model_dump"):
            params = params.model_dump()
        elif not isinstance(params, dict):
            params = vars(params)

        assert params["handle"] == handle
        assert params["results"]["total"] == 0
    finally:
        server._get_index = original_get_index


@pytest.mark.asyncio
async def test_no_notification_if_result_unchanged(sub_mgr, tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    project_file = vault / "ProjectA.md"
    project_file.write_text("---\nstatus: Active\n---\n", encoding="utf-8")

    base_file = vault / "Projects.md"
    base_file.write_text('```base\nfilters: \'note["status"] == "Active"\'\n```', encoding="utf-8")

    idx = VaultIndex(vault)
    idx.reindex()

    session = AsyncMock()
    from server import _active_sessions
    _active_sessions.clear()
    _active_sessions.add(session)

    handle = sub_mgr.add("Projects.md", None, 0)

    import server
    original_get_index = server._get_index
    server._get_index = lambda: idx

    try:
        from vault_mcp.bases import execute_base, parse_file
        pf = parse_file(base_file)
        result = execute_base(pf.bases[0], idx)
        sub_mgr.subscriptions[handle].last_result_hash = sub_mgr._hash_result(result)

        project_file.write_text("---\nstatus: Active\nunrelated: value\n---\n", encoding="utf-8")
        idx.invalidate_file(project_file)

        await sub_mgr.notify_all(project_file)

        assert not session.send_notification.called
    finally:
        server._get_index = original_get_index
