"""Bases serialization, writing and validation.

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
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path

import yaml

from vault_mcp.bases_model import (
    _BASE_BLOCK_RE,
    _CARD_PROPS,
    _MAP_PROPS,
    Base,
    FilterNode,
    ValidationResult,
)

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Serializer
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
        "filters": _serialize_filter_node(base.filters)
        if base.filters
        else None,
        "formulas": {
            name: {"expression": f.expression, "tier": f.tier}
            for name, f in base.formulas.items()
        },
        "summaries": {
            s.name: f"{s.function}({s.property})" if s.property else s.function
            for s in base.summaries
        },
        "views": [
            {
                "name": v.name,
                "type": v.type,
                "filters": _serialize_filter_node(v.filters)
                if v.filters
                else None,
                "order": v.order,
                "sort": [
                    {"property": s.property, "direction": s.direction}
                    for s in v.sort
                ],
                "groupBy": {
                    "property": v.group_by.property,
                    "direction": v.group_by.direction,
                }
                if v.group_by
                else None,
                "summaries": {
                    s.name: f"{s.function}({s.property})"
                    if s.property
                    else s.function
                    for s in v.summaries
                },
                **({"markers": v.markers} if v.markers else {}),
                **({"column_sizes": v.column_sizes} if v.column_sizes else {}),
                **(
                    {
                        k: val
                        for k, val in v.extra.items()
                        if (v.type == "cards" and k in _CARD_PROPS)
                        or (v.type == "map" and k in _MAP_PROPS)
                    }
                ),
                **(
                    {
                        "extra": {
                            k: val
                            for k, val in v.extra.items()
                            if not (
                                (v.type == "cards" and k in _CARD_PROPS)
                                or (v.type == "map" and k in _MAP_PROPS)
                            )
                        }
                    }
                    if v.extra
                    else {}
                ),
            }
            for v in base.views
        ],
    }
    return result


# ---------------------------------------------------------------------------
# Writer
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
    """Write a base block to a file — create, append, or replace by index — and report the action."""
    yaml_content = _base_dict_to_yaml(base_dict)
    new_block = f"```base\n{yaml_content}```"

    if not file_path.exists():
        file_path.write_text(new_block, encoding="utf-8")
        return {"written": True, "action": "created", "base_index": 0}

    text = file_path.read_text(encoding="utf-8")
    blocks = list(_BASE_BLOCK_RE.finditer(text))

    if not blocks:
        # Exactly ONE blank line between existing content and the new block,
        # whatever the file already ends with.
        #
        # The previous form computed a separator and then unconditionally
        # overwrote it: the `"\n"` branch could never survive, because the
        # second condition was true in every case that produced it. Dead, and
        # it also gave a file ending in a single newline TWO blank lines while
        # the other two cases got one. Found by asserting the output rather
        # than just `written` — mutants on both lines had been surviving.
        if not text or text.endswith("\n\n"):
            separator = ""
        elif text.endswith("\n"):
            separator = "\n"
        else:
            separator = "\n\n"
        file_path.write_text(text + separator + new_block, encoding="utf-8")
        return {"written": True, "action": "appended", "base_index": 0}

    if base_index is None:
        # UNPACKED, not length-compared.
        #
        # The zero-block case already returned via the append path, so `blocks`
        # always holds at least one here — and on a list that is never empty
        # EVERY spelling of the length check has an equivalent mutant: `== 1`
        # matches `<= 1`, and `> 1` matches `!= 1`. I wrote it both ways and the
        # gate reported a survivor both times. Unpacking states "exactly one"
        # with no comparison at all, so there is no operator left to mutate.
        try:
            (_only_block,) = blocks
        except ValueError:
            return {
                "written": False,
                "error": "ambiguous_target",
                "count": len(blocks),
            }
        base_index = 0

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
# Validator
# ---------------------------------------------------------------------------

_YAML_SPECIAL_CHARS = set(":{}[]#&*!|>'\"-%@`")


def validate_base(base_dict: dict[str, Any]) -> ValidationResult:
    """Validate a base dict's structure and return a ValidationResult."""
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []

    try:
        # No formatting keywords: this dump exists ONLY to be re-parsed on the
        # next line, so flow style and key order cannot affect the outcome.
        # They were mutation sites with no killable answer — the round-trip
        # succeeds either way — which is a sign they were never load-bearing.
        # The formatting that IS load-bearing lives in _base_dict_to_yaml,
        # where it is asserted.
        dumped = yaml.dump(base_dict)
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

    def _check_strings(obj: object, path: str = "") -> None:
        if isinstance(obj, str):
            if any(c in obj for c in _YAML_SPECIAL_CHARS):
                warnings.append(
                    {
                        "type": "unquoted_special_char",
                        "message": f"Value contains YAML special characters: {obj!r}",
                        # `path`, not `path or None`: the string branch is
                        # only ever reached from the dict/list recursion below,
                        # which always passes a non-empty path, so the `or None`
                        # arm could never fire. It was a dead sub-expression
                        # carrying a live mutant.
                        "location": path,
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
        valid=not errors,
        errors=errors,
        warnings=warnings,
    )
