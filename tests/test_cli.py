import json
import subprocess
from unittest.mock import MagicMock, patch

import pytest
from vault_mcp.cli_client import ObsidianCLI


def test_cli_probe_success():
    """Verifies that probe() handles a successful binary check."""
    with patch("vault_mcp.cli_client.shutil.which", return_value="/usr/bin/obsidian"):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0, stdout="1.0.0\n", stderr=""
            )
            cli = ObsidianCLI()
            res = cli.probe()

            assert res["available"] is True
            assert res["version"] == "1.0.0"
            assert res["error"] is None


def test_cli_probe_failure():
    """Verifies that probe() handles a missing binary."""
    with patch("vault_mcp.cli_client.shutil.which", return_value=None):
        cli = ObsidianCLI()
        res = cli.probe()

        assert res["available"] is False
        assert res["error"] == "cli_not_found"


def test_cli_run_success_json():
    """Verifies that run() parses JSON output."""
    with patch("vault_mcp.cli_client.shutil.which", return_value="/usr/bin/obsidian"):
        cli = ObsidianCLI()
        cli._available = True

        with patch("subprocess.run") as mock_run:
            data = {"status": "ok", "plugins": ["p1", "p2"]}
            mock_run.return_value = MagicMock(
                returncode=0, stdout=json.dumps(data), stderr=""
            )

            res = cli.run("devtools")
            assert res["ok"] is True
            assert res["data"] == data


def test_cli_run_success_text():
    """Verifies that run() returns raw text if not JSON."""
    with patch("vault_mcp.cli_client.shutil.which", return_value="/usr/bin/obsidian"):
        cli = ObsidianCLI()
        cli._available = True

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0, stdout="Success!\n", stderr=""
            )

            res = cli.run("plugin:reload", id="my-plugin")
            assert res["ok"] is True
            assert res["data"] == "Success!"

            args = mock_run.call_args[0][0]
            assert "/usr/bin/obsidian" in args
            assert "plugin:reload" in args
            assert "id=my-plugin" in args


def test_cli_run_error():
    """Verifies that run() returns error envelope on failure."""
    with patch("vault_mcp.cli_client.shutil.which", return_value="/usr/bin/obsidian"):
        cli = ObsidianCLI()
        cli._available = True

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=1, stdout="", stderr="Plugin not found\n"
            )

            res = cli.run("plugin:reload", id="bad-id")
            assert res["ok"] is False
            assert res["error"] == "cli_error"
            assert "Plugin not found" in res["detail"]


def test_cli_run_allowlist_rejection():
    """Verifies that run() rejects commands not in the allowlist."""
    with patch("vault_mcp.cli_client.shutil.which", return_value="/usr/bin/obsidian"):
        cli = ObsidianCLI()
        cli._available = True

        res = cli.run("rm_everything")
        assert res["ok"] is False
        assert res["error"] == "cli_invalid_command"
        assert "rm_everything" in res["detail"]


def test_cli_run_timeout():
    """Verifies that run() handles subprocess timeout."""
    with patch("vault_mcp.cli_client.shutil.which", return_value="/usr/bin/obsidian"):
        cli = ObsidianCLI()
        cli._available = True

        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("obsidian", 15)):
            res = cli.run("eval", code="while(true){}")
            assert res["ok"] is False
            assert res["error"] == "cli_timeout"
