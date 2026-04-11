#!/usr/bin/env bash
# PreToolUse hook: pytest safety system
#
# 3-layer structural guard for multi-tab pytest safety:
#
# Guard 1: Cross-tab mutex (lockfile + mtime)
#   Only one pytest at a time. Lockfile auto-expires after 5 min.
#   Command wrapped so lockfile is released when pytest finishes.
#
# Guard 2: Full suite → auto-rewrite to targeted
#   If no specific test file in command, rewrites to only run
#   recently-changed test files (git diff). Bypass: SWARMAI_SUITE=1.
#
# Guard 3: Sanitize (existing)
#   Strip | tail / | head pipes, normalize .venv/bin/python path.
#
# Install: cp backend/scripts/pytest-safety-hook.sh .claude/hooks/reject-pytest-tail.sh
# Hook runs OUTSIDE sandbox (harness process).

INPUT=$(cat)
CMD=$(echo "$INPUT" | jq -r '.tool_input.command // ""')

# Only act on pytest commands
if ! echo "$CMD" | grep -q 'pytest'; then
  exit 0
fi

LOCKFILE="/tmp/swarmai-pytest.lock"
REPO_ROOT="/Users/gawan/Desktop/SwarmAI-Workspace/swarmai"

# ── Guard 1: Cross-tab mutex ──────────────────────────────────
if [ -f "$LOCKFILE" ]; then
  # Check mtime: if < 5 min old, another pytest is likely running
  if [ "$(uname)" = "Darwin" ]; then
    LOCK_AGE=$(( $(date +%s) - $(stat -f %m "$LOCKFILE") ))
  else
    LOCK_AGE=$(( $(date +%s) - $(stat -c %Y "$LOCKFILE") ))
  fi

  if [ "$LOCK_AGE" -lt 300 ]; then
    jq -n --arg age "$LOCK_AGE" \
      '{
        "decision": "block",
        "reason": ("pytest already running in another tab (" + $age + "s ago). Wait for it to finish. Do NOT retry automatically — inform the user.")
      }'
    exit 0
  fi
  # Stale lock (>5 min) — clean up and proceed
  rm -f "$LOCKFILE"
fi

# ── Guard 2: Full suite auto-rewrite ─────────────────────────
# Detect full suite: no test_*.py file specified, no --lf flag
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
  # Strip the env var from command — it's just a signal, not real env
  MODIFIED=$(echo "$MODIFIED" | sed 's/SWARMAI_SUITE=1[[:space:]]*//')
  CHANGED=true
fi

if [ "$HAS_TEST_FILE" = false ] && [ "$HAS_LAST_FAILED" = false ] && [ "$HAS_BYPASS" = false ]; then
  # Full suite detected — auto-rewrite to targeted tests
  CHANGED_TESTS=$(cd "$REPO_ROOT/backend" && git diff --name-only HEAD~5 -- 'tests/test_*.py' 2>/dev/null | head -8)

  if [ -z "$CHANGED_TESTS" ]; then
    # No changed test files — nothing to run
    jq -n '{
      "decision": "block",
      "reason": "Full test suite blocked (no specific test files). No recently-changed test files found. Either specify test files or ask the user to approve full suite with: SWARMAI_SUITE=1 python -m pytest ..."
    }'
    exit 0
  fi

  # Rewrite: replace full suite with targeted tests
  # Extract timeout flag from original command
  TIMEOUT_FLAG=$(echo "$MODIFIED" | grep -oE '\-\-timeout[= ][0-9]+' | head -1)
  [ -z "$TIMEOUT_FLAG" ] && TIMEOUT_FLAG="--timeout=60"

  # Build targeted command
  TEST_FILES=$(echo "$CHANGED_TESTS" | tr '\n' ' ')
  MODIFIED="cd $REPO_ROOT/backend && python -m pytest ${TEST_FILES}${TIMEOUT_FLAG} -v"
  CHANGED=true
fi

# ── Guard 3: Sanitize ────────────────────────────────────────
# Strip | tail
if echo "$MODIFIED" | grep -qE '\|\s*tail'; then
  MODIFIED=$(echo "$MODIFIED" | sed -E 's/\|[[:space:]]*tail[[:space:]]*[^|;&]*//')
  MODIFIED=$(echo "$MODIFIED" | sed 's/[[:space:]]*$//')
  CHANGED=true
fi

# Strip | head
if echo "$MODIFIED" | grep -qE '\|\s*head'; then
  MODIFIED=$(echo "$MODIFIED" | sed -E 's/\|[[:space:]]*head[[:space:]]*[^|;&]*//')
  MODIFIED=$(echo "$MODIFIED" | sed 's/[[:space:]]*$//')
  CHANGED=true
fi

# Normalize .venv/bin/python → python
if echo "$MODIFIED" | grep -qE '[a-zA-Z./]*\.venv/bin/python'; then
  MODIFIED=$(echo "$MODIFIED" | sed -E 's|[a-zA-Z./]*\.venv/bin/python|python|g')
  CHANGED=true
fi

# ── Wrap with lockfile lifecycle ──────────────────────────────
# touch lock before pytest, rm after — finish releases the mutex.
MODIFIED="touch $LOCKFILE && ($MODIFIED); _pytest_rc=\$?; rm -f $LOCKFILE; exit \$_pytest_rc"

# ── Output ────────────────────────────────────────────────────
jq -n \
  --arg cmd "$MODIFIED" \
  '{
    "hookSpecificOutput": {
      "hookEventName": "PreToolUse",
      "updatedInput": {"command": $cmd},
      "additionalContext": "pytest safety: mutex acquired, command may have been rewritten to targeted tests. Full suite requires SWARMAI_SUITE=1 prefix."
    }
  }'

exit 0
