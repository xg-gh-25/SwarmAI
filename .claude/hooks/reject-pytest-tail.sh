#!/usr/bin/env bash
# PreToolUse hook: pytest safety
#
# Guard 1: Block full suite unless user-requested (SWARMAI_SUITE=1)
# Guard 2: Sanitize — strip | tail / | head, normalize .venv/bin/python
#
# Targeted tests and --lf always pass through untouched.
# Tabs are independent — no cross-tab mutex.
#
# Install: cp backend/scripts/pytest-safety-hook.sh .claude/hooks/reject-pytest-tail.sh

INPUT=$(cat)
CMD=$(echo "$INPUT" | jq -r '.tool_input.command // ""')

if ! echo "$CMD" | grep -q 'pytest'; then
  exit 0
fi

MODIFIED="$CMD"
CHANGED=false

# ── Guard 1: Block full suite ─────────────────────────────────
# Allow: has test file, has --lf, or has SWARMAI_SUITE=1 bypass
HAS_TEST_FILE=$(echo "$MODIFIED" | grep -cE 'test_[a-zA-Z0-9_]+\.py')
HAS_LF=$(echo "$MODIFIED" | grep -cE '\-\-lf\b')
HAS_BYPASS=$(echo "$MODIFIED" | grep -c 'SWARMAI_SUITE=1')

if [ "$HAS_BYPASS" -gt 0 ]; then
  MODIFIED=$(echo "$MODIFIED" | sed 's/SWARMAI_SUITE=1[[:space:]]*//')
  CHANGED=true
elif [ "$HAS_TEST_FILE" -eq 0 ] && [ "$HAS_LF" -eq 0 ]; then
  jq -n '{
    "decision": "block",
    "reason": "Full test suite blocked. Specify the test file(s) for the code you changed, e.g.: python -m pytest tests/test_<module>.py -v --timeout=60. For full suite, user must request with: SWARMAI_SUITE=1 python -m pytest --timeout=120"
  }'
  exit 0
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
