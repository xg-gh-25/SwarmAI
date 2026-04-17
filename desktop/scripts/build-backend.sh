#!/bin/bash
# Build Python backend with PyInstaller for Tauri sidecar

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

# Copy backend code to build directory
cp -r "$BACKEND_DIR"/* "$BUILD_DIR/"

# Copy resource files needed by PyInstaller into the build directory
cp "$PROJECT_ROOT/resources/mcp-catalog.json" "$BUILD_DIR/mcp-catalog.json"
cp "$PROJECT_ROOT/resources/required-cli-tools.json" "$BUILD_DIR/required-cli-tools.json"

# Create entry point script for PyInstaller
cat > "$BUILD_DIR/desktop_main.py" << 'EOF'
#!/usr/bin/env python3
"""Desktop application entry point for the backend server."""
import sys
import os
import platform
import traceback
from pathlib import Path
from datetime import datetime

def get_log_dir() -> Path:
    """Get the log directory based on platform."""
    if platform.system() == "Darwin":
        log_dir = Path.home() / "Library" / "Application Support" / "SwarmAI" / "logs"
    elif platform.system() == "Windows":
        log_dir = Path.home() / "AppData" / "Local" / "SwarmAI" / "logs"
    else:
        log_dir = Path.home() / ".local" / "share" / "swarmai" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir

def write_startup_log(message: str):
    """Write a startup log message before main logging is initialized."""
    try:
        log_file = get_log_dir() / "startup.log"
        timestamp = datetime.now().isoformat()
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(f"[{timestamp}] {message}\n")
        # Also print to stdout for Tauri to capture
        print(f"[STARTUP] {message}", flush=True)
    except Exception as e:
        print(f"[STARTUP ERROR] Failed to write log: {e}", flush=True)

def main():
    write_startup_log("Desktop backend starting...")
    write_startup_log(f"Python version: {sys.version}")
    write_startup_log(f"Platform: {platform.system()} {platform.machine()}")
    write_startup_log(f"Executable: {sys.executable}")
    write_startup_log(f"Working directory: {os.getcwd()}")

    # Set environment for desktop mode BEFORE any imports
    os.environ.setdefault("DATABASE_TYPE", "sqlite")
    write_startup_log(f"DATABASE_TYPE: {os.environ.get('DATABASE_TYPE')}")

    try:
        write_startup_log("Importing argparse...")
        import argparse

        write_startup_log("Importing asyncio...")
        import asyncio

        write_startup_log("Importing uvicorn...")
        import uvicorn

        write_startup_log("Importing main app...")
        from main import app
        write_startup_log("All imports successful!")

    except Exception as e:
        error_msg = f"Import error: {type(e).__name__}: {e}\n{traceback.format_exc()}"
        write_startup_log(error_msg)
        sys.exit(1)

    parser = argparse.ArgumentParser(description="Claude Agent Platform Backend")
    parser.add_argument("--port", type=int, default=8000, help="Port to run on")
    parser.add_argument("--host", type=str, default="127.0.0.1", help="Host to bind to")
    # parse_known_args: ignore unknown flags so the binary never crash-loops
    # if the daemon script or Tauri passes an unexpected argument.
    # See: 2026-04-02 --log-level crash loop (commit 6829745).
    args, _unknown = parser.parse_known_args()
    if _unknown:
        write_startup_log(f"Ignoring unknown arguments: {_unknown}")
        print(f"[python-backend] Ignoring unknown arguments: {_unknown}", flush=True)

    write_startup_log(f"Starting server on {args.host}:{args.port}")
    print(f"Starting backend server on {args.host}:{args.port}", flush=True)

    try:
        # Configure uvicorn for PyInstaller compatibility
        write_startup_log(f"Creating uvicorn config for {args.host}:{args.port}...")
        config = uvicorn.Config(
            app,
            host=args.host,
            port=args.port,
            log_level="info",
            loop="asyncio",  # Use asyncio loop explicitly
            reload=False,    # Disable reload in bundled app
            workers=1,       # Single worker for bundled app
        )
        write_startup_log("Uvicorn config created successfully")

        server = uvicorn.Server(config)
        write_startup_log("Uvicorn server instance created")

        # Run the server
        write_startup_log("Starting uvicorn server.serve()...")
        asyncio.run(server.serve())
        write_startup_log("Server stopped normally")
    except Exception as e:
        error_msg = f"Server error: {type(e).__name__}: {e}\n{traceback.format_exc()}"
        write_startup_log(error_msg)
        sys.exit(1)

if __name__ == "__main__":
    main()
EOF

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

# Install dependencies using uv
# This ensures local modules (routers, core, etc.) remain as top-level modules in current dir
echo "Installing dependencies with uv..."

# Install ALL dependencies from pyproject.toml (main + dev).
# Previously this was a hardcoded list that drifted from pyproject.toml,
# causing deps like pytest-xdist and psutil to be declared but never installed.
# Single source of truth: pyproject.toml [dependencies] + [project.optional-dependencies.dev]
uv pip install -e ".[dev]"

# Verify key local modules are accessible from current directory
echo "Verifying local modules are importable..."
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
hiddenimports += collect_submodules('claude_agent_sdk')
hiddenimports += collect_submodules('boto3')
hiddenimports += collect_submodules('botocore')
hiddenimports += ['psutil']
hiddenimports += collect_submodules('slack_bolt')
hiddenimports += collect_submodules('slack_sdk')
hiddenimports += ['sqlite_vec']  # Vector search extension (try/except import in vec_db.py)
hiddenimports += collect_submodules('amazon_transcribe')  # Voice transcription (Transcribe Streaming)
hiddenimports += collect_submodules('awscrt')  # AWS Common Runtime (native C extensions)

# Collect data files (including bundled CLI binary from claude_agent_sdk)
datas = []
datas += collect_data_files('claude_agent_sdk')
datas += collect_data_files('certifi')
datas += collect_data_files('sqlite_vec')  # vec0.dylib native extension for vector search
datas += collect_data_files('awscrt')  # AWS CRT native libraries (.dylib/.so)
# Include built-in context files and skills for agent workspace initialization
datas += [('context', 'context')]
datas += [('skills', 'skills')]
# Include DDD templates for default project provisioning
datas += [('templates', 'templates')]
# Include MCP catalog and CLI tool registry at bundle root
datas += [('mcp-catalog.json', '.')]
datas += [('required-cli-tools.json', '.')]

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
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
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
EOF

# Build with PyInstaller
echo "Running PyInstaller..."
pyinstaller backend.spec --clean --noconfirm

# Copy the built binary to output directory
SOURCE_BINARY="dist/python-backend${BINARY_EXT}"
OUTPUT_BINARY="$OUTPUT_DIR/python-backend-$TARGET${BINARY_EXT}"

if [[ ! -f "$SOURCE_BINARY" ]]; then
    echo "Error: Built binary not found at $SOURCE_BINARY"
    exit 1
fi

cp "$SOURCE_BINARY" "$OUTPUT_BINARY"
chmod +x "$OUTPUT_BINARY"

echo "Backend binary built successfully: $OUTPUT_BINARY"
echo ""
echo "File size: $(du -h "$OUTPUT_BINARY" | cut -f1)"

# Auto-deploy to daemon if the daemon directory exists.
# Without this, the daemon runs a stale binary until someone manually
# runs `./dev.sh build`. This was a recurring issue — the sidecar binary
# and daemon binary are separate copies that drift silently.
DAEMON_DIR="${HOME}/.swarm-ai/daemon"
DAEMON_BINARY="${DAEMON_DIR}/python-backend"
if [ -d "$DAEMON_DIR" ]; then
    echo ""
    echo "Deploying to daemon at $DAEMON_BINARY..."
    cp -f "$OUTPUT_BINARY" "${DAEMON_BINARY}.tmp"
    mv -f "${DAEMON_BINARY}.tmp" "$DAEMON_BINARY"  # atomic replace
    chmod +x "$DAEMON_BINARY"
    # Write version file for staleness tracking
    GIT_HASH=$(cd "$PROJECT_ROOT/.." && git rev-parse --short HEAD 2>/dev/null || echo "unknown")
    echo "$GIT_HASH $(date '+%Y-%m-%d %H:%M:%S')" > "${DAEMON_DIR}/.version"
    echo "Daemon binary deployed: $(du -h "$DAEMON_BINARY" | cut -f1) (${GIT_HASH})"
    echo "  ⚠️  Restart daemon to pick up new binary: ./dev.sh daemon restart"

    # Deploy resources (default-agent.json, mcp-catalog.json, etc.)
    RES_SRC="$PROJECT_ROOT/resources"
    RES_DST="${DAEMON_DIR}/resources"
    if [ -d "$RES_SRC" ]; then
        mkdir -p "$RES_DST"
        cp -f "$RES_SRC"/*.json "$RES_DST/" 2>/dev/null || true
        cp -f "$RES_SRC"/*.db "$RES_DST/" 2>/dev/null || true
        echo "Daemon resources deployed: $RES_DST"
    fi
else
    echo ""
    echo "Note: No daemon directory at $DAEMON_DIR — skipping daemon deploy."
    echo "  Install daemon first: python -m channels.install_backend_daemon"
fi

# ── Post-build verification ──────────────────────────────────────
# Ensures the binary has all capabilities that can silently degrade.
# This catches the class of bug where features work in dev (venv) but
# are missing from the PyInstaller binary (see: sqlite_vec incident).
echo ""
echo "Running post-build verification..."
if python scripts/verify_build.py "$OUTPUT_BINARY"; then
    echo "✅ Build verification passed"
else
    echo ""
    echo "⚠️  Build verification found issues (see above)"
    echo "  Fix before releasing to users."
fi
