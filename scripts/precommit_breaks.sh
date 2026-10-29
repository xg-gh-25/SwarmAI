#!/bin/bash
# Pre-commit: the callers this commit breaks, from the repo's code graph.
#
# Runs `core.code_intel.changed --staged --breaks`: what the index would commit is
# compared with HEAD symbol by symbol, and each caller is checked against what the
# commit removes or makes required (a removed function still called, a removed
# keyword still passed, a new required parameter not passed). Only such definite
# breaks block the commit; anything else -- no code file staged, no backend
# environment, no code graph, a crash, the time cap -- lets it through, with a line
# when it could not check. CODE_DEFENDER_SKIP_LOCAL_HOOKS=true skips the local hooks.
REPO_ROOT="$(git rev-parse --show-toplevel)"
STAGED=$(git diff --cached --name-only --diff-filter=ACMRD | grep -E '\.(py|ts|tsx|js|jsx|mjs|cjs)$')
[ -z "$STAGED" ] && exit 0
PY="$REPO_ROOT/backend/.venv/bin/python"
if [ ! -x "$PY" ]; then
    echo "  (breaking-callers check skipped: no backend/.venv here)"
    exit 0
fi
OUT=$(cd "$REPO_ROOT/backend" && perl -e 'alarm 45; exec @ARGV' "$PY" -m core.code_intel.changed \
      --repo "$REPO_ROOT" --staged --breaks --budget 20 2>/dev/null)
RC=$?
if [ $RC -eq 1 ]; then
    echo "$OUT"
    echo ""
    echo "  ❌ This commit breaks the callers above (they still call it the old way)."
    echo "  Update them in this commit, or keep the old signature."
    echo "  A wrong report: CODE_DEFENDER_SKIP_LOCAL_HOOKS=true git commit … (skips the local hooks only)."
    exit 1
fi
if [ $RC -ne 0 ]; then
    echo "  (breaking-callers check could not run, exit $RC: committing unchecked)"
elif [ -n "$OUT" ]; then
    echo "$OUT"
fi
exit 0
