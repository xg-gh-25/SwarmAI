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
    return cli("stage-start", "--project", "P", "--run-id", rid, "--stage", stage,
               token_for="stage-start" if token else None, run=rid, flow=flow)


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


def _evidence(f, rid, wt, untracked=()):
    d = f.parent / "evidence" / "tests"
    d.mkdir(parents=True, exist_ok=True)
    (d / "latest-verify.json").write_text(json.dumps({
        "schema": 1, "mode": "verify", "run_id": rid, "finished_at": "2099-01-01T00:00:00+00:00",
        "counts": {}, "results": [], "failures": [],
        "worktree": {"swarmai": {"tree": rw.change_tree(wt, rid), "untracked": list(untracked)}}}))


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


def test_land_refuses_change_after_review(flow):
    rid, f, wt = _prepared(flow)
    (wt / "a.py").write_text("a = 'after review'\n")
    _evidence(f, rid, wt)
    with pytest.raises(SystemExit):
        _land(flow, rid, f)
    assert _g(flow["main"], "log", "--oneline").count("\n") == 0


def test_terminal_update_releases_with_token(flow):
    rid, f, wt = _prepared(flow)
    rc, out = cli("run-update", "--project", "P", "--run-id", rid, "--status", "abandoned",
                  token_for="run-update", run=rid, flow=flow)
    assert rc == 0, out
    st = _state(f)
    assert st["worktrees"][0]["state"] == "released" and st["worktrees"][0]["ledger"]
    assert not wt.exists()


def test_terminal_update_without_token_only_requests_release(flow):
    rid, f, wt = _prepared(flow)
    rc, out = cli("run-update", "--project", "P", "--run-id", rid, "--status", "abandoned")
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


def test_land_refuses_when_precheck_reports_missing_review(flow):
    import pipeline_worktree as pw
    rid, f, wt = _prepared(flow)
    _evidence(f, rid, wt)
    os.environ[hook_token.TOKEN_ENV] = hook_token.issue(f.parent, "s1", rid, "stage-complete")
    try:
        with pytest.raises(SystemExit):
            pw.land_deliver(run_id=rid, run_file=f, run_state=_state(f), push_ready=True,
                            workspace=flow["ws"], self_repo=flow["main"],
                            gen_b=lambda wt, files: [], requirement="x",
                            precheck=lambda at: ["security has not reviewed"])
    finally:
        os.environ.pop(hook_token.TOKEN_ENV, None)
    assert _state(f)["worktrees"][0]["state"] == "active"
