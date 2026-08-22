"""A PINNED `NoteIO` double, modelled on the implementation production wires.

WHY THIS EXISTS, and why it is not a dict.

`mutation.yml`'s header records the scale this repo's own gate was calibrated
on: "33% for a suite over a permissive fake, 86% for a pinned one." The five
`FakeVault` classes this replaces were the 33% end — dict-backed happy paths
that never modelled a failure, never refused anything, and answered every call
with success.

That is not an abstract cost. It hid a live bug: `ConventionGate._atom_filename`
probes for a free atom slug by catching the not-found error from `read_note`,
and it catches `(KeyError, OSError)`. Both real implementations raise
`ObsidianIOError`, so the clause caught test-fake behaviour and nothing else,
and every `atom_slug: true` write failed in production while the suite stayed
green (vault-mcp#5258).

WHICH REAL IMPLEMENTATION THIS MODELS. There are two, and they DIVERGE:

  RestNoteIO      (rest_client.py:344) — HTTP on loopback. This is the one
                  production uses: server.py:2026 is the ONLY construction of
                  ConventionGate in the source and it wires RestNoteIO, with no
                  fallback.
  ObsidianNoteIO  (cli_client.py:253) — same-session CLI IPC. Never constructed
                  in production; it cannot reach a desktop Obsidian across the
                  session boundary, which is why the REST one exists.

This double models **RestNoteIO**, because shipping behaviour is what a test
should be pinned to. The divergence that matters is `create_note`: REST PUTs,
so it OVERWRITES an existing note, while the CLI calls `app.vault.create`,
which throws. A test that needs the refusing flavour should say so explicitly
via `refuse_create_over_existing=True` rather than assume it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from vault_mcp.cli_client import ObsidianIOError

_TRASH = ".trash"


@dataclass(frozen=True)
class VaultCall:
    """One recorded call, so a test can assert on the traffic, not just the state."""

    verb: str
    path: str
    content: str | None = None


@dataclass
class FakeVault:
    """In-memory `NoteIO` with RestNoteIO's semantics, including its failures.

    Every method below mirrors a real one; where the real one raises, this
    raises the SAME exception type, and where the real one is deliberately
    forgiving (`list_notes` over a missing subtree) this is forgiving too.

    The attribute names are the ones the five replaced fakes already used —
    `store`, `calls`, `deleted` — so migrating a test module is a matter of
    deleting its local class and importing this one. The DIFFERENCE is meant to
    be behavioural, not cosmetic: the same test now runs against real failure
    modes instead of a dict's.

    Args:
        store: seed content, path -> body.
        refuse_create_over_existing: model ObsidianNoteIO's `app.vault.create`
            instead of REST PUT. Off by default because production is REST.
        fail: paths that raise `ObsidianIOError` on ANY access, for exercising
            the error paths that a dict fake can never reach.
    """

    store: dict[str, str] = field(default_factory=dict)
    refuse_create_over_existing: bool = False
    fail: set[str] = field(default_factory=set)
    log: list[VaultCall] = field(default_factory=list)

    # --- NoteIO ----------------------------------------------------------

    def create_note(self, path: str, content: str) -> None:
        """Create a note. REST PUT overwrites; the CLI flavour refuses."""
        self.log.append(VaultCall("create_note", path, content))
        self._guard(path)
        if self.refuse_create_over_existing and path in self.store:
            raise ObsidianIOError(
                f"write to {path} not confirmed (got None); "
                f"is obsidian-cli connected and the path's parent folder present?"
            )
        self.store[path] = content

    def write_note(self, path: str, content: str) -> None:
        """Overwrite the note at `path`."""
        self.log.append(VaultCall("write_note", path, content))
        self._guard(path)
        self.store[path] = content

    def read_note(self, path: str) -> str:
        """Read the note at `path`.

        RAISES `ObsidianIOError` when absent — NOT `KeyError`. A 404 from the
        REST API is a non-ok response, and `RestNoteIO.read_note` turns every
        non-ok response into `ObsidianIOError`. This single line is what makes
        the fake pinned rather than permissive.
        """
        self.log.append(VaultCall("read_note", path))
        self._guard(path)
        if path not in self.store:
            raise ObsidianIOError(f"REST read {path}: not_found: 404")
        return self.store[path]

    def delete_note(self, path: str) -> None:
        """Move the note to the vault-local `.trash/`, mirroring RestNoteIO.

        The real implementation reads first and only removes the origin after
        the trash copy lands, so a failed delete never loses the note. A
        missing note therefore raises from the READ, before anything moves.

        The log entry is written AFTER both checks, so `deleted` never reports
        a note that was not in fact deleted.
        """
        self._guard(path)
        if path not in self.store:
            raise ObsidianIOError(f"REST read {path}: not_found: 404")
        self.log.append(VaultCall("delete_note", path))
        self.store[f"{_TRASH}/{path}"] = self.store[path]
        del self.store[path]

    def list_notes(
        self, directory: str = "", *, recursive: bool = True
    ) -> list[str]:
        """List `.md` paths under `directory`.

        Mirrors RestNoteIO: `.md` only, `.trash/` skipped, and an unreachable
        subtree is an EMPTY LIST rather than an error — "a scan over a missing
        subtree is empty, not an error", per that implementation's docstring.
        """
        self.log.append(VaultCall("list_notes", directory))
        prefix = directory.strip("/")
        out = [
            p
            for p in self.store
            if p.endswith(".md")
            and not p.startswith(f"{_TRASH}/")
            and (not prefix or p.startswith(f"{prefix}/"))
        ]
        if recursive:
            return sorted(out)
        depth = prefix.count("/") + 1 if prefix else 0
        return sorted(p for p in out if p.count("/") == depth)

    # --- helpers ---------------------------------------------------------

    def _guard(self, path: str) -> None:
        """Refuse an injected-failure path the way a dead REST call would."""
        if path in self.fail:
            raise ObsidianIOError(f"REST read {path}: unreachable: injected")

    # --- assertion surface -------------------------------------------------
    # `calls` and `deleted` are DERIVED views over `log`, shaped exactly like
    # the ad-hoc lists the replaced fakes kept by hand. test_gate asserts
    # `len(writer.calls) == 0` to mean "nothing was written", so `calls` must
    # count writes only — a raw call log would count the read probes too and
    # quietly invert every one of those assertions.

    @property
    def calls(self) -> list[tuple[str, str]]:
        """(path, content) per note CREATED — writes only, never reads."""
        return [
            (c.path, c.content or "")
            for c in self.log
            if c.verb == "create_note"
        ]

    @property
    def deleted(self) -> list[str]:
        """Every path passed to a SUCCESSFUL delete, in order."""
        return [c.path for c in self.log if c.verb == "delete_note"]

    def paths_touched(self, verb: str) -> list[str]:
        """Every path `verb` was called with, in order."""
        return [c.path for c in self.log if c.verb == verb]
