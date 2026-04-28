#!/bin/bash
# SwarmAI Hive — release packaging script
#
# Builds a deployable tar.gz for EC2 from local pre-built artifacts.
# The package contains everything needed to run SwarmAI on EC2 —
# no git, npm, or node required on the target machine.
#
# Prerequisites:
#   - Frontend already built: cd desktop && npm run build
#   - Python backend source ready (no build step needed)
#
# Usage: ./hive/release.sh
#        ./hive/release.sh v1.8.5    # override version
#
# Output: dist/swarmai-hive-v{VERSION}-linux-arm64.tar.gz
#         dist/checksums.txt

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="${SCRIPT_DIR}/.."
DIST_DIR="${PROJECT_ROOT}/dist"
VERSION_FILE="${PROJECT_ROOT}/VERSION"

# ---------------------------------------------------------------------------
# Version
# ---------------------------------------------------------------------------

if [ -n "${1:-}" ]; then
    VERSION="${1#v}"  # Strip leading 'v' if present
else
    if [ -f "${VERSION_FILE}" ]; then
        VERSION="$(cat "${VERSION_FILE}" | tr -d '[:space:]')"
    else
        echo "[release] ERROR: No VERSION file and no version argument" >&2
        exit 1
    fi
fi

PACKAGE_NAME="swarmai-hive-v${VERSION}-linux-arm64"
ARCHIVE="${DIST_DIR}/${PACKAGE_NAME}.tar.gz"

echo "============================================="
echo "  SwarmAI Hive Release — v${VERSION}"
echo "============================================="

# ---------------------------------------------------------------------------
# Validate prerequisites
# ---------------------------------------------------------------------------

if [ ! -d "${PROJECT_ROOT}/desktop/dist" ]; then
    echo "[release] ERROR: Frontend not built. Run: cd desktop && npm run build" >&2
    exit 1
fi

if [ ! -f "${PROJECT_ROOT}/desktop/dist/index.html" ]; then
    echo "[release] ERROR: desktop/dist/index.html missing — build may have failed" >&2
    exit 1
fi

if [ ! -f "${PROJECT_ROOT}/backend/main.py" ]; then
    echo "[release] ERROR: backend/main.py not found — wrong directory?" >&2
    exit 1
fi

if [ ! -d "${PROJECT_ROOT}/hive" ]; then
    echo "[release] ERROR: hive/ directory not found" >&2
    exit 1
fi

# ---------------------------------------------------------------------------
# Package
# ---------------------------------------------------------------------------

mkdir -p "${DIST_DIR}"
STAGING="${DIST_DIR}/${PACKAGE_NAME}"
rm -rf "${STAGING}" "${ARCHIVE}"
mkdir -p "${STAGING}"

echo "[release] Copying backend..."
rsync -a \
    --exclude='.venv' \
    --exclude='__pycache__' \
    --exclude='*.pyc' \
    --exclude='.pytest_cache' \
    --exclude='tests/' \
    --exclude='.mypy_cache' \
    "${PROJECT_ROOT}/backend/" "${STAGING}/backend/"

echo "[release] Copying pre-built frontend..."
rsync -a "${PROJECT_ROOT}/desktop/dist/" "${STAGING}/desktop/dist/"

echo "[release] Copying hive config..."
rsync -a \
    --exclude='release.sh' \
    --exclude='update-hive.sh' \
    "${PROJECT_ROOT}/hive/" "${STAGING}/hive/"

echo "[release] Copying VERSION..."
cp "${VERSION_FILE}" "${STAGING}/VERSION"

echo "[release] Copying pyproject.toml (for pip install)..."
cp "${PROJECT_ROOT}/pyproject.toml" "${STAGING}/pyproject.toml" 2>/dev/null || true

# ---------------------------------------------------------------------------
# Create tar.gz
# ---------------------------------------------------------------------------

echo "[release] Creating archive..."
cd "${DIST_DIR}"
tar czf "${PACKAGE_NAME}.tar.gz" "${PACKAGE_NAME}/"
rm -rf "${STAGING}"

# ---------------------------------------------------------------------------
# Checksums
# ---------------------------------------------------------------------------

echo "[release] Generating checksums..."
cd "${DIST_DIR}"
shasum -a 256 "${PACKAGE_NAME}.tar.gz" > checksums.txt

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

ARCHIVE_SIZE=$(du -h "${ARCHIVE}" | cut -f1)
echo ""
echo "============================================="
echo "  Release package ready"
echo "============================================="
echo ""
echo "  Archive:  ${ARCHIVE}"
echo "  Size:     ${ARCHIVE_SIZE}"
echo "  Version:  ${VERSION}"
echo "  Checksum: $(cat checksums.txt)"
echo ""
echo "  Upload to GitHub Release:"
echo "    gh release create v${VERSION} ${ARCHIVE} ${DIST_DIR}/checksums.txt"
echo ""
