"""Tests for core/review_round_gate.py — 再一再二不再三 (at most 3 rounds per expert per stage).

Methodology: a real run.json (this session's running run, an open stage with its
stage_timing window) under a temp SWARM_WORKSPACE, and real SubagentStop marker files
under a temp app-data dir; the gate is driven through its async PreToolUse callable.

Invariants:
- an expert with 3 completed rounds in the open stage is denied a 4th; the deny says
  stop fixing, run one root-cause review, and how each verdict is handled — and never
  asks the user
- 0-2 completed rounds → approve; other experts, other runs, markers before the stage
  window, and adversarial-twin markers do not count
- a re-opened stage keeps its count (prior_windows); goal_cycle is exempt
- no EXPERT-ID line, not Agent/Task, no session id, no active run → approve
- scan failure fails open; SWARM_REVIEW_ROUND_GATE_FORCE=1 approves
- build_hooks registers it for Agent|Task
"""
import asyncio
import json
import time
from datetime import datetime, timezone

import pytest

from core import review_round_gate as rrg
from core import security_hooks as sh

SID = "app-session-1"
RID = "run_round1"


def _iso(t):
    return datetime.fromtimestamp(t, tz=timezone.utc).isoformat()


@pytest.fixture
def env(tmp_path, monkeypatch):
    ws = tmp_path / "ws"
    data = tmp_path / "data"
    (data / "state" / "pipeline_agent_audit").mkdir(parents=True)
    monkeypatch.setenv("SWARM_WORKSPACE", str(ws))
    monkeypatch.setattr(sh, "_ACTIVE_RUN_GLOB_ROOT", str(ws))
    import config
    monkeypatch.setattr(config, "get_app_data_dir", lambda: data)
    monkeypatch.delenv("SWARM_REVIEW_ROUND_GATE_FORCE", raising=False)
    return ws, data / "state" / "pipeline_agent_audit"


def _run(ws, stage="deliver", started=None, prior=None, run_id=RID, session_id=SID):
    started = started or time.time() - 3600
    timing = {"review": {"started_at": _iso(started - 7200), "completed_at": _iso(started - 3600)},
              stage: {"started_at": _iso(started)}}
    if prior:
        timing[stage]["prior_windows"] = [{"started_at": _iso(a), "completed_at": _iso(b)}
                                          for a, b in prior]
    d = ws / "Projects" / "SwarmAI" / ".artifacts" / "runs" / run_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "run.json").write_text(json.dumps({
        "id": run_id, "project": "SwarmAI", "status": "running", "session_id": session_id,
        "stage_timing": timing}))


def _marker(audit, n, expert, ts=None, run_id=RID, adv=False):
    name = f"session_s1_{'adv_' if adv else ''}{n}.marker"
    (audit / name).write_text(json.dumps({"ts": ts or time.time() - 60, "event": "SubagentStop",
                                          "run_id": run_id, "expert_id": expert}))


def _spawn(expert="correctness", tool="Agent", sid=SID, prompt=None):
    gate = rrg.create_review_round_gate({"sdk_session_id": sid})
    prompt = prompt if prompt is not None else f"EXPERT-ID: {expert}\nReview the fix."
    return asyncio.run(gate({"tool_name": tool, "tool_input": {"prompt": prompt}}, None, None))


def _denied(res):
    return res.get("hookSpecificOutput", {}).get("permissionDecision") == "deny"


@pytest.mark.parametrize("done", [0, 1, 2])
def test_rounds_one_to_three_are_allowed(env, done):
    ws, audit = env
    _run(ws)
    for i in range(done):
        _marker(audit, i, "correctness")
    assert _spawn() == {"decision": "approve"}


def test_fourth_round_is_denied_with_the_handoff(env):
    ws, audit = env
    _run(ws)
    for i in range(3):
        _marker(audit, i, "correctness")
    res = _spawn()
    assert _denied(res)
    reason = res["hookSpecificOutput"]["permissionDecisionReason"]
    assert "再一再二不再三" in reason and RID in reason and "deliver" in reason
    for word in ("EXPERT-ID: root-cause", "ROOT-CAUSE VERDICT: SOUND", "WRONG-APPROACH",
                 "REPORT §10", "push_ready false", "no redesign", "do not ask the user"):
        assert word in reason
    assert "wait for the answer" not in reason


def test_fourth_review_stage_round_records_and_continues(env):
    """REVIEW has no root-cause step: the capped expert's open items are recorded and
    the run goes on (autonomous); the user is never asked."""
    ws, audit = env
    _run(ws, stage="review", started=time.time() - 600)
    for i in range(3):
        _marker(audit, i, "spec-compliance")
    res = _spawn("spec-compliance")
    assert _denied(res)
    reason = res["hookSpecificOutput"]["permissionDecisionReason"]
    assert "review stage" in reason and "do not ask the user" in reason
    assert "REPORT §10" in reason and "continue the run" in reason
    assert "root-cause" not in reason


def test_only_this_expert_run_and_stage_window_count(env):
    ws, audit = env
    started = time.time() - 3600
    _run(ws, started=started)
    _marker(audit, 1, "correctness")
    _marker(audit, 2, "correctness", adv=True)                 # adversarial twin
    _marker(audit, 3, "correctness", run_id="run_other")       # another run
    _marker(audit, 4, "correctness", ts=started - 1800)        # the earlier review stage
    _marker(audit, 5, "security")
    _marker(audit, 6, "security")
    _marker(audit, 7, "security")
    assert _spawn("correctness") == {"decision": "approve"}
    assert rrg.rounds_used(SID, "correctness")["used"] == 1
    assert _denied(_spawn("security"))


def test_reopened_stage_keeps_its_count(env):
    ws, audit = env
    now = time.time()
    _run(ws, started=now - 600, prior=[(now - 5000, now - 4000)])
    _marker(audit, 1, "red-team", ts=now - 4500)                 # first window
    _marker(audit, 2, "red-team", ts=now - 4200)
    _marker(audit, 3, "red-team", ts=now - 300)                  # current window
    _marker(audit, 4, "red-team", ts=now - 2000)                 # between windows
    assert rrg.rounds_used(SID, "red-team")["used"] == 3
    assert _denied(_spawn("red-team"))


def test_goal_cycle_is_exempt(env):
    ws, audit = env
    _run(ws, stage="goal_cycle")
    for i in range(5):
        _marker(audit, i, "correctness")
    assert _spawn() == {"decision": "approve"}


@pytest.mark.parametrize("kw", [
    {"prompt": "Review this change."},               # no EXPERT-ID line
    {"prompt": "EXPERT-ID: <EXPERT_ID>\nx"},          # malformed anchor
    {"tool": "Bash"},
    {"sid": ""},
])
def test_not_a_pipeline_expert_spawn_passes(env, kw):
    ws, audit = env
    _run(ws)
    for i in range(3):
        _marker(audit, i, "correctness")
    assert _spawn(**kw) == {"decision": "approve"}


def test_no_active_run_of_this_session_passes(env):
    ws, audit = env
    _run(ws, session_id="someone-else")
    for i in range(3):
        _marker(audit, i, "correctness")
    assert _spawn() == {"decision": "approve"}


def test_scan_failure_fails_open_and_force_approves(env, monkeypatch):
    ws, audit = env
    _run(ws)
    for i in range(3):
        _marker(audit, i, "correctness")
    monkeypatch.setenv("SWARM_REVIEW_ROUND_GATE_FORCE", "1")
    assert _spawn() == {"decision": "approve"}
    monkeypatch.delenv("SWARM_REVIEW_ROUND_GATE_FORCE")

    def boom(*_a):
        raise OSError("disk")

    monkeypatch.setattr(rrg, "rounds_used", boom)
    assert _spawn() == {"decision": "approve"}


def test_build_hooks_registers_it_for_agent_spawns(monkeypatch):
    from core import hook_builder
    from core.permission_manager import permission_manager
    recorded = []
    original = hook_builder.HookRegistry.register

    def _spy(self, event, hook, name, matcher=None, no_timeout=False):
        recorded.append((event, name, matcher))
        return original(self, event, hook, name, matcher=matcher, no_timeout=no_timeout)

    monkeypatch.setattr(hook_builder.HookRegistry, "register", _spy)
    asyncio.run(hook_builder.build_hooks(
        agent_config={"id": "default"}, enable_skills=False, enable_mcp=False,
        resume_session_id="test-sess", session_context={"sdk_session_id": "test-sess"},
        permission_manager=permission_manager))
    assert ("PreToolUse", "review_round_gate", "Agent|Task") in recorded


def test_root_cause_review_runs_once_per_cycle(env):
    ws, audit = env
    _run(ws)
    assert _spawn("root-cause") == {"decision": "approve"}
    _marker(audit, 1, "root-cause")
    res = _spawn("root-cause")
    assert _denied(res)
    assert "one per review cycle" in res["hookSpecificOutput"]["permissionDecisionReason"]


def test_a_redesign_starts_a_fresh_allowance(env):
    ws, audit = env
    now = time.time()
    _run(ws, started=now - 3600)
    for i in range(3):
        _marker(audit, i, "correctness", ts=now - 3000 + i)
    assert _denied(_spawn())
    d = ws / "Projects" / "SwarmAI" / ".artifacts" / "runs" / RID / "run.json"
    st = json.loads(d.read_text())
    st["redesigns"] = [{"ts": now - 1000}]
    d.write_text(json.dumps(st))
    assert _spawn() == {"decision": "approve"}


def _plan_run(ws, waves=None, started=None):
    started = started or time.time() - 3600
    _run(ws, started=started)
    rf = ws / "Projects" / "SwarmAI" / ".artifacts" / "runs" / RID / "run.json"
    st = json.loads(rf.read_text())
    st["dispatch_plan"] = {"deliver": ["correctness", "security", "concurrency", "meta-review"]}
    if waves is not None:
        st["gate2_waves"] = waves
    rf.write_text(json.dumps(st))
    return started


def test_a_solo_spawn_after_a_sibling_finished_is_denied_once(env):
    """Parallel by prevention: once a wave member finished, spawning another alone while
    ≥2 are left is denied ONCE, naming the rest; the batch sent next is allowed."""
    ws, audit = env
    rrg._WAVE_DENIED.clear()
    _plan_run(ws)
    assert not _denied(_spawn("security"))           # nothing finished: a batch in flight
    _marker(audit, 1, "correctness")                 # one member done, two left
    res = _spawn("security")
    assert _denied(res)
    why = res["hookSpecificOutput"]["permissionDecisionReason"]
    assert "concurrency" in why and "security" in why and "ONE message" in why
    assert not _denied(_spawn("security"))           # asked once per wave
    assert not _denied(_spawn("concurrency"))


def test_a_recheck_wave_is_kept_parallel_too(env):
    ws, audit = env
    rrg._WAVE_DENIED.clear()
    started = time.time() - 3600
    _plan_run(ws, waves=[{"at": started + 600, "experts": ["correctness", "security",
                                                          "concurrency"]}], started=started)
    _marker(audit, 1, "correctness", ts=started + 900)
    assert _denied(_spawn("security"))
    rrg._WAVE_DENIED.clear()
    # a two-expert wave, or the last one left: nothing to batch, never denied
    _plan_run(ws, waves=[{"at": started + 600, "experts": ["correctness", "security"]}],
              started=started)
    assert not _denied(_spawn("security"))
