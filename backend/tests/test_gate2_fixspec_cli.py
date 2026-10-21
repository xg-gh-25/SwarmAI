"""FIXING_SPEC (docs/design/pipeline-anti-whack-a-mole.md §7) — the artifact_cli layer.

What is tested (artifact_cli on real marker files and a real hand-out ledger in a temp
app-data dir, as in test_gate2_convergence / test_gate2_ledger_min):
- ``gate2-fixspec``: a valid spec is stored (evidence/fixing-spec-r<N>.json), rendered
  (FIXING_SPEC.md) and handed to the fixspec-skeptic (prompt bound by sha in the ledger);
  an invalid spec is refused and not counted; an unreadable marker store is ok:false
- acceptance (``_fixspec_accepted``): a bound PASS accepts, a BLOCK does not, a spec
  edited after the hand-out does not (F2); an old daemon (no verdict key) is accepted
  only through an explicit ``--unverified-reason`` after a skeptic round BOUND to the
  hand-out (D2: never a silent pass, never with no round at all)
- the 3rd submission of a round after two BLOCKs records an early root-cause trigger,
  and ``_gate2_outcome`` then hands out the root-cause review before the cap (F6); its
  SOUND sends the spec to the skeptic (never an acceptance by itself), and a later
  reopen's trigger does not take the earlier round's PASS away
- ``gate2-recheck`` goes ahead with a warning on a round whose open items no accepted
  spec covers (a skeptic verdict never stops the run), refuses only when the marker
  store is unreadable, and with an accepted spec writes the re-check prompt plus ``<expert>-recheck.map.json`` (item → cluster)
- DELIVER ``_fixspec_errors``: a re-checked round with open items and no accepted spec,
  and a fixed blocking finding in no accepted cluster (or not in the ledger), are errors;
  rounds are numbered globally (a late Red Team counts in the round it joined); a run
  created before FIXSPEC_CUTOVER gets the same as warnings
- run_docs: FIXING_SPEC.md is part of completion iff a spec exists
- the prompt: registered gate, numeric cap, the verdict line the hook parses
"""
import hashlib
import json
import time
from argparse import Namespace
from pathlib import Path
from datetime import datetime, timezone

import pytest

from core import gate2_findings as g2
from scripts import artifact_cli as cli

RID = "run_fixspec1"
HIGH_A = {"severity": "HIGH", "confidence": 8, "path": "a.py", "line": 3, "summary": "leaks the token"}
HIGH_B = {"severity": "HIGH", "confidence": 9, "path": "b.py", "line": 7, "summary": "races on write"}
LOW_D = {"severity": "LOW", "confidence": 5, "path": "c.py", "summary": "naming"}
PLAN = {"design": {"invariants": [{"id": "I1", "statement": "one writer"},
                                  {"id": "I2", "statement": "tokens never logged"}]}}


def _iso(t):
    return datetime.fromtimestamp(t, tz=timezone.utc).isoformat()


@pytest.fixture
def env(tmp_path, monkeypatch):
    data = tmp_path / "data"
    audit = data / "state" / "pipeline_agent_audit"
    audit.mkdir(parents=True)
    import config
    monkeypatch.setattr(config, "get_app_data_dir", lambda: data)
    monkeypatch.setattr(cli, "_round1_manifest", lambda rid, rd, rs: ({"taken_at": None, "late": False}, None))
    monkeypatch.setattr(cli, "GATE2_CONVERGENCE_CUTOVER", "2000-01-01T00:00:00+00:00")
    monkeypatch.setattr(cli, "FIXSPEC_CUTOVER", "2000-01-01T00:00:00+00:00")
    run_dir = tmp_path / "runs" / RID
    (run_dir / "evidence" / "prompts").mkdir(parents=True)
    t0 = time.time() - 3600
    state = {"id": RID, "created_at": _iso(t0 - 600), "profile": "full", "requirement": "Redact credentials",
             "stage_timing": {"deliver": {"started_at": _iso(t0)}}}
    (run_dir / "run.json").write_text(json.dumps(state))
    monkeypatch.setattr(cli, "_load_run_for_stage",
                        lambda p, r: (run_dir / "run.json", json.loads((run_dir / "run.json").read_text())))
    monkeypatch.setattr(cli, "_stage_artifact", lambda p, rs, st: PLAN if st == "plan" else {})
    return audit, run_dir, t0


def _state(run_dir):
    return json.loads((run_dir / "run.json").read_text())


def _marker(audit, n, expert, ts, **body):
    d = {"ts": ts, "event": "SubagentStop", "run_id": RID, "expert_id": expert}
    d.update(body)
    (audit / f"session_s_{n}.marker").write_text(json.dumps(d))


def _review(audit, t0, n=1, ts=60):
    _marker(audit, f"c{n}", "correctness", t0 + ts, reported=[HIGH_A, LOW_D], reported_state="findings")
    _marker(audit, f"s{n}", "security", t0 + ts + 1, reported=[HIGH_B], reported_state="findings")


IDS = [g2.entry_id("correctness", HIGH_A), g2.entry_id("security", HIGH_B)]


def _cluster(cid="K1", findings=None, **kw):
    c = {"id": cid, "findings": list(findings or IDS),
         "cause": {"invariant": "I2", "why": "two writers build the args; nothing owns them"},
         "scope": {"sites": ["a.py::build", "b.py::write"], "evidence": "grep -rn build_args backend -> 2 sites"},
         "solution": "every caller goes through the single builder function",
         "reuse": {"mechanism": "a.py::build", "decision": "reuse"},
         "anti_patch": "no new guard: the second writer is removed",
         "design_impact": "none",
         "break_test": "backend/tests/test_args.py::test_one_writer"}
    c.update(kw)
    return c


def _fixspec(capsys, spec, reason=None, expect_exit=False):
    ns = Namespace(project="SwarmAI", run_id=RID, data=json.dumps(spec), unverified_reason=reason)
    if expect_exit:
        with pytest.raises(SystemExit):
            cli.cmd_gate2_fixspec(ns, None)
    else:
        cli.cmd_gate2_fixspec(ns, None)
    return json.loads(capsys.readouterr().out.strip().splitlines()[-1])


def _skeptic(audit, n, _ts, prompt, verdict="PASS", with_verdict=True):
    """A fixspec-skeptic stop AFTER the hand-out (now; gate2-fixspec just ran)."""
    time.sleep(0.01)
    ts = time.time()
    reads = {str(prompt): hashlib.sha256(prompt.read_bytes()).hexdigest(),
             "<first:0>": cli._spawn_sha("fixspec-skeptic", prompt)}
    body = {"prompt_reads": reads}
    if with_verdict:
        body["verdict"] = verdict
    _marker(audit, n, "fixspec-skeptic", ts, **body)


# ── gate2-fixspec ─────────────────────────────────────────────────────────────
def test_valid_spec_is_stored_rendered_and_handed_out(env, capsys):
    audit, run_dir, t0 = env
    _review(audit, t0)
    out = _fixspec(capsys, {"clusters": [_cluster()]})
    assert out["ok"] is True and out["next"] == "skeptic" and out["round"] == 1
    spec_p = run_dir / "evidence" / "fixing-spec-r1.json"
    assert json.loads(spec_p.read_text())["clusters"][0]["id"] == "K1"
    assert "K1" in (run_dir / "FIXING_SPEC.md").read_text()
    prompt = run_dir / "evidence" / "prompts" / "fixspec-skeptic-r1.md"
    text = prompt.read_text()
    assert text.startswith("EXPERT-ID: fixspec-skeptic") and "FIXSPEC VERDICT" in text
    assert "single builder function" in text and IDS[0] in text
    assert out["spawn"] == cli._spawn_text("fixspec-skeptic", prompt)
    h = _state(run_dir)["fixspec_handout"]["1"]
    assert h["spec_sha"] == hashlib.sha256(spec_p.read_bytes()).hexdigest()
    assert cli._ledger_has(RID, prompt.name, h["sha"], latest=True)


def test_invalid_spec_is_refused_and_not_counted(env, capsys):
    audit, run_dir, t0 = env
    _review(audit, t0)
    out = _fixspec(capsys, {"clusters": [_cluster(findings=IDS[:1])]}, expect_exit=True)
    assert out["ok"] is False and IDS[1] in out["error"]
    assert not (run_dir / "evidence" / "fixing-spec-r1.json").exists()
    assert not _state(run_dir).get("fixspec_submissions")


def test_unreadable_marker_store_is_not_ok(env, capsys):
    audit, run_dir, t0 = env
    audit.rmdir()
    out = _fixspec(capsys, {"clusters": [_cluster()]}, expect_exit=True)
    assert out["ok"] is False and "marker" in out["error"]


# ── acceptance ────────────────────────────────────────────────────────────────
def test_pass_accepts_block_does_not_and_an_edit_voids_it(env, capsys):
    audit, run_dir, t0 = env
    _review(audit, t0)
    _fixspec(capsys, {"clusters": [_cluster()]})
    prompt = run_dir / "evidence" / "prompts" / "fixspec-skeptic-r1.md"
    assert cli._fixspec_accepted(RID, run_dir, _state(run_dir), 1)[0] is False   # not answered
    _skeptic(audit, "k1", t0 + 100, prompt, "BLOCK")
    assert cli._fixspec_accepted(RID, run_dir, _state(run_dir), 1)[0] is False
    _skeptic(audit, "k2", t0 + 110, prompt, "PASS")              # a re-spawn on the same spec
    ok, _spec, why = cli._fixspec_accepted(RID, run_dir, _state(run_dir), 1)
    assert ok is False and "answered BLOCK" in why                 # no verdict shopping
    assert _fixspec(capsys, {"clusters": [_cluster()]})["next"] == "revise"


def test_pass_accepts_and_an_edit_voids_it(env, capsys):
    audit, run_dir, t0 = env
    _review(audit, t0)
    _fixspec(capsys, {"clusters": [_cluster()]})
    _skeptic(audit, "k2", t0 + 110, run_dir / "evidence" / "prompts" / "fixspec-skeptic-r1.md", "PASS")
    ok, spec, _why = cli._fixspec_accepted(RID, run_dir, _state(run_dir), 1)
    assert ok is True and spec["clusters"][0]["id"] == "K1"
    st = json.loads((run_dir / "evidence" / "fixing-spec-status.json").read_text()) if (
        run_dir / "evidence" / "fixing-spec-status.json").is_file() else {}
    cli._fixspec_write_doc(run_dir, _state(run_dir))
    st = json.loads((run_dir / "evidence" / "fixing-spec-status.json").read_text())
    assert st["1"].startswith("accepted")
    assert "**Status:** accepted" in (run_dir / "FIXING_SPEC.md").read_text()
    p = run_dir / "evidence" / "fixing-spec-r1.json"
    p.write_text(p.read_text().replace("single builder", "one builder"))     # edited after PASS
    ok, _s, why = cli._fixspec_accepted(RID, run_dir, _state(run_dir), 1)
    assert ok is False and "changed" in why


def test_old_daemon_needs_an_explicit_unverified_reason(env, capsys):
    audit, run_dir, t0 = env
    _review(audit, t0)
    spec = {"clusters": [_cluster()]}
    _fixspec(capsys, spec)
    prompt = run_dir / "evidence" / "prompts" / "fixspec-skeptic-r1.md"
    _skeptic(audit, "k1", t0 + 100, prompt, with_verdict=False)
    ok, _s, why = cli._fixspec_accepted(RID, run_dir, _state(run_dir), 1)
    assert ok is False and "--unverified-reason" in why
    out = _fixspec(capsys, spec, reason="daemon predates verdict capture")
    assert out["ok"] is True and out["accepted"] is True and out["warnings"]
    assert len(_state(run_dir)["fixspec_submissions"]) == 1        # the same spec: no new submission
    assert cli._fixspec_accepted(RID, run_dir, _state(run_dir), 1)[0] is True


def test_unverified_reason_needs_a_round_that_read_this_hand_out(env, capsys):
    audit, run_dir, t0 = env
    _review(audit, t0)
    spec = {"clusters": [_cluster()]}
    _fixspec(capsys, spec)
    other = run_dir / "evidence" / "prompts" / "other.md"
    other.write_text("not the hand-out")
    _skeptic(audit, "k1", t0 + 100, other, with_verdict=False)       # read something else
    out = _fixspec(capsys, spec, reason="old daemon", expect_exit=True)
    assert out["ok"] is False and "read this hand-out" in out["error"]


def test_unverified_reason_is_refused_when_the_daemon_records_verdicts(env, capsys):
    audit, run_dir, t0 = env
    _review(audit, t0)
    spec = {"clusters": [_cluster()]}
    _fixspec(capsys, spec)
    prompt = run_dir / "evidence" / "prompts" / "fixspec-skeptic-r1.md"
    _skeptic(audit, "k1", t0 + 100, prompt, "BLOCK")
    out = _fixspec(capsys, spec, reason="I disagree", expect_exit=True)
    assert out["ok"] is False and "records verdicts" in out["error"]


def test_third_submission_after_two_blocks_hands_out_root_cause(env, capsys):
    audit, run_dir, t0 = env
    _review(audit, t0)
    for i, word in enumerate(("single", "one", "the only")):
        out = _fixspec(capsys, {"clusters": [_cluster(solution=f"every caller goes through {word} builder function")]})
        if i < 2:
            assert out["next"] == "skeptic"
            _skeptic(audit, f"k{i}", t0 + 100 + i, run_dir / "evidence" / "prompts" / "fixspec-skeptic-r1.md", "BLOCK")
    assert out["next"] == "root_cause" and "spec_not_agreed" in out["trigger"]
    req = _state(run_dir)["gate2_root_cause_request"]
    assert req["round"] == 1 and "spec_not_agreed" in req["trigger"]
    assert cli._gate2_outcome(RID, run_dir, _state(run_dir))["action"] == "root_cause"


def _root_cause_after_three_submissions(audit, run_dir, capsys, t0):
    _review(audit, t0)
    specs = [{"clusters": [_cluster(solution=f"every caller goes through {w} builder function")]}
             for w in ("single", "one", "the only")]
    for i, spec in enumerate(specs):
        out = _fixspec(capsys, spec)
        if i < 2:
            _skeptic(audit, f"k{i}", 0, run_dir / "evidence" / "prompts" / "fixspec-skeptic-r1.md", "BLOCK")
    assert out["next"] == "root_cause"
    return specs


def _root(audit, n, verdict):
    time.sleep(0.01)
    _marker(audit, n, "root-cause", time.time(), verdict=verdict)


def test_root_cause_sound_sends_the_spec_to_the_skeptic_never_accepts_it(env, capsys):
    audit, run_dir, t0 = env
    specs = _root_cause_after_three_submissions(audit, run_dir, capsys, t0)
    out = _fixspec(capsys, specs[2])                       # pending: the review is not back yet
    assert out["accepted"] is False and out["next"] == "root_cause"
    _root(audit, "r1", "SOUND")
    assert cli._fixspec_accepted(RID, run_dir, _state(run_dir), 1)[0] is False   # SOUND alone: no
    out = _fixspec(capsys, specs[2])                       # same spec: now handed to the skeptic
    assert out["next"] == "skeptic" and out["prompt_path"]
    assert cli._gate2_outcome(RID, run_dir, _state(run_dir))["action"] == "recheck"
    other = {"clusters": [_cluster(solution="every caller goes through a brand new builder")]}
    out = _fixspec(capsys, other)                          # SOUND answered the trigger: no re-fire
    assert out["next"] == "skeptic"
    _skeptic(audit, "kp", 0, Path(out["prompt_path"]), "PASS")
    assert cli._fixspec_accepted(RID, run_dir, _state(run_dir), 1)[0] is True
    # a later reopen overwrites the root-cause request: round 1's PASS still holds (AC6 stop)
    led = cli._ledger_add(RID, records={"gate2_root_cause_request": {
        "ts": time.time() + 5, "round": 2, "trigger": "reopen", "at": cli._utc_now_iso()}})
    assert not led
    assert cli._fixspec_accepted(RID, run_dir, _state(run_dir), 1)[0] is True


def test_root_cause_wrong_accepts_nothing(env, capsys):
    audit, run_dir, t0 = env
    specs = _root_cause_after_three_submissions(audit, run_dir, capsys, t0)
    _root(audit, "r1", "WRONG-APPROACH")
    assert cli._fixspec_accepted(RID, run_dir, _state(run_dir), 1)[0] is False
    assert _fixspec(capsys, specs[2])["next"] == "root_cause"     # not handed to the skeptic
    assert cli._gate2_outcome(RID, run_dir, _state(run_dir))["action"] == "sound"


def test_unverified_reason_needs_a_bound_round_never_none(env, capsys):
    audit, run_dir, t0 = env
    _review(audit, t0)
    spec = {"clusters": [_cluster()]}
    _fixspec(capsys, spec)
    for i in range(3):                                     # verdict-less rows, none after the hand-out
        _marker(audit, f"old{i}", "fixspec-skeptic", t0 + 70 + i, prompt_reads={})
    out = _fixspec(capsys, spec, reason="old daemon cap", expect_exit=True)
    assert out["ok"] is False
    _marker(audit, "noreads", "fixspec-skeptic", time.time() + 1)      # no prompt_reads key at all
    out = _fixspec(capsys, spec, reason="old daemon", expect_exit=True)
    assert out["ok"] is False and "read this hand-out" in out["error"]


def test_deliver_numbers_rounds_globally_for_a_late_reviewer(env, capsys):
    audit, run_dir, t0 = env
    _marker(audit, "c1", "correctness", t0 + 60, reported=[], reported_state="no_findings")
    _marker(audit, "c2", "correctness", t0 + 300, reported=[], reported_state="no_findings")
    _marker(audit, "r1", "red-team", t0 + 301, reported=[HIGH_A], reported_state="findings")
    _marker(audit, "r2", "red-team", t0 + 600, reported=[], reported_state="no_findings")
    errs = cli._fixspec_errors(RID, run_dir, _state(run_dir), {}, [])
    assert any("round 2" in e for e in errs) and not any("round 1" in e for e in errs)


def test_an_accepted_spec_does_not_carry_into_a_new_review_cycle(env, monkeypatch, capsys):
    audit, run_dir, t0 = env
    _review(audit, t0)
    _fixspec(capsys, {"clusters": [_cluster()]})
    _skeptic(audit, "k1", 0, run_dir / "evidence" / "prompts" / "fixspec-skeptic-r1.md", "PASS")
    assert cli._fixspec_accepted(RID, run_dir, _state(run_dir), 1)[0] is True
    later = _iso(time.time() + 30)                                   # a new round-1 manifest
    monkeypatch.setattr(cli, "_round1_manifest", lambda rid, rd, rs: ({"taken_at": later, "late": False}, None))
    ok, _s, why = cli._fixspec_accepted(RID, run_dir, _state(run_dir), 1)
    assert ok is False and "earlier review cycle" in why


def test_after_sound_skeptic_blocks_are_still_bounded(env, capsys):
    audit, run_dir, t0 = env
    _root_cause_after_three_submissions(audit, run_dir, capsys, t0)
    _root(audit, "r1", "SOUND")
    for i, w in enumerate(("alpha", "beta")):
        out = _fixspec(capsys, {"clusters": [_cluster(solution=f"every caller goes through the {w} builder")]})
        assert out["next"] == "skeptic"
        _skeptic(audit, f"kb{i}", 0, Path(out["prompt_path"]), "BLOCK")
    out = _fixspec(capsys, {"clusters": [_cluster(solution="every caller goes through the gamma builder")]})
    assert out["next"] == "root_cause" and "after the root-cause review answered SOUND" in out["trigger"]
    assert cli._gate2_outcome(RID, run_dir, _state(run_dir))["action"] == "sound"   # the loop stops


def test_the_skeptic_cap_turns_into_root_cause(env, capsys):
    from core import review_round_gate as rg
    audit, run_dir, t0 = env
    _review(audit, t0)
    for i in range(rg.cap_for("fixspec-skeptic")):
        _marker(audit, f"cap{i}", "fixspec-skeptic", t0 + 100 + i, verdict="BLOCK", prompt_reads={})
    out = _fixspec(capsys, {"clusters": [_cluster()]})
    assert out["next"] == "root_cause" and out["trigger"].startswith("skeptic_cap")


def test_convergence_errors_name_an_early_trigger():
    out = {"action": "root_cause", "early_trigger": "reopen: x", "capped": []}
    assert "reopen: x" in cli._gate2_convergence_errors(RID, None, {}, out, [])[0]
    w = []
    cli._gate2_convergence_errors(RID, None, {}, dict(out, action="sound", root_cause="SOUND", open={}), w)
    assert any("reopen: x" in x for x in w)


def test_the_same_spec_at_the_skeptic_cap_turns_into_root_cause(env, capsys):
    from core import review_round_gate as rg
    audit, run_dir, t0 = env
    _review(audit, t0)
    spec = {"clusters": [_cluster()]}
    assert _fixspec(capsys, spec)["next"] == "skeptic"
    for i in range(rg.cap_for("fixspec-skeptic")):                 # no verdict line: no BLOCK
        _marker(audit, f"nv{i}", "fixspec-skeptic", t0 + 200 + i, prompt_reads={})
    out = _fixspec(capsys, spec)                                    # the same spec again
    assert out["next"] == "root_cause" and out["trigger"].startswith("skeptic_cap")


def test_a_resubmission_keeps_the_round_spec_already_accepted(env, capsys):
    audit, run_dir, t0 = env
    _review(audit, t0)
    _fixspec(capsys, {"clusters": [_cluster()]})
    _skeptic(audit, "kp", 0, run_dir / "evidence" / "prompts" / "fixspec-skeptic-r1.md", "PASS")
    before = cli._fixspec_cluster_of(RID, run_dir, _state(run_dir), 1)
    assert before
    _fixspec(capsys, {"clusters": [_cluster(solution="every caller goes through the newer builder")]})
    assert cli._fixspec_accepted(RID, run_dir, _state(run_dir), 1)[0] is False   # not yet PASSed
    assert cli._fixspec_cluster_of(RID, run_dir, _state(run_dir), 1) == before    # fixes keep owners
    kept = run_dir / "evidence" / "fixing-spec-r1.accepted.json"
    kept.write_text(kept.read_text().replace("K1", "K9"))                         # edited copy
    assert cli._fixspec_cluster_of(RID, run_dir, _state(run_dir), 1) == {}


def test_a_recheck_map_the_ledger_does_not_hold_is_ignored(env):
    _audit, run_dir, _t0 = env
    m = run_dir / "evidence" / "prompts" / "security-recheck.map.json"
    m.write_text(json.dumps({"1": "refuted"}))
    assert cli._recheck_maps(run_dir, RID) == {}
    assert cli._ledger_add(RID, [m]) in (None, [])
    assert cli._recheck_maps(run_dir, RID) == {"security": {"1": "refuted"}}
    m.write_text(json.dumps({"1": "refuted", "2": "refuted"}))                  # edited after
    assert cli._recheck_maps(run_dir, RID) == {}


def test_recheck_refuses_when_the_marker_store_is_unreadable(env, monkeypatch, capsys):
    _audit, run_dir, _t0 = env
    monkeypatch.setattr(cli, "_gate2_cycle_rows", lambda *a, **k: None)
    _, err, _w = cli._fixspec_recheck_check(RID, run_dir, _state(run_dir))
    assert err and "unreadable" in err


def test_stage_budget_allows_the_skeptic_what_the_round_gate_allows(env, monkeypatch):
    _audit, run_dir, t0 = env
    monkeypatch.setattr(cli, "_spawns_in_windows", lambda rid, ws: {
        "by_expert": {"fixspec-skeptic": 4, "correctness": 4}})
    out = cli._stage_budget(RID, run_dir, _state(run_dir), "deliver", {})
    assert "fixspec-skeptic" not in out["missing"] and "correctness" in out["missing"]


def test_resubmissions_without_skeptic_blocks_never_force_root_cause(env, capsys):
    audit, run_dir, t0 = env
    _review(audit, t0)
    for w in ("single", "one", "the only", "a sole"):            # 4 specs, no skeptic answered
        out = _fixspec(capsys, {"clusters": [_cluster(solution=f"every caller goes through {w} builder function")]})
        assert out["next"] == "skeptic"


# ── gate2-recheck chokepoint ──────────────────────────────────────────────────
def _recheck(run_dir, monkeypatch, capsys, expect_exit=False):
    monkeypatch.setattr(cli, "_gate2_fix_diff", lambda rd, scope: ("diff --git a/a.py\n+x\n", ["a.py"], []))
    monkeypatch.setattr(cli, "_changed_files_scope", lambda ft: {"changed_files": [{"path": "a.py"}]})
    monkeypatch.setattr(cli, "_red_team_with_recheck", lambda *a, **k: {})
    findings = [dict(HIGH_A, specialist="correctness"), dict(HIGH_B, specialist="security")]
    ns = Namespace(project="SwarmAI", run_id=RID, findings=json.dumps(findings))
    if expect_exit:
        with pytest.raises(SystemExit):
            cli.cmd_gate2_recheck(ns, None)
    else:
        cli.cmd_gate2_recheck(ns, None)
    return json.loads(capsys.readouterr().out.strip().splitlines()[-1])


def _prompts(run_dir):
    p = run_dir / "evidence" / "prompts"
    for sid in ("correctness", "security"):
        (p / f"{sid}.md").write_text(f"EXPERT-ID: {sid}\nreview\n\n## The change\nx\n")


def test_recheck_without_an_accepted_spec_goes_ahead_and_warns(env, monkeypatch, capsys):
    audit, run_dir, t0 = env
    _review(audit, t0)
    _prompts(run_dir)
    out = _recheck(run_dir, monkeypatch, capsys)
    assert out["ok"] is True and {r["id"] for r in out["recheck"]} == {"correctness", "security"}
    assert any("push_ready will be false" in w and "gate2-fixspec" in w for w in out["warnings"])


def test_recheck_refuses_when_the_round_cannot_be_computed(env, monkeypatch, capsys):
    audit, run_dir, t0 = env
    _review(audit, t0)
    _prompts(run_dir)
    monkeypatch.setattr(cli, "_fixspec_round", lambda *a, **k: None)
    out = _recheck(run_dir, monkeypatch, capsys, expect_exit=True)
    assert out["ok"] is False and "unreadable" in out["error"]


def test_recheck_with_an_accepted_spec_maps_items_to_clusters(env, monkeypatch, capsys):
    audit, run_dir, t0 = env
    _review(audit, t0)
    _prompts(run_dir)
    _fixspec(capsys, {"clusters": [_cluster()]})
    _skeptic(audit, "k1", t0 + 100, run_dir / "evidence" / "prompts" / "fixspec-skeptic-r1.md")
    out = _recheck(run_dir, monkeypatch, capsys)
    assert out["ok"] is True and {r["id"] for r in out["recheck"]} == {"correctness", "security"}
    p = run_dir / "evidence" / "prompts"
    assert json.loads((p / "correctness-recheck.map.json").read_text()) == {"1": "K1"}
    assert "K1" in (p / "correctness-recheck.md").read_text()


def test_recheck_before_the_cutover_warns_instead_of_refusing(env, monkeypatch, capsys):
    audit, run_dir, t0 = env
    monkeypatch.setattr(cli, "FIXSPEC_CUTOVER", "2999-01-01T00:00:00+00:00")
    _review(audit, t0)
    _prompts(run_dir)
    out = _recheck(run_dir, monkeypatch, capsys)
    assert out["ok"] is True and any("push_ready will be false" in w for w in out["warnings"])


# ── DELIVER stage-complete ────────────────────────────────────────────────────
def _fixed(fid, f, sid):
    return dict(f, id=fid, specialist=sid, disposition="fixed", resolved=True)


def test_deliver_refuses_a_rechecked_round_without_an_accepted_spec(env, capsys):
    audit, run_dir, t0 = env
    _review(audit, t0)
    _marker(audit, "c2", "correctness", t0 + 300, reported=[], reported_state="no_findings")
    art = {"adversarial_review": {"findings": [_fixed(IDS[0], HIGH_A, "correctness")]}}
    errs = cli._fixspec_errors(RID, run_dir, _state(run_dir), art, [])
    assert any("round 1" in e and "gate2-fixspec" in e for e in errs)


def test_deliver_refuses_a_fixed_finding_outside_every_accepted_cluster(env, capsys):
    audit, run_dir, t0 = env
    _review(audit, t0)
    _fixspec(capsys, {"clusters": [_cluster()]})
    _skeptic(audit, "k1", t0 + 100, run_dir / "evidence" / "prompts" / "fixspec-skeptic-r1.md")
    _marker(audit, "c2", "correctness", t0 + 300, reported=[], reported_state="no_findings")
    art = {"adversarial_review": {"findings": [_fixed(IDS[0], HIGH_A, "correctness"),
                                               _fixed(IDS[1], HIGH_B, "security")]}}
    assert cli._fixspec_errors(RID, run_dir, _state(run_dir), art, []) == []
    z = dict(HIGH_A, path="z.py")
    _marker(audit, "a1", "api-contract", t0 + 310, reported=[z], reported_state="findings")
    other = g2.entry_id("api-contract", z)
    art["adversarial_review"]["findings"].append(_fixed(other, dict(HIGH_A, path="z.py"), "api-contract"))
    errs = cli._fixspec_errors(RID, run_dir, _state(run_dir), art, [])
    assert any(other in e for e in errs)


def test_deliver_before_the_cutover_warns_instead_of_blocking(env, monkeypatch):
    audit, run_dir, t0 = env
    monkeypatch.setattr(cli, "FIXSPEC_CUTOVER", "2999-01-01T00:00:00+00:00")
    _review(audit, t0)
    _marker(audit, "c2", "correctness", t0 + 300, reported=[], reported_state="no_findings")
    warnings = []
    assert cli._fixspec_errors(RID, run_dir, _state(run_dir), {}, warnings) == []
    assert any("not enforced" in w and "round 1" in w for w in warnings)


def test_deliver_refuses_a_hand_written_fixed_finding_not_in_the_ledger(env, capsys):
    audit, run_dir, t0 = env
    _review(audit, t0)
    _fixspec(capsys, {"clusters": [_cluster()]})
    _skeptic(audit, "k1", t0 + 100, run_dir / "evidence" / "prompts" / "fixspec-skeptic-r1.md")
    reworded = dict(HIGH_A, line=4, specialist="correctness", resolved=True)   # not the ledger key
    errs = cli._fixspec_errors(RID, run_dir, _state(run_dir),
                               {"adversarial_review": {"findings": [reworded]}}, [])
    assert any("not in the Gate-2 ledger" in e for e in errs)
    meta = dict(HIGH_A, line=4, specialist="meta-review", resolved=True)          # not a reviewer
    assert cli._fixspec_errors(RID, run_dir, _state(run_dir),
                               {"adversarial_review": {"findings": [meta]}}, []) == []


# ── documents + prompt ────────────────────────────────────────────────────────
def test_fixing_spec_doc_is_required_iff_a_spec_exists(env, capsys):
    from core import run_docs
    audit, run_dir, t0 = env
    _e, _w, status = run_docs.completion_check(run_dir, _state(run_dir))
    assert "FIXING_SPEC.md" not in {d["name"] for d in status if d["required"]}
    _review(audit, t0)
    _fixspec(capsys, {"clusters": [_cluster()]})
    (run_dir / "FIXING_SPEC.md").unlink()
    errors, _w, status = run_docs.completion_check(run_dir, _state(run_dir))
    fx = next(d for d in status if d["name"] == "FIXING_SPEC.md")
    assert fx["required"] and fx["exists"] and errors == []


def test_fixspec_prompt_is_a_bounded_registered_gate():
    sp = cli._load_spawn_prompt_module()
    text = sp.load_gate("fixspec-skeptic")
    assert sp.has_numeric_cap(text)
    assert "FIXSPEC VERDICT: PASS" in text and "FIXSPEC VERDICT: BLOCK" in text


# ── F1-F3 of the 0ee701f0 audit ───────────────────────────────────────────────
def test_cycle_rows_stamp_the_wave_each_answer_was_handed_out_in(env):
    """F1: a row's round comes from the gate2-recheck batch it STARTED in."""
    audit, run_dir, t0 = env
    st = _state(run_dir)
    st["gate2_waves"] = [{"at": t0 + 200, "experts": ["correctness", "red-team"]}]
    (run_dir / "run.json").write_text(json.dumps(st))
    _marker(audit, "c1", "correctness", t0 + 60, started_at=t0 + 10, reported=[HIGH_A], reported_state="findings")
    _marker(audit, "r1", "red-team", t0 + 230, started_at=t0 + 205, reported=[HIGH_B], reported_state="findings")
    _marker(audit, "c2", "correctness", t0 + 300, started_at=t0 + 204, reported=[], reported_state="no_findings")
    by, _root, _start = cli._gate2_cycle_rows(RID, run_dir, _state(run_dir))
    assert [d["wave"] for d in by["correctness"]] == [1, 2] and by["red-team"][0]["wave"] == 2
    ri = cli._fixspec_round(RID, run_dir, _state(run_dir))
    assert ri["round"] == 2 and [i["expert"] for i in ri["items"]] == ["red-team"]


def test_an_unverified_spec_is_latched_as_a_verification_gap(env, capsys):
    """F2: --unverified-reason used to write run.json fixspec_unverified, which nothing
    read; push_ready reads verification_gaps."""
    audit, run_dir, t0 = env
    _review(audit, t0)
    spec = {"clusters": [_cluster()]}
    _fixspec(capsys, spec)
    _skeptic(audit, "k1", 0, run_dir / "evidence" / "prompts" / "fixspec-skeptic-r1.md", with_verdict=False)
    _fixspec(capsys, spec, reason="daemon predates verdict capture")
    gaps = _state(run_dir)["verification_gaps"]
    assert any("round 1 accepted without a skeptic verdict" in g["why"] for g in gaps)
    _fixspec(capsys, spec, reason="daemon predates verdict capture")     # idempotent
    assert len(_state(run_dir)["verification_gaps"]) == 1


def test_the_status_doc_follows_a_pass_that_lands_after_the_hand_out(env, capsys):
    """F2: the PASS arrives after the hand-out wrote fixing-spec-status.json; the next
    command reading it rewrites it (here: the same spec resubmitted)."""
    audit, run_dir, t0 = env
    _review(audit, t0)
    spec = {"clusters": [_cluster()]}
    _fixspec(capsys, spec)
    st = run_dir / "evidence" / "fixing-spec-status.json"
    assert json.loads(st.read_text())["1"].startswith("not accepted")
    _skeptic(audit, "kp", 0, run_dir / "evidence" / "prompts" / "fixspec-skeptic-r1.md", "PASS")
    assert _fixspec(capsys, spec)["accepted"] is True
    assert json.loads(st.read_text())["1"].startswith("accepted")


def test_after_sound_a_new_trigger_still_fires(env, capsys):
    """F3: SOUND settles the trigger it answered, not every later one (a reopen after it
    still goes to root_cause, and outcome() then stops the loop)."""
    audit, run_dir, t0 = env
    _root_cause_after_three_submissions(audit, run_dir, capsys, t0)
    _root(audit, "r1", "SOUND")
    out = _fixspec(capsys, {"clusters": [_cluster(design_impact="redesign")]})
    assert out["next"] == "root_cause" and out["trigger"].startswith("redesign_impact")


def test_root_cause_hand_out_says_to_rerun_gate2_recheck(env, monkeypatch, capsys):
    """F3: SOUND before the cap means recheck, so the hand-out must not say stage-complete."""
    audit, run_dir, t0 = env
    _root_cause_after_three_submissions(audit, run_dir, capsys, t0)
    out = _recheck(run_dir, monkeypatch, capsys)
    assert out.get("root_cause") is True
    assert "run gate2-recheck again" in out["warnings"][0]
