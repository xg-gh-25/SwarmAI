#!/bin/bash
# Cross-platform pre-build script for Tauri's beforeBuildCommand.
# Runs guardian asset sync (macOS-only, non-fatal) then builds frontend.
#
# This script exists because Tauri executes beforeBuildCommand via the
# platform's default shell (cmd.exe on Windows, bash on macOS/Linux).
# A semicolon-separated compound command in tauri.conf.json breaks on
# Windows since `;` is not a cmd.exe command separator.
set -eu   # -u: an undefined variable stops the build instead of turning a path into "/..."

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Tauri runs this script on its own (beforeBuildCommand), so nothing from build.sh is
# inherited: define the desktop dir here, the same way build.sh does.
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

# Guardian asset sync (macOS only, non-fatal)
if [[ "$(uname)" == "Darwin" ]]; then
  bash "$SCRIPT_DIR/sync-guardian-assets.sh" || echo "WARN: guardian asset sync failed — shipping committed copies"
fi

# Build frontend (Vite)
cd "$PROJECT_ROOT"
npm run build


# App Platform Phase A: bind the canonical App/binding identity to the exact
# staged backend/resources AND compiled frontend. This runs after Vite and before
# Tauri bundles the resource, so source metadata cannot stand in for built bytes.
REPO_ROOT="$(dirname "$PROJECT_ROOT")"
STAGE_DIR="$PROJECT_ROOT/src-tauri/bundle-backend"
INSTALL_MANIFEST="$STAGE_DIR/install-manifest.json"
[ -f "$INSTALL_MANIFEST" ] || { echo "ERROR: backend install manifest missing; run the backend build first" >&2; exit 1; }
TARGET="$(node -e 'const fs=require("fs"); console.log(JSON.parse(fs.readFileSync(process.argv[1],"utf8")).target)' "$INSTALL_MANIFEST")"
case "$TARGET" in
  *-apple-darwin) APP_PROFILE="desktop-daemon" ;;
  *) APP_PROFILE="desktop-subprocess" ;;
esac
PY=python3; python3 -c '' >/dev/null 2>&1 || PY=python
"$PY" "$REPO_ROOT/backend/scripts/generate_app_execution_releases.py" \
  --apps-index "$REPO_ROOT/backend/generated/apps_index.json" \
  --install-manifest "$INSTALL_MANIFEST" \
  --profile "$APP_PROFILE" \
  --frontend-dir "$PROJECT_ROOT/dist" \
  --dependency-lock "$REPO_ROOT/backend/uv.lock" \
  --dependency-lock "$PROJECT_ROOT/package-lock.json" \
  --output "$STAGE_DIR/app-execution-releases.json"
