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
# Usage: ./hive/release.sh                       # the package every user installs
#        ./hive/release.sh v1.8.5                # override version
#        ./hive/release.sh --with-live-ddd [v]   # also seed this machine's own DDD (never publish)
#
# Two kinds of package:
#   - default: the package published with each release (CI) and the one every user's
#     Hive installs. Its seed is only what the repo ships: config-hive.json and the 5
#     public context files. It never reads this machine's workspace.
#   - --with-live-ddd: additionally seeds a fresh Hive with this machine's own SwarmAI
#     DDD (~/.swarm-ai/SwarmWS/Projects/SwarmAI), for the packager's own reference
#     Hives. That DDD is private workspace content: such a package goes only to the
#     packager's own S3 bucket, never to a GitHub release.
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

WITH_LIVE_DDD=0
VERSION_ARG=""
for arg in "$@"; do
    case "${arg}" in
        --with-live-ddd) WITH_LIVE_DDD=1 ;;
        -*) echo "[release] ERROR: unknown option ${arg}" >&2; exit 2 ;;
        *) VERSION_ARG="${arg}" ;;
    esac
done

if [ "${WITH_LIVE_DDD}" = "1" ] && [ -n "${CI:-}" ]; then
    echo "[release] ERROR: --with-live-ddd packages this machine's private workspace; it never runs in CI" >&2
    exit 2
fi

if [ -n "${VERSION_ARG}" ]; then
    VERSION="${VERSION_ARG#v}"  # Strip leading 'v' if present
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

# The backend files that ship are the ones the repo ships — the same rule the desktop
# installer uses (backend/scripts/shipped_skills.py): git-tracked plus untracked-but-not-
# ignored, node_modules/__pycache__ always skipped. Walking the directory instead put the
# checkout's gitignored files on every Hive: backend/.env (a placeholder JWT secret that
# stopped the per-boot random one), a stray data.db, test-leak junk, the s_internal-*
# skills and templates/ddd-gates (internal-only), and node_modules installed on macOS
# (darwin-only native binaries that cannot run on linux-arm64). A failing git ls-files
# aborts the build (set -e) rather than falling back to the walk.
echo "[release] Copying backend (the files the repo ships)..."
BACKEND_LIST="${STAGING}.backend-files"
(cd "${PROJECT_ROOT}" && python3 backend/scripts/shipped_skills.py backend) > "${BACKEND_LIST}.all"
grep -v -E '(^|/)(tests|\.pytest_cache|\.mypy_cache|\.hypothesis|\.venv)/|(^|/)(conftest\.py|\.DS_Store)$|\.pyc$' \
    "${BACKEND_LIST}.all" > "${BACKEND_LIST}" || true
rm -f "${BACKEND_LIST}.all"
if ! grep -qx 'backend/main.py' "${BACKEND_LIST}"; then
    echo "[release] ERROR: backend file list has no backend/main.py — aborting" >&2
    exit 1
fi
rsync -a --files-from="${BACKEND_LIST}" "${PROJECT_ROOT}/" "${STAGING}/"
echo "[release]   backend files: $(wc -l < "${BACKEND_LIST}" | tr -d ' ')"
rm -f "${BACKEND_LIST}"

echo "[release] Copying pre-built frontend..."
mkdir -p "${STAGING}/desktop/dist"
rsync -a "${PROJECT_ROOT}/desktop/dist/" "${STAGING}/desktop/dist/"

# The files of hive/ the repo ships (same rule as the backend above, so an ignored hive/.env
# stays out and a source tarball still works), minus the packaging scripts and any
# leftover hive/seed/ in this checkout: the seed is built below.
echo "[release] Copying hive config (the files the repo ships)..."
HIVE_LIST="${STAGING}.hive-files"
(cd "${PROJECT_ROOT}" && python3 backend/scripts/shipped_skills.py hive) \
    | grep -v -E '^hive/(release\.sh|update-hive\.sh)$|^hive/seed/' > "${HIVE_LIST}" || true
if ! grep -qx 'hive/swarmai-hive.sh' "${HIVE_LIST}"; then
    echo "[release] ERROR: hive file list has no hive/swarmai-hive.sh — aborting" >&2
    exit 1
fi
rsync -a --files-from="${HIVE_LIST}" "${PROJECT_ROOT}/" "${STAGING}/"
rm -f "${HIVE_LIST}"

# Write the version this package is built as (the argument, or the VERSION file when
# none is given). The update step checks that /health reports exactly this string,
# so copying the repo VERSION file made every package built with an explicit
# version fail that check and roll back.
echo "[release] Writing VERSION (${VERSION})..."
printf '%s\n' "${VERSION}" > "${STAGING}/VERSION"

# ---------------------------------------------------------------------------
# Hive seed material — read once by SwarmWorkspaceManager._seed_hive_from_package on a
# Hive's first boot (SWARMAI_MODE=hive): config-hive.json + the 5 PUBLIC context files,
# both from the repo. Private context files are never part of it (explicit whitelist
# below). With --with-live-ddd only, it also carries this machine's live SwarmAI DDD.
# Without a DDD in the seed, a fresh Hive gets the same 4-doc DDD scaffold a desktop
# install gets.
# ---------------------------------------------------------------------------
SEED="${STAGING}/hive/seed"
mkdir -p "${SEED}/context"

if [ "${WITH_LIVE_DDD}" = "1" ]; then
echo "[release] Building hive seed WITH this machine's live DDD (--with-live-ddd: do not publish)..."
mkdir -p "${SEED}/Projects"

# 1. Full SwarmAI DDD — sourced from the SwarmWS LIVE DDD, exclude runtime
#    artifacts + the code-intel binary (same boundary as .gitignore: knowledge
#    ships, build noise does not).
LIVE_DDD="${SWARM_DATA_DIR:-${HOME}/.swarm-ai}/SwarmWS/Projects/SwarmAI"

# CONTENT-FLOOR FAIL-FAST (before rsync): a Hive MUST ship the full, complete,
# non-torn DDD. Abort rather than ship a silently-degraded Hive. Checks:
#   (a) the live DDD dir exists,
#   (b) the canonical TECH.md is present AND non-empty (stub/empty detector — a
#       low sentinel, NOT a quality gate),
#   (c) no canonical doc is currently WRITE-LOCKED by cultivation. Detection is a
#       real held-flock try-lock (flock -n), NEVER lock-FILE presence: md_lock
#       never unlinks its <doc>.md.lock sidecar (ddd_cultivation.py), so the file
#       is on disk permanently — presence ≠ in-flight write. flock(1) is util-linux
#       and ABSENT on macOS, so this sub-check is command-v-gated and simply skips
#       on macOS (fail-open: the local build machine has no concurrent-writer race
#       worth bricking a build over; the guard still enforces (a)+(b) everywhere).
if [ ! -d "${LIVE_DDD}" ]; then
    echo "[release] ERROR: live SwarmAI DDD not found at ${LIVE_DDD}" >&2
    echo "[release]        --with-live-ddd sources the DDD from this machine's daemon workspace" >&2
    echo "[release]        (SWARM_DATA_DIR / ~/.swarm-ai/SwarmWS). Run it where that workspace" >&2
    echo "[release]        exists, or drop the option for the published package. Aborting." >&2
    exit 1
fi
# Stub/torn-content floor across ALL 4 canonical docs (not just TECH.md) — a
# low sentinel (present + non-empty + >=20 lines), NOT a quality gate.
for doc in PRODUCT TECH IMPROVEMENT PROJECT; do
    doc_md="${LIVE_DDD}/2-understanding/${doc}.md"
    if [ ! -s "${doc_md}" ]; then
        echo "[release] ERROR: ${doc_md} missing or empty — refusing to ship a stub/degraded DDD." >&2
        exit 1
    fi
    if [ "$(wc -l < "${doc_md}" | tr -d ' ')" -lt 20 ]; then
        echo "[release] ERROR: ${doc_md} is trivially small (<20 lines) — likely a stub. Aborting." >&2
        exit 1
    fi
done
if command -v flock >/dev/null 2>&1; then
    for doc in PRODUCT TECH IMPROVEMENT PROJECT; do
        lock="${LIVE_DDD}/2-understanding/${doc}.md.lock"
        [ -e "${lock}" ] || continue
        if ! flock -n "${lock}" true 2>/dev/null; then
            echo "[release] ERROR: ${doc}.md is WRITE-LOCKED (cultivation in-flight) — retry after it finishes." >&2
            exit 1
        fi
    done
fi

rsync -a \
    --exclude='.artifacts/' \
    --exclude='code_intel.db' \
    --exclude='code_intel.db-shm' \
    --exclude='code_intel.db-wal' \
    --exclude='code-intel.json' \
    --exclude='*-archive.md' \
    --exclude='*.md.lock' \
    --exclude='.ddd-usage.json' \
    --exclude='.session_cultivated.json' \
    --exclude='.DS_Store' \
    --exclude='__pycache__' \
    --exclude='*.pyc' \
    "${LIVE_DDD}/" "${SEED}/Projects/SwarmAI/"
echo "[release]   DDD files: $(find "${SEED}/Projects/SwarmAI" -type f | wc -l | tr -d ' ') (from live DDD: ${LIVE_DDD})"
else
    echo "[release] Building hive seed (config + public context; no workspace content)..."
fi

# 2. config-hive.json — GENERATED from backend/model_registry.py, never hand-written.
#
#    The model fields used to be a literal in this heredoc, which made the shell a
#    SECOND source of truth: it drifted behind the real flagship (it shipped 4-8
#    while the live default moved on) and its eval_judge_model named a model absent
#    from its own bedrock_model_map, so every Hive silently ran a different judge.
#    Deriving from the registry means a new model release edits ONE python file.
#
#    model_registry is deliberately STDLIB-ONLY so the SYSTEM python3 below can
#    import it — no venv is activated during packaging. (This is why the registry
#    is not in backend/config.py: that module imports pydantic_settings.)
#
#    Kept OUTSIDE the fail-fast if-block below on purpose: an import/generation
#    failure must surface as itself, not be reported as "invalid JSON".
echo "[release] generating config-hive.json from backend/model_registry.py"
#    Uses the SYSTEM interpreter explicitly (falling back to PATH python3 only
#    if absent): a venv/conda python3 on the build machine would have the
#    third-party packages installed and would therefore MASK a stdlib-only
#    violation in model_registry, which is the whole property this relies on.
SEED_PY="/usr/bin/python3"
[ -x "${SEED_PY}" ] || SEED_PY="python3"
if ! (cd "${PROJECT_ROOT}/backend" && "${SEED_PY}" - "${SEED}/config-hive.json" <<'HIVECFG_GEN'
import json, sys
from model_registry import (
    DEFAULT_JUDGE_MODEL,
    FLAGSHIP_MODEL,
    default_available_models,
    default_bedrock_model_map,
)

# A Hive boots on the registry's flagship. Non-private fields only — no
# owner_dm_channel, no machine-local sandbox paths.
config = {
    "use_bedrock": True,
    "aws_region": "us-east-1",
    "default_model": FLAGSHIP_MODEL,
    "available_models": default_available_models(),
    "bedrock_model_map": default_bedrock_model_map(),
    "thinking_mode": "adaptive",
    "thinking_effort": "high",
    # Judge pinned to a cheaper tier than production ON PURPOSE (it must not
    # drift in lockstep with the agent). Resolvable within the map above —
    # an unresolvable value is what silently swapped the judge before.
    "eval_judge_model": DEFAULT_JUDGE_MODEL,
}
assert config["eval_judge_model"] in config["bedrock_model_map"], (
    "seed eval_judge_model must be resolvable within the seed's own map"
)
assert config["default_model"] == config["available_models"][0], (
    "flagship must be first — settings.py auto-resets default_model to available_models[0]"
)
with open(sys.argv[1], "w", encoding="utf-8") as fh:
    json.dump(config, fh, indent=2)
    fh.write("\n")
HIVECFG_GEN
); then
    echo "[release] ERROR: failed to generate config-hive.json from model_registry — aborting" >&2
    exit 1
fi

# FAIL-FAST: a broken generator would ship invalid JSON; AppConfigManager would
# then silently fall back to the in-code DEFAULT_CONFIG — a healthy-looking Hive
# on unintended settings. Validate at package time so the build aborts instead.
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
# Supply closure (direction Y): auto-upload the tar to the account-suffixed S3
# bucket the provisioner actually reads (swarmai-hive-{acct4}-{region}), so
# provision/update find it instead of 404-ing on the (removed) GitHub hive tar.
#
# BEST-EFFORT + set-e-safe: every aws call is guarded (|| true / 2>/dev/null)
# so a missing cli / creds / network NEVER aborts the package build — the tar
# is already produced above. Region is resolved honestly (aws config -> env ->
# us-east-1) and PRINTED, so a future multi-region deploy sees the assumption.
# ---------------------------------------------------------------------------
UPLOADED_TO=""
if command -v aws >/dev/null 2>&1; then
    S3_REGION="$(aws configure get region 2>/dev/null || true)"
    S3_REGION="${S3_REGION:-${AWS_REGION:-${AWS_DEFAULT_REGION:-us-east-1}}}"
    S3_ACCT="$(aws sts get-caller-identity --query Account --output text 2>/dev/null || true)"
    if [ -n "${S3_ACCT}" ] && [ "${S3_ACCT}" != "None" ]; then
        S3_SUFFIX="${S3_ACCT: -4}"
        S3_BUCKET="swarmai-hive-${S3_SUFFIX}-${S3_REGION}"
        S3_KEY="v${VERSION}/${PACKAGE_NAME}.tar.gz"
        echo "[release] Uploading to s3://${S3_BUCKET}/${S3_KEY} (region ${S3_REGION})..."
        if aws s3 cp "${ARCHIVE}" "s3://${S3_BUCKET}/${S3_KEY}" --region "${S3_REGION}" 2>/dev/null \
           && aws s3 cp "${DIST_DIR}/checksums.txt" "s3://${S3_BUCKET}/v${VERSION}/checksums.txt" --region "${S3_REGION}" 2>/dev/null; then
            UPLOADED_TO="s3://${S3_BUCKET}/${S3_KEY}"
            echo "[release] Uploaded to ${UPLOADED_TO}"
        else
            echo "[release] WARN: S3 upload failed (AccessDenied/network?) — place the tar manually:"
            echo "[release]        aws s3 cp ${ARCHIVE} s3://${S3_BUCKET}/${S3_KEY} --region ${S3_REGION}"
        fi
    else
        echo "[release] WARN: no AWS credentials — skipping S3 upload (provision will 404 until the tar is uploaded)"
    fi
else
    echo "[release] WARN: aws cli absent — skipping S3 upload (provision will 404 until the tar is uploaded)"
fi

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
if [ "${WITH_LIVE_DDD}" = "1" ]; then
    echo "  Seed:     WITH this machine's live DDD (private: your own S3 only, never a GitHub release)"
else
    echo "  Seed:     config + public context only (safe to publish)"
fi
echo "  Checksum: $(cat checksums.txt)"
echo ""
if [ -n "${UPLOADED_TO}" ]; then
    echo "  S3:       ${UPLOADED_TO} (provision/update will find it here)"
else
    echo "  S3:       NOT uploaded (see WARN above) — provision/update will 404"
    echo "            until the tar is placed in swarmai-hive-{acct4}-{region}."
fi
echo ""
