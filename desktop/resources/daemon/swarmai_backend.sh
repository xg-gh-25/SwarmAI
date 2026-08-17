#!/bin/bash
# SwarmAI Backend Daemon — wrapper script for launchd
#
# Runs the PyInstaller-built backend binary from ~/.swarm-ai/daemon/.
# This binary is deployed automatically by the Tauri desktop app on first launch.
#
# Port conflict: if 18321 is already bound, exits 0 (launchd won't retry).

set -euo pipefail

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DAEMON_PORT=18321
DAEMON_BINARY="${HOME}/.swarm-ai/daemon/python-backend"
LOG_DIR="${HOME}/.swarm-ai/logs"

# ---------------------------------------------------------------------------
# Log rotation — launchd has NO built-in rotation for StandardOut/ErrorPath,
# so backend-stdout.log / backend-stderr.log grow unbounded (observed 132MB,
# mixing months of bundled-CLI stderr + raw tracebacks that bypass Python's
# RotatingFileHandler). launchd opens these in APPEND mode, so truncate-in-
# place is safe: the kernel re-seeks to EOF on every write, so the inherited
# fd resumes writing at offset 0 with no sparse-file hole. Runs at every launch
# (RunAtLoad + each KeepAlive restart) → keeps these logs bounded over time.
# ---------------------------------------------------------------------------

_LOG_MAX_BYTES=$((20 * 1024 * 1024))   # rotate when a log exceeds 20MB
_LOG_KEEP_BYTES=$((4 * 1024 * 1024))   # keep last 4MB as the .1 backup

_rotate_log() {
    local f="$1"
    [ -f "$f" ] || return 0
    local size
    size="$(stat -f%z "$f" 2>/dev/null || echo 0)"
    if [ "$size" -gt "$_LOG_MAX_BYTES" ]; then
        # Keep two bounded backups (.1/.2). Use `tail -c` rather than `cp` so a
        # huge live file (132MB observed) never needs a transient 2x-disk copy —
        # and the recent tail is the useful part anyway. .1/.2 are not held open
        # by launchd, so mv is safe for them.
        [ -f "${f}.1" ] && mv -f "${f}.1" "${f}.2" 2>/dev/null || true
        tail -c "$_LOG_KEEP_BYTES" "$f" > "${f}.1" 2>/dev/null || true
        # Truncate the LIVE inode in place — never mv it, or launchd's open
        # append fd would follow the inode and keep writing to the backup.
        : > "$f" 2>/dev/null || true
        echo "[swarmai-backend] Rotated $(basename "$f") (was ${size} bytes)"
    fi
    return 0
}

mkdir -p "${LOG_DIR}"
_rotate_log "${LOG_DIR}/backend-stderr.log"
_rotate_log "${LOG_DIR}/backend-stdout.log"

# ---------------------------------------------------------------------------
# Port conflict check — uses nc -z (instant, no hang unlike lsof on macOS)
# ---------------------------------------------------------------------------

FAIL_STAMP="${HOME}/.swarm-ai/.daemon-port-fail"
if nc -z 127.0.0.1 "${DAEMON_PORT}" 2>/dev/null; then
    FAIL_COUNT=$(cat "$FAIL_STAMP" 2>/dev/null || echo 0)
    FAIL_COUNT=$((FAIL_COUNT + 1))
    echo "$FAIL_COUNT" > "$FAIL_STAMP"
    if [ "$FAIL_COUNT" -ge 3 ]; then
        echo "[swarmai-backend] Port ${DAEMON_PORT} occupied ${FAIL_COUNT}x — giving up (exit 0)"
        rm -f "$FAIL_STAMP"
        exit 0  # Stop retrying — something else genuinely owns this port
    fi
    echo "[swarmai-backend] Port ${DAEMON_PORT} in use (attempt ${FAIL_COUNT}/3) — retrying via launchd"
    exit 1  # Non-zero → launchd KeepAlive triggers retry after ThrottleInterval
fi
rm -f "$FAIL_STAMP"  # Port is free — reset failure counter

# ---------------------------------------------------------------------------
# Environment — inherit user's login shell PATH
# ---------------------------------------------------------------------------

_login_shell="$(dscl . -read /Users/"$(whoami)" UserShell 2>/dev/null | awk '{print $2}')"
_login_shell="${_login_shell:-/bin/zsh}"
if _full_path="$("${_login_shell}" -l -c 'echo $PATH' 2>/dev/null)"; then
    export PATH="${_full_path}"
fi

# Fallback: ensure common tool directories are on PATH
for _dir in \
    "${HOME}/.toolbox/bin" \
    "${HOME}/.local/bin" \
    "${HOME}/.local/share/mise/shims" \
    "/opt/homebrew/bin" \
    "/usr/local/bin"; do
    case ":${PATH}:" in
        *":${_dir}:"*) ;;
        *) [ -d "${_dir}" ] && export PATH="${_dir}:${PATH}" ;;
    esac
done

mkdir -p "${LOG_DIR}"

# Fixed port for daemon mode
export SWARMAI_PORT="${DAEMON_PORT}"
export SWARMAI_MODE="daemon"
export HOME="${HOME}"

# Desktop tab cold-start prewarm (run_4e881e96 — 6 findings 修复后启用).
# Both enabled together. The two flags are ORTHOGONAL, not co-dependent:
#   - DESKTOP_PREWARM: a NEW (no-history) desktop tab adopts a pre-spawned
#     baseline subprocess (skips the 8-14s cold handshake). A history-bearing
#     reopened tab NEVER adopts — the CRITICAL #1 history-guard routes it to
#     cold/--resume (its prior conversation is preserved there).
#   - RESUME_VIA_QUERY: on that cold/--resume path, the prior-conversation
#     block rides the query() instead of a rebuilt system_prompt (so a warm
#     subprocess reuse can't drop it). Enabled to keep resume robust now that
#     prewarm makes warm-reuse the common case.
export SWARM_DESKTOP_PREWARM="1"
export SWARM_RESUME_VIA_QUERY="true"

# Strip proxy vars — daemon manages its own networking
unset HTTP_PROXY HTTPS_PROXY http_proxy https_proxy ALL_PROXY all_proxy NO_PROXY no_proxy 2>/dev/null || true

# ---------------------------------------------------------------------------
# Launch
# ---------------------------------------------------------------------------

if [ -x "${DAEMON_BINARY}" ]; then
    echo "[swarmai-backend] Starting on port ${DAEMON_PORT} at $(date '+%Y-%m-%d %H:%M:%S')"
    echo "[swarmai-backend] Binary: ${DAEMON_BINARY}"
    VERSION_FILE="${HOME}/.swarm-ai/daemon/.version"
    if [ -f "${VERSION_FILE}" ]; then
        echo "[swarmai-backend] Version: $(cat "${VERSION_FILE}")"
    fi

    # caffeinate -i: prevent idle sleep only (allow lid-close/system sleep to save battery)
    exec caffeinate -i "${DAEMON_BINARY}" \
        --host 127.0.0.1 \
        --port "${DAEMON_PORT}"
else
    echo "[swarmai-backend] ERROR: No backend binary at ${DAEMON_BINARY}" >&2
    echo "[swarmai-backend] The Tauri app should have deployed this on first launch." >&2
    exit 1
fi
