"""The substrate's own tests — a fake nobody checks is a fake nobody can trust.

Two jobs here. The first half pins `FakeVault` to the behaviour of the real
`RestNoteIO` it doubles for; a fake that drifts from its original is worse than
no fake, because it makes a green suite mean less than nothing.

The second half is the regression that the five permissive fakes could not
express: `ConventionGate._atom_filename` against a NoteIO that fails the way
production fails (vault-mcp#5258).
"""

from __future__ import annotations

import pytest

from tests.substrate import FakeVault

NOTE_JSON = "application/vnd.olrapi.note+json"
from vault_mcp.cli_client import ObsidianIOError


class TestPinnedFailureModes:
    """The fake must raise what the real one raises — not KeyError."""

    def test_missing_read_raises_obsidian_io_error(self):
        vault = FakeVault()
        with pytest.raises(ObsidianIOError):
            vault.read_note("Journal/absent.md")

    def test_missing_read_is_not_a_key_error(self):
        """The precise divergence that hid #5258 for two and a half months.

        Every replaced FakeVault backed `read_note` with a dict lookup, so a
        missing note surfaced as KeyError. Production raises ObsidianIOError,
        which does NOT subclass KeyError — so a caller catching KeyError
        catches the fake and misses the real thing.
        """
        vault = FakeVault()
        with pytest.raises(ObsidianIOError) as caught:
            vault.read_note("Journal/absent.md")
        assert not isinstance(caught.value, KeyError)
        assert not isinstance(caught.value, OSError)

    def test_missing_delete_raises_before_moving_anything(self):
        vault = FakeVault(store={"Journal/kept.md": "body"})
        with pytest.raises(ObsidianIOError):
            vault.delete_note("Journal/absent.md")
        assert vault.store == {"Journal/kept.md": "body"}
        assert vault.deleted == []

    def test_injected_failure_reaches_the_error_path(self):
        vault = FakeVault(store={"a.md": "x"}, fail={"a.md"})
        with pytest.raises(ObsidianIOError):
            vault.read_note("a.md")


class TestRestSemantics:
    """Behaviours copied from RestNoteIO rather than invented."""

    def test_create_overwrites_because_rest_puts(self):
        vault = FakeVault(store={"a.md": "old"})
        vault.create_note("a.md", "new")
        assert vault.store["a.md"] == "new"

    def test_cli_flavour_refuses_create_over_existing(self):
        vault = FakeVault(store={"a.md": "old"}, refuse_create_over_existing=True)
        with pytest.raises(ObsidianIOError):
            vault.create_note("a.md", "new")
        assert vault.store["a.md"] == "old"

    def test_delete_moves_to_trash_rather_than_dropping(self):
        vault = FakeVault(store={"Journal/x.md": "body"})
        vault.delete_note("Journal/x.md")
        assert "Journal/x.md" not in vault.store
        assert vault.store[".trash/Journal/x.md"] == "body"

    def test_list_skips_trash(self):
        vault = FakeVault(store={"a.md": "x"})
        vault.delete_note("a.md")
        assert vault.list_notes() == []

    def test_list_returns_markdown_only(self):
        vault = FakeVault(store={"a.md": "x", "b.canvas": "y"})
        assert vault.list_notes() == ["a.md"]

    def test_list_over_a_missing_subtree_is_empty_not_an_error(self):
        vault = FakeVault(store={"Journal/a.md": "x"})
        assert vault.list_notes("Nowhere") == []

    def test_list_non_recursive_returns_immediate_children_only(self):
        vault = FakeVault(
            store={
                "Journal/a.md": "x",
                "Journal/deep/b.md": "y",
            }
        )
        assert vault.list_notes("Journal", recursive=False) == ["Journal/a.md"]
        assert vault.list_notes("Journal", recursive=True) == [
            "Journal/a.md",
            "Journal/deep/b.md",
        ]


class TestAtomFilenameRegression:
    """vault-mcp#5258 — the bug the permissive fakes could not see."""

    def test_atom_write_survives_a_production_shaped_not_found(
        self, gate_factory
    ):
        """Drive the REAL Gate through a NoteIO that fails like production.

        `_atom_filename` probes candidate slugs and treats "not found" as "this
        one is free". Against `(KeyError, OSError)` the ObsidianIOError that
        RestNoteIO actually raises escapes instead, and the write dies. This
        test fails on gate.py before the one-line fix and passes after it.
        """
        gate, vault = gate_factory(schema="atom")
        result = gate.create_note(
            title="a captured idea",
            note_type="idea",
            pillar="Journal",
            body="body text",
        )
        assert result.path.startswith("Journal/")
        assert result.path in vault.store

    def test_the_probe_skips_slugs_already_taken(self, gate_factory):
        """A taken candidate must advance the sequence, not collide."""
        gate, vault = gate_factory(schema="atom")
        first = gate.create_note(
            title="one", note_type="idea", pillar="Journal", body="x"
        )
        second = gate.create_note(
            title="two", note_type="idea", pillar="Journal", body="y"
        )
        assert first.path != second.path
        assert first.path in vault.store
        assert second.path in vault.store


class TestTapeRefusal:
    """The tape must REFUSE what it was never told — the pinned-fake property.

    A permissive fake answers any call with a canned success, which is how a
    suite ends up green about behaviour nobody recorded. These tests assert the
    inversion directly, so the property cannot rot silently.
    """

    def test_unrecorded_path_raises(self):
        from tests.substrate.rest import TapeRESTClient, UntapedCallError

        with pytest.raises(UntapedCallError):
            TapeRESTClient().get("/vault/Never/Recorded.md")

    def test_unrecorded_accept_header_raises(self):
        """Same path, different Accept — a different response, so a different case."""
        from tests.substrate.rest import TapeRESTClient, UntapedCallError

        client = TapeRESTClient()
        assert client.get("/vault/CLAUDE.md", accept=NOTE_JSON)["ok"] is True
        with pytest.raises(UntapedCallError):
            client.get("/vault/CLAUDE.md", accept="application/x-not-recorded")

    def test_recorded_call_replays_exactly(self):
        from tests.substrate.rest import TapeRESTClient

        result = TapeRESTClient().get("/vault/CLAUDE.md", accept=NOTE_JSON)
        assert result["ok"] is True
        assert result["data"]["path"] == "CLAUDE.md"

    def test_tape_carries_no_real_vault_content(self):
        """The tape is synthetic by design — vault-mcp is published to GitHub.

        A verbatim recording would commit real note paths, frontmatter and
        journal bodies to a public repo. This asserts the marker that says so
        is present, so a future re-record cannot quietly drop the rule.
        """
        import json

        from tests.substrate.rest import TAPE

        raw = json.loads(TAPE.read_text(encoding="utf-8"))
        assert "SYNTHETIC" in raw["content"]
        assert raw["recorded_from"].startswith("Obsidian Local REST API")
