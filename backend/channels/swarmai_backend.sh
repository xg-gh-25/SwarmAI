#!/bin/bash
# SwarmAI Backend Daemon — wrapper script for launchd
#
# Starts the FastAPI backend on a fixed port (18321) with caffeinate
# to keep channels (Slack, etc.) and background jobs alive when
# macOS is locked or sleeping.
#
# Usage: launchd runs this via com.swarmai.backend.plist
#        Manual: ./swarmai_backend.sh
#
# Port conflict: if 18321 is already bound, exits 0 (launchd won't retry).

set -euo pipefail

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DAEMON_PORT=18321
# BACKEND_DIR can be set by the plist EnvironmentVariables (daemon mode)
# or derived from the script's location (manual mode / dev).
if [ -n "${SWARMAI_BACKEND_DIR:-}" ]; then
    BACKEND_DIR="${SWARMAI_BACKEND_DIR}"
else
    BACKEND_DIR="$(cd "$(dirname "$0")/.." && pwd)"
fi
VENV_PYTHON="${BACKEND_DIR}/.venv/bin/python"
LOG_DIR="${HOME}/.swarm-ai/logs"

# ---------------------------------------------------------------------------
# Port conflict check
# ---------------------------------------------------------------------------

if lsof -i :"${DAEMON_PORT}" -sTCP:LISTEN >/dev/null 2>&1; then
    echo "[swarmai-backend] Port ${DAEMON_PORT} already in use — another instance running. Exiting."
    exit 0  # Exit 0 so launchd doesn't spam restarts
fi

# ---------------------------------------------------------------------------
# Environment — inherit user's login shell PATH
# ---------------------------------------------------------------------------
# launchd starts daemons with a minimal PATH (/usr/bin:/bin:/usr/sbin:/sbin).
# The Claude CLI subprocess needs the full user PATH to resolve:
#   - credential_process (ada → ~/.toolbox/bin/ada)
#   - mise-managed runtimes (node, python)
#   - homebrew tools
# Source the login shell to pick up PATH additions from .zprofile/.bash_profile.

_login_shell="$(dscl . -read /Users/"$(whoami)" UserShell 2>/dev/null | awk '{print $2}')"
_login_shell="${_login_shell:-/bin/zsh}"
if _full_path="$("${_login_shell}" -l -c 'echo $PATH' 2>/dev/null)"; then
    export PATH="${_full_path}"
fi

# Fallback: ensure common tool directories are on PATH even if shell
# sourcing failed (e.g. non-interactive shell, missing profile).
for _dir in \
    "${HOME}/.toolbox/bin" \
    "${HOME}/.local/bin" \
    "${HOME}/.local/share/mise/shims" \
    "/opt/homebrew/bin" \
    "/usr/local/bin"; do
    case ":${PATH}:" in
        *":${_dir}:"*) ;;  # already present
        *) [ -d "${_dir}" ] && export PATH="${_dir}:${PATH}" ;;
    esac
done

mkdir -p "${LOG_DIR}"

# Fixed port for daemon mode (overrides config.py default)
export SWARMAI_PORT="${DAEMON_PORT}"

# Mark as daemon mode — Tauri reads this via /api/system/mode
export SWARMAI_MODE="daemon"

# Ensure AWS credentials are accessible (SSO tokens are file-based)
export HOME="${HOME}"

# Strip proxy vars — daemon manages its own networking
unset HTTP_PROXY HTTPS_PROXY http_proxy https_proxy ALL_PROXY all_proxy NO_PROXY no_proxy 2>/dev/null || true

# ---------------------------------------------------------------------------
# Resolve Python
# ---------------------------------------------------------------------------

if [ ! -x "${VENV_PYTHON}" ]; then
    echo "[swarmai-backend] ERROR: venv Python not found at ${VENV_PYTHON}" >&2
    exit 1
fi

# ---------------------------------------------------------------------------
# Launch with sleep prevention
# ---------------------------------------------------------------------------

echo "[swarmai-backend] Starting on port ${DAEMON_PORT} at $(date '+%Y-%m-%d %H:%M:%S')"
echo "[swarmai-backend] Backend dir: ${BACKEND_DIR}"
echo "[swarmai-backend] Python: ${VENV_PYTHON}"
echo "[swarmai-backend] PATH: ${PATH}"
echo "[swarmai-backend] ada: $(which ada 2>/dev/null || echo 'NOT FOUND')"

# caffeinate -is: prevent idle sleep (-i) and system sleep (-s)
# The backend process becomes a child of caffeinate — when it exits,
# caffeinate exits too, and launchd restarts everything.
cd "${BACKEND_DIR}"
exec caffeinate -is "${VENV_PYTHON}" -m uvicorn main:app \
    --host 127.0.0.1 \
    --port "${DAEMON_PORT}" \
    --log-level info
