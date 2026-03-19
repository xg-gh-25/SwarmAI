#!/bin/bash
# Starts the Python backend (if not running) before launching the Vite dev server.
# Called by beforeDevCommand in tauri.conf.json so that `npm run tauri:dev` works standalone.

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DESKTOP_DIR="$(dirname "$SCRIPT_DIR")"
PROJECT_ROOT="$(dirname "$DESKTOP_DIR")"
BACKEND_DIR="$PROJECT_ROOT/backend"
BACKEND_PORT=8000
BACKEND_PID_FILE="/tmp/swarmai-backend.pid"
LOG_DIR="$HOME/.swarm-ai/logs"

mkdir -p "$LOG_DIR"

_is_backend_running() {
    # Check PID file first
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

_start_backend() {
    echo "[swarm] Starting backend on port $BACKEND_PORT..."
    cd "$BACKEND_DIR"

    if [ ! -d ".venv" ]; then
        echo "[swarm] Creating venv..."
        uv venv .venv
        source .venv/bin/activate
        uv sync
    else
        source .venv/bin/activate
    fi

    DATABASE_TYPE=sqlite python main.py --port $BACKEND_PORT \
        > "$LOG_DIR/backend.log" 2>&1 &
    local pid=$!
    echo "$pid" > "$BACKEND_PID_FILE"

    # Wait for health check (max 30s)
    echo "[swarm] Waiting for backend health check..."
    for i in $(seq 1 30); do
        if curl -s --max-time 1 "http://localhost:$BACKEND_PORT/api/health" >/dev/null 2>&1; then
            echo "[swarm] ✅ Backend running (PID $pid, port $BACKEND_PORT)"
            break
        fi
        if ! kill -0 "$pid" 2>/dev/null; then
            echo "[swarm] ❌ Backend died. Check $LOG_DIR/backend.log"
            tail -20 "$LOG_DIR/backend.log"
            exit 1
        fi
        sleep 1
    done
}

# ── Main ──
if _is_backend_running; then
    echo "[swarm] ✅ Backend already running on port $BACKEND_PORT"
else
    _start_backend
fi

# Now start Vite dev server (this blocks — Tauri expects it to keep running)
cd "$DESKTOP_DIR"
exec npx vite
