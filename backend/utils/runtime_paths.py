"""runtime_paths — where the deployed backend lives (design
docs/design/2026-10-24-swarmai-usable-by-anyone.md §3.1).

The desktop app (src-tauri/src/runtime_deploy.rs) and dev tooling (scripts/daemon-lib.sh)
deploy the backend to ``<app data>/runtime/<version>/`` and write the version into the
pointer file ``<app data>/runtime/current``; the launchd wrapper runs that version. The
legacy single location ``<app data>/daemon/`` is used only when no runtime is current.
This module is the Python side of that one rule.

Public: ``runtime_root``, ``current_version``, ``current_backend_dir``.
"""
from __future__ import annotations

import re
from pathlib import Path

_VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.+_-]{0,63}$")


def _app_data() -> Path:
    try:
        from config import get_app_data_dir
        return Path(get_app_data_dir())
    except Exception:  # noqa: BLE001 — very early import
        return Path.home() / ".swarm-ai"


def runtime_root() -> Path:
    return _app_data() / "runtime"


def current_version() -> "str | None":
    try:
        text = (runtime_root() / "current").read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return text if _VERSION_RE.match(text) else None


def current_backend_dir() -> Path:
    """The directory of the backend the daemon runs: ``runtime/<current>/`` when it holds
    an executable, else the legacy ``daemon/``."""
    version = current_version()
    if version:
        d = runtime_root() / version
        if (d / "python-backend").is_file() or (d / "python-backend.exe").is_file():
            return d
    return _app_data() / "daemon"
