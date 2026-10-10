# THE BUILD AND TEST LOOP RUNS ON THE CLUSTER, NOT ON THIS MACHINE. This library
# holds ONE private recipe, `_dev`, which calls foundry-tools' Dev surface
# (`dagger call dev`). That surface drives the same pinned images and cache
# volumes as the branch lanes, so a local answer and the gate's agree and the
# workstation needs neither a Go nor a Rust toolchain.
#
# RENDERED BY THE SPEC-KIT OVERRIDE TEMPLATE (foundry-stocks, speckit/), tracked,
# re-laid at every session start, beside stages.just. A star's justfile takes it
# with `import? 'dev.just'` and writes public recipes that call `_dev`; it
# defines no `_dev` of its own. `import?`, not `import`, so a tree that has not
# been re-laid yet still parses and `just check` (the commit hook) keeps working;
# the recipes then fail at the call, naming the missing `_dev`. Edit it in foundry-stocks, never in a star.
# `ca_module` comes from stages.just, so a justfile that imports this imports
# that too.
#
# THE CALLING CONVENTION IS FIXED, because every star's recipes spell it the same:
#
#   @{{just_executable()}} --justfile {{justfile()}} --set ca_module {{quote(ca_module)}} _dev <verb> "$@"
#
# (with `[positional-arguments]` on the caller). `_dev` is recursed into rather
# than named as a dependency so each argument keeps its boundary; a dependency
# hands over one space-joined string. `--set ca_module` carries the caller's
# value across the recursion, which would otherwise re-read CA_MODULE's default.
# `justfile()` and `justfile_directory()` inside an imported file name the
# IMPORTING justfile and its directory, not this file (MEASURED, just 1.58.0,
# from the root, from another cwd and from a subdirectory), so `--tree` below is
# the worktree whatever the caller's cwd.
#
# `--tree` IS REQUIRED, NOT A NICETY. It keys the cargo target cache by worktree.
# Without it every worktree of a repo shares one LOCKED target volume and queues
# on it, test binaries included. `--source=.` is stages.just's point again: the
# module's default is its own checkout, not this tree.
#
# ARGS PASS THROUGH to the tool: `just test uuid_shape`. The Dagger CLI reads
# `--args` as CSV, so a bare comma splits an argument (`--features a,b` would
# arrive as two). `_dev` quotes each argument per RFC 4180, which the CLI
# unquotes, so the comma survives. Only a newline cannot travel, and `_dev`
# refuses it rather than send something else.
#
# THE VERBS THAT ANSWER WITH FILES get `export`: fmt, lock, update, tidy write
# the files they changed back with `export --path=.`, which adds and rewrites
# and never deletes. VENDOR IS THE EXCEPTION: it needs `--wipe` so a module
# dropped from go.mod leaves vendor/, and `--wipe` empties its destination
# before writing. MEASURED against the cluster engine (dagger v0.21.10): wiping
# `--path=vendor` left a sibling untracked file alone and replaced vendor/'s
# contents; wiping `--path=.` left only the exported file and deleted
# everything else in the directory. So vendor exports to vendor/ and nothing
# else gets --wipe, and `_dev` refuses vendor in a tree with no vendor/ (the
# Dev surface refuses it too, but only after an engine round trip).
#
# VENDOR/ MUST BE A REAL DIRECTORY INSIDE THE TREE. `test -d` follows a symlink,
# and the export follows it too: MEASURED on the engine, `vendor -> ../outside`
# had outside/'s contents wiped. So a symlinked vendor/ is refused, and so is any
# vendor/ whose realpath is not under this directory.
#
# DEV_FRESH=1 appends --fresh to build, test, check, clippy and vet: re-run past
# the engine's exec cache on an unchanged tree (a flaky test, a suspect cache
# hit). Those are the verbs whose Dev function takes `fresh`; the file-writing
# verbs do not, so they never get it.
#
# A CLIENT THAT OUTLIVES ITS ENGINE FAILS INSTEAD OF HANGING, as in `check`
# (stages.just): 30 minutes, INT first, KILL after 30s, exit 124 says which.
# Every other exit code is the tool's, passed through.

# One Dev verb on the engine: `just _dev <verb> [tool args...]`.
[private]
[positional-arguments]
_dev VERB *ARGS:
    #!/usr/bin/env bash
    set -euo pipefail
    verb="$1"; shift
    csv=""
    for a in "$@"; do
        if [[ "$a" == *$'\n'* ]]; then
            echo "_dev: an argument holds a newline, which --args cannot carry: $a" >&2
            exit 2
        fi
        # An empty argument must be spelled "" or the CLI reads --args= as no list at all.
        if [[ -z "$a" ]]; then a='""'
        elif [[ "$a" == *[,\"]* ]]; then a="\"${a//\"/\"\"}\""
        fi
        csv+="${csv:+,}${a}"
    done
    if [[ "$verb" == vendor ]]; then
        root="{{justfile_directory()}}"
        if [[ ! -d "$root/vendor" ]]; then
            echo "_dev: vendor needs a vendor/ directory in $root; there is none, and exporting with --wipe into a tree without one has nothing to replace" >&2
            exit 2
        fi
        if [[ -L "$root/vendor" || "$(realpath "$root/vendor")" != "$(realpath "$root")"/* ]]; then
            echo "_dev: $root/vendor is a symlink or resolves outside the tree; --wipe would empty whatever it points at" >&2
            exit 2
        fi
    fi
    cmd=(dagger call -m "{{ca_module}}" dev --source=. --tree="{{justfile_directory()}}" "$verb")
    # --args whenever there are arguments: a lone '' is an argument, though its csv is empty.
    [[ $# -gt 0 ]] && cmd+=(--args="$csv")
    if [[ -n "${DEV_FRESH:-}" ]]; then
        case "$verb" in build|test|check|clippy|vet) cmd+=(--fresh) ;; esac
    fi
    case "$verb" in
        fmt|lock|update|tidy) cmd+=(export --path=.) ;;
        vendor) cmd+=(export --path=vendor --wipe) ;;
    esac
    if ! command -v timeout >/dev/null 2>&1; then
        exec "${cmd[@]}"
    fi
    set +e
    timeout --signal=INT --kill-after=30s 30m "${cmd[@]}"
    code=$?
    set -e
    if [[ "$code" -eq 124 ]]; then
        echo "$verb: ran 30 minutes without finishing — the engine it was talking to is gone or wedged, not slow; look at the engine, then run it again" >&2
    fi
    exit "$code"
