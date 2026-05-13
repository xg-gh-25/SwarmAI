#!/bin/bash
# Shared daemon management for dev.sh and prod.sh.
#
# Source this file after setting:
#   PROJECT_ROOT, DESKTOP_DIR, LOG_DIR  — path variables
#   _log, _ok, _warn, _err             — logging helpers
#
# Optional (set before sourcing):
#   _DAEMON_CMD      — script name for messages (default: "dev.sh")
#   _DAEMON_VERBOSE  — "1" for extended diagnostics on health-check failure

# ── Constants ──────────────────────────────────────────────────

DAEMON_LABEL="com.swarmai.backend"
DAEMON_PORT=18321
DAEMON_API="http://127.0.0.1:${DAEMON_PORT}"
GUI_TARGET="gui/$(id -u)/${DAEMON_LABEL}"

# Build output: onedir bundle (directory containing executable + libs)
BACKEND_BUNDLE_DIR="${DESKTOP_DIR}/src-tauri/binaries/python-backend-aarch64-apple-darwin"
BACKEND_BINARY="${BACKEND_BUNDLE_DIR}/python-backend"
DAEMON_DIR="${HOME}/.swarm-ai/daemon"
DAEMON_BINARY="${DAEMON_DIR}/python-backend"
DAEMON_VERSION_FILE="${DAEMON_DIR}/.version"

: "${_DAEMON_CMD:=dev.sh}"
: "${_DAEMON_VERBOSE:=0}"

# ── Core ───────────────────────────────────────────────────────

_daemon_is_running() {
    launchctl print "$GUI_TARGET" &>/dev/null
}

_daemon_health_status() {
    local api_url="$DAEMON_API"
    # Prefer port from backend.json (daemon writes this at startup)
    local bjson="$HOME/.swarm-ai/backend.json"
    if [ -f "$bjson" ]; then
        local bjson_port
        bjson_port=$(python3 -c "import json; print(json.load(open('$bjson')).get('port',''))" 2>/dev/null)
        [ -n "$bjson_port" ] && api_url="http://127.0.0.1:${bjson_port}"
    fi
    local resp
    resp=$(curl -sf --max-time 2 "${api_url}/health" 2>/dev/null) || { echo "unreachable"; return; }
    local status
    status=$(echo "$resp" | python3 -c "import sys,json; print(json.load(sys.stdin).get('status','unknown'))" 2>/dev/null)
    echo "${status:-unknown}"
}

_wait_port_free() {
    local port="$1" max_wait="${2:-10}"
    for i in $(seq 1 "$max_wait"); do
        if ! nc -z 127.0.0.1 "${port}" 2>/dev/null; then
            return 0
        fi
        sleep 0.5
    done
    return 1
}

# ── Version & Deploy ───────────────────────────────────────────

_check_daemon_version() {
    if [ ! -f "$DAEMON_VERSION_FILE" ]; then
        return 2
    fi
    # Version file format: "{semver} {git_hash} {timestamp}"
    # Field 1 = semver (for Tauri version sync)
    # Field 2 = git hash (for dev staleness check)
    # Fields 3+ = timestamp
    local binary_version binary_hash
    binary_version=$(awk '{print $1}' "$DAEMON_VERSION_FILE")
    binary_hash=$(awk '{print $2}' "$DAEMON_VERSION_FILE")
    local head_hash
    head_hash=$(cd "$PROJECT_ROOT" && git rev-parse --short HEAD 2>/dev/null || echo "unknown")

    # If no git hash in file (legacy format: just semver), can't compare commits
    if [ -z "$binary_hash" ] || [ "$binary_hash" = "$binary_version" ]; then
        _warn "Version file has no git hash (v${binary_version}) — can't check staleness"
        _warn "  Rebuild to update: ./${_DAEMON_CMD} build"
        return 0  # Don't fail — just warn
    fi

    if [ "$binary_hash" = "$head_hash" ]; then
        _ok "Binary matches HEAD ($head_hash, v${binary_version})"
        return 0
    fi

    local behind
    behind=$(cd "$PROJECT_ROOT" && git rev-list --count "${binary_hash}..HEAD" 2>/dev/null || echo "?")
    _warn "Daemon binary is ${behind} commits behind HEAD"
    _warn "  Binary: v${binary_version} ${binary_hash} ($(awk '{$1=$2=""; print substr($0,3)}' "$DAEMON_VERSION_FILE"))"
    _warn "  HEAD:   ${head_hash}"

    local backend_changes
    backend_changes=$(cd "$PROJECT_ROOT" && git diff --name-only "${binary_hash}..HEAD" -- backend/ 2>/dev/null | wc -l | tr -d ' ')
    if [ "$backend_changes" -gt 0 ]; then
        _err "  ${backend_changes} backend file(s) changed — rebuild: ./${_DAEMON_CMD} build"
        return 1
    else
        _log "  No backend changes — binary is functionally current"
        return 0
    fi
}

_deploy_daemon_binary() {
    if [ ! -d "$BACKEND_BUNDLE_DIR" ]; then
        _err "No backend bundle at $BACKEND_BUNDLE_DIR"
        _err "Run ./${_DAEMON_CMD} build first"
        return 1
    fi

    if [ ! -f "$BACKEND_BUNDLE_DIR/python-backend" ]; then
        _err "No executable in bundle at $BACKEND_BUNDLE_DIR/python-backend"
        return 1
    fi

    mkdir -p "$DAEMON_DIR"

    # Deploy onedir bundle via rsync (fast incremental sync)
    rsync -a --delete "$BACKEND_BUNDLE_DIR/" "$DAEMON_DIR/"
    chmod +x "$DAEMON_BINARY"
    _ok "Daemon bundle deployed: $(du -sh "$DAEMON_DIR" | cut -f1)"

    # Write version file — format: "{semver} {git_hash} {timestamp}"
    # All writers (daemon-lib, Rust auto_install, Rust sync_daemon_version) use this format.
    # Reader (_check_daemon_version) uses field 2 (git_hash) for staleness check.
    local git_hash app_version
    git_hash=$(cd "$PROJECT_ROOT" && git rev-parse --short HEAD 2>/dev/null || echo "unknown")
    app_version=$(cd "$PROJECT_ROOT" && grep -m1 '"version"' desktop/src-tauri/tauri.conf.json 2>/dev/null | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' || echo "0.0.0")
    echo "${app_version} ${git_hash} $(date '+%Y-%m-%d %H:%M:%S')" > "$DAEMON_VERSION_FILE"

    # Deploy resources
    local res_src="$DESKTOP_DIR/resources"
    local res_dst="$DAEMON_DIR/resources"
    if [ -d "$res_src" ]; then
        mkdir -p "$res_dst"
        cp -f "$res_src"/*.json "$res_dst/" 2>/dev/null || true
        cp -f "$res_src"/*.db "$res_dst/" 2>/dev/null || true
        _ok "Resources deployed"
    fi

    # Deploy wrapper script + plist (keeps daemon infrastructure in sync with source)
    local wrapper_src="$DESKTOP_DIR/resources/daemon/swarmai_backend.sh"
    local wrapper_dst="$HOME/.swarm-ai/swarmai_backend.sh"
    if [ -f "$wrapper_src" ]; then
        cp -f "$wrapper_src" "$wrapper_dst"
        chmod +x "$wrapper_dst"
        _ok "Wrapper script deployed"
    fi

    local plist_template="$DESKTOP_DIR/resources/daemon/com.swarmai.backend.plist.template"
    local plist_dst="$HOME/Library/LaunchAgents/${DAEMON_LABEL}.plist"
    if [ -f "$plist_template" ]; then
        local log_dir="$HOME/.swarm-ai/logs"
        mkdir -p "$log_dir"
        mkdir -p "$(dirname "$plist_dst")"
        sed -e "s|__WRAPPER_PATH__|${wrapper_dst}|g" \
            -e "s|__LOG_DIR__|${log_dir}|g" \
            -e "s|__HOME__|${HOME}|g" \
            "$plist_template" > "$plist_dst"
        _ok "Plist deployed"
    fi
}

# ── Kill + Deploy + Bootstrap (for file-overwriting deploys) ───

_daemon_kill_and_bootstrap() {
    # Kill-first restart: for processes that need to deploy AFTER killing daemon.
    # SIGKILL (instant death) → bootout (disable KeepAlive) → bootstrap (start new).
    #
    # Use when: you are an INDEPENDENT process that will rsync AFTER this function.
    #   Examples: /api/system/upgrade endpoint, s_swarm-release manual fallback.
    #   Why: KeepAlive would restart the OLD binary during rsync → race condition.
    #
    # Do NOT use for:
    #   - Deploy-first pattern (rsync already done) → just SIGKILL + KeepAlive.
    #     Examples: prod.sh build, dev.sh deploy (script is daemon subprocess,
    #     must deploy before kill because script dies with daemon).
    #   - Simple restart with same binary → just SIGKILL + KeepAlive.
    #     Examples: daemon-lib restart, daemon-lib force-restart.
    local health_timeout="${1:-30}"

    # 1. SIGKILL — instant process death (SSE cannot block SIGKILL)
    _log "SIGKILL daemon (instant death)..."
    launchctl kill SIGKILL "$GUI_TARGET" 2>/dev/null || true
    sleep 1

    # 2. bootout — deregister service (process already dead → instant return)
    #    This disables KeepAlive so nothing restarts during/after our deploy.
    launchctl bootout "$GUI_TARGET" 2>/dev/null || true
    sleep 1

    # 3. Confirm port is free
    _wait_port_free "$DAEMON_PORT" 10 || {
        _log "Port still held after 10s — force kill..."
        pkill -9 -f "$HOME/.swarm-ai/daemon/python-backend" 2>/dev/null || true
        sleep 2
    }

    # 4. Deploy fresh plist (ensures ExitTimeOut=15 is active for future bootouts)
    local plist_dst="$HOME/Library/LaunchAgents/${DAEMON_LABEL}.plist"
    local plist_src="$DESKTOP_DIR/resources/daemon/com.swarmai.backend.plist.template"
    # Fallback: backend/channels/ has the authoritative template (used by main.py upgrade)
    [ -f "$plist_src" ] || plist_src="$PROJECT_ROOT/backend/channels/com.swarmai.backend.plist"
    if [ -f "$plist_src" ]; then
        local log_dir="$HOME/.swarm-ai/logs"
        mkdir -p "$log_dir"
        sed -e "s|__WRAPPER_PATH__|$HOME/.swarm-ai/swarmai_backend.sh|g" \
            -e "s|__LOG_DIR__|${log_dir}|g" \
            -e "s|__HOME__|${HOME}|g" \
            "$plist_src" > "$plist_dst"
    fi

    # 5. bootstrap with retry (re-register + start new binary)
    _bootstrap_daemon || return 1

    # 6. Wait for health
    _daemon_wait_healthy "$health_timeout"
}

# ── Bootstrap & Health ─────────────────────────────────────────

_bootstrap_daemon() {
    # macOS launchctl sometimes fails with "Bootstrap failed: 5: Input/output
    # error" when bootout hasn't fully cleaned up. Retry fixes it reliably.
    local plist="$HOME/Library/LaunchAgents/${DAEMON_LABEL}.plist"
    local max_attempts=3

    for attempt in $(seq 1 "$max_attempts"); do
        if launchctl bootstrap "gui/$(id -u)" "$plist" 2>/dev/null; then
            return 0
        fi
        if [ "$attempt" -lt "$max_attempts" ]; then
            _log "Bootstrap attempt $attempt failed — retrying in 2s..."
            sleep 2
            launchctl bootout "$GUI_TARGET" 2>/dev/null || true
            sleep 1
        fi
    done

    _err "Bootstrap failed after $max_attempts attempts"
    _warn "Try manually: launchctl bootstrap gui/$(id -u) $plist"
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

    if [ "$_DAEMON_VERBOSE" = "1" ]; then
        echo ""
        _log "Diagnostics:"
        if _daemon_is_running; then
            _warn "  launchd service is running"
        else
            _err "  launchd service is NOT running — check plist"
        fi
        if nc -z 127.0.0.1 "${DAEMON_PORT}" 2>/dev/null; then
            _warn "  Port ${DAEMON_PORT} is bound — server started but not healthy"
        else
            _err "  Port ${DAEMON_PORT} is NOT bound — server failed to start"
        fi
        _check_daemon_version 2>/dev/null
        echo ""
        _log "Last 10 lines of daemon log:"
        tail -10 "$LOG_DIR/backend-daemon.log" 2>/dev/null
        _log "Last 5 lines of stderr:"
        tail -5 "$LOG_DIR/backend-stderr.log" 2>/dev/null
    else
        _log "Check logs: ./${_DAEMON_CMD} daemon logs"
    fi
    return 1
}

# ── cmd_daemon ─────────────────────────────────────────────────

cmd_daemon() {
    local sub="${1:-status}"
    case "$sub" in
        restart)
            if ! _check_daemon_version; then
                _err "Binary is stale — restart would use outdated code."
                _err "Run: ./${_DAEMON_CMD} deploy   (build backend + deploy + restart)"
                _log "Or force restart anyway: ./${_DAEMON_CMD} daemon force-restart"
                return 1
            fi

            # Use `launchctl kill SIGKILL` — keeps service registered, KeepAlive auto-restarts.
            # SIGKILL (not SIGTERM): SSE streams block graceful shutdown indefinitely.
            # Safe for restart-in-place: no rsync, same binary, no file corruption risk.
            _log "Sending SIGKILL to daemon (service stays registered, KeepAlive restarts)..."
            launchctl kill SIGKILL "$GUI_TARGET" 2>/dev/null || {
                _warn "kill failed — daemon may not be running, trying bootstrap..."
                _bootstrap_daemon
                _daemon_wait_healthy 30
                return $?
            }

            _log "Waiting for port ${DAEMON_PORT} to release..."
            _wait_port_free "$DAEMON_PORT" 10 || true

            _log "Waiting for launchd KeepAlive to restart daemon..."
            _daemon_wait_healthy 30
            ;;
        force-restart)
            _warn "Force restart — skipping version check"
            # Same approach: kill process, let KeepAlive restart.
            _log "Sending SIGKILL to daemon..."
            launchctl kill SIGKILL "$GUI_TARGET" 2>/dev/null || {
                _warn "kill failed — daemon may not be running, trying bootstrap..."
                _bootstrap_daemon
                _daemon_wait_healthy 90
                return $?
            }

            _log "Waiting for port ${DAEMON_PORT} to release..."
            _wait_port_free "$DAEMON_PORT" 10 || true

            _log "Waiting for launchd KeepAlive to restart daemon..."
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
            tail -f "$LOG_DIR/backend-daemon.log" "$LOG_DIR/backend-stderr.log"
            ;;
        *)
            echo "Usage: ./${_DAEMON_CMD} daemon [restart|stop|start|status|logs]"
            ;;
    esac
}
