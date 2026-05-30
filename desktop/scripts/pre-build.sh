#!/bin/bash
# Cross-platform pre-build script for Tauri's beforeBuildCommand.
# Runs guardian asset sync (macOS-only, non-fatal) then builds frontend.
#
# This script exists because Tauri executes beforeBuildCommand via the
# platform's default shell (cmd.exe on Windows, bash on macOS/Linux).
# A semicolon-separated compound command in tauri.conf.json breaks on
# Windows since `;` is not a cmd.exe command separator.
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Guardian asset sync (macOS only, non-fatal)
if [[ "$(uname)" == "Darwin" ]]; then
  bash "$SCRIPT_DIR/sync-guardian-assets.sh" || echo "WARN: guardian asset sync failed — shipping committed copies"
fi

# Build frontend (Vite)
cd "$(dirname "$SCRIPT_DIR")"
npm run build
