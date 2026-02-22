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
import argparse
import os
import asyncio
import signal

# Set environment for desktop mode
os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("CLAUDE_CODE_USE_BEDROCK", "false")

# Import and run the FastAPI app
import uvicorn
from main import app

def main():
    parser = argparse.ArgumentParser(description="Claude Agent Platform Backend")
    parser.add_argument("--port", type=int, default=8000, help="Port to run on")
    parser.add_argument("--host", type=str, default="127.0.0.1", help="Host to bind to")
    args = parser.parse_args()

    print(f"Starting backend server on {args.host}:{args.port}", flush=True)

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
    asyncio.run(server.serve())

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
# Use python -m pip on Windows to avoid pip self-upgrade issues
if [[ "$OSTYPE" == "msys" || "$OSTYPE" == "cygwin" ]]; then
    python -m pip install --upgrade pip
    python -m pip install pyinstaller
    python -m pip install -e .
else
    pip install --upgrade pip
    pip install pyinstaller
    pip install -e .
fi

# Create PyInstaller spec file for better control
cat > backend.spec << EOF
# -*- mode: python ; coding: utf-8 -*-

block_cipher = None

a = Analysis(
    ['desktop_main.py'],
    pathex=['.'],
    binaries=[],
    datas=[
        # Include any data files needed
    ],
    hiddenimports=[
        'uvicorn.logging',
        'uvicorn.loops',
        'uvicorn.loops.auto',
        'uvicorn.protocols',
        'uvicorn.protocols.http',
        'uvicorn.protocols.http.auto',
        'uvicorn.protocols.websockets',
        'uvicorn.protocols.websockets.auto',
        'uvicorn.lifespan',
        'uvicorn.lifespan.on',
        'fastapi',
        'pydantic',
        'aiosqlite',
        'anyio._backends._asyncio',
        'claude_agent_sdk',
        # passlib handlers for auth module
        'passlib.handlers.bcrypt',
        'passlib.handlers.pbkdf2_sha256',
        'passlib.handlers.sha2_crypt',
        'passlib.handlers.argon2',
        'bcrypt',
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
