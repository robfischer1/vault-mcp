"""Behavioural coverage for the Bases model — dataclass defaults and helpers.

WHY THIS FILE EXISTS. The mutation gate reported bases_model at 84.3% honest
with 11 survivors. Reading each one rather than trusting the count changed what
this file had to be, because only 8 of the 11 were test gaps:

  TWO I DECLARED UNKILLABLE, AND THAT WAS THE WRONG ANSWER. Line 152 combined
  `re.DOTALL | re.MULTILINE`; the operators got mutated to `^` and `+`, and
  since those flags are 16 and 8 — disjoint bits — all three fold to 24. The
  arithmetic was right and the conclusion was not. I documented an exemption
  and filed an upstream request for the gate to stop counting the class.

  Rob's rule: "Either you're wrong, or the code is. There is no Option C."
  The flags are now written inline as `(?sm)`, which is the same pattern with
  no BinOp in it — so there is no operator to mutate and no survivor to excuse.
  Verified equivalent (identical `.flags`, identical extractions) before the
  change, and the module now measures 100%.

  ONE WAS DEAD CODE, not an untested branch. `for pat in _TIER2_PATTERNS`
  mutated to `for pat in []` survived because every pattern in that tuple ends
  in "(" — and the very next check returns 2 for anything containing "(". The
  loop could not change an outcome. Verified by differential test over 2,628
  inputs (0 disagreements) before deleting it rather than writing a test that
  would have asserted a tautology.

WHAT THE REMAINING EIGHT HAD IN COMMON: they are VALUES nobody read. A default
of 0 mutated to 1 or -1 survives when every test constructs the object with an
explicit value; `+ 1` mutated to `| 1` survives when the only test that reads
the result happens to use an even operand, because n|1 == n+1 for every even n.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from vault_mcp.bases_model import (
    Base,
    QueryResult,
    Summary,
    _classify_formula_tier,
    _parse_summary,
    extract_base_blocks,
)


class TestDataclassDefaults:
    """The defaults themselves, which every existing test overrode.

    `Base(...)` and `QueryResult(...)` were always constructed with explicit
    values, so mutating `= 0` to `= 1` or `= -1` changed a default nobody read.
    """

    def test_a_base_defaults_to_line_number_zero(self):
        base = Base(filters=None, formulas={}, views=[])
        assert base.line_number == 0

    def test_the_unset_line_number_cannot_collide_with_a_real_one(self):
        """The INVARIANT, not the literal.

        Rob's standing objection to mutation-driven tests is that they degrade
        into "basic/obvious value comparissons" — `assert x == 0` passes whether
        or not 0 means anything. What 0 actually has to satisfy is this: block
        line numbers are 1-based (extract_base_blocks returns `count("\n") + 1`),
        so the unset default must lie OUTSIDE the range any parsed base can
        occupy. That is what rules out 1, and equally rules out -1 being fine
        by accident.
        """
        parsed = extract_base_blocks("```base\nfilters: null\n```\n")
        real_line_numbers = [line for _, line in parsed]
        assert min(real_line_numbers) >= 1

        unset = Base(filters=None, formulas={}, views=[]).line_number
        assert unset not in real_line_numbers
        assert unset < min(real_line_numbers)

    def test_a_query_result_defaults_to_total_zero(self):
        result = QueryResult(notes=[], warnings=[], view_name=None)
        assert result.total == 0

    def test_an_empty_query_result_total_matches_its_note_count(self):
        """The invariant the default exists to satisfy: a result carrying no
        notes reports a total of 0, not 1 and not -1."""
        result = QueryResult(notes=[], warnings=[], view_name=None)
        assert result.total == len(result.notes)

    def test_the_other_defaults_are_empty_containers(self):
        base = Base(filters=None, formulas={}, views=[])
        result = QueryResult(notes=[], warnings=[], view_name=None)
        assert base.summaries == []
        assert base.raw_yaml == ""
        assert result.view_properties == {}
        assert result.summaries == {}
        assert result.groups == []

    def test_default_containers_are_not_shared_between_instances(self):
        """`field(default_factory=list)` rather than a mutable default — the
        classic dataclass trap, and nothing pinned it."""
        a = Base(filters=None, formulas={}, views=[])
        b = Base(filters=None, formulas={}, views=[])
        a.summaries.append(Summary(name="x", function="count", property=None))
        assert b.summaries == []


class TestTierFallback:
    """`return 2` at the foot of _classify_formula_tier — the value, not just
    the branch.

    Reached only by an expression that is neither a simple word nor carries an
    operator. Existing tests drove the EARLIER returns, so mutating the final
    literal to 3 or 1 changed an answer nobody asserted.
    """

    @pytest.mark.parametrize(
        "expression",
        ["kebab-case", "dotted.path", "has space", "123abc", "", "   "],
    )
    def test_an_unclassifiable_expression_is_tier_two(self, expression):
        assert _classify_formula_tier(expression) == 2

    def test_the_fallback_is_two_not_one(self):
        """Pinned against the Tier-1 arm directly: a bare word IS tier 1, and
        the same string with a hyphen is NOT. A mutant returning 1 from the
        fallback collapses the distinction."""
        assert _classify_formula_tier("status") == 1
        assert _classify_formula_tier("sta-tus") == 2

    def test_the_fallback_is_two_not_three(self):
        """There is no tier 3. The evaluator dispatches on this value, so a 3
        would route to nothing."""
        assert _classify_formula_tier("kebab-case") in (1, 2)
        assert _classify_formula_tier("kebab-case") == 2

    def test_a_paren_expression_is_tier_two_without_the_pattern_list(self):
        """The `_TIER2_PATTERNS` loop was DELETED as provably redundant (every
        entry ends in "(" and the operator check below it catches "("). These
        pin that the classifications it used to make still hold."""
        for expr in (
            "html(x)",
            "if(a, b, c)",
            'note["t"].map(x => x)',
            'note["t"].join(", ")',
            'x.replace("a", "b")',
            "n.toString()",
        ):
            assert _classify_formula_tier(expr) == 2


class TestBlockLineNumberParity:
    """`count("\\n") + 1` — killed only by an ODD operand.

    n|1 == n+1 and n^1 == n+1 for every EVEN n, so a test whose block sits after
    an even number of newlines cannot tell `+` from `|` or `^`. The existing
    coverage used 4. These use 3 and 5.
    """

    def test_three_preceding_newlines_is_line_four(self):
        # Odd operand: bitwise-or leaves it at three and xor drops it to
        # two, so only real addition reaches four.
        text = "a\nb\nc\n```base\nfilters: null\n```\n"
        assert extract_base_blocks(text)[0][1] == 4

    def test_five_preceding_newlines_is_line_six(self):
        # Odd again, and a different odd: or holds at five, xor falls to
        # four, addition reaches six.
        text = "a\nb\nc\nd\ne\n```base\nfilters: null\n```\n"
        assert extract_base_blocks(text)[0][1] == 6

    def test_the_line_number_increases_by_one_per_added_line(self):
        """The relationship, pinned across parities so no single operand can
        satisfy it by coincidence."""
        seen = []
        for n in range(6):
            text = "x\n" * n + "```base\nfilters: null\n```\n"
            seen.append(extract_base_blocks(text)[0][1])
        assert seen == [1, 2, 3, 4, 5, 6]


class TestParseSummary:
    """_parse_summary had NO tests at all — not a survivor, a blank."""

    def test_a_bare_function_name_parses(self):
        s = _parse_summary("s", "count")
        assert (s.name, s.function, s.property) == ("s", "count", None)

    def test_a_function_with_a_property_parses(self):
        s = _parse_summary("s", "sum(price)")
        assert (s.function, s.property) == ("sum", "price")

    def test_surrounding_whitespace_is_stripped(self):
        assert _parse_summary("s", "  count  ").function == "count"

    def test_an_unparseable_expression_falls_back_to_count(self):
        """The `if not m` arm: a malformed expression yields a count summary
        rather than raising."""
        s = _parse_summary("s", "!!!")
        assert (s.function, s.property) == ("count", None)

    def test_the_fallback_keeps_the_caller_s_name(self):
        assert _parse_summary("mine", "!!!").name == "mine"

    def test_a_property_containing_parens_is_kept_whole(self):
        """`(.+)` is greedy to the last paren, so a nested call survives."""
        assert _parse_summary("s", "sum(f(x))").property == "f(x)"
