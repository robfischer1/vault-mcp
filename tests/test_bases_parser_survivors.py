"""Behavioural coverage for bases_parser — the branches the happy path skips.

Second of four, one per module, closing the survivors the #5294 split surfaced.
bases_parser measured 78.4% honest with 22 real survivors.

THREE OF THE 22 I FIRST DECLARED UNKILLABLE. That was Option C, and Rob's rule
is that there is no Option C — either my analysis is wrong or the code is. All
three are now resolved in code rather than excused, and the notes below are kept
because the REASONING each site required is the useful part:

  line 81  RESOLVED: now a `_OPERATOR_OPS` lookup, so no comparison remains.
           It had been `op = "eq" if operator == "==" else "neq"`, mutated to `>=`. The
           operator comes from _COMPARISON_RE group 2, whose alternation is
           exactly `(==|!=)`. On "==" both yield "eq"; on "!=" both yield "neq"
           (because "!" is 0x21 and "=" is 0x3D, so "!=" sorts BELOW "=="). The
           regex constrains the domain to the two values on which `>=` and `==`
           agree, so no input separates them.

  line 132 RESOLVED: now unpacked as `(only_child,) = remaining_children`.
           It had been `return remaining_children[0]` mutated to `[-1]`, guarded three lines
           above by `if len(remaining_children) == 1`. With exactly one element
           those index the same object by construction.

  line 28  RESOLVED by Rob's ruling: killed with a test asserting the module
           binds no type-only name at runtime (see test_bases_eval_survivors.py,
           TestModuleRuntimeImportSurface). It had been `if TYPE_CHECKING:`
           mutated to `if not TYPE_CHECKING:`. At runtime
           TYPE_CHECKING is False, so the mutant merely executes an import the
           module never uses at runtime — `from __future__ import annotations`
           makes the annotation a string. Killing it would mean asserting on the
           module's import internals, and ruff's flake8-type-checking rules
           already own that placement — but "the linter owns it" was a
           justification for leaving a mutant alive, not a reason it could not
           be killed. It could.

WHAT THE REMAINING 19 HAVE IN COMMON: they are the ERROR and SKIP paths.
parse_file has two whole branches — the `.base` arm and the fenced-block arm —
and the suite drove valid input through one of them. Every `continue` that
skips a bad item, every line_number stamped onto an error, and both arms of the
read-failure guard were unasserted.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from vault_mcp.bases_parser import (
    _build_filter_tree,
    parse_base_yaml,
    parse_file,
)


class TestNoneChildrenAreSkipped:
    """`if child is not None` — the arm that drops an unparseable child.

    Mutated to `child is None` it appends the None and drops the real node, and
    to `not child is not None` likewise. Nothing fed the tree a child that
    parses to None, so neither changed an outcome.
    """

    def test_a_none_child_is_dropped_and_the_real_one_kept(self):
        node = _build_filter_tree({"and": [None, 'status == "active"']})
        assert node is not None
        assert node.op == "and"
        assert node.children is not None
        assert len(node.children) == 1
        assert node.children[0].field == "note.status"

    def test_every_child_being_none_yields_no_node(self):
        """`if children:` below it — an all-None list collapses to None rather
        than to an `and` node wrapping nothing."""
        assert _build_filter_tree({"and": [None, None]}) is None

    def test_children_are_real_nodes_not_nones(self):
        """The mutant that inverts the test appends None INTO children, which a
        length check alone would not notice."""
        node = _build_filter_tree({"or": [None, 'a == "1"', None, 'b == "2"']})
        assert node is not None
        assert node.children is not None
        assert all(c is not None for c in node.children)
        assert len(node.children) == 2


class TestNonLogicalKeyScalarBranch:
    """`if child is not None` at the SCALAR arm of a non-logical key.

    _build_filter_tree has THREE guards spelled identically, and they are not
    interchangeable:

      the `and`/`or`/`not` loop           — covered above
      the non-logical key, LIST value     — covered by test_bases_parse_io
      the non-logical key, SCALAR value   — nothing reached it

    I initially wrote the class above and assumed it covered all three. It did
    not: those tests drive the logical-key loop, and the mutation gate said so
    by leaving this arm alive through a full re-measure. The two arms take
    different paths for the same-looking input, which is the whole reason a
    survivor is worth reading rather than counting.
    """

    def test_a_scalar_predicate_under_a_plain_key_returns_the_leaf(self):
        """The mutant skips the real child instead of the None one, leaving
        `remaining_children` empty and returning None."""
        node = _build_filter_tree({"x": 'a == "1"'})
        assert node is not None
        assert node.field == "note.a"
        assert node.op == "eq"

    def test_a_none_scalar_alongside_a_real_one_keeps_the_real_one(self):
        node = _build_filter_tree({"x": None, "y": 'b == "2"'})
        assert node is not None
        assert node.field == "note.b"

    def test_a_lone_none_scalar_yields_no_node(self):
        assert _build_filter_tree({"x": None}) is None

    def test_a_scalar_value_discards_its_key_but_a_list_value_keeps_it(self):
        """The asymmetry the two arms encode, pinned so neither can drift into
        the other. A scalar collapses to the predicate itself; a list is wrapped
        in a node named for the key."""
        scalar = _build_filter_tree({"x": 'a == "1"'})
        listed = _build_filter_tree({"x": ['a == "1"']})
        assert scalar is not None
        assert listed is not None
        assert scalar.op == "eq"
        assert listed.op == "x"


class TestViewSkipContinues:
    """`continue` -> `break` in the views loop.

    A non-dict view is skipped and the NEXT one still parses. With `break` the
    rest of the list is silently dropped — indistinguishable unless a valid view
    follows an invalid one.
    """

    def test_a_valid_view_after_an_invalid_one_still_parses(self):
        base = parse_base_yaml(
            {"views": ["not-a-dict", {"name": "V", "type": "table"}]}, "", 1
        )
        assert [v.name for v in base.views] == ["V"]

    def test_several_invalid_views_do_not_stop_the_scan(self):
        base = parse_base_yaml(
            {
                "views": [
                    "bad",
                    None,
                    {"name": "A", "type": "table"},
                    42,
                    {"name": "B", "type": "cards"},
                ]
            },
            "",
            1,
        )
        assert [v.name for v in base.views] == ["A", "B"]


class TestColumnSizesGuard:
    """`if not isinstance(col_sizes, dict): col_sizes = {}` — both arms.

    Dropping the `not` keeps a bad value and discards a good one. Only a test
    that exercises BOTH a dict and a non-dict can tell the versions apart.
    """

    def test_a_dict_of_column_sizes_survives(self):
        base = parse_base_yaml(
            {"views": [{"name": "V", "columnSizes": {"a": 10}}]}, "", 1
        )
        assert base.views[0].column_sizes == {"a": 10}

    @pytest.mark.parametrize("bad", ["nope", 42, ["a", "b"], None])
    def test_a_non_dict_becomes_an_empty_dict(self, bad):
        base = parse_base_yaml(
            {"views": [{"name": "V", "columnSizes": bad}]}, "", 1
        )
        assert base.views[0].column_sizes == {}

    def test_the_snake_case_spelling_is_accepted_too(self):
        base = parse_base_yaml(
            {"views": [{"name": "V", "column_sizes": {"b": 5}}]}, "", 1
        )
        assert base.views[0].column_sizes == {"b": 5}


class TestUnreadableFile:
    """`except (OSError, UnicodeDecodeError)` — BOTH members, and the 0.

    Each mutant swaps one member for a class that cannot be raised here, so
    killing them needs a test that actually triggers each exception. Nothing did:
    every existing test handed parse_file a readable file.
    """

    def test_a_missing_file_is_reported_not_raised(self, tmp_path):
        result = parse_file(tmp_path / "does-not-exist.base")
        assert result.bases == []
        assert len(result.errors) == 1
        assert "Could not read file" in result.errors[0]["message"]

    def test_undecodable_bytes_are_reported_not_raised(self, tmp_path):
        """The UnicodeDecodeError arm. read_text(encoding="utf-8") raises on
        these bytes, and the guard must catch it like any other read failure."""
        target = tmp_path / "binary.base"
        target.write_bytes(b"\xff\xfe\x00\x80 not utf-8")
        result = parse_file(target)
        assert result.bases == []
        assert "Could not read file" in result.errors[0]["message"]

    def test_a_read_failure_stamps_line_number_zero(self, tmp_path):
        """0, not 1 and not -1: there is no line to point at when the file
        could not be opened, and 0 is the value that cannot be mistaken for a
        real 1-based line."""
        result = parse_file(tmp_path / "missing.base")
        assert result.errors[0]["line_number"] == 0


class TestDotBaseArm:
    """The `.base`-suffix branch: its line numbers and its suffix test."""

    def test_a_base_file_parses_at_line_one(self, tmp_path):
        target = tmp_path / "v.base"
        target.write_text("filters: null\nviews: []\n", encoding="utf-8")
        result = parse_file(target)
        assert len(result.bases) == 1
        assert result.bases[0].line_number == 1

    def test_a_non_mapping_base_file_errors_at_line_one(self, tmp_path):
        target = tmp_path / "v.base"
        target.write_text("- just\n- a list\n", encoding="utf-8")
        result = parse_file(target)
        assert result.bases == []
        assert result.errors[0]["line_number"] == 1
        assert "must be a mapping" in result.errors[0]["message"]

    def test_malformed_yaml_in_a_base_file_errors_at_line_one(self, tmp_path):
        target = tmp_path / "v.base"
        target.write_text("key: [unclosed\n", encoding="utf-8")
        result = parse_file(target)
        assert result.bases == []
        assert result.errors[0]["line_number"] == 1

    def test_the_suffix_test_is_equality_not_ordering(self, tmp_path):
        """`suffix == ".base"` mutated to `<=`.

        ".b" sorts BELOW ".base" (it is a proper prefix), so `<=` is true for it
        while `==` is not. A `.b` file holding a bare YAML mapping therefore
        parses as a base under the mutant and yields nothing under the original
        — which is the correct behaviour, since only `.base` files carry a
        bare base.
        """
        target = tmp_path / "v.b"
        target.write_text("filters: null\nviews: []\n", encoding="utf-8")
        result = parse_file(target)
        assert result.bases == []
        assert result.errors == []


class TestFencedBlockArmContinues:
    """Both `continue`s in the block loop -> `break`.

    A bad block must skip to the next one. With `break` every later block is
    dropped, so only a file whose FIRST block is bad and whose SECOND is good
    can tell them apart.
    """

    def test_a_good_block_after_malformed_yaml_still_parses(self, tmp_path):
        target = tmp_path / "notes.md"
        target.write_text(
            "```base\nkey: [unclosed\n```\n\n```base\nfilters: null\n```\n",
            encoding="utf-8",
        )
        result = parse_file(target)
        assert len(result.bases) == 1
        assert len(result.errors) == 1

    def test_a_good_block_after_a_non_mapping_block_still_parses(
        self, tmp_path
    ):
        target = tmp_path / "notes.md"
        target.write_text(
            "```base\n- a\n- b\n```\n\n```base\nfilters: null\n```\n",
            encoding="utf-8",
        )
        result = parse_file(target)
        assert len(result.bases) == 1
        assert "must be a mapping" in result.errors[0]["message"]

    def test_each_block_error_carries_its_own_line_number(self, tmp_path):
        """Not a constant: the block arm stamps the BLOCK's line, which is what
        distinguishes it from the `.base` arm's hardcoded 1."""
        target = tmp_path / "notes.md"
        target.write_text(
            "intro\n\n```base\n- a\n```\n\nmore\n\n```base\n- b\n```\n",
            encoding="utf-8",
        )
        result = parse_file(target)
        assert len(result.errors) == 2
        lines = [e["line_number"] for e in result.errors]
        assert lines[0] != lines[1]
        assert lines[0] > 1
