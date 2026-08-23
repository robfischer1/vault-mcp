"""The Bases parser — YAML and markdown into the dataclasses.

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

import logging
import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path

import yaml

from vault_mcp.bases_model import (
    Base,
    FilterNode,
    Formula,
    GroupByConfig,
    ParsedFile,
    SortDirective,
    Summary,
    ViewConfig,
    _classify_formula_tier,
    _parse_summary,
    extract_base_blocks,
)

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Dataclasses
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


def _build_filter_tree(raw: object) -> FilterNode | None:
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
                    remaining_children.append(
                        FilterNode(op=k, children=child_nodes)
                    )
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


def parse_base_yaml(
    raw: dict[str, Any], yaml_text: str, line_number: int
) -> Base:
    """Parse one base's YAML dict into a Base (filters, formulas, views)."""
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

            group_by_config = None
            raw_group_by = v.get("groupBy")
            if isinstance(raw_group_by, dict):
                group_by_config = GroupByConfig(
                    property=str(raw_group_by.get("property", "")),
                    direction=str(raw_group_by.get("direction", "ASC")).upper(),
                )

            known_keys = {
                "name",
                "type",
                "filters",
                "order",
                "sort",
                "groupBy",
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
                    group_by=group_by_config,
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
    """Parse every base code-block in a markdown/`.base` file into a ParsedFile."""
    rel_path = str(file_path)
    try:
        text = file_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        return ParsedFile(
            path=rel_path,
            bases=[],
            errors=[{"line_number": 0, "message": f"Could not read file: {e}"}],
        )

    blocks = extract_base_blocks(text)
    bases: list[Base] = []
    errors: list[dict[str, Any]] = []

    if file_path.suffix == ".base" and not blocks:
        try:
            raw = yaml.safe_load(text)
            if not isinstance(raw, dict):
                errors.append(
                    {
                        "line_number": 1,
                        "message": "Base YAML must be a mapping",
                    }
                )
            else:
                bases.append(parse_base_yaml(raw, text, 1))
        except yaml.YAMLError as e:
            errors.append({"line_number": 1, "message": str(e)})
    else:
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
