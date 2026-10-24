# SPDX-FileCopyrightText: 2026 Rob Fischer
#
# SPDX-License-Identifier: Apache-2.0

"""vault-mcp: read-only MCP query tools for an Obsidian vault."""

from importlib.metadata import version

# Read from the installed distribution so pyproject's static
# `[project].version` stays the single source of the version.
__version__ = version("vault-mcp")
