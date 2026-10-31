#!/bin/bash
# Pre-commit: a commit that changes backend/tests/ must leave backend/tests/bvt_manifest.txt
# naming only files that exist. Deleting a test without its manifest line stopped the BVT
# (scripts/run_bvt.sh refuses a missing file) on main twice before anyone noticed.
# The repo's own discipline (AGENTS.md hook table): the local pre-commit hook runs it, and
# CI's BVT step fails the same way. Exit 0: nothing under backend/tests is staged, or the
# manifest is whole; 1: it names a missing file (the message says which).
set -u
root="$(git rev-parse --show-toplevel)" || exit 0
staged="$(git diff --cached --name-only -- backend/tests)"
[ -z "$staged" ] && exit 0
if ! err="$(cd "$root" && bash scripts/run_bvt.sh --list 2>&1 >/dev/null)"; then
    echo "$err" >&2
    echo "  ❌ Remove that line from backend/tests/bvt_manifest.txt (or restore the file), then commit again." >&2
    exit 1
fi
exit 0
