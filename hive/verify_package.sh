#!/bin/bash
# SwarmAI Hive — package verification
#
# Validates a Hive tar.gz contains all required files and structure.
# Used by prod.sh release-all and GitHub Actions to gate releases.
#
# Usage: ./hive/verify_package.sh dist/swarmai-hive-v1.8.5-linux-arm64.tar.gz
#
# Exit codes:
#   0 — all checks passed
#   1 — verification failed (missing files or structure)

set -euo pipefail

if [ -z "${1:-}" ]; then
    echo "Usage: $0 <path-to-tar.gz>" >&2
    exit 1
fi

ARCHIVE="$1"

if [ ! -f "$ARCHIVE" ]; then
    echo "❌ Archive not found: $ARCHIVE" >&2
    exit 1
fi

echo "Verifying Hive package: $(basename "$ARCHIVE")"
echo "────────────────────────────────────────"

# List archive contents to a temp file (fast — no extraction needed)
# Strip the top-level directory prefix so patterns like "backend/" match
# regardless of the archive name (swarmai-hive-v1.8.4-linux-arm64/backend/...)
CONTENTS_FILE=$(mktemp)
trap 'rm -f "$CONTENTS_FILE"' EXIT
tar tzf "$ARCHIVE" | sed 's|^[^/]*/||' > "$CONTENTS_FILE"

PASS=0
FAIL=0

check() {
    local pattern="$1"
    local desc="$2"
    if grep -q "$pattern" "$CONTENTS_FILE"; then
        echo "  ✅ $desc"
        PASS=$((PASS + 1))
    else
        echo "  ❌ $desc — MISSING"
        FAIL=$((FAIL + 1))
    fi
}

# ── Structure checks ──────────────────────────────────────

echo ""
echo "Structure:"
check "^backend/"                      "backend/ directory"
check "^desktop/dist/"                 "desktop/dist/ directory"
check "^hive/"                         "hive/ directory"
check "^VERSION"                       "VERSION file"

echo ""
echo "Backend essentials:"
check "^backend/main.py"               "main.py (entry point)"
check "^backend/pyproject.toml"        "pyproject.toml (dependencies)"
check "^backend/core/"                 "core/ module"
check "^backend/database/"             "database/ module"
check "^backend/routers/"              "routers/ module"
check "^backend/skills/"               "skills/ directory"
check "^backend/context/"              "context/ templates"
check "^backend/hive/"                 "hive/ module (provisioner, etc.)"

echo ""
echo "Frontend:"
check "^desktop/dist/index.html"       "index.html (React SPA)"
check "^desktop/dist/assets/"          "assets/ (JS/CSS bundles)"

echo ""
echo "Hive config:"
check "^hive/swarmai-hive.sh"          "swarmai-hive.sh (entry point)"
check "^hive/swarmai-hive.service"     "swarmai-hive.service (systemd)"
check "^hive/Caddyfile"                "Caddyfile (reverse proxy)"

# ── Exclusion checks (should NOT be in package) ──────────

echo ""
echo "Exclusions:"

check_absent() {
    local pattern="$1"
    local desc="$2"
    if grep -q "$pattern" "$CONTENTS_FILE"; then
        echo "  ❌ $desc — SHOULD NOT be in package"
        FAIL=$((FAIL + 1))
    else
        echo "  ✅ $desc"
        PASS=$((PASS + 1))
    fi
}

check_absent "__pycache__/"            "No __pycache__"
check_absent "\.venv/"                 "No .venv"
check_absent "^tests/"                 "No tests/"
check_absent "\.pytest_cache/"         "No .pytest_cache"

# ── Summary ───────────────────────────────────────────────

TOTAL=$((PASS + FAIL))
echo ""
echo "────────────────────────────────────────"
if [ "$FAIL" -eq 0 ]; then
    echo "✅ All $TOTAL checks passed"
    exit 0
else
    echo "❌ $FAIL/$TOTAL checks FAILED"
    exit 1
fi
