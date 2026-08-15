"""Tests for config.get_app_data_dir() — the SWARM_DATA_DIR env override (AC1).

WHY: get_app_data_dir() had NO env escape hatch — every app process (incl. any
run outside the daemon) defaulted to the live production ~/.swarm-ai/data.db and
created it if missing (the upstream mechanism class behind the 8-12 data.db loss).
The override lets a test/sandbox point at a scratch dir; UNSET must remain
byte-identical to today so the 138 callers see zero behavior change.
"""

from __future__ import annotations

from pathlib import Path

import config as config_module
from config import get_app_data_dir


def test_unset_returns_home_swarm_ai(monkeypatch):
    """AC1: SWARM_DATA_DIR unset → ~/.swarm-ai (byte-identical to pre-override)."""
    monkeypatch.delenv("SWARM_DATA_DIR", raising=False)
    assert get_app_data_dir() == Path.home() / ".swarm-ai"


def test_env_override_points_elsewhere(monkeypatch, tmp_path):
    """AC1: SWARM_DATA_DIR set → that path, NOT ~/.swarm-ai."""
    monkeypatch.setenv("SWARM_DATA_DIR", str(tmp_path / "sandbox"))
    assert get_app_data_dir() == Path(str(tmp_path / "sandbox"))
    assert get_app_data_dir() != Path.home() / ".swarm-ai"


def test_env_override_empty_string_falls_back(monkeypatch):
    """AC1: empty SWARM_DATA_DIR is treated as unset (falsy) → default home."""
    monkeypatch.setenv("SWARM_DATA_DIR", "")
    assert get_app_data_dir() == Path.home() / ".swarm-ai"


def test_autouse_guard_prevents_production_path_resolution():
    """AC2: the conftest _isolate_app_data_dir autouse guard is ACTIVE — with no
    per-test opt-out, get_app_data_dir() must NOT resolve to the live production
    ~/.swarm-ai (prevention > detection: no test can touch/create the real store).
    This test does NOT delenv, so it runs under the guard's SWARM_DATA_DIR."""
    resolved = get_app_data_dir()
    assert resolved != Path.home() / ".swarm-ai", (
        "autouse guard failed — a test resolved the PRODUCTION data dir; "
        "SQLiteDatabase(None) / self-opened data.db would hit the live store"
    )
    # And it must point somewhere real+writable (the temp dir the fixture made).
    assert resolved.exists()
