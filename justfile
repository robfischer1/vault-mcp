# justfile — Python star local gate.
#
# `check` is the fast edit loop. `gate` is the full mirror of
# .forgejo/workflows/ci.yml — run it before you push and CI holds no surprises.
# `just` with no argument lists everything.
#
# Poured verbatim by copier (no .jinja suffix): just's {{ }} interpolation and
# Jinja's {{ }} are the same delimiters, so a .jinja suffix here would have Jinja
# eat every recipe parameter at pour time.

set shell := ["bash", "-euo", "pipefail", "-c"]

star := file_name(justfile_directory())

# List available recipes
default:
    @just --list

# Fast loop: lint, types, test. Run this constantly.
#
# Does NOT re-sync — `uv sync` on every invocation is the difference between a
# sub-second loop and a multi-second one. Run `just sync` when the lock changes.
check: lint types test

# Everything ci.yml runs, from a clean locked environment. Run before pushing.
gate: sync lint types test vuln sast

# Resolve the environment from the lock, exactly as CI does.
#
# --locked FAILS rather than silently re-resolving, so a drifted uv.lock is an
# error here instead of a surprise in CI. A bare `uv sync` also prunes dev deps
# that are installed but undeclared.
[doc('Resolve the environment from uv.lock, exactly as CI does')]
sync:
    uv sync --all-extras --locked

# Format + lint via the repo's pre-commit hooks (ruff et al.)
#
# ⚑ `--all-files` DOES NOT MEAN ALL FILES. It means every file GIT KNOWS
# ABOUT, so a newly written module is INVISIBLE to lint until it is staged:
# `just check` passes green, and the commit hook then fails on the same file
# for findings the gate just told you were not there.
#
# MEASURED TWICE IN ONE DAY, 2026-08-19. A new mnemosyne module carried five
# ruff findings (D401 x4, N818) through a green `just check`, surfacing only
# at `git add`. A new eris test carried a TC003 the same way hours earlier.
# Both were caught by the commit hook, which is the gate working — but it is
# the SLOW gate, after the message is written, and it teaches people that
# green means nothing.
#
# So a second pass covers what the first cannot see. It runs only when there
# ARE untracked files, and `--exclude-standard` honours .gitignore, so build
# output and .venv stay out of it. Two passes rather than one union list
# because each keeps pre-commit's own semantics intact — `--all-files` still
# does exactly what it always did.
lint:
    #!/usr/bin/env bash
    set -euo pipefail
    uv run pre-commit run --all-files --show-diff-on-failure
    mapfile -t untracked < <(git ls-files --others --exclude-standard)
    if [ ${#untracked[@]} -gt 0 ]; then
        printf 'lint: %d untracked file(s) --all-files would have skipped\n' "${#untracked[@]}"
        uv run pre-commit run --show-diff-on-failure --files "${untracked[@]}"
    fi

# BOTH type checkers, matching CI — they disagree, and CI runs both
types:
    uv run mypy src tests
    uv run pyright

[doc('Run the test suite')]
test *ARGS:
    uv run pytest -q {{ARGS}}

# Known-vulnerability scan. pip-audit is pulled ephemerally, not a project dep.
vuln:
    uv run --with pip-audit pip-audit

# Static analysis, scoped taint rules
sast:
    LANG=C.UTF-8 LC_ALL=C.UTF-8 opengrep scan --config rules/sast --error .

# Run the server from the working tree against the local environment.
#
# Python's fast path is running from source directly — there is no single-binary
# overlay trick as there is for a Go or Rust star, because the container's
# interpreter, site-packages and entrypoint all have to agree. Override MODULE
# when the import package name differs from the repo name.
[doc('Run the server from the working tree')]
dev MODULE=replace(star, '-', '_') *ARGS:
    uv run python -m {{MODULE}} {{ARGS}}

# Build the container exactly as CI does — catches Dockerfile drift `check` cannot
image:
    DOCKER_BUILDKIT=1 docker build --pull -t {{star}}:dev .

# Regenerate CHANGELOG.md from the commit log.
#
# The file is DERIVED — never hand-edit it. git-cliff buckets by git tag, so an
# untagged repo renders one [Unreleased] section; `just release` seeds the tag.
# Pulled ephemerally like pip-audit, and PINNED: an unpinned generator rewrites
# the whole file the day it changes its default template.
[doc('Regenerate CHANGELOG.md from the commit log')]
changelog:
    uvx git-cliff@2.13.1 --config cliff.toml -o CHANGELOG.md

# Compute the next semver, write it into the version literal, and regenerate.
#
# feat -> MINOR, fix -> PATCH, `!` or BREAKING CHANGE -> MAJOR (MINOR below 1.0).
# Prints what it would do unless you pass 1.
#
# THIS DOES NOT TAG, and that is the fix. The version LITERAL is authority: the
# wheel on the index is built from it, and the ourea door mints refs/tags/vX.Y.Z
# FROM it when the landing merges. This recipe used to run `git tag -a` without
# touching the literal, which names a new version while shipping the previous
# one — and hand-made version tags are forbidden fleet-wide for that reason.
#
# Measured 2026-08-13: no tag in the fleet carried this recipe's signature
# (an annotated tag whose message is just the tag name), so it had never been
# run. Every lightweight tag was door-minted; every annotated one was written
# by hand. A recipe that mints a tag the publisher will not honour is worse
# than no recipe.
#
# INVOKE AS `just release 1`, NOT `just release EXECUTE=1`. The second form
# looks like it sets the parameter and does not: just reads `NAME=value` as a
# variable assignment, so the RECIPE PARAMETER keeps its "0" default and the
# run silently dry-runs. That was the old recipe's own advice and it is the
# other reason this was never run — the documented invocation could not work.
[doc('Compute the next semver, write it to the version literal, regenerate the changelog')]
release EXECUTE="0":
    #!/usr/bin/env bash
    set -euo pipefail
    next="$(uvx git-cliff@2.13.1 --config cliff.toml --bumped-version)"
    bare="${next#v}"
    # Resolve the literal the way the door does — from pyproject, never by
    # guessing src/<repo>/__init__.py: the import package and the repo name
    # diverge often enough to matter (personal-history-db ships phdb).
    path="$(sed -n '/^\[tool\.hatch\.version\]/,/^\[/p' pyproject.toml | sed -n 's/^path *= *"\(.*\)"/\1/p' | head -1)"
    if [[ -z "${path}" ]]; then
        echo "no [tool.hatch.version] path in pyproject.toml — nothing to bump" >&2
        exit 1
    fi
    echo "next version: ${next}  (literal: ${path})"
    if [[ "{{EXECUTE}}" != "1" ]]; then
        echo "dry run — re-run as \`just release 1\` to write the literal + changelog"
        exit 0
    fi
    sed -i -E 's/^__version__[[:space:]]*=[[:space:]]*"[^"]+"/__version__ = "'"${bare}"'"/' "${path}"
    grep -qx "__version__ = \"${bare}\"" "${path}" || { echo "literal write failed in ${path}" >&2; exit 1; }
    uvx git-cliff@2.13.1 --config cliff.toml --bump -o CHANGELOG.md
    echo "wrote ${bare} to ${path}, regenerated CHANGELOG.md"
    echo "land it as: chore(release): ${bare} — the door mints ${next} when it merges"

[doc('Remove caches and __pycache__ trees')]
clean:
    rm -rf .pytest_cache .mypy_cache .ruff_cache
    find . -name __pycache__ -type d -prune -exec rm -rf {} +
