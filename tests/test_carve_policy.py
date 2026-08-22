"""Scope-policy tests (C6) — the config-driven, fail-safe dissolvable cut."""

from __future__ import annotations

from vault_mcp.carve_policy import (
    DEFAULT_SCOPE_POLICY,
    OPEN_DECISIONS,
    ScopePolicy,
)

POLICY = DEFAULT_SCOPE_POLICY


def test_dissolvable_pillar_dissolves() -> None:
    d = POLICY.classify("Brain Soup/Some Note.md")
    assert d.dissolve is True
    assert d.pillar == "Brain Soup"
    assert d.reason == "dissolvable-pillar"


def test_governance_pillar_stays() -> None:
    d = POLICY.classify("System/Pantheon/WBS/Plan.md")
    assert d.dissolve is False
    assert d.reason == "protected-pillar"


def test_unlisted_pillar_defaults_to_stay() -> None:
    # Garden (voice-Canon), Artifacts, Atlas, etc. are unlisted → fail-safe STAY.
    for path in (
        "Garden/Canon/Voice.md",
        "Artifacts/Thing.md",
        "Atlas/Audits/Audit.md",
        "Outputs/Plans/Plan.md",
    ):
        d = POLICY.classify(path)
        assert d.dissolve is False, path
        assert d.reason == "unlisted-pillar-default-stay"


def test_non_markdown_stays() -> None:
    d = POLICY.classify("Brain Soup/attachment.png")
    assert d.dissolve is False
    assert d.reason == "not-markdown"


def test_root_level_file_has_no_pillar_and_stays() -> None:
    d = POLICY.classify("README.md")
    assert d.dissolve is False
    assert d.reason == "root-file-no-pillar"


def test_protect_beats_dissolve_on_overlap() -> None:
    # If a pillar is on BOTH lists, protect wins (fail-safe).
    policy = ScopePolicy(
        dissolve_pillars=frozenset({"System"}),
        protect_pillars=frozenset({"System"}),
    )
    d = policy.classify("System/Note.md")
    assert d.dissolve is False
    assert d.reason == "protected-pillar"


def test_backslash_paths_normalize() -> None:
    d = POLICY.classify("Brain Soup\\Nested\\Note.md")
    assert d.dissolve is True
    assert d.pillar == "Brain Soup"


def test_with_overrides_is_config() -> None:
    # The cut is CONFIG — a caller/env can override without touching code.
    custom = POLICY.with_overrides(
        dissolve_pillars=frozenset({"References"}),
        protect_pillars=frozenset({"System", "Garden"}),
    )
    assert custom.classify("References/Paper.md").dissolve is True
    assert custom.classify("Garden/Canon/Voice.md").dissolve is False
    assert (
        custom.classify("Brain Soup/Note.md").dissolve is False
    )  # no longer listed


def test_open_decisions_are_surfaced() -> None:
    # The unconfirmed cut + Obsidian disposition ride on the policy, loudly.
    assert POLICY.open_decisions == OPEN_DECISIONS
    assert any("SCOPE CUT UNCONFIRMED" in d for d in POLICY.open_decisions)
    assert any(
        "OBSIDIAN DISPOSITION UNDECIDED" in d for d in POLICY.open_decisions
    )
    assert POLICY.obsidian_disposition == "undecided"
