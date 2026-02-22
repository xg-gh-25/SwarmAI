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
        log_dir = Path.home() / "Library" / "Application Support" / "Owork" / "logs"
    elif platform.system() == "Windows":
        log_dir = Path.home() / "AppData" / "Local" / "Owork" / "logs"
    else:
        log_dir = Path.home() / ".local" / "share" / "owork" / "logs"
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
    os.environ.setdefault("CLAUDE_CODE_USE_BEDROCK", "false")
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
    args = parser.parse_args()

    write_startup_log(f"Starting server on {args.host}:{args.port}")
    print(f"Starting backend server on {args.host}:{args.port}", flush=True)

    try:
        # Configure uvicorn for PyInstaller compatibility
        config = uvicorn.Config(
            app,
            host=args.host,
            port=args.port,
            log_level="info",
            loop="asyncio",  # Use asyncio loop explicitly
            reload=False,    # Disable reload in bundled app
            workers=1,       # Single worker for bundled app
        )
        server = uvicorn.Server(config)

        # Run the server
        write_startup_log("Starting uvicorn server...")
        asyncio.run(server.serve())
    except Exception as e:
        error_msg = f"Server error: {type(e).__name__}: {e}\n{traceback.format_exc()}"
        write_startup_log(error_msg)
        sys.exit(1)

if __name__ == "__main__":
    main()
EOF

# Navigate to build directory
cd "$BUILD_DIR"

# Create virtual environment and install dependencies
echo "Setting up Python environment..."

# Use python3 on Unix, python on Windows
if [[ "$OSTYPE" == "msys" || "$OSTYPE" == "cygwin" ]]; then
    python -m venv .venv
    source .venv/Scripts/activate
else
    python3 -m venv .venv
    source .venv/bin/activate
fi

# Install dependencies
# Extract dependencies from pyproject.toml and install them separately
# This ensures local modules (routers, core, etc.) remain as top-level modules in current dir

echo "Extracting and installing dependencies..."

# Use python -m pip on Windows to avoid pip self-upgrade issues
if [[ "$OSTYPE" == "msys" || "$OSTYPE" == "cygwin" ]]; then
    python -m pip install --upgrade pip
    python -m pip install pyinstaller pyinstaller-hooks-contrib
    # Install project dependencies (but not the project itself as a package)
    python -m pip install \
        "fastapi>=0.115.0" \
        "uvicorn[standard]>=0.34.0" \
        "python-multipart>=0.0.12" \
        "pydantic>=2.10.0" \
        "pydantic-settings>=2.6.0" \
        "claude-agent-sdk==0.1.20" \
        "boto3>=1.35.0" \
        "aioboto3>=13.0.0" \
        "aiosqlite>=0.20.0" \
        "python-jose[cryptography]>=3.3.0" \
        "passlib[bcrypt]>=1.7.4" \
        "bcrypt>=4.0.0" \
        "slowapi>=0.1.9" \
        "pyyaml>=6.0.0" \
        "anyio>=4.0.0"
else
    pip install --upgrade pip
    pip install pyinstaller pyinstaller-hooks-contrib
    # Install project dependencies (but not the project itself as a package)
    pip install \
        "fastapi>=0.115.0" \
        "uvicorn[standard]>=0.34.0" \
        "python-multipart>=0.0.12" \
        "pydantic>=2.10.0" \
        "pydantic-settings>=2.6.0" \
        "claude-agent-sdk==0.1.20" \
        "boto3>=1.35.0" \
        "aioboto3>=13.0.0" \
        "aiosqlite>=0.20.0" \
        "python-jose[cryptography]>=3.3.0" \
        "passlib[bcrypt]>=1.7.4" \
        "bcrypt>=4.0.0" \
        "slowapi>=0.1.9" \
        "pyyaml>=6.0.0" \
        "anyio>=4.0.0"
fi

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

# Add local modules explicitly (these are in the current directory, not installed packages)
local_modules = [
    # Main entry
    'main',
    'config',
    # Routers
    'routers',
    'routers.agents',
    'routers.auth',
    'routers.chat',
    'routers.mcp',
    'routers.plugins',
    'routers.settings',
    'routers.skills',
    'routers.workspace',
    # Schemas
    'schemas',
    'schemas.agent',
    'schemas.auth',
    'schemas.error',
    'schemas.marketplace',
    'schemas.mcp',
    'schemas.message',
    'schemas.permission',
    'schemas.settings',
    'schemas.skill',
    'schemas.workspace',
    # Database
    'database',
    'database.base',
    'database.sqlite',
    'database.dynamodb',
    # Core
    'core',
    'core.agent_manager',
    'core.auth',
    'core.exceptions',
    'core.local_skill_manager',
    'core.plugin_manager',
    'core.session_manager',
    'core.skill_manager',
    'core.workspace_manager',
    # Middleware
    'middleware',
    'middleware.auth',
    'middleware.error_handler',
    'middleware.rate_limit',
]

a = Analysis(
    ['desktop_main.py'],
    pathex=[os.getcwd()],  # Add current directory to path
    binaries=[],
    datas=[],
    hiddenimports=hiddenimports + local_modules + [
        # Claude Agent SDK
        'claude_agent_sdk',
        # passlib handlers for auth module
        'passlib.handlers.bcrypt',
        'passlib.handlers.pbkdf2_sha256',
        'passlib.handlers.sha2_crypt',
        'passlib.handlers.argon2',
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
        'pyyaml',
        'jose',
        'python_jose',
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
