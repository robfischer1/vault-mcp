"""Behavioural coverage for Base execution — view selection, grouping, sorting.

Companion to test_bases_eval.py, written for the same reason: the mutation gate
on PR #390 found ~4-in-10 of its real survivors in this module, all of them
pre-existing (see that file's header).

The shape here is property DISPATCH — `file.name` / `file.folder` / `file.path`
/ `file.ext` / `formula.*` / bare frontmatter — repeated in three places
(grouping, sorting, and the row projection). Every arm had mutants surviving
because the suite exercised one arm and left the rest unvisited, so a mutated
comparison routed a value to a different arm and no assertion noticed.

`formula.` and `file.` slicing is the sharpest case: `_prop[8:]` mutated to
`_prop[7:]` survives unless a test actually reads a formula-backed property by
name, because an off-by-one in the slice produces a key nothing looks up and the
result is `None` either way — indistinguishable unless the RIGHT key resolves to
a real value.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from vault_mcp.bases_exec import execute_base
from vault_mcp.bases_model import (
    Base,
    Formula,
    GroupByConfig,
    SortDirective,
    ViewConfig,
)
from vault_mcp.index import VaultIndex

FIXTURES = ROOT / "tests" / "fixtures"


@pytest.fixture
def idx() -> VaultIndex:
    """The bases fixture vault, indexed."""
    index = VaultIndex(FIXTURES / "bases-vault", ttl_seconds=9999)
    index.reindex()
    return index


def _base(**kw) -> Base:
    """A Base with the boilerplate filled in."""
    return Base(
        filters=kw.pop("filters", None),
        formulas=kw.pop("formulas", {}),
        views=kw.pop("views", []),
        raw_yaml="",
        line_number=1,
        **kw,
    )


class TestViewSelection:
    """`if v.name == view_name` — picking a view by name."""

    def test_named_view_is_selected(self, idx):
        base = _base(
            views=[
                ViewConfig(name="A", type="table"),
                ViewConfig(name="B", type="cards"),
            ]
        )
        result = execute_base(base, idx, view_name="B")
        assert result.view_properties["type"] == "cards"

    def test_a_different_name_selects_a_different_view(self, idx):
        """Pins the two against each other, so a mutated `==` that matched the
        wrong view would change the answer."""
        base = _base(
            views=[
                ViewConfig(name="A", type="table"),
                ViewConfig(name="B", type="cards"),
            ]
        )
        assert (
            execute_base(base, idx, view_name="A").view_properties["type"]
            == "table"
        )

    def test_an_unknown_view_name_yields_an_empty_result(self, idx):
        base = _base(views=[ViewConfig(name="A", type="table")])
        result = execute_base(base, idx, view_name="nope")
        assert result.notes == []
        assert result.total == 0

    def test_no_view_name_runs_the_base_itself(self, idx):
        result = execute_base(_base(), idx)
        assert result.total > 0


class TestCardsViewProperties:
    """`if selected_view.type == "cards"` — the card-only extras."""

    def test_cards_view_carries_its_extras(self, idx):
        view = ViewConfig(
            name="C", type="cards", extra={"cardSize": 200, "image": "cover"}
        )
        props = execute_base(
            _base(views=[view]), idx, view_name="C"
        ).view_properties
        assert props["cardSize"] == 200
        assert props["image"] == "cover"

    def test_a_table_view_does_not_carry_card_extras(self, idx):
        """The `== "cards"` mutant would copy these onto a table view."""
        view = ViewConfig(
            name="T", type="table", extra={"cardSize": 200, "image": "cover"}
        )
        props = execute_base(
            _base(views=[view]), idx, view_name="T"
        ).view_properties
        assert "cardSize" not in props
        assert "image" not in props


class TestGroupingPropertyDispatch:
    """Each `file.*` arm resolves to a DIFFERENT value for the same note."""

    @pytest.mark.parametrize(
        "prop",
        ["file.name", "file.folder", "file.path", "file.ext"],
    )
    def test_each_file_property_groups_by_its_own_value(self, idx, prop):
        view = ViewConfig(
            name="G",
            type="table",
            group_by=GroupByConfig(property=prop, direction="ASC"),
        )
        result = execute_base(_base(views=[view]), idx, view_name="G")
        assert result.groups, f"{prop} produced no groups"

    def test_the_file_arms_disagree_with_each_other(self, idx):
        """The point of the dispatch: for the same vault, grouping by name and
        by folder must NOT give the same grouping. A mutated comparison that
        routed one arm into another would collapse them."""

        def keys(prop: str) -> set[str]:
            view = ViewConfig(
                name="G",
                type="table",
                group_by=GroupByConfig(property=prop, direction="ASC"),
            )
            res = execute_base(_base(views=[view]), idx, view_name="G")
            return {g.label for g in res.groups}

        assert keys("file.name") != keys("file.folder")
        assert keys("file.ext") != keys("file.name")

    def test_extension_grouping_strips_the_dot(self, idx):
        view = ViewConfig(
            name="G",
            type="table",
            group_by=GroupByConfig(property="file.ext", direction="ASC"),
        )
        result = execute_base(_base(views=[view]), idx, view_name="G")
        assert any(g.label == "md" for g in result.groups)
        assert not any(g.label.startswith(".") for g in result.groups)


class TestFormulaPropertyDispatch:
    """`formula.` slicing — `_prop[8:]` must land on the formula's real name."""

    def test_grouping_by_a_formula_uses_the_formula_value(self, idx):
        base = _base(
            formulas={"tag": Formula("tag", '"grouped"', 2)},
            views=[
                ViewConfig(
                    name="G",
                    type="table",
                    group_by=GroupByConfig(
                        property="formula.tag", direction="ASC"
                    ),
                )
            ],
        )
        result = execute_base(base, idx, view_name="G")
        # len("formula.") == 8; a [7:] slice looks up ".tag", which no formula
        # defines, and every row would fall into "(None)" instead.
        assert any(g.label == "grouped" for g in result.groups)

    def test_sorting_by_a_formula_uses_the_formula_value(self, idx):
        base = _base(
            formulas={"k": Formula("k", "file.name", 1)},
            views=[
                ViewConfig(
                    name="S",
                    type="table",
                    sort=[SortDirective(property="formula.k", direction="ASC")],
                )
            ],
        )
        result = execute_base(base, idx, view_name="S")
        names = [n["formulas"]["k"] for n in result.notes]
        assert names == sorted(names)


class TestSortDirection:
    """`reverse = sd.direction == "DESC"` — the two directions must differ."""

    def _sorted_names(self, idx, direction: str) -> list[str]:
        view = ViewConfig(
            name="S",
            type="table",
            sort=[SortDirective(property="file.name", direction=direction)],
        )
        res = execute_base(_base(views=[view]), idx, view_name="S")
        return [Path(n["path"]).stem for n in res.notes]

    def test_ascending_is_sorted(self, idx):
        names = self._sorted_names(idx, "ASC")
        assert names == sorted(names)

    def test_descending_is_the_reverse_of_ascending(self, idx):
        assert self._sorted_names(idx, "DESC") == list(
            reversed(self._sorted_names(idx, "ASC"))
        )


class TestFormulaTierGate:
    """`tier` selects which evaluator a formula gets, and they differ.

    Found while writing the grouping test above: `Formula(..., tier=1)` with a
    bare string constant yields "Unsupported expression", while the SAME
    expression at tier 2 evaluates. Tier 1 is a restricted subset — `file.*`
    references work, arbitrary constants do not. Nothing asserted the boundary
    in either direction, so a mutated tier comparison could route every formula
    to the permissive evaluator and no test would notice.
    """

    def _run(self, idx, expr: str, tier: int):
        base = _base(
            formulas={"f": Formula("f", expr, tier)},
            views=[ViewConfig(name="V", type="table")],
        )
        return execute_base(base, idx, view_name="V")

    def test_tier_two_evaluates_a_constant(self, idx):
        result = self._run(idx, '"x"', 2)
        assert result.warnings == []
        assert all(n["formulas"]["f"] == "x" for n in result.notes)

    def test_tier_one_refuses_the_same_constant(self, idx):
        """The tier gate is real: identical expression, different verdict."""
        result = self._run(idx, '"x"', 1)
        assert result.warnings
        assert "Unsupported" in result.warnings[0]["reason"]

    def test_tier_one_still_resolves_file_references(self, idx):
        """Tier 1 is restricted, not disabled."""
        result = self._run(idx, "file.name", 1)
        assert result.warnings == []
        assert all(n["formulas"]["f"] for n in result.notes)
