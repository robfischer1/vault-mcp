# SPDX-FileCopyrightText: 2026 Rob Fischer
#
# SPDX-License-Identifier: Apache-2.0

"""Write confirmation for ``patch_note``.

The Obsidian Local REST API answers a PATCH from the document its engine
computed, and vault-mcp's own disk reads (``read_note``, the index) can trail
that write when the vault is mirrored between the Windows and WSL sides. A
caller that read back through the lagging path saw the edit as lost and retried,
which duplicated the content. ``confirm_patch`` closes that: after the PATCH it
reads the note back over REST and compares it with the document the PATCH
returned, so ``ok`` means the write is visible, not merely accepted.
"""

from __future__ import annotations

import time
from typing import Any

from vault_mcp.rest_client import vault_url

# Bounded wait: ~0.5 + 1 + 1.5 + 2 + 2.5 s of sleeping across 6 reads.
CONFIRM_ATTEMPTS = 6
CONFIRM_STEP_S = 0.5

SCOPES = ("content", "marker", "markerAndContent", "parent")


def confirm_patch(client: Any, path: str, expected: object) -> dict[str, Any]:
    """Read ``path`` back over REST until it equals ``expected``.

    ``expected`` is the patched document the PATCH response carried. Returns
    ``{"confirmed": True}`` or ``{"confirmed": False, "reason": ..., "attempts": n}``.
    """
    if not isinstance(expected, str):
        return {"confirmed": False, "reason": "patch response carried no document", "attempts": 0}
    reason = "note on REST differs from the patched document"
    for attempt in range(1, CONFIRM_ATTEMPTS + 1):
        res = client.get(vault_url(path), accept="text/markdown")
        if res["ok"] and res["data"] == expected:
            return {"confirmed": True}
        reason = (
            f"read-back failed: {res.get('error')}"
            if not res["ok"]
            else "note on REST differs from the patched document"
        )
        if attempt < CONFIRM_ATTEMPTS:
            time.sleep(CONFIRM_STEP_S * attempt)
    return {"confirmed": False, "reason": reason, "attempts": CONFIRM_ATTEMPTS}


def patch_headers(
    target_type: str,
    operation: str,
    target: str | None,
    *,
    create_if_missing: bool,
    within: int | None,
    scope: str | None,
    reject_if_content_preexists: bool,
) -> dict[str, str]:
    """Targeting headers for a PATCH (``Target`` is encoded later, by the client)."""
    headers = {"Target-Type": target_type, "Operation": operation}
    if target:
        headers["Target"] = target
    if create_if_missing:
        headers["Create-Target-If-Missing"] = "true"
    if within is not None:
        headers["Within"] = str(within)
    if scope is not None:
        headers["Target-Scope"] = scope
    if reject_if_content_preexists:
        headers["Reject-If-Content-Preexists"] = "true"
    return headers


def patch_answer(client: Any, path: str, expected: object) -> dict[str, Any]:
    """Reply to an accepted PATCH: ``ok`` only once the write reads back."""
    seen = confirm_patch(client, path, expected)
    if seen["confirmed"]:
        return {"ok": True, "patched": path, "confirmed": True}
    return {
        "ok": False,
        "status": "unconfirmed",
        "error": "unconfirmed",
        "patched": path,
        "detail": (
            f"PATCH accepted but not visible over REST ({seen['reason']}, "
            f"{seen['attempts']} reads). It may still land: check with unsaved_buffer "
            "before retrying, and retry with reject_if_content_preexists=True."
        ),
    }


def bad_scope(scope: str | None) -> dict[str, Any] | None:
    """Return an error reply for an unknown ``Target-Scope``, or None when it is fine."""
    if scope is None or scope in SCOPES:
        return None
    return {"error": "invalid_scope", "detail": f"scope must be one of {list(SCOPES)}"}
