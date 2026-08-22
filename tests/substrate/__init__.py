"""Test substrate — the sanctioned home for this repo's test doubles.

`[tool.forge_testkit_lint] substrate_pkg = "tests.substrate"` points the
fake-placement lint here, so a `Fake*`/`Stub*` class defined anywhere else in
the repo is a lint failure. That rule exists because of what it replaced: five
separately hand-rolled `FakeVault` classes in test_gate, test_compute,
test_lifecycle, test_lint and test_audit, no two of them agreeing, three of
them missing methods the `NoteIO` protocol declares — and all five raising a
failure the real vault never raises.
"""

from tests.substrate.vault import FakeVault, VaultCall

__all__ = ["FakeVault", "VaultCall"]
