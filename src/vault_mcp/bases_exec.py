"""Base execution — run a view against the index and shape the result.

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
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .index import VaultIndex

from vault_mcp.bases_eval import (
    evaluate_filter,
    evaluate_formula,
)
from vault_mcp.bases_model import (
    Base,
    FilterNode,
    GroupByConfig,
    GroupResult,
    QueryResult,
    Summary,
    ViewConfig,
)

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Evaluator — execute (T018)
# ---------------------------------------------------------------------------


# Which `extra` keys each view type promotes to top-level view properties.
#
# This was `if selected_view.type == "cards":`, a mutation site whose `<=`
# variant agrees with `==` on the only two types that can reach it — the guard
# above returns early for anything but table/cards. A lookup keyed by type has
# no operator to mutate, and it is where a third view type would be added.
_PROMOTED_VIEW_KEYS: dict[str, tuple[str, ...]] = {
    "cards": ("cardSize", "image", "imageAspectRatio", "indentProperties"),
    "table": (),
}


def _partition_results(
    notes: list[dict[str, Any]],
    config: GroupByConfig,
) -> list[GroupResult]:
    """Group notes by property and sort groups."""
    from collections import defaultdict

    groups_map = defaultdict(list)
    prop = config.property

    for note in notes:
        # Resolve group key
        val = None
        is_error = False
        if prop.startswith("formula."):
            fname = prop[8:]
            if fname in note.get("note_warnings", {}):
                is_error = True
            else:
                val = note["formulas"].get(fname)
        elif prop.startswith("file."):
            if prop == "file.name":
                val = Path(note["path"]).stem
            elif prop == "file.folder":
                val = str(Path(note["path"]).parent).replace("\\", "/")
            elif prop == "file.path":
                val = note["path"]
            elif prop == "file.ext":
                val = Path(note["path"]).suffix.lstrip(".")
            else:
                val = note["frontmatter"].get(prop[5:])
        else:
            val = note["frontmatter"].get(prop)

        # Coerce to string
        if is_error:
            label = "Error"
        elif val is None:
            label = "(None)"
        else:
            label = str(val)

        groups_map[label].append(note)

    # Sort groups
    reverse = config.direction == "DESC"
    sorted_labels = sorted(groups_map.keys(), reverse=reverse)

    results = []
    for label in sorted_labels:
        group_notes = groups_map[label]
        results.append(
            GroupResult(
                label=label,
                count=len(group_notes),
                notes=group_notes,
            )
        )
    return results


def execute_base(
    base: Base,
    index: VaultIndex,
    view_name: str | None = None,
) -> QueryResult:
    """Execute a base's selected view against the index and return a QueryResult."""
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
        if selected_view.type == "map":
            return QueryResult(
                notes=[],
                warnings=[
                    {
                        "formula": "",
                        "reason": (
                            "Execution of 'map' views is not supported by vault-mcp. "
                            "It requires the Obsidian Maps community plugin."
                        ),
                    }
                ],
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
        for key in _PROMOTED_VIEW_KEYS.get(selected_view.type, ()):
            if key in selected_view.extra:
                view_props[key] = selected_view.extra[key]

    merged_filter: FilterNode | None = None
    if base.filters and selected_view and selected_view.filters:
        merged_filter = FilterNode(
            op="and", children=[base.filters, selected_view.filters]
        )
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
    for name in summaries_to_run:
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

        if merged_filter and not evaluate_filter(
            merged_filter, path, fm, rel, outbound
        ):
            continue

        formula_values: dict[str, Any] = {}
        note_warnings: dict[str, str] = {}
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
            if warning:
                note_warnings[name] = warning
                if name not in warned_formulas:
                    warnings.append({"formula": name, "reason": warning})
                    warned_formulas.add(name)

        notes.append(
            {
                "path": rel,
                "frontmatter": fm,
                "formulas": formula_values,
                "note_warnings": note_warnings,
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
                    accums[name]["min"] = min(accums[name]["min"], num)
                    accums[name]["max"] = max(accums[name]["max"], num)
                except ValueError, TypeError:
                    pass

    # Finalize summaries
    results: dict[str, Any] = {}
    for name, s in summaries_to_run.items():
        a = accums[name]
        if s.function == "count":
            results[name] = a["count_with_val"]
        elif not a["count_with_val"]:
            results[name] = 0 if s.function == "sum" else None
        elif s.function == "sum":
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

    groups = []
    if selected_view and selected_view.group_by:
        groups = _partition_results(notes, selected_view.group_by)

    return QueryResult(
        notes=notes,
        warnings=warnings,
        view_name=view_name,
        view_properties=view_props,
        summaries=results,
        groups=groups,
        total=len(notes),
    )
