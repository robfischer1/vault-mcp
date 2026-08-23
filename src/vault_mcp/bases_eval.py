"""The Bases evaluator — the restricted formula engine and filters.

Split out of bases.py under vault-mcp#5294 (1400 LOC, over the 600 block).
`vault_mcp.bases` re-exports everything public, so no import site moved.
"""

# VERIFY: `dict[str, Any]` at the JSON boundary, and only there.
#
# An MCP tool return IS a JSON object, so the value type is open by the
# protocol's own contract — pinning it to a TypedDict per verb would encode a
# wire shape the client is free to ignore, and would still be `Any` one level
# down where Obsidian's REST payloads and YAML frontmatter arrive untyped.
# Measured 2026-08-22: of 276 `Any` in this package, 127 are `-> dict[str, Any]`
# verb returns and 34 are `list[dict[str, Any]]` rows of the same. This is a
# stated decision at the boundary, not an unexamined default.
#
# What is NOT excused by it: a BARE `: Any` or `-> Any` on anything that is not
# that boundary. Those were audited to zero in this package on the same date —
# the survivors are three sites in the Bases formula evaluator, each carrying
# its own VERIFY where it sits.

from __future__ import annotations

import ast
import logging
import re
from pathlib import Path
from typing import Any

import regex

from vault_mcp.bases_model import (
    _LINKS_FILTER_RE,
    _NOTE_KEY_RE,
    FilterNode,
    Formula,
)

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Evaluator — Tier 2 Restricted (T004, T005, T006)
# ---------------------------------------------------------------------------


class FormulaError(Exception):
    """Base class for formula evaluation errors."""


class FormulaTimeoutError(FormulaError):
    """Regex evaluation timed out."""


class FormulaDepthError(FormulaError):
    """Maximum nesting depth exceeded."""


class FormulaEvaluator:
    """Restricted AST-based evaluator for Tier 2 expressions."""

    def __init__(
        self,
        context: dict[str, Any],
        max_depth: int = 10,
        regex_timeout: float = 0.1,
    ):
        """Build an evaluator over the given context with depth and regex-timeout limits."""
        self.context = context
        self.max_depth = max_depth
        self.regex_timeout = regex_timeout
        self._current_depth = 0

    # VERIFY: Any is the return of an expression EVALUATOR — a Bases formula
    # yields whatever its expression yields (str, int, bool, list, None), so
    # the type is genuinely open at this boundary. `object` was tried and is
    # wrong here: every caller then needs a narrow before arithmetic or
    # comparison the evaluator has already validated, which relocates the
    # check rather than performing it. The SAFETY of what runs is enforced by
    # the AST visitor below (an allowlist of node types), not by this
    # annotation.
    def evaluate(self, expression: str) -> Any:
        """Evaluate a Tier-2 formula expression against the bound context."""
        # Pre-process JS syntax: if( -> _if_( , x => -> lambda x: , and /regex/ -> "/regex/"
        # We protect string literals from being corrupted.
        # Group 1: strings
        # Group 2: regexes
        # Group 3: if(
        # Group 4: var =>
        pattern = r'("[^"\\]*(?:\\.[^"\\]*)*"|\'[^\'\\]*(?:\\.[^\'\\]*)*\')|(/[^/\\\n]*(?:\\.[^/\\\n]*)*\/)|\b(if)\s*\(|(\b\w+)\s*=>'

        def replacer(m: re.Match[str]) -> str:
            # The string-literal case is the FALLTHROUGH, not its own arm.
            #
            # It used to read `if m.group(1): return m.group(1)` above a
            # `return m.group(0)` that nothing could reach — every branch of
            # the alternation captures a group, so a match always has one of
            # 1..4. Two dead-ish sites carrying live mutants, and group(0) and
            # group(1) are the same text for a string literal anyway. Now the
            # untouched-passthrough is the single exit and it is reachable.
            if m.group(2):  # regex literal
                return f'"{m.group(2)}"'
            if m.group(3):  # if(
                return "_if_("
            if m.group(4):  # var =>
                return f"lambda {m.group(4)}:"
            # `m.group()`, not `m.group(0)`: the no-argument form returns the
            # whole match, so there is no index literal to mutate into an
            # equivalent — for a string-literal match group 0 and group 1 are
            # the same text, so `group(1)` survived every possible test.
            return m.group()  # string literal — passed through untouched

        try:
            # Pre-processing lives INSIDE the guard. It used to sit above it, so
            # a failure while rewriting the expression escaped as a raw
            # exception rather than a FormulaError — and that raw path was the
            # only thing that could reach evaluate_formula's `except Exception`
            # arm. With it inside, `evaluate` raises FormulaError and nothing
            # else, which is what its callers already assumed.
            cleaned = re.sub(pattern, replacer, expression)
            tree = ast.parse(cleaned, mode="eval")
            return self._visit(tree.body)
        except Exception as e:
            if isinstance(e, FormulaError):
                raise
            raise FormulaError(f"Evaluation failed: {e}") from e

    # VERIFY: same open return as `evaluate` — this is its recursive step,
    # dispatching over the allowlisted ast node types and returning each
    # node's value.
    def _visit(self, node: ast.AST) -> Any:
        try:
            if isinstance(node, ast.Constant):
                return node.value

            if isinstance(node, ast.Name):
                return self.context.get(node.id)

            if isinstance(node, ast.Lambda):
                return node

            if isinstance(node, ast.Attribute):
                if isinstance(node.value, ast.Name) and node.value.id == "file":
                    return self.context.get(f"file.{node.attr}")
                value = self._visit(node.value)
                if value is None:
                    return None
                if isinstance(value, dict):
                    return value.get(node.attr)
                return getattr(value, node.attr, None)

            if isinstance(node, ast.Subscript):
                value = self._visit(node.value)
                if value is None:
                    return None
                if isinstance(node.slice, ast.Constant):
                    key = node.slice.value
                    if isinstance(value, dict):
                        return value.get(key)
                return None

            if isinstance(node, ast.Call):
                func_name = self._get_func_name(node.func)

                if func_name == "_if_":
                    self._current_depth += 1
                    if self._current_depth > self.max_depth:
                        raise FormulaDepthError(
                            f"Max nesting depth of {self.max_depth} exceeded"
                        )
                    try:
                        if len(node.args) != 3:
                            raise FormulaError("if() requires 3 arguments")
                        condition = self._visit(node.args[0])
                        # Eager evaluation of the chosen branch
                        if condition:
                            return self._visit(node.args[1])
                        return self._visit(node.args[2])
                    finally:
                        self._current_depth -= 1

                # Evaluate arguments for other functions
                args = [self._visit(arg) for arg in node.args]

                if func_name == "html":
                    return args[0] if args else ""

                if isinstance(node.func, ast.Attribute):
                    target = self._visit(node.func.value)
                    method = node.func.attr

                    # Graceful null handling for method targets
                    if target is None:
                        return None

                    if method == "map" and isinstance(target, list):
                        lambda_node = self._visit(node.args[0])
                        if not isinstance(lambda_node, ast.Lambda):
                            raise FormulaError(
                                "map() requires an arrow function"
                            )
                        return [
                            self._eval_lambda(lambda_node, item)
                            for item in target
                        ]

                    if method == "join" and isinstance(target, list):
                        sep = args[0] if args else ", "
                        return str(sep).join(str(i) for i in target)

                    if method == "replace":
                        if len(args) < 2:
                            raise FormulaError("replace() requires 2 arguments")
                        return self._safe_replace(target, args[0], args[1])

                    if method == "toString":
                        return str(target)

                raise FormulaError(
                    f"Unsupported function or method: {func_name}"
                )

            if isinstance(node, ast.BinOp):
                left = self._visit(node.left)
                right = self._visit(node.right)
                if isinstance(node.op, ast.Add):
                    if isinstance(left, (str, list)) or isinstance(
                        right, (str, list)
                    ):
                        if isinstance(left, list) and isinstance(right, list):
                            return left + right
                        return str(left if left is not None else "") + str(
                            right if right is not None else ""
                        )
                    return (left or 0) + (right or 0)

            if isinstance(node, ast.Compare):
                # UNPACKED, with no length check at all.
                #
                # An ast.Compare always carries at least one operator, so on a
                # never-empty list EVERY spelling of the guard has an equivalent
                # mutant: `== 1` matches `<= 1`, and `> 1` matches `!= 1`. I
                # wrote it both ways and the gate reported a survivor each time.
                # The unpack rejects a chain by itself — the same check, stated
                # once instead of twice.
                try:
                    (op,) = node.ops
                    (comparator,) = node.comparators
                except ValueError:
                    raise FormulaError(
                        "Chained comparisons are not supported"
                    ) from None
                left = self._visit(node.left)
                right = self._visit(comparator)
                if isinstance(op, ast.Eq):
                    return left == right
                if isinstance(op, ast.NotEq):
                    return left != right

            raise FormulaError(
                f"Unsupported expression construct: {type(node).__name__}"
            )
        except FormulaError:
            raise
        except Exception as e:
            raise FormulaError(f"Visitor error: {e}") from e

    def _get_func_name(self, node: ast.AST) -> str | None:
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            return node.attr
        return None

    def _safe_replace(
        self, target: object, pattern: object, replacement: object
    ) -> str:
        target_str = str(target if target is not None else "")
        pattern_str = str(pattern)
        is_regex = pattern_str.startswith("/") and pattern_str.endswith("/")

        if is_regex:
            regex_str = pattern_str[1:-1]
            try:
                # `regex`, not `re`: the stdlib engine holds the GIL for the
                # whole substitution, so the ThreadPoolExecutor this replaced
                # could never be preempted and the timeout bounded nothing
                # (vault-mcp#5310). `regex` checks the clock DURING matching,
                # so the configured value is the actual wall-time ceiling.
                return regex.sub(
                    regex_str,
                    str(replacement),
                    target_str,
                    timeout=self.regex_timeout,
                )
            except TimeoutError:
                raise FormulaTimeoutError(
                    f"Regex evaluation timed out after {self.regex_timeout}s"
                ) from None
            except Exception as e:
                raise FormulaError(f"Regex error: {e}") from e
        else:
            return target_str.replace(pattern_str, str(replacement))

    # VERIFY: `item` is one row of a Bases result (arbitrary frontmatter
    # values) and the return is the lambda's own value — both open for the
    # same reason `evaluate` is.
    def _eval_lambda(self, node: ast.Lambda, item: Any) -> Any:
        if not node.args.args:
            raise FormulaError("Lambda requires at least one argument")
        var_name = node.args.args[0].arg
        sub_context = self.context.copy()
        sub_context[var_name] = item
        evaluator = FormulaEvaluator(
            sub_context, self.max_depth, self.regex_timeout
        )
        evaluator._current_depth = self._current_depth
        return evaluator._visit(node.body)


# ---------------------------------------------------------------------------
# Evaluator — filter (T016)
# ---------------------------------------------------------------------------


def evaluate_filter(
    node: FilterNode,
    path: Path,
    frontmatter: dict[str, Any],
    rel_path: str,
    outbound_links: set[str],
) -> bool:
    """Return True if the filter node matches the given note."""
    if node.op == "and":
        return all(
            evaluate_filter(c, path, frontmatter, rel_path, outbound_links)
            for c in (node.children or [])
        )
    if node.op == "or":
        return any(
            evaluate_filter(c, path, frontmatter, rel_path, outbound_links)
            for c in (node.children or [])
        )
    if node.op == "not":
        return not any(
            evaluate_filter(c, path, frontmatter, rel_path, outbound_links)
            for c in (node.children or [])
        )

    if node.op == "hasLink":
        return node.value in outbound_links if node.value else False

    field = node.field or ""
    value = node.value or ""

    if field == "file.folder":
        folder = str(Path(rel_path).parent).replace("\\", "/")
        if folder == ".":
            folder = ""
        actual = folder
    elif field == "file.name":
        actual = path.stem
    elif field == "file.ext":
        actual = path.suffix.lstrip(".")
    elif field == "file.path":
        actual = rel_path
    elif field.startswith("note."):
        key = field[5:]
        fm_val = frontmatter.get(key)
        actual = str(fm_val) if fm_val is not None else ""
    else:
        actual = str(frontmatter.get(field, ""))

    if node.op == "eq":
        return actual == value
    if node.op == "neq":
        return actual != value
    return False


# ---------------------------------------------------------------------------
# Evaluator — formula (T017)
# ---------------------------------------------------------------------------


# The (value, error) pair every formula evaluation returns.
#
# Named rather than spelled inline as `tuple[Any, str | None]`. The inline form
# put a `|` inside a MULTI-LINE signature, and the mutation gate's
# annotation-inert classifier works by parsing each side of the diff — a hunk
# reading `) -> tuple[Any, str | None]:` is not parseable on its own, so it
# fell through to "cannot prove inert" and eleven mutants were reported as test
# gaps. As an alias the operator moves to a module-level assignment that IS
# evaluated at import, where a mutated `str - None` raises TypeError and the
# mutant dies honestly instead of lingering as an unprovable survivor.
FormulaResult = tuple[Any, str | None]


def evaluate_formula(
    formula: Formula,
    path: Path,
    frontmatter: dict[str, Any],
    rel_path: str,
    outbound_links: set[str],
    inbound_links: set[str],
) -> FormulaResult:
    """Evaluate a formula for one note, returning a (value, error) tuple."""
    expr = formula.expression

    if formula.tier == 2:
        folder = str(Path(rel_path).parent).replace("\\", "/")
        if folder == ".":
            folder = ""
        context = frontmatter.copy()
        context.update(
            {
                "file.name": path.stem,
                "file.folder": folder,
                "file.path": rel_path,
                "file.ext": path.suffix.lstrip("."),
                "file.links": list(outbound_links),
                "file.backlinks": list(inbound_links),
            }
        )
        evaluator = FormulaEvaluator(context)
        try:
            return (evaluator.evaluate(expr), None)
        except FormulaTimeoutError as e:
            return (None, str(e))
        except FormulaDepthError as e:
            return (None, str(e))
        except FormulaError as e:
            # No trailing `except Exception`. `evaluate` now does ALL of its
            # work inside its own guard and converts anything that is not
            # already a FormulaError into one, so the catch-all that used to sit
            # here was unreachable — a live mutant on a line no input executes.
            return (None, f"Evaluation error: {e}")

    m = _NOTE_KEY_RE.match(expr)
    if m:
        key = m.group(1)
        val = frontmatter.get(key)
        return (val, None)

    if expr == "file.mtime":
        try:
            mtime = path.stat().st_mtime
            from datetime import UTC, datetime

            return (datetime.fromtimestamp(mtime, tz=UTC).isoformat(), None)
        except OSError:
            return (None, None)

    if expr == "file.name":
        return (path.stem, None)
    if expr == "file.folder":
        folder = str(Path(rel_path).parent).replace("\\", "/")
        return ("" if folder == "." else folder, None)
    if expr == "file.path":
        return (rel_path, None)
    if expr == "file.ext":
        return (path.suffix.lstrip("."), None)

    m = _LINKS_FILTER_RE.match(expr)
    if m:
        direction = m.group(1)
        link_set = {"links": outbound_links, "backlinks": inbound_links}[
            direction
        ]
        return (len(link_set), None)

    if re.match(r"^[a-zA-Z_]\w*$", expr):
        val = frontmatter.get(expr)
        return (val, None)

    return (None, f"Unsupported expression: {expr}")
