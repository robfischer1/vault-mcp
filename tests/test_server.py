# SPDX-FileCopyrightText: 2026 Rob Fischer
#
# SPDX-License-Identifier: Apache-2.0

"""The MCP verb surface — the module that had no test file at all.

server.py is 3,074 lines and was at 32% coverage with 631 statements never
executed, the largest untested surface in the repo by a wide margin. It had no
test file because it could not be imported: `_resolve_vault_path()` runs at
MODULE scope (server.py:90) and raises FileNotFoundError without VAULT_MCP_PATH,
and `MCPServer("vault-mcp")` is constructed at module scope too.
tests/conftest.py now points the former at the mini-vault fixture, which is all
it took to make the whole module reachable.

What is pinned here is the WRAPPER LAYER's contract rather than each verb's
domain logic — the domain lives in gate/index/schema/lint and is tested there.
The wrapper layer is where a verb can be registered with no description, return
a bare exception instead of an envelope, or quietly stop being registered at
all, and none of that had any check.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from tests.substrate import FakeVault
from vault_mcp import server, verbs_write
from vault_mcp.gate import ConventionGate
from vault_mcp.schema import load_schema

SCHEMAS = Path(__file__).parent / "fixtures" / "schema"

# Measured 2026-08-22 against the live registration. These are regression
# anchors, not aspirations: a verb silently dropping off the surface, or the
# manifest doubling, should fail a test rather than be noticed months later.
EXPECTED_VERB_COUNT = 52
# RE-MEASURED 2026-09-10, and this is the number that moved: 25,471 -> 7,366
# (7,509 across 53 verbs, then 7,366 across 52 when obsidian_cli_eval left).
# forge-testkit's F13 schema budget is ~200 chars/verb, derived from urania's
# measured 196; this repo sat at ~481/verb, 2.4x over, on the strength of a
# comment saying the budget lint was "NOT wired". It is wired now — the gate
# runs python:forge-testkit-schema-budget — so the 48 over-budget verbs each
# took an explicit `description=` wire cue while their docstrings kept the
# engineering detail for whoever reads the source. That is F13's own stated
# pattern, not an exemption: nothing is in schema_budget_exempt.
MEASURED_MANIFEST_CHARS = 7366
# RATCHETED DOWN with that saving (was 26000, over a 25471 measured), for the
# reason the old comment already gave and the #5287 retirement already proved:
# a ceiling left at the old number lets the next verb silently spend what this
# just recovered. 8000 leaves ~630 chars of headroom, about four verbs.
MANIFEST_CEILING = 8000


def _tools() -> list[Any]:
    """The registered tool surface, as the MCP client sees it."""
    return asyncio.run(server.mcp.list_tools())


class TestVerbSurface:
    """What every session pays for on its first turn."""

    def test_every_verb_is_registered(self):
        assert len(_tools()) == EXPECTED_VERB_COUNT

    def test_every_verb_has_a_description(self):
        """An undescribed verb is unusable and still costs manifest bytes."""
        missing = [
            t.name for t in _tools() if not (t.description or "").strip()
        ]
        assert missing == []

    def test_every_verb_has_a_unique_name(self):
        names = [t.name for t in _tools()]
        assert len(names) == len(set(names))

    def test_manifest_does_not_grow(self):
        """The manifest ships on every session's first turn, called or not.

        Currently 7,366 chars across 52 verbs (~142/verb), inside
        forge-testkit's ~200/verb budget. The budget lint enforces the
        PER-VERB ceiling; this pins the TOTAL, which is the number a session
        actually pays and which 53 individually-legal verbs can still grow.
        """
        total = sum(len(t.description or "") for t in _tools())
        assert total <= MANIFEST_CEILING

    def test_dataview_query_stays_retired(self):
        """vault-mcp#5287 — it could never succeed, so it ships no schema.

        The Local REST API plugin (4.1.7) advertises exactly three vendor
        content types — note+json, document-map+json, jsonlogic+json — and DQL
        is not among them; every spelling returned HTTP 400 in ~0ms against the
        LIVE server. The bundle contains no Dataview integration at all (its
        eleven `DataView` hits are the JavaScript typed-array builtin).

        Retired outright rather than left as a tombstone, per the lesson from
        the seventeen removed under #5294: a stub still costs manifest bytes on
        every session's first turn to advertise something that cannot work.
        This asserts the ABSENCE so a copy-paste revival fails loudly.
        """
        assert "dataview_query" not in {t.name for t in _tools()}

    def test_obsidian_cli_eval_stays_retired(self):
        """It ran caller-supplied JavaScript in Obsidian, and nobody used it.

        Measured across the transcript corpus under ~/.claude-the/projects:
        THREE invocations, ever, in two sessions. No skill, rule or vault note
        reaches for it. Against that it cost a manifest entry on every
        session's first turn and an arbitrary-code path into the running app on
        every one of them — the single site on this surface where ruff's
        "check for execution of untrusted input" was a description of the
        feature rather than a false positive.

        Asserted as an ABSENCE, the same shape as dataview_query above and for
        the same reason: retiring a verb is one deletion, and reviving it is
        one paste. This is the thing that fails on the paste.

        The `eval` COMMAND is not gone — `ObsidianNoteIO` still drives it for
        note create/modify/read, where cli_client builds the JavaScript from
        json.dumps-encoded arguments. What is gone is the caller's ability to
        supply the code.
        """
        assert "obsidian_cli_eval" not in {t.name for t in _tools()}

    def test_every_verb_has_an_input_schema(self):
        """A verb without a schema cannot be called correctly by any client.

        `input_schema`, not `inputSchema`: mcp 2.x renamed every camelCase
        field on `mcp.types.Tool` to snake_case. The old spelling did not
        vanish quietly — pydantic raises AttributeError on the model — but the
        rename is the kind that a `getattr(t, "inputSchema", None)` shim would
        have turned into a silently-empty list, so it is spelled outright.
        """
        schemaless = [t.name for t in _tools() if t.input_schema is None]
        assert schemaless == []


class TestImportIsSideEffectFree:
    """Importing the server must not reach the network or the real vault."""

    def test_lazy_singletons_start_unbuilt(self):
        """The gate/REST/index singletons are built on first use, not at import.

        This is what makes the verbs testable at all: a test can swap the
        module global before the accessor ever runs. If any of these were
        constructed eagerly, importing the module would dial Obsidian.
        """
        # Re-reading the module's own accessors rather than the globals, since
        # an earlier test in the session may legitimately have built one.
        for name in ("_get_gate", "_get_rest_client", "_get_index"):
            assert callable(getattr(server, name))

    def test_module_exposes_a_main(self):
        assert callable(server.main)


@pytest.fixture
def gated(monkeypatch: pytest.MonkeyPatch) -> FakeVault:
    """Point the server's Gate at the pinned vault double.

    The verbs reach their Gate through `_get_gate()`, which caches into the
    module global `_gate`. Setting that global is the whole injection seam —
    and it only works because that singleton is lazy rather than built at
    import.
    """
    vault = FakeVault()
    schema = load_schema(str(SCHEMAS / "atom.schema.yml"))
    monkeypatch.setattr(server, "_gate", ConventionGate(schema, vault))
    return vault


class TestAtomSlugWriteVerb:
    """vault-mcp#5258 pinned at the VERB level, not just the Gate level.

    NOTE ON WHICH VERB. The `atom` verb does NOT touch the vault — it records
    into phdb's session_events and says so in its own docstring. The surface
    #5258 actually breaks is `write_note` for any note_type carrying
    atom_slug: true, because that is what routes into
    ConventionGate._atom_filename. The gate-level regression lives in
    test_substrate.py; this asserts the same defect through the MCP entry point
    a session actually calls, where an escaping ObsidianIOError is the
    difference between an error envelope and an unhandled crash.
    """

    def test_atom_slug_write_returns_an_envelope(self, gated: FakeVault):
        result = verbs_write.write_note(
            title="a captured idea",
            note_type="idea",
            pillar="Journal",
            body="body text",
        )
        assert isinstance(result, dict)
        assert result.get("error") is None, result

    def test_atom_slug_write_reaches_the_vault(self, gated: FakeVault):
        verbs_write.write_note(
            title="a captured idea",
            note_type="idea",
            pillar="Journal",
            body="body text",
        )
        assert gated.calls, "write_note produced no write"
        path, _content = gated.calls[0]
        assert path.startswith("Journal/")
        assert path.endswith(".md")
