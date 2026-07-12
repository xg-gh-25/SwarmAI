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

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # sibling _ddd_paths
from _ddd_paths import accounts_path as _accounts_path

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

    # I5: Check for accounts.yaml (optional, for publish metadata) — DDD-local
    accounts_path = str(_accounts_path())
    has_accounts = os.path.isfile(accounts_path)

    if missing:
        print(f"MISSING:{' '.join(missing)} (backend={backend})")
    else:
        accounts_note = "" if has_accounts else f" [NOTE: {accounts_path} not found — publish_meta.py will skip account identity]"
        print(f"ALL_OK (backend={backend}){accounts_note}")


if __name__ == "__main__":
    check()
    sys.exit(0)  # Always 0 -- caller reads stdout
