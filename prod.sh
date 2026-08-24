#!/bin/bash
# SwarmAI Production Operations
# Usage:
#   ./prod.sh build          — Build backend binary + deploy to daemon + restart
#   ./prod.sh release        — Full release (backend + DMG + tag + publish)
#   ./prod.sh verify         — Verify existing binary capabilities
#   ./prod.sh status         — Show daemon health, binary versions, staleness
#   ./prod.sh deploy         — Auto-scope deploy (detects changes + builds + E2E smoke)
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

# Eval gate (git-bound) — RELEASE-only. Enforces "本地必须跑完 Eval 出 report
# 才放行" at every ship boundary (NOT on `build`, which is high-frequency). Shared
# by cmd_release + cmd_release_hive so no release path ships ungated.
#   gate rc 0 = fresh + green  → return 0 (proceed)
#   gate rc 1 = stale OR red   → return 1 (caller BLOCKS the release)
#   gate rc 2 = no report      → interactive: ask (Y proceeds); non-TTY: return 1
# Escape hatch: SWARMAI_SKIP_EVAL_GATE=1 (CI / emergency).
_eval_gate() {
    # Once-per-process: release-all calls cmd_release THEN cmd_release_hive — both
    # gate. Skip the redundant 2nd run (same git tree → same verdict; avoids a
    # double rc=2 prompt). Standalone release-hive still gates (flag unset).
    if [ "${_EVAL_GATE_PASSED:-}" = "1" ]; then
        return 0
    fi
    if [ "${SWARMAI_SKIP_EVAL_GATE:-}" = "1" ]; then
        _warn "Eval gate SKIPPED (SWARMAI_SKIP_EVAL_GATE=1)"
        return 0
    fi
    _log "Eval gate (git-bound freshness + BVT)..."
    # `set -e` is active (top of file): a non-zero command substitution aborts the
    # script AT the assignment before $? is read — wrap in set +e/set -e.
    local _gate_out _gate_rc answer
    set +e
    _gate_out=$(cd "$BACKEND_DIR" && python scripts/ci_eval_gate.py 2>&1)
    _gate_rc=$?
    set -e
    case "$_gate_rc" in
        0) _ok "$_gate_out"; _EVAL_GATE_PASSED=1; return 0 ;;
        2) _warn "$_gate_out"
           # No TTY (CI / piped stdin): can't ask — fail closed with a clear message
           # instead of dying opaquely at `read` under set -e.
           if [ ! -t 0 ]; then
               _err "No eval report and no TTY — run eval first, or set SWARMAI_SKIP_EVAL_GATE=1 to bypass (CI)."
               return 1
           fi
           echo -n "  No gate-readable eval report (run 'python backend/scripts/eval_runner.py run' first). Release anyway? [y/N] "
           read -r answer
           if [[ ! "$answer" =~ ^[Yy] ]]; then
               _err "Aborted — run eval to produce a gate-readable report"
               return 1
           fi
           _EVAL_GATE_PASSED=1
           return 2 ;;  # proceed-with-warning (caller marks preflight degraded)
        *) _err "$_gate_out"
           _err "Eval gate BLOCKED the release. Re-run eval, or set SWARMAI_SKIP_EVAL_GATE=1 to override."
           return 1 ;;
    esac
}

# ── Daemon (shared library) ────────────────────────────────
_DAEMON_CMD="prod.sh"
source "$PROJECT_ROOT/scripts/daemon-lib.sh"

# ── Commands ────────────────────────────────────────────────

cmd_build() {
    local start=$(date +%s)
    echo ""
    echo -e "${BOLD}SwarmAI Production Build${NC}"
    echo "════════════════════════"
    echo ""

    # NOTE: the eval gate lives in `cmd_release` (Phase 1), NOT here. `build` is a
    # high-frequency dev action (run many times a day) — gating it stalls iteration.
    # The "本地必须跑完 Eval 才放行" enforcement belongs at the release boundary
    # (shipping to others), which is where ci_eval_gate.py now runs.

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
    if python scripts/verify_build.py "$BACKEND_BINARY"; then
        _ok "Verification passed — all capabilities present"
    else
        _err "Verification FAILED — do NOT release"
        echo ""
        _warn "Fix issues above, then re-run: ./prod.sh build"
        return 1
    fi

    # Step 3: Deploy then restart
    # Order: deploy FIRST, then kill. This script may run from inside the daemon
    # (Claude CLI subprocess). Deploy must complete before kill signal — after kill,
    # daemon dies → CLI dies → script dies. KeepAlive restarts with new binary.
    _log "Step 3/3: Deploy to daemon..."
    local daemon_was_running=false
    if _daemon_is_running; then
        daemon_was_running=true
    fi

    _deploy_daemon_binary

    echo ""
    _ok "Build complete in $(_build_time $start)"
    _ok "Binary: $BACKEND_BINARY ($(du -h "$BACKEND_BINARY" | cut -f1))"

    # Restart daemon to pick up new binary.
    # Deploy-first pattern: rsync already completed above, so KeepAlive is SAFE
    # (restarts the NEW binary). No need for bootout — KeepAlive IS the restart.
    # SIGKILL (not SIGTERM): SSE streams block graceful shutdown indefinitely.
    # NOTE: If this runs from agent subprocess, script dies after SIGKILL — that's
    # fine because deploy is already done and KeepAlive handles restart.
    if [ "$daemon_was_running" = true ]; then
        echo ""
        _log "Restarting daemon (SIGKILL → KeepAlive restarts new binary)..."
        launchctl kill SIGKILL "$GUI_TARGET" 2>/dev/null || true
        _daemon_wait_healthy 30 || {
            _warn "Daemon didn't come up — try: ./prod.sh daemon start"
        }
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

    # 1e. Eval gate (git-bound) — RELEASE-only (see _eval_gate). Capture rc under
    # set -e: 0=proceed, 1=BLOCK (return), 2=proceed-with-warning (mark degraded).
    local _eg_rc
    set +e; _eval_gate; _eg_rc=$?; set -e
    case "$_eg_rc" in
        0) ;;
        2) preflight_ok=false ;;
        *) return 1 ;;
    esac

    echo ""

    # ── Phase 2: Build ────────────────────────────────────────
    echo -e "${BOLD}Phase 2/4: Build${NC}"
    echo "────────────────"

    # 2a. PyInstaller
    _log "Step 1/4: PyInstaller backend build..."
    cd "$DESKTOP_DIR"
    npm run build:backend

    # 2b. Verify binary capabilities
    _log "Step 2/4: Post-build verification..."
    cd "$BACKEND_DIR"
    if python scripts/verify_build.py "$BACKEND_BINARY"; then
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

    # Pre-flight: detach stale DMG mounts from previous failed builds.
    # Tauri's bundle_dmg.sh lacks a cleanup trap — failed builds leave RW DMGs mounted,
    # blocking future hdiutil convert operations.
    _log "  Checking for stale DMG mounts..."
    local stale_devs
    stale_devs=$(hdiutil info 2>/dev/null | awk -v pat="rw\\..*SwarmAI" '
        /^===/ { in_section=0 }
        $0 ~ pat { in_section=1 }
        in_section && /^\/dev\/disk/ { print $1 }
    ' | sed 's/s[0-9]*$//' | sort -u || true)
    if [[ -n "$stale_devs" ]]; then
        _warn "Found stale DMG mount(s), force-detaching..."
        while IFS= read -r dev; do
            hdiutil detach "$dev" -force 2>/dev/null || true
        done <<< "$stale_devs"
        sleep 1
    fi
    # Clean orphaned temp RW DMG files
    find "$DESKTOP_DIR/src-tauri/target/release/bundle" -name "rw.*.dmg" -delete 2>/dev/null || true

    # Signing-key-aware wrapper: signs when TAURI_SIGNING_PRIVATE_KEY is set,
    # skips updater-artifact signing when absent (local dev). See tauri-build.sh.
    bash "$DESKTOP_DIR/scripts/tauri-build.sh"

    # 2e. Inject backend bundle into .app (onedir — no PyInstaller extraction at runtime)
    local app_bundle="$DESKTOP_DIR/src-tauri/target/release/bundle/macos/SwarmAI.app"
    local backend_src="$DESKTOP_DIR/src-tauri/binaries/python-backend-aarch64-apple-darwin"
    local backend_dst="${app_bundle}/Contents/Resources/python-backend"
    if [ -d "$app_bundle" ] && [ -d "$backend_src" ]; then
        _log "Injecting backend bundle into .app..."
        mkdir -p "$backend_dst"
        rsync -a "$backend_src/" "$backend_dst/"
        chmod +x "$backend_dst/python-backend"
        _ok "Backend bundle injected ($(du -sh "$backend_dst" | cut -f1))"
    else
        _warn "Skipping .app backend injection (app_bundle or backend_src missing)"
    fi

    echo ""

    # ── Phase 3: Deploy + Verify ──────────────────────────────
    echo -e "${BOLD}Phase 3/4: Deploy & Verify${NC}"
    echo "──────────────────────────"

    # 3a. Deploy binary (BEFORE kill — script may die when daemon is killed)
    local daemon_was_running=false
    if _daemon_is_running; then
        daemon_was_running=true
    fi

    _deploy_daemon_binary

    # 3b. Restart daemon — deploy-first pattern (SIGKILL + KeepAlive restarts new binary)
    if [ "$daemon_was_running" = true ]; then
        _log "Restarting daemon (SIGKILL → KeepAlive restarts new binary)..."
        launchctl kill SIGKILL "$GUI_TARGET" 2>/dev/null || true
        _daemon_wait_healthy 30 || _warn "Daemon restart slow — try: ./prod.sh daemon restart"
    elif launchctl print "$GUI_TARGET" &>/dev/null; then
        _log "Service registered but not running — starting..."
        cmd_daemon start || _warn "Daemon start failed — try: ./prod.sh daemon start"
    else
        _log "Starting daemon..."
        cmd_daemon start || _warn "Daemon start failed — try: ./prod.sh daemon start"
    fi

    # 3d. Verify daemon health after restart
    local health=$(_daemon_health_status)
    if [ "$health" = "healthy" ]; then
        _ok "Daemon healthy with new binary"
    else
        _warn "Daemon status: $health — build succeeded, daemon may need manual restart"
    fi

    echo ""

    # ── Phase 4: Automated + Manual Smoke Tests ────────────────
    local dmg=$(ls "$DESKTOP_DIR/src-tauri/target/release/bundle/dmg/"*.dmg 2>/dev/null | head -1)

    echo -e "${BOLD}Phase 4/4: Smoke Tests${NC}"
    echo "──────────────────────"

    # 4a. Automated: verify daemon serves JSON (not HTML) on health endpoint
    #     This catches the v1.9.0 class of bug: isDesktop()=false → API hits asset
    #     protocol → HTML instead of JSON → 60s timeout.
    echo ""
    _log "Automated smoke: daemon health returns JSON..."
    local smoke_ok=true
    if _daemon_is_running; then
        local health_body
        health_body=$(curl -sf --max-time 5 "${DAEMON_API}/health" 2>/dev/null || true)
        if [ -z "$health_body" ]; then
            _err "Smoke FAIL: daemon /health returned empty response"
            smoke_ok=false
        elif echo "$health_body" | python3 -c "import json,sys; d=json.load(sys.stdin); assert d.get('status')=='healthy'" 2>/dev/null; then
            _ok "Smoke: /health returns valid JSON with status=healthy"
        else
            _err "Smoke FAIL: /health response is not valid JSON or status!=healthy"
            _err "Response: $(echo "$health_body" | head -c 200)"
            smoke_ok=false
        fi

        # Check a frontend-equivalent API call returns JSON (not HTML from SPA fallback)
        local api_body
        api_body=$(curl -sf --max-time 5 "${DAEMON_API}/api/system/tokens/usage" 2>/dev/null || true)
        if [ -n "$api_body" ] && echo "$api_body" | python3 -c "import json,sys; json.load(sys.stdin)" 2>/dev/null; then
            _ok "Smoke: /api/system/tokens/usage returns valid JSON"
        elif [ -n "$api_body" ] && echo "$api_body" | head -c 20 | grep -qi "doctype\|<html"; then
            _err "Smoke FAIL: API returned HTML instead of JSON (isDesktop() bug class)"
            smoke_ok=false
        else
            _warn "Smoke: /api/system/tokens/usage not reachable (may need auth — non-blocking)"
        fi

        # Check streaming-state endpoint (catches NameError/import bugs that silently 500)
        local stream_body
        stream_body=$(curl -sf --max-time 5 "${DAEMON_API}/api/chat/sessions/streaming-state" 2>/dev/null || true)
        if [ -n "$stream_body" ] && echo "$stream_body" | python3 -c "import json,sys; d=json.load(sys.stdin); assert 'sessions' in d" 2>/dev/null; then
            _ok "Smoke: /api/chat/sessions/streaming-state returns valid JSON"
        else
            _err "Smoke FAIL: /api/chat/sessions/streaming-state not responding (reconciliation safety net broken)"
            smoke_ok=false
        fi
    else
        _warn "Smoke: daemon not running — skipping automated checks"
        smoke_ok=false
    fi

    # 4b. Manual checklist (unchanged)
    echo ""
    echo -e "  ${CYAN}Manual verification (install DMG and check):${NC}"
    echo ""
    echo "  ┌─────────────────────────────────────────────────────┐"
    echo "  │  □  1. Install DMG → open app → no crash            │"
    echo "  │  □  2. Send a message → streaming works             │"
    echo "  │  □  3. Multi-turn → context preserved               │"
    echo "  │  □  4. Close app → reopen → chat history intact     │"
    echo "  │  □  5. Slack: send a DM → reply arrives             │"
    echo "  │  □  6. SwarmWS explorer → files load                │"
    echo "  │  □  7. Settings page → no errors                    │"
    echo "  │  □  8. DevTools (⌘⌥I) → Console: no red errors,    │"
    echo "  │        [Health Check] shows JSON (not HTML),        │"
    echo "  │        [Startup] shows port 18321                   │"
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
    echo "  Binary:     $(du -h "$BACKEND_BINARY" | cut -f1)"
    if [ -n "$dmg" ]; then
        echo "  DMG:        $(du -h "$dmg" | cut -f1)"
    fi
    echo "  Daemon:     $(_daemon_health_status)"
    if [ "$preflight_ok" = true ]; then
        echo -e "  Tests:      ${GREEN}passed${NC}"
    else
        echo -e "  Tests:      ${YELLOW}passed with warnings${NC}"
    fi
    if [ "$smoke_ok" = true ]; then
        echo -e "  Smoke:      ${GREEN}passed${NC}"
    else
        echo -e "  Smoke:      ${YELLOW}needs manual verification${NC}"
    fi
    echo ""
    _ok "Build pipeline complete. Run manual smoke tests above before shipping."
    echo ""
}

cmd_deploy() {
    # Auto-scope deploy: detects what changed (backend/frontend/both) and
    # runs the correct build path. Integrates E2E smoke test as exit gate.
    local scope="${1:-auto}"
    local needs_backend=false
    local needs_frontend=false

    echo ""
    echo -e "${BOLD}SwarmAI Auto-Scope Deploy${NC}"
    echo "═════════════════════════"
    echo ""

    if [ "$scope" = "auto" ]; then
        # Compare HEAD against currently deployed daemon version
        local deployed_hash=""
        if [ -f "$DAEMON_VERSION_FILE" ]; then
            deployed_hash=$(awk '{print $2}' "$DAEMON_VERSION_FILE")
        fi

        # Fallback if no version file or hash is the semver (legacy)
        if [ -z "$deployed_hash" ] || [ ${#deployed_hash} -lt 7 ]; then
            deployed_hash="HEAD~5"
            _warn "No deployed git hash found — comparing last 5 commits"
        fi

        # Check what changed since last deploy
        local changed_files
        changed_files=$(git diff --name-only "$deployed_hash"..HEAD 2>/dev/null || git diff --name-only HEAD~5..HEAD)

        if echo "$changed_files" | grep -qE '^backend/|^scripts/'; then
            needs_backend=true
        fi
        if echo "$changed_files" | grep -qE '^desktop/src/|^desktop/index.html|^desktop/tailwind'; then
            needs_frontend=true
        fi

        # Also check uncommitted changes
        local unstaged
        unstaged=$(git status --porcelain 2>/dev/null || true)
        if echo "$unstaged" | grep -qE '^ ?M.*(backend|scripts)/'; then
            needs_backend=true
        fi
        if echo "$unstaged" | grep -qE '^ ?M.*desktop/src/'; then
            needs_frontend=true
        fi

        if [ "$needs_backend" = false ] && [ "$needs_frontend" = false ]; then
            # Safety net: if we couldn't get a valid diff, warn and default to backend
            if [ -z "$changed_files" ] && [ -z "$unstaged" ]; then
                _warn "Could not detect changes reliably — defaulting to backend build"
                needs_backend=true
            else
                _ok "Nothing to deploy (no backend/ or desktop/src/ changes since ${deployed_hash:0:7})"
                return 0
            fi
        fi

        _log "Auto-detected scope:"
        [ "$needs_backend" = true ] && echo "  ✓ Backend (Python changes)"
        [ "$needs_frontend" = true ] && echo "  ✓ Frontend (TypeScript/CSS changes)"
        echo ""
    elif [ "$scope" = "--backend" ]; then
        needs_backend=true
    elif [ "$scope" = "--frontend" ]; then
        needs_frontend=true
    elif [ "$scope" = "--all" ]; then
        needs_backend=true
        needs_frontend=true
    else
        _err "Unknown scope: $scope"
        echo "  Usage: ./prod.sh deploy [--backend|--frontend|--all]"
        return 1
    fi

    # ── Execute builds (explicit error handling — set -e alone is not enough
    # when cmd_build is called from within conditionals in some shells) ──
    if [ "$needs_backend" = true ]; then
        _log "Building backend..."
        if ! cmd_build; then
            _err "Backend build failed — aborting deploy"
            return 1
        fi
    fi

    if [ "$needs_frontend" = true ]; then
        _log "Building frontend (Tauri)..."
        if ! _build_frontend; then
            _err "Frontend build failed — aborting deploy"
            return 1
        fi
    fi

    # ── Post-deploy E2E smoke (scope-aware, retry once for model flakiness) ──
    echo ""
    local smoke_scope="full"
    if [ "$needs_backend" = false ] && [ "$needs_frontend" = true ]; then
        # Frontend-only deploy: skip chat stream (daemon didn't change)
        smoke_scope="frontend-only"
    fi

    _log "Running E2E smoke test (scope: $smoke_scope)..."
    if python3 "$PROJECT_ROOT/scripts/smoke_e2e.py" --scope "$smoke_scope"; then
        _ok "Deploy verified: all critical paths working ✓"
    else
        _warn "Smoke test failed — retrying once..."
        sleep 3
        if python3 "$PROJECT_ROOT/scripts/smoke_e2e.py" --scope "$smoke_scope" --verbose; then
            _ok "Deploy verified on retry: all critical paths working ✓"
        else
            _err "E2E smoke FAILED (2 attempts) — deploy has regressions"
            _err "Run: python3 scripts/smoke_e2e.py --scope $smoke_scope --verbose"
            return 1
        fi
    fi

    # ── Post-deploy canary (Gap #14): run eval canary for semantic correctness ──
    _log "Running eval canary (semantic correctness check)..."
    local canary_result
    canary_result=$(curl -s -X POST "http://127.0.0.1:18321/api/eval/canary" 2>/dev/null)
    local canary_status=$?
    if [ $canary_status -eq 0 ] && echo "$canary_result" | python3 -c "import sys,json; d=json.load(sys.stdin); sys.exit(0 if d.get('passed',d.get('status'))!='failed' else 1)" 2>/dev/null; then
        _ok "Eval canary passed ✓"
    else
        _warn "Eval canary failed or unavailable (non-blocking): $canary_result"
    fi
}

_build_frontend() {
    # Build frontend via Tauri (produces new .app binary with embedded assets)
    local app_path="/Applications/SwarmAI.app/Contents/MacOS/SwarmAI"
    local app_before=0
    if [ -f "$app_path" ]; then
        app_before=$(stat -f %m "$app_path" 2>/dev/null || echo 0)
    fi

    cd "$DESKTOP_DIR"
    _log "npm run build:all (this takes ~3-5 min)..."
    set +e  # Temporarily disable set -e to capture exit code through pipe
    npm run build:all 2>&1 | tail -5
    local npm_rc=${PIPESTATUS[0]}
    set -e
    if [ "$npm_rc" -ne 0 ]; then
        _err "npm run build:all failed (exit $npm_rc)"
        cd "$PROJECT_ROOT"
        return 1
    fi

    local app_after=0
    if [ -f "$app_path" ]; then
        app_after=$(stat -f %m "$app_path" 2>/dev/null || echo 0)
    fi

    if [ "$app_after" -gt "$app_before" ]; then
        _ok "Frontend built: .app binary updated"
        _warn "Relaunch SwarmAI.app to load new frontend"
    else
        _err "Frontend build may have failed — .app timestamp unchanged"
        _err "Check: ls -la $app_path"
        cd "$PROJECT_ROOT"
        return 1
    fi
    cd "$PROJECT_ROOT"
}

cmd_verify() {
    echo ""
    _log "Running post-build verification..."

    local target="${1:-$BACKEND_BINARY}"
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

    # Backend binary (PyInstaller bundle)
    if [ -f "$BACKEND_BINARY" ]; then
        local age=$(( ($(date +%s) - $(stat -f %m "$BACKEND_BINARY")) / 3600 ))
        _ok "Backend binary: $(du -h "$BACKEND_BINARY" | cut -f1), ${age}h old"
    else
        _err "Backend binary: not built"
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
    # Version file format: "{semver} {git_hash} {timestamp}" — field 2 is git hash
    echo ""
    if [ -f "$DAEMON_VERSION_FILE" ]; then
        local binary_hash
        binary_hash=$(awk '{print $2}' "$DAEMON_VERSION_FILE")
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

    # AI docs freshness
    _log "Refreshing AI_CONTEXT.md + AGENTS.md..."
    cd "$PROJECT_ROOT"
    if python backend/scripts/refresh_ai_docs.py 2>&1 | grep -q "Updated:"; then
        _ok "AI docs refreshed (commit the changes before release)"
    else
        _ok "AI docs already up to date"
    fi

    # Prose staleness detection (exit code 1 = stale or missing)
    _log "Checking AGENTS.md prose staleness..."
    if python backend/scripts/refresh_ai_docs.py --check-staleness; then
        _ok "AGENTS.md prose is current"
    else
        _err "AGENTS.md has stale/missing prose sections — fix before release"
        all_ok=false
    fi

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

# ── Hive Release ──────────────────────────────────────────

cmd_release_hive() {
    local start=$(date +%s)
    echo ""
    echo -e "${BOLD}SwarmAI Hive Package Build${NC}"
    echo "══════════════════════════"
    echo ""

    local version
    version=$(tr -d '[:space:]' < "$PROJECT_ROOT/VERSION")

    # Eval gate — Hive is a shippable target; don't let `release-hive` bypass it.
    # (release-all already gates via cmd_release, but standalone release-hive must
    # gate too.) rc 2 = proceed-with-warning is acceptable for a package build.
    local _eg_rc
    set +e; _eval_gate; _eg_rc=$?; set -e
    [ "$_eg_rc" = "1" ] && return 1

    # Step 1: Package
    _log "Step 1/2: Building Hive tar.gz (v${version})..."
    bash "$PROJECT_ROOT/hive/release.sh" "$version"

    # Step 2: Verify
    local archive="$PROJECT_ROOT/dist/swarmai-hive-v${version}-linux-arm64.tar.gz"
    _log "Step 2/2: Verifying Hive package..."
    if bash "$PROJECT_ROOT/hive/verify_package.sh" "$archive"; then
        _ok "Hive package verified"
    else
        _err "Hive package verification FAILED"
        return 1
    fi

    echo ""
    _ok "Hive package ready in $(_build_time $start)"
    _ok "Archive: $archive ($(du -h "$archive" | cut -f1))"
}

cmd_release_all() {
    local start=$(date +%s)
    echo ""
    echo -e "${BOLD}╔══════════════════════════════════════╗${NC}"
    echo -e "${BOLD}║  SwarmAI Unified Release Pipeline    ║${NC}"
    echo -e "${BOLD}║  Desktop (DMG) + Hive (tar.gz)       ║${NC}"
    echo -e "${BOLD}╚══════════════════════════════════════╝${NC}"
    echo ""

    local version
    version=$(tr -d '[:space:]' < "$PROJECT_ROOT/VERSION")

    # ── Part 1: Desktop Release ──────────────────────────────
    echo -e "${BOLD}Part 1/3: Desktop Release${NC}"
    echo "─────────────────────────"
    cmd_release
    echo ""

    # ── Part 2: Hive Release ─────────────────────────────────
    echo -e "${BOLD}Part 2/3: Hive Package${NC}"
    echo "──────────────────────"
    cmd_release_hive
    echo ""

    # ── Part 3: GitHub Release ───────────────────────────────
    echo -e "${BOLD}Part 3/3: GitHub Release${NC}"
    echo "────────────────────────"

    local dmg=$(ls "$DESKTOP_DIR/src-tauri/target/release/bundle/dmg/"*.dmg 2>/dev/null | head -1)
    local hive_tar="$PROJECT_ROOT/dist/swarmai-hive-v${version}-linux-arm64.tar.gz"
    local checksums="$PROJECT_ROOT/dist/checksums.txt"

    # Copy DMG to dist/ first, then generate checksums for both
    _log "Collecting artifacts into dist/..."
    if [ -n "$dmg" ]; then
        cp "$dmg" "$PROJECT_ROOT/dist/"
    fi

    # GitHub Release ships DESKTOP artifacts only. The Hive tar is local-only
    # (direction A): Part 2's release.sh already uploaded it to the account-suffixed
    # S3 bucket that provision/update read from — it must NOT go into the GitHub
    # Release (putting it here is the exact "hive in GitHub" drift the S3 supply
    # closure eliminated).
    _log "Generating desktop checksums..."
    cd "$PROJECT_ROOT/dist"
    : > checksums.txt
    if [ -n "$dmg" ]; then
        shasum -a 256 "$(basename "$dmg")" >> checksums.txt
    fi
    cd "$PROJECT_ROOT"

    echo ""
    echo -e "  ${CYAN}Artifacts ready for upload (desktop → GitHub):${NC}"
    if [ -n "$dmg" ]; then
        echo "    📦 $(basename "$dmg") ($(du -h "$dmg" | cut -f1))"
    fi
    echo "    📋 checksums.txt"
    if [ -f "$hive_tar" ]; then
        echo "    🐝 swarmai-hive-v${version}-linux-arm64.tar.gz ($(du -h "$hive_tar" | cut -f1)) → S3 (local-only, NOT GitHub)"
    fi
    echo ""

    # Offer to create GitHub release (desktop artifacts only)
    echo -e "  ${CYAN}Create GitHub Release:${NC}"
    echo ""
    local -a release_files=()
    if [ -n "$dmg" ]; then
        release_files+=("$PROJECT_ROOT/dist/$(basename "$dmg")")
    fi
    release_files+=("$checksums")
    echo "    gh release create v${version} \\"
    echo "      --title \"SwarmAI v${version}\" \\"
    echo "      --generate-notes \\"
    echo "      ${release_files[*]}"
    echo ""

    echo -n "  Create release now? [y/N] "
    read -r answer
    if [[ "$answer" =~ ^[Yy] ]]; then
        _log "Creating GitHub release v${version}..."
        if gh release create "v${version}" \
            --title "SwarmAI v${version}" \
            --generate-notes \
            "${release_files[@]}"; then
            _ok "GitHub Release v${version} created"
        else
            _warn "GitHub release creation failed — upload manually"
        fi
    else
        _log "Skipped. Run the command above to create the release."
    fi

    echo ""
    echo -e "${BOLD}════════════════════════════════════════${NC}"
    echo -e "${BOLD}  Unified Release Summary${NC}"
    echo -e "${BOLD}════════════════════════════════════════${NC}"
    echo ""
    echo "  Version:  ${version}"
    echo "  Desktop:  $([ -n "$dmg" ] && echo "✅ DMG ready" || echo "⚠️ DMG not found")"
    echo "  Hive:     ✅ tar.gz ready"
    echo "  Time:     $(_build_time $start)"
    echo ""
    _ok "Unified release pipeline complete."
    echo ""
}

# ── Main ────────────────────────────────────────────────────

case "${1:-help}" in
    build)          cmd_build ;;
    release)        cmd_release ;;
    release-all)    cmd_release_all ;;
    release-hive)   cmd_release_hive ;;
    deploy)         cmd_deploy ;;
    verify)         shift; cmd_verify "$@" ;;
    preflight)      cmd_preflight ;;
    status)         cmd_status ;;
    daemon)         shift; cmd_daemon "$@" ;;
    *)
        echo "SwarmAI Production Operations"
        echo ""
        echo "Usage: ./prod.sh [command]"
        echo ""
        echo "Build & Deploy:"
        echo "  build            Build backend binary + verify + deploy to daemon + restart"
        echo "  release          Full release: build + DMG + tag + publish"
        echo "  release-hive     Hive release: package tar.gz + verify"
        echo "  release-all      Unified: Desktop DMG + Hive tar.gz + GitHub Release"
        echo "  verify           Run post-build capability verification"
        echo "  preflight        Check readiness (tests, dirty tree, version) without building"
        echo "  status           Show daemon health, binary versions, staleness"
        echo "  deploy           Auto-detect scope (backend/frontend/both) + build + E2E verify"
        echo ""
        echo "Daemon:"
        echo "  daemon restart   Restart the backend daemon (launchd)"
        echo "  daemon stop      Stop the daemon"
        echo "  daemon start     Start the daemon"
        echo "  daemon status    Show daemon health (default)"
        echo "  daemon logs      Tail daemon logs"
        echo ""
        echo "Typical workflows:"
        echo "  ./prod.sh release-all          # Ship everything: Desktop + Hive + GitHub"
        echo "  ./prod.sh release              # Desktop only: check → build → DMG"
        echo "  ./prod.sh release-hive         # Hive only: tar.gz + verify"
        echo "  ./prod.sh deploy               # Auto-detect changes → build + E2E verify"
        echo "  ./prod.sh build                # Backend only: build + deploy + restart"
        echo "  ./prod.sh status               # Check what's running"
        ;;
esac
