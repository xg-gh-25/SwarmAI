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
# --prefer-offline is the difference between ~7 minutes and <1 second here.
# npm rebuilds the ideal tree on every install, and that needs a packument for each
# of the ~690 locked packages. The registry serves packuments with
# `cache-control: max-age=300`, so any run more than 5 minutes after the last one
# finds the whole cache stale and revalidates all of them — serially, over one
# keep-alive socket. On a VPN'd link (~0.5s round trip) that is the entire 7 minutes,
# and it happens even when the answer is "up to date" and nothing gets written.
# --prefer-offline reuses cached metadata regardless of age and still hits the network
# for genuine cache MISSES, so a changed package-lock.json resolves correctly.
# (--offline would be wrong: it hard-fails on a miss.)
# --no-audit drops the audit round trip; --no-fund drops the funding banner.
npm install --prefer-offline --no-audit --no-fund

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

    # ── Auto-open the installer window (local builds only) ────────────────────
    # WHY TWO STEPS (attach, then open the mount point) INSTEAD OF `open <dmg>`:
    # tauri-build.sh forces the bundler's --skip-jenkins path, so the DMG carries
    # neither a .DS_Store (window geometry / icon layout) nor a bless auto-open
    # flag — and bundle_dmg.sh defaults BLESS=0 anyway, with the arm64 branch
    # omitting --openfolder even when blessed. Consequence, verified by repro:
    # `open <dmg>` DOES mount the volume but Finder never surfaces a window, so
    # the build looks like it did nothing. Attaching explicitly gives us the mount
    # point, and opening THAT is what actually raises the Finder window.
    #
    # $CI is still unset here: tauri-build.sh sets CI=true for the bundler, but it
    # runs as a child process (`bash tauri-build.sh`), so that export does not leak
    # back into this shell. So this check sees real CI only — where `open` has no
    # GUI session to talk to and must be skipped.
    #
    # Opt out locally with SWARMAI_SKIP_DMG_OPEN=1.
    if [ -z "$CI" ] && [ -z "$SWARMAI_SKIP_DMG_OPEN" ] && [ -d "$DMG_PATH" ]; then
        dmg_file="$(ls -t "$DMG_PATH"/*.dmg 2>/dev/null | head -1)"
        if [ -n "$dmg_file" ]; then
            # Reuse an existing mount of this exact image rather than stacking a
            # second "SwarmAI 1" volume next to it.
            mount_point="$(hdiutil info 2>/dev/null | awk -v img="$dmg_file" '
                /^image-path/ { p = $0; sub(/^image-path[ \t]*:[ \t]*/, "", p); cur = (p == img) }
                cur && /^\/dev\/disk/ && index($0, "/Volumes/") {
                    mp = $0; sub(/^.*\/Volumes\//, "/Volumes/", mp); print mp; exit
                }
            ')" || mount_point=""
            if [ -z "$mount_point" ]; then
                mount_point="$(hdiutil attach "$dmg_file" 2>/dev/null \
                    | awk -F'\t' '/^\/dev\/disk/ && NF >= 3 { mp = $NF } END { print mp }')" \
                    || mount_point=""
            fi
            echo ""
            if [ -n "$mount_point" ] && [ -d "$mount_point" ]; then
                open "$mount_point" 2>/dev/null || true
                echo "Installer window opened: $mount_point"
                echo "  → Drag SwarmAI.app onto the Applications shortcut to install."
            else
                echo "Could not auto-open the installer. Run manually:"
                echo "  open \"$dmg_file\""
            fi
        fi
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
