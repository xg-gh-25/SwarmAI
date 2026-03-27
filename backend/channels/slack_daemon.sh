#!/bin/bash
# SwarmAI Slack Daemon — wrapper script for launchd
#
# Starts the FastAPI backend on a fixed port (18321) with caffeinate
# to keep the Slack bot responsive when macOS is locked or sleeping.
#
# Usage: launchd runs this via com.swarmai.slack-daemon.plist
#        Manual: ./slack_daemon.sh
#
# Port conflict: if 18321 is already bound, exits 0 (launchd won't retry).

set -euo pipefail

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DAEMON_PORT=18321
BACKEND_DIR="$(cd "$(dirname "$0")/.." && pwd)"
VENV_PYTHON="${BACKEND_DIR}/.venv/bin/python"
LOG_DIR="${HOME}/.swarm-ai/logs"

# ---------------------------------------------------------------------------
# Port conflict check
# ---------------------------------------------------------------------------

if lsof -i :"${DAEMON_PORT}" -sTCP:LISTEN >/dev/null 2>&1; then
    echo "[slack-daemon] Port ${DAEMON_PORT} already in use — another instance or Tauri backend is running. Exiting."
    exit 0  # Exit 0 so launchd doesn't spam restarts
fi

# ---------------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------------

mkdir -p "${LOG_DIR}"

# Fixed port for daemon mode (overrides config.py default)
export SWARMAI_PORT="${DAEMON_PORT}"

# Ensure AWS credentials are accessible (SSO tokens are file-based)
export HOME="${HOME}"

# Strip proxy vars — daemon manages its own networking
unset HTTP_PROXY HTTPS_PROXY http_proxy https_proxy ALL_PROXY all_proxy NO_PROXY no_proxy 2>/dev/null || true

# ---------------------------------------------------------------------------
# Resolve Python
# ---------------------------------------------------------------------------

if [ ! -x "${VENV_PYTHON}" ]; then
    echo "[slack-daemon] ERROR: venv Python not found at ${VENV_PYTHON}" >&2
    exit 1
fi

# ---------------------------------------------------------------------------
# Launch with sleep prevention
# ---------------------------------------------------------------------------

echo "[slack-daemon] Starting on port ${DAEMON_PORT} at $(date '+%Y-%m-%d %H:%M:%S')"
echo "[slack-daemon] Backend dir: ${BACKEND_DIR}"
echo "[slack-daemon] Python: ${VENV_PYTHON}"

# caffeinate -is: prevent idle sleep (-i) and system sleep (-s)
# The backend process becomes a child of caffeinate — when it exits,
# caffeinate exits too, and launchd restarts everything.
cd "${BACKEND_DIR}"
exec caffeinate -is "${VENV_PYTHON}" -m uvicorn main:app \
    --host 127.0.0.1 \
    --port "${DAEMON_PORT}" \
    --log-level info
