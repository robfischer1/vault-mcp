# Contract: the source-modification clock

## The fallback chain (named, ordered, total)

`mtime` on a stored copy is resolved in exactly this order. The chain is total —
every input lands on one rung — and no rung fabricates a value.

| # | Source | When used | Format |
| :-- | :--- | :--- | :--- |
| 1 | **Source file's `st_mtime`** | a readable file exists at `file_path` | ISO-8601 UTC, second precision, `Z` suffix |
| 2 | **Frontmatter `updated`** | no accessible file, but the note declares one | verbatim as declared |
| 3 | **absent** | neither | attribute not written (not `""`, not `null`) |

Rung 1 beats rung 2 even when both exist and disagree — the filesystem time is
mechanical, the declared one is hand-maintained. Measured: the Aglaia plan
declares `updated: 2026-07-24` and was last written `2026-08-11 17:22`.

Rung 3 preserves the existing contract that an absent provenance value means
*unset*: `notes-sink.provenanceAttrs` only sets `mtime` when the payload carries
it, so omitting the key is the established way to say "unknown".

## `note_to_payloads` signature change

```python
def note_to_payloads(
    frontmatter, body, source_path, *, file_path=None,
    source_mtime: str | None = None,   # NEW — rung 1, resolved by the caller
) -> list[dict[str, Any]]: ...
```

The translator stays a **pure function**: it selects between rungs but never
stats. Callers holding a path resolve rung 1 and pass it. This keeps the
non-file case an ordinary argument rather than a branch, and keeps the
translator testable with no filesystem.

Back-compatible: omitting `source_mtime` reproduces today's behaviour exactly
(rung 2 then rung 3), so existing callers are unaffected until updated.

## `file_mtime_iso(path) -> str | None`

Resolves rung 1. Returns `None` — never raises — when the path is missing,
unreadable, or unstattable (FR-007: a stat failure must not fail a dissolve).

Format: `datetime.fromtimestamp(st_mtime, tz=UTC).isoformat(timespec="seconds")`
rendered with a `Z` suffix, e.g. `2026-08-11T21:22:29Z`. Timezone-explicit so
comparisons are unambiguous (FR-004); second precision because that is the
resolution the comparison needs and it keeps values stable across filesystems
with differing sub-second granularity.

## Staleness, as one comparison (SC-002)

```text
stale  ⟺  file_mtime_iso(path) > stored_mtime
```

String comparison is correct here because ISO-8601 UTC at fixed precision is
lexicographically ordered. No body read, no hash.

This is a **cheaper, weaker** test than F1's hash comparison, and both are kept
deliberately:

| | F1 hash | F2 timestamp |
| :--- | :--- | :--- |
| Cost | reads every body | reads an index |
| False "stale" | never | yes — a touched-but-unchanged file |
| False "current" | never | yes — a restored older file |

F4's trigger uses the timestamp to decide *what to look at*; F1's hash decides
*whether to write*. The cheap test gates the expensive one; it does not replace
it. A `current` verdict from the hash is still the only thing that suppresses a
write, so a false "stale" costs one wasted comparison, never a bad version.

## Backfill

Rides F1's sweep rather than adding a second traversal (F2's planning context:
"consumes F1's sweep"). A backfill pass reports, per plan, whether the stored
`mtime` could be corrected from a live source file, and names those it could not
(FR-006). Backfilling does not write a new body version — a stored copy whose
body still matches stays `current`; only the provenance value is reconciled.
