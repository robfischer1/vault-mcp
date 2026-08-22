"""Unit tests for vault_mcp.lint — the standalone Convention Linter.

Covers the Linter Core epic: collect-all findings (not first-raise), the
structured Finding shape, delta-aware severity (the un-stick-the-edit bug fix),
target-state evaluation for repair callers, and Gate integration proving an
edit to a note with a pre-existing invalid value is no longer blocked.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from vault_mcp.gate import ConventionGate
from vault_mcp.lint import (
    Code,
    LintCandidate,
    Linter,
    Severity,
)
from vault_mcp.provenance import Actor, WriteMode
from vault_mcp.schema import load_schema

VALID = ROOT / "tests" / "fixtures" / "schema" / "valid.schema.yml"


class FakeVault:
    """In-memory vault IO (read raises KeyError on a missing path)."""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    def create_note(self, path: str, content: str) -> None:
        self.store[path] = content

    def read_note(self, path: str) -> str:
        return self.store[path]

    def write_note(self, path: str, content: str) -> None:
        self.store[path] = content


def _linter(note_exists=None) -> Linter:
    return Linter(load_schema(VALID), note_exists)


def _fm(**over):
    base = {
        "title": "X",
        "created": "2026-05-30",
        "author_type": "ai",
        "author_level": "ai-assisted",
    }
    base.update(over)
    return base


def _candidate(**over) -> LintCandidate:
    base = {
        "frontmatter": _fm(),
        "body": "",
        "directory": "Knowledge/Notes",
        "filename": "X",
        "note_type": "note",
        "tags": [],
        "actor": Actor.AGENT,
        "mode": WriteMode.CREATE,
    }
    base.update(over)
    return LintCandidate(**base)


class TestCollectAll:
    def test_clean_candidate_ok(self):
        res = _linter().lint(_candidate())
        assert res.ok
        assert res.findings == []

    def test_multiple_faults_collected_in_one_pass(self):
        # bad tag + off-vocabulary status + bare placeholder body = 3 errors
        c = _candidate(
            tags=["topic/nope"],
            frontmatter=_fm(status="bogus"),
            body="see <Name> here",
        )
        res = _linter().lint(c)
        assert not res.ok
        codes = {f.code for f in res.errors}
        assert {Code.TAG, Code.FIELD, Code.BODY} <= codes
        assert len(res.errors) >= 3  # collect-all, not first-raise

    def test_finding_carries_field_and_serializes(self):
        res = _linter().lint(_candidate(tags=["topic/nope"]))
        tag = next(f for f in res.findings if f.code is Code.TAG)
        assert tag.field == "tags"
        assert tag.severity is Severity.ERROR
        d = tag.to_dict()
        assert d["code"] == "tag"
        assert d["severity"] == "error"
        assert "topic/nope" in d["message"]


class TestDeltaAwareSeverity:
    def test_untouched_preexisting_fault_is_warning(self):
        # update touches only 'status'; the note carries a pre-existing bad tag,
        # so the tag fault is drift -> WARNING, and the result does not block.
        c = _candidate(
            tags=["topic/nope"],
            frontmatter=_fm(status="Active"),
            touched_fields={"status"},
        )
        res = _linter().lint(c)
        assert res.ok
        assert any(
            f.code is Code.TAG and f.severity is Severity.WARNING
            for f in res.warnings
        )

    def test_touched_fault_stays_error(self):
        c = _candidate(tags=["topic/nope"], touched_fields={"tags"})
        res = _linter().lint(c)
        assert not res.ok
        assert any(
            f.code is Code.TAG and f.severity is Severity.ERROR
            for f in res.errors
        )

    def test_structural_fault_not_downgraded_on_update(self):
        # protection is structural (field=None) -> stays ERROR even when untouched
        c = _candidate(directory="Artifacts", touched_fields={"status"})
        res = _linter().lint(c)
        assert not res.ok
        assert any(
            f.code is Code.PROTECTION and f.severity is Severity.ERROR
            for f in res.errors
        )


class TestTargetStateEvaluation:
    def test_corrected_candidate_passes(self):
        c = _candidate(
            tags=["topic/ai"],
            frontmatter=_fm(status="Active"),
            touched_fields={"tags", "status"},
        )
        assert _linter().lint(c).ok


class TestLinkResolution:
    def test_unresolved_link_flagged(self):
        c = _candidate(frontmatter=_fm(prev="Missing"))
        res = _linter(note_exists=lambda _t: False).lint(c)
        assert any(f.code is Code.LINK for f in res.errors)

    def test_resolved_link_passes(self):
        c = _candidate(frontmatter=_fm(prev="Exists"))
        res = _linter(note_exists=lambda _t: True).lint(c)
        assert not any(f.code is Code.LINK for f in res.findings)


class TestGateIntegrationBugFix:
    """The motivating bug: an edit to a note with a pre-existing invalid value
    must not be blocked by that pre-existing value."""

    def _gate(self) -> tuple[ConventionGate, FakeVault]:
        vault = FakeVault()
        return ConventionGate(load_schema(VALID), vault), vault

    def test_body_edit_on_note_with_bad_preexisting_link_succeeds(self):
        gate, vault = self._gate()
        vault.store["Knowledge/Notes/n.md"] = (
            "---\ntitle: n\ncreated: 2026-05-30\nauthor_type: ai\n"
            "author_level: ai-assisted\nprev: Ghost\n---\n\nold body\n"
        )
        result = gate.update_note("Knowledge/Notes/n.md", body="new body")
        assert "new body" in vault.store["Knowledge/Notes/n.md"]
        assert any(
            "prev" in w for w in result.warnings
        )  # surfaced, not blocked

    def test_new_bad_value_on_update_still_blocks(self):
        gate, vault = self._gate()
        vault.store["Knowledge/Notes/n.md"] = (
            "---\ntitle: n\ncreated: 2026-05-30\nauthor_type: ai\n"
            "author_level: ai-assisted\n---\n\nbody\n"
        )
        import pytest

        from vault_mcp.gate import TagError

        with pytest.raises(TagError):
            gate.update_note("Knowledge/Notes/n.md", tags=["topic/nope"])
