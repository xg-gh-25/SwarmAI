#!/bin/bash
# SwarmAI Development Helper
# Usage:
#   ./dev.sh              — Start everything (backend + frontend) in dev mode
#   ./dev.sh backend      — Restart backend only (after Python changes)
#   ./dev.sh frontend     — Start frontend only (backend already running)
#   ./dev.sh deploy       — Build backend + deploy to daemon (the dev deploy path)
#   ./dev.sh quick        — Quick build: skip PyInstaller, rebuild Tauri only
#   ./dev.sh kill         — Kill all dev processes
#   ./dev.sh status       — Show what's running
#   ./dev.sh build        — (deprecated → runs deploy)
#
# Daemon & Channel management:
#   ./dev.sh daemon restart  — Restart the backend daemon (launchd)
#   ./dev.sh daemon stop     — Stop the daemon
#   ./dev.sh daemon start    — Start the daemon
#   ./dev.sh daemon status   — Show daemon status
#   ./dev.sh daemon logs     — Tail daemon logs (Ctrl-C to stop)
#   ./dev.sh channel restart [id] — Restart channel(s) via API
#   ./dev.sh channel stop [id]    — Stop channel(s)
#   ./dev.sh channel list         — List all channels + status

set -e

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$PROJECT_ROOT/backend"
DESKTOP_DIR="$PROJECT_ROOT/desktop"
BACKEND_PORT=8000
BACKEND_PID_FILE="/tmp/swarmai-backend.pid"
LOG_DIR="$HOME/.swarm-ai/logs"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
CYAN='\033[0;36m'
NC='\033[0m'

mkdir -p "$LOG_DIR"

# ── Helpers ─────────────────────────────────────────────────

_log()  { echo -e "${CYAN}[swarm]${NC} $*"; }
_ok()   { echo -e "${GREEN}✅${NC} $*"; }
_warn() { echo -e "${YELLOW}⚠️${NC}  $*"; }
_err()  { echo -e "${RED}❌${NC} $*"; }

# ── Port PID helpers (lsof with 2s timeout — dev script only) ──
# lsof is the ONLY way to get port→PID on macOS (no `ss`, netstat lacks PIDs).
# Wrapped with `timeout 2` to prevent macOS hang (sandbox/network extensions).
# Steering rule "no lsof" applies to daemon/production scripts (use nc -z for
# port checks). Dev scripts may use lsof with timeout for PID discovery.

_get_port_pid() {
    # Get first PID listening on a port. Returns empty string on failure/timeout.
    local port="$1"
    timeout 2 lsof -ti :"$port" 2>/dev/null | head -1
}

_kill_port_holders() {
    # Kill all processes listening on a port (with 2s timeout on PID lookup).
    local port="$1"
    local pids
    pids=$(timeout 2 lsof -ti :"$port" 2>/dev/null)
    if [ -n "$pids" ]; then
        _log "Killing processes on port $port: $pids"
        echo "$pids" | xargs kill -9 2>/dev/null || true
        sleep 0.5
    fi
}

# ── Daemon (shared library) ────────────────────────────────
_DAEMON_CMD="dev.sh"
_DAEMON_VERBOSE=1
source "$PROJECT_ROOT/scripts/daemon-lib.sh"

_is_backend_running() {
    if [ -f "$BACKEND_PID_FILE" ]; then
        local pid=$(cat "$BACKEND_PID_FILE")
        if kill -0 "$pid" 2>/dev/null; then
            return 0
        fi
        rm -f "$BACKEND_PID_FILE"
    fi
    # Fallback: check by port (nc -z is instant; lsof hangs on some macOS configs)
    nc -z 127.0.0.1 $BACKEND_PORT 2>/dev/null
}

_kill_backend() {
    if [ -f "$BACKEND_PID_FILE" ]; then
        local pid=$(cat "$BACKEND_PID_FILE")
        if kill -0 "$pid" 2>/dev/null; then
            _log "Stopping backend (PID $pid)..."
            kill "$pid" 2>/dev/null
            sleep 1
            kill -9 "$pid" 2>/dev/null || true
        fi
        rm -f "$BACKEND_PID_FILE"
    fi
    # Kill anything else on the port — use kill-by-port helper
    if nc -z 127.0.0.1 $BACKEND_PORT 2>/dev/null; then
        _kill_port_holders "$BACKEND_PORT"
    fi
}

_start_backend() {
    _kill_backend
    _log "Starting backend on port $BACKEND_PORT..."
    cd "$BACKEND_DIR"

    # Activate venv and sync deps (always sync to catch new dependencies)
    if [ ! -d ".venv" ]; then
        _log "Creating venv..."
        uv venv .venv
    fi
    source .venv/bin/activate
    uv sync --group dev

    # Start in background, log to separate dev file (don't clobber daemon's backend.log)
    # SWARMAI_MODE=dev prevents channel gateway startup (avoids Slack Socket Mode conflict
    # with the production daemon which may be running simultaneously).
    SWARMAI_MODE=dev DATABASE_TYPE=sqlite python main.py --port $BACKEND_PORT \
        > "$LOG_DIR/backend-dev.log" 2>&1 &
    local pid=$!
    echo "$pid" > "$BACKEND_PID_FILE"

    # Wait for health check
    _log "Waiting for backend..."
    for i in $(seq 1 30); do
        if curl -s --max-time 1 "http://localhost:$BACKEND_PORT/health" >/dev/null 2>&1; then
            _ok "Backend running (PID $pid, port $BACKEND_PORT)"
            return 0
        fi
        # Check if process died
        if ! kill -0 "$pid" 2>/dev/null; then
            _err "Backend process died. Check $LOG_DIR/backend-dev.log"
            tail -20 "$LOG_DIR/backend-dev.log"
            return 1
        fi
        sleep 1
    done
    _err "Backend didn't respond in 30s. Check $LOG_DIR/backend-dev.log"
    return 1
}

_start_frontend() {
    _log "Starting frontend dev server..."
    cd "$DESKTOP_DIR"
    npm install --silent 2>/dev/null
    npm run tauri:dev 2>&1 | tee "$LOG_DIR/frontend.log"
}

_build_time() {
    local start=$1
    local end=$(date +%s)
    local elapsed=$((end - start))
    local min=$((elapsed / 60))
    local sec=$((elapsed % 60))
    echo "${min}m ${sec}s"
}

# ── Commands ────────────────────────────────────────────────

cmd_start() {
    _log "Starting SwarmAI dev environment..."

    # Daemon conflict: kill + bootout daemon before dev mode to avoid two backends
    # (two ChannelGateways = duplicate Slack connections, DB write conflicts)
    # SIGKILL first (SSE streams block SIGTERM/bootout's internal SIGTERM for 20s)
    if _daemon_is_running; then
        _warn "Backend daemon running — stopping for dev mode..."
        launchctl kill SIGKILL "$GUI_TARGET" 2>/dev/null || true
        sleep 1
        launchctl bootout "$GUI_TARGET" 2>/dev/null || true
        _wait_port_free "$DAEMON_PORT" 5 || true
        _ok "Daemon stopped (will re-bootstrap on ./dev.sh kill or next app launch)"
    fi

    _start_backend
    _start_frontend
}

cmd_backend() {
    local start=$(date +%s)
    _start_backend
    _ok "Backend restarted in $(_build_time $start)"
    _log "Tail logs: tail -f $LOG_DIR/backend-dev.log"

    # NOTE: Daemon is NOT restarted here. The daemon runs the built binary,
    # not dev source code. Use './dev.sh deploy' to update the daemon.
    if _daemon_is_running; then
        _warn "Daemon is running (built binary). Dev backend on port $BACKEND_PORT."
        _warn "To update daemon: ./dev.sh deploy"
    fi
}

cmd_frontend() {
    if ! _is_backend_running; then
        _warn "Backend not running — starting it first..."
        _start_backend
    fi
    _start_frontend
}

cmd_build() {
    # DEPRECATED: Use ./prod.sh release for full DMG builds,
    # or ./dev.sh deploy for backend-only deploy to daemon.
    _warn "DEPRECATED: './dev.sh build' is deprecated."
    _warn "  Backend deploy: ./dev.sh deploy  (PyInstaller + deploy + restart)"
    _warn "  Full release:   ./prod.sh release (backend + DMG + tag + publish)"
    echo ""
    _log "Running './dev.sh deploy' for you..."
    cmd_deploy
}

cmd_deploy() {
    local start=$(date +%s)
    _log "Backend-only deploy to daemon (no frontend/Tauri)..."
    echo ""

    # Step 0: Sync versions
    _log "Step 0: Syncing version from VERSION file..."
    bash "$PROJECT_ROOT/scripts/sync-version.sh"
    echo ""

    # Step 1: PyInstaller
    _log "Step 1/3: PyInstaller backend build..."
    cd "$DESKTOP_DIR"
    npm run build:backend

    # Step 2: Verify
    _log "Step 2/3: Post-build verification..."
    cd "$BACKEND_DIR"
    if python scripts/verify_build.py "$BACKEND_BINARY"; then
        _ok "Verification passed"
    else
        _err "Verification FAILED — binary has missing capabilities"
        return 1
    fi

    # Step 3: Deploy then restart (deploy first — script may die on kill)
    _log "Step 3/3: Deploy to daemon..."
    local daemon_was_running=false
    if _daemon_is_running; then
        daemon_was_running=true
    fi

    _deploy_daemon_binary

    echo ""
    _ok "Deploy complete in $(_build_time $start)"

    # Kill old daemon so it restarts with new binary (deploy already done)
    # Use SIGKILL (not SIGTERM): SSE streams block SIGTERM indefinitely.
    # KeepAlive is safe here because deploy already finished (no rsync race).
    if [ "$daemon_was_running" = true ]; then
        _log "Restarting daemon to pick up new binary..."
        launchctl kill SIGKILL "$GUI_TARGET" 2>/dev/null || true
        _daemon_wait_healthy 90
    else
        _log "Daemon was not running. Start with: ./dev.sh daemon start"
    fi
}

cmd_quick() {
    local start=$(date +%s)
    _log "Quick build (skip PyInstaller, Tauri only)..."
    cd "$DESKTOP_DIR"

    # Check backend bundle exists (onedir, bundled into .app for daemon deployment)
    local binary="$DESKTOP_DIR/src-tauri/binaries/python-backend-aarch64-apple-darwin/python-backend"
    if [ ! -f "$binary" ]; then
        _warn "No backend bundle found — need full build first"
        _log "Running: ./dev.sh deploy"
        cmd_deploy
        return
    fi

    _log "Step 1/2: Frontend build..."
    npm run build

    _log "Step 2/2: Tauri build..."
    # Signing-key-aware wrapper: signs when TAURI_SIGNING_PRIVATE_KEY is set,
    # skips updater-artifact signing when absent (local dev). See tauri-build.sh.
    bash "$DESKTOP_DIR/scripts/tauri-build.sh"

    _ok "Quick build complete in $(_build_time $start)"

    local dmg=$(ls "$DESKTOP_DIR/src-tauri/target/release/bundle/dmg/"*.dmg 2>/dev/null | head -1)
    if [ -n "$dmg" ]; then
        _ok "DMG: $dmg ($(du -h "$dmg" | cut -f1))"
    fi
}

cmd_kill() {
    _kill_backend
    # Kill Vite dev server
    if nc -z 127.0.0.1 1420 2>/dev/null; then
        _log "Killing Vite dev server..."
        _kill_port_holders 1420
    fi
    _ok "All dev processes stopped"

    # Re-bootstrap daemon if plist exists (restore 24/7 Slack after dev session)
    local plist="$HOME/Library/LaunchAgents/${DAEMON_LABEL}.plist"
    if [ -f "$plist" ] && ! _daemon_is_running; then
        _log "Re-starting backend daemon (Slack/channels back online)..."
        cmd_daemon start
    fi
}

cmd_status() {
    echo ""
    echo "SwarmAI Dev Status"
    echo "──────────────────"

    # Backend
    if _is_backend_running; then
        local pid=$(cat "$BACKEND_PID_FILE" 2>/dev/null || _get_port_pid $BACKEND_PORT)
        local health=$(curl -s --max-time 2 "http://localhost:$BACKEND_PORT/health" 2>/dev/null)
        if echo "$health" | python3 -c "import sys,json; d=json.load(sys.stdin); exit(0 if d.get('status')=='healthy' else 1)" 2>/dev/null; then
            _ok "Backend: running (PID $pid, port $BACKEND_PORT)"
        else
            _warn "Backend: process alive but not healthy (PID $pid)"
        fi
    else
        _err "Backend: not running"
    fi

    # Frontend
    if nc -z 127.0.0.1 1420 2>/dev/null; then
        _ok "Frontend: Vite dev server running (port 1420)"
    else
        _err "Frontend: not running"
    fi

    # Daemon
    echo ""
    if _daemon_is_running; then
        local daemon_port=""
        local daemon_health=""
        # Read port from backend.json (source of truth written by running daemon)
        local bjson="$HOME/.swarm-ai/backend.json"
        if [ -f "$bjson" ]; then
            daemon_port=$(python3 -c "import json; print(json.load(open('$bjson')).get('port',''))" 2>/dev/null)
        fi
        : "${daemon_port:=$DAEMON_PORT}"  # fallback to hardcoded constant
        daemon_health=$(curl -sf --max-time 2 "http://127.0.0.1:${daemon_port}/health" 2>/dev/null)
        if [ -n "$daemon_health" ]; then
            local dver=$(echo "$daemon_health" | python3 -c "import sys,json; print(json.load(sys.stdin).get('version','?'))" 2>/dev/null)
            _ok "Daemon: healthy on port ${daemon_port} (v${dver})"
        else
            _warn "Daemon: launchd running but API not responding on port ${daemon_port}"
        fi
        _check_daemon_version 2>/dev/null || true
    else
        _err "Daemon: not running"
    fi

    # Backend bundle (PyInstaller onedir)
    local binary="$DESKTOP_DIR/src-tauri/binaries/python-backend-aarch64-apple-darwin/python-backend"
    if [ -f "$binary" ]; then
        local bundle_dir="$DESKTOP_DIR/src-tauri/binaries/python-backend-aarch64-apple-darwin"
        local age=$(( ($(date +%s) - $(stat -f %m "$binary")) / 3600 ))
        _ok "Backend bundle: exists ($(du -sh "$bundle_dir" | cut -f1), ${age}h old)"
    else
        _warn "Backend bundle: not built"
    fi

    # Recent changes since last build
    echo ""
    _log "Recent uncommitted changes:"
    cd "$PROJECT_ROOT"
    git diff --stat HEAD 2>/dev/null | tail -5
    echo ""
}

# ── Channel management ──────────────────────────────────────

_api() {
    # Helper: call daemon API, pretty-print JSON response
    local method="$1" path="$2"
    local resp
    resp=$(curl -sfL -X "$method" "${DAEMON_API}${path}" 2>&1) || {
        _err "API call failed: $method $path"
        _warn "Is daemon running? Try: ./dev.sh daemon status"
        return 1
    }
    echo "$resp" | python3 -m json.tool 2>/dev/null || echo "$resp"
}

cmd_channel() {
    local sub="${1:-list}"
    local channel_id="${2:-}"

    case "$sub" in
        list)
            _log "Channels:"
            _api GET /api/channels
            ;;
        restart)
            if [ -z "$channel_id" ]; then
                # Restart ALL channels
                _log "Restarting all channels..."
                local ids
                ids=$(curl -sfL "${DAEMON_API}/api/channels/" | python3 -c "
import sys, json
data = json.load(sys.stdin)
channels = data if isinstance(data, list) else data.get('channels', [])
for ch in channels:
    print(ch['id'])
" 2>/dev/null)
                if [ -z "$ids" ]; then
                    _warn "No channels found"
                    return
                fi
                for id in $ids; do
                    _log "  Restarting $id..."
                    _api POST "/api/channels/${id}/restart"
                done
                _ok "All channels restarted"
            else
                _log "Restarting channel $channel_id..."
                _api POST "/api/channels/${channel_id}/restart"
            fi
            ;;
        stop)
            if [ -z "$channel_id" ]; then
                _warn "Usage: ./dev.sh channel stop <channel_id>"
                _warn "Use './dev.sh channel list' to see channel IDs"
                return 1
            fi
            _log "Stopping channel $channel_id..."
            _api POST "/api/channels/${channel_id}/stop"
            ;;
        status)
            if [ -z "$channel_id" ]; then
                cmd_channel list
                return
            fi
            _log "Channel $channel_id:"
            _api GET "/api/channels/${channel_id}/status"
            ;;
        *)
            echo "Usage: ./dev.sh channel [list|restart|stop|status] [channel_id]"
            ;;
    esac
}

# ── Main ────────────────────────────────────────────────────

case "${1:-start}" in
    start)    cmd_start ;;
    backend)  cmd_backend ;;
    frontend) cmd_frontend ;;
    build)    cmd_build ;;
    deploy)   cmd_deploy ;;
    quick)    cmd_quick ;;
    kill)     cmd_kill ;;
    status)   cmd_status ;;
    daemon)   shift; cmd_daemon "$@" ;;
    channel)  shift; cmd_channel "$@" ;;
    *)
        echo "Usage: ./dev.sh [command]"
        echo ""
        echo "Commands:"
        echo "  start            Start backend + frontend (default)"
        echo "  backend          Restart backend only (after Python changes)"
        echo "  frontend         Start frontend only"
        echo "  deploy           Build backend + deploy to daemon (the dev deploy path)"
        echo "  build            (deprecated → runs deploy)"
        echo "  quick            Quick build: skip PyInstaller (frontend/Rust only)"
        echo "  kill             Stop all dev processes"
        echo "  status           Show what's running"
        echo ""
        echo "Daemon:"
        echo "  daemon restart   Restart the backend daemon (launchd)"
        echo "  daemon stop      Stop the daemon"
        echo "  daemon start     Start the daemon"
        echo "  daemon status    Show daemon health (default)"
        echo "  daemon logs      Tail daemon logs"
        echo ""
        echo "Channels:"
        echo "  channel list     List all channels + status"
        echo "  channel restart  Restart all channels (or: channel restart <id>)"
        echo "  channel stop <id>  Stop a specific channel"
        echo "  channel status [id]  Show channel status"
        ;;
esac
