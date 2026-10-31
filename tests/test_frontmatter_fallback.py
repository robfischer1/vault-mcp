# SPDX-FileCopyrightText: 2026 Rob Fischer
#
# SPDX-License-Identifier: Apache-2.0

"""The tolerant line fallback must not read a list item as a top-level key."""

import pytest

from vault_mcp.parsers import _load_yaml_flat, parse_frontmatter

# `title: x: y` is invalid YAML (nested mapping value), so these reach the fallback.
BAD = "title: x: y\n"


@pytest.mark.parametrize(
    ("body", "items"),
    [
        ("tags:\n# c\n- a: b\n", ["a: b"]),
        ("tags:\n# c\n  - a: b\n", ["a: b"]),
        ("tags:\n- q\n# c\n- a: b\n", ["q", "a: b"]),
        ("tags:\n- q\n# c\n  - a: b\n", ["q", "a: b"]),
        ("tags:\n- a: b\n", ["a: b"]),
        ("tags:\n  - a: b\n", ["a: b"]),
    ],
)
def test_list_item_with_colon_stays_under_key(body: str, items: list[str]) -> None:
    text = f"---\n{BAD}{body}---\n"
    assert _load_yaml_flat(BAD + body) is None
    fm = parse_frontmatter(text)
    assert fm["tags"] == items
    assert set(fm) == {"title", "tags"}


def test_stray_column0_item_is_not_a_key() -> None:
    fm = parse_frontmatter(f"---\n{BAD}- a: b\nz: 1\n---\n")
    assert set(fm) == {"title", "z"}
