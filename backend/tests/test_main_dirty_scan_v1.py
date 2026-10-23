"""Pipeline v1 coverage for the live PostToolUse main-checkout dirty reporter.

The tests use RunEngine's public API, a real temporary Git repository, and real linked
worktrees so run discovery and the persisted dirty baseline are exercised end to end.
"""
from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path

import pytest

from core.main_dirty_scan import create_main_dirty_reporter
from core.run_protocol.engine import RunEngine
from core.worktree_write_gate import V1_MAIN_EDIT_REMEDY


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        stdout=subprocess.PIPE,
        text=True,
    ).stdout.strip()


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    (repo / "app.py").write_text("value = 1\n", encoding="utf-8")
    (repo / "validations.json").write_text(
        json.dumps({"schema": 1, "entries": []}), encoding="utf-8"
    )
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "base")
    return repo


def _start_v1_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, RunEngine]:
    data = tmp_path / "data"
    workspace = tmp_path / "workspace"
    (workspace / "Projects" / "P").mkdir(parents=True)
    monkeypatch.setenv("SWARM_DATA_DIR", str(data))
    monkeypatch.setenv("SWARM_WORKSPACE", str(workspace))
    monkeypatch.setenv("SWARM_WORKTREE_ROOT", str(tmp_path / "worktrees"))
    monkeypatch.setenv("SWARM_WORKTREE_LEDGER", str(tmp_path / "ledger"))
    repo = _repo(tmp_path)
    engine = RunEngine.create(
        data_dir=data,
        workspace=workspace,
        project="P",
        profile="direct", requirement="test change",
        session_id="session-v1",
        repos={"repo": str(repo)},
        run_id="run_dirtyv1",
    )
    assert Path(engine.ensure_stage_worktrees()["repo"]).is_dir()
    return repo, engine


def _invoke(reporter) -> dict:
    return asyncio.run(reporter({"tool_name": "Bash"}, "tool-use", None))


def _context(result: dict) -> str:
    return result.get("hookSpecificOutput", {}).get("additionalContext", "")


def test_v1_main_change_reported_once_and_baseline_survives_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, _engine = _start_v1_run(tmp_path, monkeypatch)

    # The first process establishes the clean baseline. A fresh reporter must load that
    # persisted baseline and report a subsequent main-checkout edit exactly once.
    first_process = create_main_dirty_reporter({"sdk_session_id": "session-v1"})
    assert _invoke(first_process) == {}
    (repo / "app.py").write_text("value = 200\n", encoding="utf-8")

    restarted = create_main_dirty_reporter({"sdk_session_id": "session-v1"})
    report = _context(_invoke(restarted))
    assert str(repo / "app.py") in report
    assert V1_MAIN_EDIT_REMEDY in report
    assert "run-claim-main" not in report
    assert _invoke(restarted) == {}
    assert _invoke(create_main_dirty_reporter({"sdk_session_id": "session-v1"})) == {}


def test_silent_when_session_has_no_isolated_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setenv("SWARM_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SWARM_WORKSPACE", str(workspace))
    monkeypatch.setenv("SWARM_WORKTREE_ROOT", str(tmp_path / "worktrees"))
    monkeypatch.setenv("SWARM_WORKTREE_LEDGER", str(tmp_path / "ledger"))

    reporter = create_main_dirty_reporter({"sdk_session_id": "session-without-run"})
    assert _invoke(reporter) == {}


def test_internal_scan_error_fails_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _repo_path, _engine = _start_v1_run(tmp_path, monkeypatch)
    from core import worktree_write_gate

    def fail(_session_id: str) -> list[dict]:
        raise RuntimeError("simulated run-index failure")

    monkeypatch.setattr(worktree_write_gate, "_session_runs_light", fail)
    reporter = create_main_dirty_reporter({"sdk_session_id": "session-v1"})
    assert _invoke(reporter) == {}
