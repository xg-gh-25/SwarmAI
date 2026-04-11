#!/usr/bin/env bash
# PreToolUse hook: sanitize pytest commands
# Fixes 3 anti-patterns that cause session eviction and re-run loops:
# 1. "| tail" pipe — buffers all output, blocks STREAMING state
# 2. "| head" pipe — same buffering problem as tail
# 3. ".venv/bin/python -m pytest" — hardcodes venv path unnecessarily
#
# Data: 206 pytest commands in 24h, only 27% were correct.
# | tail: 57%, .venv/bin/python: 68%, | head: 12%

INPUT=$(cat)
CMD=$(echo "$INPUT" | jq -r '.tool_input.command // ""')

# Only act on pytest commands
if ! echo "$CMD" | grep -q 'pytest'; then
  exit 0
fi

MODIFIED="$CMD"
CHANGED=false

# Strip | tail (with optional flags like -20, -n 30, -f)
if echo "$MODIFIED" | grep -qE '\|\s*tail'; then
  MODIFIED=$(echo "$MODIFIED" | sed -E 's/\|[[:space:]]*tail[[:space:]]*[^|;&]*//')
  MODIFIED=$(echo "$MODIFIED" | sed 's/[[:space:]]*$//')
  CHANGED=true
fi

# Strip | head (same buffering problem)
if echo "$MODIFIED" | grep -qE '\|\s*head'; then
  MODIFIED=$(echo "$MODIFIED" | sed -E 's/\|[[:space:]]*head[[:space:]]*[^|;&]*//')
  MODIFIED=$(echo "$MODIFIED" | sed 's/[[:space:]]*$//')
  CHANGED=true
fi

# Normalize any .venv/bin/python variant → python
# Covers: .venv/bin/python, backend/.venv/bin/python, etc.
if echo "$MODIFIED" | grep -qE '[a-zA-Z./]*\.venv/bin/python'; then
  MODIFIED=$(echo "$MODIFIED" | sed -E 's|[a-zA-Z./]*\.venv/bin/python|python|g')
  CHANGED=true
fi

if [ "$CHANGED" = true ]; then
  jq -n \
    --arg cmd "$MODIFIED" \
    '{
      "hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "updatedInput": {"command": $cmd},
        "additionalContext": "pytest command sanitized: stripped output pipe and/or normalized python path. Never pipe pytest through tail/head — it causes output buffering, re-run loops, and session eviction. Use pytest native flags (-k, --lf, -x) for filtering."
      }
    }'
fi

exit 0
