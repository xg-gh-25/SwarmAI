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

# ── Hive Release ──────────────────────────────────────────

cmd_release_hive() {
    local start=$(date +%s)
    echo ""
    echo -e "${BOLD}SwarmAI Hive Package Build${NC}"
    echo "══════════════════════════"
    echo ""

    local version
    version=$(tr -d '[:space:]' < "$PROJECT_ROOT/VERSION")

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

    _log "Generating unified checksums..."
    cd "$PROJECT_ROOT/dist"
    : > checksums.txt
    if [ -n "$dmg" ]; then
        shasum -a 256 "$(basename "$dmg")" >> checksums.txt
    fi
    shasum -a 256 "swarmai-hive-v${version}-linux-arm64.tar.gz" >> checksums.txt
    cd "$PROJECT_ROOT"

    echo ""
    echo -e "  ${CYAN}Artifacts ready for upload:${NC}"
    if [ -n "$dmg" ]; then
        echo "    📦 $(basename "$dmg") ($(du -h "$dmg" | cut -f1))"
    fi
    echo "    📦 swarmai-hive-v${version}-linux-arm64.tar.gz ($(du -h "$hive_tar" | cut -f1))"
    echo "    📋 checksums.txt"
    echo ""

    # Offer to create GitHub release
    echo -e "  ${CYAN}Create GitHub Release:${NC}"
    echo ""
    local -a release_files=()
    if [ -n "$dmg" ]; then
        release_files+=("$PROJECT_ROOT/dist/$(basename "$dmg")")
    fi
    release_files+=("$hive_tar" "$checksums")
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
        echo "  build            Build backend binary + verify + deploy to daemon"
        echo "  release          Desktop release: preflight → build → verify → DMG → smoke test"
        echo "  release-hive     Hive release: package tar.gz + verify"
        echo "  release-all      Unified: Desktop DMG + Hive tar.gz + GitHub Release"
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
        echo "  ./prod.sh release-all          # Ship everything: Desktop + Hive + GitHub"
        echo "  ./prod.sh release              # Desktop only: check → build → DMG"
        echo "  ./prod.sh release-hive         # Hive only: tar.gz + verify"
        echo "  ./prod.sh build                # Backend change → build + deploy + restart"
        echo "  ./prod.sh status               # Check what's running"
        ;;
esac
