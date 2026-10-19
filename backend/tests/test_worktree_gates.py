"""Tests for the worktree PreToolUse gates: core.worktree_write_gate (§6.1) and
core.run_owner_gate (§4.2 takeover + one-time token).

Methodology: a real temp git repo + a real run worktree (core.run_worktree.create),
a temp workspace holding the run's run.json, and the gates' pure decision functions
(``judge`` / ``_decide``) called in-process; owner liveness is monkeypatched.
Invariants: an owner's writes to the main checkout are denied with the worktree twin
named; other runs' worktrees are off limits; stash / shared refs are denied from a
run worktree; a chat session's ordinary git use in main is untouched; the owner gate
rewrites exactly one simple command with a token and denies compound ones.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from core import hook_token, run_owner_gate as og, worktree_write_gate as wg
from core import run_worktree as rw

RID, OTHER = "run_own1", "run_other1"


def _g(cwd, *a):
    p = subprocess.run(["git", "-C", str(cwd), *a], capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    return p.stdout.strip()


@pytest.fixture(autouse=True)
def _hook_token_oracle():
    """The daemon's token service (in-process, as in the daemon)."""
    hook_token.stop_oracle()
    assert hook_token.start_oracle(50000 + os.getpid() % 10000) or not hook_token.oracle_supported()
    yield
    hook_token.stop_oracle()


@pytest.fixture
def env(tmp_path, monkeypatch):
    tmp = Path(os.path.realpath(tmp_path))
    main = tmp / "src" / "swarmai"
    main.mkdir(parents=True)
    _g(main, "init", "-q", "-b", "main")
    for k, v in (("user.name", "T"), ("user.email", "t@e.com"), ("core.hooksPath", str(tmp))):
        _g(main, "config", k, v)
    (main / "a.py").write_text("a = 1\n")
    _g(main, "add", "-A")
    _g(main, "commit", "-q", "-m", "i")
    monkeypatch.setenv("SWARM_WORKTREE_ROOT", str(tmp / "wt"))
    ws = tmp / "ws"
    monkeypatch.setenv("SWARM_WORKSPACE", str(ws))
    runs = {}
    for rid, sid in ((RID, "s1"), (OTHER, "s9")):
        e = rw.create(rid, "swarmai", main)
        rd = ws / "Projects" / "P" / ".artifacts" / "runs" / rid
        rd.mkdir(parents=True)
        (rd / "run.json").write_text(json.dumps({
            "id": rid, "project": "P", "status": "running", "session_id": sid,
            "worktree_mode": "isolated", "worktrees": [e]}))
        runs[rid] = rd / "run.json"
    own_r = rw.own_isolated_run("s1", ws)
    return {"main": main, "ws": ws, "runs": runs, "own": wg._Own(own_r["id"], own_r["worktrees"]),
            "wt": rw.wt_path(RID, "swarmai"), "other": rw.wt_path(OTHER, "swarmai")}


def _call(tool, cwd="", **ti):
    return {"tool_name": tool, "tool_input": ti, "cwd": cwd}


def _denied(v):
    return bool(v) and v.get("hookSpecificOutput", {}).get("permissionDecision") == "deny"


# ── write gate ────────────────────────────────────────────────────────────────
def test_owner_write_to_main_denied_with_twin(env):
    v = wg.judge(_call("Write", file_path=str(env["main"] / "a.py")), env["own"])
    assert _denied(v) and str(env["wt"] / "a.py") in v["hookSpecificOutput"]["permissionDecisionReason"]
    assert wg.judge(_call("Edit", file_path=str(env["wt"] / "a.py")), env["own"]) is None
    assert _denied(wg.judge(_call("NotebookEdit", notebook_path=str(env["main"] / "n.ipynb")),
                            env["own"]))


def test_other_runs_worktree_denied(env):
    assert _denied(wg.judge(_call("Write", file_path=str(env["other"] / "a.py")), env["own"]))


def test_bash_redirect_into_main_denied(env):
    v = wg.judge(_call("Bash", cwd=str(env["wt"]), command=f"echo x > {env['main']}/a.py"),
                 env["own"])
    assert _denied(v)
    assert wg.judge(_call("Bash", cwd=str(env["wt"]), command="echo x > a.py"), env["own"]) is None


@pytest.mark.parametrize("cmd", [
    "git -C {main} add a.py", "cd {main} && git commit -m x", "git --work-tree={main} reset",
])
def test_owner_git_writes_in_main_denied(env, cmd):
    assert _denied(wg.judge(_call("Bash", cwd=str(env["wt"]), command=cmd.format(main=env["main"])),
                            env["own"]))


@pytest.mark.parametrize("cmd", [
    "git stash", "git stash pop", "git update-ref refs/heads/main HEAD",
    "git branch -f main HEAD", "git config core.hooksPath /tmp/x", "git worktree remove ../x",
    "git push . HEAD:main",
])
def test_shared_git_state_denied_inside_any_run_worktree(env, cmd):
    assert _denied(wg.judge(_call("Bash", cwd=str(env["wt"]), command=cmd), None))


def test_git_stash_in_worktree_via_dash_c_from_main(env):
    assert _denied(wg.judge(_call("Bash", cwd=str(env["main"]),
                                  command=f"git -C {env['wt']} stash pop"), None))


@pytest.mark.parametrize("cmd,deny", [
    ("git branch -D feature", False), ("git tag v1.0", False), ("git stash", False),
    ("git config --get user.name", False), ("git branch -D run/" + OTHER, True),
])
def test_chat_session_in_main_untouched_except_run_branches(env, cmd, deny):
    v = wg.judge(_call("Bash", cwd=str(env["main"]), command=cmd), None)
    assert _denied(v) is deny


def test_own_branch_refs_allowed_in_worktree(env):
    assert wg.judge(_call("Bash", cwd=str(env["wt"]), command=f"git branch -f run/{RID} HEAD"),
                    env["own"]) is None


def test_hardlink_in_worktree_denied(env):
    target = env["wt"] / "a.py"
    target.unlink()
    os.link(env["main"] / "a.py", target)
    assert _denied(wg.judge(_call("Edit", file_path=str(target)), env["own"]))


def test_read_of_main_gets_reminder(env):
    v = wg.judge(_call("Read", file_path=str(env["main"] / "a.py")), env["own"])
    assert v["decision"] == "approve" and str(env["wt"] / "a.py") in v["additionalContext"]


# ── owner gate ────────────────────────────────────────────────────────────────
CLI = "python /x/backend/scripts/artifact_cli.py"


def _cmd(sub, rid=RID, extra=""):
    return f"{CLI} {sub} --project P --run-id {rid} {extra}".strip()


def test_token_issued_by_rewriting_single_command(env):
    c = _cmd("stage-start", extra="--stage deliver")
    v = og._decide("s1", c, {"command": c})
    new = v["hookSpecificOutput"]["updatedInput"]["command"]
    assert new.startswith(hook_token.TOKEN_ENV + "=") and new.endswith(c)
    nonce = new.split("=", 1)[1].split(" ", 1)[0]
    assert hook_token.consume(env["runs"][RID].parent, nonce, "s1", RID, "stage-start")[0]


@pytest.mark.parametrize("c", [
    _cmd("stage-start", extra="--stage deliver") + " && echo done",
    "cd /x && " + _cmd("stage-complete", extra="--stage deliver --data @f"),
    _cmd("stage-start", extra="--stage build") + " | tail -1",
])
def test_compound_token_command_denied(env, c):
    assert _denied(og._decide("s1", c, {"command": c}))


@pytest.mark.skipif(not hook_token.oracle_supported(), reason="file mode (no AF_UNIX)")
def test_token_command_denied_when_token_service_down(env):
    hook_token.stop_oracle()
    c = _cmd("stage-start", extra="--stage deliver")
    assert _denied(og._decide("s1", c, {"command": c}))


def test_model_supplied_token_denied(env):
    c = f"{hook_token.TOKEN_ENV}=abc " + _cmd("stage-start", extra="--stage build")
    assert _denied(og._decide("s1", c, {"command": c}))


def test_read_only_command_untouched(env):
    c = _cmd("run-get")
    assert og._decide("s2", c, {"command": c}) is None


def test_takeover_when_owner_not_live(env, monkeypatch):
    monkeypatch.setattr(og, "_owner_live", lambda sid: False)
    monkeypatch.setattr("core.runtime_hooks.write_session_run_map", lambda *a, **k: None)
    c = _cmd("run-update", extra="--files-touched '[]'")
    assert og._decide("s2", c, {"command": c}) is None
    d = json.loads(env["runs"][RID].read_text())
    assert d["session_id"] == "s2" and d["session_history"][0]["session_id"] == "s1"


def test_takeover_denied_while_owner_live(env, monkeypatch):
    monkeypatch.setattr(og, "_owner_live", lambda sid: True)
    c = _cmd("stage-start", extra="--stage build")
    assert _denied(og._decide("s2", c, {"command": c}))
    assert json.loads(env["runs"][RID].read_text())["session_id"] == "s1"


def test_no_effect_when_switch_off_and_run_shared(env, monkeypatch):
    monkeypatch.setenv("SWARM_WORKTREE_MODE", "off")
    d = json.loads(env["runs"][RID].read_text())
    d["worktree_mode"] = "shared"
    env["runs"][RID].write_text(json.dumps(d))
    c = _cmd("stage-start", extra="--stage deliver") + " && echo ok"
    assert og._decide("s1", c, {"command": c}) is None


@pytest.mark.parametrize("c", [
    # the script path is only an ARGUMENT of a hookless agent: never gets the token
    "claude --print --bare -p go /x/backend/scripts/artifact_cli.py stage-complete "
    f"--project P --run-id {RID} --stage deliver",
])
def test_script_mentioned_but_not_run_gets_no_token(env, c):
    assert og._decide("s1", c, {"command": c}) is None


@pytest.mark.parametrize("extra", [
    f"--stage deliver --run-id {OTHER}",      # twice: argparse takes the last
    "--stag deliver",                          # abbreviation
])
def test_ambiguous_options_denied(env, extra):
    c = _cmd("stage-start", extra=extra)
    assert _denied(og._decide("s1", c, {"command": c}))


def test_incidental_command_does_not_take_over(env, monkeypatch):
    monkeypatch.setattr(og, "_owner_live", lambda sid: False)
    c = _cmd("run-report")
    og._decide("s2", c, {"command": c})
    assert json.loads(env["runs"][RID].read_text())["session_id"] == "s1"
