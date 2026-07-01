#!/usr/bin/env bash
# Thin human/pipeline wrapper around the SSOT code-security scanner.
#
# This is the SHIFT-LEFT (local + pipeline) caller — the same scanner CI runs.
# It is NOT a git hook: git-defender owns core.hooksPath, and installing our own
# hook there would disable Amazon's secret scanner. Run this manually before a
# `git push`, or let the pipeline's TEST/DELIVER stage call it.
#
# Usage:
#   scripts/security_scan.sh                 # scan the source tree (exit 1 on new findings)
#   scripts/security_scan.sh --update-baseline
#
# Exit codes are the scanner's own: 0 pass, 1 new finding (blocks), 2 infra error.
#
# Note: a full scan is ~90s (bandit over backend/ dominates). That is fine for an
# opt-in pre-push check; it is deliberately NOT wired into every commit.
set -euo pipefail

# Resolve repo root from this script's location (never cwd-dependent — the scanner
# anchors its baselines to repo root and bandit/detect-secrets are cwd-sensitive).
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

# Prefer the backend venv's python (has bandit + detect-secrets pinned); fall back
# to whatever python3 is on PATH (CI activates the venv itself).
PY="${REPO_ROOT}/backend/.venv/bin/python"
if [[ ! -x "${PY}" ]]; then
  PY="$(command -v python3 || command -v python)"
fi

exec "${PY}" "${SCRIPT_DIR}/security_scan.py" "$@"
