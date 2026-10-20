"""A deterministic run's Gate-2 ends, re-entry included, and only the ledger and tests
judge its DELIVER (Knowledge/Designs/2026-10-19-pipeline-deterministic-convergence.md,
step 3b follow-up).

What is tested:
- AC1 termination at the CLI layer: ``gate2-next`` on real SubagentStop marker files,
  real gate2_v2 events in run.json and a real hand-out ledger in a temp app-data dir.
  R1, fix 1, a DELIVER re-entry that grows the dispatch (Red Team) and opens a second
  stage window, R2 on the fix, prove, done. The added expert does not hold round 1
  open; it is named as a push_ready reason. A fix with no R2 answer is not done.
- AC2 re-entry hand-out: ``stage-start --stage deliver`` (real CLI subprocess, isolated
  worktree) hands spawn texts on the first entry only; on re-entry there is no legacy
  delta prompt and no spawn text, and the rule says to follow gate2-next.
- AC3 every shared branch of ``pipeline_worktree._decide_and_create`` turns a
  deterministic run legacy with ``convergence_fallback``; an isolated run stays
  deterministic.
- AC4 a deterministic DELIVER gets no "Fix by class" error at stage-complete and no
  marker re-review error in run-commit's precheck; a legacy run keeps both.
Invariants: I1 Gate-2 reaches done or a named stop; I2 deterministic implies isolated;
I3 no model-verdict or marker-count check judges a deterministic DELIVER.
"""
import json
import os
import time
from argparse import Namespace
from pathlib import Path

import pytest

from core import run_worktree as rw
from core.gate2_findings import entry_id
from scripts import artifact_cli as cli
from scripts import pipeline_worktree as pw
from tests.test_gate2_next import _iso, _marker, _next
from tests.test_pipeline_worktree import (_g, _hook_token_oracle, _start, _state,  # noqa: F401
                                          cli as run_cli, flow)

RID = "run_g2next1"   # the id test_gate2_next's _marker / _next write and read
HIGH_A = {"severity": "HIGH", "confidence": 8, "path": "a.py", "line": 3, "summary": "leaks the token"}
MED_B = {"severity": "MED", "confidence": 7, "path": "b.py", "line": 9, "summary": "races on write"}
A_ID, B_ID = entry_id("correctness", HIGH_A), entry_id("security", MED_B)


# ── AC1: the ledger reaches done through a re-entry that grows the dispatch ──────────
@pytest.fixture
def det(tmp_path, monkeypatch):
    data = tmp_path / "data"
    audit = data / "state" / "pipeline_agent_audit"
    audit.mkdir(parents=True)
    import config
    monkeypatch.setattr(config, "get_app_data_dir", lambda: data)
    monkeypatch.setattr(cli, "_round1_manifest", lambda rid, rd, rs: ({"taken_at": None, "late": False}, None))
    run_dir = tmp_path / "runs" / RID
    (run_dir / "evidence" / "proofs").mkdir(parents=True)
    (run_dir / "evidence" / "prompts").mkdir(parents=True)
    t0 = time.time() - 3600
    state = {"id": RID, "project": "SwarmAI", "created_at": _iso(t0 - 600), "profile": "direct",
             "convergence": "deterministic", "requirement": "r",
             "dispatch_plan": {"deliver": ["correctness", "security", "meta-review"]},
             "stage_timing": {"deliver": {"started_at": _iso(t0)}}}
    (run_dir / "run.json").write_text(json.dumps(state))
    monkeypatch.setattr(cli, "_load_run_for_stage",
                        lambda p, r: (run_dir / "run.json", json.loads((run_dir / "run.json").read_text())))

    def snap(rid, rd, rs, label):
        p = rd / "evidence" / "proofs" / "snapshots.json"
        cur = json.loads(p.read_text()) if p.exists() else {}
        cur.setdefault(label, {"sha": ("s1" if label == "R1" else "s2") * 20})
        p.write_text(json.dumps(cur))
        return {"sha": label}
    monkeypatch.setattr(cli, "_take_proof_snapshot", snap)
    monkeypatch.setattr(cli, "_run_worktree_path", lambda rid, rs: tmp_path / "wt")
    snap(RID, run_dir, state, "R1")
    return audit, run_dir, t0


def _hand_out(run_dir, *ids):
    """The CLI's round-1 hand-out: the prompt files, recorded in the protected ledger."""
    paths = []
    for i in ids:
        p = run_dir / "evidence" / "prompts" / f"{i}.md"
        p.write_text(f"review as {i}")
        paths.append(p)
    assert cli._ledger_add(RID, paths) == []


def _edit(run_dir, mutate):
    f = run_dir / "run.json"
    d = json.loads(f.read_text())
    mutate(d)
    f.write_text(json.dumps(d))


def test_a_reentry_that_adds_the_red_team_still_reaches_done(det, capsys):
    audit, run_dir, t0 = det
    _hand_out(run_dir, "correctness", "security", "meta-review")
    out = _next(capsys)
    assert out["next"]["action"] == "spawn" and out["next"]["round"] == 1
    _marker(audit, 1, "correctness", t0 + 10, [HIGH_A])
    _marker(audit, 2, "security", t0 + 11, [MED_B])
    out = _next(capsys)
    assert out["next"]["action"] == "fix"
    time.sleep(1.1)   # the ledger stamps whole seconds: the fix comes after the hand-out
    out = _next(capsys, test=[f"{A_ID}=backend/tests/test_t.py::a",
                              f"{B_ID}=backend/tests/test_t.py::b"], fixed=1)
    assert out["next"]["action"] == "spawn" and out["next"]["round"] == 2
    # a fix with no R2 answer is not done, and it lowers push_ready (Gate-1 note 2)
    assert any("not finished (R2 review not answered)" in r for r in out["push_ready_reasons"])

    # DELIVER re-entry: the diff grew past the Red Team threshold, a second window opens
    time.sleep(1.1)
    _hand_out(run_dir, "red-team")

    def reenter(d):
        d["dispatch_plan"]["deliver"] = ["correctness", "security", "red-team", "meta-review"]
        d["stage_timing"]["deliver"] = {
            "first_started_at": _iso(t0), "started_at": _iso(time.time() - 1),
            "prior_windows": [{"started_at": _iso(t0), "completed_at": _iso(time.time() - 2)}]}
    _edit(run_dir, reenter)
    roster, *_ = cli._gate2_v2_inputs(RID, run_dir, json.loads((run_dir / "run.json").read_text()))
    assert roster == ["correctness", "security"]
    out = _next(capsys)
    assert out["next"]["action"] == "spawn" and out["next"]["round"] == 2
    assert [s["id"] for s in out["next"]["spawns"]] == ["correctness", "security"]

    now = time.time()
    _marker(audit, 3, "correctness", now + 1, [])
    _marker(audit, 4, "security", now + 2, [])
    _marker(audit, 5, "red-team", now + 3, [])   # spawned by hand anyway: not a reviewer
    out = _next(capsys)
    assert out["next"]["action"] == "prove", out
    (run_dir / "evidence" / "proofs" / "prove-R1-20261020T000001.json").write_text(json.dumps(
        {"snapshot": "s1" * 20, "proofs": [
            {"nodeid": n, "old": ["assert_fail", "assert_fail"], "new": "pass", "verdict": "valid"}
            for n in ("backend/tests/test_t.py::a", "backend/tests/test_t.py::b")]}))
    out = _next(capsys)
    assert out["next"]["action"] == "done", out
    assert {f["id"]: f["state"] for f in out["findings"]} == {A_ID: "proven", B_ID: "proven"}
    assert out["push_ready_reasons"] == [
        "gate2: red-team joined DELIVER after round 1 was fixed: not reviewed"]
    assert not any("ignored" in w for w in out["warnings"])


def test_no_dispatch_growth_gives_no_late_reason(det, capsys):
    audit, run_dir, t0 = det
    _hand_out(run_dir, "correctness", "security")
    _marker(audit, 1, "correctness", t0 + 10, [])
    _marker(audit, 2, "security", t0 + 11, [])
    out = _next(capsys)
    assert out["next"]["action"] == "done" and out["push_ready_reasons"] == []


# ── AC2: a re-entered deterministic DELIVER hands out no spawn of its own ─────────────
@pytest.fixture
def appdata(monkeypatch):
    """A private app-data dir for the CLI subprocesses (markers, ledger, seals), short
    enough for the hook-token socket path; the token oracle is restarted on it."""
    import shutil
    import tempfile
    from core import hook_token
    d = Path(tempfile.mkdtemp(prefix="sw", dir="/tmp"))
    monkeypatch.setenv("SWARM_DATA_DIR", str(d))
    hook_token.stop_oracle()
    assert hook_token.start_oracle(50000 + os.getpid() % 10000) or not hook_token.oracle_supported()
    yield d
    hook_token.stop_oracle()
    shutil.rmtree(d, ignore_errors=True)


def test_deliver_reentry_hands_out_no_spawn(flow, appdata):
    rc, out = run_cli("run-create", "--project", "P", "--requirement", "x", "--profile",
                      "direct", "--convergence", "deterministic")
    assert rc == 0, out
    rid = out["pipeline_id"]
    f = flow["ws"] / "Projects" / "P" / ".artifacts" / "runs" / rid / "run.json"
    assert _start(flow, rid, "plan")[0] == 0
    assert _state(f)["convergence"] == "deterministic"
    wt = rw.wt_path(rid, "swarmai")
    (wt / "feature.py").write_text("".join(f"def f{i}():\n    return {i}\n" for i in range(30)))
    _g(wt, "add", "feature.py")
    rc, first = _start(flow, rid, "deliver")
    assert rc == 0, first
    assert first["experts"] and all(e.get("spawn") for e in first["experts"])
    assert first["gate2_flow"]["snapshot_R1"].get("sha"), first["gate2_flow"]
    # the legacy re-entry case: an expert answered round 1 and the change grew since,
    # so _reentry_delta would hand out <id>-delta.md prompts (correctness R1 finding)
    audit = appdata / "state" / "pipeline_agent_audit"
    audit.mkdir(parents=True, exist_ok=True)
    (audit / "session_t_1.marker").write_text(json.dumps(
        {"ts": time.time(), "event": "SubagentStop", "run_id": rid, "expert_id": "correctness",
         "reported": [], "reported_state": "no_findings"}))
    (wt / "feature.py").write_text("".join(f"def f{i}():\n    return {i}\n" for i in range(60)))
    rc, again = _start(flow, rid, "deliver")
    assert rc == 0, again
    assert "gate2_reentry" not in again
    prompts = f.parent / "evidence" / "prompts"
    assert not list(prompts.glob("*-delta.md")) and not (prompts / "reentry.diff").exists()
    assert again["experts"] and not any(e.get("spawn") for e in again["experts"])
    assert "meta_review_spawn" not in again
    assert again["gate2_flow"]["snapshot_R1"].get("already")
    assert "spawn nothing" in again["gate2_flow"]["rule"]


# ── AC3: a deterministic run is isolated, or it is legacy ─────────────────────────────
def test_switch_off_makes_a_deterministic_run_legacy(flow, tmp_path, monkeypatch):
    monkeypatch.setenv("SWARM_WORKTREE_MODE", "off")
    rc, out = run_cli("run-create", "--project", "P", "--requirement", "x", "--profile",
                      "direct", "--convergence", "deterministic")
    rid = out["pipeline_id"]
    f = flow["ws"] / "Projects" / "P" / ".artifacts" / "runs" / rid / "run.json"
    rc, out = _start(flow, rid, "plan", token=False)
    assert rc == 0, out
    st = _state(f)
    assert st["worktree_mode"] == "shared" and st["convergence"] == "legacy"
    assert "worktree mode is off" in st["convergence_fallback"]
    assert any("legacy Gate-2 flow" in w for w in out["warnings"])


def test_hookless_failure_records_the_fallback(flow, tmp_path, monkeypatch):
    rc, out = run_cli("run-create", "--project", "P", "--requirement", "x", "--profile",
                      "direct", "--convergence", "deterministic")
    rid = out["pipeline_id"]
    f = flow["ws"] / "Projects" / "P" / ".artifacts" / "runs" / rid / "run.json"
    for _ in range(3):
        rc, out = _start(flow, rid, "plan", token=False)
    st = _state(f)
    assert st["status"] == "failed" and st["worktree_mode"] == "shared"
    assert st["convergence"] == "legacy" and "hooks" in st["convergence_fallback"]


def _decide(tmp_path, monkeypatch, state, *, repos, token_env, create=None):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    run_file = run_dir / "run.json"
    run_file.write_text(json.dumps(state))
    monkeypatch.setattr(rw, "mode_enabled", lambda: True)
    monkeypatch.setattr(rw, "code_repos", lambda ws, p, s: repos)
    monkeypatch.setattr(pw, "_token", lambda *a: (True, ""))
    monkeypatch.setattr(rw, "stamp_workspace", lambda *a: None)
    if create:
        monkeypatch.setattr(rw, "create", create)
    for k in ("SWARM_APP_SESSION_ID", pw.hook_token.TOKEN_ENV):
        monkeypatch.delenv(k, raising=False)
    if token_env:
        monkeypatch.setenv("SWARM_APP_SESSION_ID", "s1")
    extra: dict = {}
    mode = pw._decide_and_create(run_id="run_d", stage="plan", run_file=run_file,
                                 run_state=dict(state), run_dir=run_dir, workspace=tmp_path,
                                 self_repo=tmp_path, extra=extra)
    return mode, json.loads(run_file.read_text()), extra


def test_hookless_executor_with_no_repo_is_legacy(tmp_path, monkeypatch):
    mode, st, extra = _decide(tmp_path, monkeypatch, {"convergence": "deterministic"},
                              repos=[], token_env=False)
    assert mode == "shared" and st["convergence"] == "legacy"
    assert "hookless" in st["convergence_fallback"]


def test_worktree_create_error_is_legacy(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise rw.WorktreeError("dirty_main", "main has conflicts")
    mode, st, extra = _decide(tmp_path, monkeypatch, {"convergence": "deterministic"},
                              repos=[("swarmai", tmp_path)], token_env=True, create=boom)
    assert mode == "shared" and st["convergence"] == "legacy"
    assert "dirty_main" in st["convergence_fallback"]


def test_a_legacy_run_records_no_fallback(tmp_path, monkeypatch):
    mode, st, extra = _decide(tmp_path, monkeypatch, {"convergence": "legacy"},
                              repos=[], token_env=False)
    assert mode == "shared" and st["convergence"] == "legacy"
    assert "convergence_fallback" not in st
    assert not any("legacy Gate-2 flow" in w for w in extra.get("warnings", []))


def test_an_isolated_run_stays_deterministic(flow, tmp_path, monkeypatch):
    rc, out = run_cli("run-create", "--project", "P", "--requirement", "x", "--profile",
                      "direct", "--convergence", "deterministic")
    rid = out["pipeline_id"]
    f = flow["ws"] / "Projects" / "P" / ".artifacts" / "runs" / rid / "run.json"
    assert _start(flow, rid, "plan")[0] == 0
    st = _state(f)
    assert st["worktree_mode"] == "isolated" and st["convergence"] == "deterministic"
    assert "convergence_fallback" not in st


# ── AC4: no fix-by-class and no marker re-review check for a deterministic DELIVER ────
_FINDING = {"severity": "HIGH", "confidence": 9, "summary": "x", "resolved": True}


def _candidate(convergence):
    art = {"adversarial_review": {"findings": [dict(_FINDING)], "profile_tier": "full"}}
    run = {"id": "run_v", "profile": "full", "convergence": convergence,
           "created_at": "2026-10-20T00:00:00+00:00", "stages": []}
    return art, run


@pytest.mark.parametrize("convergence,expect", [("legacy", True), ("deterministic", False)])
def test_fix_by_class_only_for_legacy(monkeypatch, convergence, expect):
    import design_spec
    calls = []
    real = design_spec.stage_errors

    def spy(stage, artifact, **kw):
        calls.append(stage)
        return real(stage, artifact, **kw)
    monkeypatch.setattr(design_spec, "stage_errors", spy)
    monkeypatch.setattr(cli, "_stage_artifact", lambda *a: None)
    art, run = _candidate(convergence)
    errors, _ = cli._validate_stage_candidate(
        project="P", run_id="run_v", stage="deliver", profile="full", artifact=art,
        record_in={}, cand_rec={"stage": "deliver"}, cand_run=run)
    assert (calls == ["deliver"]) is expect
    assert any("Fix by class" in e for e in errors) is expect


@pytest.mark.parametrize("convergence,expect", [("legacy", ["correctness"]), ("deterministic", [])])
def test_marker_rereview_check_only_for_legacy(monkeypatch, convergence, expect):
    calls = []

    def spawns(rid, since, until):
        calls.append(rid)
        return {"by_expert": {"security": 1}}
    monkeypatch.setattr(cli, "_completed_spawns", spawns)
    rs = {"convergence": convergence, "dispatch_plan": {"deliver": ["correctness", "security"]}}
    warnings: list = []
    assert cli._stale_gate2_reviewers("run_v", rs, time.time() - 60, warnings) == expect
    assert bool(calls) is (convergence == "legacy")


# ── security R1 note: an edit after the last accepted snapshot is not reviewed ───────
def _repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _g(repo, "init", "-q", "-b", "main")
    _g(repo, "config", "user.name", "T")
    _g(repo, "config", "user.email", "t@example.com")
    (repo / "a.py").write_text("a = 1\n")
    (repo / "b.py").write_text("b = 1\n")
    _g(repo, "add", "-A")
    _g(repo, "commit", "-q", "-m", "init")
    return repo


def test_an_edit_after_the_accepted_snapshot_lowers_push_ready(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    rid = "run_abcdef12"
    (repo / "a.py").write_text("a = 2\n")
    snaps = {"R1": rw.snapshot_commit(repo, rid, "R1")}
    monkeypatch.setattr(cli, "_run_worktree_path", lambda r, s: repo)
    rs = {"convergence": "deterministic"}
    assert cli._gate2_unreviewed_drift(rid, rs, snaps, "done") is None
    # main moved under the run (a clean rebase): the run's own edits are unchanged
    (repo / "b.py").write_text("b = 2\n")
    _g(repo, "commit", "-q", "-m", "main moved", "--", "b.py")
    assert cli._gate2_unreviewed_drift(rid, rs, snaps, "done") is None
    # a loop-back edit after round 1: nobody reviewed it
    (repo / "a.py").write_text("a = 3\n")
    why = cli._gate2_unreviewed_drift(rid, rs, snaps, "done")
    assert why and "R1 snapshot" in why and "not reviewed" in why
    assert cli._gate2_unreviewed_drift(rid, rs, snaps, "fix") is None   # mid-fix: meant to move
    # the latest accepted point wins: a fix-2 snapshot of this code accepts it
    snaps["F2"] = rw.snapshot_commit(repo, rid, "F2")
    assert cli._gate2_unreviewed_drift(rid, rs, snaps, "done") is None


def test_the_view_names_the_drift(det, capsys, monkeypatch):
    audit, run_dir, t0 = det
    _hand_out(run_dir, "correctness", "security")
    _marker(audit, 1, "correctness", t0 + 10, [])
    _marker(audit, 2, "security", t0 + 11, [])
    monkeypatch.setattr(cli, "_gate2_unreviewed_drift",
                        lambda rid, rs, snaps, action: "gate2: drift")
    out = _next(capsys)
    assert out["next"]["action"] == "done" and out["push_ready_reasons"] == ["gate2: drift"]


def test_fix_two_takes_the_f2_snapshot(det, capsys):
    audit, run_dir, t0 = det
    high_c = {"severity": "HIGH", "confidence": 9, "path": "a.py", "line": 40, "summary": "fix broke empty"}
    c_id = entry_id("correctness", high_c)
    _hand_out(run_dir, "correctness", "security")
    _marker(audit, 1, "correctness", t0 + 10, [HIGH_A])
    _marker(audit, 2, "security", t0 + 11, [])
    _next(capsys)
    time.sleep(1.1)
    _next(capsys, test=[f"{A_ID}=backend/tests/test_t.py::a"], fixed=1)
    now = time.time()
    _marker(audit, 3, "correctness", now + 1, [high_c])
    _marker(audit, 4, "security", now + 2, [])
    out = _next(capsys)
    assert out["next"]["action"] == "fix" and out["next"]["pass"] == 2
    out = _next(capsys, test=[f"{c_id}=backend/tests/test_t.py::c"], fixed=2)
    snaps = json.loads((run_dir / "evidence" / "proofs" / "snapshots.json").read_text())
    assert set(snaps) == {"R1", "R2", "F2"}
    assert out["snapshot_F2"] == {"sha": "F2"}


# ── Gate-2 R2 findings on the drift check ────────────────────────────────────────────
def test_an_indentation_only_edit_is_drift(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    rid = "run_abcdef13"
    (repo / "a.py").write_text("def f(x):\n    if x:\n        y = 1\n    return y\n")
    snaps = {"R2": rw.snapshot_commit(repo, rid, "R2")}
    monkeypatch.setattr(cli, "_run_worktree_path", lambda r, s: repo)
    rs = {"convergence": "deterministic"}
    assert cli._gate2_unreviewed_drift(rid, rs, snaps, "done") is None
    # `return y` moves into the guard: same text, other meaning
    (repo / "a.py").write_text("def f(x):\n    if x:\n        y = 1\n        return y\n")
    why = cli._gate2_unreviewed_drift(rid, rs, snaps, "done")
    assert why and "R2 snapshot" in why


def test_a_new_untracked_file_is_drift(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    rid = "run_abcdef14"
    (repo / "a.py").write_text("a = 2\n")
    snaps = {"R1": {**rw.snapshot_commit(repo, rid, "R1"),
                    "untracked": rw.untracked_new_files(repo, rid)}}
    monkeypatch.setattr(cli, "_run_worktree_path", lambda r, s: repo)
    rs = {"convergence": "deterministic"}
    assert cli._gate2_unreviewed_drift(rid, rs, snaps, "done") is None
    (repo / "hook_new.py").write_text("import os\n")   # run-commit would stage it
    why = cli._gate2_unreviewed_drift(rid, rs, snaps, "done")
    assert why and "not reviewed" in why


def test_an_unusable_snapshot_list_is_a_reason(det, capsys):
    audit, run_dir, t0 = det
    _hand_out(run_dir, "correctness", "security")
    _marker(audit, 1, "correctness", t0 + 10, [])
    _marker(audit, 2, "security", t0 + 11, [])
    (run_dir / "evidence" / "proofs" / "snapshots.json").write_text("{not json")
    out = _next(capsys)
    assert out["next"]["action"] == "done"
    assert any("snapshots.json unusable" in r for r in out["push_ready_reasons"]), out


def test_fix_two_view_is_built_after_the_f2_snapshot(det, capsys, monkeypatch):
    audit, run_dir, t0 = det
    seen = []
    monkeypatch.setattr(cli, "_gate2_unreviewed_drift",
                        lambda rid, rs, snaps, action: seen.append(sorted(snaps)) or None)
    high_c = {"severity": "HIGH", "confidence": 9, "path": "a.py", "line": 40, "summary": "fix broke empty"}
    c_id = entry_id("correctness", high_c)
    _hand_out(run_dir, "correctness", "security")
    _marker(audit, 1, "correctness", t0 + 10, [HIGH_A])
    _marker(audit, 2, "security", t0 + 11, [])
    _next(capsys)
    time.sleep(1.1)
    _next(capsys, test=[f"{A_ID}=backend/tests/test_t.py::a"], fixed=1)
    now = time.time()
    _marker(audit, 3, "correctness", now + 1, [high_c])
    _marker(audit, 4, "security", now + 2, [])
    _next(capsys)
    seen.clear()
    _next(capsys, test=[f"{c_id}=backend/tests/test_t.py::c"], fixed=2)
    assert seen and seen[-1] == ["F2", "R1", "R2"]
