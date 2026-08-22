"""The Bases verb surface — parse, execute, write and subscribe to Obsidian Bases.

Split out of server.py under vault-mcp#5294. A REGISTRATION MODULE: server.py
imports it at its foot for the side effect of these six `@mcp.tool()` calls, not
for a name.

`Context` IS IMPORTED AT RUNTIME, DELIBERATELY. FastMCP evaluates a verb's type
annotations when it registers it, so a TYPE_CHECKING-only import of a name used
in a signature raises InvalidSignature at import — which is exactly what
`subscribe_base(ctx: Context[Any, Any] | None = None)` did on the first attempt
at this split. It fails loudly rather than silently dropping the verb, but only
because the annotation is reachable; `from __future__ import annotations` makes
every OTHER annotation lazy, which is why nothing else here needed the same
treatment.

`subscribe_base` is also the one verb with live state — it registers the caller
on the SubscriptionManager and stamps `_active_sessions` — so it reaches back
into server.py for both. That coupling is why the accessors stayed there rather
than moving with the verbs.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from mcp.server.fastmcp import Context

# The bases helpers come STRAIGHT FROM vault_mcp.bases, not via server.py.
# Routing them through the façade cannot work: once these verbs left server.py
# it no longer used those names, so `ruff --fix` correctly deleted the imports —
# and a re-export kept alive only to feed this module would be a name with no
# reader, which the same lint would flag again on the next edit.
from vault_mcp.bases import (
    _serialize_base,
)
from vault_mcp.bases import (
    execute_base as _execute_base_impl,
)
from vault_mcp.bases import (
    parse_file as _parse_file_impl,
)
from vault_mcp.bases import (
    validate_base as _validate_base_impl,
)
from vault_mcp.bases import (
    write_base_to_file as _write_base_to_file_impl,
)

# Only the live server state comes from server.py — the FastMCP instance, the
# lazy accessors, and the session set that subscribe_base stamps.
from vault_mcp.server import (
    VAULT_PATH,
    _active_sessions,
    _get_index,
    _get_sub_manager,
    mcp,
)


@mcp.tool()
async def subscribe_base(
    path: str,
    view: str | None = None,
    base_index: int = 0,
    ctx: Context[Any, Any] | None = None,
) -> dict[str, Any]:
    """Subscribe to live updates for a base query.

    Pushes a notification whenever the result set for this base changes.
    Notifications use method 'notifications/bases/update'.

    Args:
        path: Vault-relative file path containing the base.
        view: Named view to restrict to.
        base_index: 0-based index of which base (default 0).
        ctx: FastMCP context used to push live-update notifications (injected).

    Returns:
        {"handle": str, "initial_results": dict}

    """
    idx = _get_index()
    file_path = idx.vault / path
    if not file_path.exists():
        return {"error": "not_found", "path": path}

    pf = _parse_file_impl(file_path)
    if pf.errors and not pf.bases:
        return {
            "error": "parse_error",
            "path": path,
            "detail": pf.errors[0]["message"],
        }

    if base_index < 0 or base_index >= len(pf.bases):
        return {
            "error": "invalid_base_index",
            "path": path,
            "index": base_index,
            "available": len(pf.bases),
        }

    if ctx:
        _active_sessions.add(ctx.session)

    mgr = _get_sub_manager()
    handle = mgr.add(path, view, base_index)

    base = pf.bases[base_index]
    result = _execute_base_impl(base, idx, view_name=view)

    with mgr.lock:
        if handle in mgr.subscriptions:
            mgr.subscriptions[handle].last_result_hash = mgr._hash_result(
                result
            )

    return {
        "handle": handle,
        "initial_results": asdict(result)
        if hasattr(result, "__dataclass_fields__")
        else result,
    }


@mcp.tool()
async def unsubscribe_base(handle: str) -> dict[str, Any]:
    """Cancel a base live update subscription.

    Args:
        handle: The subscription handle returned by subscribe_base.

    Returns:
        {"ok": bool}

    """
    mgr = _get_sub_manager()
    success = mgr.remove(handle)
    return {"ok": success}


@mcp.tool()
def parse_base(path: str) -> dict[str, Any]:
    """Parse a markdown file for Obsidian Bases code blocks.

    Returns the structured representation of each base: filter tree,
    formula definitions, and view configurations.

    Args:
        path: Vault-relative file path. Example: "Outputs/Plans/Plans.md"

    Returns:
        {"path": str, "count": int, "bases": [...], "errors": [...]}

    """
    file_path = VAULT_PATH / path
    if not file_path.exists():
        return {"error": "not_found", "path": path}
    pf = _parse_file_impl(file_path)
    return {
        "path": path,
        "count": len(pf.bases),
        "bases": [_serialize_base(b) for b in pf.bases],
        "errors": pf.errors,
    }


@mcp.tool()
def execute_base(
    path: str,
    view: str | None = None,
    base_index: int = 0,
) -> dict[str, Any]:
    """Execute a base's filters and formulas against the vault index.

    Returns matching notes with computed formula columns, optionally
    restricted to a named view.

    Args:
        path: Vault-relative file path containing the base.
        view: Named view to restrict to. If null, base-level filters only.
        base_index: 0-based index of which base to execute (default 0).

    Returns:
        {"total": int, "view": str|null, "notes": [...], "warnings": [...]}

    """
    file_path = VAULT_PATH / path
    if not file_path.exists():
        return {"error": "not_found", "path": path}

    pf = _parse_file_impl(file_path)
    if pf.errors and not pf.bases:
        return {
            "error": "parse_error",
            "path": path,
            "detail": pf.errors[0]["message"],
        }

    if base_index < 0 or base_index >= len(pf.bases):
        return {
            "error": "invalid_base_index",
            "path": path,
            "index": base_index,
            "available": len(pf.bases),
        }

    base = pf.bases[base_index]

    if view is not None:
        view_names = [v.name for v in base.views]
        matched = [v for v in base.views if v.name == view]
        if not matched:
            return {
                "error": "view_not_found",
                "path": path,
                "view": view,
                "available": view_names,
            }
        if matched[0].type != "table":
            return {
                "error": "unsupported_view_type",
                "view": view,
                "type": matched[0].type,
                "detail": "Only table views are executable in Phase 1",
            }

    idx = _get_index()
    result = _execute_base_impl(base, idx, view_name=view)
    return {
        "total": result.total,
        "view": result.view_name,
        "notes": result.notes,
        "warnings": result.warnings,
        "summaries": result.summaries,
    }


@mcp.tool()
def write_base(
    path: str,
    base: dict[str, Any],
    base_index: int | None = None,
    validate: bool = True,
) -> dict[str, Any]:
    """Write or update an inline base code block in a markdown file.

    Args:
        path: Vault-relative file path.
        base: Base configuration dict with keys: filters, formulas, views.
        base_index: 0-based index of which base to replace. If null and file
                    has no bases, appends. If null and one base, replaces it.
        validate: Run validation before writing (default true).

    Returns:
        {"written": true, "path": str, "action": str, "base_index": int} on success.

    """
    file_path = VAULT_PATH / path
    if not file_path.exists() and not file_path.parent.exists():
        return {"error": "not_found", "path": path}

    if validate:
        vr = _validate_base_impl(base)
        if not vr.valid:
            return {
                "written": False,
                "path": path,
                "validation": {
                    "valid": False,
                    "errors": vr.errors,
                    "warnings": vr.warnings,
                },
            }

    result = _write_base_to_file_impl(file_path, base, base_index=base_index)
    result["path"] = path
    return result


@mcp.tool()
def validate_base_tool(base: dict[str, Any]) -> dict[str, Any]:
    """Validate a base configuration without writing it.

    Checks YAML validity, formula references, special characters,
    and sort property references.

    Args:
        base: Base configuration dict (same shape as write_base.base).

    Returns:
        {"valid": bool, "errors": [...], "warnings": [...]}

    """
    vr = _validate_base_impl(base)
    return {
        "valid": vr.valid,
        "errors": vr.errors,
        "warnings": vr.warnings,
    }
