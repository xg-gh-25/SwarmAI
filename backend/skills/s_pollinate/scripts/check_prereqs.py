#!/usr/bin/env python3
"""Pre-flight check for Pollinate dependencies.

Checks required binaries and per-backend env vars.
Always exits 0 -- prints status for SKILL.md consumption.

Usage:
    python check_prereqs.py
    # Output: ALL_OK (backend=edge)
    # or:    MISSING:ffmpeg AZURE_SPEECH_KEY (backend=azure)
"""
import os
import shutil
import sys

# Backend -> required env vars
BACKEND_ENV = {
    "edge":       [],
    "azure":      ["AZURE_SPEECH_KEY", "AZURE_SPEECH_REGION"],
    "doubao":     ["VOLCENGINE_APPID", "VOLCENGINE_ACCESS_TOKEN"],
    "cosyvoice":  ["DASHSCOPE_API_KEY"],
    "elevenlabs": ["ELEVENLABS_API_KEY"],
    "openai":     ["OPENAI_API_KEY"],
    "google":     ["GOOGLE_TTS_API_KEY"],
}

REQUIRED_BINS = ["python3", "node", "ffmpeg", "npx"]


def resolve_backend() -> str:
    """Resolve TTS backend: env > user_prefs > default."""
    env = os.environ.get("TTS_BACKEND")
    if env and env in BACKEND_ENV:
        return env
    # Could read user_prefs.json here, but keep it simple for pre-flight
    return "edge"


def check():
    backend = resolve_backend()
    missing = []

    # Check binaries
    for b in REQUIRED_BINS:
        if not shutil.which(b):
            missing.append(b)

    # Check backend env vars
    for var in BACKEND_ENV.get(backend, []):
        if not os.environ.get(var):
            missing.append(var)

    if missing:
        print(f"MISSING:{' '.join(missing)} (backend={backend})")
    else:
        print(f"ALL_OK (backend={backend})")


if __name__ == "__main__":
    check()
    sys.exit(0)  # Always 0 -- caller reads stdout
