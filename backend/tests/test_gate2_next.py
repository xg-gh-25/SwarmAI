"""Gate-2 under convergence = deterministic: core.gate2_replay and ``gate2-next``
(Knowledge/Designs/2026-10-19-pipeline-deterministic-convergence.md §2.1-§2.2, step 3a).

What is tested:
- replay (pure): marker rows + declared events + prove verdicts rebuild the ledger in
  time order; an expert unparsable twice becomes an advisory; a LOW finding is a note,
  never a ledger row; a spawn the budget does not allow (R2 before the fix was
  declared, a third round) adds nothing; a forged proof in an answer is ignored
- the CLI on real marker files in a temp app-data dir (as test_gate2_fixspec_cli):
  R1 spawn texts → fix with the findings → --test / --fixed 1 takes the R2 snapshot and
  asks for R2 on the fix only → prove commands per round → done; only not-proven
  findings are push_ready reasons; the legacy gate2-fixspec / gate2-recheck refuse;
  _deliver_push_ready_bound lowers push_ready by the ledger's reasons
Invariants: no model verdict is an input; at most 2 review rounds; a finding closes
only by a valid proof.
"""
import json
import time
from argparse import Namespace
from datetime import datetime, timezone

import pytest

from core import finding_ledger as fl
from core import gate2_replay as gr
from core.gate2_findings import entry_id
from scripts import artifact_cli as cli

RID = "run_g2next1"
HIGH_A = {"severity": "HIGH", "confidence": 8, "path": "a.py", "line": 3, "summary": "leaks the token"}
MED_B = {"severity": "MED", "confidence": 7, "path": "b.py", "line": 9, "summary": "races on write"}
HIGH_C = {"severity": "HIGH", "confidence": 9, "path": "a.py", "line": 40, "summary": "fix broke empty"}
LOW_D = {"severity": "LOW", "confidence": 5, "path": "c.py", "summary": "naming"}
A_ID, B_ID, C_ID = entry_id("correctness", HIGH_A), entry_id("security", MED_B), entry_id("correctness", HIGH_C)


def _row(expert, ts, reported=None, unparsed=False):
    r = {"expert_id": expert, "ts": ts, "reported": reported or [],
         "reported_state": "unparsed" if unparsed else
         ("findings" if reported else "no_findings")}
    return r


# ── replay (pure) ────────────────────────────────────────────────────────────
def test_replay_rebuilds_the_ledger_in_time_order():
    rows = [_row("correctness", 10, [HIGH_A, LOW_D]), _row("security", 11, [MED_B]),
            _row("red-team", 12, unparsed=True), _row("red-team", 13, unparsed=True),
            _row("correctness", 30, [HIGH_C]), _row("security", 31, []), _row("red-team", 32)]
    events = [{"kind": "test", "finding": A_ID, "test": "backend/tests/test_t.py::a", "ts": 20},
              {"kind": "declare", "finding": B_ID, "declared": "untestable",
               "reason": "only under real load", "ts": 21},
              {"kind": "fixed", "pass": 1, "ts": 22},
              {"kind": "test", "finding": C_ID, "test": "backend/tests/test_t.py::c", "ts": 40},
              {"kind": "fixed", "pass": 2, "ts": 41}]
    proofs = {"R1": {"backend/tests/test_t.py::a": {"old": ["assert_fail", "assert_fail"], "new": "pass"}},
              "R2": {"backend/tests/test_t.py::c": {"old": ["pass"], "new": None}}}
    s, notes, warns = gr.replay(["correctness", "security", "red-team"], rows, events, proofs, 1)
    st = {f["id"]: fl.finding_state(f) for f in s["findings"]}
    assert st == {A_ID: "proven", B_ID: "untestable", "red-team-R1-nooutput": "open",
                  C_ID: "invalid_test"}
    assert [n["claim"] for n in notes] == ["naming"]  # LOW: a note, not a ledger row
    assert {(r["round"], r["expert"]) for r in s["spawns"]} >= {(2, "correctness")}
    assert not warns


def test_a_row_the_budget_does_not_allow_adds_nothing():
    rows = [_row("correctness", 10, [HIGH_A]),
            _row("correctness", 11, [HIGH_C])]  # an R2 answer before fix 1 was declared
    s, _n, warns = gr.replay(["correctness"], rows, [], {}, 0)
    assert [f["id"] for f in s["findings"]] == [A_ID] and len(s["spawns"]) == 1
    assert warns and "ignored" in warns[0]
    rows3 = [_row("correctness", 10, [HIGH_A]), _row("correctness", 30, []),
             _row("correctness", 50, [HIGH_C])]  # a third round
    ev = [{"kind": "fixed", "pass": 1, "ts": 20}, {"kind": "fixed", "pass": 2, "ts": 40}]
    s, _n, warns = gr.replay(["correctness"], rows3, ev, {}, 0)
    assert C_ID not in [f["id"] for f in s["findings"]] and len(s["spawns"]) == 2


def test_an_answer_cannot_carry_its_own_proof():
    forged = dict(HIGH_A, proof={"old": ["assert_fail", "assert_fail"], "new": "pass"},
                  state="proven", test="backend/tests/test_t.py::a")
    s, _n, _w = gr.replay(["correctness"], [_row("correctness", 10, [forged])], [], {}, 0)
    assert fl.finding_state(s["findings"][0]) == "open"
    assert s["findings"][0]["test"] is None  # the test comes from the author's event only


# ── the CLI ──────────────────────────────────────────────────────────────────
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
    run_dir = tmp_path / "runs" / RID
    (run_dir / "evidence" / "proofs").mkdir(parents=True)
    t0 = time.time() - 3600
    state = {"id": RID, "project": "SwarmAI", "created_at": _iso(t0 - 600), "profile": "direct",
             "convergence": "deterministic", "requirement": "r",
             "dispatch_plan": {"deliver": ["correctness", "security"]},
             "stage_timing": {"deliver": {"started_at": _iso(t0)}}}
    (run_dir / "run.json").write_text(json.dumps(state))
    monkeypatch.setattr(cli, "_load_run_for_stage",
                        lambda p, r: (run_dir / "run.json", json.loads((run_dir / "run.json").read_text())))
    snaps = []

    def fake_snap(rid, rd, rs, label):
        snaps.append(label)
        (rd / "evidence" / "proofs" / "snapshots.json").write_text(json.dumps(
            {"R1": {"sha": "s1" * 20}, **({label: {"sha": "s2" * 20}} if label == "R2" else {})}))
        return {"sha": label}
    monkeypatch.setattr(cli, "_take_proof_snapshot", fake_snap)
    monkeypatch.setattr(cli, "_run_worktree_path", lambda rid, rs: tmp_path / "wt")
    (run_dir / "evidence" / "proofs" / "snapshots.json").write_text(json.dumps({"R1": {"sha": "s1" * 20}}))
    return audit, run_dir, t0, snaps


def _marker(audit, n, expert, ts, reported=(), unparsed=False):
    d = {"ts": ts, "event": "SubagentStop", "run_id": RID, "expert_id": expert,
         "reported": list(reported),
         "reported_state": "unparsed" if unparsed else
         ("findings" if reported else "no_findings")}
    (audit / f"session_s_{n}.marker").write_text(json.dumps(d))


def _next(capsys, **kw):
    ns = Namespace(project="SwarmAI", run_id=RID, fixed=kw.get("fixed"),
                   test=kw.get("test"), declare=kw.get("declare"))
    cli.cmd_gate2_next(ns, None)
    return json.loads(capsys.readouterr().out.strip().splitlines()[-1])


def test_gate2_next_walks_the_deterministic_flow(env, capsys):
    audit, run_dir, t0, snaps = env
    out = _next(capsys)
    assert out["next"]["action"] == "spawn" and out["next"]["round"] == 1
    assert [s["id"] for s in out["next"]["spawns"]] == ["correctness", "security"]
    assert out["next"]["spawns"][0]["spawn"].startswith("EXPERT-ID: correctness\n")
    _marker(audit, 1, "correctness", t0 + 10, [HIGH_A, LOW_D])
    _marker(audit, 2, "security", t0 + 11, [MED_B])
    out = _next(capsys)
    assert out["next"]["action"] == "fix" and out["next"]["pass"] == 1
    assert {f["id"] for f in out["next"]["findings"]} == {A_ID, B_ID}
    assert out["notes_total"] == 1
    out = _next(capsys, test=[f"{A_ID}=backend/tests/test_t.py::a"],
                declare=[f"{B_ID}=untestable:only under real load"], fixed=1)
    assert snaps == ["R2"]
    assert out["next"]["action"] == "spawn" and out["next"]["round"] == 2
    # the declared fix pass is a document now (XG 2026-10-20: every stage hands one back)
    assert out["documents"] == [str(run_dir / "FIXING_SPEC.md")]
    assert A_ID in (run_dir / "FIXING_SPEC.md").read_text() and not (run_dir / "REVIEW.md").exists()
    assert "Round 2" in out["next"]["spawns"][0]["spawn"] and "s1s1" in out["next"]["spawns"][0]["spawn"]
    now = time.time()
    _marker(audit, 3, "correctness", now + 1, [])
    _marker(audit, 4, "security", now + 2, [])
    out = _next(capsys)
    assert out["next"]["action"] == "prove"
    assert out["next"]["commands"] and "--round R1 --files backend/tests/test_t.py::a" in out["next"]["commands"][0]
    (run_dir / "evidence" / "proofs" / "prove-R1-20261019T000001.json").write_text(json.dumps(
        {"snapshot": "s1" * 20, "proofs": [{"nodeid": "backend/tests/test_t.py::a",
                                            "old": ["assert_fail", "assert_fail"],
                                            "new": "pass", "verdict": "valid"}]}))
    out = _next(capsys)
    assert out["next"]["action"] == "done"
    assert out["push_ready_reasons"] == [f"gate2: {B_ID} untestable: races on write"]
    assert str(run_dir / "REVIEW.md") in out["documents"]
    assert "## Gate 2 — deterministic ledger" in (run_dir / "REVIEW.md").read_text()
    assert json.loads((run_dir / "evidence" / "gate2-v2.json").read_text())["next"]["action"] == "done"
    assert len(json.loads((run_dir / "run.json").read_text())["gate2_v2"]["events"]) == 3


def test_bad_declarations_are_refused(env, capsys):
    for kw in ({"test": ["no-equals"]}, {"test": ["X=not_a_nodeid"]},
               {"declare": ["X=untestable:"]}, {"declare": ["X=proven:yes"]}):
        with pytest.raises(SystemExit) as e:
            _next(capsys, **kw)
        assert e.value.code == 2
    assert "gate2_v2" not in json.loads((env[1] / "run.json").read_text())


def test_legacy_gate2_commands_refuse_in_the_deterministic_flow(env, capsys):
    ns = Namespace(project="SwarmAI", run_id=RID, data=None, unverified_reason=None)
    for fn in (cli.cmd_gate2_fixspec, cli.cmd_gate2_recheck):
        with pytest.raises(SystemExit) as e:
            fn(ns, None)
        assert e.value.code == 1
        assert "gate2-next" in capsys.readouterr().out


def test_gate2_next_refuses_a_legacy_run(env, capsys):
    rd = env[1]
    st = json.loads((rd / "run.json").read_text())
    st.pop("convergence")
    (rd / "run.json").write_text(json.dumps(st))
    with pytest.raises(SystemExit):
        _next(capsys)
    assert "legacy" in capsys.readouterr().out


def test_push_ready_bound_is_lowered_by_unproven_findings(env, monkeypatch):
    audit, run_dir, t0, _s = env
    _marker(audit, 1, "correctness", t0 + 10, [HIGH_A])
    _marker(audit, 2, "security", t0 + 11, [])
    import pipeline_validator as pv
    monkeypatch.setattr(pv, "load_test_evidence", lambda rd: [])
    monkeypatch.setattr(pv, "push_ready_upper_bound", lambda *a, **k: (True, []))
    st = json.loads((run_dir / "run.json").read_text())
    bound, reasons, _w = cli._deliver_push_ready_bound(
        project="SwarmAI", run_id=RID, run_dir=run_dir, run_state=st, profile="direct",
        artifact={}, exit_result={"met": True})
    assert bound is False
    assert any(A_ID in r and "open" in r for r in reasons)
    st.pop("convergence")
    bound, reasons, _w = cli._deliver_push_ready_bound(
        project="SwarmAI", run_id=RID, run_dir=run_dir, run_state=st, profile="direct",
        artifact={}, exit_result={"met": True})
    assert not any(r.startswith("gate2:") for r in reasons)


def test_take_proof_snapshot_on_a_real_run_worktree(tmp_path, monkeypatch):
    """Smoke with real objects: the CLI's snapshot glue writes the runner's
    snapshots.json through pipeline_test.run_snapshot, once per round."""
    import subprocess
    from core import run_worktree as rw
    tmp = tmp_path.resolve()
    main = tmp / "swarmai"
    (main / "backend").mkdir(parents=True)

    def g(*a):
        subprocess.run(["git", "-C", str(main), *a], check=True, capture_output=True)
    g("init", "-q", "-b", "main")
    for k, v in (("user.name", "T"), ("user.email", "t@e.com"), ("core.hooksPath", str(tmp))):
        g("config", k, v)
    (main / "backend" / "m.py").write_text("X = 1\n")
    g("add", "-A")
    g("commit", "-q", "-m", "i")
    monkeypatch.setenv("SWARM_WORKTREE_ROOT", str(tmp / "wt"))
    rid = "run_g2snap1"
    e = rw.create(rid, "swarmai", main)
    run_dir = tmp / "runs" / rid
    run_dir.mkdir(parents=True)
    st = {"worktrees": [dict(e, state="active")]}
    (rw.wt_path(rid, "swarmai") / "backend" / "m.py").write_text("X = 2\n")
    first = cli._take_proof_snapshot(rid, run_dir, st, "R1")
    again = cli._take_proof_snapshot(rid, run_dir, st, "R1")
    snaps = json.loads((run_dir / "evidence" / "proofs" / "snapshots.json").read_text())
    assert first.get("sha") == snaps["R1"]["sha"] and again.get("already") is True
    assert cli._take_proof_snapshot(rid, run_dir, {"worktrees": []}, "R2").get("error")



# ── review round fixes (one pass): each test fails on the code before the fix ─────
def test_an_early_fix_event_is_ignored_and_lowers_push_ready():
    """--fixed 1/2 before any expert answered must not throw the answers away."""
    ev = [{"kind": "fixed", "pass": 1, "ts": 5}, {"kind": "fixed", "pass": 2, "ts": 6}]
    s, _n, warns = gr.replay(["security"], [_row("security", 10, [HIGH_A])], ev, {}, 0)
    assert [f["round"] for f in s["findings"]] == [1]  # the answer is an R1 finding
    assert fl.next_action(s)["action"] == "fix"
    assert sum("fixed event ignored" in w for w in warns) == 2


def test_fixed_is_refused_unless_the_ledger_asks_for_it(env, capsys):
    with pytest.raises(SystemExit) as e:
        _next(capsys, fixed=1)  # right after stage-start: R1 not answered
    assert e.value.code == 2 and "spawn" in capsys.readouterr().out
    assert "gate2_v2" not in json.loads((env[1] / "run.json").read_text())


def test_replay_warnings_lower_push_ready(env, capsys):
    audit, run_dir, t0, _s = env
    st = json.loads((run_dir / "run.json").read_text())
    st["gate2_v2"] = {"events": [{"kind": "fixed", "pass": 1, "ts": t0 + 1}]}
    (run_dir / "run.json").write_text(json.dumps(st))
    _marker(audit, 1, "correctness", t0 + 10, [])
    _marker(audit, 2, "security", t0 + 11, [])
    out = _next(capsys)
    assert out["next"]["action"] == "done"
    assert any("fixed event ignored" in r for r in out["push_ready_reasons"])


def test_proofs_merge_over_every_prove_call(tmp_path):
    pdir = tmp_path
    (pdir / "snapshots.json").write_text(json.dumps({"R1": {"sha": "aa"}}))
    ok = {"old": ["assert_fail", "assert_fail"], "new": "pass", "verdict": "valid"}
    calls = [("1", "aa", [{"nodeid": "n::a", **ok}]),
             ("2", "aa", [{"nodeid": "n::b", **ok}]),
             ("3", "aa", [{"nodeid": "n::a", "old": ["assert_fail"], "new": None,
                           "verdict": "pending"}]),
             ("4", "bb", [{"nodeid": "n::b", "old": ["pass"], "new": "pass",
                           "verdict": "invalid"}])]
    import os
    for i, (stamp, snap, rows) in enumerate(calls):
        f = pdir / f"prove-R1-{stamp}.json"
        f.write_text(json.dumps({"snapshot": snap, "proofs": rows}))
        os.utime(f, (1000 + i, 1000 + i))
    got = cli._merged_proofs(pdir)["R1"]
    assert got["n::a"] == {"old": ok["old"], "new": "pass"}  # a later pending row loses
    assert got["n::b"] == {"old": ok["old"], "new": "pass"}  # another snapshot's row is ignored


def test_a_registered_test_is_normalized_like_the_runner(env, capsys):
    audit, run_dir, t0, _s = env
    _marker(audit, 1, "correctness", t0 + 10, [HIGH_A])
    _marker(audit, 2, "security", t0 + 11, [])
    _next(capsys, test=[f"{A_ID}=./backend/tests/test_t.py::a "])
    ev = json.loads((run_dir / "run.json").read_text())["gate2_v2"]["events"]
    assert ev[-1]["test"] == "backend/tests/test_t.py::a"
    with pytest.raises(SystemExit) as e:
        _next(capsys, test=[f"{A_ID}=backend/core/x.py::a"])
    assert e.value.code == 2


def test_meta_review_is_not_a_gate2_reviewer(env, capsys):
    rd = env[1]
    st = json.loads((rd / "run.json").read_text())
    st["dispatch_plan"]["deliver"] += ["meta-review", "root-cause"]
    (rd / "run.json").write_text(json.dumps(st))
    out = _next(capsys)
    assert [s["id"] for s in out["next"]["spawns"]] == ["correctness", "security"]


def test_rows_are_read_over_the_whole_run(env, monkeypatch):
    """A re-entered DELIVER continues the run's ledger: rows since the FIRST window."""
    audit, run_dir, t0, _s = env
    st = json.loads((run_dir / "run.json").read_text())
    st["stage_timing"]["deliver"] = {
        "prior_windows": [{"started_at": _iso(t0), "completed_at": _iso(t0 + 100)}],
        "started_at": _iso(t0 + 500)}
    seen = []
    monkeypatch.setattr(cli, "_gate2_window_rows",
                        lambda rid, w, since: seen.append(since) or [])
    cli._gate2_v2_inputs(RID, run_dir, st)
    assert seen and abs(seen[0] - t0) < 1


def test_an_answer_without_a_captured_state_is_not_clean():
    row = {"expert_id": "security", "ts": 10, "reported": []}  # no reported_state
    s, _n, _w = gr.replay(["security"], [row], [], {}, 0)
    assert s["outputs"]["1"]["security"]["ok"] is False
