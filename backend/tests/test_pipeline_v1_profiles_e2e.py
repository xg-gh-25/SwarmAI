"""Profile × mode end-to-end lifecycle tests for Pipeline v1.

Methodology: real RunEngine persistence/worktree creation plus complete typed stage/check
chains; direct's full code/runner/landing path is covered separately.
"""
from __future__ import annotations

import json
import subprocess

import pytest

from core.run_protocol import checks, lifecycle
from core.run_protocol.engine import RunEngine


def _git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True,
                          stdout=subprocess.PIPE, text=True).stdout.strip()


def _repo(tmp_path):
    repo = tmp_path / "repo"; repo.mkdir()
    _git(repo, "init", "-q"); _git(repo, "config", "user.email", "a@b.c"); _git(repo, "config", "user.name", "T")
    (repo / "a.py").write_text("x=1\n")
    (repo / "validations.json").write_text(json.dumps({"schema": 1, "entries": [{
        "id": "none", "paths": ["docs/**"], "cwd": ".",
        "argv": ["python", "-c", "pass"], "call_type": "lint"}]}))
    _git(repo, "add", "."); _git(repo, "commit", "-qm", "base")
    return repo


def _append(engine, kind, payload, **kwargs):
    event, _ = engine.store.append_new(writer=engine.authority.writer("cli"),
                                        kind=kind, at="2026-10-22T00:00:00+00:00",
                                        payload=payload, **kwargs)
    engine._settle()
    return event


def _complete(engine):
    state = engine.projection(); stage = state["stage"]
    roster = [spec.check_id for spec in checks.stage_checks(state["profile"], stage)]
    attempt = state["stage_attempts"].get(stage, 0) + 1
    opened = _append(engine, "stage_attempt", {"check_ids": roster},
                     stage=stage, attempt=attempt)
    for check_id in roster:
        _append(engine, "check", {"stage_attempt_event_id": opened["event_id"],
                                   "check_id": check_id, "passed": True, "detail": "e2e"},
                stage=stage, attempt=attempt)
    payload = {"stage_attempt_event_id": opened["event_id"]}
    if stage == "test": payload["final_verdict"] = "green"
    _append(engine, "stage_complete", payload, stage=stage, attempt=attempt)


@pytest.mark.parametrize("profile", sorted(lifecycle.PROFILE_STAGES))
@pytest.mark.parametrize("mode", ["isolated", "shared"])
def test_every_profile_and_mode_reaches_safe_completed(tmp_path, monkeypatch, profile, mode):
    case = tmp_path / f"{profile}-{mode}"; case.mkdir()
    data, workspace = case / "data", case / "workspace"
    (workspace / "Projects/P").mkdir(parents=True)
    monkeypatch.setenv("SWARM_DATA_DIR", str(data))
    monkeypatch.setenv("SWARM_WORKTREE_ROOT", str(case / "worktrees"))
    monkeypatch.setenv("SWARM_WORKTREE_LEDGER", str(case / "ledger"))
    repo = _repo(case)
    repos = {} if profile == "research" else {"repo": str(repo)}
    engine = RunEngine.create(data_dir=data, workspace=workspace, project="P",
                              profile=profile, session_id="s", repos=repos,
                              mode=mode, run_id=f"run_{profile}_{mode}")
    if mode == "isolated" and repos:
        assert set(engine.ensure_worktrees()) == {"repo"}
    while engine.projection()["stage"] != "deliver":
        _complete(engine)
    if profile != "research":
        _append(engine, "land", {"landing": "not_landed", "commit": None,
                                  "reason": "tests_red"}, ordinal=1)
    _append(engine, "render", {"document": "REPORT", "blob_sha": "a" * 64},
            stage="deliver", attempt=1)
    _complete(engine)
    assert engine.projection()["status"] == "reflecting"
    _append(engine, "reflect_complete", {}, ordinal=2)
    state = engine.projection()
    assert state["status"] == "completed"
    assert state["landing"] == ("no_code" if profile == "research" else "not_landed")
