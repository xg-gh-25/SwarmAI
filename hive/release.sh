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

if [ ! -f "${PROJECT_ROOT}/desktop/dist/index.html" ]; then
    echo "[release] Frontend not built — building now..."
    cd "${PROJECT_ROOT}/desktop"
    npm run build
    cd "${PROJECT_ROOT}"
    if [ ! -f "${PROJECT_ROOT}/desktop/dist/index.html" ]; then
        echo "[release] ERROR: Frontend build failed — desktop/dist/index.html still missing" >&2
        exit 1
    fi
    echo "[release] Frontend built successfully"
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
    --exclude='.DS_Store' \
    --exclude='.hypothesis' \
    --exclude='conftest.py' \
    "${PROJECT_ROOT}/backend/" "${STAGING}/backend/"

echo "[release] Copying pre-built frontend..."
mkdir -p "${STAGING}/desktop/dist"
rsync -a "${PROJECT_ROOT}/desktop/dist/" "${STAGING}/desktop/dist/"

echo "[release] Copying hive config..."
rsync -a \
    --exclude='release.sh' \
    --exclude='update-hive.sh' \
    "${PROJECT_ROOT}/hive/" "${STAGING}/hive/"

echo "[release] Copying VERSION..."
cp "${VERSION_FILE}" "${STAGING}/VERSION"

# ---------------------------------------------------------------------------
# Hive seed material (run_ca7f92c1) — a fresh Hive is a SHARED reference
# instance, so it ships the COMPLETE SwarmAI sample DDD + a model-4-8 config +
# the 5 PUBLIC context files. SwarmWorkspaceManager._seed_hive_from_package
# reads this dir on first boot (SWARMAI_MODE=hive). Runtime artifacts + the
# private context files are DELIBERATELY excluded (see below).
# ---------------------------------------------------------------------------
echo "[release] Building hive seed (full DDD + config-4-8 + public context)..."
SEED="${STAGING}/hive/seed"
mkdir -p "${SEED}/Projects" "${SEED}/context"

# 1. Full SwarmAI DDD — exclude runtime artifacts + the code-intel binary
#    (same boundary as .gitignore: knowledge ships, build noise does not).
# FAIL-FAST: a Hive MUST ship the full DDD. A silent 4-stub fallback here (the old
# WARNING-and-continue) ships a degraded Hive that looks healthy — abort instead so
# the build machine's missing/gitignored Projects/SwarmAI is caught at package time.
if [ ! -d "${PROJECT_ROOT}/Projects/SwarmAI" ]; then
    echo "[release] ERROR: Projects/SwarmAI not found at ${PROJECT_ROOT}/Projects/SwarmAI" >&2
    echo "[release]        A Hive must ship the full SwarmAI DDD sample. Ensure the source" >&2
    echo "[release]        repo includes Projects/SwarmAI (it's git-tracked, .gitignore keeps" >&2
    echo "[release]        only SwarmAI public). Aborting rather than shipping a 4-stub Hive." >&2
    exit 1
fi
rsync -a \
    --exclude='.artifacts/' \
    --exclude='code_intel.db' \
    --exclude='code_intel.db-shm' \
    --exclude='code_intel.db-wal' \
    --exclude='.*.md.lock' \
    --exclude='.ddd-usage.json' \
    --exclude='.session_cultivated.json' \
    --exclude='.DS_Store' \
    "${PROJECT_ROOT}/Projects/SwarmAI/" "${SEED}/Projects/SwarmAI/"
echo "[release]   DDD files: $(find "${SEED}/Projects/SwarmAI" -type f | wc -l | tr -d ' ')"

# 2. config-hive.json — model 4-8 (a Hive boots on the current flagship, not the
#    code DEFAULT_CONFIG 4-6). Non-private fields only; no owner_dm_channel, no
#    machine-local sandbox paths.
cat > "${SEED}/config-hive.json" <<'HIVECFG'
{
  "use_bedrock": true,
  "aws_region": "us-east-1",
  "default_model": "claude-opus-4-8",
  "available_models": ["claude-opus-4-8", "claude-opus-4-6", "claude-sonnet-4-6"],
  "bedrock_model_map": {
    "claude-opus-4-8": "us.anthropic.claude-opus-4-8",
    "claude-opus-4-6": "us.anthropic.claude-opus-4-6-v1",
    "claude-sonnet-4-6": "us.anthropic.claude-sonnet-4-6"
  },
  "thinking_mode": "adaptive",
  "thinking_effort": "high",
  "eval_judge_model": "claude-sonnet-4-20250514"
}
HIVECFG

# FAIL-FAST: a typo in the heredoc above would ship invalid JSON; AppConfigManager
# would then silently fall back to DEFAULT_CONFIG (model 4-6) — a healthy-looking
# Hive on the WRONG model. Validate at package time so the build aborts instead.
if ! python3 -c "import json,sys; json.load(open('${SEED}/config-hive.json'))" 2>/dev/null; then
    echo "[release] ERROR: config-hive.json is not valid JSON — aborting (would boot Hive on wrong model)" >&2
    exit 1
fi

# 3. PUBLIC context — EXPLICIT WHITELIST (never a glob): only the 5 system-owned
#    files. The private six (MEMORY/USER/EVOLUTION/STEERING/TOOLS/KNOWLEDGE) must
#    NEVER ship to a shared Hive. Mirrors _PUBLIC_CONTEXT_SEED in the manager.
for f in SWARMAI.md IDENTITY.md SOUL.md AGENT.md SELF.md; do
    if [ -f "${PROJECT_ROOT}/backend/context/${f}" ]; then
        cp "${PROJECT_ROOT}/backend/context/${f}" "${SEED}/context/${f}"
    else
        echo "[release]   WARNING: public context ${f} not found in backend/context/"
    fi
done
# FAIL-FAST: all 5 public context files must ship. A missing one → a Hive booting
# without part of its cognition framework (the "变智障" failure class). Abort.
_CTX_COUNT=$(ls "${SEED}/context" 2>/dev/null | wc -l | tr -d ' ')
if [ "${_CTX_COUNT}" -ne 5 ]; then
    echo "[release] ERROR: expected 5 public context files, found ${_CTX_COUNT} — aborting" >&2
    exit 1
fi
echo "[release]   public context: ${_CTX_COUNT} files"

# pyproject.toml lives inside backend/ — already copied by rsync above.
# No root-level copy needed (setup.sh runs `pip install -e .` from backend/).

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
