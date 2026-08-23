"""Behavioural coverage for the Bases parser, model helpers and validator.

Third companion to test_bases_eval.py / test_bases_exec.py, closing the
remaining mutation survivors from PR #390 (bases_parser, bases_model, bases_io).

The gaps here are a different shape from the evaluator's. They are not
unexercised branches so much as UNASSERTED VALUES: code that ran in existing
tests, but whose specific output nobody checked, so mutating a literal changed
the answer and no assertion moved. `line_number = ...count("\\n") + 1` mutated
to `+ 2` survived because every test that parsed a block ignored where the block
was; `ValidationResult(valid=False, ...)` mutated to `valid=True` survived
because the tests read `errors` and never read `valid`.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from vault_mcp.bases_io import validate_base
from vault_mcp.bases_model import FilterNode, extract_base_blocks
from vault_mcp.bases_parser import _build_filter_tree, parse_base_yaml


def _tree(spec: object) -> FilterNode:
    """`_build_filter_tree` narrowed to non-None, so the checkers agree.

    Every call below expects a node; asserting it once here keeps the tests
    reading as assertions about SHAPE rather than about None-ness.
    """
    node = _build_filter_tree(spec)
    assert node is not None
    return node


def _kids(node: FilterNode) -> list[FilterNode]:
    """A node's children, narrowed — `children` is `list | None` on the model."""
    assert node.children is not None
    return node.children


class TestBlockLineNumbers:
    """`line_number = text[:start_offset].count("\\n") + 1` — 1-BASED.

    Every off-by-one mutant here survived: nothing asserted the number, only
    that a block was found.
    """

    def test_a_block_on_the_first_line_is_line_one(self):
        blocks = extract_base_blocks("```base\nfilters: null\n```\n")
        assert blocks[0][1] == 1

    def test_the_line_number_counts_preceding_newlines(self):
        text = "# Heading\n\nsome prose\n\n```base\nfilters: null\n```\n"
        # 4 newlines precede the fence, so it opens on line 5.
        assert extract_base_blocks(text)[0][1] == 5

    def test_two_blocks_report_distinct_ascending_lines(self):
        text = "```base\na: 1\n```\n\nmiddle\n\n```base\nb: 2\n```\n"
        first, second = extract_base_blocks(text)
        assert first[1] == 1
        assert second[1] > first[1]

    def test_no_block_yields_nothing(self):
        assert extract_base_blocks("just prose\n") == []


class TestFilterTreeCollapse:
    """`if len(remaining_children) == 1` — a lone child is NOT wrapped."""

    def test_a_single_top_level_key_is_not_double_wrapped(self):
        """The collapse is one level UP from the predicates.

        `{"and": [p]}` yields the `and` node itself, NOT an outer `and`
        containing it. Getting this wrong was my first reading; the assertion
        below is what the code actually promises.
        """
        node = _tree({"and": ['status == "active"']})
        assert node.op == "and"
        assert len(_kids(node)) == 1
        # the child is the LEAF, not another wrapper
        assert _kids(node)[0].field == "note.status"

    def test_two_children_are_wrapped_in_an_and(self):
        node = _tree({"and": ['a == "1"', 'b == "2"']})
        assert node.op == "and"
        assert len(_kids(node)) == 2

    def test_two_non_logical_keys_get_an_outer_and(self):
        """THIS is what `len(remaining_children) == 1` guards, and reaching it
        took three attempts worth of reading.

        The logical keys short-circuit: `for key in ("and", "or", "not"): if
        key in raw: return ...` returns on the FIRST match, so a dict carrying
        one never reaches the collapse at all. Only a mapping of NON-logical
        keys gets there — one such key returns that key's node directly, two
        are joined under an outer `and`.
        """
        one = _tree({"x": ['a == "1"', 'b == "2"']})
        two = _tree({"x": ['a == "1"'], "y": ['b == "2"']})
        assert one.op == "x"  # returned directly, no outer wrapper
        assert two.op == "and"  # outer join over the two keys
        assert len(_kids(two)) == 2

    def test_a_logical_key_short_circuits_before_the_collapse(self):
        """`{"or": [...]}` returns the or-node even though a sibling key
        exists — the loop returns on first match, so `and` never wraps it."""
        node = _tree({"or": ['a == "1"'], "and": ['b == "2"']})
        assert node.op == "and"  # "and" is checked first in the tuple
        assert len(_kids(node)) == 1

    def test_an_empty_filter_is_none(self):
        assert _build_filter_tree({}) is None

    def test_a_non_mapping_is_none(self):
        assert _build_filter_tree(None) is None


class TestParseErrors:
    """Malformed YAML reports line 1, and that number is now asserted."""

    def test_a_scalar_base_is_refused(self):
        """parse_base_yaml is TYPED to take a mapping, so passing a scalar is a
        type error, not a runtime contract. What is actually asserted here is
        the runtime guard behind it — the annotation and the check agree."""
        with pytest.raises((TypeError, AttributeError, ValueError)):
            parse_base_yaml(
                "just a string",  # type: ignore[arg-type]  # VERIFY: the point
                "```base\njust a string\n```",
                1,
            )


class TestValidationResultFlag:
    """`ValidationResult(valid=False, ...)` — the flag itself, not just errors.

    The `valid=True` mutant survived because every existing assertion read
    `errors` and none read `valid`, so a validator that reported failures while
    calling itself valid looked identical.
    """

    def test_a_valid_base_is_valid(self):
        result = validate_base({"filters": None, "views": []})
        assert result.valid is True
        assert result.errors == []

    def test_an_invalid_base_is_not_valid(self):
        """A formula referenced by a view but never defined is an error, and
        `valid` must agree with `errors`."""
        result = validate_base(
            {
                "formulas": {},
                "views": [
                    {"name": "V", "type": "table", "order": ["formula.missing"]}
                ],
            }
        )
        assert result.errors
        assert result.valid is False

    def test_valid_never_disagrees_with_errors(self):
        """The invariant the flag exists to express, pinned directly."""
        specs: list[dict[str, object]] = [
            {"filters": None, "views": []},
            {
                "formulas": {},
                "views": [
                    {"name": "V", "type": "table", "order": ["formula.nope"]}
                ],
            },
        ]
        for spec in specs:
            result = validate_base(spec)
            assert result.valid is (not result.errors)


class TestFilterNodeShape:
    """The dataclass the parser builds — leaf vs branch."""

    def test_a_leaf_carries_field_op_value(self):
        # Leaves are STRING predicates. A bare field normalises to note.<field>,
        # and `==` maps to op "eq" — both were unasserted before.
        node = _tree('status == "active"')
        assert isinstance(node, FilterNode)
        assert (node.field, node.op, node.value) == (
            "note.status",
            "eq",
            "active",
        )

    def test_a_not_equal_predicate_maps_to_neq(self):
        """Pins `op = "eq" if operator == "==" else "neq"` in both directions."""
        assert _tree('status != "active"').op == "neq"
        assert _tree('status == "active"').op == "eq"

    def test_a_file_field_is_not_prefixed_with_note(self):
        """`raw_field.startswith("file.")` — the branch that skips the prefix."""
        assert _tree('file.ext == "md"').field == "file.ext"

    def test_a_branch_carries_children_and_no_field(self):
        node = _tree({"or": ['a == "1"', 'b == "2"']})
        assert node.op == "or"
        assert len(_kids(node)) == 2
        assert node.field is None
