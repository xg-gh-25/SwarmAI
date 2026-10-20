"""End-to-end tests of artifact_cli's per-run worktree flow (scripts/pipeline_worktree.py;
docs/design/pipeline-run-worktree.md §15 acceptance 1, 5, 6, 10, 11, 14, 16).

Methodology: a temp SwarmWS whose project binds a temp git repo (``source_workspace``),
the real artifact_cli run as a subprocess (so the per-command run lock and the hook
token are exercised exactly as in production), worktree/ledger roots in tmp_path.
The hook's part is simulated with ``hook_token.issue`` + ``SWARM_HOOK_TOKEN``.
Landing is driven in-process through ``pipeline_worktree.land_deliver`` with
evidence written in the runner's format.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
CLI = BACKEND / "scripts" / "artifact_cli.py"
sys.path.insert(0, str(BACKEND / "scripts"))

from core import hook_token  # noqa: E402
from core import run_worktree as rw  # noqa: E402


def _g(cwd, *args):
    p = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    return p.stdout.strip()


@pytest.fixture(autouse=True)
def _hook_token_oracle():
    """The daemon's token service runs in this test process; the CLI subprocesses burn
    their tokens through its socket, exactly as in production."""
    hook_token.stop_oracle()
    assert hook_token.start_oracle(50000 + os.getpid() % 10000) or not hook_token.oracle_supported()
    yield
    hook_token.stop_oracle()


@pytest.fixture
def flow(tmp_path, monkeypatch):
    tmp = Path(os.path.realpath(tmp_path))
    main = tmp / "src" / "swarmai"
    main.mkdir(parents=True)
    (tmp / "hooks").mkdir()
    _g(main, "init", "-q", "-b", "main")
    for k, v in (("user.name", "T"), ("user.email", "t@example.com"),
                 ("core.hooksPath", str(tmp / "hooks"))):
        _g(main, "config", k, v)
    (main / "a.py").write_text("a = 1\n")
    _g(main, "add", "-A")
    _g(main, "commit", "-q", "-m", "init")
    ws = tmp / "ws"
    (ws / "Projects" / "P").mkdir(parents=True)
    (ws / "Projects" / "P" / "bindings.yaml").write_text(
        "governed_assets:\n  - kind: code-repo\n    name: swarmai\n"
        f"    source_workspace: {main}\n")
    env = {"SWARM_WORKSPACE": str(ws), "SWARM_WORKTREE_MODE": "on",
           "SWARM_WORKTREE_ROOT": str(tmp / "wt"), "SWARM_WORKTREE_LEDGER": str(tmp / "ledger"),
           "SWARM_APP_SESSION_ID": "s1"}
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv(hook_token.TOKEN_ENV, raising=False)
    return {"main": main, "ws": ws, "tmp": tmp}


def cli(*args, token_for=None, run=None, flow=None, env=None):
    e = dict(os.environ, **(env or {}))
    if token_for:
        rd = flow["ws"] / "Projects" / "P" / ".artifacts" / "runs" / run
        e[hook_token.TOKEN_ENV] = hook_token.issue(rd, e.get("SWARM_APP_SESSION_ID"), run, token_for)
    p = subprocess.run([sys.executable, str(CLI), *args], capture_output=True, text=True,
                       cwd=str(BACKEND), env=e)
    line = (p.stdout.strip().splitlines() or [p.stderr.strip()])[-1]
    try:
        return p.returncode, json.loads(line)
    except ValueError:
        return p.returncode, {"raw": p.stdout + p.stderr}


def _create(flow):
    rc, out = cli("run-create", "--project", "P", "--requirement", "x", "--profile", "direct")
    assert rc == 0, out
    rid = out["pipeline_id"]
    return rid, flow["ws"] / "Projects" / "P" / ".artifacts" / "runs" / rid / "run.json"


def _state(f):
    return json.loads(Path(f).read_text())


def _start(flow, rid, stage, token=True):
    if stage in ("build", "deliver"):
        _plan_done(flow, rid)
    return cli("stage-start", "--project", "P", "--run-id", rid, "--stage", stage,
               token_for="stage-start" if token else None, run=rid, flow=flow)


def _plan_done(flow, rid):
    """These tests are about worktrees, not PLAN: a direct run must finish PLAN before
    BUILD / DELIVER start (artifact_cli._direct_plan_missing), so a started PLAN is
    recorded completed here instead of writing an exit contract in every test."""
    f = flow["ws"] / "Projects" / "P" / ".artifacts" / "runs" / rid / "run.json"
    if not f.exists():
        return
    st = _state(f)
    recs = [r for r in st.get("stages") or [] if isinstance(r, dict) and r.get("stage") == "plan"]
    if recs and recs[-1].get("status") in ("completed", "skipped"):
        return
    if recs:
        recs[-1]["status"] = "completed"
    else:
        st.setdefault("stages", []).append({"stage": "plan", "status": "completed"})
    f.write_text(json.dumps(st))


def test_first_plan_start_creates_validated_worktree(flow):
    rid, f = _create(flow)
    rc, out = _start(flow, rid, "plan")
    assert rc == 0, out
    st = _state(f)
    assert st["worktree_mode"] == "isolated"
    wt = rw.wt_path(rid, "swarmai")
    assert wt.is_dir() and st["worktrees"][0]["base"] == _g(flow["main"], "rev-parse", "HEAD")
    rc, out = _start(flow, rid, "build", token=False)
    assert rc == 0 and out["worktrees"] == [str(wt)] and "worktree" in out["worktree_rule"]


def test_hookless_start_fails_the_run(flow):
    rid, f = _create(flow)
    for _ in range(2):  # a hooked session's gate may fail open once: retryable
        rc, out = _start(flow, rid, "plan", token=False)
        assert rc == 1 and out["code"] == "hookless_session" and _state(f)["status"] == "running"
    rc, out = _start(flow, rid, "plan", token=False)
    assert rc == 1 and out["code"] == "hookless_session"
    assert _state(f)["status"] == "failed" and not rw.wt_path(rid, "swarmai").exists()


def test_busy_cancel_is_applied_by_the_next_lock_holder(flow):
    rid, f = _create(flow)
    assert _start(flow, rid, "plan")[0] == 0
    (f.parent / ".cancel_requested").write_text("now")
    rc, out = _start(flow, rid, "build")
    assert rc == 1 and out["code"] == "run_ended"
    st = _state(f)
    assert st["status"] == "cancelled" and st["worktrees"][0]["state"] == "released"
    assert not (f.parent / ".cancel_requested").exists()


def test_inherited_env_without_hook_cannot_reuse_token(flow):
    rid, f = _create(flow)
    rd = f.parent
    nonce = hook_token.issue(rd, "s1", rid, "stage-start")
    assert hook_token.consume(rd, nonce, "s1", rid, "stage-start")[0]
    rc, out = cli("stage-start", "--project", "P", "--run-id", rid, "--stage", "plan",
                  env={hook_token.TOKEN_ENV: nonce})
    assert rc == 1 and out["code"] == "hookless_session"


def test_switch_off_keeps_shared_mode(flow, monkeypatch):
    monkeypatch.setenv("SWARM_WORKTREE_MODE", "off")
    rid, f = _create(flow)
    rc, _ = _start(flow, rid, "plan", token=False)
    assert rc == 0 and _state(f)["worktree_mode"] == "shared"
    assert not rw.wt_path(rid, "swarmai").exists()


def test_deliver_start_blocks_untracked_then_syncs(flow):
    rid, f = _create(flow)
    assert _start(flow, rid, "plan")[0] == 0
    wt = rw.wt_path(rid, "swarmai")
    (wt / "a.py").write_text("a = 2\n")
    (wt / "foo_helper.py").write_text("x = 1\n")
    rc, out = _start(flow, rid, "deliver")
    assert rc == 1 and out["code"] == "untracked_new_files"
    assert out["files"] == {"swarmai": ["foo_helper.py"]}
    _g(wt, "add", "foo_helper.py")
    (flow["main"] / "b.py").write_text("b = 1\n")
    _g(flow["main"], "add", "b.py")
    _g(flow["main"], "commit", "-q", "-m", "moved")
    rc, out = _start(flow, rid, "deliver")
    assert rc == 0, out
    assert out["sync"] == {"swarmai": "rebased"} and "rerun the pipeline test runner" in out["sync_note"]
    st = _state(f)
    assert st["worktrees"][0]["base"] == _g(flow["main"], "rev-parse", "HEAD")
    assert sorted(out["files_touched"]) == sorted([str(wt / "a.py"), str(wt / "foo_helper.py")])
    tree = json.loads((f.parent / "evidence" / "prompts" / "review-tree.json").read_text())
    assert tree["trees"]["swarmai"] == rw.change_tree(wt, rid)


def test_run_commit_refuses_and_completion_blocks_while_unintegrated(flow):
    rid, f = _create(flow)
    assert _start(flow, rid, "plan")[0] == 0
    (rw.wt_path(rid, "swarmai") / "a.py").write_text("a = 2\n")
    rc, _ = cli("run-commit", "--project", "P", "--run-id", rid)
    assert rc == 2
    import pipeline_worktree as pw
    assert "unintegrated change" in pw.completion_block(rid, _state(f))


def test_run_diff_mark_and_since(flow):
    rid, f = _create(flow)
    assert _start(flow, rid, "plan")[0] == 0
    wt = rw.wt_path(rid, "swarmai")
    (wt / "a.py").write_text("a = 2\n")
    rc, out = cli("run-diff", "--project", "P", "--run-id", rid, "--mark", "c1")
    assert rc == 0 and out["marks"]["swarmai"]
    (wt / "a.py").write_text("a = 3\n")
    rc, out = cli("run-diff", "--project", "P", "--run-id", rid, "--since", "c1", "--json")
    assert rc == 0 and "+a = 3" in out["diff"]["swarmai"] and "-a = 2" in out["diff"]["swarmai"]


def _runner_write(path, data):
    """Evidence as the test runner writes it (sealed: core.evidence_seal)."""
    import pipeline_test as _runner
    _runner._write_json(path, data)


def _evidence(f, rid, wt, untracked=()):
    """The runner's verify report, written through the runner's own writer: runs made
    by run-create seal their evidence (core.evidence_seal), so a hand-written report
    would not count."""
    import pipeline_test
    d = f.parent / "evidence" / "tests"
    pipeline_test._write_json(d / "latest-verify.json", {
        "schema": 1, "mode": "verify", "run_id": rid, "finished_at": "2099-01-01T00:00:00+00:00",
        "counts": {"passed": 1}, "results": [], "failures": [],
        "worktree": {"swarmai": {"tree": rw.change_tree(wt, rid), "untracked": list(untracked)}}})


def _land(flow, rid, f):
    import pipeline_worktree as pw
    os.environ[hook_token.TOKEN_ENV] = hook_token.issue(f.parent, "s1", rid, "stage-complete")
    try:
        return pw.land_deliver(run_id=rid, run_file=f, run_state=_state(f), push_ready=True,
                               workspace=flow["ws"], self_repo=flow["main"],
                               gen_b=lambda wt, files: [], requirement="do x")
    finally:
        os.environ.pop(hook_token.TOKEN_ENV, None)


def _prepared(flow):
    rid, f = _create(flow)
    assert _start(flow, rid, "plan")[0] == 0
    wt = rw.wt_path(rid, "swarmai")
    (wt / "a.py").write_text("a = 2\n")
    assert _start(flow, rid, "deliver")[0] == 0
    return rid, f, wt


def test_land_records_commit_and_maps_files_then_run_commit_is_idempotent(flow):
    rid, f, wt = _prepared(flow)
    _evidence(f, rid, wt)
    res = _land(flow, rid, f)
    sha = _g(flow["main"], "rev-parse", "HEAD")
    assert res["landed"]["swarmai"]["sha"] == sha
    st = _state(f)
    assert st["worktrees"][0]["state"] == "integrated"
    assert st["commits"][-1]["landed"] and st["commits"][-1]["files"] == ["a.py"]
    assert st["files_touched"] == [str(flow["main"] / "a.py")]
    assert "Co-Authored-By: Swarm <swarm@swarmai.dev>" in _g(flow["main"], "log", "-1", "--format=%B")
    rc, out = cli("run-commit", "--project", "P", "--run-id", rid)
    assert rc == 0 and "idempotent" in out.get("note", "") or "already landed" in json.dumps(out)


def test_land_refuses_evidence_with_untracked_files(flow):
    rid, f, wt = _prepared(flow)
    _evidence(f, rid, wt, untracked=["probe.py"])
    with pytest.raises(SystemExit):
        _land(flow, rid, f)
    assert _state(f)["worktrees"][0]["state"] == "active"


def _set_convergence(f, value):
    st = _state(f)
    st.pop("convergence", None)
    if value:
        st["convergence"] = value
    f.write_text(json.dumps(st))


def test_a_change_after_review_lands_marked_not_push_ready(flow):
    """One landing decision: tests on the landed tree decide; a change the Gate-2 prompts
    did not see only marks the commit push_ready false (legacy Gate-2 flow)."""
    rid, f, wt = _prepared(flow)
    _set_convergence(f, None)
    (wt / "a.py").write_text("a = 'after review'\n")
    _evidence(f, rid, wt)
    res = _land(flow, rid, f)
    assert res["landed"]["swarmai"]["sha"] == _g(flow["main"], "rev-parse", "HEAD")
    c = _state(f)["commits"][-1]
    assert c["push_ready"] is False and "not all reviewed" in c["push_ready_reason"]


def test_deterministic_run_has_no_reviewed_tree_gap(flow):
    """convergence=deterministic (a direct run's default): a finding is closed by its
    break test and R2 reviews the fix, so code changed after a review is no gap."""
    rid, f, wt = _prepared(flow)
    assert _state(f)["convergence"] == "deterministic"   # the direct default
    (wt / "a.py").write_text("a = 'after review'\n")
    _evidence(f, rid, wt)
    _land(flow, rid, f)
    c = _state(f)["commits"][-1]
    assert "not all reviewed" not in str(c.get("push_ready_reason") or ""), c


def test_an_old_verify_is_not_rebound_by_a_newer_tdd_run(flow):
    """verify ran on the old tree; after an edit only a narrow tdd ran on the new tree:
    the binding report is still the verify, so landing is refused (rerun verify)."""
    rid, f, wt = _prepared(flow)
    _evidence(f, rid, wt)                          # latest-verify.json on tree T1
    (wt / "a.py").write_text("a = 'edited after verify'\n")
    d = f.parent / "evidence" / "tests"
    tdd = json.loads((d / "latest-verify.json").read_text())
    tdd.update(mode="tdd", finished_at="2099-01-02T00:00:00+00:00",
               worktree={"swarmai": {"tree": rw.change_tree(wt, rid), "untracked": []}})
    _runner_write(d / "latest-tdd.json", tdd)
    with pytest.raises(SystemExit):
        _land(flow, rid, f)
    assert _state(f)["worktrees"][0]["state"] == "active"


def test_land_refuses_without_runner_evidence_and_on_red_tests(flow):
    rid, f, wt = _prepared(flow)
    with pytest.raises(SystemExit):           # no runner report for this run
        _land(flow, rid, f)
    assert _state(f)["worktrees"][0]["state"] == "active"
    _evidence(f, rid, wt)
    p = f.parent / "evidence" / "tests" / "latest-verify.json"
    ev = json.loads(p.read_text())
    ev["failures"] = [{"nodeid": "t::x", "classification": "regression", "outcome": "failed"}]
    _runner_write(p, ev)
    with pytest.raises(SystemExit):           # a test the change broke
        _land(flow, rid, f)
    assert _state(f)["worktrees"][0]["state"] == "active"
    assert _g(flow["main"], "log", "--oneline").count("\n") == 0


def test_terminal_update_releases_with_token(flow):
    rid, f, wt = _prepared(flow)
    rc, out = cli("run-update", "--project", "P", "--run-id", rid, "--status", "abandoned",
                  "--reason", "test teardown of the run", token_for="run-update", run=rid, flow=flow)
    assert rc == 0, out
    st = _state(f)
    assert st["worktrees"][0]["state"] == "released" and st["worktrees"][0]["ledger"]
    assert not wt.exists()


def test_terminal_update_without_token_only_requests_release(flow):
    rid, f, wt = _prepared(flow)
    rc, out = cli("run-update", "--project", "P", "--run-id", rid, "--status", "abandoned",
                  "--reason", "test teardown of the run")
    assert rc == 0, out
    assert _state(f)["release_requested"] is True and wt.exists()


def test_tool_copy_inside_worktree_refuses(flow):
    rid, f = _create(flow)
    assert _start(flow, rid, "plan")[0] == 0
    copy = rw.wt_path(rid, "swarmai") / "artifact_cli.py"
    copy.write_text("import sys; sys.path.insert(0, %r)\nimport pipeline_worktree as p\n"
                    "p.refuse_if_worktree_copy(__file__)\nprint('ran')\n" % str(BACKEND / "scripts"))
    p = subprocess.run([sys.executable, str(copy)], capture_output=True, text=True,
                       cwd=str(BACKEND), env=dict(os.environ, PYTHONPATH=str(BACKEND)))
    assert p.returncode == 2 and "ran" not in p.stdout


def test_two_repo_landing_records_both(flow):
    """Every repo's entry is looked up afresh after each landing (review MED-9)."""
    other = flow["tmp"] / "src" / "other"
    other.mkdir(parents=True)
    _g(other, "init", "-q", "-b", "main")
    for k, v in (("user.name", "T"), ("user.email", "t@example.com"),
                 ("core.hooksPath", str(flow["tmp"] / "hooks"))):
        _g(other, "config", k, v)
    (other / "o.py").write_text("o = 1\n")
    _g(other, "add", "-A")
    _g(other, "commit", "-q", "-m", "init")
    b = flow["ws"] / "Projects" / "P" / "bindings.yaml"
    b.write_text(b.read_text() + f"  - kind: code-repo\n    name: other\n    source_workspace: {other}\n")
    rid, f = _create(flow)
    assert _start(flow, rid, "plan")[0] == 0
    (rw.wt_path(rid, "swarmai") / "a.py").write_text("a = 2\n")
    (rw.wt_path(rid, "other") / "o.py").write_text("o = 2\n")
    assert _start(flow, rid, "deliver")[0] == 0
    _evidence(f, rid, rw.wt_path(rid, "swarmai"))
    res = _land(flow, rid, f)
    assert set(res["landed"]) == {"other", "swarmai"}
    st = _state(f)
    assert {e["repo_name"]: e["state"] for e in st["worktrees"]} == {"other": "integrated",
                                                                    "swarmai": "integrated"}
    assert (other / "o.py").read_text() == "o = 2\n"
    assert len([c for c in st["commits"] if c.get("landed")]) == 2


def test_a_missing_review_lands_marked_not_push_ready(flow):
    import pipeline_worktree as pw
    rid, f, wt = _prepared(flow)
    _evidence(f, rid, wt)
    os.environ[hook_token.TOKEN_ENV] = hook_token.issue(f.parent, "s1", rid, "stage-complete")
    try:
        res = pw.land_deliver(run_id=rid, run_file=f, run_state=_state(f), push_ready=True,
                              workspace=flow["ws"], self_repo=flow["main"],
                              gen_b=lambda wt, files: [], requirement="x",
                              precheck=lambda at: ["security has not reviewed"])
    finally:
        os.environ.pop(hook_token.TOKEN_ENV, None)
    assert res["landed"]["swarmai"]["sha"]
    c = _state(f)["commits"][-1]
    assert c["push_ready"] is False and "security has not reviewed" in c["push_ready_reason"]


# ── known-issue fixes (design §16.8) ─────────────────────────────────────────
def test_stage_start_overrides_the_card_steps(flow):
    rid, f = _create(flow)
    assert _start(flow, rid, "plan")[0] == 0
    for stage, word in (("build", "files_touched"), ("deliver", "run-commit")):
        rc, out = _start(flow, rid, stage)
        assert rc == 0 and word in out["card_override"], out


def test_concurrent_landings_one_wins_and_the_other_resyncs(flow):
    """§15 item 3: two runs land at once — ff-only lets exactly one through, the other
    gets needs_resync with main untouched, then syncs and lands on top."""
    import threading
    runs = []
    for fname in ("a.py", "c.py"):
        rid, f = _create(flow)
        assert _start(flow, rid, "plan")[0] == 0
        wt = rw.wt_path(rid, "swarmai")
        (wt / fname).write_text("v = 2\n")
        if fname == "c.py":
            _g(wt, "add", "c.py")
        runs.append((rid, f, wt, _state(f)["worktrees"][0]))
    out = {}

    def go(rid, f, wt, e):
        try:
            out[rid] = rw.land(rid, e, flow["main"], "m\n\nCo-Authored-By: Swarm <swarm@swarmai.dev>",
                               rw.change_tree(wt, rid), still_live=lambda: True)["sha"]
        except rw.WorktreeError as err:
            out[rid] = err.code
    ts = [threading.Thread(target=go, args=r) for r in runs]
    for t in ts:
        t.start()
    for t in ts:
        t.join(120)
    shas = [v for v in out.values() if len(v) == 40]
    loser = [v for v in out.values() if len(v) != 40]
    assert len(shas) == 1 and loser[0] in ("busy", "needs_resync"), out
    assert _g(flow["main"], "rev-parse", "HEAD") == shas[0]
    rid, f, wt, e = next(r for r in runs if len(out[r[0]]) != 40)
    assert _g(wt, "rev-parse", "HEAD") == e["base"]          # back to the invariant
    assert rw.sync(rid, e, flow["main"])["status"] == "rebased"
    sha2 = rw.land(rid, e, flow["main"], "m2", rw.change_tree(wt, rid))["sha"]
    assert _g(flow["main"], "rev-parse", "HEAD") == sha2
    assert (flow["main"] / "a.py").read_text() == "v = 2\n" and (flow["main"] / "c.py").exists()
    assert len(_g(flow["main"], "log", "--format=%H").splitlines()) == 3


def test_same_file_conflict_resolved_in_worktree_then_lands(flow):
    """§15 item 4, end to end through the CLI: B conflicts with A's landed change,
    resolves in its worktree, re-syncs, retests and lands."""
    rid_a, f_a, wt_a = _prepared(flow)
    rid_b, f_b = _create(flow)
    assert _start(flow, rid_b, "plan")[0] == 0
    wt_b = rw.wt_path(rid_b, "swarmai")
    (wt_b / "a.py").write_text("a = 3\n")
    _evidence(f_a, rid_a, wt_a)
    assert _land(flow, rid_a, f_a)["landed"]["swarmai"]["sha"]
    rc, out = _start(flow, rid_b, "deliver")
    assert rc == 1 and out["code"] == "conflict" and out["unmerged"] == ["a.py"], out
    (wt_b / "a.py").write_text("a = 23\n")
    _g(wt_b, "add", "a.py")
    rc, out = _start(flow, rid_b, "deliver")
    assert rc == 0 and out["sync"] == {"swarmai": "rebased"}, out
    _evidence(f_b, rid_b, wt_b)
    assert _land(flow, rid_b, f_b)["landed"]["swarmai"]["sha"] == _g(flow["main"], "rev-parse", "HEAD")
    assert (flow["main"] / "a.py").read_text() == "a = 23\n"
    assert len(_g(flow["main"], "log", "--format=%H").splitlines()) == 3


def _stale_release(flow, f):
    import time as _t
    rep = rw.sweep(flow["ws"], owner_active=lambda sid: False, now=_t.time() + 15 * 86400)
    assert rep["released"] and not rep["errors"], rep
    assert _state(f)["worktree_released_stale"]


def test_stale_released_worktree_is_rebuilt_exactly_on_next_start(flow):
    rid, f = _create(flow)
    assert _start(flow, rid, "plan")[0] == 0
    wt = rw.wt_path(rid, "swarmai")
    (wt / "a.py").write_text("a = 5\n")
    (wt / "new.py").write_text("n = 1\n")
    _g(wt, "add", "new.py")
    (wt / "probe.txt").write_text("p\n")
    tree = rw.change_tree(wt, rid)
    _stale_release(flow, f)
    assert not wt.exists()
    import pipeline_worktree as pw
    assert "ledger" in pw.completion_block(rid, _state(f))     # never completable unlanded
    with pytest.raises(SystemExit):
        _land(flow, rid, f)
    rc, out = _start(flow, rid, "build")
    assert rc == 0 and out["worktree_restored"] == ["swarmai"], out
    assert rw.change_tree(wt, rid) == tree                     # the change set, as it was
    assert rw.untracked_new_files(wt, rid) == ["probe.txt"]    # still un-added (§6.2)
    st = _state(f)
    assert st["worktrees"][0]["state"] == "active" and "worktree_released_stale" not in st
    assert pw.completion_block(rid, st)


def test_landing_attempt_keeps_a_backing_off_run_from_the_24h_release(flow):
    import time as _t
    from datetime import datetime, timezone
    rid, f = _create(flow)
    assert _start(flow, rid, "plan")[0] == 0
    (rw.wt_path(rid, "swarmai") / "a.py").write_text("a = 2\n")
    later = _t.time() + 2 * 86400
    st = _state(f)
    st.setdefault("stages", []).append({"stage": "deliver", "status": "completed"})
    st["landing_attempt_at"] = datetime.fromtimestamp(later - 3600, timezone.utc).isoformat()
    f.write_text(json.dumps(st))
    rep = rw.sweep(flow["ws"], owner_active=lambda sid: False, now=later)
    assert not rep["released"], rep
    st.pop("landing_attempt_at")
    f.write_text(json.dumps(st))
    rep = rw.sweep(flow["ws"], owner_active=lambda sid: False, now=later)
    assert rep["released"] and "never integrated" in rep["released"][0]["reason"]


def test_released_worktree_paths_resolve_to_main_and_cache_dropped(flow, monkeypatch, tmp_path):
    import hashlib
    cache = tmp_path / "rcache"
    cache.mkdir()
    monkeypatch.setenv("SWARM_TEST_CACHE_DIR", str(cache))
    rid, f, wt = _prepared(flow)
    sg = cache / f"srcgraph-{hashlib.sha256(str(wt).encode()).hexdigest()[:16]}.json"
    sg.write_text("{}")
    _evidence(f, rid, wt)
    _land(flow, rid, f)
    assert rw.landed_path(wt / "a.py", flow["ws"]) is None     # worktree still there
    st = _state(f)
    assert rw.release(rid, st, {"swarmai": flow["main"]}, "done")[0]["ok"]
    f.write_text(json.dumps(st))
    assert not wt.exists() and not sg.exists()
    assert rw.landed_path(wt / "a.py", flow["ws"]) == flow["main"] / "a.py"
    assert rw.landed_path(wt / "nope.py", flow["ws"]) is None
    assert rw.landed_path(wt / ".." / ".." / "x", flow["ws"]) is None


def test_isolated_land_has_no_force_past_d7(flow, monkeypatch):
    import artifact_cli as ac
    rid, f, wt = _prepared(flow)
    _evidence(f, rid, wt)
    st = _state(f)
    st["required_experts"] = ["security"]
    f.write_text(json.dumps(st))
    monkeypatch.setattr(ac, "_d7_missing_required_experts", lambda req, run_id: ["security"])
    monkeypatch.setattr(ac, "_generate_review_deliverable", lambda *a, **k: ([], []))
    warns: list = []
    try:
        os.environ[hook_token.TOKEN_ENV] = hook_token.issue(f.parent, "s1", rid, "stage-complete")
        res = ac._land_isolated_deliver("P", rid, f, True, None, warns)
        assert res["landed"]   # a missing expert lowers push_ready, it never stops landing
        c = _state(f)["commits"][-1]
        assert c["push_ready"] is False and "security" in c["push_ready_reason"]
        body = _g(flow["main"], "log", "-1", "--format=%B")
        assert "NOT PUSH-READY" in body and "security" in body   # the gap is in git log too
        assert any("security" in g["why"] for g in _state(f)["verification_gaps"])  # latched
        with pytest.raises(TypeError):  # the force_reason override is gone
            ac._land_isolated_deliver("P", rid, f, True, None, warns, force_reason="x")
    finally:
        os.environ.pop(hook_token.TOKEN_ENV, None)


# ── SOUND, not converged: an isolated run lands anyway (autonomous, f2ffe9c2 twin) ──
class _Every(dict):
    def __bool__(self):
        return True

    def get(self, k, d=None):
        return 1


def _all_reviewed(ac, monkeypatch):
    """Every dispatched Gate-2 expert reviewed after the recorded tree: the precheck
    passes, so the test isolates the push_ready decision."""
    monkeypatch.setattr(ac, "_completed_spawns", lambda *a, **k: {"by_expert": _Every()})


def _land_via_cli(ac, rid, f, warns, deliver_rec):
    try:
        os.environ[hook_token.TOKEN_ENV] = hook_token.issue(f.parent, "s1", rid, "stage-complete")
        return ac._land_isolated_deliver("P", rid, f, False, None, warns, deliver_rec=deliver_rec)
    finally:
        os.environ.pop(hook_token.TOKEN_ENV, None)


def test_isolated_land_proceeds_when_only_review_did_not_converge(flow, monkeypatch):
    """push_ready false only because Gate-2 did not converge in 3 rounds with a SOUND
    root-cause verdict: the run lands (it is autonomous: never parked for a human), the
    commit is marked review_not_converged, and the record keeps push_ready false."""
    import artifact_cli as ac
    rid, f, wt = _prepared(flow)
    _evidence(f, rid, wt)
    seen = {}

    def only_review(project, run_id, run_file, run_state, deliver_rec):
        seen["rec"] = deliver_rec
        return True
    monkeypatch.setattr(ac, "_only_review_not_converged", only_review)
    monkeypatch.setattr(ac, "_generate_review_deliverable", lambda *a, **k: ([], []))
    _all_reviewed(ac, monkeypatch)
    rec = {"stage": "deliver", "push_ready": False}
    warns: list = []
    res = _land_via_cli(ac, rid, f, warns, rec)
    assert seen["rec"] is rec                       # the record being completed is checked
    assert res["landed"]["swarmai"]["sha"] == _g(flow["main"], "rev-parse", "HEAD")
    assert res["landed"]["swarmai"]["review_not_converged"] is True
    st = _state(f)
    assert st["worktrees"][0]["state"] == "integrated"
    assert st["commits"][-1]["review_not_converged"] is True
    assert any("review_not_converged" in w for w in warns)


def test_isolated_land_proceeds_marked_when_push_ready_fails_for_another_reason(flow, monkeypatch):
    """Commit-ready is not push-ready: any other push_ready failure (tests, completion
    audit, a model-lowered push_ready) still lands, every commit marked push_ready:
    false with the reason and NOT review_not_converged; push stays blocked."""
    import artifact_cli as ac
    rid, f, wt = _prepared(flow)
    _evidence(f, rid, wt)
    head = _g(flow["main"], "rev-parse", "HEAD")
    monkeypatch.setattr(ac, "_only_review_not_converged", lambda *a, **k: False)
    monkeypatch.setattr(ac, "_generate_review_deliverable", lambda *a, **k: ([], []))
    _all_reviewed(ac, monkeypatch)
    warns: list = []
    res = _land_via_cli(ac, rid, f, warns, {"stage": "deliver", "push_ready": False,
                                            "push_ready_reason": "regression red"})
    new = _g(flow["main"], "rev-parse", "HEAD")
    assert new != head and res["landed"]["swarmai"]["sha"] == new
    assert "NOT PUSH-READY: regression red" in _g(flow["main"], "log", "-1", "--format=%B")
    c = _state(f)["commits"][-1]
    assert c["push_ready"] is False and c["push_ready_reason"] == "regression red"
    assert not c.get("review_not_converged")
    assert any("do NOT push" in w for w in warns)


def test_push_ready_landing_is_not_marked_review_not_converged(flow):
    rid, f, wt = _prepared(flow)
    _evidence(f, rid, wt)
    res = _land(flow, rid, f)
    assert "review_not_converged" not in res["landed"]["swarmai"]
    assert "review_not_converged" not in _state(f)["commits"][-1]
    assert "push_ready" not in _state(f)["commits"][-1]


# ── automatic landing while main keeps moving ─────────────────────────────────
def _evidence_at_base(f, rid, wt):
    _evidence(f, rid, wt)
    p = f.parent / "evidence" / "tests" / "latest-verify.json"
    d = json.loads(p.read_text())
    d["worktree"]["swarmai"]["base"] = _g(wt, "rev-parse", "HEAD")
    _runner_write(p, d)


def test_land_resyncs_by_itself_when_main_moved_elsewhere(flow):
    """main gains a commit on another file between review and landing: the landing
    rebases, keeps the review and the tests (same patch, untouched files) and lands —
    no stage-start round, no re-review."""
    rid, f, wt = _prepared(flow)
    _evidence_at_base(f, rid, wt)
    (flow["main"] / "c.py").write_text("c = 'main'\n")
    _g(flow["main"], "add", "c.py")
    _g(flow["main"], "commit", "-q", "-m", "moved")
    res = _land(flow, rid, f)
    sha = res["landed"]["swarmai"]["sha"]
    assert _g(flow["main"], "rev-parse", "HEAD") == sha
    assert (flow["main"] / "a.py").read_text() == "a = 2\n"
    assert (flow["main"] / "c.py").read_text() == "c = 'main'\n"


def test_land_stops_when_main_moved_onto_the_runs_files(flow):
    rid, f, wt = _prepared(flow)
    _evidence_at_base(f, rid, wt)
    (flow["main"] / "a.py").write_text("a = 1\nextra = 1\n")
    _g(flow["main"], "add", "a.py")
    _g(flow["main"], "commit", "-q", "-m", "main edits a.py")
    head = _g(flow["main"], "rev-parse", "HEAD")
    with pytest.raises(SystemExit):
        _land(flow, rid, f)
    assert _g(flow["main"], "rev-parse", "HEAD") == head


def test_deliver_start_keeps_the_review_after_a_harmless_rebase(flow):
    rid, f, wt = _prepared(flow)
    _evidence_at_base(f, rid, wt)
    rt = f.parent / "evidence" / "prompts" / "review-tree.json"
    at = json.loads(rt.read_text())["at"]
    (flow["main"] / "c.py").write_text("c = 'main'\n")
    _g(flow["main"], "add", "c.py")
    _g(flow["main"], "commit", "-q", "-m", "moved")
    rc, out = _start(flow, rid, "deliver")
    assert rc == 0, out
    assert "still holds" in out["review_note"] and "still holds" in out["sync_note"]
    rec = json.loads(rt.read_text())
    assert rec["at"] == at and rec["trees"]["swarmai"] == rw.change_tree(wt, rid)


def test_landing_resync_refuses_a_tree_edited_after_the_check(flow):
    import pipeline_worktree as pw
    rid, f, wt = _prepared(flow)
    st = _state(f)
    e = st["worktrees"][0]
    checked = rw.change_tree(wt, rid)
    (wt / "a.py").write_text("a = 'late'\n")
    with pytest.raises(rw.WorktreeError) as ei:
        pw._resync_for_landing(rid, f, st, f.parent, e, wt, flow["main"], checked)
    assert ei.value.code == "changed_since_review"


def test_landing_records_carried_uncommitted_files(flow):
    """Another session's uncommitted hunk in a file the run changed: the landing merges
    it, lands, and run.json + the output name the file and the backup."""
    lines = "".join(f"l{i} = {i}\n" for i in range(1, 9))
    (flow["main"] / "m.py").write_text(lines)
    _g(flow["main"], "add", "m.py")
    _g(flow["main"], "commit", "-q", "-m", "m")
    rid, f = _create(flow)
    assert _start(flow, rid, "plan")[0] == 0
    wt = rw.wt_path(rid, "swarmai")
    (wt / "m.py").write_text(lines.replace("l2 = 2", "l2 = 'run'"))
    assert _start(flow, rid, "deliver")[0] == 0
    _evidence_at_base(f, rid, wt)
    (flow["main"] / "m.py").write_text(lines.replace("l7 = 7", "l7 = 'them'"))
    res = _land(flow, rid, f)["landed"]["swarmai"]
    assert res["carried_uncommitted"] == ["m.py"] and Path(res["carried_backup"]).is_dir()
    assert (flow["main"] / "m.py").read_text() == \
        lines.replace("l2 = 2", "l2 = 'run'").replace("l7 = 7", "l7 = 'them'")
    c = _state(f)["commits"][-1]
    assert c["carried_uncommitted"] == ["m.py"]


def test_a_red_cleared_by_a_fix_recheck_past_the_cap_lands_not_push_ready(flow):
    """The cap / landing deadlock (XG 2026-10-20): the last full-scope report is red,
    the fix changes the tree, and no new full round is allowed. The recheck overlay on
    the fixed code must clear the red AND bind the fixed tree, so the change lands, with
    push_ready false (the rest of the scope did not re-run on the final code)."""
    import pipeline_test as pt
    rid, f, wt = _prepared(flow)
    red, ok = "backend/tests/test_a.py::t_red", "backend/tests/test_a.py::t_ok"
    base = {"schema": 1, "mode": "regression", "run_id": rid,
            "finished_at": "2099-01-01T00:00:00+00:00", "counts": {"passed": 1, "failed": 1},
            "results": [{"nodeid": red, "outcome": "failed", "file": "backend/tests/test_a.py"},
                        {"nodeid": ok, "outcome": "passed", "file": "backend/tests/test_a.py"}],
            "failures": [{"nodeid": red, "classification": "regression", "outcome": "failed"}],
            "runner_errors": [], "layers": {}, "changed_files": {"a.py": "d1"},
            "worktree": {"swarmai": {"tree": rw.change_tree(wt, rid), "untracked": []}}}
    (wt / "a.py").write_text("a = 'fixed'\n")
    re_ev = {"results": [{"nodeid": red, "outcome": "passed", "file": "backend/tests/test_a.py"}],
             "failures": [], "runner_errors": [], "changed_files": {"a.py": "d2"},
             "worktree": {"swarmai": {"tree": rw.change_tree(wt, rid), "untracked": []}}}
    d = f.parent / "evidence" / "tests"
    _runner_write(d / "latest-regression.json", pt._overlay_evidence(Path("."), base, re_ev, []))
    res = _land(flow, rid, f)
    assert res["landed"]["swarmai"]["sha"] == _g(flow["main"], "rev-parse", "HEAD")
    c = _state(f)["commits"][-1]
    assert c["push_ready"] is False and "completed by a recheck" in c["push_ready_reason"]


def test_a_stage_without_a_summary_gets_one_from_its_artifact(flow, tmp_path):
    """XG 2026-10-20: every completed stage shows a summary. The model's own words win;
    without them the line is built from the stage artifact."""
    rid, f = _create(flow)
    assert _start(flow, rid, "plan")[0] == 0
    data = tmp_path / "plan.json"
    data.write_text(json.dumps({"artifact": {
        "approach": "one owner builds args", "change_spec": [{"id": "S1"}, {"id": "S2"}],
        "exit_criteria": [{"id": "X1", "kind": "test", "ref": "backend/tests/t.py", "expect": "pass"}]}}))
    rc, out = cli("stage-complete", "--project", "P", "--run-id", rid, "--stage", "plan",
                  "--data", f"@{data}", token_for="stage-complete", run=rid, flow=flow)
    assert rc == 0, out
    rec = [s for s in _state(f)["stages"] if s["stage"] == "plan"][-1]
    assert rec["summary"] == "one owner builds args · 2 changes · 1 exit criterion"
    assert rec["derived_sources"]["summary"] == "stage artifact"
    assert _start(flow, rid, "build", token=False)[0] == 0, "build stage did not open"
    data.write_text(json.dumps({"artifact": {"files_changed": ["a.py"]},
                                "record": {"summary": "my own words"}}))
    rc, out = cli("stage-complete", "--project", "P", "--run-id", rid, "--stage", "build",
                  "--data", f"@{data}", token_for="stage-complete", run=rid, flow=flow)
    rec = [s for s in _state(f)["stages"] if s["stage"] == "build"]
    assert rec and rec[-1]["summary"] == "my own words", out


def test_an_abandoned_run_still_hands_back_its_report(flow):
    rid, f = _create(flow)
    assert _start(flow, rid, "plan")[0] == 0
    rc, out = cli("run-update", "--project", "P", "--run-id", rid, "--status", "abandoned",
                  "--reason", "stopped by the user", token_for="run-update", run=rid, flow=flow)
    assert rc == 0, out
    assert (f.parent / "REPORT.md").is_file() and out.get("report") == str(f.parent / "REPORT.md")


def test_run_create_defaults_direct_to_deterministic_convergence(flow):
    """XG 2026-10-20: a direct run gets the deterministic Gate-2 flow unless it opts
    out with --convergence legacy; other profiles keep the legacy default."""
    rid, f = _create(flow)
    assert _state(f).get("convergence") == "deterministic"
    rc, out = cli("run-create", "--project", "P", "--requirement", "y", "--profile",
                  "direct", "--convergence", "legacy")
    assert rc == 0, out
    runs = flow["ws"] / "Projects" / "P" / ".artifacts" / "runs"
    assert "convergence" not in _state(runs / out["pipeline_id"] / "run.json")
    rc, out = cli("run-create", "--project", "P", "--requirement", "z", "--profile", "full")
    assert rc == 0, out
    assert "convergence" not in _state(runs / out["pipeline_id"] / "run.json")
