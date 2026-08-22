"""Test substrate — the sanctioned home for this repo's test doubles.

`[tool.forge_testkit_lint] substrate_pkg = "tests.substrate"` points the
fake-placement lint here, so a `Fake*`/`Stub*` class defined anywhere else in
the repo is a lint failure. That rule exists because of what it replaced: eight
doubles scattered across test modules, five of them separately hand-rolled
copies of one `FakeVault`, no two agreeing, three missing methods the `NoteIO`
protocol declares — and all five raising a failure the real vault never raises.
"""

from tests.substrate.cli import FakeCLI
from tests.substrate.poster import FakePoster
from tests.substrate.transport import FakeTransport
from tests.substrate.vault import FakeVault, VaultCall

__all__ = [
    "FakeCLI",
    "FakePoster",
    "FakeTransport",
    "FakeVault",
    "VaultCall",
]
