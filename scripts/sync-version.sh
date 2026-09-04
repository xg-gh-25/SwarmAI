#!/bin/bash
# Sync version from the single source of truth (VERSION file) to all targets.
#
# Usage:
#   ./scripts/sync-version.sh          — sync current VERSION to all files
#   ./scripts/sync-version.sh 1.7.0    — set VERSION to 1.7.0 and sync
#   ./scripts/sync-version.sh check    — check if all files match (exit 1 if not)
#
# Targets synced:
#   backend/config.py          — app_version: str = "X.Y.Z"
#   backend/pyproject.toml     — version = "X.Y.Z"
#   desktop/package.json       — "version": "X.Y.Z"
#   desktop/src-tauri/tauri.conf.json  — "version": "X.Y.Z"
#   desktop/src-tauri/Cargo.toml       — version = "X.Y.Z"

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
VERSION_FILE="$PROJECT_ROOT/VERSION"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
NC='\033[0m'

# ── Read or set VERSION ───────────────────────────────────

if [ "$1" = "check" ]; then
    MODE="check"
elif [ -n "$1" ]; then
    # Validate semver format
    if [[ ! "$1" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
        echo -e "${RED}❌ Invalid version: $1 (must be X.Y.Z)${NC}"
        exit 1
    fi
    echo "$1" > "$VERSION_FILE"
    MODE="sync"
else
    MODE="sync"
fi

if [ ! -f "$VERSION_FILE" ]; then
    echo -e "${RED}❌ VERSION file not found at $VERSION_FILE${NC}"
    exit 1
fi

VERSION=$(tr -d '[:space:]' < "$VERSION_FILE")

if [[ ! "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    echo -e "${RED}❌ Invalid version in VERSION file: '$VERSION'${NC}"
    exit 1
fi

# ── Target definitions ────────────────────────────────────
# Each target: file path, grep pattern to extract current version, sed pattern to replace

declare -a TARGETS=(
    "backend/pyproject.toml"
    "desktop/package.json"
    "desktop/src-tauri/tauri.conf.json"
    "desktop/src-tauri/Cargo.toml"
)
# Note: backend/config.py removed — it reads VERSION file directly at runtime (no hardcoded fallback)

_get_version() {
    local file="$1"
    local basename=$(basename "$file")
    case "$basename" in
        pyproject.toml)
            # Match the project-level version, not dependency versions
            grep '^version = ' "$file" | head -1 | sed 's/version = "\(.*\)"/\1/'
            ;;
        Cargo.toml)
            grep '^version = ' "$file" | head -1 | sed 's/version = "\(.*\)"/\1/'
            ;;
        package.json|tauri.conf.json)
            python3 -c "import json, sys; print(json.load(open(sys.argv[1]))['version'])" "$file"
            ;;
    esac
}

_set_version() {
    local file="$1"
    local ver="$2"
    local basename=$(basename "$file")
    case "$basename" in
        pyproject.toml|Cargo.toml)
            # BSD sed (macOS) doesn't support 0,/pat/ — use python for reliable first-match replace
            python3 -c "
import re, pathlib, sys
p = pathlib.Path(sys.argv[1])
txt = p.read_text()
p.write_text(re.sub(r'^version = \"[^\"]*\"', 'version = \"' + sys.argv[2] + '\"', txt, count=1, flags=re.MULTILINE))
" "$file" "$ver"
            ;;
        package.json|tauri.conf.json)
            python3 -c "
import json, sys
with open(sys.argv[1]) as f:
    d = json.load(f)
d['version'] = sys.argv[2]
with open(sys.argv[1], 'w') as f:
    json.dump(d, f, indent=2)
    f.write('\n')
" "$file" "$ver"
            ;;
    esac
}

# ── Execute ───────────────────────────────────────────────

all_match=true

for target in "${TARGETS[@]}"; do
    file="$PROJECT_ROOT/$target"
    if [ ! -f "$file" ]; then
        echo -e "${YELLOW}⚠️  $target — file not found${NC}"
        all_match=false
        continue
    fi

    current=$(_get_version "$file")

    if [ "$current" = "$VERSION" ]; then
        echo -e "${GREEN}✅${NC} $target — $current"
    elif [ "$MODE" = "check" ]; then
        echo -e "${RED}❌${NC} $target — $current (expected $VERSION)"
        all_match=false
    else
        _set_version "$file" "$VERSION"
        echo -e "${GREEN}✅${NC} $target — $current → $VERSION"
    fi
done

echo ""
if [ "$MODE" = "check" ]; then
    if $all_match; then
        echo -e "${GREEN}All versions match: $VERSION${NC}"
        exit 0
    else
        echo -e "${RED}Version mismatch detected. Run: ./scripts/sync-version.sh${NC}"
        exit 1
    fi
else
    # Update lockfiles to match bumped versions.
    #
    # SCOPE: this script writes ONE number — the package's own version. Neither
    # lockfile refresh below may re-resolve the dependency graph. Widening that
    # scope is what broke the build on 2026-09-04 (see Cargo.lock note).
    #
    # Cargo.lock — `cargo update --workspace` updates only the workspace member
    # entries. Do NOT use `cargo generate-lockfile` here: it re-resolves the WHOLE
    # graph to newest-semver-compatible, i.e. a silent `cargo update` on every
    # `./prod.sh build`. That is how tauri-plugin-notification (2.3.3 -> 2.4.0) and
    # tauri-plugin-updater (2.10.1 -> 2.11.0) drifted a MINOR ahead of their npm
    # counterparts, which the Tauri CLI's version-parity preflight rejects — a
    # version-sync step failing the build it was supposed to prepare.
    # --offline additionally guarantees no registry round trip.
    cargo_dir="$PROJECT_ROOT/desktop/src-tauri"
    if [ -f "$cargo_dir/Cargo.lock" ]; then
        (cd "$cargo_dir" && cargo update --workspace --offline 2>/dev/null) && \
            echo -e "${GREEN}✅${NC} Cargo.lock updated" || true
    fi
    # package-lock.json (npm install --package-lock-only doesn't touch node_modules).
    # --prefer-offline is load-bearing: --package-lock-only IS a full tree resolve, so
    # without it npm revalidates a packument for each of the ~690 locked packages
    # (registry TTL is max-age=300, so anything but a back-to-back run finds the cache
    # stale) — serially, over one socket. Measured ~7min vs <1s, and 2>/dev/null makes
    # it a silent stall right after "Cargo.lock updated".
    pkg_dir="$PROJECT_ROOT/desktop"
    if [ -f "$pkg_dir/package-lock.json" ]; then
        (cd "$pkg_dir" && npm install --package-lock-only --ignore-scripts \
            --prefer-offline --no-audit --no-fund 2>/dev/null) && \
            echo -e "${GREEN}✅${NC} package-lock.json updated" || true
    fi
    echo -e "${GREEN}All versions synced to $VERSION${NC}"
fi
