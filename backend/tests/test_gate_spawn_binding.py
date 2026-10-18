"""Gate-0 / Gate-1 verdicts bound to a real spawn (artifact_cli ``_gate_verdict_spawn_errors``).

WHAT IS TESTED
- A Gate-1 verdict on a run created after GATE_SPAWN_CUTOVER needs a gate1-skeptic
  SubagentStop marker of THIS run since the prompt hand-out; another run's marker, an
  earlier marker or another expert's marker does not count.
- full / bugfix BUILD must record a verdict and may not record SKIPPED.
- Gate-0: understanding.skeptic_verdict needs a gate0-skeptic marker since EVALUATE began.
- Fail-open (warning, no error): unreadable store, no marker of the run at all (no audit
  hook), only pre-EXPERT-ID markers; runs created before the cutover are not enforced.
- stage-start never skips Gate-1 on a hand-written verdict with no spawn behind it.
- Wiring: stage-complete --stage build refuses an unbacked verdict end to end.

METHODOLOGY: markers are written into a tmp ``state/pipeline_agent_audit`` with
``config.get_app_data_dir`` monkeypatched (as test_stage_boundary_commands does).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.test_stage_boundary_commands import (  # noqa: F401  (ws is a fixture)
    DECISION, PROJECT, _build_artifact, _c2, _complete, _make_run, _plan_artifact,
    _run_state, _start, _write_evidence, cli, ws)

AFTER = "2026-10-18T00:00:00+00:00"


def _now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()

HANDOUT = "2026-10-18T01:00:00+00:00"


@pytest.fixture
def audit(tmp_path, monkeypatch):
    import config
    d = tmp_path / "appdata" / "state" / "pipeline_agent_audit"
    d.mkdir(parents=True)
    monkeypatch.setattr(config, "get_app_data_dir", lambda: tmp_path / "appdata")
    # every timestamp here must be in the past (markers are read up to now)
    monkeypatch.setattr(cli, "GATE_SPAWN_CUTOVER", "2026-10-17T00:00:00+00:00")
    n = [0]

    def write(run_id, expert_id, iso, legacy=False):
        n[0] += 1
        data = {"ts": cli._iso_epoch(iso), "event": "SubagentStop", "run_id": run_id}
        if not legacy:
            data["expert_id"] = expert_id
        (d / f"session_s_{n[0]}.marker").write_text(json.dumps(data))
    return write


def _state(**over):
    st = {"created_at": AFTER, "gate1_handout": {"at": HANDOUT},
          "stage_timing": {"build": {"started_at": HANDOUT},
                           "evaluate": {"started_at": "2026-10-18T00:10:00+00:00"}}}
    st.update(over)
    return st


def _errs(state, stage="build", profile="bugfix", rec=None, artifact=None):
    w: list = []
    e = cli._gate_verdict_spawn_errors("run_g", state, stage, profile, artifact,
                                      rec if rec is not None else {"gate1_verdict": "PASS"}, w)
    return e, w


def test_a_gate1_verdict_needs_this_runs_gate1_spawn_after_the_handout(audit):
    audit("run_g", "correctness", "2026-10-18T00:30:00+00:00")       # the run has markers
    e, _ = _errs(_state())
    assert e and "no gate1-skeptic sub-agent" in e[0]
    audit("run_other", "gate1-skeptic", "2026-10-18T02:00:00+00:00")  # another run
    audit("run_g", "gate1-skeptic", "2026-10-18T00:40:00+00:00")      # before the hand-out
    assert _errs(_state())[0]
    audit("run_g", "gate1-skeptic", "2026-10-18T01:05:00+00:00")
    assert _errs(_state()) == ([], [])


def test_full_and_bugfix_builds_must_record_a_real_verdict(audit):
    audit("run_g", "gate1-skeptic", "2026-10-18T01:05:00+00:00")
    assert "no gate1_verdict" in _errs(_state(), rec={})[0][0]
    assert "SKIPPED is only for" in _errs(_state(), rec={"gate1_verdict": "SKIPPED"})[0][0]
    assert _errs(_state(), profile="trivial", rec={}) == ([], [])
    assert _errs(_state(), profile="trivial", rec={"gate1_verdict": "SKIPPED"}) == ([], [])
    assert _errs(_state(), rec={"gate1": {"verdict": "WARN"}}) == ([], [])  # legacy shape


def test_a_gate0_verdict_needs_a_gate0_spawn_during_evaluate(audit):
    art = {"understanding": {"skeptic_verdict": "SUPPORTED"}}
    audit("run_g", "gate0-skeptic", "2026-10-18T00:05:00+00:00")    # before EVALUATE began
    e, _ = _errs(_state(), stage="evaluate", rec={}, artifact=art)
    assert e and "gate0-skeptic" in e[0]
    audit("run_g", "gate0-skeptic", "2026-10-18T00:20:00+00:00")
    assert _errs(_state(), stage="evaluate", rec={}, artifact=art) == ([], [])
    assert _errs(_state(), stage="evaluate", rec={}, artifact={"understanding": {}}) == ([], [])


def test_a_run_that_spawned_nothing_is_blocked_not_waved_through(audit):
    """red-team 2026-10-18: "this run left no marker" used to pass as "no audit hook",
    which is exactly the zero-spawn run the gate exists for."""
    e, w = _errs(_state())
    assert e and "no gate1-skeptic sub-agent" in e[0] and "gate1_unverified_reason" in e[0]
    reason = {"gate1_verdict": "PASS",
              "gate1_unverified_reason": "pipeline driven from a Kiro session: no SubagentStop hook"}
    e, w = _errs(_state(), rec=reason)
    assert e == [] and "accepted on the recorded gate1_unverified_reason" in w[0]
    assert _errs(_state(), rec={"gate1_verdict": "PASS", "gate1_unverified_reason": "n/a"})[0]


def test_a_miss_is_explained_and_unprovable_cases_pass(audit, tmp_path, monkeypatch):
    audit("run_g", None, "2026-10-18T01:10:00+00:00")                 # no readable EXPERT-ID
    audit("run_newer", "gate1-skeptic", "2026-10-18T01:11:00+00:00")  # shared session
    e, _ = _errs(_state())
    assert "1 sub-agent(s) of this run completed without" in e[0] and "run_newer" in e[0]
    audit("run_g", "correctness", "2026-10-18T01:12:00+00:00")
    audit("run_g", None, "2026-10-18T01:13:00+00:00", legacy=True)    # daemon rebuilt mid-BUILD
    e, w = _errs(_state())
    assert e == [] and "predate" in w[0]
    assert _errs(_state(created_at="2026-10-16T00:00:00+00:00")) == ([], [])  # before cutover
    import config
    monkeypatch.setattr(config, "get_app_data_dir", lambda: tmp_path / "missing")
    e, w = _errs(_state())
    assert e == [] and "unreadable" in w[0]


def test_a_verdict_dropped_from_the_replaced_record_is_named(audit):
    w: list = []
    e = cli._gate_verdict_spawn_errors("run_g", _state(), "build", "bugfix", None,
                                       {"decisions": []}, w, {"gate1_verdict": "WARN"})
    assert e and "drops the gate1_verdict 'WARN'" in e[0]


def test_the_same_prompt_handed_out_again_keeps_its_first_handout_time(ws, capsys):
    root, reg = ws
    rid = "run_g_twice"
    _make_run(root, rid, "full", stages=["evaluate", "think", "plan"])
    _start(reg, capsys, rid, "build")
    first = _run_state(root, rid)["gate1_handout"]["at"]
    _start(reg, capsys, rid, "build")                       # resumed before any verdict
    assert _run_state(root, rid)["gate1_handout"]["at"] == first


def test_stage_start_does_not_skip_gate1_on_an_unbacked_verdict(audit):
    st = _state(gate1_handout={"at": HANDOUT, "sha": "s1", "gate1_fields": "{}"},
                stages=[{"stage": "build", "gate1_verdict": "PASS"}])
    audit("run_g", "correctness", "2026-10-18T01:10:00+00:00")
    assert cli._gate1_skip(st, "s1", "run_g") is None          # hand-written, never spawned
    audit("run_g", "gate1-skeptic", "2026-10-18T01:20:00+00:00")
    assert cli._gate1_skip(st, "s1", "run_g") == {"verdict": "PASS"}


def test_build_stage_complete_refuses_an_unbacked_verdict(ws, capsys, audit):
    root, reg = ws
    rid = "run_g_e2e"
    rd = _make_run(root, rid, "direct", files_touched=["backend/a.py"], created_at=AFTER)
    assert _complete(reg, capsys, rid, "plan", _plan_artifact(), {"decisions": DECISION})[0] == 0
    _write_evidence(rd, "tests/latest-tdd.json", _c2("tdd", rid, [
        {"nodeid": "tests/test_x.py::test_a", "outcome": "passed", "file": "tests/test_x.py"}]))
    _start(reg, capsys, rid, "build")
    audit(rid, "correctness", _now())                          # the run has an audit hook
    rec = {"decisions": DECISION, "exit_evidence": {"X2": "ok checked"}, "gate1_verdict": "PASS"}
    code, out = _complete(reg, capsys, rid, "build", _build_artifact(), rec)
    assert code == 1 and "no gate1-skeptic sub-agent" in json.dumps(out), out
    audit(rid, "gate1-skeptic", _now())
    code, out = _complete(reg, capsys, rid, "build", _build_artifact(), rec)
    assert code == 0, out
