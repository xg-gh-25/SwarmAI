#!/bin/bash
# SwarmAI Development Helper
# Usage:
#   ./dev.sh              — Start everything (backend + frontend)
#   ./dev.sh backend      — Restart backend only (after Python changes)
#   ./dev.sh frontend     — Start frontend only (backend already running)
#   ./dev.sh build        — Full production build (PyInstaller + Tauri + DMG)
#   ./dev.sh quick        — Quick build: skip PyInstaller, rebuild Tauri only
#   ./dev.sh kill         — Kill all dev processes
#   ./dev.sh status       — Show what's running
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

# Daemon constants
DAEMON_LABEL="com.swarmai.backend"
DAEMON_PORT=18321
DAEMON_API="http://127.0.0.1:${DAEMON_PORT}"
GUI_TARGET="gui/$(id -u)/${DAEMON_LABEL}"

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

_is_backend_running() {
    if [ -f "$BACKEND_PID_FILE" ]; then
        local pid=$(cat "$BACKEND_PID_FILE")
        if kill -0 "$pid" 2>/dev/null; then
            return 0
        fi
        rm -f "$BACKEND_PID_FILE"
    fi
    # Fallback: check by port
    lsof -i :$BACKEND_PORT -t >/dev/null 2>&1
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
    # Kill anything else on the port
    local pids=$(lsof -i :$BACKEND_PORT -t 2>/dev/null)
    if [ -n "$pids" ]; then
        _log "Killing processes on port $BACKEND_PORT: $pids"
        echo "$pids" | xargs kill -9 2>/dev/null || true
        sleep 0.5
    fi
}

_start_backend() {
    _kill_backend
    _log "Starting backend on port $BACKEND_PORT..."
    cd "$BACKEND_DIR"

    # Activate venv
    if [ ! -d ".venv" ]; then
        _log "Creating venv..."
        uv venv .venv
        source .venv/bin/activate
        uv sync --group dev
    else
        source .venv/bin/activate
    fi

    # Start in background, log to file
    DATABASE_TYPE=sqlite python main.py --port $BACKEND_PORT \
        > "$LOG_DIR/backend.log" 2>&1 &
    local pid=$!
    echo "$pid" > "$BACKEND_PID_FILE"

    # Wait for health check
    _log "Waiting for backend..."
    for i in $(seq 1 30); do
        if curl -s --max-time 1 "http://localhost:$BACKEND_PORT/api/health" >/dev/null 2>&1; then
            _ok "Backend running (PID $pid, port $BACKEND_PORT)"
            return 0
        fi
        # Check if process died
        if ! kill -0 "$pid" 2>/dev/null; then
            _err "Backend process died. Check $LOG_DIR/backend.log"
            tail -20 "$LOG_DIR/backend.log"
            return 1
        fi
        sleep 1
    done
    _err "Backend didn't respond in 30s. Check $LOG_DIR/backend.log"
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
    _start_backend
    _start_frontend
}

cmd_backend() {
    local start=$(date +%s)
    _start_backend
    _ok "Backend restarted in $(_build_time $start)"
    _log "Tail logs: tail -f $LOG_DIR/backend.log"
}

cmd_frontend() {
    if ! _is_backend_running; then
        _warn "Backend not running — starting it first..."
        _start_backend
    fi
    _start_frontend
}

cmd_build() {
    local start=$(date +%s)
    _log "Full production build..."
    cd "$DESKTOP_DIR"

    _log "Step 1/3: PyInstaller backend build..."
    npm run build:backend

    _log "Step 2/3: Frontend build..."
    npm run build

    _log "Step 3/3: Tauri build..."
    npm run tauri build

    _ok "Full build complete in $(_build_time $start)"

    # Show output
    local dmg=$(ls "$DESKTOP_DIR/src-tauri/target/release/bundle/dmg/"*.dmg 2>/dev/null | head -1)
    if [ -n "$dmg" ]; then
        _ok "DMG: $dmg ($(du -h "$dmg" | cut -f1))"
        _log "Install: open \"$dmg\""
    fi
}

cmd_quick() {
    local start=$(date +%s)
    _log "Quick build (skip PyInstaller, Tauri only)..."
    cd "$DESKTOP_DIR"

    # Check sidecar binary exists
    local binary="$DESKTOP_DIR/src-tauri/binaries/python-backend-aarch64-apple-darwin"
    if [ ! -f "$binary" ]; then
        _warn "No sidecar binary found — need full build first"
        _log "Running: ./dev.sh build"
        cmd_build
        return
    fi

    _log "Step 1/2: Frontend build..."
    npm run build

    _log "Step 2/2: Tauri build..."
    npm run tauri build

    _ok "Quick build complete in $(_build_time $start)"

    local dmg=$(ls "$DESKTOP_DIR/src-tauri/target/release/bundle/dmg/"*.dmg 2>/dev/null | head -1)
    if [ -n "$dmg" ]; then
        _ok "DMG: $dmg ($(du -h "$dmg" | cut -f1))"
    fi
}

cmd_kill() {
    _kill_backend
    # Kill Vite dev server
    local vite_pids=$(lsof -i :1420 -t 2>/dev/null)
    if [ -n "$vite_pids" ]; then
        _log "Killing Vite dev server..."
        echo "$vite_pids" | xargs kill -9 2>/dev/null || true
    fi
    _ok "All dev processes stopped"
}

cmd_status() {
    echo ""
    echo "SwarmAI Dev Status"
    echo "──────────────────"

    # Backend
    if _is_backend_running; then
        local pid=$(cat "$BACKEND_PID_FILE" 2>/dev/null || lsof -i :$BACKEND_PORT -t 2>/dev/null | head -1)
        local health=$(curl -s --max-time 2 "http://localhost:$BACKEND_PORT/api/health" 2>/dev/null)
        if echo "$health" | python3 -c "import sys,json; d=json.load(sys.stdin); exit(0 if d.get('status')=='ok' else 1)" 2>/dev/null; then
            _ok "Backend: running (PID $pid, port $BACKEND_PORT)"
        else
            _warn "Backend: process alive but not healthy (PID $pid)"
        fi
    else
        _err "Backend: not running"
    fi

    # Frontend
    if lsof -i :1420 -t >/dev/null 2>&1; then
        _ok "Frontend: Vite dev server running (port 1420)"
    else
        _err "Frontend: not running"
    fi

    # Sidecar binary
    local binary="$DESKTOP_DIR/src-tauri/binaries/python-backend-aarch64-apple-darwin"
    if [ -f "$binary" ]; then
        local age=$(( ($(date +%s) - $(stat -f %m "$binary")) / 3600 ))
        _ok "Sidecar: exists ($(du -h "$binary" | cut -f1), ${age}h old)"
    else
        _warn "Sidecar: not built"
    fi

    # Recent changes since last build
    echo ""
    _log "Recent uncommitted changes:"
    cd "$PROJECT_ROOT"
    git diff --stat HEAD 2>/dev/null | tail -5
    echo ""
}

# ── Daemon management ───────────────────────────────────────

_daemon_is_running() {
    launchctl print "$GUI_TARGET" &>/dev/null
}

_daemon_health() {
    curl -sf "${DAEMON_API}/health" 2>/dev/null
}

cmd_daemon() {
    local sub="${1:-status}"
    case "$sub" in
        restart)
            _log "Restarting daemon..."
            launchctl kickstart -k "$GUI_TARGET"
            _log "Waiting for health..."
            for i in $(seq 1 30); do
                sleep 1
                if _daemon_health >/dev/null; then
                    _ok "Daemon healthy on port ${DAEMON_PORT} (${i}s)"
                    return
                fi
            done
            _err "Daemon did not become healthy within 30s"
            _warn "Check: tail -30 ~/.swarm-ai/logs/backend-stderr.log"
            ;;
        stop)
            _log "Stopping daemon..."
            launchctl bootout "$GUI_TARGET" 2>/dev/null || true
            _ok "Daemon stopped"
            ;;
        start)
            if _daemon_is_running; then
                _warn "Daemon already running"
                _daemon_health && _ok "Healthy on port ${DAEMON_PORT}"
                return
            fi
            _log "Starting daemon..."
            launchctl bootstrap "gui/$(id -u)" "$HOME/Library/LaunchAgents/${DAEMON_LABEL}.plist"
            _log "Waiting for health..."
            for i in $(seq 1 30); do
                sleep 1
                if _daemon_health >/dev/null; then
                    _ok "Daemon healthy on port ${DAEMON_PORT} (${i}s)"
                    return
                fi
            done
            _err "Daemon did not become healthy within 30s"
            ;;
        status)
            if _daemon_is_running; then
                _ok "Daemon: running"
            else
                _err "Daemon: not running"
                return 1
            fi
            local health
            if health=$(_daemon_health); then
                echo "$health" | python3 -m json.tool 2>/dev/null || echo "$health"
            else
                _warn "API not responding on port ${DAEMON_PORT}"
            fi
            ;;
        logs)
            _log "Tailing daemon logs (Ctrl-C to stop)..."
            tail -f "$LOG_DIR/backend-stderr.log"
            ;;
        *)
            echo "Usage: ./dev.sh daemon [restart|stop|start|status|logs]"
            ;;
    esac
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
        echo "  build            Full production build (PyInstaller + Tauri → DMG)"
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
