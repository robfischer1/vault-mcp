"""Obsidian Bases parser, evaluator, writer, and validator."""

from __future__ import annotations

import ast
import concurrent.futures
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

# ---------------------------------------------------------------------------
# Dataclasses (T006)
# ---------------------------------------------------------------------------


@dataclass
class FilterNode:
    op: str
    field: str | None = None
    value: str | None = None
    children: list[FilterNode] | None = None


@dataclass
class Formula:
    name: str
    expression: str
    tier: int


@dataclass
class Summary:
    name: str
    function: str
    property: str | None


@dataclass
class SortDirective:
    property: str
    direction: str


@dataclass
class ViewConfig:
    name: str
    type: str
    filters: FilterNode | None = None
    order: list[str] = field(default_factory=list)
    sort: list[SortDirective] = field(default_factory=list)
    summaries: list[Summary] = field(default_factory=list)
    markers: str | None = None
    column_sizes: dict[str, int] = field(default_factory=dict)
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class Base:
    filters: FilterNode | None
    formulas: dict[str, Formula]
    views: list[ViewConfig]
    summaries: list[Summary] = field(default_factory=list)
    raw_yaml: str = ""
    line_number: int = 0


@dataclass
class ParsedFile:
    path: str
    bases: list[Base]
    errors: list[dict[str, Any]]


@dataclass
class QueryResult:
    notes: list[dict[str, Any]]
    warnings: list[dict[str, str]]
    view_name: str | None
    view_properties: dict[str, Any] = field(default_factory=dict)
    summaries: dict[str, Any] = field(default_factory=dict)
    total: int = 0


@dataclass
class ValidationResult:
    valid: bool
    errors: list[dict[str, Any]]
    warnings: list[dict[str, Any]]


# ---------------------------------------------------------------------------
# Globals & Constants (T011)
# ---------------------------------------------------------------------------

_BASE_BLOCK_RE = re.compile(r"^```base\s*\n(.*?)^```", re.DOTALL | re.MULTILINE)

# Shared executor for regex timeouts (FR-010)
_REGEX_EXECUTOR = concurrent.futures.ThreadPoolExecutor(max_workers=4)

_NOTE_KEY_RE = re.compile(r'^note\["([^"]+)"\]$')
_LINKS_FILTER_RE = re.compile(r"^file\.(links|backlinks)\.filter\(.*\)\.length$")

_SUMMARY_RE = re.compile(r"^(\w+)(?:\((.+)\))?$")


_TIER2_PATTERNS = ("html(", "if(", ".map(", ".join(", ".replace(", ".toString(")


def _classify_formula_tier(expression: str) -> int:
    """Classify formula into Tier 1 (simple) or Tier 2 (complex)."""
    # Simple Tier 1 matches
    if _NOTE_KEY_RE.match(expression):
        return 1
    if expression.startswith("file."):
        if _LINKS_FILTER_RE.match(expression):
            return 1
        if expression in ("file.name", "file.folder", "file.path", "file.ext", "file.mtime"):
            return 1

    # Check for Tier 2 markers
    for pat in _TIER2_PATTERNS:
        if pat in expression:
            return 2

    # Any operators or parentheses likely require Tier 2 evaluator
    if any(op in expression for op in ("+", "==", "!=", "(", "=>")):
        return 2

    # Fallback: simple word matches (property access) stay in Tier 1
    if re.match(r"^[a-zA-Z_]\w*$", expression):
        return 1

    return 2


def _parse_summary(name: str, expression: str) -> Summary:
    """Parse summary expression into Summary dataclass."""
    m = _SUMMARY_RE.match(expression.strip())
    if not m:
        return Summary(name=name, function="count", property=None)
    func = m.group(1)
    prop = m.group(2)
    return Summary(name=name, function=func, property=prop)


def extract_base_blocks(text: str) -> list[tuple[str, int]]:
    """Extract base fenced code blocks from markdown text.

    Returns list of (yaml_content, 1-based line_number) tuples.
    """
    results: list[tuple[str, int]] = []
    for m in _BASE_BLOCK_RE.finditer(text):
        start_offset = m.start()
        line_number = text[:start_offset].count("\n") + 1
        results.append((m.group(1), line_number))
    return results


# ---------------------------------------------------------------------------
# Parser — filter predicates (T009)
# ---------------------------------------------------------------------------

_HASLINK_RE = re.compile(r'^file\.hasLink\(\s*"([^"]+)"\s*\)$')
_COMPARISON_RE = re.compile(
    r'^((?:file\.\w+|note\["[^"]+"\]|[a-zA-Z_]\w*))\s*(==|!=)\s*"([^"]*)"$'
)


def _parse_filter_predicate(pred: str) -> FilterNode:
    pred = pred.strip()

    m = _HASLINK_RE.match(pred)
    if m:
        return FilterNode(op="hasLink", field="file.hasLink", value=m.group(1))

    m = _COMPARISON_RE.match(pred)
    if m:
        raw_field = m.group(1)
        operator = m.group(2)
        val = m.group(3)

        if raw_field.startswith('note["'):
            key = raw_field[6:-2]
            normalized_field = f"note.{key}"
        elif raw_field.startswith("file."):
            normalized_field = raw_field
        else:
            normalized_field = f"note.{raw_field}"

        op = "eq" if operator == "==" else "neq"
        return FilterNode(op=op, field=normalized_field, value=val)

    return FilterNode(op="eq", field=pred, value="")


# ---------------------------------------------------------------------------
# Parser — filter tree (T010)
# ---------------------------------------------------------------------------


def _build_filter_tree(raw: Any) -> FilterNode | None:
    if raw is None:
        return None

    if isinstance(raw, str):
        return _parse_filter_predicate(raw)

    if isinstance(raw, dict):
        for key in ("and", "or", "not"):
            if key in raw:
                children_raw = raw[key]
                if not isinstance(children_raw, list):
                    children_raw = [children_raw]
                children = []
                for item in children_raw:
                    child = _build_filter_tree(item)
                    if child is not None:
                        children.append(child)
                if children:
                    return FilterNode(op=key, children=children)
                return None

        remaining_children: list[FilterNode] = []
        for k, v in raw.items():
            if isinstance(v, list):
                child_nodes = []
                for item in v:
                    child = _build_filter_tree(item)
                    if child is not None:
                        child_nodes.append(child)
                if child_nodes:
                    remaining_children.append(FilterNode(op=k, children=child_nodes))
            else:
                child = _build_filter_tree(v)
                if child is not None:
                    remaining_children.append(child)

        if len(remaining_children) == 1:
            return remaining_children[0]
        if remaining_children:
            return FilterNode(op="and", children=remaining_children)

    return None


# ---------------------------------------------------------------------------
# Parser — YAML to dataclass (T012)
# ---------------------------------------------------------------------------


def parse_base_yaml(raw: dict[str, Any], yaml_text: str, line_number: int) -> Base:
    filters = _build_filter_tree(raw.get("filters"))

    formulas: dict[str, Formula] = {}
    raw_formulas = raw.get("formulas", {})
    if isinstance(raw_formulas, dict):
        for name, expr in raw_formulas.items():
            expr_str = str(expr)
            tier = _classify_formula_tier(expr_str)
            formulas[name] = Formula(name=name, expression=expr_str, tier=tier)

    base_summaries: list[Summary] = []
    raw_summaries = raw.get("summaries", {})
    if isinstance(raw_summaries, dict):
        for name, expr in raw_summaries.items():
            base_summaries.append(_parse_summary(name, str(expr)))

    views: list[ViewConfig] = []
    raw_views = raw.get("views", [])
    if isinstance(raw_views, list):
        for v in raw_views:
            if not isinstance(v, dict):
                continue
            sort_list: list[SortDirective] = []
            for s in v.get("sort", []) or []:
                if isinstance(s, dict):
                    sort_list.append(
                        SortDirective(
                            property=str(s.get("property", "")),
                            direction=str(s.get("direction", "ASC")).upper(),
                        )
                    )

            view_summaries: list[Summary] = []
            raw_v_summaries = v.get("summaries", {})
            if isinstance(raw_v_summaries, dict):
                for name, expr in raw_v_summaries.items():
                    view_summaries.append(_parse_summary(name, str(expr)))

            known_keys = {
                "name",
                "type",
                "filters",
                "order",
                "sort",
                "summaries",
                "markers",
                "columnSizes",
                "column_sizes",
            }
            extra = {k: v_val for k, v_val in v.items() if k not in known_keys}

            col_sizes = v.get("columnSizes", v.get("column_sizes", {}))
            if not isinstance(col_sizes, dict):
                col_sizes = {}

            views.append(
                ViewConfig(
                    name=str(v.get("name", "")),
                    type=str(v.get("type", "table")),
                    filters=_build_filter_tree(v.get("filters")),
                    order=v.get("order", []) or [],
                    sort=sort_list,
                    summaries=view_summaries,
                    markers=v.get("markers"),
                    column_sizes=col_sizes,
                    extra=extra,
                )
            )

    return Base(
        filters=filters,
        formulas=formulas,
        views=views,
        summaries=base_summaries,
        raw_yaml=yaml_text,
        line_number=line_number,
    )


# ---------------------------------------------------------------------------
# Parser — file-level (T013)
# ---------------------------------------------------------------------------


def parse_file(file_path: Path) -> ParsedFile:
    rel_path = str(file_path)
    try:
        text = file_path.read_text(encoding="utf-8")
    except Exception as e:
        return ParsedFile(
            path=rel_path,
            bases=[],
            errors=[{"line_number": 0, "message": f"Could not read file: {e}"}],
        )

    blocks = extract_base_blocks(text)
    bases: list[Base] = []
    errors: list[dict[str, Any]] = []

    for yaml_text, line_number in blocks:
        try:
            raw = yaml.safe_load(yaml_text)
        except yaml.YAMLError as e:
            errors.append({"line_number": line_number, "message": str(e)})
            continue

        if not isinstance(raw, dict):
            errors.append(
                {
                    "line_number": line_number,
                    "message": "Base YAML must be a mapping",
                }
            )
            continue

        bases.append(parse_base_yaml(raw, yaml_text, line_number))

    return ParsedFile(path=rel_path, bases=bases, errors=errors)


# ---------------------------------------------------------------------------
# Evaluator — Tier 2 Restricted (T004, T005, T006)
# ---------------------------------------------------------------------------

class FormulaError(Exception):
    """Base class for formula evaluation errors."""
    pass


class FormulaTimeoutError(FormulaError):
    """Regex evaluation timed out."""
    pass


class FormulaDepthError(FormulaError):
    """Maximum nesting depth exceeded."""
    pass


class FormulaEvaluator:
    """Restricted AST-based evaluator for Tier 2 expressions."""

    def __init__(
        self,
        context: dict[str, Any],
        max_depth: int = 10,
        regex_timeout: float = 0.1,
    ):
        self.context = context
        self.max_depth = max_depth
        self.regex_timeout = regex_timeout
        self._current_depth = 0

    def evaluate(self, expression: str) -> Any:
        # Pre-process JS syntax: if( -> _if_( , x => -> lambda x: , and /regex/ -> "/regex/"
        # We protect string literals from being corrupted.
        # Group 1: strings
        # Group 2: regexes
        # Group 3: if(
        # Group 4: var =>
        pattern = r'("[^"\\]*(?:\\.[^"\\]*)*"|\'[^\'\\]*(?:\\.[^\'\\]*)*\')|(/[^/\\\n]*(?:\\.[^/\\\n]*)*\/)|\b(if)\s*\(|(\b\w+)\s*=>'

        def replacer(m: re.Match[str]) -> str:
            if m.group(1):  # string literal
                return m.group(1)
            elif m.group(2):  # regex literal
                return f'"{m.group(2)}"'
            elif m.group(3):  # if(
                return "_if_("
            elif m.group(4):  # var =>
                return f"lambda {m.group(4)}:"
            return m.group(0)

        cleaned = re.sub(pattern, replacer, expression)

        try:
            tree = ast.parse(cleaned, mode="eval")
            return self._visit(tree.body)
        except Exception as e:
            if isinstance(e, FormulaError):
                raise
            raise FormulaError(f"Evaluation failed: {e}") from e

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
                        raise FormulaDepthError(f"Max nesting depth of {self.max_depth} exceeded")
                    try:
                        if len(node.args) != 3:
                            raise FormulaError("if() requires 3 arguments")
                        condition = self._visit(node.args[0])
                        # Eager evaluation of the chosen branch
                        if condition:
                            return self._visit(node.args[1])
                        else:
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
                            raise FormulaError("map() requires an arrow function")
                        return [self._eval_lambda(lambda_node, item) for item in target]

                    if method == "join" and isinstance(target, list):
                        sep = args[0] if args else ", "
                        return str(sep).join(str(i) for i in target)

                    if method == "replace":
                        if len(args) < 2:
                            raise FormulaError("replace() requires 2 arguments")
                        return self._safe_replace(target, args[0], args[1])

                    if method == "toString":
                        return str(target)

                raise FormulaError(f"Unsupported function or method: {func_name}")

            if isinstance(node, ast.BinOp):
                left = self._visit(node.left)
                right = self._visit(node.right)
                if isinstance(node.op, ast.Add):
                    if isinstance(left, (str, list)) or isinstance(right, (str, list)):
                        if isinstance(left, list) and isinstance(right, list):
                            return left + right
                        return str(left if left is not None else "") + str(right if right is not None else "")
                    return (left or 0) + (right or 0)

            if isinstance(node, ast.Compare):
                left = self._visit(node.left)
                if len(node.ops) == 1:
                    op = node.ops[0]
                    right = self._visit(node.comparators[0])
                    if isinstance(op, ast.Eq):
                        return left == right
                    if isinstance(op, ast.NotEq):
                        return left != right

            raise FormulaError(f"Unsupported expression construct: {type(node).__name__}")
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

    def _safe_replace(self, target: Any, pattern: Any, replacement: Any) -> str:
        target_str = str(target if target is not None else "")
        pattern_str = str(pattern)
        is_regex = pattern_str.startswith("/") and pattern_str.endswith("/")

        if is_regex:
            regex_str = pattern_str[1:-1]
            try:
                future = _REGEX_EXECUTOR.submit(re.sub, regex_str, str(replacement), target_str)
                return future.result(timeout=self.regex_timeout)
            except concurrent.futures.TimeoutError:
                raise FormulaTimeoutError(f"Regex evaluation timed out after {self.regex_timeout}s") from None
            except Exception as e:
                raise FormulaError(f"Regex error: {e}") from e
        else:
            return target_str.replace(pattern_str, str(replacement))

    def _eval_lambda(self, node: ast.Lambda, item: Any) -> Any:
        if not node.args.args:
            raise FormulaError("Lambda requires at least one argument")
        var_name = node.args.args[0].arg
        sub_context = self.context.copy()
        sub_context[var_name] = item
        evaluator = FormulaEvaluator(sub_context, self.max_depth, self.regex_timeout)
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

_NOTE_KEY_RE = re.compile(r'^note\["([^"]+)"\]$')
_LINKS_FILTER_RE = re.compile(r"^file\.(links|backlinks)\.filter\(.*\)\.length$")


def evaluate_formula(
    formula: Formula,
    path: Path,
    frontmatter: dict[str, Any],
    rel_path: str,
    outbound_links: set[str],
    inbound_links: set[str],
) -> tuple[Any, str | None]:
    expr = formula.expression

    if formula.tier == 2:
        folder = str(Path(rel_path).parent).replace("\\", "/")
        if folder == ".":
            folder = ""
        context = frontmatter.copy()
        context.update({
            "file.name": path.stem,
            "file.folder": folder,
            "file.path": rel_path,
            "file.ext": path.suffix.lstrip("."),
            "file.links": list(outbound_links),
            "file.backlinks": list(inbound_links),
        })
        # Add 'tags' if present in frontmatter for list operations
        if "tags" not in context and "tags" in frontmatter:
            context["tags"] = frontmatter["tags"]

        evaluator = FormulaEvaluator(context)
        try:
            return (evaluator.evaluate(expr), None)
        except FormulaTimeoutError as e:
            return (None, str(e))
        except FormulaDepthError as e:
            return (None, str(e))
        except FormulaError as e:
            return (None, f"Evaluation error: {e}")
        except Exception as e:
            return (None, f"Unexpected error: {e}")

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
        link_set = outbound_links if direction == "links" else inbound_links
        return (len(link_set), None)

    if re.match(r"^[a-zA-Z_]\w*$", expr):
        val = frontmatter.get(expr)
        return (val, None)

    return (None, f"Unsupported expression: {expr}")


# ---------------------------------------------------------------------------
# Evaluator — execute (T018)
# ---------------------------------------------------------------------------


def execute_base(
    base: Base,
    index: Any,
    view_name: str | None = None,
) -> QueryResult:
    from .index import VaultIndex

    idx: VaultIndex = index

    selected_view: ViewConfig | None = None
    if view_name is not None:
        for v in base.views:
            if v.name == view_name:
                selected_view = v
                break
        if selected_view is None:
            return QueryResult(
                notes=[],
                warnings=[],
                view_name=view_name,
                total=0,
            )
        if selected_view.type not in ("table", "cards"):
            return QueryResult(
                notes=[],
                warnings=[
                    {
                        "formula": "",
                        "reason": f"View type '{selected_view.type}' is not supported (table/cards only)",
                    }
                ],
                view_name=view_name,
                total=0,
            )

    view_props = {}
    if selected_view:
        view_props["type"] = selected_view.type
        if selected_view.type == "cards":
            for key in ("cardSize", "image", "imageAspectRatio", "indentProperties"):
                if key in selected_view.extra:
                    view_props[key] = selected_view.extra[key]

    merged_filter: FilterNode | None = None
    if base.filters and selected_view and selected_view.filters:
        merged_filter = FilterNode(op="and", children=[base.filters, selected_view.filters])
    elif base.filters:
        merged_filter = base.filters
    elif selected_view and selected_view.filters:
        merged_filter = selected_view.filters

    # Prepare summaries
    summaries_to_run: dict[str, Summary] = {}
    for s in base.summaries:
        summaries_to_run[s.name] = s
    if selected_view:
        for s in selected_view.summaries:
            summaries_to_run[s.name] = s

    # Initialize accumulators
    # count: int, sum: float, min: float, max: float, count_with_val: int
    accums: dict[str, dict[str, Any]] = {}
    for name, _s in summaries_to_run.items():
        accums[name] = {
            "sum": 0.0,
            "min": float("inf"),
            "max": float("-inf"),
            "count_with_val": 0,
        }

    notes: list[dict[str, Any]] = []
    warnings: list[dict[str, str]] = []
    warned_formulas: set[str] = set()

    idx._ensure_fresh()

    for path, fm, rel in idx._content:
        outbound = set(idx._outbound.get(path.stem, []))
        inbound = set(idx._inbound.get(path.stem, []))

        if merged_filter and not evaluate_filter(merged_filter, path, fm, rel, outbound):
            continue

        formula_values: dict[str, Any] = {}
        for name, formula in base.formulas.items():
            val, warning = evaluate_formula(
                formula,
                path,
                fm,
                rel,
                outbound,
                inbound,
            )
            formula_values[name] = val
            if warning and name not in warned_formulas:
                warnings.append({"formula": name, "reason": warning})
                warned_formulas.add(name)

        notes.append(
            {
                "path": rel,
                "frontmatter": fm,
                "formulas": formula_values,
            }
        )

        # Update summaries
        for name, s in summaries_to_run.items():
            if s.function == "count":
                accums[name]["count_with_val"] += 1
                continue

            # Extract property value
            val = None
            if s.property:
                if s.property.startswith("formula."):
                    fname = s.property[8:]
                    val = formula_values.get(fname)
                else:
                    val = fm.get(s.property)

            # Numeric update
            if val is not None:
                try:
                    num = float(val)
                    accums[name]["sum"] += num
                    accums[name]["count_with_val"] += 1
                    if num < accums[name]["min"]:
                        accums[name]["min"] = num
                    if num > accums[name]["max"]:
                        accums[name]["max"] = num
                except (ValueError, TypeError):
                    pass

    # Finalize summaries
    results: dict[str, Any] = {}
    for name, s in summaries_to_run.items():
        a = accums[name]
        if s.function == "count":
            results[name] = a["count_with_val"]
        elif a["count_with_val"] == 0:
            results[name] = 0 if s.function == "sum" else None
        else:
            if s.function == "sum":
                results[name] = a["sum"]
            elif s.function == "average":
                results[name] = a["sum"] / a["count_with_val"]
            elif s.function == "min":
                results[name] = a["min"]
            elif s.function == "max":
                results[name] = a["max"]
            elif s.function == "range":
                results[name] = a["max"] - a["min"]

    if selected_view and selected_view.sort:
        for sd in reversed(selected_view.sort):
            reverse = sd.direction == "DESC"
            prop = sd.property

            def sort_key(note: dict[str, Any], _prop: str = prop) -> Any:
                if _prop.startswith("formula."):
                    fname = _prop[8:]
                    val = note["formulas"].get(fname)
                elif _prop.startswith("file."):
                    if _prop == "file.name":
                        val = Path(note["path"]).stem
                    elif _prop == "file.folder":
                        val = str(Path(note["path"]).parent).replace("\\", "/")
                    elif _prop == "file.path":
                        val = note["path"]
                    else:
                        val = note["frontmatter"].get(_prop[5:])
                else:
                    val = note["frontmatter"].get(_prop)
                if val is None:
                    return ""
                return str(val)

            notes.sort(key=sort_key, reverse=reverse)

    return QueryResult(
        notes=notes,
        warnings=warnings,
        view_name=view_name,
        view_properties=view_props,
        summaries=results,
        total=len(notes),
    )


# ---------------------------------------------------------------------------
# Serializer (T022)
# ---------------------------------------------------------------------------


def _serialize_filter_node(node: FilterNode) -> dict[str, Any]:
    result: dict[str, Any] = {"op": node.op}
    if node.field is not None:
        result["field"] = node.field
    if node.value is not None:
        result["value"] = node.value
    if node.children is not None:
        result["children"] = [_serialize_filter_node(c) for c in node.children]
    return result


def _serialize_base(base: Base) -> dict[str, Any]:
    result: dict[str, Any] = {
        "line_number": base.line_number,
        "filters": _serialize_filter_node(base.filters) if base.filters else None,
        "formulas": {
            name: {"expression": f.expression, "tier": f.tier} for name, f in base.formulas.items()
        },
        "summaries": {
            s.name: f"{s.function}({s.property})" if s.property else s.function
            for s in base.summaries
        },
        "views": [
            {
                "name": v.name,
                "type": v.type,
                "filters": _serialize_filter_node(v.filters) if v.filters else None,
                "order": v.order,
                "sort": [{"property": s.property, "direction": s.direction} for s in v.sort],
                "summaries": {
                    s.name: f"{s.function}({s.property})" if s.property else s.function
                    for s in v.summaries
                },
                **({"markers": v.markers} if v.markers else {}),
                **({"column_sizes": v.column_sizes} if v.column_sizes else {}),
                **({k: val for k, val in v.extra.items() if v.type == "cards" and k in ("cardSize", "image", "imageAspectRatio", "indentProperties")}),
                **({"extra": {k: val for k, val in v.extra.items() if not (v.type == "cards" and k in ("cardSize", "image", "imageAspectRatio", "indentProperties"))}} if v.extra else {}),
            }
            for v in base.views
        ],
    }
    return result


# ---------------------------------------------------------------------------
# Writer (T027, T028)
# ---------------------------------------------------------------------------


def _base_dict_to_yaml(base_dict: dict[str, Any]) -> str:
    return yaml.dump(
        base_dict,
        default_flow_style=False,
        sort_keys=False,
        allow_unicode=True,
    )


def write_base_to_file(
    file_path: Path,
    base_dict: dict[str, Any],
    base_index: int | None = None,
) -> dict[str, Any]:
    yaml_content = _base_dict_to_yaml(base_dict)
    new_block = f"```base\n{yaml_content}```"

    if not file_path.exists():
        file_path.write_text(new_block, encoding="utf-8")
        return {"written": True, "action": "created", "base_index": 0}

    text = file_path.read_text(encoding="utf-8")
    blocks = list(_BASE_BLOCK_RE.finditer(text))

    if not blocks:
        separator = "\n" if text and not text.endswith("\n") else ""
        if text and not text.endswith("\n\n"):
            separator = "\n\n" if text.endswith("\n") else "\n\n"
        file_path.write_text(text + separator + new_block, encoding="utf-8")
        return {"written": True, "action": "appended", "base_index": 0}

    if base_index is None:
        if len(blocks) == 1:
            base_index = 0
        else:
            return {
                "written": False,
                "error": "ambiguous_target",
                "count": len(blocks),
            }

    if base_index < 0 or base_index >= len(blocks):
        return {
            "written": False,
            "error": "invalid_base_index",
            "index": base_index,
            "available": len(blocks),
        }

    match = blocks[base_index]
    before = text[: match.start()]
    after = text[match.end() :]
    file_path.write_text(before + new_block + after, encoding="utf-8")
    return {"written": True, "action": "updated", "base_index": base_index}


# ---------------------------------------------------------------------------
# Validator (T032)
# ---------------------------------------------------------------------------

_YAML_SPECIAL_CHARS = set(":{}[]#&*!|>'\"-%@`")


def validate_base(base_dict: dict[str, Any]) -> ValidationResult:
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []

    try:
        dumped = yaml.dump(base_dict, default_flow_style=False, sort_keys=False)
        yaml.safe_load(dumped)
    except yaml.YAMLError as e:
        errors.append(
            {
                "type": "invalid_yaml",
                "message": f"YAML round-trip failed: {e}",
                "location": None,
            }
        )
        return ValidationResult(valid=False, errors=errors, warnings=warnings)

    defined_formulas = set()
    raw_formulas = base_dict.get("formulas", {})
    if isinstance(raw_formulas, dict):
        defined_formulas = set(raw_formulas.keys())

    raw_views = base_dict.get("views", [])
    if isinstance(raw_views, list):
        for v in raw_views:
            if not isinstance(v, dict):
                continue
            view_name = v.get("name", "unnamed")

            for col in v.get("order", []) or []:
                if isinstance(col, str) and col.startswith("formula."):
                    formula_name = col[8:]
                    if formula_name not in defined_formulas:
                        errors.append(
                            {
                                "type": "undefined_formula_ref",
                                "message": (
                                    f"View '{view_name}' references formula.{formula_name} "
                                    f"but no formula '{formula_name}' is defined"
                                ),
                                "location": f"views[{view_name}].order",
                            }
                        )

            for s in v.get("sort", []) or []:
                if not isinstance(s, dict):
                    continue
                prop = s.get("property", "")
                if isinstance(prop, str) and prop.startswith("formula."):
                    fname = prop[8:]
                    if fname not in defined_formulas:
                        warnings.append(
                            {
                                "type": "undefined_sort_ref",
                                "message": (
                                    f"View '{view_name}' sorts by formula.{fname} "
                                    f"but no formula '{fname}' is defined"
                                ),
                                "location": f"views[{view_name}].sort",
                            }
                        )

    def _check_strings(obj: Any, path: str = "") -> None:
        if isinstance(obj, str):
            if any(c in obj for c in _YAML_SPECIAL_CHARS):
                warnings.append(
                    {
                        "type": "unquoted_special_char",
                        "message": f"Value contains YAML special characters: {obj!r}",
                        "location": path or None,
                    }
                )
        elif isinstance(obj, dict):
            for k, v in obj.items():
                _check_strings(v, f"{path}.{k}" if path else k)
        elif isinstance(obj, list):
            for i, item in enumerate(obj):
                _check_strings(item, f"{path}[{i}]")

    _check_strings(base_dict)

    return ValidationResult(
        valid=len(errors) == 0,
        errors=errors,
        warnings=warnings,
    )
