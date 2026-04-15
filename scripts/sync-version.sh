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
    "backend/config.py"
    "backend/pyproject.toml"
    "desktop/package.json"
    "desktop/src-tauri/tauri.conf.json"
    "desktop/src-tauri/Cargo.toml"
)

_get_version() {
    local file="$1"
    local basename=$(basename "$file")
    case "$basename" in
        config.py)
            # Pattern: app_version: str = _read_version("X.Y.Z")
            grep 'app_version.*_read_version(' "$file" | head -1 | sed 's/.*_read_version("\([^"]*\)").*/\1/'
            ;;
        pyproject.toml)
            # Match the project-level version, not dependency versions
            grep '^version = ' "$file" | head -1 | sed 's/version = "\(.*\)"/\1/'
            ;;
        Cargo.toml)
            grep '^version = ' "$file" | head -1 | sed 's/version = "\(.*\)"/\1/'
            ;;
        package.json)
            python3 -c "import json; print(json.load(open('$file'))['version'])"
            ;;
        tauri.conf.json)
            python3 -c "import json; print(json.load(open('$file'))['version'])"
            ;;
    esac
}

_set_version() {
    local file="$1"
    local ver="$2"
    local basename=$(basename "$file")
    case "$basename" in
        config.py)
            # Pattern: _read_version("X.Y.Z")
            sed -i '' "s/_read_version(\"[^\"]*\")/_read_version(\"${ver}\")/" "$file"
            ;;
        pyproject.toml|Cargo.toml)
            # BSD sed (macOS) doesn't support 0,/pat/ — use python for reliable first-match replace
            python3 -c "
import re, pathlib
p = pathlib.Path('$file')
txt = p.read_text()
p.write_text(re.sub(r'^version = \"[^\"]*\"', 'version = \"$ver\"', txt, count=1, flags=re.MULTILINE))
"
            ;;
        package.json)
            python3 -c "
import json
with open('$file') as f:
    d = json.load(f)
d['version'] = '$ver'
with open('$file', 'w') as f:
    json.dump(d, f, indent=2)
    f.write('\n')
"
            ;;
        tauri.conf.json)
            python3 -c "
import json
with open('$file') as f:
    d = json.load(f)
d['version'] = '$ver'
with open('$file', 'w') as f:
    json.dump(d, f, indent=2)
    f.write('\n')
"
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
    # Update Cargo.lock if Cargo.toml was synced
    cargo_dir="$PROJECT_ROOT/desktop/src-tauri"
    if [ -f "$cargo_dir/Cargo.lock" ]; then
        (cd "$cargo_dir" && cargo generate-lockfile 2>/dev/null) && \
            echo -e "${GREEN}✅${NC} Cargo.lock updated" || true
    fi
    echo -e "${GREEN}All versions synced to $VERSION${NC}"
fi
