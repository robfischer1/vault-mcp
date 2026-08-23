"""Behavioural coverage for Bases serialization and file writing.

Round two of closing the mutation survivors (see test_bases_eval.py's header for
why). After the first 68 tests took the honest score 66.5% -> 76.1%, bases_io
was the largest remaining cluster at 64 survivors — because almost nothing here
had ever been asserted at all.

TWO SHAPES DOMINATE:

  VIEW-PROP PROMOTION (22 survivors). `_serialize_base` splits a view's `extra`
  in two: keys in the type's allowlist are promoted to TOP-LEVEL view keys, the
  rest stay nested under "extra". The predicate
  `(v.type == "cards" and k in _CARD_PROPS) or (v.type == "map" and k in
  _MAP_PROPS)` appears TWICE — once to select the promoted keys, once negated to
  select the leftovers — so every mutation of either half survived while nothing
  asserted where a key landed.

  WRITER ACTIONS (17 survivors). `write_base_to_file` returns a different
  `action` for each of four paths — created / appended / updated / refused — and
  the tests only ever checked `written`. Each string, each index bound, and the
  blank-line separator logic were all unasserted.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from vault_mcp.bases_io import (
    _serialize_base,
    _serialize_filter_node,
    write_base_to_file,
)
from vault_mcp.bases_model import Base, FilterNode, ViewConfig


def _base(views: list[ViewConfig] | None = None) -> Base:
    return Base(
        filters=None,
        formulas={},
        views=views or [],
        raw_yaml="",
        line_number=1,
    )


def _view_dict(view: ViewConfig) -> dict[str, Any]:
    return _serialize_base(_base([view]))["views"][0]


class TestViewPropPromotion:
    """Which `extra` keys are promoted to top level, and which stay nested."""

    def test_cards_promotes_its_own_props(self):
        d = _view_dict(
            ViewConfig(name="V", type="cards", extra={"cardSize": 200})
        )
        assert d["cardSize"] == 200
        assert "cardSize" not in d.get("extra", {})

    def test_cards_does_not_promote_a_map_prop(self):
        """The `and` binds the TYPE to its own allowlist. A mutant dropping the
        type check would promote map props onto a cards view."""
        d = _view_dict(ViewConfig(name="V", type="cards", extra={"lat": 1.0}))
        assert "lat" not in d
        assert d["extra"]["lat"] == 1.0

    def test_a_table_view_promotes_nothing(self):
        """Neither allowlist applies, so every key stays nested."""
        d = _view_dict(
            ViewConfig(
                name="V", type="table", extra={"cardSize": 200, "lat": 1.0}
            )
        )
        assert "cardSize" not in d
        assert "lat" not in d
        assert d["extra"] == {"cardSize": 200, "lat": 1.0}

    def test_an_unknown_key_stays_nested_even_on_cards(self):
        d = _view_dict(
            ViewConfig(
                name="V", type="cards", extra={"cardSize": 200, "mystery": "x"}
            )
        )
        assert d["cardSize"] == 200
        assert d["extra"] == {"mystery": "x"}

    def test_the_two_halves_partition_extra_exactly(self):
        """The promoted set and the leftover set are complements — the property
        the doubled (and negated) predicate exists to guarantee. Any mutation
        that makes the two halves disagree drops a key or duplicates one."""
        extra = {"cardSize": 200, "image": "c", "mystery": "x", "lat": 1.0}
        view = ViewConfig(name="V", type="cards", extra=extra)
        d = _view_dict(view)
        promoted = {k for k in extra if k in d}
        leftover = set(d.get("extra", {}))
        assert promoted | leftover == set(extra)
        assert promoted & leftover == set()

    def test_no_extra_means_no_extra_key(self):
        """`if v.extra else {}` — the guard that omits the key entirely."""
        assert "extra" not in _view_dict(ViewConfig(name="V", type="table"))


class TestOptionalViewKeys:
    """`**({"markers": ...} if v.markers else {})` — present only when set."""

    def test_markers_appear_when_set(self):
        assert (
            _view_dict(ViewConfig(name="V", type="table", markers="m"))[
                "markers"
            ]
            == "m"
        )

    def test_markers_are_omitted_when_unset(self):
        assert "markers" not in _view_dict(ViewConfig(name="V", type="table"))

    def test_column_sizes_appear_when_set(self):
        assert _view_dict(
            ViewConfig(name="V", type="table", column_sizes={"a": 10})
        )["column_sizes"] == {"a": 10}

    def test_column_sizes_are_omitted_when_empty(self):
        assert "column_sizes" not in _view_dict(
            ViewConfig(name="V", type="table", column_sizes={})
        )


class TestFilterNodeSerialization:
    """`if node.field is not None` / `if node.value is not None` — key presence."""

    def test_a_leaf_carries_field_and_value(self):
        d = _serialize_filter_node(
            FilterNode(op="eq", field="note.status", value="active")
        )
        assert d == {"op": "eq", "field": "note.status", "value": "active"}

    def test_a_branch_omits_field_and_value(self):
        """A node with field=None must not emit the key at all — the `is not
        None` mutants would emit `field: null`."""
        d = _serialize_filter_node(
            FilterNode(
                op="and",
                children=[FilterNode(op="eq", field="a", value="1")],
            )
        )
        assert "field" not in d
        assert "value" not in d
        assert d["op"] == "and"
        assert len(d["children"]) == 1

    def test_an_empty_string_value_is_still_emitted(self):
        """`is not None`, NOT truthiness — an empty value is a real value."""
        d = _serialize_filter_node(FilterNode(op="eq", field="a", value=""))
        assert d["value"] == ""

    def test_children_nest_recursively(self):
        d = _serialize_filter_node(
            FilterNode(
                op="or",
                children=[
                    FilterNode(op="eq", field="a", value="1"),
                    FilterNode(
                        op="and",
                        children=[FilterNode(op="eq", field="b", value="2")],
                    ),
                ],
            )
        )
        assert d["children"][1]["children"][0]["field"] == "b"


class TestWriterActions:
    """Each path returns its OWN action string, and the tests now read it."""

    def test_a_missing_file_is_created(self, tmp_path):
        target = tmp_path / "new.md"
        result = write_base_to_file(target, {"filters": None})
        assert result == {"written": True, "action": "created", "base_index": 0}
        assert "```base" in target.read_text(encoding="utf-8")

    def test_a_file_without_a_block_is_appended_to(self, tmp_path):
        target = tmp_path / "prose.md"
        target.write_text("# Heading\n", encoding="utf-8")
        result = write_base_to_file(target, {"filters": None})
        assert result["action"] == "appended"
        body = target.read_text(encoding="utf-8")
        assert body.startswith("# Heading")
        assert "```base" in body

    def test_appending_leaves_a_blank_line_between_prose_and_block(
        self, tmp_path
    ):
        """The separator logic: prose not ending in a blank line gets one, so
        the fence is not glued to the previous paragraph."""
        target = tmp_path / "prose.md"
        target.write_text("# Heading\n", encoding="utf-8")
        write_base_to_file(target, {"filters": None})
        assert "# Heading\n\n```base" in target.read_text(encoding="utf-8")

    def test_a_single_existing_block_is_updated_in_place(self, tmp_path):
        target = tmp_path / "one.md"
        write_base_to_file(target, {"filters": None})
        result = write_base_to_file(target, {"filters": "x"})
        assert result["action"] == "updated"
        assert result["base_index"] == 0
        assert target.read_text(encoding="utf-8").count("```base") == 1

    def test_two_blocks_without_an_index_are_refused(self, tmp_path):
        """`if len(blocks) == 1` — ambiguity must REFUSE, not guess."""
        target = tmp_path / "two.md"
        target.write_text(
            "```base\na: 1\n```\n\n```base\nb: 2\n```\n", encoding="utf-8"
        )
        result = write_base_to_file(target, {"filters": None})
        assert result["written"] is False
        assert result["error"] == "ambiguous_target"
        assert result["count"] == 2

    def test_an_explicit_index_disambiguates(self, tmp_path):
        target = tmp_path / "two.md"
        target.write_text(
            "```base\na: 1\n```\n\n```base\nb: 2\n```\n", encoding="utf-8"
        )
        result = write_base_to_file(target, {"filters": None}, base_index=1)
        assert result["action"] == "updated"
        assert result["base_index"] == 1
        # the FIRST block is untouched
        assert "a: 1" in target.read_text(encoding="utf-8")

    @pytest.mark.parametrize("bad", [-1, 2, 99])
    def test_an_out_of_range_index_is_refused(self, tmp_path, bad):
        """`base_index < 0 or base_index >= len(blocks)` — both bounds."""
        target = tmp_path / "two.md"
        target.write_text(
            "```base\na: 1\n```\n\n```base\nb: 2\n```\n", encoding="utf-8"
        )
        result = write_base_to_file(target, {"filters": None}, base_index=bad)
        assert result["written"] is False
        assert result["error"] == "invalid_base_index"
        assert result["available"] == 2

    def test_the_last_valid_index_is_accepted(self, tmp_path):
        """The boundary itself: index 1 of 2 must WORK. A `>` mutated to `>=`
        on the upper bound would refuse it."""
        target = tmp_path / "two.md"
        target.write_text(
            "```base\na: 1\n```\n\n```base\nb: 2\n```\n", encoding="utf-8"
        )
        assert (
            write_base_to_file(target, {"filters": None}, base_index=1)[
                "written"
            ]
            is True
        )

    def test_a_refused_write_does_not_touch_the_file(self, tmp_path):
        target = tmp_path / "two.md"
        original = "```base\na: 1\n```\n\n```base\nb: 2\n```\n"
        target.write_text(original, encoding="utf-8")
        write_base_to_file(target, {"filters": None}, base_index=9)
        assert target.read_text(encoding="utf-8") == original
