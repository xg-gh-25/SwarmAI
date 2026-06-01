#!/bin/bash
# AI-Ready-Repo Engine — IDE Installer
# Zero-config, non-destructive. Detects IDE, places files, generates manifest.
#
# Usage:
#   bash install.sh <source_dir> <target_project>
#   bash install.sh <source_dir> <target_project> --force    # overwrite existing
#   bash install.sh <source_dir> <target_project> --uninstall # remove installed files
#
# source_dir: directory containing AGENTS.md + .ai-ready/ (engine output)
# target_project: root of the project to make AI-ready
#
# Supports: Claude Code, Kiro. Default: Claude Code (AGENTS.md is most universal).
# Requires: bash (uses arrays). macOS + Linux compatible.

set -eo pipefail

# ─── Args ───

SOURCE="${1:-}"
TARGET="${2:-}"
FORCE=false
UNINSTALL=false

for arg in "$@"; do
    case "$arg" in
        --force) FORCE=true ;;
        --uninstall) UNINSTALL=true ;;
    esac
done

if [ -z "$SOURCE" ] || [ -z "$TARGET" ]; then
    echo "Usage: bash install.sh <source_dir> <target_project> [--force] [--uninstall]"
    echo ""
    echo "  source_dir:     Directory containing AGENTS.md + .ai-ready/ (engine output)"
    echo "  target_project: Root of the project to install into"
    echo ""
    echo "  --force:     Overwrite existing AGENTS.md (default: skip)"
    echo "  --uninstall: Remove all installed files (reads WHAT_WAS_ADDED.md)"
    exit 1
fi

# Resolve paths
SOURCE="$(cd "$SOURCE" && pwd)"
TARGET="$(cd "$TARGET" && pwd)"

# ─── Uninstall mode ───

if [ "$UNINSTALL" = true ]; then
    # Detect IDE to find correct manifest location
    if [ -d "$TARGET/.kiro" ]; then
        MANIFEST="$TARGET/.kiro/docs/ai-ready/WHAT_WAS_ADDED.md"
    else
        MANIFEST="$TARGET/.ai-ready/WHAT_WAS_ADDED.md"
    fi
    if [ ! -f "$MANIFEST" ]; then
        echo "❌ No WHAT_WAS_ADDED.md found at $MANIFEST — nothing to uninstall."
        exit 1
    fi
    echo "🗑️  Uninstalling AI-Ready artifacts from $TARGET..."
    # Parse manifest — lines starting with "- " are file paths
    grep '^- ' "$MANIFEST" | sed 's/^- //' | while read -r filepath; do
        full="$TARGET/$filepath"
        if [ -f "$full" ]; then
            rm "$full"
            echo "  removed: $filepath"
        fi
    done
    # Remove empty .ai-ready/ if it exists
    [ -d "$TARGET/.ai-ready" ] && rmdir "$TARGET/.ai-ready" 2>/dev/null && echo "  removed: .ai-ready/" || true
    rm -f "$MANIFEST" 2>/dev/null
    echo "✅ Uninstall complete."
    exit 0
fi

# ─── Validate source ───

if [ ! -f "$SOURCE/AGENTS.md" ]; then
    echo "❌ Source directory missing AGENTS.md: $SOURCE"
    echo "   Run the AI-Ready-Repo Engine first to generate output."
    exit 1
fi

if [ ! -d "$SOURCE/.ai-ready" ]; then
    echo "❌ Source directory missing .ai-ready/: $SOURCE"
    exit 1
fi

# ─── Detect IDE ───

IDE="claude-code"  # default

if [ -d "$TARGET/.kiro" ]; then
    IDE="kiro"
elif [ -d "$TARGET/.claude" ] || [ -f "$TARGET/CLAUDE.md" ]; then
    IDE="claude-code"
fi

echo "🏛️  AI-Ready-Repo Installer"
echo "   Source: $SOURCE"
echo "   Target: $TARGET"
echo "   IDE:    $IDE"
echo ""

# ─── Install — track everything for manifest ───

INSTALLED_FILES=()

install_file() {
    local src="$1"
    local dst="$2"
    local rel="${dst#$TARGET/}"

    if [ -f "$dst" ] && [ "$FORCE" != true ]; then
        echo "  skip: $rel (exists — use --force to overwrite)"
        return
    fi

    # Create parent directory
    mkdir -p "$(dirname "$dst")"
    cp "$src" "$dst"
    INSTALLED_FILES+=("$rel")
    echo "  ✓ $rel"
}

# ─── Claude Code layout ───

if [ "$IDE" = "claude-code" ]; then
    echo "Installing for Claude Code..."
    echo ""

    # AGENTS.md at project root
    install_file "$SOURCE/AGENTS.md" "$TARGET/AGENTS.md"

    # .ai-ready/ directory
    for f in "$SOURCE/.ai-ready/"*; do
        [ -f "$f" ] || continue
        filename="$(basename "$f")"
        install_file "$f" "$TARGET/.ai-ready/$filename"
    done

# ─── Kiro layout ───

elif [ "$IDE" = "kiro" ]; then
    echo "Installing for Kiro..."
    echo ""

    # AGENTS.md → .kiro/steering/ai-ready-context.md
    install_file "$SOURCE/AGENTS.md" "$TARGET/.kiro/steering/ai-ready-context.md"

    # .ai-ready/ files → .kiro/docs/ai-ready/
    for f in "$SOURCE/.ai-ready/"*; do
        [ -f "$f" ] || continue
        filename="$(basename "$f")"
        install_file "$f" "$TARGET/.kiro/docs/ai-ready/$filename"
    done
fi

# ─── Generate manifest (WHAT_WAS_ADDED.md) — only if files were installed ───

if [ ${#INSTALLED_FILES[@]} -eq 0 ]; then
    echo ""
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "✅ Installed 0 files for $IDE (all already present)"
    echo "   Use --force to overwrite existing files."
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    exit 0
fi

MANIFEST_PATH="$TARGET/.ai-ready/WHAT_WAS_ADDED.md"
if [ "$IDE" = "kiro" ]; then
    MANIFEST_PATH="$TARGET/.kiro/docs/ai-ready/WHAT_WAS_ADDED.md"
fi

mkdir -p "$(dirname "$MANIFEST_PATH")"
{
    echo "# What Was Added"
    echo ""
    echo "Installed by AI-Ready-Repo Engine on $(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "IDE: $IDE | Source: $SOURCE"
    echo ""
    echo "## Files (remove all to uninstall, or run: bash install.sh <src> <target> --uninstall)"
    echo ""
    for f in "${INSTALLED_FILES[@]}"; do
        echo "- $f"
    done
    echo "- $(echo "$MANIFEST_PATH" | sed "s|$TARGET/||")"
} > "$MANIFEST_PATH"

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "✅ Installed ${#INSTALLED_FILES[@]} files for $IDE"
echo ""
echo "   Your agent now has full project understanding."
echo "   See .ai-ready/REVIEW-REPORT.md for confidence levels and gaps."
echo ""
echo "   Manifest: $(echo "$MANIFEST_PATH" | sed "s|$TARGET/||")"
echo "   Uninstall: bash install.sh $SOURCE $TARGET --uninstall"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
