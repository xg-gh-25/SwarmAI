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


# ---------------------------------------------------------------------------
# AC2: the 3 daemon-pathed constants must resolve via get_app_data_dir() AT CALL
# TIME (functionized), not freeze the value at import. A module-level constant
# `X = Path.home()/'.swarm-ai'/...` captures the value BEFORE conftest sets
# SWARM_DATA_DIR → the daemon would write to the live production tree even under
# the sandbox guard. These tests set SWARM_DATA_DIR then call the resolver; a
# frozen import-time constant makes them RED (mutation-proven). The autouse
# _isolate_app_data_dir guard already sets SWARM_DATA_DIR to a temp dir, so the
# resolvers must return a path UNDER that temp dir, never ~/.swarm-ai.
# ---------------------------------------------------------------------------

def test_task_manager_tasks_dir_resolves_at_call_time():
    """AC2: task_manager tasks dir honors the live SWARM_DATA_DIR (call-time)."""
    from core.task_manager import _tasks_dir

    resolved = _tasks_dir()
    assert resolved == get_app_data_dir() / "tasks"
    assert resolved != Path.home() / ".swarm-ai" / "tasks", (
        "tasks dir froze at import — a frozen constant ignores SWARM_DATA_DIR and "
        "the daemon would write into the live production tree"
    )


def test_correction_tracker_default_state_path_resolves_at_call_time(tmp_path, monkeypatch):
    """AC2: correction_tracker default state path honors live SWARM_DATA_DIR, AND a
    passed state_path still overrides it (lazy override preserved)."""
    from core.evolution.correction_tracker import _default_state_path, CorrectionClassTracker

    resolved = _default_state_path()
    assert resolved == get_app_data_dir() / "state" / "correction_tracker.json"
    assert resolved != Path.home() / ".swarm-ai" / "state" / "correction_tracker.json"

    # override-if-passed semantics must survive functionizing
    explicit = tmp_path / "custom_state.json"
    tracker = CorrectionClassTracker(state_path=explicit)
    assert tracker._state_path == explicit


def test_ddd_bindings_default_root_resolves_at_call_time():
    """AC2: ddd_bindings default bindings root honors the live SWARM_DATA_DIR."""
    from core.ddd_bindings import _default_bindings_root

    resolved = _default_bindings_root()
    assert resolved == get_app_data_dir() / "bindings"
    assert resolved != Path.home() / ".swarm-ai" / "bindings"
