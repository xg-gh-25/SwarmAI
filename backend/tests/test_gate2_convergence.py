"""Tests for 再一再二不再三, fully autonomous (core/gate2_convergence + artifact_cli wiring).

Methodology: real SubagentStop marker files under a temp app-data dir and a real
run.json; the pure outcome function is table-tested; the artifact_cli pieces
(cycle rows, gate2-recheck, stage-complete convergence errors, redesign record,
validator deferral) are driven directly with those files.

Invariants:
- outcome: nothing open → converged; open with rounds left → recheck; open and capped
  with no verdict → root_cause; any verdict (SOUND or WRONG-APPROACH) → sound: no
  redesign, no 4th round (MAX_REDESIGNS = 0). NOT RESOLVED, a blocking report and an unparsed answer
  are open; a non-blocking report is not; an old-daemon row is unjudged
- the hook parses `ROOT-CAUSE VERDICT: SOUND | WRONG-APPROACH` (most severe wins)
- gate2-recheck: converged → no prompt; capped → writes root-cause.md (EXPERT-ID first
  line, requirement, rounds, open items) and prints its spawn; verdict present → points
  at stage-complete; never writes a round-4 re-check
- stage-complete: root_cause → E_ROOT_CAUSE_REQUIRED; WRONG-APPROACH → no error, the
  verdict in the warning; a legacy run with a recorded redesign must still redo PLAN and
  restart Gate-2;
  sound → no error, a warning; no outcome → the older checks decide (None)
- rows before the latest redesign, outside DELIVER, of another run, or adversarial
  twins are not counted; the rule applies only from its cutover
- validator: a tool-deferred finding stops blocking DELIVER/completion, never push_ready
"""
import json
import time
from argparse import Namespace
from datetime import datetime, timezone

import pytest

from core import gate2_convergence as g2c
from scripts import artifact_cli as cli

RID = "run_conv1"


def _iso(t):
    return datetime.fromtimestamp(t, tz=timezone.utc).isoformat()


def _row(answers=None, reported=None, state="findings", **kw):
    d = {"reported": reported or [], "reported_state": state, "recheck_answers": answers or {}}
    d.update(kw)
    return d


HIGH = {"severity": "HIGH", "file": "a.py", "line": 3, "finding": "leaks the token"}
LOW = {"severity": "LOW", "file": "a.py", "finding": "naming"}


# ── pure outcome ────────────────────────────────────────────────────────────
@pytest.mark.parametrize("rows,root,used,action", [
    ({}, [], 0, "none"),
    ({"correctness": [_row(state="no_findings")]}, [], 0, "converged"),
    ({"correctness": [_row(reported=[LOW])]}, [], 0, "converged"),
    ({"correctness": [_row(reported=[HIGH])]}, [], 0, "recheck"),
    ({"correctness": [_row(reported=[HIGH]), _row(answers={"1": "RESOLVED"}, state="no_findings")]},
     [], 0, "converged"),
    ({"correctness": [_row(reported=[HIGH])] * 2 + [_row(answers={"1": "NOT RESOLVED"})]},
     [], 0, "root_cause"),
    ({"red-team": [_row(reported=[HIGH])] * 3}, [{"verdict": "WRONG-APPROACH"}], 0, "sound"),
    ({"red-team": [_row(reported=[HIGH])] * 3}, [{"verdict": "WRONG-APPROACH"}], 1, "sound"),
    ({"red-team": [_row(reported=[HIGH])] * 3}, [{"verdict": "SOUND"}], 0, "sound"),
    ({"security": [_row(state="unparsed")] * 3}, [], 0, "root_cause"),
    ({"meta-review": [_row(reported=[HIGH])] * 5}, [], 0, "none"),
    # the root-cause review answered without a verdict line (verdict None) or on an old
    # daemon (no verdict key): it ran, and the round gate allows only one per cycle,
    # so the loop ends instead of asking for another root-cause review forever
    ({"red-team": [_row(reported=[HIGH])] * 3}, [{"verdict": None}], 0, "sound"),
    ({"red-team": [_row(reported=[HIGH])] * 3}, [{"ts": 1}], 0, "sound"),
])
def test_outcome_table(rows, root, used, action):
    assert g2c.outcome(rows, root, used)["action"] == action


def test_outcome_details_and_unjudged_rows():
    out = g2c.outcome({"correctness": [_row(reported=[HIGH])] * 3,
                       "integration": [{"ts": 1}]},           # old daemon: no reported_state
                      [{"verdict": "SOUND", "verdict_text": "ROOT-CAUSE VERDICT: SOUND\nedge"}], 0)
    assert out["capped"] == ["correctness"] and out["unjudged"] == ["integration"]
    assert out["open"]["correctness"] == ["[HIGH] — a.py:3 — leaks the token"]
    assert out["root_cause"] == "SOUND" and "edge" in out["root_cause_text"]


def test_hook_parses_the_root_cause_verdict():
    from core.runtime_hooks import _gate_verdict
    assert _gate_verdict("root-cause", "ROOT-CAUSE VERDICT: SOUND\nreasons") == "SOUND"
    assert _gate_verdict("root-cause", "**Root-cause verdict: wrong-approach**") == "WRONG-APPROACH"
    assert _gate_verdict("root-cause", "ROOT-CAUSE VERDICT: SOUND\nROOT-CAUSE VERDICT: "
                                       "WRONG-APPROACH") == "WRONG-APPROACH"
    assert _gate_verdict("root-cause", "I think it is sound.") is None


# ── artifact_cli wiring, on real marker files ───────────────────────────────
@pytest.fixture
def env(tmp_path, monkeypatch):
    data = tmp_path / "data"
    audit = data / "state" / "pipeline_agent_audit"
    audit.mkdir(parents=True)
    import config
    monkeypatch.setattr(config, "get_app_data_dir", lambda: data)
    monkeypatch.setattr(cli, "_round1_manifest", lambda rid, rd, rs: ({"taken_at": None, "late": False}, None))
    monkeypatch.setattr(cli, "GATE2_CONVERGENCE_CUTOVER", "2000-01-01T00:00:00+00:00")
    monkeypatch.setattr(cli, "FIXSPEC_CUTOVER", "2999-01-01T00:00:00+00:00")  # FIXING_SPEC: test_gate2_fixspec_cli
    run_dir = tmp_path / "runs" / RID
    (run_dir / "evidence" / "prompts").mkdir(parents=True)
    t0 = time.time() - 3600
    state = {"id": RID, "created_at": _iso(t0 - 600), "requirement": "Redact credentials",
             "stage_timing": {"plan": {"started_at": _iso(t0 - 500), "completed_at": _iso(t0 - 400)},
                              "deliver": {"started_at": _iso(t0)}}}
    (run_dir / "run.json").write_text(json.dumps(state))
    return audit, run_dir, state, t0


def _marker(audit, n, expert, ts, run_id=RID, adv=False, **answer):
    body = {"ts": ts, "event": "SubagentStop", "run_id": run_id, "expert_id": expert}
    body.update(answer)
    (audit / f"session_s_{'adv_' if adv else ''}{n}.marker").write_text(json.dumps(body))


def _capped(audit, t0, expert="correctness"):
    for i in range(3):
        _marker(audit, i, expert, t0 + 60 * (i + 1), **_row(reported=[HIGH]))


def test_cycle_rows_count_only_this_cycle(env):
    audit, run_dir, state, t0 = env
    _marker(audit, 1, "correctness", t0 + 60, **_row(reported=[HIGH]))
    _marker(audit, 2, "correctness", t0 - 120, **_row())                  # before DELIVER
    _marker(audit, 3, "correctness", t0 + 70, run_id="run_other", **_row())
    _marker(audit, 4, "correctness", t0 + 80, adv=True, **_row())
    _marker(audit, 5, "root-cause", t0 + 90, verdict="SOUND")
    by, root, start = cli._gate2_cycle_rows(RID, run_dir, state)
    assert list(by) == ["correctness"] and len(by["correctness"]) == 1
    assert [r["verdict"] for r in root] == ["SOUND"] and start == pytest.approx(t0, abs=1)
    state["redesigns"] = [{"ts": t0 + 85}]
    by, root, _ = cli._gate2_cycle_rows(RID, run_dir, state)
    assert by == {} and len(root) == 1                                     # floored at the redesign


def test_rule_applies_only_from_its_cutover(env, monkeypatch):
    audit, run_dir, state, t0 = env
    _capped(audit, t0)
    monkeypatch.setattr(cli, "GATE2_CONVERGENCE_CUTOVER", "2999-01-01T00:00:00+00:00")
    assert cli._gate2_outcome(RID, run_dir, state) is None


def _recheck(run_dir, state, monkeypatch, capsys):
    monkeypatch.setattr(cli, "_load_run_for_stage", lambda p, r: (run_dir / "run.json", state))
    monkeypatch.setattr(cli, "_ledger_add", lambda rid, paths: [])
    monkeypatch.setattr(cli, "_stage_artifact", lambda p, rs, st: {})
    cli.cmd_gate2_recheck(Namespace(project="SwarmAI", run_id=RID, findings="[]"), None)
    return json.loads(capsys.readouterr().out)


def test_gate2_recheck_hands_out_root_cause_after_round_3(env, monkeypatch, capsys):
    audit, run_dir, state, t0 = env
    _capped(audit, t0)
    out = _recheck(run_dir, state, monkeypatch, capsys)
    assert out["root_cause"] is True and out["capped"] == ["correctness"] and out["recheck"] == []
    prompt = (run_dir / "evidence" / "prompts" / "root-cause.md").read_text()
    assert prompt.startswith("EXPERT-ID: root-cause\n")
    for part in ("ROOT-CAUSE VERDICT: SOUND", "Redact credentials", "correctness round 3",
                 "a.py:3", "Do not fix anything"):
        assert part in prompt
    assert "Do not ask the user" in out["warnings"][0]
    assert not list((run_dir / "evidence" / "prompts").glob("*-recheck.md"))


def test_gate2_recheck_converged_and_verdict_paths(env, monkeypatch, capsys):
    audit, run_dir, state, t0 = env
    _marker(audit, 1, "correctness", t0 + 60, **_row(state="no_findings"))
    out = _recheck(run_dir, state, monkeypatch, capsys)
    assert out["converged"] is True and out["next"] == "meta-review"
    _capped(audit, t0)
    _marker(audit, 9, "root-cause", t0 + 400, verdict="SOUND")
    out = _recheck(run_dir, state, monkeypatch, capsys)
    assert out["root_cause_verdict"] == "SOUND" and out["next"].startswith("stage-complete")


def test_stage_complete_errors_per_action(env):
    audit, run_dir, state, t0 = env
    rf = run_dir / "run.json"
    assert cli._gate2_convergence_errors(RID, rf, state, None, []) is None
    _capped(audit, t0)
    out = cli._gate2_outcome(RID, run_dir, state)
    assert cli._gate2_convergence_errors(RID, rf, state, out, [])[0].startswith("E_ROOT_CAUSE_REQUIRED")

    _marker(audit, 9, "root-cause", t0 + 400, verdict="WRONG-APPROACH",
            verdict_text="ROOT-CAUSE VERDICT: WRONG-APPROACH\navoid regex redaction")
    out = cli._gate2_outcome(RID, run_dir, state)
    warns: list = []
    # 再一再二不再三: WRONG-APPROACH after round 3 is recorded, not redesigned
    assert out["action"] == "sound" and out["root_cause"] == "WRONG-APPROACH"
    assert cli._gate2_convergence_errors(RID, rf, state, out, warns) == []
    assert "WRONG-APPROACH" in warns[0] and "push_ready is false" in warns[0]
    assert not json.loads(rf.read_text()).get("redesigns")

    # a legacy run already sent back to PLAN still has to redo it
    cli._record_redesign(rf, out)
    saved = json.loads(rf.read_text())
    assert len(saved["redesigns"]) == 1
    errs = cli._gate2_convergence_errors(RID, rf, saved, cli._gate2_outcome(RID, run_dir, saved), [])
    assert errs[0].startswith("E_REDESIGN_PENDING")
    # PLAN redone, but no Gate-2 round 1 on the new change
    saved["stage_timing"]["plan"]["completed_at"] = _iso(time.time())
    errs = cli._gate2_convergence_errors(RID, rf, saved, cli._gate2_outcome(RID, run_dir, saved), [])
    assert errs[0].startswith("E_REDESIGN_UNREVIEWED")


def test_second_miss_after_redesign_is_sound(env):
    audit, run_dir, state, t0 = env
    state["redesigns"] = [{"ts": t0 + 10}]
    state["stage_timing"]["plan"]["completed_at"] = _iso(t0 + 20)
    _capped(audit, t0)
    _marker(audit, 9, "root-cause", t0 + 400, verdict="WRONG-APPROACH")
    out = cli._gate2_outcome(RID, run_dir, state)
    assert out["action"] == "sound" and out["redesigns_used"] == 1
    warns = []
    assert cli._gate2_convergence_errors(RID, run_dir / "run.json", state, out, warns) == []
    assert "WRONG-APPROACH (after a redesign)" in warns[0] and "push_ready is false" in warns[0]


def test_validator_defers_only_tool_tagged_findings_and_never_for_push_ready():
    import pipeline_validator as pv
    f_open = dict(HIGH)
    f_def = dict(HIGH, deferred_by=pv.DEFERRED_BY_ROOT_CAUSE)
    f_fake = dict(HIGH, deferred_by="me")
    assert pv._blocked_findings([f_open, f_def, f_fake]) == [f_open, f_def, f_fake]
    assert pv._blocked_findings([f_open, f_def, f_fake], honor_deferred=True) == [f_open, f_fake]
    assert pv.DEFERRED_BY_ROOT_CAUSE == cli._DEFERRED_BY


# ── end to end through the real stage-complete (direct profile) ─────────────
def test_sound_verdict_completes_deliver_with_open_items_in_report(tmp_path, monkeypatch, capsys):
    """Capped loop + SOUND root-cause verdict: DELIVER completes, the open HIGH is
    tool-deferred, push_ready is false, and REPORT §10's attention flag names it."""
    import tests.test_stage_boundary_commands as sb
    ArtifactRegistry = sb.ArtifactRegistry
    monkeypatch.setenv("SWARM_WORKSPACE", str(tmp_path))
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)
    (tmp_path / "Projects" / sb.PROJECT / ".artifacts").mkdir(parents=True)
    reg = ArtifactRegistry(tmp_path)
    data = tmp_path / "data"
    audit = data / "state" / "pipeline_agent_audit"
    audit.mkdir(parents=True)
    import config
    monkeypatch.setattr(config, "get_app_data_dir", lambda: data)
    monkeypatch.setattr(cli, "GATE2_CONVERGENCE_CUTOVER", "2000-01-01T00:00:00+00:00")
    monkeypatch.setattr(cli, "FIXSPEC_CUTOVER", "2999-01-01T00:00:00+00:00")  # FIXING_SPEC: test_gate2_fixspec_cli

    rid = "run_sound_e2e"
    rd = sb._make_run(tmp_path, rid, "direct", files_touched=["backend/a.py", "backend/tests/test_x.py"])
    assert sb._start(reg, capsys, rid, "plan")[0] == 0
    assert sb._complete(reg, capsys, rid, "plan", sb._plan_artifact(), {"decisions": sb.DECISION})[0] == 0
    sb._write_evidence(rd, "tests/latest-tdd.json", sb._c2("tdd", rid, [
        {"nodeid": "tests/test_x.py::test_a", "outcome": "passed", "file": "tests/test_x.py"}]))
    assert sb._start(reg, capsys, rid, "build")[0] == 0
    code, out = sb._complete(reg, capsys, rid, "build", sb._build_artifact(),
                             {"decisions": sb.DECISION, "exit_evidence": {"X2": "checked the page"}})
    assert code == 0, out
    assert sb._start(reg, capsys, rid, "deliver")[0] == 0
    now = time.time()
    for i in range(3):
        _marker(audit, i, "correctness", now + i, run_id=rid, **_row(reported=[HIGH]))
    _marker(audit, 9, "root-cause", now + 5, run_id=rid, verdict="SOUND",
            verdict_text="ROOT-CAUSE VERDICT: SOUND\nthe token leak is in a legacy path outside this change")
    deliver = sb._tmpl("deliver")
    deliver["adversarial_review"]["findings"] = [
        {"specialist": "correctness", "severity": "HIGH", "confidence": 8, "resolved": False,
         "file": "a.py", "line": 3, "finding": "a.py line 3: leaks the token",
         "deferred_by": "self-granted"}]
    monkeypatch.setattr(time, "time", lambda: now + 30)
    code, out = sb._complete(reg, capsys, rid, "deliver", deliver, {"exit_evidence": {"X2": "checked the page"}})
    assert code == 0, out
    rec = cli._stage_record_of(sb._run_state(tmp_path, rid), "deliver")
    assert rec["push_ready"] is False and "root-cause review: SOUND" in rec["push_ready_reason"]
    assert rec["gate2_outcome"]["action"] == "sound"
    art = sb._artifact_on_disk(tmp_path, rec["artifact_id"])
    assert art["adversarial_review"]["findings"][0]["deferred_by"] == cli._DEFERRED_BY
    flag = [f for f in art["attention_flags"] if f.startswith("[gate2] ")]
    assert flag and "legacy path" in flag[0] and "a.py:3" in flag[0]


def test_root_cause_without_a_verdict_ends_the_loop(env, monkeypatch, capsys):
    """The dead loop run_93773c32's experts found: a root-cause answer with no
    ROOT-CAUSE VERDICT line left the outcome at root_cause, so stage-complete asked for
    the review again, gate2-recheck handed the prompt out again, and review_round_gate
    (one root-cause spawn per cycle) denied it. Only SWARM_REVIEW_ROUND_GATE_FORCE got out."""
    audit, run_dir, state, t0 = env
    _capped(audit, t0)
    _marker(audit, 9, "root-cause", t0 + 400, verdict=None,
            verdict_text="I think the approach is mostly fine but the race in _rounds")
    out = cli._gate2_outcome(RID, run_dir, state)
    assert out["action"] == "sound" and out["root_cause"] == "UNPARSED"
    assert "race in _rounds" in out["root_cause_text"]
    warns: list = []
    assert cli._gate2_convergence_errors(RID, run_dir / "run.json", state, out, warns) == []
    assert "UNPARSED" in warns[0] and "push_ready is false" in warns[0]
    rec = _recheck(run_dir, state, monkeypatch, capsys)
    assert rec["root_cause_verdict"] == "UNPARSED" and rec["next"].startswith("stage-complete")
    assert rec["recheck"] == []


# ── the loop not shrinking ends it early; exits never wait on the snapshot ─────
R1 = {"1": "RESOLVED"}
NR = {"1": "NOT RESOLVED"}


@pytest.mark.parametrize("rows,root,action", [
    ({"correctness": [_row(reported=[HIGH]), _row(answers=NR)]}, [], "root_cause"),
    ({"correctness": [_row(reported=[HIGH]), _row(answers=R1, reported=[dict(HIGH, line=8)])]},
     [], "root_cause"),                                                       # fixed one, found one
    ({"correctness": [_row(reported=[HIGH, dict(HIGH, line=4)]), _row(answers=NR)]}, [], "recheck"),
    ({"correctness": [_row(reported=[HIGH]), _row(answers=NR)]}, [{"verdict": "SOUND"}], "sound"),
    ({"correctness": [_row(reported=[HIGH])]}, [], "recheck"),               # one round: nothing to compare
    # a re-ask after an unparsed answer is not a new round
    ({"correctness": [_row(state="unparsed"), _row(reported=[HIGH])]}, [], "recheck"),
    # an unparsed row between round 1 and the re-check is skipped, not the base
    ({"correctness": [_row(reported=[HIGH]), _row(state="unparsed"), _row(answers=NR)]}, [], "root_cause"),
])
def test_not_shrinking_hands_out_root_cause_before_the_cap(rows, root, action):
    assert g2c.outcome(rows, root, 0)["action"] == action


def test_stalled_sums_reviewers_with_a_recheck_round_only():
    st = g2c.stalled({"correctness": [_row(reported=[HIGH]), _row(answers=NR)],
                      "security": [_row(reported=[HIGH, dict(HIGH, line=9)])],     # one round
                      "meta-review": [_row(reported=[HIGH]), _row(answers=NR)]})
    assert st == {"experts": ["correctness"], "previous": 1, "latest": 1}
    assert g2c.stalled({"correctness": [_row(reported=[HIGH]), _row(answers=R1, state="no_findings")]}) is None


def test_late_snapshot_does_not_block_the_root_cause_exit(env, monkeypatch, capsys):
    audit, run_dir, state, t0 = env
    _capped(audit, t0)
    monkeypatch.setattr(cli, "_round1_manifest", lambda rid, rd, rs: ({"late": True}, None))
    out = _recheck(run_dir, state, monkeypatch, capsys)
    assert out["root_cause"] is True and (run_dir / "evidence" / "prompts" / "root-cause.md").is_file()
    assert "not converging" not in out["warnings"][0] and "3 rounds" in out["warnings"][0]


def test_late_snapshot_still_refuses_a_recheck(env, monkeypatch, capsys):
    audit, run_dir, state, t0 = env
    _marker(audit, 1, "correctness", t0 + 60, **_row(reported=[HIGH]))
    monkeypatch.setattr(cli, "_round1_manifest", lambda rid, rd, rs: ({"late": True}, None))
    with pytest.raises(SystemExit):
        _recheck(run_dir, state, monkeypatch, capsys)


def test_stalled_reason_is_named(env, monkeypatch, capsys):
    audit, run_dir, state, t0 = env
    _marker(audit, 1, "correctness", t0 + 60, **_row(reported=[HIGH]))
    _marker(audit, 2, "correctness", t0 + 120, **_row(answers={"1": "NOT RESOLVED"}))
    out = _recheck(run_dir, state, monkeypatch, capsys)
    assert out["root_cause"] is True and "not converging" in out["warnings"][0]
    assert "not converging" in (run_dir / "evidence" / "prompts" / "root-cause.md").read_text()


def test_reentry_is_late_only_after_this_window_reached_gate2(env, monkeypatch):
    audit, run_dir, state, t0 = env
    monkeypatch.setattr(cli, "HANDOUT_CUTOVER", "2000-01-01T00:00:00+00:00")
    led = {"files": {}}
    monkeypatch.setattr(cli, "_ledger_read", lambda rid: led)
    # the first attempt exited on a sync conflict: nothing handed out, nothing ran
    assert cli._gate2_reviewed_in_window(RID, state, ["correctness"], t0) is False
    # a hand-out from an EARLIER window does not count
    led["files"]["/x/evidence/prompts/correctness.md"] = [["sha0", _iso(t0 - 900)]]
    assert cli._gate2_reviewed_in_window(RID, state, ["correctness"], t0) is False
    led["files"]["/x/evidence/prompts/correctness.md"].append(["sha1", _iso(t0 + 30)])
    assert cli._gate2_reviewed_in_window(RID, state, ["correctness"], t0) is True
    led["files"].clear()
    _marker(audit, 1, "correctness", t0 + 60, **_row(reported=[HIGH]))      # a round ran
    assert cli._gate2_reviewed_in_window(RID, state, ["correctness"], t0) is True
    # unknowable → late (costs a re-review, never a missed fix)
    monkeypatch.setattr(cli, "_ledger_read", lambda rid: None)
    assert cli._gate2_reviewed_in_window(RID, state, ["security"], t0) is True


def test_failed_first_attempt_of_a_new_cycle_still_retakes_the_snapshot():
    closed = {"started_at": "b", "prior_windows": [{"started_at": "a", "completed_at": "a2"}]}
    assert cli._cycle_completed_at(closed, None, True, False) == "a2"     # failed attempt, retake
    assert cli._cycle_completed_at(closed, None, True, True) is None      # Gate-2 ran: keep it
    assert cli._cycle_completed_at({"started_at": "a"}, None, True, False) is None   # first window
    assert cli._cycle_completed_at(closed, "c2", False, False) == "c2"    # a normal new cycle


def test_one_round_definition_for_stall_and_fixspec():
    from core import gate2_fixspec as fs
    rows = {"correctness": [_row(reported=[HIGH]), _row(state="unparsed"), _row(answers=NR)]}
    for i, r in enumerate(rows["correctness"]):
        r["ts"] = 10.0 + i
    assert [r for r, _e, _i in fs._rounds(rows)] == [1, 2, 2]
    assert len(g2c.round_finals(rows["correctness"])) == 2
    respawn = {"correctness": [_row(reported=[HIGH], ts=1.0), _row(reported=[HIGH], ts=2.0)]}
    assert max(r for r, _e, _i in fs._rounds(respawn)) == len(g2c.round_finals(respawn["correctness"])) == 2
    # a re-asked round 2 (unparsed, then parsed) is one round 2, compared with round 1 (the
    # cap still counts the re-ask: it bounds the cost of a reviewer that never parses)
    shrinking = [_row(reported=[HIGH, dict(HIGH, line=4)]), _row(state="unparsed"), _row(answers=NR)]
    assert g2c.stalled({"correctness": shrinking}) is None
    # round 2 came back unparsed: wait for its re-ask, do not call the loop stalled
    assert g2c.stalled({"correctness": [_row(reported=[HIGH]), _row(state="unparsed")]}) is None
    assert g2c.outcome({"correctness": shrinking}, [], 0)["capped"] == ["correctness"]
