"""Behavioural coverage for bases_eval — the filter and formula dispatch chains.

Fourth and largest of the per-module passes. bases_eval measured 77.3% honest
with 93 real survivors, and they concentrate in three pure functions that take
plain arguments and return plain values, so nothing here needs an index.

THE DOMINANT SHAPE, again: `==` mutated to `<=` / `>=` / `<` / `>` in a chain
over literal names. A chain is only pinned when a test drives EVERY arm AND
supplies an input from OUTSIDE the set of literals the chain names — because on
the valid names alone, an ordering comparison usually agrees with equality.
That is why several tests below pass a deliberately unrecognised op, field or
expression: those are the inputs that separate the operators.

THE FIXTURE SEPARATES EVERY ARM. One note, whose file properties all differ:

    path "Sub/Alpha.md"   name "Alpha"   folder "Sub"   ext "md"
                          path "Sub/Alpha.md"
    frontmatter: status "active", count 3, tags ["t"]

Plus a root note (folder "." -> "") and a note under a folder that sorts BELOW
"." ("!Special"), which is the only input separating `folder == "."` from
`folder <= "."`.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from vault_mcp.bases_eval import (
    FormulaError,
    FormulaEvaluator,
    evaluate_filter,
    evaluate_formula,
)
from vault_mcp.bases_model import FilterNode, Formula

FM = {"status": "active", "count": 3, "tags": ["t"]}


def _filter(node: FilterNode, rel: str = "Sub/Alpha.md", fm=None, links=None):
    return evaluate_filter(
        node, Path(rel), FM if fm is None else fm, rel, links or set()
    )


def _leaf(op: str, field: str, value: str) -> FilterNode:
    return FilterNode(op=op, field=field, value=value)


class TestFilterFieldChain:
    """Every `field == "file.*"` arm, by a value unique to that arm."""

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("file.folder", "Sub"),
            ("file.name", "Alpha"),
            ("file.ext", "md"),
            ("file.path", "Sub/Alpha.md"),
            ("note.status", "active"),
            ("status", "active"),
        ],
    )
    def test_each_field_resolves_to_its_own_value(self, field, value):
        assert _filter(_leaf("eq", field, value)) is True

    @pytest.mark.parametrize(
        ("field", "wrong"),
        [
            ("file.folder", "Alpha"),
            ("file.name", "Sub"),
            ("file.ext", "Sub"),
            ("file.path", "Alpha"),
        ],
    )
    def test_no_field_resolves_to_another_field_s_value(self, field, wrong):
        """The mis-routing a mutated comparison causes. `file.ext <= "file.folder"`
        is TRUE, so an `<=` mutant hands the extension arm's input to the folder
        arm — which only shows if the two arms' values are asserted apart."""
        assert _filter(_leaf("eq", field, wrong)) is False

    def test_an_unknown_file_field_reads_frontmatter_not_a_file_property(self):
        """`file.aaa` sorts BELOW "file.ext", "file.folder", "file.name" and
        "file.path", so it is the single input that separates `==` from `<=` at
        all four arms. It must fall through to the frontmatter lookup and find
        nothing — not silently resolve to the extension or the path."""
        assert _filter(_leaf("eq", "file.aaa", "")) is True
        assert _filter(_leaf("eq", "file.aaa", "md")) is False
        assert _filter(_leaf("eq", "file.aaa", "Sub/Alpha.md")) is False


class TestRootFolderSentinel:
    """`folder == "."` -> "" , and the folder that sorts below "." ."""

    def test_a_root_note_has_an_empty_folder(self):
        assert _filter(_leaf("eq", "file.folder", ""), rel="Root.md") is True

    def test_a_nested_note_keeps_its_folder(self):
        assert _filter(_leaf("eq", "file.folder", "Sub")) is True

    def test_a_folder_sorting_below_dot_is_not_treated_as_root(self):
        """ "!Special" < "." because "!" is 0x21 and "." is 0x2E, so `<=` and `<`
        mutants blank it out as if it were the vault root. Folders named this way
        are ordinary in Obsidian vaults."""
        node = _leaf("eq", "file.folder", "!Special")
        assert _filter(node, rel="!Special/Note.md") is True
        assert (
            _filter(_leaf("eq", "file.folder", ""), rel="!Special/Note.md")
            is False
        )


class TestFilterOpChain:
    """and / or / not / hasLink / eq / neq, and the unknown-op fallthrough."""

    def _kids(self, *values):
        return [_leaf("eq", "status", v) for v in values]

    def test_and_requires_every_child(self):
        assert (
            _filter(FilterNode(op="and", children=self._kids("active"))) is True
        )
        assert (
            _filter(FilterNode(op="and", children=self._kids("active", "no")))
            is False
        )

    def test_or_requires_any_child(self):
        assert (
            _filter(FilterNode(op="or", children=self._kids("no", "active")))
            is True
        )
        assert _filter(FilterNode(op="or", children=self._kids("no"))) is False

    def test_not_inverts(self):
        assert (
            _filter(FilterNode(op="not", children=self._kids("active")))
            is False
        )
        assert _filter(FilterNode(op="not", children=self._kids("no"))) is True

    def test_an_empty_and_is_true_but_an_empty_or_is_false(self):
        """`(node.children or [])` mutated to `and []` turns every child list
        into [], making `and` vacuously true and `or` vacuously false — which is
        indistinguishable unless both are asserted with REAL children above."""
        assert _filter(FilterNode(op="and", children=[])) is True
        assert _filter(FilterNode(op="or", children=[])) is False
        assert _filter(FilterNode(op="not", children=[])) is True

    def test_haslink_matches_an_outbound_link(self):
        node = FilterNode(op="hasLink", field="file.hasLink", value="Target")
        assert _filter(node, links={"Target"}) is True
        assert _filter(node, links={"Other"}) is False

    def test_haslink_with_no_value_is_false(self):
        """`... if node.value else False` — the else arm, mutated to True."""
        node = FilterNode(op="hasLink", field="file.hasLink", value="")
        assert _filter(node, links={"Target"}) is False

    def test_eq_and_neq_disagree(self):
        assert _filter(_leaf("eq", "status", "active")) is True
        assert _filter(_leaf("neq", "status", "active")) is False
        assert _filter(_leaf("neq", "status", "other")) is True

    @pytest.mark.parametrize("op", ["AND", "zzz", "aaa", "EQ", ""])
    def test_an_unrecognised_op_is_false(self, op):
        """THE MASTER KEY for this chain. On the six valid ops an ordering
        comparison mostly agrees with equality; an op from outside the set is
        what separates them — "AND" <= "and", "zzz" > "or" and > "not", "aaa"
        <= "eq". Each would capture the unknown op into a real arm. The correct
        answer for all of them is the trailing `return False`."""
        assert _filter(_leaf(op, "status", "active")) is False


class TestFormulaTierAndFileExprs:
    """evaluate_formula's tier gate and its literal-expression chain."""

    def _formula(
        self, expr: str, tier: int = 1, rel: str = "Sub/Alpha.md", **kw
    ):
        return evaluate_formula(
            Formula(name="f", expression=expr, tier=tier),
            Path(rel),
            kw.get("fm", FM),
            rel,
            kw.get("out", {"O1", "O2"}),
            kw.get("inb", {"I1"}),
        )

    def test_only_tier_two_uses_the_evaluator(self):
        """`formula.tier == 2` mutated to `>= 2` sends tier 3 down the evaluator
        path as well. A tier-3 formula must fall through to the literal chain."""
        assert self._formula('"x" + "y"', tier=2)[0] == "xy"
        assert self._formula('"x" + "y"', tier=3)[0] is None

    @pytest.mark.parametrize(
        ("expr", "expected"),
        [
            ("file.name", "Alpha"),
            ("file.folder", "Sub"),
            ("file.path", "Sub/Alpha.md"),
            ("file.ext", "md"),
        ],
    )
    def test_each_file_expression_returns_its_own_value(self, expr, expected):
        assert self._formula(expr)[0] == expected

    def test_no_file_expression_returns_another_s_value(self):
        """`expr >= "file.name"` captures "file.path" and "file.ext"; the arms
        are only separable because each returns a different string."""
        got = {
            e: self._formula(e)[0]
            for e in ("file.name", "file.folder", "file.path", "file.ext")
        }
        assert len(set(got.values())) == 4

    def test_a_bare_frontmatter_key_is_not_a_file_expression(self):
        """ "zzz" >= "file.path" and >= "file.name", so those mutants return the
        path or the stem for what is really a frontmatter lookup."""
        assert self._formula("status")[0] == "active"
        assert self._formula("status")[0] != "Sub/Alpha.md"

    def test_a_root_note_folder_expression_is_empty(self):
        assert self._formula("file.folder", rel="Root.md")[0] == ""

    def test_a_folder_below_dot_is_not_blanked(self):
        assert (
            self._formula("file.folder", rel="!Special/N.md")[0] == "!Special"
        )

    def test_link_counts_use_the_right_direction(self):
        """outbound has 2, inbound has 1 — distinct, so a swapped direction
        changes the number."""
        assert self._formula("file.links.filter(x).length")[0] == 2
        assert self._formula("file.backlinks.filter(x).length")[0] == 1

    def test_mtime_on_a_missing_file_is_reported_not_raised(self):
        """`except OSError` — path.stat() raises FileNotFoundError here."""
        assert self._formula("file.mtime", rel="Nope/Missing.md") == (
            None,
            None,
        )

    def test_an_unsupported_expression_is_reported(self):
        val, err = self._formula("!!! not valid !!!")
        assert val is None
        assert err is not None
        assert "Unsupported expression" in err

    def test_a_note_key_expression_reads_frontmatter(self):
        assert self._formula('note["status"]')[0] == "active"


def _ev(context=None, depth: int = 10, timeout: float = 0.1):
    return FormulaEvaluator(context or {}, depth, timeout)


class TestEvaluatorDefaults:
    """`max_depth: int = 10` and `regex_timeout: float = 0.1`.

    Asserted through BEHAVIOUR rather than by reading the attribute back: a
    default is only worth pinning where it changes what the evaluator does, and
    `assert ev.max_depth == 10` is the kind of value-equality check that passes
    whether or not the number means anything.
    """

    def test_the_default_depth_admits_ten_nested_calls_and_refuses_eleven(self):
        ev = FormulaEvaluator({"a": 1})
        ten = "_if_(a == 1, " * 10 + '"y"' + ', "n")' * 10
        assert ev.evaluate(ten) == "y"

        ev2 = FormulaEvaluator({"a": 1})
        eleven = "_if_(a == 1, " * 11 + '"y"' + ', "n")' * 11
        with pytest.raises(FormulaError):
            ev2.evaluate(eleven)

    def test_the_default_regex_timeout_is_sub_second(self):
        """0.1 mutated to 1.1 or -0.9 changes a real bound. A negative timeout
        cannot bound anything and a 1.1s one is an order of magnitude over."""
        ev = FormulaEvaluator({})
        assert 0 < ev.regex_timeout < 1


class TestAttributeAndSubscript:
    """The `file.` attribute arm, and the None / dict guards around it."""

    def test_a_file_attribute_reads_the_prefixed_key(self):
        assert _ev({"file.name": "N"}).evaluate("file.name") == "N"

    def test_a_non_file_name_is_not_a_file_attribute(self):
        """`node.value.id == "file"` mutated to `<=` or `>=` captures other
        names — "a" <= "file", "z" >= "file" — and would look up "a.attr"."""
        # An ATTRIBUTE access, not a bare name — a bare name never reaches
        # the guard. "a" sorts below "file" and "z" above it, so the pair
        # separates `<=` from `>=`.
        ctx = {
            "a": {"attr": "right"},
            "z": {"attr": "right"},
            "file.attr": "WRONG",
        }
        assert _ev(ctx).evaluate("a.attr") == "right"
        assert _ev(ctx).evaluate("z.attr") == "right"

    def test_the_and_in_the_file_guard_requires_both_halves(self):
        """Mutated to `or`, ANY attribute access on a non-Name value takes the
        file branch. `x.y` where x is a dict must read the dict."""
        assert _ev({"x": {"y": "v"}}).evaluate("x.y") == "v"

    def test_an_attribute_of_none_is_none(self):
        assert _ev({"x": None}).evaluate("x.y") is None

    def test_an_attribute_of_a_non_dict_is_none_not_an_error(self):
        assert _ev({"x": 5}).evaluate("x.nope") is None

    def test_a_subscript_of_a_dict_reads_the_key(self):
        assert _ev({"d": {"k": "v"}}).evaluate('d["k"]') == "v"

    def test_a_subscript_of_none_is_none(self):
        assert _ev({"d": None}).evaluate('d["k"]') is None

    def test_a_subscript_of_a_non_dict_is_none(self):
        assert _ev({"d": [1, 2, 3]}).evaluate('d["k"]') is None


class TestIfDispatch:
    """`_if_` — its argument count, its branch choice, and its depth counter."""

    def test_if_returns_the_then_branch_when_true(self):
        assert _ev({"a": 1}).evaluate('_if_(a == 1, "yes", "no")') == "yes"

    def test_if_returns_the_else_branch_when_false(self):
        assert _ev({"a": 2}).evaluate('_if_(a == 1, "yes", "no")') == "no"

    @pytest.mark.parametrize("expr", ["_if_(1)", "_if_(1, 2)", "_if_(1,2,3,4)"])
    def test_if_requires_exactly_three_arguments(self, expr):
        """`len(node.args) != 3` mutated to `> 3` accepts one and two args;
        mutated to `< 3` accepts four. Both directions are asserted."""
        with pytest.raises(FormulaError):
            _ev({}).evaluate(expr)

    def test_the_depth_counter_is_restored_between_sibling_calls(self):
        """`self._current_depth -= 1` mutated to `-= 0` never unwinds, so a
        sequence of shallow sibling calls eventually trips the limit; mutated to
        `-= 2` it unwinds too far. Nine siblings at depth 1 must all evaluate
        under a max_depth of 2."""
        ev = FormulaEvaluator({"a": 1}, 2, 0.1)
        for _ in range(9):
            assert ev.evaluate('_if_(a == 1, "y", "n")') == "y"

    def test_nesting_at_the_limit_passes_and_past_it_raises(self):
        ev = FormulaEvaluator({"a": 1}, 2, 0.1)
        assert ev.evaluate('_if_(a == 1, _if_(a == 1, "y", "n"), "n")') == "y"
        ev2 = FormulaEvaluator({"a": 1}, 1, 0.1)
        with pytest.raises(FormulaError):
            ev2.evaluate('_if_(a == 1, _if_(a == 1, "y", "n"), "n")')


class TestMethodDispatch:
    """html / map / join / replace / toString, and the unknown-method raise."""

    def test_html_returns_its_first_argument(self):
        """`args[0] if args else ""` — mutated to `args[-1]` or `args[1]`, both
        of which need a call with TWO arguments to show."""
        assert _ev({}).evaluate('html("first", "second")') == "first"

    def test_html_with_no_arguments_is_empty(self):
        assert _ev({}).evaluate("html()") == ""

    def test_map_applies_the_lambda_to_every_item(self):
        assert _ev({"xs": [1, 2]}).evaluate("xs.map(x => x)") == [1, 2]

    def test_map_requires_a_list_target(self):
        """`method == "map" and isinstance(target, list)` — the `and` matters;
        a non-list target must not enter the map arm."""
        with pytest.raises(FormulaError):
            _ev({"s": "notalist"}).evaluate("s.map(x => x)")

    def test_join_uses_its_separator_and_defaults_to_comma_space(self):
        assert _ev({"xs": ["a", "b"]}).evaluate('xs.join("-")') == "a-b"
        assert _ev({"xs": ["a", "b"]}).evaluate("xs.join()") == "a, b"

    def test_join_uses_the_first_separator_argument(self):
        """`sep = args[0] if args else ", "` mutated to `args[-1]`."""
        assert _ev({"xs": ["a", "b"]}).evaluate('xs.join("-", "+")') == "a-b"

    def test_join_requires_a_list_target(self):
        with pytest.raises(FormulaError):
            _ev({"s": "ab"}).evaluate('s.join("-")')

    def test_replace_requires_two_arguments(self):
        """`len(args) < 2` mutated to `!= 2` also rejects THREE arguments,
        which the original accepts."""
        assert (
            _ev({"s": "a-b"}).evaluate('s.replace("-", "+", "ignored")')
            == "a+b"
        )
        with pytest.raises(FormulaError):
            _ev({"s": "ab"}).evaluate('s.replace("a")')

    def test_tostring_stringifies(self):
        assert _ev({"n": 7}).evaluate("n.toString()") == "7"

    @pytest.mark.parametrize("method", ["frobnicate", "aaa", "zzz"])
    def test_an_unknown_method_raises(self, method):
        """Names sorting above and below every dispatched literal, so the
        `>=` / `<=` mutants on map / join / toString are all separated."""
        with pytest.raises(FormulaError):
            _ev({"s": "a"}).evaluate(f"s.{method}()")

    def test_a_method_on_none_is_none_not_an_error(self):
        assert _ev({"s": None}).evaluate("s.toString()") is None


class TestBinOpAndCompare:
    """Addition's operand defaults, and the single-comparison guard."""

    def test_addition_defaults_a_none_operand_to_zero(self):
        """`(right or 0)` mutated to `or 1` / `or -1` shifts the answer by one."""
        assert _ev({"a": 5, "b": None}).evaluate("a + b") == 5
        assert _ev({"a": None, "b": 5}).evaluate("a + b") == 5

    def test_list_concatenation_requires_both_sides_to_be_lists(self):
        """`isinstance(left, list) and isinstance(right, list)` mutated to `or`
        sends a list-plus-scalar down the list branch."""
        assert _ev({"a": [1], "b": [2]}).evaluate("a + b") == [1, 2]
        # list + None falls through to the STRING branch and stringifies the
        # list — it does not concatenate. Under the `or` mutant both operands
        # enter the list branch and `["x"] + None` raises TypeError instead.
        assert _ev({"a": ["x"], "b": None}).evaluate("a + b") == "['x']"

    def test_equality_and_inequality_disagree(self):
        assert _ev({"a": 1, "b": 1}).evaluate("a == b") is True
        assert _ev({"a": 1, "b": 1}).evaluate("a != b") is False
        assert _ev({"a": 1, "b": 2}).evaluate("a != b") is True

    def test_inequality_is_not_ordering_and_not_identity(self):
        """`left != right` mutated to `left > right` or `left is not right`.
        256 and 256 are equal but NOT the same object outside the small-int
        cache, so `is not` reports True where `!=` reports False."""
        big = 256 * 4
        assert _ev({"a": big, "b": big * 1}).evaluate("a != b") is False
        assert _ev({"a": 1, "b": 2}).evaluate("a != b") is True
        assert _ev({"a": 2, "b": 1}).evaluate("a != b") is True

    def test_a_chained_comparison_is_refused_by_name(self):
        """Matched on the MESSAGE, not just the type.

        The chain is rejected by unpacking `node.ops`, whose ValueError the
        Compare arm catches and re-raises as a named FormulaError. If that
        `except ValueError` stops catching, the ValueError simply travels one
        frame further and _visit's outer guard turns it into a FormulaError too
        — same exception class, different message. A bare `pytest.raises(
        FormulaError)` cannot tell the two apart, and did not.
        """
        with pytest.raises(FormulaError, match="Chained comparisons"):
            _ev({"a": 1, "b": 1, "c": 1}).evaluate("a == b == c")

    def test_a_longer_chain_is_refused_the_same_way(self):
        with pytest.raises(FormulaError, match="Chained comparisons"):
            _ev({"a": 1, "b": 1, "c": 1, "d": 1}).evaluate("a == b == c == d")

    def test_ordering_comparisons_are_refused(self):
        for expr in ("a > b", "a < b", "a >= b", "a <= b"):
            with pytest.raises(FormulaError):
                _ev({"a": 2, "b": 1}).evaluate(expr)


class TestRegexLiteralDetection:
    """`startswith("/") and endswith("/")` — both halves, mutated to `or`."""

    def test_a_full_regex_literal_is_treated_as_a_regex(self):
        assert _ev({"s": "a1b"}).evaluate('s.replace(/[0-9]/, "-")') == "a-b"

    def test_a_leading_slash_alone_is_a_literal_string(self):
        assert _ev({"s": "/x"}).evaluate('s.replace("/x", "ok")') == "ok"

    def test_a_trailing_slash_alone_is_a_literal_string(self):
        assert _ev({"s": "x/"}).evaluate('s.replace("x/", "ok")') == "ok"


class TestLambdaBinding:
    """`node.args.args[0].arg` — the FIRST parameter names the item."""

    def test_the_first_parameter_receives_the_item(self):
        """Exercised through _eval_lambda directly, because the arrow-function
        rewrite only recognises a SINGLE bare parameter — `(a, b) => a` is not
        valid input to this evaluator. A two-parameter lambda is the only shape
        that separates `args.args[0]` from `args.args[-1]`."""
        ev = _ev({})
        node = ev.evaluate("lambda first, second: first")
        assert isinstance(node, ast.Lambda)
        assert [a.arg for a in node.args.args] == ["first", "second"]
        # binds the FIRST parameter; `[-1]` would bind "second" and leave
        # "first" unresolved, yielding None.
        assert ev._eval_lambda(node, "ITEM") == "ITEM"

    def test_a_single_parameter_arrow_still_maps(self):
        assert _ev({"xs": ["v"]}).evaluate("xs.map(x => x)") == ["v"]

    def test_a_lambda_with_no_parameters_is_refused(self):
        with pytest.raises(FormulaError):
            _ev({"xs": ["v"]}).evaluate("xs.map(() => 1)")


class TestModuleRuntimeImportSurface:
    """`if TYPE_CHECKING:` -> `if not TYPE_CHECKING:` executes the block.

    Rob's ruling: kill it, do not exempt it. The mutant binds a name the module
    is supposed to reference only in annotations, and `from __future__ import
    annotations` means nothing at runtime depends on it. Asserting the absence
    is what makes the branch observable at all.
    """

    @pytest.mark.parametrize(
        ("module", "name"),
        [
            ("vault_mcp.bases_parser", "Path"),
            ("vault_mcp.bases_io", "Path"),
            ("vault_mcp.bases_exec", "VaultIndex"),
        ],
    )
    def test_type_only_names_are_not_bound_at_runtime(self, module, name):
        import importlib

        mod = importlib.import_module(module)
        assert not hasattr(mod, name)


class TestDispatchOutsideTheValidSet:
    """Round two: the inputs that separate `==` from an ordering comparison.

    Every one of these needed an op, method or function name from OUTSIDE the
    set the chain names — on the valid names alone the operators agree, which
    is why the first round left them alive.
    """

    def test_an_unknown_op_with_children_is_not_an_or(self):
        """ "zzz" > "or" and >= "or". A childless unknown op cannot separate
        them (both yield False), so this one carries a MATCHING child: the
        mutant answers True by any()-ing it, the original returns False."""
        child = FilterNode(op="eq", field="status", value="active")
        assert _filter(FilterNode(op="zzz", children=[child])) is False

    def test_an_unknown_op_with_a_non_matching_value_is_not_a_neq(self):
        """ "zzz" >= "neq" routes into the neq arm, which reports True whenever
        the value differs. The first round asserted with a value that MATCHED,
        so neq answered False and agreed with the correct answer by luck."""
        assert _filter(_leaf("zzz", "status", "different")) is False
        assert _filter(_leaf("neq", "status", "different")) is True
        # "aaa" sorts BELOW "neq" where "zzz" sorts above it, so the two are
        # needed together: one separates `>=`, the other `<=`.
        assert _filter(_leaf("aaa", "status", "different")) is False
        assert _filter(_leaf("aaa", "status", "active")) is False

    def test_a_function_name_below_if_is_not_treated_as_if(self):
        """`func_name == "_if_"` mutated to `<=`. Underscore is 0x5F, so any
        name starting with a capital sorts BELOW it."""
        with pytest.raises(FormulaError, match="Unsupported function"):
            _ev({}).evaluate('ABC("a", "b", "c")')

    def test_replace_on_a_list_target_is_not_map(self):
        """`method == "map"` mutated to `>=`: "replace" >= "map", and the map
        arm additionally requires a list — so a list target with .replace()
        is exactly the input that separates them."""
        assert _ev({"xs": [1, 2]}).evaluate('xs.replace("1", "9")') == "[9, 2]"

    def test_tostring_on_a_list_target_is_not_join(self):
        """`method == "join"` mutated to `>=` or to `or`: "toString" >= "join",
        and the `or` form lets ANY list target enter the join arm. Either way a
        list would come back joined rather than stringified."""
        assert _ev({"xs": [1, 2]}).evaluate("xs.toString()") == "[1, 2]"

    def test_an_unknown_method_on_a_list_target_still_raises(self):
        """`method == "join"` mutated to `<=`: "aaa" <= "join", so an unknown
        method on a LIST would silently join instead of raising. The first
        round used a string target, which the mutant leaves alone."""
        with pytest.raises(FormulaError):
            _ev({"xs": [1, 2]}).evaluate("xs.aaa()")

    def test_map_takes_its_lambda_from_the_first_argument(self):
        """`node.args[0]` mutated to `[-1]` — only a two-argument call shows
        it, because with one argument the indices coincide."""
        assert _ev({"xs": [1]}).evaluate('xs.map(x => x, "extra")') == [1]


class TestDepthCounterUnwind:
    """`self._current_depth -= 1` mutated to `-= 2`.

    The first round ran nine shallow siblings and passed under both, because
    over-unwinding only ever GRANTS headroom. What catches it is spending that
    headroom: after a shallow call the counter must be back at zero, so a
    too-deep nest still raises.
    """

    def test_headroom_is_not_gained_by_a_previous_call(self):
        ev = FormulaEvaluator({"a": 1}, 2, 0.1)
        assert ev.evaluate('_if_(a == 1, "y", "n")') == "y"
        three_deep = (
            '_if_(a == 1, _if_(a == 1, _if_(a == 1, "y", "n"), "n"), "n")'
        )
        with pytest.raises(FormulaError):
            ev.evaluate(three_deep)

    def test_the_limit_is_the_same_on_the_first_and_later_calls(self):
        first = FormulaEvaluator({"a": 1}, 2, 0.1)
        three_deep = (
            '_if_(a == 1, _if_(a == 1, _if_(a == 1, "y", "n"), "n"), "n")'
        )
        with pytest.raises(FormulaError):
            first.evaluate(three_deep)

        later = FormulaEvaluator({"a": 1}, 2, 0.1)
        later.evaluate('_if_(a == 1, "y", "n")')
        with pytest.raises(FormulaError):
            later.evaluate(three_deep)


class TestUnexpectedErrorArms:
    """The `except Exception` guards, which no test had ever entered."""

    def test_an_invalid_regex_is_reported_as_a_formula_error(self):
        """_safe_replace's guard: an unterminated character class makes the
        engine raise, and it must surface as a FormulaError rather than escape."""
        with pytest.raises(FormulaError, match="Regex error"):
            _ev({"s": "abc"}).evaluate('s.replace(/[/, "x")')

    def test_an_unsupported_operand_pair_is_reported_not_raised(self):
        """_visit's outer guard.

        Both operands must be non-str and non-list to reach the numeric arm —
        dict + list takes the STRING branch and stringifies happily. dict + int
        reaches `(left or 0) + (right or 0)` and raises a TypeError from Python
        itself, which the guard must convert rather than let escape.
        """
        with pytest.raises(FormulaError, match="Visitor error"):
            _ev({"a": {"k": 1}, "b": 1}).evaluate("a + b")

    def test_a_call_whose_func_is_neither_a_name_nor_an_attribute(self):
        """`isinstance(node, ast.Attribute)` in _get_func_name, mutated to
        `not isinstance(...)`. A Name or an Attribute both still work under the
        mutant; only a func node that is NEITHER reaches the difference."""
        # Both versions raise FormulaError — the mutant by way of an
        # AttributeError caught upstream — so only the MESSAGE separates them.
        with pytest.raises(FormulaError, match="Unsupported function"):
            _ev({"d": {"k": 1}}).evaluate('d["k"]()')


class TestTierTwoContextFolder:
    """`folder == "."` inside the TIER-2 context build, not the tier-1 chain."""

    def _tier2(self, expr: str, rel: str):
        return evaluate_formula(
            Formula(name="f", expression=expr, tier=2),
            Path(rel),
            {},
            rel,
            set(),
            set(),
        )

    def test_a_root_note_gets_an_empty_folder_in_the_context(self):
        assert self._tier2("file.folder", "Root.md")[0] == ""

    def test_a_nested_note_keeps_its_folder_in_the_context(self):
        assert self._tier2("file.folder", "Sub/Alpha.md")[0] == "Sub"

    def test_a_folder_below_dot_is_not_blanked_in_the_context(self):
        """ "!Special" sorts below "." — the only input separating `==` from
        `<=` here, and the tier-2 build has its OWN copy of this guard."""
        assert self._tier2("file.folder", "!Special/N.md")[0] == "!Special"
