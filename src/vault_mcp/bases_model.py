"""The Bases data model — the dataclasses and the module constants.

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
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class FilterNode:
    """A node in a Bases filter tree: a leaf condition (field/value) or a boolean group (op over children)."""

    op: str
    field: str | None = None
    value: str | None = None
    children: list[FilterNode] | None = None


@dataclass
class Formula:
    """A named computed-property formula, evaluated at a given dependency tier."""

    name: str
    expression: str
    tier: int


@dataclass
class Summary:
    """A column summary: an aggregate function over a property."""

    name: str
    function: str
    property: str | None


@dataclass
class SortDirective:
    """One sort key: a property and a direction (ASC/DESC)."""

    property: str
    direction: str


@dataclass
class GroupByConfig:
    """Group-by setting for a view: the property to group on and the group direction."""

    property: str
    direction: str


@dataclass
class ViewConfig:
    """A single Bases view (table/board/etc.) with its filters, order, sort, grouping, and summaries."""

    name: str
    type: str
    filters: FilterNode | None = None
    order: list[str] = field(default_factory=list)
    sort: list[SortDirective] = field(default_factory=list)
    group_by: GroupByConfig | None = None
    summaries: list[Summary] = field(default_factory=list)
    markers: str | None = None
    column_sizes: dict[str, int] = field(default_factory=dict)
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class Base:
    """A parsed `.base` file: its top-level filters, named formulas, and views."""

    filters: FilterNode | None
    formulas: dict[str, Formula]
    views: list[ViewConfig]
    summaries: list[Summary] = field(default_factory=list)
    raw_yaml: str = ""
    line_number: int = 0


@dataclass
class ParsedFile:
    """The result of parsing one `.base` file: its bases plus any parse errors."""

    path: str
    bases: list[Base]
    errors: list[dict[str, Any]]


@dataclass
class GroupResult:
    """One group in a grouped query result: its label, row count, and member notes."""

    label: str
    count: int
    notes: list[dict[str, Any]]


@dataclass
class QueryResult:
    """The result of executing a Bases view: matched notes, computed properties, summaries, groups, and total."""

    notes: list[dict[str, Any]]
    warnings: list[dict[str, str]]
    view_name: str | None
    view_properties: dict[str, Any] = field(default_factory=dict)
    summaries: dict[str, Any] = field(default_factory=dict)
    groups: list[GroupResult] = field(default_factory=list)
    total: int = 0


@dataclass
class ValidationResult:
    """The outcome of validating a base: valid plus structured errors and warnings."""

    valid: bool
    errors: list[dict[str, Any]]
    warnings: list[dict[str, Any]]


# ---------------------------------------------------------------------------
# Globals & Constants
# ---------------------------------------------------------------------------

# Flags INLINE as `(?sm)` rather than `re.DOTALL | re.MULTILINE`.
#
# The combined form is a mutation site with no killable answer: DOTALL is 16 and
# MULTILINE is 8, disjoint bits, so `|`, `^` and `+` all fold to 56 and no test
# can separate them. That is not a reason to exempt the site — it is a reason to
# write the flags where there is no operator to mutate. Verified identical:
# same `.flags` value, same extractions.
_BASE_BLOCK_RE = re.compile(r"(?sm)^```base\s*\n(.*?)^```")

_NOTE_KEY_RE = re.compile(r'^note\["([^"]+)"\]$')
_LINKS_FILTER_RE = re.compile(
    r"^file\.(links|backlinks)\.filter\(.*\)\.length$"
)

_CARD_PROPS = {"cardSize", "image", "imageAspectRatio", "indentProperties"}
_MAP_PROPS = {
    "latProperty",
    "lngProperty",
    "defaultZoom",
    "markerConfig",
    "lat property",
    "lng property",
    "default zoom",
    "marker config",
}

_SUMMARY_RE = re.compile(r"^(\w+)(?:\((.+)\))?$")


def _classify_formula_tier(expression: str) -> int:
    """Classify formula into Tier 1 (simple) or Tier 2 (complex)."""
    # Simple Tier 1 matches
    if _NOTE_KEY_RE.match(expression):
        return 1
    if expression.startswith("file."):
        if _LINKS_FILTER_RE.match(expression):
            return 1
        if expression in (
            "file.name",
            "file.folder",
            "file.path",
            "file.ext",
            "file.mtime",
        ):
            return 1

    # Any operators or parentheses require the Tier 2 evaluator.
    #
    # A `for pat in _TIER2_PATTERNS` loop used to sit above this, listing
    # "html(", "if(", ".map(", ".join(", ".replace(" and ".toString(". It was
    # DEAD: every entry ends in "(", so this check already returned 2 for all
    # of them and the loop could not change an outcome. The mutation gate found
    # it — `for pat in []` survived, which is the signature of a branch that
    # never decides anything. Verified equivalent over 2,628 inputs before
    # removal, not argued. (vault-mcp#5294 follow-up.)
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
