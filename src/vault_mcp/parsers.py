"""Vault parsing helpers — canonical home.

Ported from vault-propagation/audit.py. After Phase 5, vault-propagation
imports from here instead of carrying its own copy.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

SKIP_DIRS: set[str] = {
    ".git", ".obsidian", ".claude", ".amazonq", ".trash",
    ".smart-env", "attachments",
}
SKIP_CONTENT_CHECKS: set[str] = {"Archives"}
SKIP_SYSTEM_SUBDIRS: set[str] = {"Tools", "Templates"}

WIKILINK_RE = re.compile(r'\[\[([^\]|#]+)(?:#[^\]|]+)?(?:\|[^\]]+)?\]\]')


def parse_frontmatter(text: str) -> dict[str, Any]:
    """Extract YAML frontmatter into a plain dict.

    Hand-rolled parser matching vault-propagation/audit.py behavior:
    supports quoted keys, inline lists, and block lists.
    """
    if not text.startswith('---'):
        return {}
    end = text.find('\n---', 3)
    if end == -1:
        return {}
    yaml_text = text[3:end].strip('\n')
    fm: dict[str, Any] = {}
    lines = yaml_text.split('\n')
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if not stripped or stripped.startswith('#'):
            i += 1
            continue
        if line.startswith((' ', '\t')):
            i += 1
            continue
        m = re.match(r'^("[^"]+"|[^:]+):\s*(.*)$', line)
        if not m:
            i += 1
            continue
        key = m.group(1).strip().strip('"').strip("'")
        val = m.group(2).strip()
        if val == '':
            list_items: list[str] = []
            j = i + 1
            while j < len(lines):
                nxt = lines[j]
                if not nxt.strip():
                    j += 1
                    continue
                ns = nxt.lstrip()
                if (nxt.startswith(' ') or nxt.startswith('\t')) and ns.startswith('- '):
                    item = ns[2:].strip()
                    if (item.startswith('"') and item.endswith('"')) or \
                       (item.startswith("'") and item.endswith("'")):
                        item = item[1:-1]
                    list_items.append(item)
                    j += 1
                else:
                    break
            if list_items:
                fm[key] = list_items
                i = j
            else:
                fm[key] = ''
                i += 1
            continue
        if val.startswith('[') and val.endswith(']'):
            inner = val[1:-1].strip()
            fm[key] = [] if not inner else [
                x.strip().strip('"').strip("'") for x in inner.split(',')
            ]
            i += 1
            continue
        if (val.startswith('"') and val.endswith('"')) or \
           (val.startswith("'") and val.endswith("'")):
            val = val[1:-1]
        fm[key] = val
        i += 1
    return fm


def strip_frontmatter(text: str) -> str:
    """Return the body of a markdown file with YAML frontmatter removed."""
    if text.startswith('---'):
        end = text.find('\n---', 3)
        if end != -1:
            return text[end + 4:]
    return text


def extract_wikilink_targets(value: Any) -> list[str]:
    """Pull wikilink stems from a string or list of strings."""
    if isinstance(value, str):
        values = [value]
    elif isinstance(value, list):
        values = value
    else:
        return []
    targets: list[str] = []
    for v in values:
        if not isinstance(v, str):
            continue
        for m in WIKILINK_RE.finditer(v):
            targets.append(m.group(1).strip())
    return targets


def build_content_index(
    vault: Path,
    skip_dirs: set[str] | None = None,
    skip_content: set[str] | None = None,
    skip_system: set[str] | None = None,
) -> tuple[list[tuple[Path, dict[str, Any], str]], dict[str, list[Path]], dict[Path, float]]:
    """Single vault walk with topdown directory pruning.

    Returns:
        content: list of (Path, frontmatter_dict, rel_str) for files with frontmatter
        by_name: dict[stem -> list[Path]] for ALL non-hidden .md files

    """
    _skip_dirs = skip_dirs if skip_dirs is not None else SKIP_DIRS
    _skip_content = skip_content if skip_content is not None else SKIP_CONTENT_CHECKS
    _skip_system = skip_system if skip_system is not None else SKIP_SYSTEM_SUBDIRS

    content: list[tuple[Path, dict[str, Any], str]] = []
    by_name: dict[str, list[Path]] = {}
    mtime_map: dict[Path, float] = {}
    vault_str = str(vault)

    for dirpath, dirnames, filenames in os.walk(vault_str, topdown=True, followlinks=False):
        rel_dir = Path(dirpath).relative_to(vault)
        parts = rel_dir.parts

        dirnames[:] = [
            d for d in dirnames
            if not d.startswith('.')
            and d not in _skip_dirs
        ]

        if parts == ('System',):
            dirnames[:] = [d for d in dirnames if d not in _skip_system]

        top = parts[0] if parts else ''
        for fname in filenames:
            if not (fname.endswith('.md') or fname.endswith('.base')):
                continue
            p_file = Path(dirpath) / fname
            rel = p_file.relative_to(vault)

            stat = p_file.stat()
            mtime_map[p_file] = stat.st_mtime

            if not top.startswith('.'):
                by_name.setdefault(p_file.stem, []).append(p_file)

            if not fname.endswith('.md'):
                continue

            if top in _skip_content:
                continue
            try:
                text_inner = p_file.read_text(encoding='utf-8')
            except Exception:
                continue
            fm = parse_frontmatter(text_inner)
            if not fm:
                continue
            rel_str = str(rel).replace('\\', '/')
            content.append((p_file, fm, rel_str))

    return content, by_name, mtime_map
