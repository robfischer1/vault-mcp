"""Behavioural coverage for bases_io — serialization, writing, and validation.

bases_io is the fifth critical module and had NEVER been measured on its own; a
first full run put it at 81.9% honest with 30 real survivors. The shapes are the
ones this pass has met everywhere else, plus one new family:

  YAML DUMP OPTIONS. `default_flow_style=False`, `sort_keys=False` and
  `allow_unicode=True` are each a mutation site, and flipping any of them
  changes the bytes written to a note while leaving every parse-it-back test
  green. Round-tripping is not enough — a round-trip passes whether or not key
  order was preserved. What pins them is asserting the SHAPE of the emitted
  text: block style rather than inline braces, declaration order rather than
  alphabetical, and real characters rather than escapes.

  VIEW-PROP PROMOTION, again, but with the type comparison this time. The
  predicate `(v.type == "cards" and k in _CARD_PROPS) or (v.type == "map" and
  k in _MAP_PROPS)` appears twice — once to select promoted keys and once
  negated for the leftovers — and its `==` mutants need a view type from
  OUTSIDE {cards, map} to separate. "table" sorts above both; "board" sorts
  below "cards"; both are needed.

  FORMULA PREFIX SLICING. `prop[8:]` where len("formula.") == 8, in two places.
  A [7:] or [9:] slice yields a name no formula defines, so the validator still
  reports a problem — just a differently-worded one. Only asserting the NAME in
  the message separates them.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from vault_mcp.bases_io import (
    _base_dict_to_yaml,
    _serialize_base,
    validate_base,
    write_base_to_file,
)
from vault_mcp.bases_model import Base, Summary, ViewConfig


def _base(views=None, summaries=None) -> Base:
    return Base(
        filters=None,
        formulas={},
        views=views or [],
        summaries=summaries or [],
        raw_yaml="",
        line_number=1,
    )


class TestYamlDumpOptions:
    """Each dump keyword changes the emitted TEXT, which round-trips hide."""

    def test_nested_structures_use_block_style_not_inline_braces(self):
        """`default_flow_style=False`. Flipped to True the same data emits as
        `{a: 1, b: 2}` on one line — valid YAML, unreadable in a note."""
        out = _base_dict_to_yaml({"outer": {"a": 1, "b": 2}})
        assert "{" not in out
        assert "\n" in out.strip()

    def test_key_order_is_preserved_not_alphabetised(self):
        """`sort_keys=False`. A base's keys are written in the order the
        serializer chose; sorting them reorders every file it touches."""
        out = _base_dict_to_yaml({"zebra": 1, "apple": 2, "mango": 3})
        assert out.index("zebra") < out.index("apple") < out.index("mango")

    def test_non_ascii_is_written_literally_not_escaped(self):
        """`allow_unicode=True`. Flipped to False this emits `\\u2014` escapes
        into a human-edited note."""
        out = _base_dict_to_yaml({"title": "em—dash and ünicode"})
        assert "em—dash" in out
        assert "\\u" not in out


class TestViewPropPromotion:
    """The doubled predicate: promoted keys and the negated leftovers."""

    def _view_dict(self, view: ViewConfig) -> dict[str, Any]:
        return _serialize_base(_base([view]))["views"][0]

    def test_a_cards_view_promotes_its_own_props(self):
        d = self._view_dict(
            ViewConfig(name="V", type="cards", extra={"cardSize": 200})
        )
        assert d["cardSize"] == 200
        assert "cardSize" not in d.get("extra", {})

    def test_a_map_view_promotes_its_own_props(self):
        d = self._view_dict(
            ViewConfig(name="V", type="map", extra={"latProperty": "lat"})
        )
        assert d["latProperty"] == "lat"

    @pytest.mark.parametrize("vtype", ["table", "board"])
    def test_a_type_outside_cards_and_map_promotes_nothing(self, vtype):
        """ "table" sorts ABOVE both literals and "board" sorts BELOW "cards",
        so the two together separate every ordering mutant on both comparisons.
        Neither type has an allowlist, so every key must stay nested."""
        d = self._view_dict(
            ViewConfig(
                name="V",
                type=vtype,
                extra={"cardSize": 200, "latProperty": "lat"},
            )
        )
        assert "cardSize" not in d
        assert "latProperty" not in d
        assert d["extra"] == {"cardSize": 200, "latProperty": "lat"}

    def test_a_cards_view_does_not_promote_map_props(self):
        """The `and` binds each type to its OWN allowlist; mutated to `or`, a
        cards view would promote map keys."""
        d = self._view_dict(
            ViewConfig(name="V", type="cards", extra={"latProperty": "lat"})
        )
        assert "latProperty" not in d
        assert d["extra"]["latProperty"] == "lat"

    def test_the_two_halves_partition_extra_exactly(self):
        extra = {"cardSize": 1, "latProperty": 2, "mystery": 3}
        d = self._view_dict(ViewConfig(name="V", type="cards", extra=extra))
        promoted = {k for k in extra if k in d}
        leftover = set(d.get("extra", {}))
        assert promoted | leftover == set(extra)
        assert promoted & leftover == set()


class TestSummarySerialization:
    """`f"{s.function}({s.property})" if s.property else s.function`."""

    def test_a_summary_with_a_property_renders_the_call_form(self):
        out = _serialize_base(
            _base(summaries=[Summary(name="agg", function="sum", property="n")])
        )
        assert out["summaries"]["agg"] == "sum(n)"

    def test_a_summary_without_a_property_renders_the_bare_function(self):
        """Mutated to `if not s.property`, the two forms swap: a bare count
        becomes "count(None)" and a real property loses its parentheses."""
        out = _serialize_base(
            _base(
                summaries=[Summary(name="agg", function="count", property=None)]
            )
        )
        assert out["summaries"]["agg"] == "count"

    def test_a_view_summary_follows_the_same_rule(self):
        view = ViewConfig(
            name="V",
            type="table",
            summaries=[
                Summary(name="a", function="sum", property="n"),
                Summary(name="b", function="count", property=None),
            ],
        )
        got = _serialize_base(_base([view]))["views"][0]["summaries"]
        assert got == {"a": "sum(n)", "b": "count"}


class TestAppendSeparator:
    """`if not text or text.endswith("\\n\\n")` — the `or`, and the index."""

    def test_appending_to_an_empty_file_adds_no_separator(self, tmp_path):
        target = tmp_path / "empty.md"
        target.write_text("", encoding="utf-8")
        write_base_to_file(target, {"filters": None})
        assert target.read_text(encoding="utf-8").startswith("```base")

    def test_appending_after_a_blank_line_adds_no_second_one(self, tmp_path):
        target = tmp_path / "p.md"
        target.write_text("text\n\n", encoding="utf-8")
        write_base_to_file(target, {"filters": None})
        assert "text\n\n```base" in target.read_text(encoding="utf-8")

    def test_appending_after_one_newline_adds_exactly_one_blank_line(
        self, tmp_path
    ):
        """Mutated to `and`, an empty file takes the `\\n` branch instead — and
        a file already ending in a blank line gains a second one."""
        target = tmp_path / "p.md"
        target.write_text("text\n", encoding="utf-8")
        write_base_to_file(target, {"filters": None})
        assert "text\n\n```base" in target.read_text(encoding="utf-8")

    def test_appending_after_no_newline_adds_a_blank_line(self, tmp_path):
        target = tmp_path / "p.md"
        target.write_text("text", encoding="utf-8")
        write_base_to_file(target, {"filters": None})
        assert "text\n\n```base" in target.read_text(encoding="utf-8")

    def test_an_append_reports_base_index_zero(self, tmp_path):
        """The file had no blocks, so the one just written is index 0 — not 1,
        and not -1."""
        target = tmp_path / "p.md"
        target.write_text("text\n", encoding="utf-8")
        assert write_base_to_file(target, {"filters": None})["base_index"] == 0

    def test_the_reported_index_addresses_the_block_just_written(
        self, tmp_path
    ):
        """The invariant behind the literal: the index it returns must be usable
        to rewrite that same block."""
        target = tmp_path / "p.md"
        target.write_text("text\n", encoding="utf-8")
        idx = write_base_to_file(target, {"filters": None})["base_index"]
        again = write_base_to_file(target, {"filters": "x"}, base_index=idx)
        assert again["written"] is True
        assert target.read_text(encoding="utf-8").count("```base") == 1


class TestAmbiguousTarget:
    """`if len(blocks) == 1` — one block is implicit, more than one is not."""

    def test_a_single_block_is_targeted_implicitly(self, tmp_path):
        target = tmp_path / "one.md"
        target.write_text("```base\na: 1\n```\n", encoding="utf-8")
        assert write_base_to_file(target, {"filters": None})["written"] is True

    def test_two_blocks_without_an_index_are_refused(self, tmp_path):
        """`<= 1` cannot differ here — a zero-block file returned earlier — so
        what separates it is the TWO-block case staying refused."""
        target = tmp_path / "two.md"
        target.write_text(
            "```base\na: 1\n```\n\n```base\nb: 2\n```\n", encoding="utf-8"
        )
        result = write_base_to_file(target, {"filters": None})
        assert result["written"] is False
        assert result["error"] == "ambiguous_target"
        assert result["count"] == 2


class TestValidationFormulaRefs:
    """`prop[8:]` / `col[8:]` — len("formula.") is 8, in two places."""

    def test_an_undefined_order_formula_is_named_exactly(self):
        """A [7:] slice reports ".missing" and a [9:] reports "issing"; all
        three produce an error, so only the NAME separates them."""
        r = validate_base(
            {
                "formulas": {},
                "views": [{"name": "V", "order": ["formula.missing"]}],
            }
        )
        assert r.valid is False
        assert "formula.missing" in r.errors[0]["message"]
        assert "'missing'" in r.errors[0]["message"]

    def test_a_defined_order_formula_is_accepted(self):
        r = validate_base(
            {
                "formulas": {"present": {"expression": "1", "tier": 1}},
                "views": [{"name": "V", "order": ["formula.present"]}],
            }
        )
        assert r.errors == []
        assert r.valid is True

    def test_an_undefined_sort_formula_warns_and_names_it(self):
        r = validate_base(
            {
                "formulas": {},
                "views": [
                    {"name": "V", "sort": [{"property": "formula.nope"}]}
                ],
            }
        )
        assert r.warnings
        assert "'nope'" in r.warnings[0]["message"]

    def test_a_non_formula_sort_property_is_not_checked(self):
        """`isinstance(prop, str) and prop.startswith("formula.")` — mutated to
        `or`, a plain property name is treated as a formula reference."""
        r = validate_base(
            {
                "formulas": {},
                "views": [{"name": "V", "sort": [{"property": "status"}]}],
            }
        )
        assert r.warnings == []

    def test_a_later_view_is_still_checked_after_a_skipped_one(self):
        """Both `continue`s in the validator, mutated to `break`, drop every
        later item silently."""
        r = validate_base(
            {
                "formulas": {},
                "views": [
                    "not-a-dict",
                    {"name": "V", "order": ["formula.missing"]},
                ],
            }
        )
        assert r.errors
        assert "missing" in r.errors[0]["message"]

    def test_a_later_sort_entry_survives_a_skipped_one(self):
        r = validate_base(
            {
                "formulas": {},
                "views": [
                    {
                        "name": "V",
                        "sort": ["not-a-dict", {"property": "formula.nope"}],
                    }
                ],
            }
        )
        assert r.warnings
        assert "nope" in r.warnings[0]["message"]


class TestYamlRoundTripFailure:
    """`except yaml.YAMLError` — the arm nothing had ever entered.

    A dict that round-trips is the only thing the suite had ever validated, so
    the guard's body was unexecuted. A value PyYAML cannot represent makes
    `yaml.dump` raise RepresenterError, which is a YAMLError subclass.
    """

    def test_an_unrepresentable_value_is_reported_not_raised(self):
        result = validate_base({"bad": object()})
        assert result.valid is False
        assert result.errors[0]["type"] == "invalid_yaml"
        assert "round-trip failed" in result.errors[0]["message"]

    def test_a_round_trip_failure_stops_before_the_view_checks(self):
        """The arm returns early, so a base that ALSO has an undefined formula
        reference reports only the YAML problem."""
        result = validate_base(
            {
                "bad": object(),
                "formulas": {},
                "views": [{"name": "V", "order": ["formula.missing"]}],
            }
        )
        assert len(result.errors) == 1
        assert result.errors[0]["type"] == "invalid_yaml"


class TestSpecialCharLocations:
    """The location string each warning carries.

    `"location": path or None` USED to sit here and carried a mutant. Writing a
    test for it showed the `or None` arm was unreachable: the string branch is
    only entered from the dict/list recursion, which always passes a non-empty
    path, so the top-level `path=""` never meets a string. The only way to
    exercise it was to hand validate_base a non-dict, which its signature
    forbids. Dead sub-expression, so it was deleted rather than excused.
    """

    def test_a_top_level_key_reports_a_bare_location(self):
        r = validate_base({"title": "has: a colon"})
        w = [x for x in r.warnings if x["type"] == "unquoted_special_char"]
        assert w
        assert w[0]["location"] == "title"

    def test_a_nested_key_reports_a_dotted_path(self):
        """`f"{path}.{k}" if path else k` mutated to `if not path` inverts the
        two, producing "k" at depth and "path.k" at the top."""
        r = validate_base({"outer": {"inner": "has: a colon"}})
        w = [x for x in r.warnings if x["type"] == "unquoted_special_char"]
        assert w
        assert w[0]["location"] == "outer.inner"

    def test_a_list_element_reports_its_index(self):
        r = validate_base({"items": ["fine", "has: a colon"]})
        w = [x for x in r.warnings if x["type"] == "unquoted_special_char"]
        assert w
        assert w[0]["location"] == "items[1]"
