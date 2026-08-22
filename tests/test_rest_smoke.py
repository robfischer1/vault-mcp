"""REST-seam tests, replayed from a golden tape by default and live on demand.

WHAT CHANGED AND WHY. This file used to require a running Obsidian and skip
without one. Measured 2026-08-22: that meant all 25 of its tests had never run
in CI a single time — the entire skip population of the suite was this one file.
A skip is indistinguishable from a pass, so this read as REST coverage and was
none.

Now the same 25 assertions run twice over:

  DEFAULT (CI)  against tests/substrate/rest.py replaying
                tests/fixtures/rest/olrapi.tape.json — shapes recorded from the
                live plugin v4.1.7, values synthetic (vault-mcp is published to
                GitHub; a verbatim tape would commit real note content).
  -m live       against the real API, unchanged behaviour, for the machine that
                has Obsidian up.

WHAT RUNNING THEM FOR THE FIRST TIME FOUND. Four of these tests were asserting
against a vault that no longer exists:

  * System/Governance/ is absent from BOTH the live vault and the WSL checkout,
    so test_subdirectory, the DQL TABLE query and the JsonLogic glob were all
    pointed at an empty path. Retargeted to System/, which does exist.
  * The daily periodic note 404s whenever today's note has not been created —
    an environmental fact, not a defect, so the live arm asserts the CONTRACT
    (a well-formed note envelope OR a well-formed not-found envelope) instead
    of assuming one branch.
  * Dataview DQL cannot succeed at all against plugin 4.1.7, which advertises
    only note+json, document-map+json and jsonlogic+json. Those tests are
    xfail(strict=True) pending Rob's ruling on vault-mcp#5287 — strict so they
    shout if the verb ever starts working rather than passing silently.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from tests.substrate.rest import TapeRESTClient
from vault_mcp.rest_client import ObsidianRESTClient

KEY_PATH = os.environ.get(
    "VAULT_MCP_REST_KEY_PATH",
    str(Path.home() / "Obsidian" / ".local" / "rest-api-key.txt"),
)
REST_URL = os.environ.get("VAULT_MCP_REST_URL", "http://127.0.0.1:27123")
NOTE_JSON = "application/vnd.olrapi.note+json"
MAP_JSON = "application/vnd.olrapi.document-map+json"
DQL_TYPE = "application/vnd.olrapi.dataview.dql+txt"
JSONLOGIC = "application/vnd.olrapi.jsonlogic+json"

# A directory that exists in the vault. The previous value, System/Governance,
# does not — verified against the live API and both checkouts on 2026-08-22.
REAL_DIR = "System"

# THE SKIP-VS-FAIL CONTRACT, still here but now scoped to the LIVE arm only.
# The default arm cannot skip: it has a tape and no external dependency.
# VAULT_MCP_REST_REQUIRED truthy turns an unreachable API into a FAILURE rather
# than a skip, per the fleet-wide <SCOPE>_<RESOURCE>_REQUIRED convention that
# forge-testkit-go/containers sets and 13 repos read.
REQUIRED_ENV = "VAULT_MCP_REST_REQUIRED"
_TRUTHY = frozenset({"1", "true", "yes", "on"})


def _rest_required() -> bool:
    """True when an unreachable REST API must FAIL the live arm, not skip it."""
    return os.environ.get(REQUIRED_ENV, "").strip().lower() in _TRUTHY


def _live() -> ObsidianRESTClient:
    """Build the live client, honouring the skip-vs-fail contract."""
    api_key = os.environ.get("VAULT_MCP_REST_KEY", "")
    c = ObsidianRESTClient(
        base_url=REST_URL,
        key_path=None if api_key else KEY_PATH,
        api_key=api_key or None,
    )
    health = c.probe()
    if not health["reachable"]:
        msg = f"Obsidian REST API not reachable at {REST_URL}"
        if _rest_required():
            pytest.fail(
                f"{msg} — {REQUIRED_ENV} is armed, so these tests must RUN, "
                "not skip. A skipped gate reports success, which is the "
                "outcome arming the variable exists to prevent."
            )
        pytest.skip(f"{msg} — skipping the live arm")
    return c


@pytest.fixture(
    params=[
        pytest.param("tape", id="tape"),
        pytest.param("live", id="live", marks=pytest.mark.live),
    ]
)
def client(request: pytest.FixtureRequest):
    """Every assertion below runs against BOTH backends.

    The tape arm is unmarked, so it runs everywhere — including CI, where
    `addopts` deselects `live`. The live arm carries the `live` marker and is
    opt-in with `-m live` on a machine that has Obsidian up. One set of
    assertions, two backends: the tape can never drift from the live API
    without the live arm reddening, which is the property a hand-written mock
    cannot give.
    """
    if request.param == "tape":
        yield TapeRESTClient()
        return
    live = _live()
    try:
        yield live
    finally:
        # Without this the httpx pool is finalized by the GC and raises a
        # ResourceWarning, which filterwarnings = ["error"] turns into a
        # failure. That is how the missing close() was found (see rest_client).
        live.close()


class TestHealthSmoke:
    def test_reachable(self, client):
        result = client.probe()
        assert result["reachable"] is True
        assert result["version"] is not None

    def test_version_string(self, client):
        result = client.probe()
        assert "." in result["version"]


class TestActiveNote:
    def test_returns_path(self, client):
        result = client.get("/active/", accept=NOTE_JSON)
        assert result["ok"] is True
        assert "path" in result["data"]

    def test_has_frontmatter(self, client):
        result = client.get("/active/", accept=NOTE_JSON)
        assert "frontmatter" in result["data"]


class TestPeriodicNote:
    def test_daily_today(self, client):
        """Today's daily note either exists or does not — both are well-formed.

        Asserting `ok is True` assumed the note had been created today. Against
        the live vault on 2026-08-22 it had not, and the API correctly answered
        404 "Periodic note does not exist for the specified period." That is an
        environmental fact, not a defect, so what is pinned here is the ENVELOPE
        CONTRACT for both branches. The tape records the success branch, so the
        default arm still exercises the note shape every run.
        """
        result = client.get(
            "/periodic/daily/",
            accept=NOTE_JSON,
            extra_headers={"Target-Type": "note"},
        )
        if result["ok"]:
            assert "path" in result["data"]
            assert "frontmatter" in result["data"]
        else:
            assert result["error"] == "rest_not_found"


class TestVaultRead:
    def test_read_known_file(self, client):
        result = client.get(
            "/vault/CLAUDE.md",
            accept=NOTE_JSON,
        )
        assert result["ok"] is True
        assert "content" in result["data"]

    def test_read_missing_file(self, client):
        result = client.get("/vault/DOES_NOT_EXIST_12345.md")
        assert result["ok"] is False
        assert result["error"] == "rest_not_found"


class TestSearch:
    def test_simple_search(self, client):
        result = client.post("/search/simple/", params={"query": "CLAUDE"})
        assert result["ok"] is True
        data = result["data"]
        assert isinstance(data, list)
        assert len(data) > 0

    def test_empty_query(self, client):
        result = client.post("/search/simple/", params={"query": ""})
        # Plugin may return 400 or empty — either is acceptable
        assert "ok" in result


class TestCommands:
    def test_list_commands(self, client):
        result = client.get("/commands/")
        assert result["ok"] is True
        assert "commands" in result["data"]

    def test_execute_focus(self, client):
        result = client.post("/commands/editor:focus/")
        assert result["ok"] is True


# -------------------------------------------------------------------
# Phase 7 — Advanced query tools
# -------------------------------------------------------------------


class TestDataviewDQL:
    @pytest.mark.xfail(
        strict=True,
        reason=(
            "vault-mcp#5287 — DQL cannot succeed against Local REST API 4.1.7, "
            "which advertises only note+json, document-map+json and "
            "jsonlogic+json; every DQL content-type spelling returns HTTP 400 "
            "in ~0ms. STRICT so a plugin upgrade or a reimplementation over "
            "jsonlogic_search fails loudly rather than passing unnoticed."
        ),
    )
    def test_table_query(self, client):
        result = client.post(
            "/search/",
            content='TABLE file.name FROM "System" LIMIT 3',
            content_type=DQL_TYPE,
        )
        assert result["ok"] is True
        data = result["data"]
        assert isinstance(data, list)
        assert len(data) > 0

    def test_list_query_rejected(self, client):
        result = client.post(
            "/search/",
            content='LIST FROM "System" LIMIT 3',
            content_type=DQL_TYPE,
        )
        assert result["ok"] is False
        assert result["error"] == "rest_invalid_request"

    def test_invalid_dql(self, client):
        result = client.post(
            "/search/",
            content="NOT A VALID QUERY %%%",
            content_type=DQL_TYPE,
        )
        assert result["ok"] is False


class TestJsonLogic:
    def test_glob_query(self, client):
        result = client.post(
            "/search/",
            json_body={"glob": ["System/*", {"var": "path"}]},
            content_type=JSONLOGIC,
        )
        assert result["ok"] is True
        data = result["data"]
        assert isinstance(data, list)
        assert len(data) > 0

    def test_frontmatter_equality(self, client):
        result = client.post(
            "/search/",
            json_body={"==": [{"var": "frontmatter.note_type"}, "folder-note"]},
            content_type=JSONLOGIC,
        )
        assert result["ok"] is True
        assert isinstance(result["data"], list)
        assert len(result["data"]) > 0


class TestTags:
    def test_returns_tags(self, client):
        result = client.get("/tags/")
        assert result["ok"] is True
        data = result["data"]
        assert "tags" in data
        assert isinstance(data["tags"], list)
        assert len(data["tags"]) > 0

    def test_tag_has_name_and_count(self, client):
        result = client.get("/tags/")
        tag = result["data"]["tags"][0]
        assert "name" in tag
        assert "count" in tag


class TestListDirectory:
    def test_vault_root(self, client):
        result = client.get("/vault/")
        assert result["ok"] is True
        assert "files" in result["data"]
        files = result["data"]["files"]
        assert len(files) > 0
        assert any(f.endswith("/") for f in files)

    def test_subdirectory(self, client):
        """A real subdirectory lists both notes and nested folders.

        This used to assert `any("AGENTS" in f)` against System/Governance —
        a directory that exists in neither the live vault nor the checkout. The
        first live run then caught the replacement assertion drifting too: the
        live System/ holds no AGENTS.md either. What is pinned now is the
        listing CONTRACT the client depends on — .md entries plain, folders
        suffixed "/" — which is what RestNoteIO.list_notes actually parses, and
        which both arms satisfy without naming a file that can be moved.
        """
        result = client.get(f"/vault/{REAL_DIR}/")
        assert result["ok"] is True
        files = result["data"]["files"]
        assert len(files) > 0
        assert any(f.endswith(".md") for f in files)
        assert any(f.endswith("/") for f in files)

    def test_missing_directory(self, client):
        result = client.get("/vault/NONEXISTENT_DIR_12345/")
        assert result["ok"] is False
        assert result["error"] == "rest_not_found"


class TestOpenInObsidian:
    def test_open_existing_file(self, client):
        result = client.post("/open/CLAUDE.md")
        assert result["ok"] is True

    def test_open_returns_empty_body(self, client):
        result = client.post("/open/CLAUDE.md")
        assert result["ok"] is True
        assert result["data"] is None


class TestDocumentMap:
    def test_active_note_map(self, client):
        result = client.get(
            "/active/",
            accept=MAP_JSON,
        )
        assert result["ok"] is True
        data = result["data"]
        assert "headings" in data
        assert "blocks" in data
        assert "frontmatterFields" in data

    def test_specific_file_map(self, client):
        result = client.get(
            "/vault/CLAUDE.md",
            accept=MAP_JSON,
        )
        assert result["ok"] is True
        data = result["data"]
        assert isinstance(data["headings"], list)
        assert len(data["headings"]) > 0
