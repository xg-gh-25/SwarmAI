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


def test_another_live_session_s_files_outside_the_run_are_not_listed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """design 2026-11-01-pipeline-delivery-friction D8: a main-checkout file another live
    session wrote, still uncommitted and not one of this run's files, is someone's
    parallel work: it is counted, not listed. A file nobody else claims is listed, and
    so is a sibling's file this run also changes."""
    repo, engine = _start_v1_run(tmp_path, monkeypatch)
    worktree = Path(engine.ensure_stage_worktrees()["repo"])
    (repo / "theirs.py").write_text("x = 0\n", encoding="utf-8")
    (repo / "shared.py").write_text("x = 0\n", encoding="utf-8")
    _git(repo, "add", "theirs.py", "shared.py"); _git(repo, "commit", "-qm", "two files")
    mine = {"sdk_session_id": "session-v1"}
    other = {"_files_written": {str(Path(repo).resolve() / "theirs.py"),
                                str(Path(repo).resolve() / "shared.py")}}
    from core import shared_checkout
    monkeypatch.setattr(shared_checkout, "_live_contexts",
                        lambda: [("session-v1", mine), ("other-session", other)])
    reporter = create_main_dirty_reporter(mine)
    assert _invoke(reporter) == {}                      # the baseline
    (worktree / "shared.py").write_text("x = 'run'\n", encoding="utf-8")   # the run's file
    (repo / "theirs.py").write_text("x = 1\n", encoding="utf-8")
    (repo / "shared.py").write_text("x = 1\n", encoding="utf-8")
    (repo / "app.py").write_text("value = 3\n", encoding="utf-8")
    report = _context(_invoke(reporter))
    assert str(Path(repo).resolve() / "app.py") in report or str(repo / "app.py") in report
    assert "shared.py" in report, report
    assert "theirs.py" not in report.split("(1 more changed by")[0], report
    assert "1 more changed by another live session" in report, report


def test_a_file_this_session_wrote_stays_listed_even_if_another_wrote_it_too(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, engine = _start_v1_run(tmp_path, monkeypatch)
    (repo / "both.py").write_text("x = 0\n", encoding="utf-8")
    _git(repo, "add", "both.py"); _git(repo, "commit", "-qm", "one file")
    path = str(Path(repo).resolve() / "both.py")
    mine = {"sdk_session_id": "session-v1", "_files_written": {path}}
    other = {"_files_written": {path}}
    from core import shared_checkout
    monkeypatch.setattr(shared_checkout, "_live_contexts",
                        lambda: [("session-v1", mine), ("other-session", other)])
    reporter = create_main_dirty_reporter(mine)
    assert _invoke(reporter) == {}
    (repo / "both.py").write_text("x = 1\n", encoding="utf-8")
    assert "both.py" in _context(_invoke(reporter))
