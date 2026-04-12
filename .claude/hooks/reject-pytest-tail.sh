#!/usr/bin/env bash
# PreToolUse hook: pytest safety
#
# Guard 1: Block full suite unless user-requested (SWARMAI_SUITE=1)
# Guard 2: Sanitize — strip | tail / | head, normalize .venv/bin/python
#
# Targeted tests, --lf, -k, and make targets always pass through.
# Tabs are independent — no cross-tab mutex.
#
# Install: cp backend/scripts/pytest-safety-hook.sh .claude/hooks/reject-pytest-tail.sh

INPUT=$(cat)
CMD=$(echo "$INPUT" | jq -r '.tool_input.command // ""')

# Only act on actual pytest execution — not grep, echo, python -c, etc.
# Match: "python -m pytest" or bare "pytest" as a command (after && ; or start)
# Also match: "make test" variants (make test, make test-all, make test-file)
IS_PYTEST=$(echo "$CMD" | grep -cE 'python[0-9.]* -m pytest\b|(^|&&\s*|;\s*)\s*pytest\s')
IS_MAKE_TEST=$(echo "$CMD" | grep -cE '(^|&&\s*|;\s*)\s*make\s+test')

if [ "$IS_PYTEST" -eq 0 ] && [ "$IS_MAKE_TEST" -eq 0 ]; then
  exit 0
fi

MODIFIED="$CMD"
CHANGED=false

# ── Guard 1: Block full suite ─────────────────────────────────
# Allow if ANY of: specific test file, --lf, -k filter, SWARMAI_SUITE=1,
# or make with a specific target (make test-file, make test-lf)
HAS_TEST_FILE=$(echo "$MODIFIED" | grep -cE 'test_[a-zA-Z0-9_]+\.py')
HAS_LF=$(echo "$MODIFIED" | grep -cE '\-\-lf\b')
HAS_K=$(echo "$MODIFIED" | grep -cE '\s-k[[:space:]"'"'"']')
HAS_BYPASS=$(echo "$MODIFIED" | grep -c 'SWARMAI_SUITE=1')
HAS_MAKE_TARGETED=$(echo "$MODIFIED" | grep -cE 'make\s+test-(file|lf)')

if [ "$HAS_BYPASS" -gt 0 ]; then
  MODIFIED=$(echo "$MODIFIED" | sed 's/SWARMAI_SUITE=1[[:space:]]*//')
  CHANGED=true
elif [ "$HAS_TEST_FILE" -eq 0 ] && [ "$HAS_LF" -eq 0 ] && [ "$HAS_K" -eq 0 ] && [ "$HAS_MAKE_TARGETED" -eq 0 ]; then
  # Block: stderr + exit 2 (Claude Code hook convention)
  echo '{"decision":"block","reason":"Full test suite blocked. Specify test file(s) for the code you changed, e.g.: python -m pytest tests/test_<module>.py -v --timeout=60. For full suite, user must request with: SWARMAI_SUITE=1 python -m pytest --timeout=120"}' >&2
  exit 2
fi

# ── Guard 2: Sanitize ────────────────────────────────────────
if echo "$MODIFIED" | grep -qE '\|\s*tail'; then
  MODIFIED=$(echo "$MODIFIED" | sed -E 's/\|[[:space:]]*tail[[:space:]]*[^|;&]*//')
  MODIFIED=$(echo "$MODIFIED" | sed 's/[[:space:]]*$//')
  CHANGED=true
fi

if echo "$MODIFIED" | grep -qE '\|\s*head'; then
  MODIFIED=$(echo "$MODIFIED" | sed -E 's/\|[[:space:]]*head[[:space:]]*[^|;&]*//')
  MODIFIED=$(echo "$MODIFIED" | sed 's/[[:space:]]*$//')
  CHANGED=true
fi

if echo "$MODIFIED" | grep -qE '[a-zA-Z./]*\.venv/bin/python'; then
  MODIFIED=$(echo "$MODIFIED" | sed -E 's|[a-zA-Z./]*\.venv/bin/python|python|g')
  CHANGED=true
fi

if [ "$CHANGED" = true ]; then
  jq -n --arg cmd "$MODIFIED" \
    '{"hookSpecificOutput":{"hookEventName":"PreToolUse","updatedInput":{"command":$cmd},"additionalContext":"pytest: sanitized or SWARMAI_SUITE=1 stripped."}}'
fi

exit 0
