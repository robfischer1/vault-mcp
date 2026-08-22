"""Obsidian Bases — parse, evaluate, execute, serialize and validate.

THIS MODULE IS A FAÇADE. The implementation was 1400 lines, well over the
600-LOC block, and split under vault-mcp#5294 into five modules by phase:

    bases_model    the dataclasses and constants
    bases_parser   YAML / markdown -> dataclasses
    bases_eval     the restricted formula engine and filter evaluation
    bases_exec     running a view against the index
    bases_io       serialization, writing, validation

Everything public is re-exported here, so every existing
`from vault_mcp.bases import X` keeps working and no call site moved. New code
may import the submodule directly; both are supported.

THE MUTATION GATE FOLLOWS THE CODE. `critical-modules` in mutation.yml and
mutation-nightly.yml named bases.py; left alone, it would now mutate a file of
re-export statements and report a healthy score over nothing. The five modules
are named there instead — a split that quietly narrows a gate is worse than the
length it fixed.
"""

from vault_mcp.bases_eval import (
    FormulaDepthError,
    FormulaError,
    FormulaEvaluator,
    FormulaTimeoutError,
    evaluate_filter,
    evaluate_formula,
)
from vault_mcp.bases_exec import _partition_results, execute_base
from vault_mcp.bases_io import (
    _base_dict_to_yaml,
    _serialize_base,
    _serialize_filter_node,
    validate_base,
    write_base_to_file,
)
from vault_mcp.bases_model import (
    Base,
    FilterNode,
    Formula,
    GroupByConfig,
    GroupResult,
    ParsedFile,
    QueryResult,
    SortDirective,
    Summary,
    ValidationResult,
    ViewConfig,
    _classify_formula_tier,
    _parse_summary,
    extract_base_blocks,
)
from vault_mcp.bases_parser import (
    _build_filter_tree,
    _parse_filter_predicate,
    parse_base_yaml,
    parse_file,
)

__all__ = [
    "Base",
    "FilterNode",
    "Formula",
    "FormulaDepthError",
    "FormulaError",
    "FormulaEvaluator",
    "FormulaTimeoutError",
    "GroupByConfig",
    "GroupResult",
    "ParsedFile",
    "QueryResult",
    "SortDirective",
    "Summary",
    "ValidationResult",
    "ViewConfig",
    "_base_dict_to_yaml",
    "_build_filter_tree",
    "_classify_formula_tier",
    "_parse_filter_predicate",
    "_parse_summary",
    "_partition_results",
    "_serialize_base",
    "_serialize_filter_node",
    "evaluate_filter",
    "evaluate_formula",
    "execute_base",
    "extract_base_blocks",
    "parse_base_yaml",
    "parse_file",
    "validate_base",
    "write_base_to_file",
]
