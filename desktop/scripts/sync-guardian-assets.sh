#!/bin/bash
# Sync the C034 guardian assets into desktop/resources/daemon/ (the Tauri-bundled
# location) from their sources of truth, so the .app always ships current copies.
#
# Sources of truth:
#   backend/core/daemon_guard.py            → daemon_guard.py (standalone stdlib guard)
#   backend/channels/swarmai_guardian.sh    → swarmai_guardian.sh (guardian loop)
#   backend/channels/com.swarmai.guardian.plist → com.swarmai.guardian.plist.template
#
# Why a dedicated script (not just build-backend.sh): the .app embed happens at
# `tauri build`, whose beforeBuildCommand runs THIS via npm. A direct
# `npm run tauri build` (without prod.sh's build:backend step) would otherwise
# ship stale committed copies. Wiring this into beforeBuildCommand makes the
# freshness invariant hold regardless of build entry point. A pytest
# (test_staged_guard_py_identical_to_source) is the backstop.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DESKTOP_DIR="$(dirname "$SCRIPT_DIR")"
REPO_ROOT="$(dirname "$DESKTOP_DIR")"
BACKEND_DIR="$REPO_ROOT/backend"
RES_DIR="$DESKTOP_DIR/resources/daemon"

mkdir -p "$RES_DIR"
cp "$BACKEND_DIR/core/daemon_guard.py"               "$RES_DIR/daemon_guard.py"
cp "$BACKEND_DIR/channels/swarmai_guardian.sh"        "$RES_DIR/swarmai_guardian.sh"
cp "$BACKEND_DIR/channels/com.swarmai.guardian.plist" "$RES_DIR/com.swarmai.guardian.plist.template"
chmod +x "$RES_DIR/swarmai_guardian.sh"
echo "Synced guardian assets → $RES_DIR"
