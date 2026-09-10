# SPDX-FileCopyrightText: 2026 Rob Fischer
#
# SPDX-License-Identifier: Apache-2.0

import json
import subprocess
from unittest.mock import MagicMock, patch

import pytest

from vault_mcp.cli_client import (
    ObsidianCLI,
    ParamRejectedError,
    render_params,
)


def test_cli_probe_success():
    """Verifies that probe() handles a successful binary check."""
    with (
        patch(
            "vault_mcp.cli_client.shutil.which",
            return_value="/usr/bin/obsidian",
        ),
        patch("subprocess.run") as mock_run,
    ):
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
    with patch(
        "vault_mcp.cli_client.shutil.which", return_value="/usr/bin/obsidian"
    ):
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
    with patch(
        "vault_mcp.cli_client.shutil.which", return_value="/usr/bin/obsidian"
    ):
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
    with patch(
        "vault_mcp.cli_client.shutil.which", return_value="/usr/bin/obsidian"
    ):
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
    with patch(
        "vault_mcp.cli_client.shutil.which", return_value="/usr/bin/obsidian"
    ):
        cli = ObsidianCLI()
        cli._available = True

        res = cli.run("rm_everything")
        assert res["ok"] is False
        assert res["error"] == "cli_invalid_command"
        assert "rm_everything" in res["detail"]


def test_cli_run_timeout():
    """Verifies that run() handles subprocess timeout."""
    with patch(
        "vault_mcp.cli_client.shutil.which", return_value="/usr/bin/obsidian"
    ):
        cli = ObsidianCLI()
        cli._available = True

        with patch(
            "subprocess.run",
            side_effect=subprocess.TimeoutExpired("obsidian", 15),
        ):
            res = cli.run("eval", code="while(true){}")
            assert res["ok"] is False
            assert res["error"] == "cli_timeout"


class TestRenderParams:
    """The security boundary as a pure function — no binary, no subprocess.

    THE COMMAND ALLOWLIST WAS ONLY HALF THE DOOR. `obsidian_cli_command` is an
    MCP verb whose `params` dict comes straight from the caller and went into
    the argument vector unexamined: a `True` value appended the BARE key, so
    `{"--config": True}` put `--config` on obsidian-cli's own command line.
    Every test here fails against that code.
    """

    def test_a_verified_parameter_renders_as_key_equals_value(self):
        assert render_params("plugin:reload", {"id": "my-plugin"}) == [
            "id=my-plugin"
        ]

    def test_a_flag_shaped_name_is_refused(self):
        """The injection itself: a name that could be read as an option."""
        with pytest.raises(ParamRejectedError) as caught:
            render_params("plugin:reload", {"--config": "anything"})
        assert "--config" in str(caught.value)

    def test_a_short_flag_shaped_name_is_refused(self):
        with pytest.raises(ParamRejectedError):
            render_params("eval", {"-e": "x"})

    def test_a_name_the_command_does_not_take_is_refused(self):
        with pytest.raises(ParamRejectedError) as caught:
            render_params("plugin:reload", {"code": "alert(1)"})
        assert "code" in str(caught.value)

    def test_a_command_with_no_verified_surface_takes_no_parameters(self):
        """Fail-closed: the six commands this repo does not drive take none."""
        with pytest.raises(ParamRejectedError):
            render_params("devtools", {"id": "x"})

    def test_a_non_scalar_value_is_refused(self):
        with pytest.raises(ParamRejectedError) as caught:
            render_params("eval", {"code": ["alert(1)"]})
        assert "list" in str(caught.value)

    def test_a_nul_byte_is_refused(self):
        """NUL truncates a C string, so the argument the binary sees is not the
        argument that was validated."""
        with pytest.raises(ParamRejectedError) as caught:
            render_params("eval", {"code": "ok\x00rest"})
        assert "NUL" in str(caught.value)

    def test_a_newline_is_allowed_because_argv_has_no_delimiter(self):
        """Multi-line JavaScript is the ordinary case for `eval`.

        Each parameter is ONE element of the argument vector, so there is
        nothing for a newline to break out of. Refusing control characters
        wholesale would break the feature to buy nothing — which is why the
        rule names NUL specifically.
        """
        assert render_params("eval", {"code": "let a = 1;\nlet b = 2;"}) == [
            "code=let a = 1;\nlet b = 2;"
        ]

    def test_booleans_render_as_values_not_bare_keys(self):
        """The bare-key branch WAS the injection; True is a value now."""
        assert render_params("plugin:reload", {"id": True}) == ["id=true"]
        assert render_params("plugin:reload", {"id": False}) == []


class TestRunRefusesBeforeSpawning:
    def test_a_rejected_parameter_never_reaches_subprocess(self):
        """The envelope is not enough — the spawn must not happen at all."""
        with patch(
            "vault_mcp.cli_client.shutil.which",
            return_value="/usr/bin/obsidian",
        ):
            cli = ObsidianCLI()
            cli._available = True
            with patch("subprocess.run") as mock_run:
                res = cli.run("plugin:reload", **{"--config": "anything"})
                assert res["ok"] is False
                assert res["error"] == "cli_invalid_param"
                assert mock_run.called is False
