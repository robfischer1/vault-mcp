"""Behavioural coverage for bases_exec — the property and function dispatch arms.

Third of four, one per module, closing the survivors the #5294 split surfaced.
bases_exec measured 82.9% honest with 37 real survivors, and 30 of them are one
shape: `==` mutated to `<`, `<=`, `>`, `>=` or `!=` in a dispatch chain over
literal names.

WHY THAT SHAPE SURVIVES A GREEN SUITE. A chain like

    if   prop == "file.name":   ...
    elif prop == "file.folder": ...
    elif prop == "file.path":   ...
    elif prop == "file.ext":    ...
    else:                       frontmatter.get(prop[5:])

is only pinned if a test drives EVERY arm and reads a value that differs per
arm. The old suite grouped by one property and asserted "some groups came back".
Mutating `==` to `>` then routes `file.path` into the folder arm — a different
value, still non-empty, and nothing looks.

SO THE FIXTURE IS THE TEST. This module builds a three-note vault whose name,
folder, path and frontmatter orders are all DIFFERENT from one another:

    Zeta.md          folder "."       zzz "b"   num 10
    Sub/Beta.md      folder "Sub"     zzz "c"   num  2
    Other/Gamma.md   folder "Other"   zzz "a"   num  6

    group by name   -> Beta, Gamma, Zeta        sort by name   -> Beta, Gamma, Zeta
    group by folder -> ".", Other, Sub          sort by folder -> Zeta, Gamma, Beta
    group by path   -> Other/…, Sub/…, Zeta.md  sort by path   -> Gamma, Beta, Zeta
    group by zzz    -> a, b, c                  sort by zzz    -> Gamma, Zeta, Beta

Every arm yields its own answer, so any mis-routing changes an assertion.

THE zzz VALUES ARE NOT ARBITRARY, and my first attempt got them wrong. With
zzz = a/b/c the frontmatter sort came out Zeta, Beta, Gamma — which is exactly
the UNSORTED order. A mutant slicing `_prop[6:]` instead of `[5:]` looks up a
key that does not exist, so every sort key becomes "", the sort is stable, and
the original order survives untouched. It passed. Only a frontmatter order that
differs from the unsorted order can catch it, so zzz is permuted to give the one
arrangement none of unsorted / name / folder / path already occupies.
The summary values are chosen the same way: count 3, sum 18, average 6, min 2,
max 10, range 8 — six distinct numbers, so no aggregate can be mistaken for
another.

THREE SURVIVORS I FIRST LEFT ALONE AS "PROVABLY UNKILLABLE". All three are now
resolved in code — the reasoning is kept because it is what each site needed,
but the conclusion "therefore exempt it" was wrong:

  L171 RESOLVED: the promoted keys now come from a `_PROMOTED_VIEW_KEYS`
       mapping keyed by view type, so no comparison remains.
       It had been `selected_view.type == "cards"` -> `<= "cards"`. Seventeen lines above,
       `if selected_view.type not in ("table", "cards")` returns early, and both
       sit in the same `view_name is not None` branch. On the domain that
       actually reaches L171, `"table" <= "cards"` is False and
       `"cards" <= "cards"` is True — identical to `==`.

  L284 RESOLVED: now `if not a["count_with_val"]:`, which has no operator.
       It had been `a["count_with_val"] == 0` -> `<= 0`. The accumulator is initialised to 0
       and only ever `+= 1`, so it cannot go negative and `<=` cannot differ.

  L28  RESOLVED by Rob's ruling: killed with a test asserting the module binds
       no type-only name at runtime. It had been `if TYPE_CHECKING:` ->
       `if not TYPE_CHECKING:`, which I called runtime-inert and ruff's business.
       Ruff does own the placement; that was never a reason the branch could not
       be observed.
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
    GroupByConfig,
    SortDirective,
    Summary,
    ViewConfig,
)
from vault_mcp.index import VaultIndex


@pytest.fixture
def idx(tmp_path):
    """A vault whose name / folder / path / frontmatter orders all differ."""
    (tmp_path / "Sub").mkdir()
    (tmp_path / "Other").mkdir()
    (tmp_path / "Zeta.md").write_text(
        "---\nzzz: b\naaa: 2\nnum: 10\n---\n# Zeta\n", encoding="utf-8"
    )
    (tmp_path / "Sub" / "Beta.md").write_text(
        "---\nzzz: c\naaa: 1\nnum: 2\n---\n# Beta\n", encoding="utf-8"
    )
    (tmp_path / "Other" / "Gamma.md").write_text(
        "---\nzzz: a\naaa: 3\nnum: 6\n---\n# Gamma\n", encoding="utf-8"
    )
    index = VaultIndex(tmp_path, ttl_seconds=9999)
    index.reindex()
    return index


def _base(views: list[ViewConfig]) -> Base:
    return Base(
        filters=None, formulas={}, views=views, raw_yaml="", line_number=1
    )


def _labels(index, prop: str, direction: str = "ASC") -> list[str]:
    view = ViewConfig(
        name="G",
        type="table",
        group_by=GroupByConfig(property=prop, direction=direction),
    )
    res = execute_base(_base([view]), index, view_name="G")
    return [g.label for g in res.groups]


def _order(index, prop: str, direction: str = "ASC") -> list[str]:
    view = ViewConfig(
        name="S",
        type="table",
        sort=[SortDirective(property=prop, direction=direction)],
    )
    res = execute_base(_base([view]), index, view_name="S")
    return [Path(n["path"]).stem for n in res.notes]


GROUPING = {
    "file.name": ["Beta", "Gamma", "Zeta"],
    "file.folder": [".", "Other", "Sub"],
    "file.path": ["Other/Gamma.md", "Sub/Beta.md", "Zeta.md"],
    "file.ext": ["md"],
    "file.zzz": ["a", "b", "c"],
}

SORTING = {
    "file.name": ["Beta", "Gamma", "Zeta"],
    "file.folder": ["Zeta", "Gamma", "Beta"],
    "file.path": ["Gamma", "Beta", "Zeta"],
    "file.zzz": ["Gamma", "Zeta", "Beta"],
    "file.aaa": ["Beta", "Zeta", "Gamma"],
}


class TestGroupingDispatch:
    """_partition_results' file.* chain — every arm, by its own value."""

    @pytest.mark.parametrize(("prop", "expected"), sorted(GROUPING.items()))
    def test_each_arm_produces_its_own_labels(self, idx, prop, expected):
        assert _labels(idx, prop) == expected

    def test_no_two_arms_produce_the_same_labels(self, idx):
        """The property the chain exists to provide. A mutated comparison that
        routes one property into another arm collapses two of these together."""
        seen = {prop: tuple(_labels(idx, prop)) for prop in GROUPING}
        assert len(set(seen.values())) == len(seen)

    def test_a_file_property_below_file_ext_still_reads_frontmatter(self, idx):
        """`file.aaa` sorts BELOW "file.ext", "file.folder", "file.name" and
        "file.path" — my first pass used `file.zzz`, which sorts ABOVE all four
        and therefore could not separate `==` from `<=` at any of them. The
        re-measure caught it: L84 was still alive."""
        assert _labels(idx, "file.aaa") == ["1", "2", "3"]
        assert _labels(idx, "file.aaa") != _labels(idx, "file.ext")
        assert _labels(idx, "file.aaa") != _labels(idx, "file.path")

    def test_an_unknown_file_property_reads_frontmatter_minus_the_prefix(
        self, idx
    ):
        """`prop[5:]` — len("file.") is 5. A [4:] slice looks up ".zzz" and a
        [6:] slice looks up "zz"; neither key exists, so both would collapse
        every note into a single "(None)" group."""
        assert _labels(idx, "file.zzz") == ["a", "b", "c"]
        assert _labels(idx, "file.zzz") != ["(None)"]


class TestSortingDispatch:
    """sort_key's file.* chain — the same shape, a different chain.

    NOT the same arms: sort_key has NO `file.ext` branch, so `file.ext` falls
    through to the frontmatter lookup. That asymmetry is pinned below.
    """

    @pytest.mark.parametrize(("prop", "expected"), sorted(SORTING.items()))
    def test_each_arm_produces_its_own_order(self, idx, prop, expected):
        assert _order(idx, prop) == expected

    def test_no_two_arms_produce_the_same_order(self, idx):
        seen = {prop: tuple(_order(idx, prop)) for prop in SORTING}
        assert len(set(seen.values())) == len(seen)

    def test_sorting_has_no_file_ext_arm_but_grouping_does(self, idx):
        """A real asymmetry between the two chains, not a bug to fix here.

        Grouping resolves file.ext to the suffix; sorting has no such branch, so
        it reads frontmatter["ext"], finds nothing, and every key is "" — a
        stable no-op sort. Pinned so neither chain silently grows to match the
        other.
        """
        assert _labels(idx, "file.ext") == ["md"]
        assert _order(idx, "file.ext") == _order(idx, "file.nonexistent")

    def test_a_frontmatter_key_below_file_path_is_not_the_path_arm(self, idx):
        """The survivor my first pass left alive at L317.

        sort_key has NO `file.ext` branch, so `file.ext` and every unknown
        `file.*` name fall through to the frontmatter lookup TOGETHER — and both
        sort below "file.path". I compared those two against each other, which
        `<=` satisfies exactly as well as `==` does, because it routes BOTH into
        the path arm and they stay equal. Separating them needs a name below
        "file.path" that resolves to REAL frontmatter and whose order differs
        from the path order.
        """
        assert _order(idx, "file.aaa") == ["Beta", "Zeta", "Gamma"]
        assert _order(idx, "file.aaa") != _order(idx, "file.path")

    def test_the_frontmatter_sort_actually_reorders(self, idx):
        """The property that makes the [5:] slice killable at all.

        A wrong slice looks up a key that does not exist, so every sort key
        becomes "" and Python's stable sort leaves the list exactly as it found
        it. That is indistinguishable from a correct sort UNLESS the correct
        order differs from the incoming one — so this asserts the reorder
        happened, not merely that some order came back.
        """
        unsorted = _order(idx, "file.nonexistent")
        assert _order(idx, "file.zzz") != unsorted

    def test_descending_reverses_ascending(self, idx):
        assert _order(idx, "file.name", "DESC") == list(
            reversed(_order(idx, "file.name", "ASC"))
        )


class TestDirectionIsExactlyDesc:
    """`direction == "DESC"` — only that literal reverses.

    Mutated to `>= "DESC"`, any direction string sorting at or above "DESC"
    reverses too. "ASC" does not (A < D), so the existing tests could not tell.
    """

    def test_an_arbitrary_direction_does_not_reverse_the_sort(self, idx):
        assert _order(idx, "file.name", "ZZZ") == _order(
            idx, "file.name", "ASC"
        )

    def test_an_arbitrary_direction_does_not_reverse_the_groups(self, idx):
        assert _labels(idx, "file.name", "ZZZ") == _labels(
            idx, "file.name", "ASC"
        )

    def test_desc_still_reverses_the_groups(self, idx):
        assert _labels(idx, "file.name", "DESC") == list(
            reversed(_labels(idx, "file.name", "ASC"))
        )


class TestSummaryFunctionDispatch:
    """Six aggregates over num = {10, 2, 6}, each a distinct number.

    count 3 · sum 18 · average 6 · min 2 · max 10 · range 8. Nothing shares a
    value, so a mutated `s.function == "sum"` that routes into another arm
    changes the answer.
    """

    def _summary(self, index, function: str, prop: str = "num"):
        view = ViewConfig(
            name="V",
            type="table",
            summaries=[Summary(name="agg", function=function, property=prop)],
        )
        return execute_base(_base([view]), index, view_name="V").summaries[
            "agg"
        ]

    @pytest.mark.parametrize(
        ("function", "expected"),
        [
            ("count", 3),
            ("sum", 18),
            ("average", 6),
            ("min", 2),
            ("max", 10),
            ("range", 8),
        ],
    )
    def test_each_aggregate_returns_its_own_value(
        self, idx, function, expected
    ):
        assert self._summary(idx, function) == expected

    def test_no_two_aggregates_agree(self, idx):
        got = {
            f: self._summary(idx, f)
            for f in ("count", "sum", "average", "min", "max", "range")
        }
        assert len(set(got.values())) == len(got)

    def test_sum_over_nothing_is_zero_and_the_rest_are_none(self, idx):
        """The `count_with_val == 0` arm: `0 if s.function == "sum" else None`.
        Sum of an empty set is 0; there is no meaningful max of nothing."""
        assert self._summary(idx, "sum", prop="absent") == 0
        assert self._summary(idx, "max", prop="absent") is None
        assert self._summary(idx, "average", prop="absent") is None


class TestUnsupportedViewTypes:
    """The two early returns the re-measure showed were still unasserted.

    `total=0` appears in THREE early returns — unknown view name, map view, and
    an unsupported type. My first pass asserted only the first, so mutating the
    map arm's literal changed a value nothing read.
    """

    def test_a_map_view_returns_nothing_with_a_warning(self, idx):
        view = ViewConfig(name="M", type="map")
        res = execute_base(_base([view]), idx, view_name="M")
        assert res.total == 0
        assert res.notes == []
        assert "map" in res.warnings[0]["reason"]

    def test_an_unsupported_view_type_returns_nothing_with_a_warning(self, idx):
        view = ViewConfig(name="B", type="board")
        res = execute_base(_base([view]), idx, view_name="B")
        assert res.total == 0
        assert "not supported" in res.warnings[0]["reason"]

    def test_every_refusal_reports_a_zero_total(self, idx):
        """All three arms agree, so no single literal can drift unnoticed."""
        for name, vtype in (("M", "map"), ("B", "board")):
            res = execute_base(
                _base([ViewConfig(name=name, type=vtype)]), idx, view_name=name
            )
            assert res.total == len(res.notes) == 0
        miss = execute_base(
            _base([ViewConfig(name="A", type="table")]), idx, view_name="nope"
        )
        assert miss.total == len(miss.notes) == 0


class TestUnrecognisedSummaryFunction:
    """An unknown function name must produce NO result — and that is the only
    input that separates `==` from `>=` / `<=` in this chain.

    FOUND BY SPOT-CHECKING, not by reading. `elif s.function == "sum"` mutated
    to `>= "sum"` survived the whole class above, because among the six valid
    names only "sum" itself satisfies `>= "sum"` — "average", "max", "min" and
    "range" all sort below it, and "count" is caught by an earlier arm. The
    mutant is equivalent ON THE VALID DOMAIN and differs only outside it.

    So the discriminator is a name that sorts OUTSIDE the valid set: "zzz" lies
    above every one of them and "aaa" below, and the correct answer for both is
    the same — the aggregate is skipped entirely and its key never appears.
    """

    def _summaries(self, index, function: str, prop: str = "num"):
        view = ViewConfig(
            name="V",
            type="table",
            summaries=[Summary(name="agg", function=function, property=prop)],
        )
        return execute_base(_base([view]), index, view_name="V").summaries

    @pytest.mark.parametrize("function", ["zzz", "aaa", "median", "stdev"])
    def test_an_unknown_function_yields_no_summary_key(self, idx, function):
        assert self._summaries(idx, function) == {}

    def test_a_name_above_sum_does_not_fall_into_the_sum_arm(self, idx):
        """ "zzz" >= "sum", so a `>=` mutant hands back 18 instead of nothing."""
        assert "agg" not in self._summaries(idx, "zzz")

    def test_a_name_below_average_does_not_fall_into_the_average_arm(self, idx):
        """ "aaa" <= "average" and <= "max", covering the `<=` mutants."""
        assert "agg" not in self._summaries(idx, "aaa")

    def test_an_unknown_function_over_an_absent_property_is_none_not_zero(
        self, idx
    ):
        """The empty-accumulator arm reads `0 if s.function == "sum" else None`.
        Mutated to `>= "sum"`, an unknown name above "sum" reports 0 — a real
        number where there was no data at all."""
        assert self._summaries(idx, "zzz", prop="absent") == {"agg": None}
        assert self._summaries(idx, "sum", prop="absent") == {"agg": 0}


class TestViewSelection:
    """The selection loop's `break`, and the miss path's total."""

    def test_the_first_matching_view_wins(self, idx):
        """`break` -> `continue` makes the LAST match win instead. Only two
        views sharing a name can tell them apart."""
        views = [
            ViewConfig(name="Dup", type="table"),
            ViewConfig(name="Dup", type="cards"),
        ]
        res = execute_base(_base(views), idx, view_name="Dup")
        assert res.view_properties["type"] == "table"

    def test_an_unknown_view_returns_zero_total(self, idx):
        res = execute_base(
            _base([ViewConfig(name="A", type="table")]), idx, view_name="nope"
        )
        assert res.total == 0
        assert res.notes == []

    def test_the_zero_total_matches_the_empty_note_list(self, idx):
        res = execute_base(
            _base([ViewConfig(name="A", type="table")]), idx, view_name="nope"
        )
        assert res.total == len(res.notes)
