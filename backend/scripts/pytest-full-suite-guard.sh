#!/usr/bin/env bash
# PreToolUse hook (Claude Code project setting, .claude/settings.json): block a FULL
# pytest suite run unless the user asked for it (SWARMAI_SUITE=1 in the command).
#
# Targeted runs pass: a test file (test_*.py), --lf, -k, or a `make test-*` target.
# It never rewrites a command. Its earlier version also stripped `| tail` / `| head`
# and replaced `.venv/bin/python` with `python`, which made conftest exit with
# WRONG PYTHON; that part was removed (2026-10-21). The wall-clock cap is checked by
# the backend's pytest_command_guard, not here.

INPUT=$(cat)
CMD=$(echo "$INPUT" | jq -r '.tool_input.command // ""')

# Only act on an actual pytest run or a make test target, not grep / echo / python -c.
IS_PYTEST=$(echo "$CMD" | grep -cE 'python[0-9.]* -m pytest\b|(^|&&\s*|;\s*)\s*pytest\s')
IS_MAKE_TEST=$(echo "$CMD" | grep -cE '(^|&&\s*|;\s*)\s*make\s+test')
if [ "$IS_PYTEST" -eq 0 ] && [ "$IS_MAKE_TEST" -eq 0 ]; then
  exit 0
fi

HAS_TEST_FILE=$(echo "$CMD" | grep -cE 'test_[a-zA-Z0-9_]+\.py')
HAS_LF=$(echo "$CMD" | grep -cE '\-\-lf\b')
HAS_K=$(echo "$CMD" | grep -cE '(\s-k[[:space:]"'"'"']|(^|\s)-k\S)')
HAS_BYPASS=$(echo "$CMD" | grep -c 'SWARMAI_SUITE=1')
HAS_MAKE_TARGETED=$(echo "$CMD" | grep -cE 'make\s+test-\w+')

if [ "$HAS_BYPASS" -eq 0 ] && [ "$HAS_TEST_FILE" -eq 0 ] && [ "$HAS_LF" -eq 0 ] \
   && [ "$HAS_K" -eq 0 ] && [ "$HAS_MAKE_TARGETED" -eq 0 ]; then
  # Block: stderr + exit 2 (Claude Code hook convention)
  echo '{"decision":"block","reason":"Full test suite blocked. Run the test file(s) for the code you changed, e.g.: perl -e '"'"'alarm 100; exec @ARGV'"'"' python -m pytest tests/test_<module>.py -q --timeout=60. For the full suite the user must ask for it: SWARMAI_SUITE=1 in the command."}' >&2
  exit 2
fi
exit 0
