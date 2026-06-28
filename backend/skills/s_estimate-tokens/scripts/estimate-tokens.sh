#!/bin/bash
set -euo pipefail

# Token estimator — delegates to the CANONICAL calibrated estimator.
#
# run_3f25a73a: this script used to compute `wc -w * 1.8` against a hardcoded
# 200000-token window. `wc -w` counts a whole CJK paragraph as ~1 word, so it
# massively under-counted CJK; and 200K was the wrong window for our 1M models.
# It now shells out to the bundled estimate_tokens.py, which imports the SAME
# calibrated estimator (ContextDirectoryLoader.estimate_tokens) the prompt
# assembly uses — so the number reported matches what actually enters context.
# No CJK regex is re-rolled in bash (that would re-create the drift this fixes).
#
# Usage:
#   ./estimate-tokens.sh [--window N] <filepath> [filepath2 ...]
#   <command> | ./estimate-tokens.sh [--window N]

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="${SCRIPT_DIR}/estimate_tokens.py"

# Prefer python3; the bundled entry discovers the repo root itself.
exec python3 "$PY" "$@"
