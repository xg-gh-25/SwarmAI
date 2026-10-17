"""`resume_executions` counts real resumes — the agent running `run-resume`.

cmd_run_resume increments it at the paused→running landing point, and stage
completion resets it. (`resume_attempts` is a legacy field: its writer, a
briefing auto-resume directive, was deleted — resuming is always deliberate.)
"""

import json

import pytest


# ─── AC1: cmd_run_resume increments resume_executions ─────────────────────


def _make_run(tmp_path, project, run_id, status="paused", **extra):
    run_dir = tmp_path / "Projects" / project / ".artifacts" / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    data = {
        "id": run_id,
        "status": status,
        "requirement": "test run",
        "stages": [],
        "checkpoint": {"stage": "build"},
        **extra,
    }
    (run_dir / "run.json").write_text(json.dumps(data), encoding="utf-8")
    return run_dir / "run.json"


def test_resume_execution_increments_on_paused_to_running(tmp_path, monkeypatch):
    """A real paused→running resume bumps resume_executions 0→1."""
    import scripts.artifact_cli as cli

    run_file = _make_run(tmp_path, "Proj", "run_x", status="paused",
                         resume_executions=0)

    monkeypatch.setattr(cli, "_resolve_run_file", lambda p, r: run_file)
    monkeypatch.setattr(cli, "_estimate_session_budget", lambda p: 800000)

    args = type("A", (), {"project": "Proj", "run_id": "run_x", "stage": "build"})()
    cli.cmd_run_resume(args, reg=None)

    after = json.loads(run_file.read_text())
    assert after["status"] == "running"
    assert after["resume_executions"] == 1, "real resume must count as 1 execution"


def test_resume_execution_not_incremented_on_non_paused(tmp_path, monkeypatch, capsys):
    """status != paused early-exits → no execution counted (no-op isn't an attempt)."""
    import scripts.artifact_cli as cli

    run_file = _make_run(tmp_path, "Proj", "run_y", status="running",
                         resume_executions=0)
    monkeypatch.setattr(cli, "_resolve_run_file", lambda p, r: run_file)

    args = type("A", (), {"project": "Proj", "run_id": "run_y", "stage": "build"})()
    with pytest.raises(SystemExit):
        cli.cmd_run_resume(args, reg=None)

    after = json.loads(run_file.read_text())
    assert after["resume_executions"] == 0, "early-exit must not count an execution"


def test_resume_execution_accumulates(tmp_path, monkeypatch):
    """Two real resumes → resume_executions == 2 (count REAL attempts)."""
    import scripts.artifact_cli as cli

    run_file = _make_run(tmp_path, "Proj", "run_z", status="paused",
                         resume_executions=0)
    monkeypatch.setattr(cli, "_resolve_run_file", lambda p, r: run_file)
    monkeypatch.setattr(cli, "_estimate_session_budget", lambda p: 800000)
    args = type("A", (), {"project": "Proj", "run_id": "run_z", "stage": "build"})()

    cli.cmd_run_resume(args, reg=None)
    # reset to paused to simulate another crash+resume
    d = json.loads(run_file.read_text()); d["status"] = "paused"
    run_file.write_text(json.dumps(d))
    cli.cmd_run_resume(args, reg=None)

    assert json.loads(run_file.read_text())["resume_executions"] == 2


# ─── AC3: both counters reset on stage completion ─────────────────────────


def test_both_counters_reset_on_stage_completion(tmp_path, monkeypatch):
    """A completed stage zeroes resume_attempts AND resume_executions."""
    import scripts.artifact_cli as cli

    run_file = _make_run(tmp_path, "Proj", "run_r", status="running",
                         resume_attempts=2, resume_executions=2)
    monkeypatch.setattr(cli, "_resolve_run_file", lambda p, r: run_file)

    args = type("A", (), {
        "project": "Proj", "run_id": "run_r",
        "stage_json": json.dumps({"stage": "build", "status": "completed",
                                  "stage_doc_consumed": True, "token_cost": 100}),
        "status": None, "profile": None, "ddd_checksums": None,
        "taste_decision": None, "files_touched": None,
    })()
    cli.cmd_run_update(args, reg=None)

    after = json.loads(run_file.read_text())
    assert after["resume_attempts"] == 0
    assert after["resume_executions"] == 0, \
        "execution counter must reset with attempt counter (else stale diagnostic)"
