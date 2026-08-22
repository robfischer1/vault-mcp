"""A tape-replaying stand-in for `ObsidianRESTClient` — the Golden-Fake shape.

WHY A TAPE AND NOT A MOCK. tests/test_rest_smoke.py exercised the REST seam
against a live Obsidian and skipped when one was not answering, which meant it
had never run in CI — 25 tests that looked like coverage and were not. A mock
would not fix that: the assertions are worth keeping precisely because they were
written against the real API, and a hand-mocked envelope drifts from it silently.

So the shapes below were RECORDED from the live plugin (v4.1.7) and the values
were then invented. That split is deliberate and load-bearing:

  * vault-mcp is published to GitHub. A verbatim tape would commit real note
    paths, frontmatter and journal content to a public repo.
  * The smoke assertions are STRUCTURAL — `"path" in data`,
    `isinstance(data, list)`, `error == "rest_not_found"` — so synthetic values
    satisfy every one of them without weakening a single check.

THE REFUSAL IS THE POINT. An un-recorded call raises `UntapedCallError` rather than
returning a canned success. That is the inversion forge_testkit.replay
describes: a permissive fake answers anything and teaches the suite nothing,
while a pinned one fails loudly the moment production asks a question the tape
was never told the answer to.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

TAPE = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "rest"
    / "olrapi.tape.json"
)

_JSON = "application/json"


class UntapedCallError(LookupError):
    """No recorded case for this request. The fake refuses to invent one.

    Named with the Error suffix ruff's N818 wants; forge_testkit spells its own
    equivalent `UntapedCall`, and this is the local analogue for the REST seam.
    """


def _key(method: str, path: str, args: dict[str, Any]) -> tuple[str, str]:
    """Reduce a request to the (verb, discriminator) the tape is keyed on."""
    verb = f"{method} {path}"
    accept = str(args.get("accept") or _JSON)
    ctype = str(args.get("content_type") or "")
    if ctype:
        disc = ctype
    elif "query" in args:
        disc = f"query={args['query']}"
    else:
        disc = accept
    return verb, disc


def _case_disc(case: dict[str, Any]) -> str:
    a = case.get("args") or {}
    if a.get("content_type"):
        ct = str(a["content_type"])
        # JsonLogic cases share a content type, so the query shape disambiguates.
        if "jsonlogic" in ct:
            if "glob" in a:
                return f"{ct}|glob"
            return f"{ct}|frontmatter"
        return ct
    if "query" in a:
        return f"query={a['query']}"
    return str(a.get("accept") or _JSON)


class TapeRESTClient:
    """Replays `olrapi.tape.json`. Same call surface as ObsidianRESTClient."""

    def __init__(self, tape: Path | None = None) -> None:
        """Load and index the tape."""
        raw = json.loads((tape or TAPE).read_text(encoding="utf-8"))
        cases = raw.get("cases")
        if not isinstance(cases, list) or not cases:
            msg = f"tape has no cases: {tape or TAPE}"
            raise ValueError(msg)
        self._cases: dict[tuple[str, str], dict[str, Any]] = {}
        for c in cases:
            if "verb" not in c or "response" not in c:
                msg = f"tape case missing verb/response: {c}"
                raise ValueError(msg)
            self._cases[(c["verb"], _case_disc(c))] = c["response"]

    # --- the ObsidianRESTClient surface ----------------------------------

    def probe(self) -> dict[str, Any]:
        """Report the recorded plugin as reachable."""
        return {
            "reachable": True,
            "version": "4.1.7",
            "last_probed": 0.0,
            "last_error": None,
        }

    def get(
        self,
        path: str,
        *,
        accept: str = _JSON,
        extra_headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Replay a recorded GET."""
        return self._replay("GET", path, {"accept": accept})

    def post(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        content: str | None = None,
        content_type: str | None = None,
        accept: str = _JSON,
    ) -> dict[str, Any]:
        """Replay a recorded POST."""
        args: dict[str, Any] = {"accept": accept}
        if content_type:
            args["content_type"] = content_type
        if params and "query" in params:
            args["query"] = params["query"]
        if (
            json_body is not None
            and content_type
            and "jsonlogic" in content_type
        ):
            args["content_type"] = (
                f"{content_type}|glob"
                if "glob" in json_body
                else f"{content_type}|frontmatter"
            )
        return self._replay("POST", path, args)

    # --- replay ----------------------------------------------------------

    def _replay(
        self, method: str, path: str, args: dict[str, Any]
    ) -> dict[str, Any]:
        key = _key(method, path, args)
        if key not in self._cases:
            msg = (
                f"no recorded case for {key[0]} ({key[1]}). "
                f"Re-record the tape rather than widening the fake."
            )
            raise UntapedCallError(msg)
        return self._cases[key]
