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
# The versioned runtime the launchd wrapper runs (design 2026-10-24-swarmai-usable-by-anyone
# §3.1): the same place the app's own deploy writes, so dev and release share one path.
RUNTIME_ROOT="${HOME}/.swarm-ai/runtime"
_APP_VERSION="$(grep -m1 '"version"' "${PROJECT_ROOT:-.}/desktop/src-tauri/tauri.conf.json" 2>/dev/null | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' || echo 0.0.0)"
DAEMON_DIR="${RUNTIME_ROOT}/${_APP_VERSION}"
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

    # Deploy onedir bundle via rsync (fast incremental sync).
    # Exclude resources/ and .version from --delete: they live in $DAEMON_DIR
    # but NOT in the binary bundle, so a bare --delete would wipe them ~50s
    # before they are re-copied below (L139)/rewritten (L132). A daemon that
    # KeepAlive restarts inside that window can't find default-agent.json and
    # silently falls back to DB defaults. Excluding them from --delete means
    # they are never momentarily absent. --delete still prunes stale _internal
    # files (the exclude is surgical, not a blanket --delete removal).
    # Patterns are ANCHORED with a leading '/' so they match ONLY the top-level
    # $DAEMON_DIR/resources and $DAEMON_DIR/.version — NOT a nested 'resources'
    # dir that legitimately ships inside the bundle (e.g. _internal/limits/resources).
    # Explicit failure checks throughout: callers may run us with errexit
    # suspended (prod.sh `if ! cmd_build`), where a failed step would otherwise
    # be ignored and the daemon restarted anyway.
    #
    # STAGE, THEN SWAP — never sync into the live dir in place: a sync that
    # fails halfway (disk full, I/O error) would leave the running daemon — and
    # its next KeepAlive respawn — on a mix of old and new bundle files.
    #   1. $stage = hard-link clone of the live dir (cheap; keeps resources/ and
    #      .version). rsync replaces changed files via temp+rename, so writing
    #      into the clone never touches a live inode.
    #   2. the exact deploy sync below, into $stage.
    #   3. two renames: live → .prev, stage → live. .prev is kept as a rollback
    #      copy (and so a process whose cwd/open files are in it keeps working)
    #      and removed at the start of the next deploy.
    [ -n "${DAEMON_DIR:-}" ] || { _err "DAEMON_DIR is not set"; return 1; }
    local _stage="${DAEMON_DIR}.next" _prev="${DAEMON_DIR}.prev"
    rm -rf "$_stage" "$_prev"
    if [ -d "$DAEMON_DIR" ]; then
        rsync -a --link-dest="$DAEMON_DIR/" "$DAEMON_DIR/" "$_stage/" || {
            rm -rf "$_stage"; _err "Staging a copy of $DAEMON_DIR failed — live daemon untouched"; return 1
        }
    else
        mkdir -p "$_stage" || { _err "Creating $_stage failed"; return 1; }
    fi
    local _rsync_rc=0
    rsync -a --delete --exclude='/resources' --exclude='/.version' \
        "$BACKEND_BUNDLE_DIR/" "$_stage/" || _rsync_rc=$?
    if [ "$_rsync_rc" -ne 0 ]; then
        rm -rf "$_stage"
        _err "rsync of the backend bundle FAILED (rc=$_rsync_rc) — staged copy discarded, live daemon bundle untouched"
        return 1
    fi
    chmod +x "$_stage/python-backend" || { rm -rf "$_stage"; _err "chmod +x on the staged binary failed"; return 1; }
    if [ -d "$DAEMON_DIR" ]; then
        mv "$DAEMON_DIR" "$_prev" || { rm -rf "$_stage"; _err "Swapping out $DAEMON_DIR failed — live daemon untouched"; return 1; }
    fi
    if ! mv "$_stage" "$DAEMON_DIR"; then
        [ -d "$_prev" ] && mv "$_prev" "$DAEMON_DIR"
        _err "Swapping in the staged bundle failed — previous bundle restored"
        return 1
    fi
    _ok "Daemon bundle deployed: $(du -sh "$DAEMON_DIR" | cut -f1)"
    # point the runtime's `current` at this version (tmp + rename), keeping the old one
    # as `previous` — the same pointer files the app's deploy writes
    local _old_current=""
    [ -f "$RUNTIME_ROOT/current" ] && _old_current="$(head -n 1 "$RUNTIME_ROOT/current" | tr -d '[:space:]')"
    if [ -n "$_old_current" ] && [ "$_old_current" != "$_APP_VERSION" ]; then
        printf '%s' "$_old_current" > "$RUNTIME_ROOT/previous.tmp" && mv "$RUNTIME_ROOT/previous.tmp" "$RUNTIME_ROOT/previous"
    fi
    printf '%s' "$_APP_VERSION" > "$RUNTIME_ROOT/current.tmp" && mv "$RUNTIME_ROOT/current.tmp" "$RUNTIME_ROOT/current" || \
        { _err "Writing $RUNTIME_ROOT/current failed"; return 1; }

    # Write version file — format: "{semver} {git_hash} {timestamp}"
    # All writers (daemon-lib, Rust auto_install, Rust sync_daemon_version) use this format.
    # Reader (_check_daemon_version) uses field 2 (git_hash) for staleness check.
    local git_hash app_version
    git_hash=$(cd "$PROJECT_ROOT" && git rev-parse --short HEAD 2>/dev/null || echo "unknown")
    app_version=$(cd "$PROJECT_ROOT" && grep -m1 '"version"' desktop/src-tauri/tauri.conf.json 2>/dev/null | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' || echo "0.0.0")
    echo "${app_version} ${git_hash} $(date '+%Y-%m-%d %H:%M:%S')" > "$DAEMON_VERSION_FILE" || \
        { _err "Writing $DAEMON_VERSION_FILE failed"; return 1; }

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
        cp -f "$wrapper_src" "$wrapper_dst" && chmod +x "$wrapper_dst" || \
            { _err "Deploying the wrapper to $wrapper_dst failed"; return 1; }
        _ok "Wrapper script deployed"
    fi

    local plist_template="$DESKTOP_DIR/resources/daemon/com.swarmai.backend.plist.template"
    local plist_dst="$HOME/Library/LaunchAgents/${DAEMON_LABEL}.plist"
    _DAEMON_PLIST_CHANGED=0
    if [ -f "$plist_template" ]; then
        local log_dir="$HOME/.swarm-ai/logs"
        mkdir -p "$log_dir"
        mkdir -p "$(dirname "$plist_dst")"
        # Render to a temp file first so we can tell whether the job definition
        # changed: launchd only re-reads a plist on bootstrap, so a changed plist
        # needs `daemon reload`, not SIGKILL+KeepAlive (see _daemon_restart_after_deploy).
        local rendered
        rendered="$(mktemp "${TMPDIR:-/tmp}/swarmai-plist.XXXXXX")"
        if ! sed -e "s|__WRAPPER_PATH__|${wrapper_dst}|g" \
            -e "s|__LOG_DIR__|${log_dir}|g" \
            -e "s|__HOME__|${HOME}|g" \
            "$plist_template" > "$rendered"; then
            rm -f "$rendered"; _err "Rendering the launchd plist failed"; return 1
        fi
        # Never put an invalid plist in place: a reload would bootout the job
        # and then fail to bootstrap it, leaving the daemon down.
        if command -v plutil >/dev/null 2>&1 && ! plutil -lint -s "$rendered"; then
            rm -f "$rendered"; _err "Rendered launchd plist is invalid — keeping the current one"; return 1
        fi
        if [ -f "$plist_dst" ] && cmp -s "$rendered" "$plist_dst"; then
            _DAEMON_PLIST_CHANGED=0
        else
            _DAEMON_PLIST_CHANGED=1
        fi
        chmod 644 "$rendered"   # mktemp creates 0600; keep the plist's usual mode
        mv -f "$rendered" "$plist_dst" || { _err "Writing $plist_dst failed"; return 1; }
        _ok "Plist deployed$([ "$_DAEMON_PLIST_CHANGED" = 1 ] && echo ' (changed — job will be reloaded)')"
    fi
}

# True (exit 0) when the LOADED launchd job no longer matches the plist on disk
# in a way a SIGKILL+KeepAlive respawn would not fix. Today that is ProcessType:
# a job bootstrapped while the plist said Background keeps running at Darwin
# background priority after the file says Standard, until bootout+bootstrap.
_daemon_loaded_job_stale() {
    local plist_dst="$HOME/Library/LaunchAgents/${DAEMON_LABEL}.plist"
    [ -f "$plist_dst" ] || return 1
    local want
    want="$(plutil -extract ProcessType raw -o - "$plist_dst" 2>/dev/null)" || return 1
    local loaded_bg=false
    if launchctl print "$GUI_TARGET" 2>/dev/null | grep -q "spawn type = background"; then
        loaded_bg=true
    fi
    if [ "$want" = "Background" ]; then
        [ "$loaded_bg" = false ]
    else
        [ "$loaded_bg" = true ]
    fi
}

# Restart the daemon after _deploy_daemon_binary so it runs the new binary.
# Normal case: SIGKILL → KeepAlive respawns the new binary (deploy-first pattern).
# If the job definition itself is stale (plist content changed, or the loaded
# job still has an old ProcessType), SIGKILL+KeepAlive would respawn from the
# OLD in-memory definition, so go through `daemon reload` (bootout+bootstrap),
# which also handles being called from inside the daemon's own process tree.
_daemon_restart_after_deploy() {
    local timeout="${1:-30}"
    if [ "${_DAEMON_PLIST_CHANGED:-0}" = "1" ] || _daemon_loaded_job_stale; then
        _log "launchd job definition changed — reloading (bootout + bootstrap)..."
        local old_boot
        old_boot="$(_daemon_health_line | awk '{print $2}')"
        _DAEMON_OP_DETACHED=0
        cmd_daemon reload || return $?
        if [ "${_DAEMON_OP_DETACHED:-0}" = "1" ]; then
            # We are inside the daemon's process tree: the reload runs in a
            # detached copy and cmd_daemon returned at once. If we survive the
            # daemon's death, wait for the NEW process — otherwise every caller
            # check after this (health, smoke) would run against the OLD daemon.
            _log "Reload delegated to a detached process — waiting for the new daemon..."
            _daemon_wait_restarted "$old_boot" 150
            return $?
        fi
        return 0
    fi
    _log "Restarting daemon (SIGKILL → KeepAlive restarts new binary)..."
    launchctl kill SIGKILL "$GUI_TARGET" 2>/dev/null || true
    _daemon_wait_healthy "$timeout"
}

# ── Deploy drain ───────────────────────────────────────────────
#
# Max seconds a deploy waits for OTHER sessions' in-flight turns before it kills
# the daemon anyway (0 disables). Bounded: a deploy must not hang on a 30-minute
# pipeline turn in another tab; that tab then resumes after the restart.
#
# Callers run the drain BEFORE _deploy_daemon_binary, never between the rsync
# and the kill: the running PyInstaller binary re-opens its own executable by
# path for every lazy import, so an old process serving turns on a swapped
# bundle reads new bytes at old offsets (zlib/ImportError). rsync→kill must stay
# back-to-back; a drain interrupted (e.g. the caller's tool timeout) then leaves
# nothing half-deployed.
: "${SWARMAI_DEPLOY_DRAIN_SECS:=180}"
: "${_DRAIN_POLL_SECS:=3}"

# Print (one per line) the sessions that are mid-turn — streaming, or finishing
# a turn after an SSE disconnect — EXCLUDING the caller's own session: a deploy
# often runs from a chat tab's CLI (a pipeline's DELIVER step;
# $SWARM_APP_SESSION_ID is set for every CLI the daemon spawns), and waiting on
# our own turn would deadlock.
_daemon_other_busy_sessions() {
    local body
    if ! body="$(curl -sf --max-time 5 "${DAEMON_API}/api/chat/sessions/streaming-state" 2>/dev/null)"; then
        if nc -z 127.0.0.1 "${DAEMON_PORT}" 2>/dev/null; then
            _warn "streaming-state unreadable while the daemon is up — cannot see other turns" >&2
        fi
        return 0
    fi
    printf '%s' "$body" | _DRAIN_SELF="${SWARM_APP_SESSION_ID:-}" python3 -c '
import json, os, sys
me = os.environ.get("_DRAIN_SELF", "")
for sid, s in (json.load(sys.stdin).get("sessions") or {}).items():
    if sid != me and s.get("streaming"):
        print(sid)
' 2>/dev/null || true
}

# Wait (bounded by wall clock) until no other session is mid-turn. If any was,
# also wait for the lifecycle loop's next session_state.json write (~60s cadence)
# so tabs that just went IDLE keep their --resume identity across the SIGKILL.
# (That file holds IDLE units only — the caller's own streaming tab and tabs
# waiting on a question still cold-resume from DB history.)
_daemon_drain_before_restart() {
    local max="$SWARMAI_DEPLOY_DRAIN_SECS" start=$SECONDS waited=0 busy saw_busy=false
    [ "$max" -gt 0 ] 2>/dev/null || return 0
    while :; do
        busy="$(_daemon_other_busy_sessions)"
        [ -z "$busy" ] && break
        saw_busy=true
        waited=$(( SECONDS - start ))
        if [ "$waited" -ge "$max" ]; then
            _warn "Drain timeout (${max}s) — still mid-turn: $(echo $busy) — restarting anyway (they resume after restart)"
            return 0
        fi
        if [ $(( waited % 15 )) -lt "$_DRAIN_POLL_SECS" ]; then
            _log "Waiting for $(echo "$busy" | wc -l | tr -d ' ') other session(s) to finish their turn (${waited}s/${max}s; SWARMAI_DEPLOY_DRAIN_SECS=0 skips)..."
        fi
        sleep "$_DRAIN_POLL_SECS"
    done
    if [ "$saw_busy" = true ]; then
        _ok "Other sessions idle after ${waited}s"
        _daemon_wait_state_persisted_after "$(date +%s)" 70
    fi
    return 0
}

# Wait until ~/.swarm-ai/session_state.json was rewritten after epoch $1.
_daemon_wait_state_persisted_after() {
    local since="$1" max="${2:-70}" i f="$HOME/.swarm-ai/session_state.json"
    for i in $(seq 1 "$max"); do
        if python3 -c 'import json,sys; sys.exit(0 if float(json.load(open(sys.argv[1])).get("_persisted_at", 0)) > float(sys.argv[2]) else 1)' \
            "$f" "$since" 2>/dev/null; then
            _ok "Session identities persisted (${i}s)"
            return 0
        fi
        sleep 1
    done
    _warn "session_state.json not rewritten within ${max}s — those tabs will cold-resume"
    return 0
}

# "<status> <boot_id>" from the fixed daemon port (empty fields when unreachable).
_daemon_health_line() {
    curl -sf --max-time 2 "${DAEMON_API}/health" 2>/dev/null | python3 -c \
        "import sys,json; d=json.load(sys.stdin); print(d.get('status',''), d.get('boot_id',''))" \
        2>/dev/null || true
}

# Wait until the daemon on the fixed port is healthy with a boot_id different
# from $1 (a NEW process). $2 = timeout seconds.
_daemon_wait_restarted() {
    local old="$1" timeout="${2:-120}" i line
    for i in $(seq 1 "$timeout"); do
        line="$(_daemon_health_line)"
        if [ "${line%% *}" = "healthy" ] && [ -n "${line#* }" ] && [ "${line#* }" != "$old" ]; then
            _ok "Daemon restarted (boot_id ${old:-?} → ${line#* }, ${i}s)"
            return 0
        fi
        sleep 1
    done
    _err "Daemon did not come back as a new process within ${timeout}s"
    return 1
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

# ── Daemon-descendant guard (C034 prevention) ──────────────────
#
# If a lifecycle op is invoked from inside the daemon's own process tree
# (e.g. an agent CLI subprocess), killing/booting-out the daemon kills our
# own host — leaving it in HTTP-dead-but-not-exited limbo (C034). Detect this
# via core.daemon_guard ancestry walk and re-exec the command detached in a
# new session group so it survives the daemon's death.
#
# Resolves a Python that can import core.daemon_guard (dev venv or system).
_guard_python() {
    if [ -n "${_GUARD_PYTHON:-}" ]; then echo "${_GUARD_PYTHON}"; return 0; fi
    local backend_dir="${PROJECT_ROOT}/backend"
    for cand in "${backend_dir}/.venv/bin/python" "$(command -v python3)" /opt/homebrew/bin/python3 /usr/bin/python3; do
        [ -x "$cand" ] && { echo "$cand"; return 0; }
    done
    return 1
}

# Re-exec this lifecycle command detached if we're a daemon descendant; returns
# 0 when it re-execed (caller MUST stop — the detached copy owns the op), 1 when
# the caller should proceed inline.
#
# Delegates BOTH the ancestry decision AND the detach to the unit-tested
# core.daemon_guard reexec-if-descendant CLI, so the production prevention path
# is exactly the code path covered by tests. The guard uses
# start_new_session=True (NOT setsid — absent on macOS — and NOT `nohup &` which
# keeps the child in the daemon's process group where a daemon SIGKILL would
# still reap it). The detached child is invoked with the detach env flag set, so
# its own guard short-circuits (no recursion).
_reexec_detached_if_descendant() {
    [ -n "${_SWARM_LIFECYCLE_DETACHED:-}" ] && return 1  # already detached → inline
    local py; py="$(_guard_python)" || return 1          # no python → can't guard → inline
    # Resolve $0 to an ABSOLUTE path. The CLI Popens the re-exec, and a relative
    # $0 like "./prod.sh" would be unfindable in the child's cwd. Use /bin/bash
    # NOT $SHELL: $0 has a #!/bin/bash shebang + bashisms; $SHELL is often zsh
    # and would mis-execute. PYTHONPATH lets the CLI import core.* without cwd.
    local abs0
    abs0="$(cd "$(dirname "$0")" 2>/dev/null && pwd)/$(basename "$0")"
    [ -f "$abs0" ] || abs0="$0"
    if PYTHONPATH="${PROJECT_ROOT}/backend${PYTHONPATH:+:$PYTHONPATH}" \
        "$py" -m core.daemon_guard reexec-if-descendant /bin/bash "$abs0" "$@" >/dev/null 2>&1; then
        _DAEMON_OP_DETACHED=1  # read by _daemon_restart_after_deploy
        _warn "Lifecycle op from a daemon-descendant (C034 guard) — re-executed detached."
        _ok "This session may end if it was the daemon's child; the detached copy owns the op."
        return 0
    fi
    return 1  # not a descendant (or guard unavailable) → proceed inline
}

# ── cmd_daemon ─────────────────────────────────────────────────

cmd_daemon() {
    local sub="${1:-status}"

    # C034 prevention: ops that kill/bootout the daemon must not run from inside
    # the daemon's process tree. Re-exec detached if we're a descendant.
    case "$sub" in
        restart|force-restart|stop|reload)
            if _reexec_detached_if_descendant daemon "$@"; then
                return 0
            fi
            if [ -n "${_SWARM_LIFECYCLE_DETACHED:-}" ]; then
                # We ARE the detached copy (C034): reexec_detached sends our
                # stdout/stderr to /dev/null, so a failed op left no trace.
                # Log to a file instead, with the final exit code.
                local _lc_log="${LOG_DIR:-$HOME/.swarm-ai/logs}/daemon-lifecycle.log"
                mkdir -p "$(dirname "$_lc_log")"
                exec >>"$_lc_log" 2>&1
                echo "== $(date '+%Y-%m-%d %H:%M:%S') daemon $sub (detached, pid $$)"
                # capture $? FIRST — the $(date) substitution would reset it to 0
                trap '_lc_rc=$?; echo "== $(date "+%Y-%m-%d %H:%M:%S") daemon '"$sub"' (detached) exit rc=$_lc_rc"' EXIT
            fi
            ;;
    esac

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
        reload)
            # Re-register the job so launchd re-reads the plist on disk
            # (ProcessType, env, ExitTimeOut). restart/SIGKILL cannot do this:
            # KeepAlive respawns from the loaded definition. SIGKILL first so the
            # bootout is instant (an open SSE stream holds SIGTERM for ExitTimeOut).
            # Non-permanent sentinel: the guardian must not race our bootstrap,
            # and its stale-guard still recovers the daemon if this op dies midway.
            local _sentinel="$HOME/.swarm-ai/.daemon-intentional-down"
            if [ -f "$_sentinel" ] && \
                [ $(( $(date +%s) - $(stat -f %m "$_sentinel" 2>/dev/null || echo 0) )) -lt 300 ]; then
                # A fresh sentinel = another lifecycle op (stop / upgrade) owns
                # the down-window. Older than the guardian's 5-min stale window
                # = leftover from a crashed op → safe to take over.
                _err "Intent sentinel present ($_sentinel) — another daemon op is in flight; not reloading"
                return 1
            fi
            local _gpy; _gpy="$(_guard_python)" && \
                (cd "${PROJECT_ROOT}/backend" && "$_gpy" -m core.daemon_guard write-sentinel "plist reload" "reload" 2>/dev/null) || \
                _warn "Could not write intent sentinel — guardian may race the reload"
            _log "Reloading launchd job (SIGKILL → bootout → bootstrap)..."
            launchctl kill SIGKILL "$GUI_TARGET" 2>/dev/null || true
            sleep 1
            launchctl bootout "$GUI_TARGET" 2>/dev/null || true
            _wait_port_free "$DAEMON_PORT" 10 || {
                _log "Port still held — force kill..."
                pkill -9 -f "$HOME/.swarm-ai/daemon/python-backend" 2>/dev/null || true
                sleep 2
            }
            local _reload_rc=0
            _bootstrap_daemon || _reload_rc=$?
            if [ "$_reload_rc" -eq 0 ]; then
                _daemon_wait_healthy 90 || _reload_rc=$?
            fi
            # Clear OUR sentinel on both outcomes (on failure the guardian should
            # recover now, not after the 5-minute stale window). Never clear one
            # another op wrote meanwhile.
            if grep -q '"written_by": "reload"' "$_sentinel" 2>/dev/null; then
                rm -f "$_sentinel"
            fi
            echo "reload finished rc=$_reload_rc"
            return "$_reload_rc"
            ;;
        stop)
            _log "Stopping daemon..."
            # Write the intent sentinel BEFORE bootout so the guardian knows
            # this down-state is intentional and does NOT resurrect the daemon.
            local _gpy; _gpy="$(_guard_python)" && \
                (cd "${PROJECT_ROOT}/backend" && "$_gpy" -m core.daemon_guard write-sentinel "manual stop" "stop" "permanent" 2>/dev/null) || \
                _warn "Could not write intent sentinel — guardian may restart the daemon"
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
            # Clear the intent sentinel — an explicit start means the operator
            # wants the daemon up; the guardian should resume protecting it.
            local _gpy; _gpy="$(_guard_python)" && \
                (cd "${PROJECT_ROOT}/backend" && "$_gpy" -m core.daemon_guard clear-sentinel 2>/dev/null) || true
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
            echo "Usage: ./${_DAEMON_CMD} daemon [restart|reload|stop|start|status|logs]"
            ;;
    esac
}
