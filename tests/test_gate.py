"""Unit tests for vault_mcp.gate — the Convention Gate write API.

Covers Convention Gate (#53): note creation (frontmatter generation, routing,
write, success contract), write validation & rejection, and write-protection
enforcement. The Obsidian writer is mocked (Constitution III: never a live
instance).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from vault_mcp.gate import (  # noqa: E402
    BodyError,
    ConventionGate,
    FieldError,
    FilenameError,
    LinkError,
    ProtectionError,
    TagError,
    WriteModeError,
)
from vault_mcp.provenance import Actor, Provenance, WriteMode  # noqa: E402
from vault_mcp.schema import load_schema  # noqa: E402

VALID = ROOT / "tests" / "fixtures" / "schema" / "valid.schema.yml"
NAMED = ROOT / "tests" / "fixtures" / "schema" / "named.schema.yml"


class FakeVault:
    """In-memory vault IO; captures create calls instead of touching Obsidian."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.store: dict[str, str] = {}
        self.deleted: list[str] = []

    def create_note(self, path: str, content: str) -> None:
        self.calls.append((path, content))
        self.store[path] = content

    def read_note(self, path: str) -> str:
        return self.store[path]

    def write_note(self, path: str, content: str) -> None:
        self.store[path] = content

    def delete_note(self, path: str) -> None:
        self.deleted.append(path)
        self.store.pop(path, None)


def _gate(diff_sink=None) -> tuple[ConventionGate, FakeVault]:
    vault = FakeVault()
    return ConventionGate(load_schema(VALID), vault, diff_sink=diff_sink), vault


class TestCreateNote:
    def test_generates_required_frontmatter(self):
        gate, writer = _gate()
        result = gate.create_note(
            title="My Idea",
            note_type="note",
            pillar="Knowledge",
            body="An idea.",
            tags=["topic/ai"],
            created="2026-05-30",
        )
        for required in ("title", "created", "author_type", "author_level"):
            assert required in result.frontmatter
        assert "provenance" not in result.frontmatter  # retired single-axis key
        assert result.frontmatter["title"] == "My Idea"
        assert len(writer.calls) == 1

    def test_routes_to_resolved_directory(self):
        gate, writer = _gate()
        result = gate.create_note(
            title="My Idea", note_type="note", pillar="Knowledge", created="2026-05-30"
        )
        assert result.path == "Knowledge/Notes/My Idea.md"
        assert writer.calls[0][0] == "Knowledge/Notes/My Idea.md"

    def test_success_contract_returns_path_fm_provenance(self):
        gate, _ = _gate()
        result = gate.create_note(
            title="My Idea", note_type="note", pillar="Knowledge", created="2026-05-30"
        )
        assert result.path
        assert isinstance(result.frontmatter, dict)
        assert result.provenance == Provenance.AI_ASSISTED

    def test_agent_create_stamps_ai_assisted(self):
        gate, _ = _gate()
        result = gate.create_note(
            title="X",
            note_type="note",
            pillar="Knowledge",
            actor=Actor.AGENT,
            created="2026-05-30",
        )
        assert result.frontmatter["author_level"] == "ai-assisted"
        assert result.frontmatter["author_type"] == "ai"

    def test_human_create_stamps_human(self):
        gate, _ = _gate()
        result = gate.create_note(
            title="X",
            note_type="note",
            pillar="Knowledge",
            actor=Actor.HUMAN,
            created="2026-05-30",
        )
        assert result.frontmatter["author_level"] == "human"
        assert result.frontmatter["author_type"] == "human"


class TestValidation:
    def test_reject_unknown_tag(self):
        gate, writer = _gate()
        with pytest.raises(TagError) as exc:
            gate.create_note(
                title="X",
                note_type="note",
                pillar="Knowledge",
                tags=["topic/nope"],
                created="2026-05-30",
            )
        assert "topic/nope" in str(exc.value)
        assert len(writer.calls) == 0  # no file written

    def test_unknown_tag_suggests_nearest(self):
        gate, _ = _gate()
        with pytest.raises(TagError) as exc:
            gate.create_note(
                title="X",
                note_type="note",
                pillar="Knowledge",
                tags=["status/activ"],
                created="2026-05-30",
            )
        assert "status/active" in str(exc.value)

    def test_reject_missing_required_title(self):
        gate, writer = _gate()
        with pytest.raises(FieldError):
            gate.create_note(title="", note_type="note", pillar="Knowledge", created="2026-05-30")
        assert len(writer.calls) == 0


class TestTypeEnforcement:
    def test_missing_required_field_rejected(self):
        gate, writer = _gate()
        with pytest.raises(FieldError) as exc:
            gate.create_note(
                title="G", note_type="Gadget", directory="Knowledge", created="2026-05-30"
            )
        assert "Gadget" in str(exc.value) and "serial" in str(exc.value)
        assert len(writer.calls) == 0

    def test_required_field_present_passes(self):
        gate, _ = _gate()
        result = gate.create_note(
            title="G",
            note_type="Gadget",
            directory="Knowledge",
            extra_fields={"serial": "S1"},
            created="2026-05-30",
        )
        assert result.frontmatter["serial"] == "S1"

    def test_off_vocabulary_value_rejected_cites_type_and_field(self):
        gate, writer = _gate()
        with pytest.raises(FieldError) as exc:
            gate.create_note(
                title="G",
                note_type="Gadget",
                directory="Knowledge",
                extra_fields={"serial": "S1", "condition": "Mint"},
                created="2026-05-30",
            )
        msg = str(exc.value)
        assert "Gadget" in msg and "condition" in msg and "Mint" in msg
        assert len(writer.calls) == 0

    def test_in_vocabulary_value_passes(self):
        gate, _ = _gate()
        result = gate.create_note(
            title="G",
            note_type="Gadget",
            directory="Knowledge",
            extra_fields={"serial": "S1", "condition": "Used"},
            created="2026-05-30",
        )
        assert result.frontmatter["condition"] == "Used"

    def test_bad_format_rejected(self):
        gate, _ = _gate()
        with pytest.raises(FieldError) as exc:
            gate.create_note(
                title="P",
                note_type="Spot",
                directory="Knowledge",
                extra_fields={"coords": "not-coords"},
                created="2026-05-30",
            )
        assert "Spot" in str(exc.value) and "geo" in str(exc.value)

    def test_good_format_passes(self):
        gate, _ = _gate()
        result = gate.create_note(
            title="P",
            note_type="Spot",
            directory="Knowledge",
            extra_fields={"coords": "40.7,-74.0"},
            created="2026-05-30",
        )
        assert result.frontmatter["coords"] == "40.7,-74.0"

    def test_status_repaired_on_create(self):
        gate, _ = _gate()
        result = gate.create_note(
            title="X",
            note_type="note",
            pillar="Knowledge",
            extra_fields={"status": "Archive"},
            created="2026-05-30",
        )
        assert result.frontmatter["status"] == "Archived"

    def test_invalid_status_rejected(self):
        gate, _ = _gate()
        with pytest.raises(FieldError) as exc:
            gate.create_note(
                title="X",
                note_type="note",
                pillar="Knowledge",
                extra_fields={"status": "Bogus"},
                created="2026-05-30",
            )
        assert "Bogus" in str(exc.value)

    def test_unknown_type_passes_through(self):
        gate, _ = _gate()
        # note_type with no TypeConfig carries no per-type rules
        result = gate.create_note(
            title="X", note_type="note", pillar="Knowledge", created="2026-05-30"
        )
        assert result.path == "Knowledge/Notes/X.md"


class TestPillarAutoStamp:
    def test_pillar_defaults_stamped(self):
        gate, _ = _gate()
        result = gate.create_note(
            title="X", note_type="note", pillar="Knowledge", created="2026-05-30"
        )
        assert result.frontmatter["nn_color"] == "#8caaee"
        assert result.frontmatter["nn_icon"] == "book"

    def test_caller_value_wins_over_default(self):
        gate, _ = _gate()
        result = gate.create_note(
            title="X",
            note_type="note",
            pillar="Knowledge",
            extra_fields={"nn_color": "#000000"},
            created="2026-05-30",
        )
        assert result.frontmatter["nn_color"] == "#000000"
        assert result.frontmatter["nn_icon"] == "book"  # untouched default still stamped


class TestFrontmatterStamping:
    def test_identifier_autogen(self):
        gate, _ = _gate()
        result = gate.create_note(
            title="My Big Idea", note_type="note", pillar="Knowledge", created="2026-05-30"
        )
        assert result.frontmatter["identifier"] == "my-big-idea"

    def test_identifier_caller_override(self):
        gate, _ = _gate()
        result = gate.create_note(
            title="My Big Idea",
            note_type="note",
            pillar="Knowledge",
            extra_fields={"identifier": "custom-id"},
            created="2026-05-30",
        )
        assert result.frontmatter["identifier"] == "custom-id"

    def test_status_defaults_pending(self):
        gate, _ = _gate()
        result = gate.create_note(
            title="X", note_type="note", pillar="Knowledge", created="2026-05-30"
        )
        assert result.frontmatter["status"] == "Pending"

    def test_invalid_status_suggests_nearest(self):
        gate, _ = _gate()
        with pytest.raises(FieldError) as exc:
            gate.create_note(
                title="X",
                note_type="note",
                pillar="Knowledge",
                extra_fields={"status": "Activ"},
                created="2026-05-30",
            )
        assert "did you mean 'Active'" in str(exc.value)

    def test_note_type_title_cased(self):
        gate, _ = _gate()
        result = gate.create_note(
            title="X", note_type="note", directory="Knowledge", created="2026-05-30"
        )
        assert result.frontmatter["note_type"] == "Note"

    def test_note_type_preserves_existing_caps(self):
        gate, _ = _gate()
        result = gate.create_note(
            title="X", note_type="TVSeries", directory="Knowledge", created="2026-05-30"
        )
        assert result.frontmatter["note_type"] == "TVSeries"

    def test_at_key_double_quoted_in_render(self):
        gate, vault = _gate()
        result = gate.create_note(
            title="X",
            note_type="note",
            pillar="Knowledge",
            extra_fields={"@type": "Person"},
            created="2026-05-30",
        )
        content = vault.store[result.path]
        assert '"@type":' in content

    def test_forensic_keys_pass_through(self):
        gate, _ = _gate()
        result = gate.create_note(
            title="X",
            note_type="note",
            pillar="Knowledge",
            extra_fields={
                "origin_date": "2026-01-01",
                "date_precision": "day",
                "source": "import",
                "predicate": "knows",
            },
            created="2026-05-30",
        )
        for key in ("origin_date", "date_precision", "source", "predicate"):
            assert key in result.frontmatter


class TestNonPillarGuard:
    def test_attachments_dir_rejected(self):
        gate, writer = _gate()
        with pytest.raises(ProtectionError) as exc:
            gate.create_note(
                title="X", note_type="note", directory="attachments/sub", created="2026-05-30"
            )
        assert "non-pillar" in str(exc.value)
        assert len(writer.calls) == 0

    def test_dotfile_dir_rejected(self):
        gate, _ = _gate()
        with pytest.raises(ProtectionError):
            gate.create_note(
                title="X", note_type="note", directory=".obsidian", created="2026-05-30"
            )

    def test_pillar_dir_allowed(self):
        gate, _ = _gate()
        result = gate.create_note(
            title="X", note_type="note", pillar="Knowledge", created="2026-05-30"
        )
        assert result.path.startswith("Knowledge/")


class TestFilenameConventions:
    def test_atom_slug_filename(self):
        gate, _ = _gate()
        result = gate.create_note(
            title="A fleeting idea",
            note_type="atom",
            directory="Knowledge",
            created="2026-05-30",
        )
        assert result.path == "Knowledge/2026-05-30-atom.0.md"

    def test_atom_slug_increments(self):
        gate, vault = _gate()
        first = gate.create_note(
            title="one", note_type="atom", directory="Knowledge", created="2026-05-30"
        )
        second = gate.create_note(
            title="two", note_type="atom", directory="Knowledge", created="2026-05-30"
        )
        assert first.path.endswith("-atom.0.md")
        assert second.path.endswith("-atom.1.md")

    def test_numeric_folder_prefix_rejected(self):
        gate, _ = _gate()
        with pytest.raises(FilenameError) as exc:
            gate.create_note(
                title="X", note_type="note", directory="01 Foo", created="2026-05-30"
            )
        assert "numeric folder prefix" in str(exc.value)

    def test_pillar_dash_prefix_rejected(self):
        gate, _ = _gate()
        with pytest.raises(FilenameError):
            gate.create_note(
                title="Knowledge -- My Note",
                note_type="note",
                directory="Knowledge",
                created="2026-05-30",
            )


class TestProvenanceBodyProtection:
    def _seed(self, vault, path, author_type, note_type="note", at_type=None):
        at = f'"@type": {at_type}\n' if at_type else ""
        vault.store[path] = (
            f"---\ntitle: t\nnote_type: {note_type}\nauthor_type: {author_type}\n"
            f"author_level: human\n{at}---\n\noriginal\n"
        )

    def test_agent_body_edit_human_note_rejected(self):
        gate, vault = _gate()
        self._seed(vault, "Knowledge/Notes/h.md", "human")
        with pytest.raises(ProtectionError) as exc:
            gate.update_note("Knowledge/Notes/h.md", body="rewritten", actor=Actor.AGENT)
        assert "metadata" in str(exc.value)

    def test_agent_metadata_edit_human_note_allowed(self):
        gate, vault = _gate()
        self._seed(vault, "Knowledge/Notes/h.md", "human")
        result = gate.update_note(
            "Knowledge/Notes/h.md", fields={"status": "Active"}, actor=Actor.AGENT
        )
        assert result.frontmatter["status"] == "Active"

    def test_agent_body_edit_external_note_rejected(self):
        gate, vault = _gate()
        self._seed(vault, "Knowledge/Notes/e.md", "external")
        with pytest.raises(ProtectionError):
            gate.update_note("Knowledge/Notes/e.md", body="rewritten", actor=Actor.AGENT)

    def test_outputs_article_exception_allows_ai_body(self):
        gate, vault = _gate()
        self._seed(vault, "Outputs/Articles/a.md", "human", note_type="Article")
        result = gate.update_note("Outputs/Articles/a.md", body="new draft", actor=Actor.AGENT)
        assert "new draft" in vault.store["Outputs/Articles/a.md"]
        assert result is not None

    def test_human_body_edit_human_note_allowed(self):
        gate, vault = _gate()
        self._seed(vault, "Knowledge/Notes/h.md", "human")
        gate.update_note("Knowledge/Notes/h.md", body="rewritten", actor=Actor.HUMAN)
        assert "rewritten" in vault.store["Knowledge/Notes/h.md"]


class TestWriteModeEnforcement:
    def test_agent_create_materialize_only_rejected(self):
        gate, writer = _gate()
        with pytest.raises(WriteModeError) as exc:
            gate.create_note(
                title="H", note_type="Dossier", directory="Knowledge", created="2026-05-30"
            )
        assert "materialize-only" in str(exc.value)
        assert len(writer.calls) == 0

    def test_compute_mode_materialize_only_allowed(self):
        gate, _ = _gate()
        result = gate.create_note(
            title="H",
            note_type="Dossier",
            directory="Knowledge",
            mode=WriteMode.COMPUTE,
            created="2026-05-30",
        )
        assert result.path.endswith("H.md")

    def test_pure_db_type_rejected(self):
        gate, writer = _gate()
        with pytest.raises(WriteModeError) as exc:
            gate.create_note(
                title="d", note_type="signal", directory="Knowledge", created="2026-05-30"
            )
        assert "pure-DB" in str(exc.value)
        assert len(writer.calls) == 0

    def test_pure_db_rejected_even_in_compute(self):
        gate, _ = _gate()
        with pytest.raises(WriteModeError):
            gate.create_note(
                title="d",
                note_type="signal",
                directory="Knowledge",
                mode=WriteMode.COMPUTE,
                created="2026-05-30",
            )


class TestLinkValidation:
    def test_unresolvable_prev_rejected(self):
        gate, _ = _gate()
        with pytest.raises(LinkError) as exc:
            gate.create_note(
                title="X",
                note_type="note",
                pillar="Knowledge",
                extra_fields={"prev": "[[Nonexistent]]"},
                created="2026-05-30",
            )
        assert "prev" in str(exc.value)

    def test_resolvable_next_passes(self):
        gate, vault = _gate()
        vault.store["Knowledge/Notes/Target.md"] = "---\ntitle: t\n---\n\nbody\n"
        result = gate.create_note(
            title="X",
            note_type="note",
            pillar="Knowledge",
            extra_fields={"next": "Knowledge/Notes/Target"},
            created="2026-05-30",
        )
        assert result.frontmatter["next"] == "Knowledge/Notes/Target"

    def test_missing_isbasedon_warns_in_records(self):
        gate, vault = _gate()
        # update path (Records create is protected) to exercise the advisory
        vault.store["Records/r.md"] = (
            "---\ntitle: r\nauthor_type: human\nauthor_level: human\nup: '[[Records]]'\n---\n\nb\n"
        )
        result = gate.update_note("Records/r.md", fields={"status": "Active"})
        assert any("isBasedOn" in w for w in result.warnings)

    def test_missing_up_warns(self):
        gate, _ = _gate()
        result = gate.create_note(
            title="Child", note_type="note", pillar="Knowledge", created="2026-05-30"
        )
        assert any("up:" in w for w in result.warnings)

    def test_folder_note_no_up_warning(self):
        gate, _ = _gate()
        result = gate.create_note(
            title="Knowledge", note_type="note", directory="Knowledge", created="2026-05-30"
        )
        assert not any("up:" in w for w in result.warnings)


class TestTagEnhancements:
    def test_reserved_tag_warns_not_rejects(self):
        gate, vault = _gate()
        result = gate.create_note(
            title="X",
            note_type="note",
            pillar="Knowledge",
            tags=["todo"],
            actor=Actor.AGENT,
            created="2026-05-30",
        )
        assert any("todo" in w for w in result.warnings)
        assert len(vault.calls) == 1  # write still happened

    def test_reserved_tag_no_warn_for_human(self):
        gate, _ = _gate()
        result = gate.create_note(
            title="X",
            note_type="note",
            pillar="Knowledge",
            tags=["todo"],
            actor=Actor.HUMAN,
            created="2026-05-30",
        )
        assert not any("reserved" in w for w in result.warnings)

    def test_inline_tags_escaped_in_references(self):
        gate, vault = _gate()
        result = gate.create_note(
            title="imp",
            note_type="note",
            directory="References/Imports",
            body="I like #cooking and #fitness here.",
            created="2026-05-30",
        )
        content = vault.store[result.path]
        assert "\\#cooking" in content and "\\#fitness" in content

    def test_inline_tag_exemptions(self):
        gate, vault = _gate()
        result = gate.create_note(
            title="imp2",
            note_type="note",
            directory="References/Imports",
            body="done #activity/processed see http://x.com#frag and `#code`",
            created="2026-05-30",
        )
        content = vault.store[result.path]
        assert "#activity/processed" in content and "\\#activity/processed" not in content
        assert "x.com#frag" in content  # URL fragment not escaped

    def test_no_escaping_outside_imported_dirs(self):
        gate, vault = _gate()
        result = gate.create_note(
            title="k",
            note_type="note",
            pillar="Knowledge",
            body="A #realtag stays bare in Knowledge.",
            created="2026-05-30",
        )
        content = vault.store[result.path]
        assert "#realtag" in content and "\\#realtag" not in content


class TestBodyValidation:
    def test_angle_bracket_placeholder_rejected(self):
        gate, _ = _gate()
        with pytest.raises(BodyError) as exc:
            gate.create_note(
                title="X",
                note_type="note",
                pillar="Knowledge",
                body="Hello <Name>, welcome.",
                created="2026-05-30",
            )
        assert "<Name>" in str(exc.value) and "{Name}" in str(exc.value)

    def test_angle_bracket_inside_code_allowed(self):
        gate, _ = _gate()
        result = gate.create_note(
            title="X",
            note_type="note",
            pillar="Knowledge",
            body="Use the `<Name>` token, or:\n```\n<topic>\n```\n",
            created="2026-05-30",
        )
        assert result.path.endswith("X.md")

    def test_consumed_media_body_must_be_empty(self):
        gate, _ = _gate()
        with pytest.raises(BodyError) as exc:
            gate.create_note(
                title="Dune",
                note_type="Stub",
                directory="Knowledge",
                body="A novel about spice.",
                created="2026-05-30",
            )
        assert "body must be empty" in str(exc.value)

    def test_stub_empty_body_allowed(self):
        gate, _ = _gate()
        result = gate.create_note(
            title="Dune", note_type="Stub", directory="Knowledge", body="  \n", created="2026-05-30"
        )
        assert result.path.endswith("Dune.md")

    def test_template_literal_fence_rejected(self):
        gate, _ = _gate()
        with pytest.raises(BodyError) as exc:
            gate.create_note(
                title="tmpl",
                note_type="note",
                directory="System/Templates",
                body="---\nfoo: bar\n---\nbody\n",
                created="2026-05-30",
            )
        assert "Templater fence" in str(exc.value)


class TestDeprecatedKeyMigration:
    def test_rename_preserves_value(self):
        gate, _ = _gate()
        result = gate.create_note(
            title="X",
            note_type="note",
            pillar="Knowledge",
            extra_fields={"id": "abc-123", "published": "2020-01-01"},
            created="2026-05-30",
        )
        fm = result.frontmatter
        assert fm["identifier"] == "abc-123"  # id -> identifier (before autogen)
        assert fm["datePublished"] == "2020-01-01"  # published -> datePublished
        assert "id" not in fm
        assert "published" not in fm

    def test_dead_keys_dropped_project_preserved(self):
        gate, _ = _gate()
        result = gate.create_note(
            title="X",
            note_type="note",
            pillar="Knowledge",
            extra_fields={"sub_type": "junk", "legacy_type": "old", "project": "keepme"},
            created="2026-05-30",
        )
        fm = result.frontmatter
        assert "sub_type" not in fm
        assert "legacy_type" not in fm
        assert fm["project"] == "keepme"  # project is NOT deprecated

    def test_migration_on_update(self):
        gate, vault = _gate()
        vault.store["Knowledge/Notes/n.md"] = (
            "---\ntitle: n\nauthor_type: human\nauthor_level: human\nid: old-id\nsub_type: x\n---\n\nbody\n"
        )
        result = gate.update_note("Knowledge/Notes/n.md", fields={"status": "Active"})
        fm = result.frontmatter
        assert fm["identifier"] == "old-id"
        assert "id" not in fm
        assert "sub_type" not in fm


class TestProvenanceThreeProperty:
    def test_ai_model_stamped_for_agent(self):
        gate, _ = _gate()
        result = gate.create_note(
            title="X",
            note_type="note",
            pillar="Knowledge",
            actor=Actor.AGENT,
            ai_model="claude-opus-4-8",
            created="2026-05-30",
        )
        assert result.frontmatter["ai_model"] == "claude-opus-4-8"
        assert result.ai_model == "claude-opus-4-8"

    def test_ai_model_omitted_for_human(self):
        gate, _ = _gate()
        result = gate.create_note(
            title="X",
            note_type="note",
            pillar="Knowledge",
            actor=Actor.HUMAN,
            ai_model="claude-opus-4-8",  # ignored for a human write
            created="2026-05-30",
        )
        assert "ai_model" not in result.frontmatter

    def test_declared_external_author_type(self):
        gate, _ = _gate()
        result = gate.create_note(
            title="X",
            note_type="note",
            pillar="Knowledge",
            author_type="external",
            created="2026-05-30",
        )
        assert result.frontmatter["author_type"] == "external"

    def test_to_dict_carries_three_properties(self):
        gate, _ = _gate()
        result = gate.create_note(
            title="X", note_type="note", pillar="Knowledge", created="2026-05-30"
        )
        d = result.to_dict()
        assert d["author_type"] == "ai"
        assert d["author_level"] == "ai-assisted"
        assert "ai_model" in d
        assert "provenance" not in d

    def test_legacy_provenance_note_migrated_on_update(self):
        gate, vault = _gate()
        # a note that predates the 3-property model carries only `provenance:`
        vault.store["Knowledge/Notes/old.md"] = (
            "---\ntitle: old\nprovenance: human\n---\n\nbody\n"
        )
        result = gate.update_note(
            "Knowledge/Notes/old.md", fields={"status": "Active"}, actor=Actor.AGENT
        )
        assert "provenance" not in result.frontmatter  # legacy key retired
        assert result.frontmatter["author_level"] == "ai-assisted"  # human + agent edit
        assert result.frontmatter["author_type"] == "ai"  # no-downgrade


class TestProtection:
    def test_create_into_body_immutable_rejected(self):
        gate, writer = _gate()
        with pytest.raises(ProtectionError) as exc:
            gate.create_note(title="R", pillar="Records", created="2026-05-30")
        assert "Records are imported primary-source data" in str(exc.value)
        assert len(writer.calls) == 0

    def test_fully_immutable_rejects_all(self):
        gate, _ = _gate()
        with pytest.raises(ProtectionError):
            gate.check_protection("Artifacts", Actor.AGENT, WriteMode.CREATE)

    def test_compute_only_rejects_session_allows_compute(self):
        gate, _ = _gate()
        with pytest.raises(ProtectionError):
            gate.check_protection("Atlas", Actor.AGENT, WriteMode.CREATE)
        # compute mode is permitted
        gate.check_protection("Atlas", Actor.AGENT, WriteMode.COMPUTE)

    def test_voice_only_rejects_agent_allows_human(self):
        gate, _ = _gate()
        with pytest.raises(ProtectionError):
            gate.check_protection("Outputs/Tao", Actor.AGENT, WriteMode.CREATE)
        gate.check_protection("Outputs/Tao", Actor.HUMAN, WriteMode.CREATE)

    def test_unprotected_directory_passes(self):
        gate, _ = _gate()
        gate.check_protection("Knowledge/Notes", Actor.AGENT, WriteMode.CREATE)


class TestUpdateNote:
    def _seed(self, gate, vault) -> str:
        result = gate.create_note(
            title="Idea",
            note_type="note",
            pillar="Knowledge",
            body="Original body.",
            tags=["topic/ai"],
            created="2026-05-30",
        )
        return result.path

    def test_changes_only_requested_fields(self):
        gate, vault = _gate()
        path = self._seed(gate, vault)
        result = gate.update_note(path, fields={"status": "Completed"})
        assert result.frontmatter["status"] == "Completed"
        assert result.frontmatter["title"] == "Idea"  # untouched field preserved
        assert "Original body." in vault.store[path]  # body preserved

    def test_metadata_update_on_records_allowed(self):
        gate, vault = _gate()
        vault.store["Records/r.md"] = "---\ntitle: r\nprovenance: human\n---\n\nbody\n"
        # metadata-only (no body) update is permitted on a body-immutable dir
        gate.update_note("Records/r.md", fields={"status": "Active"})
        assert "status: Active" in vault.store["Records/r.md"]

    def test_body_update_on_records_rejected(self):
        gate, vault = _gate()
        vault.store["Records/r.md"] = "---\ntitle: r\nprovenance: human\n---\n\nbody\n"
        with pytest.raises(ProtectionError):
            gate.update_note("Records/r.md", body="rewritten")

    def test_human_edit_transitions_ai_assisted_to_human_edited(self):
        gate, vault = _gate()
        path = self._seed(gate, vault)  # created by agent -> ai-assisted
        result = gate.update_note(path, fields={"status": "Completed"}, actor=Actor.HUMAN)
        assert result.provenance is Provenance.HUMAN_EDITED


class TestSchemaDrivenFrontmatter:
    def _named_gate(self) -> tuple[ConventionGate, FakeVault]:
        vault = FakeVault()
        return ConventionGate(load_schema(NAMED), vault), vault

    def test_emits_configured_label_and_updated(self):
        gate, _ = self._named_gate()
        result = gate.create_note(
            title="My Note", note_type="note", pillar="Knowledge", created="2026-05-30"
        )
        fm = result.frontmatter
        assert fm["name"] == "My Note"  # label_field, not "title"
        assert fm["created"] == "2026-05-30"
        assert fm["updated"] == "2026-05-30"  # auto-stamped on create
        assert "title" not in fm

    def test_update_restamps_updated(self):
        gate, vault = self._named_gate()
        path = gate.create_note(
            title="My Note", note_type="note", pillar="Knowledge", created="2026-05-30"
        ).path
        result = gate.update_note(path, fields={"status": "Active"}, actor=Actor.HUMAN)
        assert result.frontmatter["name"] == "My Note"
        assert result.frontmatter["status"] == "Active"
        assert "updated" in result.frontmatter  # re-stamped


class TestObservability:
    def test_diff_emitted_on_create(self):
        records: list[dict] = []
        gate, _ = _gate(diff_sink=records.append)
        gate.create_note(title="X", note_type="note", pillar="Knowledge", created="2026-05-30")
        assert len(records) == 1
        assert records[0]["op"] == "create"
        assert records[0]["path"] == "Knowledge/Notes/X.md"
        assert records[0]["provenance"] == "ai-assisted"

    def test_emission_failure_does_not_block_write(self):
        def boom(_record: dict) -> None:
            raise RuntimeError("sink down")

        gate, vault = _gate(diff_sink=boom)
        result = gate.create_note(
            title="X", note_type="note", pillar="Knowledge", created="2026-05-30"
        )
        # write still succeeded despite the sink raising
        assert len(vault.calls) == 1
        assert result.path == "Knowledge/Notes/X.md"


class TestWriteNote:
    def test_creates_when_absent(self):
        gate, vault = _gate()
        result = gate.write_note(
            title="W", note_type="note", pillar="Knowledge", body="hi", created="2026-05-30"
        )
        assert result.created is True
        assert result.path == "Knowledge/Notes/W.md"
        assert len(vault.calls) == 1

    def test_updates_when_present(self):
        gate, vault = _gate()
        gate.write_note(title="W", note_type="note", pillar="Knowledge", created="2026-05-30")
        result = gate.write_note(
            title="W", note_type="note", pillar="Knowledge", fields={"status": "Active"}
        )
        assert result.created is False
        assert result.frontmatter["status"] == "Active"

    def test_mode_create_refuses_existing(self):
        gate, _ = _gate()
        gate.write_note(title="W", note_type="note", pillar="Knowledge", created="2026-05-30")
        with pytest.raises(FieldError):
            gate.write_note(title="W", note_type="note", pillar="Knowledge", mode="create")

    def test_mode_update_refuses_missing(self):
        gate, _ = _gate()
        with pytest.raises(FieldError):
            gate.write_note(title="Nope", note_type="note", pillar="Knowledge", mode="update")

    def test_update_leaves_body_untouched_when_null(self):
        gate, vault = _gate()
        gate.write_note(
            title="W", note_type="note", pillar="Knowledge", body="original", created="2026-05-30"
        )
        gate.write_note(title="W", note_type="note", pillar="Knowledge", fields={"status": "Active"})
        assert "original" in vault.store["Knowledge/Notes/W.md"]


class TestDelete:
    def _seed(self, gate) -> str:
        return gate.create_note(
            title="Doomed", note_type="note", pillar="Knowledge", created="2026-05-30"
        ).path

    def test_delete_removes_and_records(self):
        gate, vault = _gate()
        path = self._seed(gate)
        result = gate.delete(path)
        assert result["ok"] is True and result["deleted"] is True
        assert path not in vault.store
        assert path in vault.deleted

    def test_delete_missing_rejected(self):
        gate, _ = _gate()
        with pytest.raises(FieldError):
            gate.delete("Knowledge/Notes/ghost.md")

    def test_delete_blocked_on_voice_only_for_agent(self):
        gate, vault = _gate()
        vault.store["Outputs/Tao/t.md"] = "---\ntitle: t\n---\n\nbody\n"
        with pytest.raises(ProtectionError):
            gate.delete("Outputs/Tao/t.md", actor=Actor.AGENT)
        assert "Outputs/Tao/t.md" in vault.store  # not trashed

    def test_delete_allowed_on_voice_only_for_human(self):
        gate, vault = _gate()
        vault.store["Outputs/Tao/t.md"] = "---\ntitle: t\n---\n\nbody\n"
        result = gate.delete("Outputs/Tao/t.md", actor=Actor.HUMAN)
        assert result["deleted"] is True
        assert "Outputs/Tao/t.md" not in vault.store


class TestMoveNote:
    def _seed(self, gate, vault, title="Movable", path="Knowledge/Notes/Movable.md"):
        gate.create_note(
            title=title, note_type="note", pillar="Knowledge", created="2026-05-30"
        )
        return path

    def test_move_succeeds(self):
        gate, vault = _gate()
        src = self._seed(gate, vault)
        result = gate.move_note(src, "Projects/Movable.md")
        assert result["ok"] is True and result["moved"] is True
        assert src not in vault.store
        assert "Projects/Movable.md" in vault.store

    def test_move_preserves_content(self):
        gate, vault = _gate()
        src = self._seed(gate, vault)
        original = vault.store[src]
        gate.move_note(src, "Projects/Movable.md")
        assert vault.store["Projects/Movable.md"] == original

    def test_move_source_missing_rejected(self):
        gate, _ = _gate()
        with pytest.raises(FieldError):
            gate.move_note("Knowledge/Notes/ghost.md", "Projects/ghost.md")

    def test_move_destination_exists_rejected(self):
        gate, vault = _gate()
        src = self._seed(gate, vault)
        gate.create_note(
            title="Blocker", note_type="project", pillar="Projects", created="2026-05-30"
        )
        with pytest.raises(FieldError, match="already exists"):
            gate.move_note(src, "Projects/Blocker.md")

    def test_move_blocked_by_src_protection(self):
        gate, vault = _gate()
        vault.store["Records/old.md"] = "---\ntitle: old\n---\n\nbody\n"
        with pytest.raises(ProtectionError):
            gate.move_note("Records/old.md", "Knowledge/Notes/old.md")

    def test_move_blocked_by_dst_protection(self):
        gate, vault = _gate()
        src = self._seed(gate, vault)
        with pytest.raises(ProtectionError):
            gate.move_note(src, "Artifacts/Movable.md")
