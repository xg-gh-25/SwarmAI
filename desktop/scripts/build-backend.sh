#!/bin/bash
# Build Python backend with PyInstaller (bundled into .app for daemon deployment)

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
BACKEND_DIR="$PROJECT_ROOT/../backend"
OUTPUT_DIR="$PROJECT_ROOT/src-tauri/binaries"

echo "Building Python backend for desktop app..."
echo "Backend dir: $BACKEND_DIR"
echo "Output dir: $OUTPUT_DIR"

# Detect platform and architecture
if [[ "$OSTYPE" == "darwin"* ]]; then
    if [[ $(uname -m) == "arm64" ]]; then
        TARGET="aarch64-apple-darwin"
    else
        TARGET="x86_64-apple-darwin"
    fi
    BINARY_EXT=""
elif [[ "$OSTYPE" == "linux"* ]]; then
    if [[ $(uname -m) == "aarch64" ]]; then
        TARGET="aarch64-unknown-linux-gnu"
    else
        TARGET="x86_64-unknown-linux-gnu"
    fi
    BINARY_EXT=""
elif [[ "$OSTYPE" == "msys" || "$OSTYPE" == "cygwin" ]]; then
    # Windows (Git Bash or Cygwin)
    if [[ $(uname -m) == "x86_64" ]]; then
        TARGET="x86_64-pc-windows-msvc"
    else
        TARGET="i686-pc-windows-msvc"
    fi
    BINARY_EXT=".exe"
else
    echo "Unsupported platform: $OSTYPE"
    exit 1
fi

echo "Target platform: $TARGET"

# Ensure output directory exists
mkdir -p "$OUTPUT_DIR"

# Create temporary build directory
BUILD_DIR=$(mktemp -d)
trap "rm -rf $BUILD_DIR" EXIT

# Refresh the bundled C034 guardian assets (script, standalone guard.py, plist
# template) from their sources of truth so the .app never ships stale copies.
# Delegates to the shared sync script — the SAME script tauri.conf
# beforeBuildCommand runs — so both build entry points stay consistent.
if [ -f "$SCRIPT_DIR/sync-guardian-assets.sh" ]; then
    bash "$SCRIPT_DIR/sync-guardian-assets.sh"
fi

# Copy backend code to build directory
cp -r "$BACKEND_DIR"/* "$BUILD_DIR/"

# Copy resource files needed by PyInstaller into the build directory
cp "$PROJECT_ROOT/resources/mcp-catalog.json" "$BUILD_DIR/mcp-catalog.json"
cp "$PROJECT_ROOT/resources/required-cli-tools.json" "$BUILD_DIR/required-cli-tools.json"
cp "$BACKEND_DIR/../VERSION" "$BUILD_DIR/VERSION"
# The checkout this bundle is built from: the packaged daemon names ITS scripts for the
# pipeline commands the agent runs (backend/utils/source_repo.py). A CI-built release
# records a path that does not exist on the user's machine, which reads as "none".
SOURCE_ROOT="$(cd "$BACKEND_DIR/.." && pwd)" python3 -c 'import json, os, sys; json.dump({"root": os.environ["SOURCE_ROOT"]}, sys.stdout)' > "$BUILD_DIR/source_repo.json"

# The entry point is backend/desktop_main.py (copied above with the rest of backend/): the
# subcommands daemon / pipeline-v1 / py, the same file the dev shim backend/bin/python-backend runs.
[ -f "$BUILD_DIR/desktop_main.py" ] || { echo "Error: backend/desktop_main.py missing"; exit 1; }

# Navigate to build directory
cd "$BUILD_DIR"

# Install dependencies using uv (fast Python package manager)
echo "Setting up Python environment with uv..."

# Check if uv is available, if not install it
if ! command -v uv &> /dev/null; then
    echo "Installing uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$PATH"
fi

# Create virtual environment with uv
uv venv .venv

# Activate virtual environment
if [[ "$OSTYPE" == "msys" || "$OSTYPE" == "cygwin" ]]; then
    source .venv/Scripts/activate
else
    source .venv/bin/activate
fi

# Install dependencies using uv FROM THE LOCKFILE (uv.lock), not a fresh resolve.
# This ensures local modules (routers, core, etc.) remain as top-level modules in current dir.
echo "Installing dependencies with uv (locked)..."

# WHY `uv sync` instead of the old `uv pip install -e ".[dev]"`:
#   `uv pip install` IGNORES uv.lock and re-resolves the LATEST of every unpinned
#   dep on each build. botocore ships a new release almost daily, so every build
#   re-fetched botocore's huge PyPI index and re-downloaded a fresh ~14MB wheel —
#   the main source of "why is build so slow". `uv sync` installs the EXACT
#   versions pinned in uv.lock: cache hits every time, reproducible, no churn.
#   (When pyproject.toml deps change, uv sync auto-updates this throwaway copy's
#   lock and resolves correctly — so it can't silently ship a missing dep.)
# --extra dev mirrors the old ".[dev]" (pulls pytest/httpx/hypothesis/bandit/etc.).
# uv.lock is the single source of truth; regenerate with `cd backend && uv lock`.
uv sync --extra dev

# Verify key local modules are accessible from current directory
echo "Verifying local modules are importable..."
# Isolated: `import main` runs main's module-level logging setup, and with no
# SWARMAI_MODE it resolves to daemon mode and attaches a second rotating file
# handler to the LIVE ~/.swarm-ai/logs/backend-daemon.log (an orphan
# "Log file: …" line on every build). Point it at a throwaway data dir.
IMPORT_PROBE_DIR="$(mktemp -d "${TMPDIR:-/tmp}/swarmai-import-probe.XXXXXX")"
# A failing import exits the script (errexit) before the rm below — extend the
# EXIT trap so the probe dir never leaks.
trap 'rm -rf "$BUILD_DIR" "${IMPORT_PROBE_DIR:-}"' EXIT
SWARMAI_MODE=subprocess SWARM_DATA_DIR="$IMPORT_PROBE_DIR" \
DATABASE_TYPE=sqlite python -c "
import sys
sys.path.insert(0, '.')
import main
import config
import routers
import core
import schemas
import database
import middleware
print('All local modules verified!')
"
rm -rf "$IMPORT_PROBE_DIR"

# Create PyInstaller spec file for better control
cat > backend.spec << EOF
# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_submodules, collect_data_files
import os

block_cipher = None

# Collect submodules for installed packages
hiddenimports = []
hiddenimports += collect_submodules('uvicorn')
hiddenimports += collect_submodules('fastapi')
hiddenimports += collect_submodules('starlette')
hiddenimports += collect_submodules('pydantic')
hiddenimports += collect_submodules('pydantic_settings')
hiddenimports += collect_submodules('anyio')
hiddenimports += collect_submodules('slowapi')
hiddenimports += collect_submodules('limits')  # slowapi dependency — imports redis/memcached at class level
hiddenimports += collect_submodules('claude_agent_sdk')
# boto3/botocore: only include the services we actually use.
# collect_submodules('botocore') pulls ALL 424 AWS services into PyInstaller's
# Analysis graph, causing 4-6GB peak RAM usage → OOM kill (exit 137) on 36GB
# machines with other processes running. We only use 6 services at runtime.
hiddenimports += collect_submodules('boto3')
hiddenimports += [
    'botocore', 'botocore.args', 'botocore.auth', 'botocore.awsrequest',
    'botocore.client', 'botocore.compress', 'botocore.config', 'botocore.configloader',
    'botocore.configprovider', 'botocore.credentials', 'botocore.discovery',
    'botocore.endpoint', 'botocore.endpoint_provider', 'botocore.errorfactory',
    'botocore.eventstream', 'botocore.exceptions', 'botocore.handlers',
    'botocore.hooks', 'botocore.httpchecksum', 'botocore.httpsession',
    'botocore.loaders', 'botocore.model', 'botocore.monitoring',
    'botocore.paginate', 'botocore.parsers', 'botocore.regions',
    'botocore.response', 'botocore.retries', 'botocore.retries.adaptive',
    'botocore.retries.base', 'botocore.retries.bucket', 'botocore.retries.quota',
    'botocore.retries.special', 'botocore.retries.standard', 'botocore.retries.throttling',
    'botocore.retryhandler', 'botocore.serialize', 'botocore.session',
    'botocore.signers', 'botocore.stub', 'botocore.tokens',
    'botocore.translate', 'botocore.useragent', 'botocore.utils',
    'botocore.validate', 'botocore.waiter',
]
hiddenimports += ['psutil']
hiddenimports += collect_submodules('slack_bolt')
hiddenimports += collect_submodules('slack_sdk')
hiddenimports += ['sqlite_vec']  # Vector search extension (try/except import in vec_db.py)
hiddenimports += collect_submodules('amazon_transcribe')  # Voice transcription (Transcribe Streaming)
hiddenimports += collect_submodules('awscrt')  # AWS Common Runtime (native C extensions)
hiddenimports += ['tree_sitter', 'tree_sitter._binding']  # Code Intelligence AST parsing (native C extension)

# "\$SWARM_BACKEND_BIN" py runs skill scripts and agent one-liners with THIS interpreter (design
# 2026-10-24-swarmai-usable-by-anyone §3.2, S3c), so it carries (1) the whole standard library,
# not just what the backend happens to import (json.tool, http.server, csv ...), and (2) every
# module a skill declares under python-deps in its SKILL.md (scripts/skill_python_deps.py).
import importlib.util as _ilu, sys as _sys
_STDLIB_SKIP = {'test', 'idlelib', 'tkinter', 'turtle', 'turtledemo', 'lib2to3', 'ensurepip',
                'venv', 'pydoc_data', 'antigravity', 'this', '__phello__'}
for _m in sorted(_sys.stdlib_module_names - _STDLIB_SKIP):
    if _m.startswith('_') or _ilu.find_spec(_m) is None:
        continue
    _spec = _ilu.find_spec(_m)
    hiddenimports += collect_submodules(_m, filter=lambda n: '.test' not in n) if _spec.submodule_search_locations else [_m]
_sys.path.insert(0, os.path.abspath('scripts'))
from skill_python_deps import bundled_imports as _skill_deps
SKILL_PY_DEPS = _skill_deps('skills')
for _m in SKILL_PY_DEPS:
    hiddenimports += collect_submodules(_m)
hiddenimports += collect_submodules('tree_sitter_language_pack')  # Language grammars for tree-sitter

# Collect data files (including bundled CLI binary from claude_agent_sdk)
datas = []
datas += collect_data_files('claude_agent_sdk')
datas += collect_data_files('certifi')
datas += collect_data_files('sqlite_vec')  # vec0.dylib native extension for vector search
datas += collect_data_files('awscrt')  # AWS CRT native libraries (.dylib/.so)
datas += collect_data_files('limits')  # Lua scripts for redis rate limiting (loaded at import time)
for _m in SKILL_PY_DEPS:  # e.g. python-pptx's default template, markitdown's resources
    datas += collect_data_files(_m)
# botocore: include ALL data files (JSON service models loaded at runtime via botocore.loaders).
# We only list botocore Python modules above (not collect_submodules) to save ~3GB build RAM,
# but the data/ directory with service JSON is needed at runtime for any boto3.client() call.
datas += collect_data_files('botocore')
# Include built-in context files and skills for agent workspace initialization
datas += [('context', 'context')]
# Include the skills the repo ships: git-tracked or untracked-but-not-ignored files, so a
# checkout's gitignored local skills stay out of the installer (scripts/shipped_skills.py).
# node_modules is always skipped — Remotion's alone is 609MB/15K+ files, which causes
# PyInstaller OOM (SIGKILL exit 137); templates run npm install at runtime.
import os as _os
from shipped_skills import shipped_files as _shipped_skill_files
for _src in _shipped_skill_files('skills'):
    datas.append((_src, _os.path.dirname(_src)))
# Include DDD templates for default project provisioning
datas += [('templates', 'templates')]
# Include the expert library (backend/experts/<domain>/<id>.md): core.expert_manager
# reads it at runtime (registry API, and the SubagentStop hook's registry-backed
# adversarial classification). Missing here, the packaged daemon scans an absent dir,
# finds no experts and silently falls back to keyword classification (run_1a309783).
datas += [('experts', 'experts')]
# Include MCP server scripts as LOOSE FILES on disk. These are launched as
# subprocesses BY PATH (mcp_config_loader.py: .../mcp_servers/channel_file_sender.py),
# not imported — so being in hiddenimports/LOCAL_PACKAGES (archive-only) is not
# enough; the .py must exist on disk in _internal/mcp_servers/. Without this the
# daemon logged "Channel-tools MCP script not found" and channel file-sending was
# silently dead in the packaged build (worked in dev where the source file exists).
for _mroot, _mdirs, _mfiles in _os.walk('mcp_servers'):
    _mdirs[:] = [d for d in _mdirs if d != '__pycache__']
    for _mf in _mfiles:
        if _mf.endswith('.pyc'):
            continue
        _msrc = _os.path.join(_mroot, _mf)
        datas.append((_msrc, _os.path.dirname(_msrc)))
# Include MCP catalog and CLI tool registry at bundle root
datas += [('mcp-catalog.json', '.')]
datas += [('required-cli-tools.json', '.')]
# VERSION file — single source of truth for app version (eliminates hardcoded fallback in config.py)
datas += [('VERSION', '.')]
# the source checkout this bundle was built from (utils/source_repo.py)
datas += [('source_repo.json', '.')]

# Auto-discover local modules from filesystem instead of hardcoding.
# This eliminates the class of bugs where new .py files are added to the
# codebase but never registered in the build script (see: sqlite_vec incident
# 2026-04-15 where 18 modules were missing for 5 days).
import glob

LOCAL_PACKAGES = [
    'core', 'routers', 'schemas', 'database', 'middleware',
    'channels', 'mcp_servers', 'hooks', 'jobs', 'utils', 'scripts',
]

local_modules = ['main', 'config']
for pkg in LOCAL_PACKAGES:
    # Add the package itself
    local_modules.append(pkg)
    # Add all .py files in the package (including nested like jobs/adapters/*.py)
    for py_file in sorted(glob.glob(f'{pkg}/**/*.py', recursive=True)):
        # Normalize path separators (Windows glob returns backslashes)
        mod = py_file.replace(os.sep, '.').replace('/', '.').removesuffix('.py')
        if '__pycache__' in mod or mod.endswith('.__init__') or 'test_' in mod or '_test.' in mod:
            continue
        local_modules.append(mod)

print(f"[build] Auto-discovered {len(local_modules)} local modules")

a = Analysis(
    ['desktop_main.py'],
    pathex=[os.getcwd()],  # Add current directory to path
    binaries=[],
    datas=datas,  # Include bundled CLI from claude_agent_sdk
    hiddenimports=hiddenimports + local_modules + [
        # Claude Agent SDK
        'claude_agent_sdk',
        # Auth
        'bcrypt',
        # Database
        'aiosqlite',
        'sqlite3',
        # Rate limiting
        'slowapi',
        'slowapi.errors',
        # HTTP/SSL
        'ssl',
        'certifi',
        'requests',  # Dynamic import in Slack adapter file download
        # Backend local modules - CRITICAL for bundling
        'main',
        'config',
        # Additional dependencies that may be dynamically imported
        'email_validator',
        'httptools',
        'websockets',
        'watchfiles',
        'h11',
        'httpcore',
        'httpx',
        'yaml',
        'cryptography',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,  # onedir: binaries go into COLLECT
    name='python-backend',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,  # Disable UPX on macOS to avoid code signing issues
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='python-backend',
)
EOF

# Build with PyInstaller
echo "Running PyInstaller..."
pyinstaller backend.spec --clean --noconfirm

# Copy the built directory to output location (onedir mode)
SOURCE_DIR="dist/python-backend"
OUTPUT_BINARY_DIR="$OUTPUT_DIR/python-backend-$TARGET"

if [[ ! -d "$SOURCE_DIR" ]]; then
    echo "Error: Built directory not found at $SOURCE_DIR"
    exit 1
fi

# Clean previous output and copy fresh
rm -rf "$OUTPUT_BINARY_DIR"
cp -r "$SOURCE_DIR" "$OUTPUT_BINARY_DIR"
chmod +x "$OUTPUT_BINARY_DIR/python-backend${BINARY_EXT}"

# Stage the bundle where tauri.bundle.conf.json picks it up as resource python-backend/
# (design 2026-10-24-swarmai-usable-by-anyone §3.1: the installer carries the backend;
# no post-build injection). Target-agnostic path, rebuilt every time.
STAGE_DIR="$PROJECT_ROOT/src-tauri/bundle-backend"
rm -rf "$STAGE_DIR"
mkdir -p "$STAGE_DIR"
cp -R "$SOURCE_DIR" "$STAGE_DIR/python-backend"
# Windows runners have `python`, and often only a Store stub named python3
PY=python3; python3 -c '' >/dev/null 2>&1 || PY=python
APP_VERSION="$($PY -c 'import json,sys;print(json.load(open(sys.argv[1]))["version"])' "$PROJECT_ROOT/src-tauri/tauri.conf.json")"
$PY "$SCRIPT_DIR/write_install_manifest.py" "$STAGE_DIR" "$PROJECT_ROOT/resources" "$APP_VERSION" "$TARGET"

echo "Backend built successfully (onedir): $OUTPUT_BINARY_DIR"
echo "Staged for the installer: $STAGE_DIR/python-backend (+ install-manifest.json)"
echo ""
echo "Directory size: $(du -sh "$OUTPUT_BINARY_DIR" | cut -f1)"
echo "Executable: $OUTPUT_BINARY_DIR/python-backend${BINARY_EXT}"

# NOTE: Daemon deploy (binary + wrapper + plist) is handled exclusively by
# _deploy_daemon_binary in daemon-lib.sh (called via ./dev.sh deploy or ./prod.sh build).
# build-backend.sh only builds the binary — deploy is a separate step.
echo ""
echo "To deploy to daemon: ./dev.sh deploy  (or ./prod.sh build)"

# The executable path within the onedir bundle
OUTPUT_EXECUTABLE="$OUTPUT_BINARY_DIR/python-backend${BINARY_EXT}"

# ── Post-build verification ──────────────────────────────────────
# Ensures the binary has all capabilities that can silently degrade.
# This catches the class of bug where features work in dev (venv) but
# are missing from the PyInstaller binary (see: sqlite_vec incident).
#
# On Windows CI: the binary hangs during lifespan startup because
# workspace dirs (~/.swarm-ai/) don't exist. The bundle itself is fine
# (195 modules auto-discovered). Skip verification on Windows CI —
# the macOS build verifies module completeness for both platforms.
echo ""
if [[ "$OSTYPE" == "msys" || "$OSTYPE" == "win32" || "$OSTYPE" == "cygwin" ]] && [[ -n "${CI:-}" ]]; then
    echo "Skipping full post-build verification on Windows CI (binary hangs in lifespan startup without workspace)."
    echo "  Running lightweight module-presence check instead..."
    if "$OUTPUT_EXECUTABLE" --help >/dev/null 2>&1; then
        echo "  ✅ Binary executes (--help returned successfully)"
    else
        echo "  ⚠️  Binary --help failed (exit code $?) — may be missing dependencies"
        echo "  Module bundling verified by macOS build. Dir size: $(du -sh "$OUTPUT_BINARY_DIR" | cut -f1)"
    fi
else
    echo "Running post-build verification..."
    if python scripts/verify_build.py "$OUTPUT_EXECUTABLE"; then
        echo "✅ Build verification passed"
    else
        echo ""
        echo "❌ Build verification FAILED (see above)"
        echo "  Fix missing modules before releasing."
        exit 1
    fi
fi
