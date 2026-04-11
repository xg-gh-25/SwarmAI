#!/usr/bin/env bash
# PreToolUse hook: pytest safety system
#
# 2 structural guards:
#
# Guard 1: Full suite → auto-rewrite to targeted
#   If no specific test file in command, rewrites to only run
#   recently-changed test files (git diff HEAD~5).
#   Bypass: SWARMAI_SUITE=1 prefix for user-requested full suite.
#
# Guard 2: Sanitize
#   Strip | tail / | head pipes, normalize .venv/bin/python path.
#
# No cross-tab mutex — tabs are independent. Targeted tests are
# lightweight (seconds), so parallel runs across tabs are fine.
# The guard prevents full suite (700+ tests, 2-3 min) from running
# unless the user explicitly asks.
#
# Install: cp backend/scripts/pytest-safety-hook.sh .claude/hooks/reject-pytest-tail.sh

INPUT=$(cat)
CMD=$(echo "$INPUT" | jq -r '.tool_input.command // ""')

# Only act on pytest commands
if ! echo "$CMD" | grep -q 'pytest'; then
  exit 0
fi

REPO_ROOT="/Users/gawan/Desktop/SwarmAI-Workspace/swarmai"

# ── Guard 1: Full suite auto-rewrite ─────────────────────────
MODIFIED="$CMD"
CHANGED=false

HAS_TEST_FILE=false
if echo "$MODIFIED" | grep -qE 'test_[a-zA-Z0-9_]+\.py'; then
  HAS_TEST_FILE=true
fi

HAS_LAST_FAILED=false
if echo "$MODIFIED" | grep -qE '\-\-lf\b'; then
  HAS_LAST_FAILED=true
fi

HAS_BYPASS=false
if echo "$MODIFIED" | grep -q 'SWARMAI_SUITE=1'; then
  HAS_BYPASS=true
  # Strip the marker — it's a signal, not a real env var
  MODIFIED=$(echo "$MODIFIED" | sed 's/SWARMAI_SUITE=1[[:space:]]*//')
  CHANGED=true
fi

if [ "$HAS_TEST_FILE" = false ] && [ "$HAS_LAST_FAILED" = false ] && [ "$HAS_BYPASS" = false ]; then
  # Full suite detected — auto-rewrite to targeted tests
  CHANGED_TESTS=$(cd "$REPO_ROOT/backend" && git diff --name-only HEAD~5 -- 'tests/test_*.py' 2>/dev/null | head -8)

  if [ -z "$CHANGED_TESTS" ]; then
    jq -n '{
      "decision": "block",
      "reason": "Full test suite blocked. No recently-changed test files found. Specify test files, or user approves full suite with: SWARMAI_SUITE=1 python -m pytest ..."
    }'
    exit 0
  fi

  # Extract timeout from original command, default 60s
  TIMEOUT_FLAG=$(echo "$MODIFIED" | grep -oE '\-\-timeout[= ][0-9]+' | head -1)
  [ -z "$TIMEOUT_FLAG" ] && TIMEOUT_FLAG="--timeout=60"

  TEST_FILES=$(echo "$CHANGED_TESTS" | tr '\n' ' ')
  MODIFIED="cd $REPO_ROOT/backend && python -m pytest ${TEST_FILES}${TIMEOUT_FLAG} -v"
  CHANGED=true
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

# ── Output ────────────────────────────────────────────────────
if [ "$CHANGED" = true ]; then
  jq -n \
    --arg cmd "$MODIFIED" \
    '{
      "hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "updatedInput": {"command": $cmd},
        "additionalContext": "pytest: command rewritten to targeted tests or sanitized. Full suite requires SWARMAI_SUITE=1."
      }
    }'
fi

exit 0
