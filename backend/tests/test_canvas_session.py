"""core/canvas_session — a session's Canvas: Artifacts from its attribution record,
and core/turn_changes' tree snapshots.

Methodology: real git in tmp_path (a bare origin + a clone as the "main checkout");
a run's worktree is made with `git worktree add` at the derived location
(SWARM_WORKTREE_ROOT); the messages table is a
throwaway SQLite file. No real repository, run or database is read.

Invariants:
- (Changes moved to core/session_changes: tests/test_session_changes.py)
- Artifacts are the session's attribution rows joined with the product catalog, and
  carry whether the file is in its repo's HEAD (one git call per repo; no repo or
  no file = unknown);
- list_session_blocks reads only this session's sent rows.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from core import canvas_session as cs
from core import turn_changes as tc
from core.turn_changes import TurnChanges


def _git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def repo(tmp_path, monkeypatch):
    origin = tmp_path / "origin.git"
    _git(tmp_path, "init", "-q", "--bare", "-b", "main", str(origin))
    work = tmp_path / "swarmai"
    _git(tmp_path, "clone", "-q", str(origin), str(work))
    for k, v in (("user.email", "t@t"), ("user.name", "t")):
        _git(work, "config", k, v)
    (work / "a.py").write_text("one\ntwo\n")
    (work / "b.py").write_text("keep\n")
    _git(work, "add", "-A"); _git(work, "commit", "-qm", "init"); _git(work, "push", "-q", "origin", "main")
    _git(work, "branch", "-q", "-u", "origin/main")
    real = os.path.realpath(work)
    monkeypatch.setattr(tc, "code_roots", lambda: [real])
    return Path(real)


async def _turn(roots, writes: dict, at: str):
    """One turn: snapshot, write {path: text|None}, note, finish → (at, [block])."""
    t = TurnChanges.start(roots)
    await t.ready()
    for p, text in writes.items():
        if text is None:
            p.unlink()
        else:
            p.write_text(text)
        t.note({"type": "file_changed", "absolutePath": str(p), "path": p.name,
                "operation": "deleted" if text is None else "written"})
    block = await t.finish()
    return (at, [block] if block else [])


@pytest.mark.asyncio
async def test_a_non_run_worktree_is_not_a_code_root(repo, tmp_path, monkeypatch):
    """Only bound checkouts and run worktrees are code trees: an ad-hoc
    `git worktree add` tree is never snapshotted, so its edits are left off."""
    import core.needs_human_review as nhr
    monkeypatch.undo()
    monkeypatch.setattr(nhr, "_worktree_roots", lambda ws: ((str(repo), "swarmai"),))
    wt = tmp_path / "adhoc"
    _git(repo, "worktree", "add", "-q", "-b", "kiro/adhoc", str(wt))
    assert tc.code_roots() == [str(repo)]
    t = TurnChanges.start(None)
    await t.ready()
    (wt / "a.py").write_text("edited in adhoc\n")
    t.note({"type": "file_changed", "absolutePath": str(wt / "a.py"), "path": "a.py", "operation": "written"})
    assert await t.finish() is None


@pytest.mark.asyncio
async def test_a_tree_snapshotted_after_the_first_tool_call_is_left_off(repo, monkeypatch):
    t = TurnChanges.start([str(repo)])
    t.note_tool({"type": "tool_use", "name": "Bash", "input": {"command": "echo"}})   # before the snapshot ran
    await t.ready()
    (repo / "a.py").write_text("changed\n")
    t.note({"type": "file_changed", "absolutePath": str(repo / "a.py"), "path": "a.py", "operation": "written"})
    assert await t.finish() is None


@pytest.mark.asyncio
async def test_trees_past_the_cap_are_left_off_not_guessed(repo, monkeypatch):
    monkeypatch.setattr(tc, "MAX_START_SNAPSHOTS", 0)
    t = await _started_turn([str(repo)])
    (repo / "a.py").write_text("changed\n")
    t.note({"type": "file_changed", "absolutePath": str(repo / "a.py"), "path": "a.py", "operation": "written"})
    assert await t.finish() is None


async def _started_turn(roots):
    t = TurnChanges.start(roots)
    await t.ready()
    return t


def test_in_head_is_the_repo_head_one_call_per_repo(repo, tmp_path, monkeypatch):
    from core import run_worktree as rw
    calls = []
    real_git = rw.git
    monkeypatch.setattr(rw, "git", lambda *a, **k: (calls.append(a), real_git(*a, **k))[1])
    (repo / "sub").mkdir()
    (repo / "sub" / "new.md").write_text("x")
    outside = tmp_path / "plain" / "n.md"
    outside.parent.mkdir()
    outside.write_text("x")
    got = cs.in_head([str(repo / "a.py"), str(repo / "sub" / "new.md"), str(outside), str(repo / "gone.py")])
    assert got == {str(repo / "a.py"): True, str(repo / "sub" / "new.md"): False,
                   str(outside): None, str(repo / "gone.py"): None}
    assert len(calls) == 1
    _git(repo, "add", "sub/new.md"); _git(repo, "commit", "-qm", "add")
    assert cs.in_head([str(repo / "sub" / "new.md")]) == {str(repo / "sub" / "new.md"): True}


def test_artifact_rows_join_the_product_catalog(tmp_path):
    from core.product_registry import ProductRegistry
    ws = tmp_path / "ws"; (ws / "Knowledge" / "Notes").mkdir(parents=True)
    f = ws / "Knowledge" / "Notes" / "n.md"; f.write_text("x")
    ProductRegistry(ws).register_batch([str(f)])
    ProductRegistry(ws).set_starred("Knowledge/Notes/n.md", True)
    rows = cs.artifact_rows([
        {"path": "Knowledge/Notes/n.md", "absolutePath": str(f), "kind": "knowledge", "firstAt": "t1",
         "lastAt": "t1", "writes": 2, "deleted": False},
        {"path": "x.md", "absolutePath": "/elsewhere/x.md", "kind": "external-nodiff", "firstAt": "t0",
         "lastAt": "t0", "writes": 1, "deleted": False}], str(ws))
    a, b = rows
    assert (a["role"], a["product"], a["starred"], a["exists"], a["writes"]) == ("Knowledge", True, True, True, 2)
    assert (b["role"], b["product"], b["exists"], b["displayLabel"]) == ("Other", False, False, "x.md")


@pytest.mark.asyncio
async def test_list_session_blocks_reads_only_this_sessions_sent_rows(tmp_path):
    import aiosqlite
    from database.sqlite import SQLiteMessagesTable
    db = tmp_path / "t.db"
    blk = json.dumps([{"type": "turn_deliverables", "files": []}])
    async with aiosqlite.connect(db) as conn:
        await conn.execute("CREATE TABLE messages (id TEXT PRIMARY KEY, session_id TEXT, role TEXT, "
                           "content TEXT, created_at TEXT, sent INTEGER)")
        for row in (("m1", "s1", blk, "2", 1), ("m0", "s1", blk, "1", None), ("m2", "s2", blk, "1", 1),
                    ("m3", "s1", blk, "3", 0), ("m4", "s1", json.dumps([{"type": "text"}]), "4", 1)):
            await conn.execute("INSERT INTO messages VALUES (?,?,'assistant',?,?,?)", row)
        await conn.commit()
    got = await SQLiteMessagesTable("messages", db).list_session_blocks("s1", cs.BACKFILL_NEEDLES)
    assert [at for at, _ in got] == ["1", "2"]


def test_a_removed_session_worktree_row_reads_its_landed_file(tmp_path, monkeypatch):
    """design 2026-10-25-landings-consumers.md D1: exists / inHead on the mapped file;
    the row keeps its recorded path (its identity in session_outputs)."""
    from core import session_landings
    landed = tmp_path / "proj" / "b.py"
    landed.parent.mkdir()
    landed.write_text("b\n")
    gone = str(tmp_path / "swt" / "x" / "proj" / "b.py")
    seen = []
    monkeypatch.setattr(session_landings, "landed_path",
                        lambda p, sid=None: seen.append((p, sid)) or (str(landed) if p == gone else None))
    monkeypatch.setattr(cs, "in_head", lambda paths: {p: (True if p == str(landed) else None) for p in paths})
    [row] = cs.artifact_rows([{"path": "b.py", "absolutePath": gone, "kind": "source-final"}],
                             None, "sess-1")
    assert row["absolutePath"] == gone and row["exists"] is True and row["inHead"] is True
    assert seen == [(gone, "sess-1")]
