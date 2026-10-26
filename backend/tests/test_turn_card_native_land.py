"""The code-changes card after native coding (design 2026-10-26-turn-cards-unified.md §3).

What is tested (real git repos in tmp_path, the real ``session_worktree`` script run in a
real shell, the real ``run_owner_gate``, the real ``TurnChanges``):
- 5: ``cd <wt> && … land``, ``W=$(…); … land $W``, ``bash -c '… land'`` from the main
  agent: the stamp carries the turn id and the card lists the commit and its files;
- 6: the same from a sub-agent: ``turn=none``, not on the card;
- 7: another session's land, and this session's land in an earlier turn: not on the card;
- 8: land, then switch the main checkout's branch: still on the card;
- 9: > 200 newer reflog entries: still found; a turn that ran no session_worktree
  command reads no landings and has no card; one land is exactly one entry (its branch
  and HEAD reflog hits are one);
- 9a: a file edited in a session worktree and landed in the same turn: one row, under
  the landing;
- 9b: a session_worktree call and a pipeline rewrite on one line: one updatedInput
  carrying both;
- 10: a session worktree present at the start, edited, not landed: rows; one created
  during the turn: rows from its creation commit; another session's worktree: none;
- 11: two units of one session hand their own turn ids.
"""
from __future__ import annotations

import asyncio
import os
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

from core import app_session_id as asi
from core import session_landings as sl
from core import turn_changes as tc
from core.run_owner_gate import create_run_owner_gate
from core.turn_changes import TurnChanges

BACKEND = Path(__file__).resolve().parent.parent
SID = "11111111-2222-3333-4444-555555555555"
OTHER = "99999999-2222-3333-4444-555555555555"
T1 = "0123456789abcdef0123456789abcdef"
T0 = "fedcba9876543210fedcba9876543210"
SW = '"$SWARM_BACKEND_BIN" py -m scripts.session_worktree'


def _g(cwd, *a):
    return subprocess.run(["git", "-C", str(cwd), *a], check=True, capture_output=True,
                          text=True).stdout


@pytest.fixture
def env(tmp_path, monkeypatch):
    """A main checkout bound for the card, a session-worktree root, a fake backend bin."""
    data = tmp_path / "appdata"
    (data / "SwarmWS").mkdir(parents=True)
    monkeypatch.setattr("config.get_app_data_dir", lambda: data)
    ident = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
             "GIT_COMMITTER_EMAIL": "t@t"}
    for k, v in ident.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("SWARM_APP_SESSION_ID", raising=False)
    monkeypatch.delenv("SWARM_TURN_ID", raising=False)
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    (repo / "a.py").write_text("a = 1\n")
    _g(repo, "add", "-A")
    _g(repo, "-c", "core.hooksPath=/dev/null", "commit", "-qm", "init")
    repo = Path(os.path.realpath(repo))
    wt_root = tmp_path / "sw"
    wt_root.mkdir()
    monkeypatch.setenv("SWARM_SESSION_WORKTREE_ROOT", str(wt_root))
    fake = tmp_path / "backend-bin"
    fake.write_text(f"#!/bin/sh\nshift\nexec {shlex.quote(sys.executable)} \"$@\"\n")
    fake.chmod(0o755)
    monkeypatch.setattr(sl, "_bound_checkouts", lambda: [str(repo)])
    monkeypatch.setattr(tc, "code_roots", lambda: [str(repo)])
    shell_env = {**os.environ, **ident, "SWARM_BACKEND_BIN": str(fake),
                 "PYTHONPATH": str(BACKEND), "SWARM_SESSION_WORKTREE_ROOT": str(wt_root),
                 "GIT_CONFIG_GLOBAL": "/dev/null"}
    shell_env.pop("SWARM_APP_SESSION_ID", None)
    shell_env.pop("SWARM_TURN_ID", None)
    return {"repo": repo, "root": Path(os.path.realpath(wt_root)), "shell": shell_env}


def _ctx(turn=T1, sid=SID):
    return {"sdk_session_id": sid, "_turn_id": turn}


async def _agate(command, ctx, agent_id=None):
    """The command the agent's shell runs after run_owner_gate (its updatedInput)."""
    data = {"tool_name": "Bash", "tool_input": {"command": command}}
    if agent_id:
        data["agent_id"] = agent_id
    out = await create_run_owner_gate(ctx, "check")(data, "tu1", None)
    return ((out.get("hookSpecificOutput") or {}).get("updatedInput") or {}).get("command") \
        or command


def _gate(command, ctx, agent_id=None):
    return asyncio.run(_agate(command, ctx, agent_id))


def _sh(env, command, *, sid=None):
    """Run *command* as the agent's Bash tool does: a fresh shell in the backend dir.
    *sid*: the session's own fixed environment (a prewarmed CLI has none)."""
    e = dict(env["shell"])
    if sid:
        e["SWARM_APP_SESSION_ID"] = sid
    p = subprocess.run(["bash", "-c", command], cwd=str(BACKEND), env=e,
                       capture_output=True, text=True, timeout=120)
    assert p.returncode == 0, (command, p.stdout, p.stderr)
    return p.stdout


def _create(env, name, sid=SID):
    out = _sh(env, f"{SW} create {shlex.quote(str(env['repo']))} --name {name}", sid=sid)
    return Path(os.path.realpath(env["root"] / name / env["repo"].name))


def _commit(wt, name, text):
    (Path(wt) / name).write_text(text)
    _g(wt, "add", "-A")
    _g(wt, "-c", "core.hooksPath=/dev/null", "commit", "-qm", name)


def _write_tool(t, path):
    t.note_tool({"type": "tool_use", "name": "Write", "input": {"file_path": str(path)}})


def _stamps(repo):
    return [m for m in _g(repo, "reflog", "show", "--format=%gs", "main").splitlines()
            if m.startswith("swarm-land ")]


async def _turn(ctx, sid=SID):
    t = TurnChanges.start(session_id=sid, turn_id=ctx["_turn_id"], context=ctx)
    await t.ready()
    return t


# ── 5 / 6: every command shape hands the turn id; a sub-agent's does not ──────

SHAPES = [
    "cd {wt} && {sw} land {wt}",
    "W=$(ls -d {wt}); {sw} land $W",
    "bash -c '{sw} land {wt}'",
    "cat > /dev/null <<'EOF'\nx\nEOF\n{sw} land {wt} 2>&1 | tail -5",
]


@pytest.mark.parametrize("shape", SHAPES)
def test_5_main_agent_land_in_any_shape_is_on_the_card(env, shape):
    async def go():
        wt = _create(env, "s5")
        _commit(wt, "b.py", "b = 2\n")
        ctx = _ctx()
        t = await _turn(ctx)
        cmd = shape.format(wt=shlex.quote(str(wt)), sw=SW)
        _sh(env, await _agate(cmd, ctx))
        return wt, await t.finish()
    wt, block = asyncio.run(go())
    assert any(f"turn={T1} " in s for s in _stamps(env["repo"])), _stamps(env["repo"])
    assert block is not None and len(block["landed"]) == 1
    land = block["landed"][0]
    assert land["count"] == 1 and [f["path"] for f in land["files"]] == ["b.py"]


def test_6_a_sub_agent_land_is_not_on_the_card(env):
    async def go():
        wt = _create(env, "s6")
        _commit(wt, "b.py", "b = 2\n")
        ctx = _ctx()
        t = await _turn(ctx)
        _sh(env, await _agate(f"cd {wt} && {SW} land {wt}", ctx, agent_id="sub1"))
        return await t.finish()
    block = asyncio.run(go())
    assert any(" turn=none " in s for s in _stamps(env["repo"]))
    assert not (block or {}).get("landed")


# ── 7 / 8: only this turn's own landings, whatever the branch does after ──────

def test_7_another_sessions_land_and_an_earlier_turns_land_are_not_on_the_card(env):
    async def go():
        mine = _create(env, "s7a")
        _commit(mine, "b.py", "b = 2\n")
        _sh(env, await _agate(f"{SW} land {mine}", _ctx(turn=T0)))      # an earlier turn
        theirs = _create(env, "s7b", sid=OTHER)
        _commit(theirs, "c.py", "c = 3\n")
        ctx = _ctx()
        t = await _turn(ctx)
        _sh(env, await _agate(f"cd {theirs} && {SW} land {theirs}", _ctx(sid=OTHER)))
        ctx["_turn_sw"] = True        # this turn ran a session_worktree command too
        return await t.finish()
    block = asyncio.run(go())
    assert len(_stamps(env["repo"])) == 2
    assert not (block or {}).get("landed")


def test_8_a_branch_switch_after_the_land_keeps_it_on_the_card(env):
    async def go():
        wt = _create(env, "s8")
        _commit(wt, "b.py", "b = 2\n")
        ctx = _ctx()
        t = await _turn(ctx)
        _sh(env, await _agate(f"cd {wt} && {SW} land {wt}", ctx))
        _g(env["repo"], "checkout", "-q", "-b", "other")
        return await t.finish()
    block = asyncio.run(go())
    assert block is not None and len(block["landed"]) == 1
    assert block["landed"][0]["dest"] == "main"


# ── 9: a busy reflog, a turn without session_worktree, one land = one entry ───

def test_9_a_land_under_more_than_200_newer_entries_is_found_once(env):
    async def go():
        wt = _create(env, "s9")
        _commit(wt, "b.py", "b = 2\n")
        ctx = _ctx()
        t = await _turn(ctx)
        _sh(env, await _agate(f"{SW} land {wt}", ctx))
        head = _g(env["repo"], "rev-parse", "HEAD").strip()
        log = env["repo"] / ".git" / "logs" / "refs" / "heads" / "main"
        with open(log, "a") as f:
            for i in range(sl.TURN_CAP + 10):
                f.write(f"{head} {head} t <t@t> {1800000000 + i} +0000\tnoise {i}\n")
        return await t.finish()
    block = asyncio.run(go())
    assert block is not None and len(block["landed"]) == 1, block
    assert not block.get("landed_truncated")


def test_9_a_turn_without_session_worktree_reads_no_landings(env, monkeypatch):
    calls = []
    monkeypatch.setattr(sl, "read_turn", lambda *a, **k: calls.append(1) or {
        "landed": [], "unverified": 0, "truncated": False, "unknown": []})

    async def go():
        ctx = _ctx()
        t = await _turn(ctx)
        await _agate("ls /tmp", ctx)
        return await t.finish()
    assert asyncio.run(go()) is None
    assert calls == []


# ── 9a / 10: session worktrees are code trees ─────────────────────────────────

def test_9a_a_file_edited_and_landed_in_one_turn_is_one_row(env):
    async def go():
        wt = _create(env, "s9a")
        ctx = _ctx()
        t = await _turn(ctx)
        _write_tool(t, wt / "b.py")
        _commit(wt, "b.py", "b = 2\n")
        _sh(env, await _agate(f"cd {wt} && {SW} land {wt}", ctx))
        return await t.finish()
    block = asyncio.run(go())
    assert block["files"] == [], block["files"]
    assert [f["path"] for f in block["landed"][0]["files"]] == ["b.py"]


def test_10_an_unlanded_session_worktree_present_at_the_start_has_rows(env):
    """Diffed from the turn's start snapshot: an earlier turn's commit there is not
    this turn's change."""
    async def go():
        wt = _create(env, "s10")
        _commit(wt, "a.py", "a = 1\nb = 2\nc = 3\nd = 4\n")     # an earlier turn
        t = await _turn(_ctx())
        (wt / "a.py").write_text("a = 1\nb = 2\nc = 3\nd = 5\n")
        _write_tool(t, wt / "a.py")
        return await t.finish()
    block = asyncio.run(go())
    assert [(f["path"], f["status"], f["added"], f["deleted"]) for f in block["files"]] \
        == [("a.py", "modified", 1, 1)]


def test_10_a_session_worktree_created_in_the_turn_diffs_from_its_creation(env):
    async def go():
        t = await _turn(_ctx())
        wt = _create(env, "s10b")
        (wt / "n.py").write_text("n = 1\n")
        _write_tool(t, wt / "n.py")
        _commit(wt, "m.py", "m = 1\n")          # committed in the worktree: still shown
        _write_tool(t, wt / "m.py")
        return await t.finish()
    block = asyncio.run(go())
    assert sorted(f["path"] for f in block["files"]) == ["m.py", "n.py"]
    assert all(f["status"] == "added" for f in block["files"])


def test_10_another_sessions_worktree_has_no_rows(env):
    async def go():
        wt = _create(env, "s10c", sid=OTHER)
        t = await _turn(_ctx())
        (wt / "a.py").write_text("a = 7\n")
        _write_tool(t, wt / "a.py")
        return await t.finish()
    assert asyncio.run(go()) is None


# ── 9b / 11: the gate's one updatedInput, per-unit turn ids ───────────────────

def test_9b_session_worktree_and_pipeline_rewrite_compose_into_one_input(monkeypatch):
    from core import run_owner_gate as rog
    pipe = '"$SWARM_BACKEND_BIN" pipeline-v1 status --project P --run-id run_abcd1234'
    line = f"cd /w/x && {SW} land /w/x && {pipe}"
    rewritten = f"SWARM_APP_SESSION_ID={shlex.quote(SID)} PIPELINE-REWRITTEN"
    monkeypatch.setattr(rog, "_decide", lambda *a: {"hookSpecificOutput": {
        "hookEventName": "PreToolUse", "permissionDecision": "allow",
        "updatedInput": {"command": rewritten}}})
    new = _gate(line, _ctx())
    assert new == (f"export SWARM_APP_SESSION_ID={shlex.quote(SID)} SWARM_TURN_ID={T1}; "
                   + rewritten)


def test_11_two_units_of_one_session_hand_their_own_turn_ids():
    a, b = _ctx(turn=T1), _ctx(turn=T0)
    cmd = f"cd /w && {SW} land /w"
    assert f"SWARM_TURN_ID={T1};" in _gate(cmd, a)
    assert f"SWARM_TURN_ID={T0};" in _gate(cmd, b)
    assert asi.valid_turn_id(T1) and asi.valid_turn_id(T0)
