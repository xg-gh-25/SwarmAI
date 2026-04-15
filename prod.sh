#!/bin/bash
# SwarmAI Production Operations
# Usage:
#   ./prod.sh build          — Build backend binary (PyInstaller + verify)
#   ./prod.sh release        — Full release build (backend + frontend + Tauri → DMG)
#   ./prod.sh deploy         — Deploy sidecar binary to daemon + restart
#   ./prod.sh verify         — Verify existing binary capabilities
#   ./prod.sh status         — Show daemon health, binary versions, staleness
#
# Daemon management:
#   ./prod.sh daemon restart — Restart the backend daemon (launchd)
#   ./prod.sh daemon stop    — Stop the daemon
#   ./prod.sh daemon start   — Start the daemon
#   ./prod.sh daemon status  — Show daemon health
#   ./prod.sh daemon logs    — Tail daemon logs (Ctrl-C to stop)

set -e

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$PROJECT_ROOT/backend"
DESKTOP_DIR="$PROJECT_ROOT/desktop"
LOG_DIR="$HOME/.swarm-ai/logs"

# Binary locations
SIDECAR_BINARY="$DESKTOP_DIR/src-tauri/binaries/python-backend-aarch64-apple-darwin"
DAEMON_BINARY_DIR="$HOME/.swarm-ai/daemon"
DAEMON_BINARY="$DAEMON_BINARY_DIR/python-backend"
DAEMON_VERSION_FILE="$DAEMON_BINARY_DIR/.version"

# Daemon constants
DAEMON_LABEL="com.swarmai.backend"
DAEMON_PORT=18321
DAEMON_API="http://127.0.0.1:${DAEMON_PORT}"
GUI_TARGET="gui/$(id -u)/${DAEMON_LABEL}"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

mkdir -p "$LOG_DIR"

# ── Helpers ─────────────────────────────────────────────────

_log()  { echo -e "${CYAN}[prod]${NC} $*"; }
_ok()   { echo -e "${GREEN}✅${NC} $*"; }
_warn() { echo -e "${YELLOW}⚠️${NC}  $*"; }
_err()  { echo -e "${RED}❌${NC} $*"; }

_build_time() {
    local start=$1
    local end=$(date +%s)
    local elapsed=$((end - start))
    local min=$((elapsed / 60))
    local sec=$((elapsed % 60))
    echo "${min}m ${sec}s"
}

_daemon_is_running() {
    launchctl print "$GUI_TARGET" &>/dev/null
}

_daemon_health_status() {
    local resp
    resp=$(curl -sf --max-time 2 "${DAEMON_API}/health" 2>/dev/null) || { echo "unreachable"; return; }
    local status
    status=$(echo "$resp" | python3 -c "import sys,json; print(json.load(sys.stdin).get('status','unknown'))" 2>/dev/null)
    echo "${status:-unknown}"
}

_wait_port_free() {
    local port="$1" max_wait="${2:-10}"
    for i in $(seq 1 "$max_wait"); do
        if ! lsof -i :"${port}" -sTCP:LISTEN >/dev/null 2>&1; then
            return 0
        fi
        sleep 0.5
    done
    return 1
}

_daemon_wait_healthy() {
    local timeout="${1:-90}"
    local saw_initializing=false

    for i in $(seq 1 "$timeout"); do
        local status
        status=$(_daemon_health_status)

        case "$status" in
            healthy)
                _ok "Daemon healthy on port ${DAEMON_PORT} (${i}s)"
                return 0
                ;;
            initializing)
                if ! $saw_initializing; then
                    _log "Server responding (initializing)..."
                    saw_initializing=true
                fi
                ;;
            unreachable)
                if (( i % 10 == 0 )); then
                    _log "Still waiting for port ${DAEMON_PORT}... (${i}s)"
                fi
                ;;
        esac
        sleep 1
    done

    _err "Daemon did not become healthy within ${timeout}s"
    _log "Check logs: ./prod.sh daemon logs"
    return 1
}

_check_daemon_version() {
    if [ ! -f "$DAEMON_VERSION_FILE" ]; then
        return 2
    fi
    local binary_hash
    binary_hash=$(awk '{print $1}' "$DAEMON_VERSION_FILE")
    local head_hash
    head_hash=$(cd "$PROJECT_ROOT" && git rev-parse --short HEAD 2>/dev/null || echo "unknown")

    if [ "$binary_hash" = "$head_hash" ]; then
        _ok "Binary matches HEAD ($head_hash)"
        return 0
    fi

    local behind
    behind=$(cd "$PROJECT_ROOT" && git rev-list --count "${binary_hash}..HEAD" 2>/dev/null || echo "?")
    _warn "Daemon binary is ${behind} commits behind HEAD"
    _warn "  Binary: ${binary_hash} ($(awk '{$1=""; print substr($0,2)}' "$DAEMON_VERSION_FILE"))"
    _warn "  HEAD:   ${head_hash}"

    local backend_changes
    backend_changes=$(cd "$PROJECT_ROOT" && git diff --name-only "${binary_hash}..HEAD" -- backend/ 2>/dev/null | wc -l | tr -d ' ')
    if [ "$backend_changes" -gt 0 ]; then
        _err "  ${backend_changes} backend file(s) changed — rebuild: ./prod.sh build"
        return 1
    else
        _log "  No backend changes — binary is functionally current"
        return 0
    fi
}

_deploy_daemon_binary() {
    if [ ! -f "$SIDECAR_BINARY" ]; then
        _err "No sidecar binary at $SIDECAR_BINARY"
        _err "Run ./prod.sh build first"
        return 1
    fi

    mkdir -p "$DAEMON_BINARY_DIR"

    # Atomic replace
    cp -f "$SIDECAR_BINARY" "${DAEMON_BINARY}.tmp"
    mv -f "${DAEMON_BINARY}.tmp" "$DAEMON_BINARY"
    chmod +x "$DAEMON_BINARY"
    _ok "Daemon binary deployed: $(du -h "$DAEMON_BINARY" | cut -f1)"

    # Write version file
    local git_hash
    git_hash=$(cd "$PROJECT_ROOT" && git rev-parse --short HEAD 2>/dev/null || echo "unknown")
    echo "$git_hash $(date '+%Y-%m-%d %H:%M:%S')" > "$DAEMON_VERSION_FILE"

    # Deploy resources
    local res_src="$DESKTOP_DIR/resources"
    local res_dst="$DAEMON_BINARY_DIR/resources"
    if [ -d "$res_src" ]; then
        mkdir -p "$res_dst"
        cp -f "$res_src"/*.json "$res_dst/" 2>/dev/null || true
        cp -f "$res_src"/*.db "$res_dst/" 2>/dev/null || true
        _ok "Resources deployed"
    fi
}

# ── Commands ────────────────────────────────────────────────

cmd_build() {
    local start=$(date +%s)
    echo ""
    echo -e "${BOLD}SwarmAI Production Build${NC}"
    echo "════════════════════════"
    echo ""

    # Step 0: Sync versions from VERSION file
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
    if python scripts/verify_build.py "$SIDECAR_BINARY"; then
        _ok "Verification passed — all capabilities present"
    else
        _err "Verification FAILED — do NOT release"
        echo ""
        _warn "Fix issues above, then re-run: ./prod.sh build"
        return 1
    fi

    # Step 3: Deploy to daemon
    _log "Step 3/3: Deploy to daemon..."
    _deploy_daemon_binary

    echo ""
    _ok "Build complete in $(_build_time $start)"
    _ok "Binary: $SIDECAR_BINARY ($(du -h "$SIDECAR_BINARY" | cut -f1))"

    # Auto-restart daemon if running (best-effort — build already succeeded)
    if _daemon_is_running; then
        echo ""
        _log "Daemon running — restarting to pick up new binary..."
        if ! cmd_daemon restart; then
            _warn "Daemon restart failed — try manually: ./prod.sh daemon restart"
            _warn "Build itself succeeded. Binary is deployed."
        fi
    else
        echo ""
        _log "Daemon not running. Start with: ./prod.sh daemon start"
    fi
}

cmd_release() {
    local start=$(date +%s)
    echo ""
    echo -e "${BOLD}╔══════════════════════════════════════╗${NC}"
    echo -e "${BOLD}║     SwarmAI Release Pipeline         ║${NC}"
    echo -e "${BOLD}╚══════════════════════════════════════╝${NC}"
    echo ""

    # ── Phase 1: Pre-flight checks ────────────────────────────
    echo -e "${BOLD}Phase 1/4: Pre-flight Checks${NC}"
    echo "────────────────────────────"
    local preflight_ok=true

    # 1a. Uncommitted changes
    cd "$PROJECT_ROOT"
    local dirty
    dirty=$(git status --porcelain 2>/dev/null | grep -v '^\?\?' | head -20)
    if [ -n "$dirty" ]; then
        _warn "Uncommitted changes detected:"
        echo "$dirty" | sed 's/^/    /'
        echo ""
        echo -n "  Continue anyway? [y/N] "
        read -r answer
        if [[ ! "$answer" =~ ^[Yy] ]]; then
            _err "Aborted — commit or stash first"
            return 1
        fi
    else
        _ok "Working tree clean"
    fi

    # 1b. Version sync + check
    _log "Syncing version from VERSION file..."
    bash "$PROJECT_ROOT/scripts/sync-version.sh"
    local version
    version=$(tr -d '[:space:]' < "$PROJECT_ROOT/VERSION")
    _ok "Version: ${version}"

    # 1c. Check if version tag already exists
    if git rev-parse "v${version}" &>/dev/null; then
        _warn "Tag v${version} already exists — did you forget to bump version?"
        echo -n "  Continue with same version? [y/N] "
        read -r answer
        if [[ ! "$answer" =~ ^[Yy] ]]; then
            _err "Aborted — bump VERSION file first"
            return 1
        fi
    fi

    # 1d. Run targeted backend tests
    _log "Running backend tests..."
    cd "$BACKEND_DIR"
    if SWARMAI_SUITE=1 python -m pytest --timeout=120 -x -q --tb=short 2>&1 | tail -5; then
        _ok "Backend tests passed"
    else
        _err "Tests FAILED"
        echo -n "  Continue despite test failure? [y/N] "
        read -r answer
        if [[ ! "$answer" =~ ^[Yy] ]]; then
            _err "Aborted — fix tests first"
            return 1
        fi
        preflight_ok=false
    fi

    echo ""

    # ── Phase 2: Build ────────────────────────────────────────
    echo -e "${BOLD}Phase 2/4: Build${NC}"
    echo "────────────────"

    # 2a. PyInstaller
    _log "Step 1/4: PyInstaller backend build..."
    cd "$DESKTOP_DIR"
    npm run build:backend

    # 2b. Verify binary (38 capability checks)
    _log "Step 2/4: Post-build verification (38 checks)..."
    cd "$BACKEND_DIR"
    if python scripts/verify_build.py "$SIDECAR_BINARY"; then
        _ok "All capabilities verified"
    else
        _err "Verification FAILED — aborting release"
        _err "Fix the missing modules, then re-run: ./prod.sh release"
        return 1
    fi

    # 2c. Frontend
    _log "Step 3/4: Frontend build..."
    cd "$DESKTOP_DIR"
    npm run build

    # 2d. Tauri → DMG
    _log "Step 4/4: Tauri build → DMG..."
    npm run tauri build

    echo ""

    # ── Phase 3: Deploy + Verify ──────────────────────────────
    echo -e "${BOLD}Phase 3/4: Deploy & Verify${NC}"
    echo "──────────────────────────"

    # 3a. Deploy to daemon
    _deploy_daemon_binary

    # 3b. Restart daemon with new binary (best-effort — build already succeeded)
    if _daemon_is_running; then
        _log "Restarting daemon with new binary..."
        cmd_daemon restart || _warn "Daemon restart failed — try: ./prod.sh daemon restart"
    else
        _log "Starting daemon..."
        cmd_daemon start || _warn "Daemon start failed — try: ./prod.sh daemon start"
    fi

    # 3c. Verify daemon health after restart
    local health=$(_daemon_health_status)
    if [ "$health" = "healthy" ]; then
        _ok "Daemon healthy with new binary"
    else
        _warn "Daemon status: $health — build succeeded, daemon may need manual restart"
    fi

    echo ""

    # ── Phase 4: Smoke Test Checklist ─────────────────────────
    local dmg=$(ls "$DESKTOP_DIR/src-tauri/target/release/bundle/dmg/"*.dmg 2>/dev/null | head -1)

    echo -e "${BOLD}Phase 4/4: Smoke Test Checklist${NC}"
    echo "───────────────────────────────"
    echo ""
    echo -e "  ${CYAN}Install the DMG and verify these manually:${NC}"
    echo ""
    echo "  ┌─────────────────────────────────────────────────────┐"
    echo "  │  □  1. Install DMG → open app → no crash            │"
    echo "  │  □  2. Send a message → streaming works             │"
    echo "  │  □  3. Multi-turn → context preserved               │"
    echo "  │  □  4. Close app → reopen → chat history intact     │"
    echo "  │  □  5. Slack: send a DM → reply arrives             │"
    echo "  │  □  6. SwarmWS explorer → files load                │"
    echo "  │  □  7. Settings page → no errors                    │"
    echo "  └─────────────────────────────────────────────────────┘"
    echo ""

    if [ -n "$dmg" ]; then
        echo -e "  ${GREEN}DMG ready:${NC} $(basename "$dmg") ($(du -h "$dmg" | cut -f1))"
        echo -e "  ${CYAN}Install:${NC}  open \"$dmg\""
    else
        _warn "  DMG not found — check build output"
    fi

    echo ""

    # ── Summary ───────────────────────────────────────────────
    echo -e "${BOLD}════════════════════════════════════════${NC}"
    echo -e "${BOLD}  Release Summary${NC}"
    echo -e "${BOLD}════════════════════════════════════════${NC}"
    echo ""
    echo "  Version:    ${version}"
    echo "  Commit:     $(cd "$PROJECT_ROOT" && git rev-parse --short HEAD)"
    echo "  Built in:   $(_build_time $start)"
    echo "  Binary:     $(du -h "$SIDECAR_BINARY" | cut -f1)"
    if [ -n "$dmg" ]; then
        echo "  DMG:        $(du -h "$dmg" | cut -f1)"
    fi
    echo "  Daemon:     $(_daemon_health_status)"
    if [ "$preflight_ok" = true ]; then
        echo -e "  Tests:      ${GREEN}passed${NC}"
    else
        echo -e "  Tests:      ${YELLOW}passed with warnings${NC}"
    fi
    echo ""
    _ok "Build pipeline complete. Run smoke tests above before shipping."
    echo ""
}

cmd_deploy() {
    echo ""
    _log "Deploying sidecar binary to daemon..."

    if [ ! -f "$SIDECAR_BINARY" ]; then
        _err "No sidecar binary found. Run ./prod.sh build first."
        return 1
    fi

    _deploy_daemon_binary

    if _daemon_is_running; then
        _log "Restarting daemon..."
        cmd_daemon restart || _warn "Daemon restart failed — try: ./prod.sh daemon restart"
    else
        _ok "Deployed. Start daemon with: ./prod.sh daemon start"
    fi
}

cmd_verify() {
    echo ""
    _log "Running post-build verification..."

    local target="${1:-$SIDECAR_BINARY}"
    if [ ! -f "$target" ]; then
        # Fallback to daemon binary
        target="$DAEMON_BINARY"
    fi
    if [ ! -f "$target" ]; then
        _err "No binary found. Run ./prod.sh build first."
        return 1
    fi

    _log "Verifying: $target"
    cd "$BACKEND_DIR"
    python scripts/verify_build.py "$target"
}

cmd_status() {
    echo ""
    echo -e "${BOLD}SwarmAI Production Status${NC}"
    echo "═════════════════════════"
    echo ""

    # Sidecar binary
    if [ -f "$SIDECAR_BINARY" ]; then
        local age=$(( ($(date +%s) - $(stat -f %m "$SIDECAR_BINARY")) / 3600 ))
        _ok "Sidecar binary: $(du -h "$SIDECAR_BINARY" | cut -f1), ${age}h old"
    else
        _err "Sidecar binary: not built"
    fi

    # Daemon binary
    if [ -f "$DAEMON_BINARY" ]; then
        local age=$(( ($(date +%s) - $(stat -f %m "$DAEMON_BINARY")) / 3600 ))
        _ok "Daemon binary:  $(du -h "$DAEMON_BINARY" | cut -f1), ${age}h old"
        _check_daemon_version || true
    else
        _err "Daemon binary:  not deployed"
    fi

    echo ""

    # Daemon process
    if _daemon_is_running; then
        local health=$(_daemon_health_status)
        case "$health" in
            healthy)      _ok "Daemon: running, healthy (port ${DAEMON_PORT})" ;;
            initializing) _warn "Daemon: running, initializing..." ;;
            *)            _warn "Daemon: running, status=$health" ;;
        esac

        # Show uptime from health endpoint
        local resp
        resp=$(curl -sf --max-time 2 "${DAEMON_API}/health" 2>/dev/null)
        if [ -n "$resp" ]; then
            echo "$resp" | python3 -c "
import sys, json
d = json.load(sys.stdin)
uptime = d.get('uptime_seconds', 0)
h, m = divmod(int(uptime), 3600)
m, s = divmod(m, 60)
tabs = d.get('active_sessions', d.get('sessions', '?'))
print(f'  Uptime: {h}h {m}m {s}s  |  Sessions: {tabs}')
" 2>/dev/null || true
        fi
    else
        _err "Daemon: not running"
    fi

    # DMG
    echo ""
    local dmg=$(ls "$DESKTOP_DIR/src-tauri/target/release/bundle/dmg/"*.dmg 2>/dev/null | head -1)
    if [ -n "$dmg" ]; then
        local age=$(( ($(date +%s) - $(stat -f %m "$dmg")) / 3600 ))
        _ok "DMG: $(du -h "$dmg" | cut -f1), ${age}h old"
        _log "  $dmg"
    else
        _warn "DMG: not built (run ./prod.sh release)"
    fi

    # Recent backend commits since binary
    echo ""
    if [ -f "$DAEMON_VERSION_FILE" ]; then
        local binary_hash
        binary_hash=$(awk '{print $1}' "$DAEMON_VERSION_FILE")
        local changes
        changes=$(cd "$PROJECT_ROOT" && git log --oneline "${binary_hash}..HEAD" -- backend/ 2>/dev/null)
        if [ -n "$changes" ]; then
            _warn "Backend commits since last build:"
            echo "$changes" | head -5 | sed 's/^/  /'
            local total
            total=$(echo "$changes" | wc -l | tr -d ' ')
            if [ "$total" -gt 5 ]; then
                _log "  ... and $((total - 5)) more"
            fi
        else
            _ok "No backend changes since last build"
        fi
    fi
    echo ""
}

# ── Daemon management ──────────────────────────────────────

_bootstrap_daemon() {
    # Bootstrap with retry — macOS launchctl sometimes fails with
    # "Bootstrap failed: 5: Input/output error" when bootout hasn't
    # fully cleaned up. A short wait + retry fixes it reliably.
    local plist="$HOME/Library/LaunchAgents/${DAEMON_LABEL}.plist"
    local max_attempts=3

    for attempt in $(seq 1 "$max_attempts"); do
        if launchctl bootstrap "gui/$(id -u)" "$plist" 2>/dev/null; then
            return 0
        fi
        if [ "$attempt" -lt "$max_attempts" ]; then
            _log "Bootstrap attempt $attempt failed — retrying in 2s..."
            sleep 2
            # Ensure fully cleaned up before retry
            launchctl bootout "$GUI_TARGET" 2>/dev/null || true
            sleep 1
        fi
    done

    _err "Bootstrap failed after $max_attempts attempts"
    _warn "Try manually: launchctl bootstrap gui/$(id -u) $plist"
    return 1
}

cmd_daemon() {
    local sub="${1:-status}"
    case "$sub" in
        restart)
            _check_daemon_version || true

            _log "Stopping daemon..."
            launchctl bootout "$GUI_TARGET" 2>/dev/null || true

            _log "Waiting for port ${DAEMON_PORT} to release..."
            if ! _wait_port_free "$DAEMON_PORT" 15; then
                _warn "Port still in use — force-killing..."
                local stale_pids
                stale_pids=$(lsof -i :"${DAEMON_PORT}" -t 2>/dev/null)
                if [ -n "$stale_pids" ]; then
                    echo "$stale_pids" | xargs kill -9 2>/dev/null || true
                    sleep 1
                fi
            fi

            _log "Starting daemon..."
            _bootstrap_daemon
            _daemon_wait_healthy 90
            ;;
        stop)
            _log "Stopping daemon..."
            launchctl bootout "$GUI_TARGET" 2>/dev/null || true
            if _wait_port_free "$DAEMON_PORT" 10; then
                _ok "Daemon stopped (port ${DAEMON_PORT} released)"
            else
                _warn "Daemon stopped but port ${DAEMON_PORT} may still be lingering"
            fi
            ;;
        start)
            if _daemon_is_running; then
                _warn "Daemon already running"
                local health=$(_daemon_health_status)
                [ "$health" = "healthy" ] && _ok "Healthy on port ${DAEMON_PORT}"
                return
            fi
            _check_daemon_version || true
            _log "Starting daemon..."
            _bootstrap_daemon
            _daemon_wait_healthy 90
            ;;
        status)
            if _daemon_is_running; then
                _ok "Daemon: running (launchd)"
            else
                _err "Daemon: not running"
                return 1
            fi
            local health
            if health=$(curl -sf --max-time 2 "${DAEMON_API}/health"); then
                echo "$health" | python3 -m json.tool 2>/dev/null || echo "$health"
            else
                _warn "API not responding on port ${DAEMON_PORT}"
            fi
            _check_daemon_version 2>/dev/null || true
            ;;
        logs)
            _log "Tailing daemon logs (Ctrl-C to stop)..."
            tail -f "$LOG_DIR/backend-stderr.log"
            ;;
        *)
            echo "Usage: ./prod.sh daemon [restart|stop|start|status|logs]"
            ;;
    esac
}

# ── Preflight (standalone) ─────────────────────────────────

cmd_preflight() {
    echo ""
    echo -e "${BOLD}Release Preflight Check${NC}"
    echo "═══════════════════════"
    echo ""
    local all_ok=true

    # Working tree
    cd "$PROJECT_ROOT"
    local dirty
    dirty=$(git status --porcelain 2>/dev/null | grep -v '^\?\?' | wc -l | tr -d ' ')
    if [ "$dirty" -gt 0 ]; then
        _warn "Uncommitted changes: $dirty file(s)"
        git status --porcelain 2>/dev/null | grep -v '^\?\?' | head -5 | sed 's/^/    /'
        all_ok=false
    else
        _ok "Working tree clean"
    fi

    # Version
    local version
    version=$(python3 -c "
import json
with open('$DESKTOP_DIR/src-tauri/tauri.conf.json') as f:
    print(json.load(f).get('version', '?'))
" 2>/dev/null)
    _ok "Version: ${version}"
    if git rev-parse "v${version}" &>/dev/null; then
        _warn "Tag v${version} already exists"
        all_ok=false
    fi

    # Binary staleness
    _check_daemon_version 2>/dev/null || true

    # Tests
    _log "Running backend tests..."
    cd "$BACKEND_DIR"
    if SWARMAI_SUITE=1 python -m pytest --timeout=120 -x -q --tb=short 2>&1 | tail -5; then
        _ok "Tests passed"
    else
        _err "Tests FAILED"
        all_ok=false
    fi

    echo ""
    if [ "$all_ok" = true ]; then
        _ok "All preflight checks passed — ready for: ./prod.sh release"
    else
        _warn "Some checks need attention (see above)"
    fi
    echo ""
}

# ── Main ────────────────────────────────────────────────────

case "${1:-help}" in
    build)      cmd_build ;;
    release)    cmd_release ;;
    deploy)     cmd_deploy ;;
    verify)     shift; cmd_verify "$@" ;;
    preflight)  cmd_preflight ;;
    status)     cmd_status ;;
    daemon)     shift; cmd_daemon "$@" ;;
    *)
        echo "SwarmAI Production Operations"
        echo ""
        echo "Usage: ./prod.sh [command]"
        echo ""
        echo "Build & Deploy:"
        echo "  build            Build backend binary + verify + deploy to daemon"
        echo "  release          Full release: preflight → build → verify → DMG → smoke test"
        echo "  deploy           Deploy existing binary to daemon + restart"
        echo "  verify           Run post-build capability verification"
        echo "  preflight        Check readiness (tests, dirty tree, version) without building"
        echo "  status           Show daemon health, binary versions, staleness"
        echo ""
        echo "Daemon:"
        echo "  daemon restart   Restart the backend daemon (launchd)"
        echo "  daemon stop      Stop the daemon"
        echo "  daemon start     Start the daemon"
        echo "  daemon status    Show daemon health (default)"
        echo "  daemon logs      Tail daemon logs"
        echo ""
        echo "Typical workflows:"
        echo "  ./prod.sh preflight          # Check if ready to release"
        echo "  ./prod.sh release            # Full pipeline: check → build → DMG → smoke test"
        echo "  ./prod.sh build              # Backend change → build + deploy + restart"
        echo "  ./prod.sh deploy             # Re-deploy existing binary to daemon"
        echo "  ./prod.sh status             # Check what's running"
        ;;
esac
