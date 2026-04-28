#!/bin/bash
# SwarmAI Hive — entry point for EC2/Linux cloud deployment
#
# Equivalent of channels/swarmai_backend.sh but for Linux (systemd).
# Differences from macOS version:
#   - No caffeinate (Linux doesn't sleep daemons)
#   - No dscl (no macOS Directory Service)
#   - ss instead of lsof for port check
#   - --host 0.0.0.0 (allow Caddy reverse proxy)
#   - stat -c instead of stat -f (Linux stat format)
#
# Usage: systemd runs this via swarmai-hive.service
#        Manual: ./swarmai-hive.sh

set -euo pipefail

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

HIVE_PORT="${SWARMAI_PORT:-18321}"
LOG_DIR="${HOME}/.swarm-ai/logs"

# Resolve backend directory (relative to this script)
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BACKEND_DIR="${SCRIPT_DIR}/../backend"
VENV_PYTHON="${BACKEND_DIR}/.venv/bin/python"

# ---------------------------------------------------------------------------
# Port conflict check
# ---------------------------------------------------------------------------

if ss -tlnp 2>/dev/null | grep -q ":${HIVE_PORT} "; then
    echo "[hive] Port ${HIVE_PORT} already in use — exiting"
    exit 1
fi

# ---------------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------------

mkdir -p "${LOG_DIR}"

# Fixed port for Hive mode
export SWARMAI_PORT="${HIVE_PORT}"

# Mark as Hive mode
export SWARMAI_MODE="hive"

# Claude SDK: use Bedrock, disable auto-memory
export CLAUDE_CODE_USE_BEDROCK="${CLAUDE_CODE_USE_BEDROCK:-1}"
export CLAUDE_CODE_DISABLE_AUTO_MEMORY=1

# Strip proxy vars — Hive manages its own networking
unset HTTP_PROXY HTTPS_PROXY http_proxy https_proxy ALL_PROXY all_proxy NO_PROXY no_proxy 2>/dev/null || true

# ---------------------------------------------------------------------------
# Resolve backend executable
# ---------------------------------------------------------------------------

if [ -x "${VENV_PYTHON}" ]; then
    echo "[hive] Starting SwarmAI Hive on port ${HIVE_PORT} at $(date '+%Y-%m-%d %H:%M:%S')"
    echo "[hive] Backend dir: ${BACKEND_DIR}"
    echo "[hive] Python: ${VENV_PYTHON}"
    echo "[hive] Mode: ${SWARMAI_MODE}"
    echo "[hive] PATH: ${PATH}"

    cd "${BACKEND_DIR}"
    # Bind to 127.0.0.1 — Caddy reverse-proxies locally.
    # Never bind 0.0.0.0: if Caddy crashes, backend would be directly exposed.
    exec "${VENV_PYTHON}" -m uvicorn main:app \
        --host 127.0.0.1 \
        --port "${HIVE_PORT}" \
        --log-level info
else
    echo "[hive] ERROR: No venv found at ${VENV_PYTHON}" >&2
    echo "[hive] Run: cd ${BACKEND_DIR} && python3 -m venv .venv && .venv/bin/pip install -e '.[all]'" >&2
    exit 1
fi
