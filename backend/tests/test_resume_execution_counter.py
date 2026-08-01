"""R2: split the conflated resume_attempts counter into two signals.

Bug: `resume_attempts` (proactive_intelligence.py) increments at directive EMIT
and serves BOTH the emit-throttle AND the exhaustion signal. The TRUE execution
event — the agent actually running `run-resume` (cmd_run_resume) — is uncounted.
So "exhausted 3 attempts" cannot tell apart:
  - agent executed resume 3× and the pipeline keeps crashing  → PIPELINE broken
  - directive emitted 3×, agent never executed it             → DELIVERY broken
These need opposite human responses.

Fix: add `resume_executions`, incremented in cmd_run_resume (the real
paused→running landing point). resume_attempts stays the emit-throttle.
The exhausted briefing line reports both and diagnoses which mode.
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




# ─── AC2: exhausted briefing line distinguishes the two failure modes ─────


def test_exhausted_line_delivery_mode_when_zero_executions(tmp_path):
    """executions==0 → 'never executed / delivery' wording."""
    from core.proactive_intelligence import _get_paused_pipeline_highlights

    _make_run(tmp_path, "Proj", "run_d", status="paused",
              resume_attempts=3, resume_executions=0)

    lines = _get_paused_pipeline_highlights(tmp_path)
    blob = "\n".join(lines).lower()
    assert "exhausted" in blob or "manual" in blob
    assert ("never executed" in blob or "delivery" in blob
            or "not picked up" in blob), \
        f"zero-execution exhaustion must flag DELIVERY mode; got: {blob}"


def test_exhausted_line_pipeline_mode_when_executions_present(tmp_path):
    """executions>0 → 'executed Nx / pipeline' wording."""
    from core.proactive_intelligence import _get_paused_pipeline_highlights

    _make_run(tmp_path, "Proj", "run_p", status="paused",
              resume_attempts=3, resume_executions=3)

    lines = _get_paused_pipeline_highlights(tmp_path)
    blob = "\n".join(lines).lower()
    assert "exhausted" in blob or "manual" in blob
    assert ("executed" in blob or "pipeline" in blob), \
        f"nonzero-execution exhaustion must flag PIPELINE mode; got: {blob}"


# ─── AC4: empty crash-shell runs are auto-abandoned, never nagged forever ──
# Root cause (run_843962a5 follow-up): a run that crashed BEFORE completing any
# stage (stages==[], 0 tokens, checkpoint.reason==session_crash_auto_detected)
# has ZERO recoverable state, yet it entered the auto-resume flow, exhausted its
# 3 attempts, and emitted a "manual intervention needed" nag EVERY session. Worse,
# each emit rewrote updated_at, which reset the age-gated crash-zombie cleaner so
# it could never reap the shell → a self-perpetuating false alarm. The fix is the
# mirror of the existing terminal guard: terminal (all done) → skip; empty-shell
# (nothing done + 0 tokens + crash) → abandon immediately.


def test_empty_crash_shell_is_abandoned_not_surfaced(tmp_path):
    """stages==[] + 0 tokens + crash reason → abandoned, and NOT surfaced/nagged."""
    from core.proactive_intelligence import _get_paused_pipeline_highlights

    run_file = _make_run(
        tmp_path, "Proj", "run_shell", status="paused",
        checkpoint={"reason": "session_crash_auto_detected"},
        stages=[], resume_attempts=0,
    )

    lines = _get_paused_pipeline_highlights(tmp_path)
    blob = "\n".join(lines).lower()

    after = json.loads(run_file.read_text())
    assert after["status"] == "abandoned", \
        "an empty crash-shell must be abandoned, not left paused to nag forever"
    assert "crash_residue" in (after.get("abandon_reason") or ""), \
        f"abandon_reason must mark it a crash residue; got {after.get('abandon_reason')!r}"
    # It must NOT appear as a resume candidate OR an exhausted-nag line.
    assert "run_shell" not in blob, f"empty shell must not be surfaced; got: {blob}"
    assert "manual intervention" not in blob, \
        f"empty shell must not trigger the manual-intervention nag; got: {blob}"


def test_empty_shell_guard_does_not_touch_run_with_completed_stage(tmp_path):
    """A crash orphan that DID complete a stage is recoverable → NOT abandoned as a shell."""
    from core.proactive_intelligence import _get_paused_pipeline_highlights

    run_file = _make_run(
        tmp_path, "Proj", "run_realwork", status="paused",
        checkpoint={"reason": "session_crash_auto_detected"},
        stages=[{"stage": "evaluate", "status": "completed", "token_cost": 11000}],
        resume_attempts=0,
    )

    _get_paused_pipeline_highlights(tmp_path)

    after = json.loads(run_file.read_text())
    assert after["status"] != "abandoned", \
        "a crash orphan with a completed stage has recoverable work — never a shell"


def test_empty_shell_guard_spares_recorded_stage_with_zero_tokens(tmp_path):
    """A crash orphan stopped mid-THINK/PLAN (stage 'recorded', token_cost 0) is
    RECOVERABLE — it must NOT be false-killed as an empty shell.

    Adversarial-BLOCK regression (run_843962a5 follow-up): the earlier predicate
    keyed on 'no COMPLETED stage', which would abandon a run whose only stages are
    'recorded' (THINK/PLAN publish artifacts as status='recorded', not 'completed')
    even at token_cost 0 — destroying resumable state. The guard now triggers ONLY
    on a truly empty stages==[]. A non-empty stages[] = progress = never a shell.
    """
    from core.proactive_intelligence import _get_paused_pipeline_highlights

    run_file = _make_run(
        tmp_path, "Proj", "run_recorded", status="paused",
        checkpoint={"reason": "session_crash_auto_detected"},
        stages=[
            {"stage": "think", "status": "recorded", "artifact_id": "art_1"},
            {"stage": "plan", "status": "recorded", "artifact_id": "art_2"},
        ],
        resume_attempts=0,
    )

    _get_paused_pipeline_highlights(tmp_path)

    after = json.loads(run_file.read_text())
    assert after["status"] != "abandoned", \
        "a run with 'recorded' THINK/PLAN stages has resumable state — never a shell, even at 0 tokens"


def test_empty_shell_guard_ignores_deliberate_pause(tmp_path):
    """A deliberate pause (true-trigger reason) with no stages is NOT a crash shell → preserved."""
    from core.proactive_intelligence import _get_paused_pipeline_highlights

    run_file = _make_run(
        tmp_path, "Proj", "run_decision", status="paused",
        checkpoint={"reason": "Gate 1 BLOCK: decide X?"},
        stages=[], resume_attempts=0,
    )

    _get_paused_pipeline_highlights(tmp_path)

    after = json.loads(run_file.read_text())
    assert after["status"] == "paused", \
        "a deliberate Gate-BLOCK pause must never be reaped as a crash shell"
