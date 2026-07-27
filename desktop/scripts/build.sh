#!/bin/bash
# Build the complete desktop application
# This script:
# 1. Builds the Python backend with PyInstaller
# 2. Builds the Tauri desktop app (frontend + Rust)
# 3. Creates the DMG installer (macOS)

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

echo "========================================"
echo "Claude Agent Platform Desktop App Build"
echo "========================================"
echo ""

# Check prerequisites
echo "Checking prerequisites..."

# Check for Node.js
if ! command -v node &> /dev/null; then
    echo "Error: Node.js is not installed. Please install it from https://nodejs.org/"
    exit 1
fi
echo "✓ Node.js $(node --version)"

# Check for npm
if ! command -v npm &> /dev/null; then
    echo "Error: npm is not installed."
    exit 1
fi
echo "✓ npm $(npm --version)"

# Check for Python
if ! command -v python3 &> /dev/null; then
    echo "Error: Python 3 is not installed."
    exit 1
fi
echo "✓ Python $(python3 --version)"

# Check for Rust/Cargo
if ! command -v cargo &> /dev/null; then
    echo "Error: Rust/Cargo is not installed. Please install from https://rustup.rs/"
    exit 1
fi
echo "✓ Cargo $(cargo --version)"

echo ""
echo "Step 1/3: Building Python backend..."
echo "--------------------------------------"
cd "$PROJECT_ROOT"
./scripts/build-backend.sh

echo ""
echo "Step 2/3: Installing frontend dependencies..."
echo "----------------------------------------------"
cd "$PROJECT_ROOT"
npm install

echo ""
echo "Step 3/3: Building Tauri application..."
echo "----------------------------------------"

# Pre-flight: detach any stale DMG mounts from previous failed builds.
# Tauri's bundle_dmg.sh has no cleanup trap — if it fails mid-way, the temp RW DMG
# stays mounted and blocks future builds (hdiutil can't convert while mounted).
if [[ "$OSTYPE" == "darwin"* ]]; then
    echo "Checking for stale DMG mounts..."
    stale_devs=$(hdiutil info 2>/dev/null | awk -v pat="rw\\..*SwarmAI" '
        /^===/ { in_section=0 }
        $0 ~ pat { in_section=1 }
        in_section && /^\/dev\/disk/ { print $1 }
    ' | sed 's/s[0-9]*$//' | sort -u || true)
    if [[ -n "$stale_devs" ]]; then
        echo "Found stale mount(s) from previous builds, detaching..."
        while IFS= read -r dev; do
            echo "  Detaching $dev..."
            hdiutil detach "$dev" -force 2>/dev/null || true
        done <<< "$stale_devs"
        sleep 1
    fi
    # Also clean up orphaned temp RW DMG files
    rw_files=$(find "$PROJECT_ROOT/src-tauri/target/release/bundle" -name "rw.*.dmg" 2>/dev/null || true)
    if [[ -n "$rw_files" ]]; then
        echo "Cleaning orphaned temp DMG files..."
        echo "$rw_files" | while read -r f; do
            echo "  Removing $f"
            rm -f "$f"
        done
    fi
fi

# Signing-key-aware wrapper: signs when TAURI_SIGNING_PRIVATE_KEY is set (CI),
# skips updater-artifact signing when absent (local dev). See tauri-build.sh.
bash "$SCRIPT_DIR/tauri-build.sh"

echo ""
echo "========================================"
echo "Build Complete!"
echo "========================================"
echo ""

# Show output location
if [[ "$OSTYPE" == "darwin"* ]]; then
    DMG_PATH="$PROJECT_ROOT/src-tauri/target/release/bundle/dmg"
    APP_PATH="$PROJECT_ROOT/src-tauri/target/release/bundle/macos"

    if [ -d "$DMG_PATH" ]; then
        echo "DMG installer: $(ls "$DMG_PATH"/*.dmg 2>/dev/null || echo 'Not found')"
    fi
    if [ -d "$APP_PATH" ]; then
        echo "Application bundle: $(ls -d "$APP_PATH"/*.app 2>/dev/null || echo 'Not found')"
    fi
elif [[ "$OSTYPE" == "linux"* ]]; then
    DEB_PATH="$PROJECT_ROOT/src-tauri/target/release/bundle/deb"
    APPIMAGE_PATH="$PROJECT_ROOT/src-tauri/target/release/bundle/appimage"

    if [ -d "$DEB_PATH" ]; then
        echo "DEB package: $(ls "$DEB_PATH"/*.deb 2>/dev/null || echo 'Not found')"
    fi
    if [ -d "$APPIMAGE_PATH" ]; then
        echo "AppImage: $(ls "$APPIMAGE_PATH"/*.AppImage 2>/dev/null || echo 'Not found')"
    fi
fi

echo ""
echo "Build artifacts are in: $PROJECT_ROOT/src-tauri/target/release/bundle/"
