#!/bin/bash
# Pre-commit: a commit that changes backend/pyproject.toml must keep backend/uv.lock in
# step with it. CI, release builds and every Hive install exactly the locked versions
# (`uv sync --locked`), which refuses a lock that no longer matches; an unlocked install
# had pulled newer fastapi/mcp than any developer ran, and their import smoke failed.
# The repo's own discipline (AGENTS.md hook table): the local pre-commit hook runs it.
# Exit 0: pyproject.toml not staged, uv not installed (said on stderr), or the lock
# matches; 1: the lock needs `uv lock` (the message says so).
set -u
root="$(git rev-parse --show-toplevel)" || exit 0
[ -z "$(git diff --cached --name-only -- backend/pyproject.toml)" ] && exit 0
if ! command -v uv >/dev/null 2>&1; then
    echo "  ⚠️  uv is not installed: backend/uv.lock was not checked against pyproject.toml" >&2
    exit 0
fi
if ! out="$(cd "$root/backend" && uv lock --check 2>&1)"; then
    echo "$out" >&2
    echo "  ❌ backend/uv.lock does not match backend/pyproject.toml: run \`cd backend && uv lock\`, stage uv.lock, commit again." >&2
    exit 1
fi
exit 0
