#!/bin/bash
# AI-Ready-Repo Engine — Universal IDE Installer
# Zero-config, non-destructive. Supports 12+ IDEs via platforms_table.
#
# Usage:
#   bash install.sh <source_dir> <target_project> [platform]
#   bash install.sh <source_dir> <target_project> --force
#   bash install.sh <source_dir> <target_project> --uninstall
#   bash install.sh --list-platforms
#
# source_dir: directory containing AGENTS.md + .ai-context/ (engine output)
# target_project: root of the project to make AI-ready
# platform: optional — auto-detects if not specified
#
# Requires: bash (uses arrays). macOS + Linux compatible.

set -eo pipefail

# ─── Platforms Table ───
# Format: id|agents_file_target|ddd_dir_target|detect_pattern
# detect_pattern: file/dir to check in target for auto-detection
# Agents file = entry point (AGENTS.md content). DDD dir = .ai-context/ content.

platforms_table() {
  cat <<'EOF'
claude-code|AGENTS.md|.ai-context|.claude
kiro|.kiro/steering/ai-ready-context.md|.kiro/docs/ai-ready|.kiro
cursor|AGENTS.md|.ai-context|.cursor
codex|AGENTS.md|.ai-context|.codex
gemini|AGENTS.md|.ai-context|.gemini
opencode|AGENTS.md|.ai-context|.opencode
vscode-copilot|AGENTS.md|.ai-context|.copilot
windsurf|AGENTS.md|.ai-context|.windsurf
cline|AGENTS.md|.ai-context|.cline
hermes|AGENTS.md|.ai-context|.hermes
trae|AGENTS.md|.ai-context|.trae
generic|AGENTS.md|.ai-context|NONE
EOF
}

platform_ids() { platforms_table | cut -d'|' -f1; }

resolve_platform() {
  local id="$1"
  platforms_table | awk -F'|' -v id="$id" '$1==id {print; exit}'
}

# ─── Args ───

SOURCE="${1:-}"
TARGET="${2:-}"
PLATFORM=""
FORCE=false
UNINSTALL=false
LIST_PLATFORMS=false

for arg in "$@"; do
    case "$arg" in
        --force) FORCE=true ;;
        --uninstall) UNINSTALL=true ;;
        --list-platforms) LIST_PLATFORMS=true ;;
    esac
done

# Check if 3rd positional arg is a platform name (not a flag)
if [ -n "${3:-}" ] && [[ "${3}" != --* ]]; then
    PLATFORM="$3"
fi

if [ "$LIST_PLATFORMS" = true ]; then
    echo "Supported platforms:"
    platforms_table | while IFS='|' read -r id agents ddd detect; do
        printf "  %-16s agents→%s  ddd→%s\n" "$id" "$agents" "$ddd"
    done
    exit 0
fi

if [ -z "$SOURCE" ] || [ -z "$TARGET" ]; then
    echo "Usage: bash install.sh <source_dir> <target_project> [platform] [--force] [--uninstall]"
    echo ""
    echo "  source_dir:     Directory containing AGENTS.md + .ai-context/"
    echo "  target_project: Root of the project to install into"
    echo "  platform:       Optional (auto-detects). Use --list-platforms to see all."
    echo ""
    echo "  --force:          Overwrite existing files"
    echo "  --uninstall:      Remove installed files (reads manifest)"
    echo "  --list-platforms: Show supported platforms"
    exit 1
fi

# Resolve paths
SOURCE="$(cd "$SOURCE" && pwd)"
TARGET="$(cd "$TARGET" && pwd)"

# ─── Worktree Detection ───
# Claude Code worktrees are ephemeral — output written there is lost on session end.

if command -v git >/dev/null 2>&1 && [ -d "$TARGET/.git" ] || git -C "$TARGET" rev-parse 2>/dev/null; then
    COMMON_DIR=$(git -C "$TARGET" rev-parse --git-common-dir 2>/dev/null || true)
    GIT_DIR=$(git -C "$TARGET" rev-parse --git-dir 2>/dev/null || true)
    if [ -n "$COMMON_DIR" ] && [ -n "$GIT_DIR" ]; then
        COMMON_ABS=$(cd "$TARGET" && cd -- "$COMMON_DIR" 2>/dev/null && pwd -P || echo "")
        GIT_ABS=$(cd "$TARGET" && cd -- "$GIT_DIR" 2>/dev/null && pwd -P || echo "")
        if [ -n "$COMMON_ABS" ] && [ -n "$GIT_ABS" ] && [ "$COMMON_ABS" != "$GIT_ABS" ]; then
            MAIN_ROOT=$(dirname "$COMMON_ABS")
            echo "⚠️  WARNING: Target is a git worktree (ephemeral)."
            echo "   Output written here may be lost when the session ends."
            echo "   Main repo root: $MAIN_ROOT"
            echo "   Recommendation: install to main root instead:"
            echo "     bash install.sh $SOURCE $MAIN_ROOT"
            echo ""
            echo "   Continuing anyway (Ctrl+C to abort)..."
            sleep 2
        fi
    fi
fi

# ─── Uninstall Mode ───

if [ "$UNINSTALL" = true ]; then
    # Detect platform to find correct manifest location
    if [ -d "$TARGET/.kiro" ]; then
        MANIFEST="$TARGET/.kiro/docs/ai-ready/WHAT_WAS_ADDED.md"
    else
        MANIFEST="$TARGET/.ai-context/WHAT_WAS_ADDED.md"
    fi
    if [ ! -f "$MANIFEST" ]; then
        echo "❌ No WHAT_WAS_ADDED.md found — nothing to uninstall."
        echo "   Checked: $MANIFEST"
        exit 1
    fi
    echo "🗑️  Uninstalling AI-Ready artifacts from $TARGET..."
    grep '^- ' "$MANIFEST" | sed 's/^- //' | while read -r filepath; do
        full="$TARGET/$filepath"
        if [ -f "$full" ]; then
            rm "$full"
            echo "  removed: $filepath"
        fi
    done
    [ -d "$TARGET/.ai-context" ] && rmdir "$TARGET/.ai-context" 2>/dev/null && echo "  removed: .ai-context/" || true
    rm -f "$MANIFEST" 2>/dev/null
    echo "✅ Uninstall complete."
    exit 0
fi

# ─── Validate Source ───

if [ ! -f "$SOURCE/AGENTS.md" ]; then
    echo "❌ Source directory missing AGENTS.md: $SOURCE"
    echo "   Run the AI-Ready-Repo Engine first to generate output."
    exit 1
fi

if [ ! -d "$SOURCE/.ai-context" ]; then
    echo "❌ Source directory missing .ai-context/: $SOURCE"
    exit 1
fi

# ─── Auto-Detect Platform ───

if [ -z "$PLATFORM" ]; then
    # Check each platform's detect_pattern against target directory
    PLATFORM="generic"
    while IFS='|' read -r id agents ddd detect; do
        if [ "$detect" != "NONE" ] && [ -d "$TARGET/$detect" ]; then
            PLATFORM="$id"
            break
        fi
    done < <(platforms_table)
fi

# Resolve platform config
PLATFORM_ROW=$(resolve_platform "$PLATFORM")
if [ -z "$PLATFORM_ROW" ]; then
    echo "❌ Unknown platform: $PLATFORM"
    echo "   Use --list-platforms to see supported platforms."
    exit 1
fi

IFS='|' read -r P_ID P_AGENTS P_DDD P_DETECT <<< "$PLATFORM_ROW"

echo "🏛️  AI-Ready-Repo Installer"
echo "   Source:   $SOURCE"
echo "   Target:   $TARGET"
echo "   Platform: $PLATFORM"
echo ""

# ─── Install — Track Everything for Manifest ───

INSTALLED_FILES=()

install_file() {
    local src="$1"
    local dst="$2"
    local rel="${dst#$TARGET/}"

    if [ -f "$dst" ] && [ "$FORCE" != true ]; then
        echo "  skip: $rel (exists — use --force to overwrite)"
        return
    fi

    mkdir -p "$(dirname "$dst")"
    cp "$src" "$dst"
    INSTALLED_FILES+=("$rel")
    echo "  ✓ $rel"
}

# ─── Install Files Based on Platform Config ───

echo "Installing for $PLATFORM..."
echo ""

# Install agents entry point
install_file "$SOURCE/AGENTS.md" "$TARGET/$P_AGENTS"

# Install .ai-context/ contents to DDD target
for f in "$SOURCE/.ai-context/"*; do
    [ -f "$f" ] || continue
    filename="$(basename "$f")"
    install_file "$f" "$TARGET/$P_DDD/$filename"
done

# ─── Generate Manifest ───

if [ ${#INSTALLED_FILES[@]} -eq 0 ]; then
    echo ""
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "✅ Installed 0 files for $PLATFORM (all already present)"
    echo "   Use --force to overwrite existing files."
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    exit 0
fi

MANIFEST_PATH="$TARGET/$P_DDD/WHAT_WAS_ADDED.md"
mkdir -p "$(dirname "$MANIFEST_PATH")"
{
    echo "# What Was Added"
    echo ""
    echo "Installed by AI-Ready-Repo Engine on $(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "Platform: $PLATFORM | Source: $SOURCE"
    echo ""
    echo "## Files (run with --uninstall to remove all)"
    echo ""
    for f in "${INSTALLED_FILES[@]}"; do
        echo "- $f"
    done
    echo "- $(echo "$MANIFEST_PATH" | sed "s|$TARGET/||")"
} > "$MANIFEST_PATH"

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "✅ Installed ${#INSTALLED_FILES[@]} files for $PLATFORM"
echo ""
echo "   Your agent now has full project understanding."
echo "   See $P_DDD/REVIEW-REPORT.md for confidence levels and gaps."
echo ""
echo "   Manifest: $P_DDD/WHAT_WAS_ADDED.md"
echo "   Uninstall: bash install.sh $SOURCE $TARGET --uninstall"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
