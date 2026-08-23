"""Tests for vault_mcp.bases — parser, evaluator, writer, validator."""

from __future__ import annotations

import contextlib
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import pytest

from vault_mcp.bases import (
    Base,
    FilterNode,
    Formula,
    FormulaEvaluator,
    FormulaTimeoutError,
    Summary,
    ViewConfig,
    _classify_formula_tier,
    _parse_filter_predicate,
    _serialize_base,
    evaluate_formula,
    execute_base,
    extract_base_blocks,
    parse_file,
    validate_base,
    write_base_to_file,
)
from vault_mcp.index import VaultIndex

FIXTURES = ROOT / "tests" / "fixtures" / "bases"
VAULT_FIXTURE = ROOT / "tests" / "fixtures" / "bases-vault"


def _vault_idx() -> VaultIndex:
    idx = VaultIndex(VAULT_FIXTURE, ttl_seconds=9999)
    idx.reindex()
    return idx


# ===================================================================
# Phase 2: Parser tests (T007)
# ===================================================================


class TestExtractBaseBlocks:
    def test_simple_extraction(self):
        pf = parse_file(FIXTURES / "simple-table.md")
        assert len(pf.bases) == 1
        assert pf.bases[0].line_number > 0

    def test_multiple_bases(self):
        pf = parse_file(FIXTURES / "multiple-bases.md")
        assert len(pf.bases) == 2
        assert pf.bases[0].line_number < pf.bases[1].line_number

    def test_yaml_content_extracted(self):
        text = '# Title\n\n```base\nfilters:\n  and:\n    - file.folder == "X"\n```\n'
        blocks = extract_base_blocks(text)
        assert len(blocks) == 1
        assert "filters:" in blocks[0][0]


class TestFilterTreeParsing:
    def test_and_filter(self):
        pf = parse_file(FIXTURES / "simple-table.md")
        base = pf.bases[0]
        assert base.filters is not None
        assert base.filters.op == "and"
        assert base.filters.children is not None
        assert len(base.filters.children) == 2

    def test_nested_or_not(self):
        pf = parse_file(FIXTURES / "nested-filters.md")
        base = pf.bases[0]
        assert base.filters is not None
        assert base.filters.op == "and"
        children = base.filters.children
        assert children is not None
        ops = [c.op for c in children]
        assert "or" in ops
        assert "not" in ops

    def test_eq_predicate(self):
        node = _parse_filter_predicate('file.folder == "Projects"')
        assert node.op == "eq"
        assert node.field == "file.folder"
        assert node.value == "Projects"

    def test_neq_predicate(self):
        node = _parse_filter_predicate('file.name != "Projects"')
        assert node.op == "neq"
        assert node.field == "file.name"
        assert node.value == "Projects"

    def test_note_property_predicate(self):
        node = _parse_filter_predicate('note["status"] != "complete"')
        assert node.op == "neq"
        assert node.field == "note.status"
        assert node.value == "complete"

    def test_bare_property_predicate(self):
        node = _parse_filter_predicate('note_type != "plan"')
        assert node.op == "neq"
        assert node.field == "note.note_type"
        assert node.value == "plan"

    def test_haslink_predicate(self):
        node = _parse_filter_predicate('file.hasLink("Topics")')
        assert node.op == "hasLink"
        assert node.value == "Topics"

    def test_haslink_filter_file(self):
        pf = parse_file(FIXTURES / "has-link-filter.md")
        base = pf.bases[0]
        assert base.filters is not None
        assert base.filters.op == "and"
        assert base.filters.children is not None
        assert any(c.op == "hasLink" for c in base.filters.children)


class TestFormulaTierClassification:
    def test_note_key_tier1(self):
        assert _classify_formula_tier('note["status"]') == 1

    def test_file_mtime_tier1(self):
        assert _classify_formula_tier("file.mtime") == 1

    def test_file_name_tier1(self):
        assert _classify_formula_tier("file.name") == 1

    def test_links_filter_tier1(self):
        assert (
            _classify_formula_tier(
                'file.links.filter(value.asFile().ext == "md").length'
            )
            == 1
        )

    def test_bare_property_tier1(self):
        assert _classify_formula_tier("categories") == 1

    def test_html_tier2(self):
        assert _classify_formula_tier("html(something)") == 2

    def test_if_tier2(self):
        assert _classify_formula_tier('if(note["x"], "a", "b")') == 2

    def test_map_tier2(self):
        assert _classify_formula_tier('note["tags"].map(x => x)') == 2

    def test_join_tier2(self):
        assert _classify_formula_tier('note["tags"].join(", ")') == 2

    def test_replace_tier2(self):
        assert _classify_formula_tier('note["status"].replace("x", "y")') == 2

    def test_tostring_tier2(self):
        assert _classify_formula_tier('note["phase"].toString()') == 2

    def test_concat_tier2(self):
        assert _classify_formula_tier('note["a"] + " - " + note["b"]') == 2


class TestViewConfigParsing:
    def test_single_view(self):
        pf = parse_file(FIXTURES / "simple-table.md")
        base = pf.bases[0]
        assert len(base.views) == 1
        v = base.views[0]
        assert v.name == "Active"
        assert v.type == "table"
        assert len(v.order) == 3
        assert len(v.sort) == 1
        assert v.sort[0].property == "formula.Status"
        assert v.sort[0].direction == "ASC"

    def test_multi_view(self):
        pf = parse_file(FIXTURES / "multi-view.md")
        base = pf.bases[0]
        assert len(base.views) == 2
        names = [v.name for v in base.views]
        assert "Active" in names
        assert "All" in names

    def test_cards_view_parsed(self):
        pf = parse_file(FIXTURES / "cards-view.md")
        base = pf.bases[0]
        assert len(base.views) == 1
        assert base.views[0].type == "cards"
        assert base.views[0].name == "Project Cards"
        assert "cardSize" in base.views[0].extra

    def test_parse_map_view(self):
        pf = parse_file(FIXTURES / "map-view.md")
        base = pf.bases[0]
        v = base.views[0]
        assert v.type == "map"
        assert v.extra.get("lat property") == "latitude"
        assert v.extra.get("lng property") == "longitude"
        assert v.extra.get("default zoom") == 12

    def test_no_views(self):
        pf = parse_file(FIXTURES / "no-views.md")
        base = pf.bases[0]
        assert len(base.views) == 0
        assert base.filters is not None
        assert len(base.formulas) == 1


class TestInvalidYAML:
    def test_invalid_yaml_error(self):
        pf = parse_file(FIXTURES / "invalid-yaml.md")
        assert len(pf.errors) > 0
        assert pf.errors[0]["line_number"] > 0

    def test_valid_bases_still_returned(self):
        pf = parse_file(FIXTURES / "multiple-bases.md")
        assert len(pf.bases) == 2
        assert len(pf.errors) == 0


class TestTier2Formula:
    def test_tier2_formulas_classified(self):
        pf = parse_file(FIXTURES / "tier2-formula.md")
        base = pf.bases[0]
        assert all(f.tier == 2 for f in base.formulas.values())


# ===================================================================
# Phase 3: Execution tests (T015)
# ===================================================================


class TestFilterEvaluation:
    def test_folder_match(self):
        idx = _vault_idx()
        pf = parse_file(VAULT_FIXTURE / "Projects" / "Projects.md")
        base = pf.bases[0]
        result = execute_base(base, idx)
        paths = [n["path"] for n in result.notes]
        assert "Projects/Alpha.md" in paths
        assert "Projects/Beta.md" in paths
        assert "Projects/Gamma.md" in paths
        assert not any(Path(p).name == "Projects.md" for p in paths)

    def test_name_exclusion(self):
        idx = _vault_idx()
        pf = parse_file(VAULT_FIXTURE / "Projects" / "Projects.md")
        base = pf.bases[0]
        result = execute_base(base, idx)
        names = [Path(n["path"]).stem for n in result.notes]
        assert "Projects" not in names

    def test_frontmatter_equality(self):
        idx = _vault_idx()
        pf = parse_file(VAULT_FIXTURE / "Projects" / "Projects.md")
        base = pf.bases[0]
        result = execute_base(base, idx, view_name="Active")
        statuses = [n["frontmatter"].get("status") for n in result.notes]
        assert "complete" not in statuses
        assert "active" in statuses
        assert "draft" in statuses

    def test_haslink_filter(self):
        idx = _vault_idx()
        pf = parse_file(VAULT_FIXTURE / "Topics.md")
        base = pf.bases[0]
        result = execute_base(base, idx)
        paths = [n["path"] for n in result.notes]
        assert any("Linked" in p for p in paths)
        assert not any("Isolated" in p for p in paths)


class TestFormulaEvaluation:
    def test_note_key_formula(self):
        idx = _vault_idx()
        pf = parse_file(VAULT_FIXTURE / "Projects" / "Projects.md")
        base = pf.bases[0]
        result = execute_base(base, idx)
        alpha = next(n for n in result.notes if "Alpha" in n["path"])
        assert alpha["formulas"]["Status"] == "active"
        assert alpha["formulas"]["Phase"] == "1"

    def test_formula_null_for_missing(self):
        idx = _vault_idx()
        pf = parse_file(VAULT_FIXTURE / "Projects" / "Projects.md")
        base = pf.bases[0]
        result = execute_base(base, idx)
        gamma = next(n for n in result.notes if "Gamma" in n["path"])
        assert gamma["formulas"]["Phase"] is None

    def test_file_name_formula(self):
        idx = _vault_idx()
        pf = parse_file(VAULT_FIXTURE / "Topics.md")
        base = pf.bases[0]
        result = execute_base(base, idx)
        for note in result.notes:
            assert note["formulas"]["Name"] == Path(note["path"]).stem

    def test_tier2_evaluation_success(self):
        idx = _vault_idx()
        pf = parse_file(FIXTURES / "tier2-formulas.md")
        # Base 1: Conditionals
        base = pf.bases[0]
        result = execute_base(base, idx)
        assert len(result.warnings) == 0
        for note in result.notes:
            # Check labels based on status
            status = note["frontmatter"].get("status")
            if status == "active":
                assert note["formulas"]["label"] == "ACTIVE"
            else:
                assert note["formulas"]["label"] == "INACTIVE"

    def test_cards_view_execution(self):
        idx = _vault_idx()
        pf = parse_file(FIXTURES / "cards-view.md")
        base = pf.bases[0]
        result = execute_base(base, idx, view_name="Project Cards")
        assert result.view_properties["type"] == "cards"
        assert result.view_properties["cardSize"] == "medium"
        assert result.view_properties["image"] == "cover"
        assert result.view_properties["imageAspectRatio"] == "16/9"
        assert result.view_properties["indentProperties"] is True

    def test_list_shaping(self):
        idx = _vault_idx()
        pf = parse_file(FIXTURES / "tier2-formulas.md")
        # Base 2: List Shaping
        base = pf.bases[1]
        result = execute_base(base, idx)
        assert len(result.warnings) == 0
        for note in result.notes:
            tags = note["frontmatter"].get("tags", [])
            if tags:
                # the tags_str formula under test, verbatim, in the Bases
                # expression language rather than Python —
                #     tags.map(t => "#" + t).join(", ")
                expected = ", ".join(f"#{t}" for t in tags)
                assert note["formulas"]["tags_str"] == expected

    def test_concatenation(self):
        idx = _vault_idx()
        pf = parse_file(FIXTURES / "tier2-formulas.md")
        # Base 3: Concatenation and Coercion
        base = pf.bases[2]
        result = execute_base(base, idx)
        assert len(result.warnings) == 0
        for note in result.notes:
            # the full_path formula under test, verbatim, in the Bases
            # expression language rather than Python —
            #     file.folder + "/" + file.name + "." + file.ext
            expected = note["path"]
            assert note["formulas"]["full_path"] == expected

    def test_unknown_function_warning(self):
        idx = _vault_idx()
        base = Base(
            filters=None,
            formulas={"bad": Formula("bad", "futureFunc(1, 2)", 2)},
            views=[],
            raw_yaml="",
            line_number=1,
        )
        result = execute_base(base, idx)
        assert len(result.warnings) == 1
        assert "Unsupported function" in result.warnings[0]["reason"]

    def test_nesting_depth_exceeded(self):
        idx = _vault_idx()
        # Create a 11-level nested if
        expr = "if(1, " * 11 + "1" + ", 0)" * 11
        base = Base(
            filters=None,
            formulas={"deep": Formula("deep", expr, 2)},
            views=[],
            raw_yaml="",
            line_number=1,
        )
        result = execute_base(base, idx)
        assert len(result.warnings) == 1
        assert "Max nesting depth" in result.warnings[0]["reason"]

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "vault-mcp#5310 — regex_timeout does not bound anything. `re.sub` "
            "holds the GIL for its whole run, so the ThreadPoolExecutor worker "
            "never yields and future.result(timeout=...) cannot wake to honour "
            "its own timeout. Measured: at regex_timeout=0.001 the same "
            "expression still ran 1.95s and COMPLETED, while 0.01 timed out — "
            "the outcome is a scheduling race, not a timeout. STRICT so a real "
            "fix (the `regex` module's timeout=, or refusing nested "
            "quantifiers at parse time) fails this loudly instead of landing "
            "unnoticed."
        ),
    )
    def test_regex_timeout_bounds_the_work(self):
        """A pathological regex must cost about `regex_timeout`, not its full run.

        THIS REPLACES A TEST THAT PASSED BY LUCK. The previous version asserted
        that a "timed out" warning appeared, which is a race — and it cost ~18
        of the suite's 24 seconds, because it drove `execute_base` over every
        note in a fixture vault and each catastrophic regex ran to completion.

        Two things changed. It is now a UNIT check on the evaluator, so the
        regex runs once rather than once per note; and the input is 22 'a's
        rather than 25, which is ~0.33s of backtracking instead of ~2.2s — still
        far over the 0.05s timeout, so the assertion below is just as
        meaningful, at a fraction of the cost.

        That cost matters out of proportion to its size: the suite is the inner
        loop of the mutation gate, which runs it once per mutant. On PR #390
        that was 1159 mutants.
        """
        evaluator = FormulaEvaluator({}, 5, 0.05)
        expr = '"' + "a" * 22 + '".replace(/(a+)+b/, "c")'

        start = time.monotonic()
        with contextlib.suppress(FormulaTimeoutError):
            evaluator.evaluate(expr)
        elapsed = time.monotonic() - start

        # The contract a timeout is supposed to give: bounded work. 3x the
        # configured timeout is generous enough that this fails on the
        # MECHANISM rather than on machine speed — the measured cost today is
        # ~0.33s against a 0.05s timeout, a 6x overrun, and a real fix would
        # land near 0.05s.
        assert elapsed < 0.15, (
            f"regex ran {elapsed:.2f}s against a 0.05s timeout — "
            f"the timeout bounded nothing"
        )


class TestLinkCountFormula:
    def test_outbound_link_count(self):
        idx = _vault_idx()
        beta_path = VAULT_FIXTURE / "Projects" / "Beta.md"
        f = Formula(
            name="LinksOut",
            expression='file.links.filter(value.asFile().ext == "md").length',
            tier=1,
        )
        outbound = set(idx._outbound.get("Beta", []))
        inbound = set(idx._inbound.get("Beta", []))
        val, warning = evaluate_formula(
            f, beta_path, {}, "Projects/Beta.md", outbound, inbound
        )
        assert warning is None
        assert val == len(outbound)
        assert val >= 2

    def test_inbound_link_count(self):
        idx = _vault_idx()
        alpha_path = VAULT_FIXTURE / "Projects" / "Alpha.md"
        f = Formula(
            name="LinksIn",
            expression='file.backlinks.filter(value.asFile().ext == "md").length',
            tier=1,
        )
        outbound = set(idx._outbound.get("Alpha", []))
        inbound = set(idx._inbound.get("Alpha", []))
        val, warning = evaluate_formula(
            f, alpha_path, {}, "Projects/Alpha.md", outbound, inbound
        )
        assert warning is None
        assert val >= 1


class TestViewSelection:
    def test_base_level_only(self):
        idx = _vault_idx()
        pf = parse_file(VAULT_FIXTURE / "Projects" / "Projects.md")
        base = pf.bases[0]
        result = execute_base(base, idx)
        assert result.view_name is None
        assert result.total == 3

    def test_named_view(self):
        idx = _vault_idx()
        pf = parse_file(VAULT_FIXTURE / "Projects" / "Projects.md")
        base = pf.bases[0]
        result = execute_base(base, idx, view_name="Active")
        assert result.view_name == "Active"
        assert result.total == 2

    def test_view_specific_filter(self):
        idx = _vault_idx()
        pf = parse_file(VAULT_FIXTURE / "Projects" / "Projects.md")
        base = pf.bases[0]
        result = execute_base(base, idx, view_name="Active")
        for note in result.notes:
            assert note["frontmatter"].get("status") != "complete"

    def test_sort_application(self):
        idx = _vault_idx()
        pf = parse_file(VAULT_FIXTURE / "Projects" / "Projects.md")
        base = pf.bases[0]
        result = execute_base(base, idx, view_name="All")
        names = [Path(n["path"]).stem for n in result.notes]
        assert names == sorted(names)

    def test_empty_result_set(self):
        idx = _vault_idx()
        base = Base(
            filters=FilterNode(
                op="eq", field="file.folder", value="Nonexistent"
            ),
            formulas={},
            views=[],
            raw_yaml="",
            line_number=1,
        )
        result = execute_base(base, idx)
        assert result.total == 0
        assert result.notes == []

    def test_invalid_view_type_unsupported(self):
        idx = _vault_idx()
        base = Base(
            filters=None,
            formulas={},
            views=[ViewConfig(name="Gallery", type="gallery")],
            raw_yaml="",
            line_number=1,
        )
        result = execute_base(base, idx, view_name="Gallery")
        assert result.total == 0
        assert any("not supported" in w["reason"] for w in result.warnings)

    def test_execute_map_view(self):
        idx = _vault_idx()
        pf = parse_file(FIXTURES / "map-view.md")
        base = pf.bases[0]
        result = execute_base(base, idx, view_name="Global Map")
        assert result.notes == []
        assert any(
            "Maps community plugin" in w["reason"] for w in result.warnings
        )

    def test_no_views_execution(self):
        idx = _vault_idx()
        pf = parse_file(FIXTURES / "no-views.md")
        base = pf.bases[0]
        result = execute_base(base, idx)
        assert result.view_name is None


# ===================================================================
# Phase 4: Parse tool tests (T021)
# ===================================================================


class TestSerializeBase:
    def test_serialize_simple(self):
        pf = parse_file(FIXTURES / "simple-table.md")
        base = pf.bases[0]
        s = _serialize_base(base)
        assert "filters" in s
        assert s["filters"]["op"] == "and"
        assert "formulas" in s
        assert "Status" in s["formulas"]
        assert s["formulas"]["Status"]["tier"] == 1
        assert "views" in s
        assert len(s["views"]) == 1

    def test_serialize_multi_base(self):
        pf = parse_file(FIXTURES / "multiple-bases.md")
        assert len(pf.bases) == 2
        for base in pf.bases:
            s = _serialize_base(base)
            assert s["line_number"] > 0

    def test_serialize_cards_no_error(self):
        pf = parse_file(FIXTURES / "cards-view.md")
        base = pf.bases[0]
        s = _serialize_base(base)
        cards = [v for v in s["views"] if v["type"] == "cards"]
        assert len(cards) == 1

    def test_round_trip_map_view(self):
        pf = parse_file(FIXTURES / "map-view.md")
        base = pf.bases[0]
        s = _serialize_base(base)
        v = s["views"][0]
        assert v["type"] == "map"
        assert v["lat property"] == "latitude"
        assert v["lng property"] == "longitude"
        assert v["default zoom"] == 12
        assert v["marker config"] == "custom-icon"

    def test_parse_empty_file(self):
        import tempfile

        with tempfile.NamedTemporaryFile(
            suffix=".md", delete=False, mode="w"
        ) as f:
            f.write("# No base here\n\nJust some text.\n")
            f.flush()
            pf = parse_file(Path(f.name))
        Path(f.name).unlink()
        assert len(pf.bases) == 0
        assert len(pf.errors) == 0

    def test_invalid_yaml_in_errors(self):
        pf = parse_file(FIXTURES / "invalid-yaml.md")
        assert len(pf.errors) > 0
        assert pf.errors[0]["line_number"] > 0
        assert "message" in pf.errors[0]


# ===================================================================
# Phase 5: Writer tests (T026)
# ===================================================================


class TestWriter:
    def _tmp_copy(self, src: Path, tmp_path: Path) -> Path:
        dest = tmp_path / src.name
        shutil.copy2(src, dest)
        return dest

    def test_append_to_empty(self, tmp_path: Path):
        target = tmp_path / "empty.md"
        target.write_text("# Empty\n\nSome text.\n", encoding="utf-8")
        original_text = target.read_text(encoding="utf-8")

        base_dict = {
            "filters": {"and": ['file.folder == "Test"']},
            "formulas": {"Name": "file.name"},
            "views": [{"type": "table", "name": "Table"}],
        }
        result = write_base_to_file(target, base_dict)
        assert result["written"] is True
        assert result["action"] == "appended"

        new_text = target.read_text(encoding="utf-8")
        assert "```base" in new_text
        assert new_text.startswith(original_text.rstrip("\n"))

    def test_replace_single(self, tmp_path: Path):
        target = self._tmp_copy(FIXTURES / "simple-table.md", tmp_path)
        original = target.read_text(encoding="utf-8")
        before_base = original.split("```base")[0]
        after_base = original.split("```\n")[-1]

        base_dict = {
            "filters": {"and": ['file.folder == "New"']},
            "formulas": {"X": "file.name"},
            "views": [{"type": "table", "name": "Updated"}],
        }
        result = write_base_to_file(target, base_dict)
        assert result["written"] is True
        assert result["action"] == "updated"

        new_text = target.read_text(encoding="utf-8")
        assert new_text.startswith(before_base)
        assert new_text.endswith(after_base) or after_base.strip() in new_text

    def test_replace_by_index_in_multi(self, tmp_path: Path):
        target = self._tmp_copy(FIXTURES / "multiple-bases.md", tmp_path)
        base_dict = {"filters": {"and": ['file.folder == "Replaced"']}}
        result = write_base_to_file(target, base_dict, base_index=1)
        assert result["written"] is True
        assert result["base_index"] == 1

        pf = parse_file(target)
        assert len(pf.bases) == 2
        assert pf.bases[0].filters is not None
        second_filter = pf.bases[1].filters
        assert second_filter is not None

    def test_ambiguous_target_error(self, tmp_path: Path):
        target = self._tmp_copy(FIXTURES / "multiple-bases.md", tmp_path)
        base_dict = {"filters": {"and": ['file.folder == "X"']}}
        result = write_base_to_file(target, base_dict, base_index=None)
        assert result["written"] is False
        assert result["error"] == "ambiguous_target"

    def test_round_trip(self, tmp_path: Path):
        target = tmp_path / "roundtrip.md"
        target.write_text("# Test\n", encoding="utf-8")
        base_dict = {
            "filters": {
                "and": ['file.folder == "Test"', 'file.name != "Test"']
            },
            "formulas": {"Status": 'note["status"]'},
            "views": [{"type": "table", "name": "Table"}],
        }
        write_base_to_file(target, base_dict)
        pf = parse_file(target)
        assert len(pf.bases) == 1
        assert len(pf.errors) == 0

    def test_byte_identical_surrounding(self, tmp_path: Path):
        original_content = '---\nnote_type: folder\n---\n\n# Title\n\nParagraph one.\n\n```base\nfilters:\n  and:\n    - file.folder == "Old"\n```\n\nParagraph two.\n'
        target = tmp_path / "preserve.md"
        target.write_text(original_content, encoding="utf-8")

        base_dict = {"filters": {"and": ['file.folder == "New"']}}
        write_base_to_file(target, base_dict)

        new_content = target.read_text(encoding="utf-8")
        original_before = original_content.split("```base", maxsplit=1)[0]
        new_before = new_content.split("```base")[0]
        assert original_before == new_before

        original_after = original_content.split("```\n", 1)
        new_after = new_content.split("```\n", 1)
        if len(original_after) > 1 and len(new_after) > 1:
            assert original_after[-1] == new_after[-1]


# ===================================================================
# Phase 6: Validator tests (T031)
# ===================================================================


class TestValidator:
    def test_valid_base(self):
        base_dict = {
            "filters": {"and": ['file.folder == "Test"']},
            "formulas": {"Status": 'note["status"]'},
            "views": [
                {
                    "type": "table",
                    "name": "Table",
                    "order": ["file.name", "formula.Status"],
                }
            ],
        }
        result = validate_base(base_dict)
        assert result.valid is True
        assert len(result.errors) == 0

    def test_undefined_formula_ref(self):
        base_dict = {
            "filters": {"and": ['file.folder == "Test"']},
            "formulas": {"Status": 'note["status"]'},
            "views": [
                {"type": "table", "name": "Table", "order": ["formula.Missing"]}
            ],
        }
        result = validate_base(base_dict)
        assert result.valid is False
        assert any(e["type"] == "undefined_formula_ref" for e in result.errors)

    def test_unquoted_special_char_warning(self):
        base_dict = {
            "filters": {"and": ['file.folder == "Test: Special"']},
            "formulas": {},
            "views": [],
        }
        result = validate_base(base_dict)
        assert any(
            w["type"] == "unquoted_special_char" for w in result.warnings
        )

    def test_undefined_sort_ref_warning(self):
        base_dict = {
            "filters": {},
            "formulas": {"Status": 'note["status"]'},
            "views": [
                {
                    "type": "table",
                    "name": "Table",
                    "sort": [
                        {"property": "formula.Missing", "direction": "ASC"}
                    ],
                }
            ],
        }
        result = validate_base(base_dict)
        assert any(w["type"] == "undefined_sort_ref" for w in result.warnings)

    def test_multiple_errors(self):
        base_dict = {
            "filters": {},
            "formulas": {},
            "views": [
                {
                    "type": "table",
                    "name": "V1",
                    "order": ["formula.A", "formula.B"],
                }
            ],
        }
        result = validate_base(base_dict)
        assert result.valid is False
        assert len(result.errors) >= 2


# ===================================================================
# Phase 7: Performance smoke test (T041 / SC-006)
# ===================================================================


class TestPerformance:
    """SC-006: parse + execute round-trip < 200 ms on the fixture vault."""

    def test_parse_under_200ms(self):
        import time

        path = VAULT_FIXTURE / "Projects" / "Projects.md"
        start = time.perf_counter()
        for _ in range(50):
            parse_file(path)
        elapsed_per_call = (time.perf_counter() - start) / 50
        assert elapsed_per_call < 0.2, (
            f"parse_file took {elapsed_per_call * 1000:.1f}ms"
        )

    def test_execute_under_200ms(self):
        import time

        path = VAULT_FIXTURE / "Projects" / "Projects.md"
        idx = VaultIndex(VAULT_FIXTURE)
        parsed = parse_file(path)
        base = parsed.bases[0]

        start = time.perf_counter()
        for _ in range(50):
            execute_base(base, idx)
        elapsed_per_call = (time.perf_counter() - start) / 50
        assert elapsed_per_call < 0.2, (
            f"execute_base took {elapsed_per_call * 1000:.1f}ms"
        )


# ===================================================================
# Phase 8: Summaries tests (003)
# ===================================================================


class TestSummaries:
    def _summaries_idx(self) -> VaultIndex:
        v_path = ROOT / "tests" / "fixtures" / "summaries-vault"
        idx = VaultIndex(v_path, ttl_seconds=9999)
        idx.reindex()
        return idx

    def test_count_summary(self):
        idx = self._summaries_idx()
        pf = parse_file(FIXTURES / "summaries-base.md")
        base = pf.bases[0]

        # Base-level count
        result = execute_base(base, idx)
        assert result.summaries["Total"] == 3

        # View-level count
        result = execute_base(base, idx, view_name="Active")
        assert result.summaries["Active Count"] == 2
        assert result.summaries["Average Phase"] == 1.5

    def test_numeric_summaries(self):
        idx = self._summaries_idx()
        pf = parse_file(FIXTURES / "summaries-numeric.md")
        base = pf.bases[0]
        result = execute_base(base, idx)

        # Alpha: 10, Beta: 20, Gamma: 30
        # sum = 60, avg = 20, min = 10, max = 30, range = 20
        assert result.summaries["Total Amount"] == 60
        assert result.summaries["Average Amount"] == 20
        assert result.summaries["Min Amount"] == 10
        assert result.summaries["Max Amount"] == 30
        assert result.summaries["Range Amount"] == 20

    def test_non_numeric_values_are_skipped_not_fatal(self):
        """A numeric summary over un-coercible values skips them and survives.

        THE MUTATION GATE FOUND THIS GAP. The accumulator wraps `float(val)` in
        `except ValueError, TypeError`, and cosmic-ray replaced `ValueError`
        with a dummy exception on a diff-scoped run — no test noticed, so that
        arm was never exercised. Both arms are distinct in practice:

            float("not a number")  -> ValueError   (a non-numeric STRING)
            float([1, 2])          -> TypeError    (a non-coercible TYPE)

        Frontmatter is user-written YAML, so both reach this code from a real
        vault. Without the catch, one malformed note aborts the whole base
        execution rather than being skipped.
        """
        v_path = ROOT / "tests" / "fixtures" / "summaries-mixed-vault"
        idx = VaultIndex(v_path, ttl_seconds=9999)
        idx.reindex()
        pf = parse_file(FIXTURES / "summaries-numeric.md")
        base = pf.bases[0]

        result = execute_base(base, idx)

        # Three notes, only one with a coercible amount (10).
        assert result.total == 3
        assert result.summaries["Total Amount"] == 10
        assert result.summaries["Average Amount"] == 10
        assert result.summaries["Min Amount"] == 10
        assert result.summaries["Max Amount"] == 10

    def test_boundary_summaries(self):
        idx = self._summaries_idx()
        # Empty result set
        base = Base(
            filters=FilterNode(op="eq", field="file.name", value="Nonexistent"),
            formulas={},
            views=[],
            summaries=[Summary(name="Zero", function="count", property=None)],
        )
        result = execute_base(base, idx)
        assert result.summaries["Zero"] == 0

        # Numeric summary on empty set
        base.summaries = [
            Summary(name="NullSum", function="sum", property="amount")
        ]
        result = execute_base(base, idx)
        assert result.summaries["NullSum"] == 0

        base.summaries = [
            Summary(name="NullAvg", function="average", property="amount")
        ]
        result = execute_base(base, idx)
        assert result.summaries["NullAvg"] is None


# ===================================================================
# Phase 9: Grouping tests (005)
# ===================================================================


class TestGrouping:
    def test_groupby_raw_property(self):
        idx = _vault_idx()
        # Alpha: active, Beta: complete, Gamma: draft
        # ASC: active, complete, draft
        pf = parse_file(FIXTURES / "groupby-raw.md")
        base = pf.bases[0]
        result = execute_base(base, idx, view_name="StatusGroup")

        assert len(result.groups) == 3
        assert result.groups[0].label == "active"
        assert result.groups[0].count == 1
        assert result.groups[1].label == "complete"
        assert result.groups[1].count == 1
        assert result.groups[2].label == "draft"
        assert result.groups[2].count == 1

        # DESC: draft, complete, active
        pf_desc = parse_file(FIXTURES / "groupby-desc.md")
        base_desc = pf_desc.bases[0]
        result_desc = execute_base(base_desc, idx, view_name="StatusGroupDesc")

        assert len(result_desc.groups) == 3
        assert result_desc.groups[0].label == "draft"
        assert result_desc.groups[1].label == "complete"
        assert result_desc.groups[2].label == "active"

    def test_groupby_formula(self):
        idx = _vault_idx()
        pf = parse_file(FIXTURES / "groupby-formula.md")
        base = pf.bases[0]

        # IconGroup DESC
        result = execute_base(base, idx, view_name="IconGroup")
        assert len(result.groups) == 3
        # DESC: 🟢, ✅, ⚪ (Verify alphabetical order in Python)
        labels = [g.label for g in result.groups]
        assert labels == sorted(labels, reverse=True)
        assert result.groups[0].label == "🟢"
        assert result.groups[1].label == "✅"
        assert result.groups[2].label == "⚪"

        # ErrorGroup
        result_err = execute_base(base, idx, view_name="ErrorGroup")
        assert len(result_err.groups) == 1
        assert result_err.groups[0].label == "Error"
        assert result_err.groups[0].count == 3

    def test_groupby_preserves_sort(self):
        idx = _vault_idx()
        pf = parse_file(FIXTURES / "groupby-sorted.md")
        base = pf.bases[0]
        result = execute_base(base, idx, view_name="TypeGroup")

        assert len(result.groups) == 1
        group = result.groups[0]
        assert group.label == "plan"
        assert group.count == 3
        # Should be Gamma, Beta, Alpha (DESC name)
        names = [Path(n["path"]).stem for n in group.notes]
        assert names == ["Gamma", "Beta", "Alpha"]

    def test_groupby_missing_property_yields_none_group(self):
        """Notes without the groupBy property land in a '(None)' group."""
        idx = _vault_idx()
        pf = parse_file(FIXTURES / "groupby-missing-prop.md")
        base = pf.bases[0]
        result = execute_base(base, idx, view_name="PhaseGroup")

        # Alpha has phase=1, Beta has phase=2, Gamma has NO phase
        labels = {g.label for g in result.groups}
        assert "(None)" in labels
        none_group = next(g for g in result.groups if g.label == "(None)")
        none_paths = [Path(n["path"]).stem for n in none_group.notes]
        assert "Gamma" in none_paths

    def test_groupby_file_name_metadata(self):
        """groupBy on file.name uses each note's stem as the group key."""
        idx = _vault_idx()
        pf = parse_file(FIXTURES / "groupby-file-name.md")
        base = pf.bases[0]
        result = execute_base(base, idx, view_name="ByFileName")

        # Three projects: Alpha, Beta, Gamma — each in its own group
        assert len(result.groups) == 3
        labels = sorted(g.label for g in result.groups)
        assert labels == ["Alpha", "Beta", "Gamma"]
        for g in result.groups:
            assert g.count == 1

    def test_groupby_empty_result_set(self):
        """groupBy on a base with no matching notes produces empty groups."""
        idx = _vault_idx()
        pf = parse_file(FIXTURES / "groupby-empty.md")
        base = pf.bases[0]
        result = execute_base(base, idx, view_name="EmptyGroup")

        assert result.total == 0
        assert result.groups == []

    def test_groupby_non_string_key_coercion(self):
        """Numeric property values are coerced to string group labels."""
        idx = _vault_idx()
        pf = parse_file(FIXTURES / "groupby-missing-prop.md")
        base = pf.bases[0]
        result = execute_base(base, idx, view_name="PhaseGroup")

        # Alpha: phase=1, Beta: phase=2, Gamma: no phase
        labels = [g.label for g in result.groups]
        # Numeric values coerced to strings
        assert "1" in labels
        assert "2" in labels
        assert "(None)" in labels
        # Verify counts
        for g in result.groups:
            if g.label in ("1", "2"):
                assert g.count == 1
