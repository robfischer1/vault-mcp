"""Behavioural coverage for the Bases formula evaluator.

WHY THIS FILE EXISTS. The mutation gate on PR #390 reported 324 real survivors
across the Bases engine, ~55% of them in this module, and every sampled one was
code that exists verbatim on main — pre-existing gaps the file split merely made
visible. Rob ruled: close them, because the gate surfacing debt is the gate
working.

WHAT A SURVIVOR MEANT HERE. The evaluator is a dispatch tree — `isinstance`
checks over AST node types, then string comparisons over method and field names.
Almost every survivor was a comparison operator mutated (`==` -> `>=`, `>` ->
`is`) on a branch NO TEST EVER TOOK. The existing suite exercised a handful of
happy paths through `execute_base`; it never drove the individual branches, so
changing which branch a value lands in changed no outcome.

These are therefore written per BRANCH rather than per feature: each one picks
an input that can only reach the site under test, so a mutation to that site's
condition changes the answer rather than merely reaching the same result by a
different route.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from vault_mcp.bases_eval import (
    FormulaDepthError,
    FormulaError,
    FormulaEvaluator,
)


def _ev(context: dict[str, object] | None = None, depth: int = 5):
    """An evaluator over `context`, with a small depth so limits are reachable."""
    return FormulaEvaluator(context or {}, depth, 0.5)


class TestArithmetic:
    """BinOp Add — the single most-mutated line in the module.

    `return (left or 0) + (right or 0)` had mutants substituting %, |, &, - and
    `left or -1` all survive, which means no test ever added two numbers in a
    formula. Each assertion below distinguishes + from a specific substitute.
    """

    def test_adds_two_numbers(self):
        assert _ev({"a": 2, "b": 3}).evaluate("a + b") == 5

    def test_addition_is_not_modulo(self):
        # 7 + 3 == 10, 7 % 3 == 1 — kills the % mutant.
        assert _ev({"a": 7, "b": 3}).evaluate("a + b") == 10

    def test_addition_is_not_bitwise(self):
        # 6 + 3 == 9, 6 | 3 == 7, 6 & 3 == 2 — kills both bitwise mutants.
        assert _ev({"a": 6, "b": 3}).evaluate("a + b") == 9

    def test_addition_is_not_subtraction(self):
        assert _ev({"a": 10, "b": 4}).evaluate("a + b") == 14

    def test_none_operand_defaults_to_zero_not_minus_one(self):
        """`(left or 0)` — the `or -1` mutant changes this answer by one."""
        assert _ev({"a": None, "b": 5}).evaluate("a + b") == 5

    def test_string_concatenation_takes_the_string_branch(self):
        assert _ev({"a": "x", "b": "y"}).evaluate("a + b") == "xy"

    def test_list_concatenation_takes_the_list_branch(self):
        assert _ev({"a": [1], "b": [2]}).evaluate("a + b") == [1, 2]

    def test_string_plus_none_renders_empty_not_the_word_none(self):
        assert _ev({"a": "x", "b": None}).evaluate("a + b") == "x"


class TestComparisons:
    """Compare — `len(node.ops) == 1`, and the Eq/NotEq allowlist.

    THE EVALUATOR SUPPORTS ONLY `==` AND `!=`. Ordering comparisons fall
    through to the same raise as any unsupported construct — which is a real
    part of the Tier-2 restriction and, until now, untested in either direction.
    """

    def test_equality(self):
        assert _ev({"a": 5, "b": 3}).evaluate("a == b") is False
        assert _ev({"a": 3, "b": 3}).evaluate("a == b") is True

    def test_inequality(self):
        assert _ev({"a": 5, "b": 3}).evaluate("a != b") is True
        assert _ev({"a": 3, "b": 3}).evaluate("a != b") is False

    def test_eq_and_neq_are_not_each_other(self):
        """Pins the two branches against one another: on equal operands they
        must disagree, so a mutant routing Eq into the NotEq arm shows."""
        ev = _ev({"a": 4, "b": 4})
        assert ev.evaluate("a == b") is not ev.evaluate("a != b")

    @pytest.mark.parametrize("expr", ["a > b", "a < b", "a >= b", "a <= b"])
    def test_ordering_comparisons_are_refused(self, expr):
        """Not supported, by design — and nothing asserted it before."""
        with pytest.raises(FormulaError, match="Unsupported"):
            _ev({"a": 5, "b": 3}).evaluate(expr)

    def test_chained_comparison_is_refused(self):
        """`len(node.ops) == 1` — a chain has 2 ops and must not evaluate only
        the first. The `>= 1` mutant would accept it."""
        with pytest.raises(FormulaError):
            _ev({"a": 1, "b": 2, "c": 3}).evaluate("a == b == c")


class TestMethodDispatch:
    """`if method == "replace"` / `"join"` / `"toString"` — name dispatch."""

    def test_replace_substitutes(self):
        assert _ev({"s": "a-b"}).evaluate('s.replace("-", "+")') == "a+b"

    def test_replace_requires_two_arguments(self):
        with pytest.raises(FormulaError, match="2 arguments"):
            _ev({"s": "ab"}).evaluate('s.replace("a")')

    def test_join_joins_a_list(self):
        assert _ev({"xs": ["a", "b"]}).evaluate('xs.join("-")') == "a-b"

    def test_join_defaults_to_comma_space(self):
        assert _ev({"xs": ["a", "b"]}).evaluate("xs.join()") == "a, b"

    def test_tostring_stringifies(self):
        assert _ev({"n": 7}).evaluate("n.toString()") == "7"

    def test_unknown_method_raises(self):
        """The dispatch falls through to a raise; a mutated comparison that
        matched here would return a value instead."""
        with pytest.raises(FormulaError, match="Unsupported"):
            _ev({"s": "a"}).evaluate("s.frobnicate()")


class TestFileAttributes:
    """`node.value.id == "file"` — the file.* attribute branch."""

    def test_file_attribute_reads_the_prefixed_context_key(self):
        assert _ev({"file.name": "Note"}).evaluate("file.name") == "Note"

    def test_a_non_file_name_is_not_treated_as_a_file_attribute(self):
        """`== "file"` mutated to `!=` would send this down the file branch and
        look up "file.attr" instead of resolving `other`."""
        assert (
            _ev({"other": None, "file.attr": "wrong"}).evaluate("other") is None
        )


class TestDepthLimit:
    """`if self._current_depth > self.max_depth` — the boundary itself."""

    def test_nesting_at_the_limit_is_allowed(self):
        """Exactly max_depth must PASS. `>` mutated to `>=` rejects this."""
        ev = _ev({"a": 1}, depth=2)
        assert ev.evaluate('_if_(a == 1, _if_(a == 1, "y", "n"), "n")') == "y"

    def test_nesting_past_the_limit_raises(self):
        ev = _ev({"a": 1}, depth=1)
        with pytest.raises((FormulaDepthError, FormulaError)):
            ev.evaluate('_if_(a == 1, _if_(a == 1, "y", "n"), "n")')


class TestConstructRefusal:
    """Anything not in the allowlist must raise, not evaluate."""

    @pytest.mark.parametrize(
        "expr",
        [
            "[1, 2][0]",
            "{'a': 1}",
            "a if a else b",
        ],
    )
    def test_unsupported_constructs_raise(self, expr):
        with pytest.raises(FormulaError):
            _ev({"a": 1, "b": 2}).evaluate(expr)

    def test_a_name_missing_from_context_is_none(self):
        assert _ev({}).evaluate("nope") is None

    def test_lambda_is_supported_and_returns_its_node(self):
        """Lambda is NOT refused — it is the callable `.filter(...)` consumes,
        so the evaluator returns the AST node for the caller to apply."""
        import ast as _ast

        assert isinstance(_ev({}).evaluate("lambda x: x"), _ast.Lambda)
