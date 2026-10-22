"""core/canvas_session — a session's Canvas (Artifacts + Changes) from its own turn records.

Methodology: real git in tmp_path (a bare origin + a clone); turn_changes blocks are
made by the real TurnChanges (start → write → note → finish), so their trees are real;
the messages table is a throwaway SQLite file. No real repository or database is read.

Invariants:
- one row per file however many turns wrote it, with the NET change; a file put back
  as it was is no row;
- the state follows the CONTENT the session left: uncommitted → committed (its sha,
  also after the commit is cherry-picked / rebased elsewhere) → pushed; a later edit by
  someone else reads "changed";
- another session's commits are not this session's: a commit lists only if it holds
  a blob this session left;
- an edit in a linked worktree of a bound repo is recorded (turn_changes snapshots
  the bound repos' live worktrees) and, after an ff-merge and the worktree's removal,
  still resolves to its commit;
- Artifacts aggregate turn_deliverables per file; pipeline runs are found from the
  session's surface_run_outputs calls;
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
    tc._linked_cache.clear()
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


def _state(repo, rows):
    ex = cs.extract(rows)
    [row] = [cs.git_state(root, files) for root, files in ex["edits"].items()]
    return row, {f["path"]: f for f in row["files"]}


@pytest.mark.asyncio
async def test_turns_fold_into_one_net_row_and_follow_the_content(repo):
    r1 = await _turn([str(repo)], {repo / "a.py": "one\nTWO\n"}, "2026-10-22T10:00")
    r2 = await _turn([str(repo)], {repo / "a.py": "one\nTWO\nthree\n", repo / "new.py": "x\n"}, "2026-10-22T10:05")
    row, files = _state(repo, [r1, r2])
    assert files["a.py"]["turns"] == 2 and (files["a.py"]["added"], files["a.py"]["deleted"]) == (2, 1)
    assert files["a.py"]["state"] == files["new.py"]["state"] == "uncommitted"
    assert files["new.py"]["status"] == "added" and row["commits"] == []

    _git(repo, "add", "-A"); _git(repo, "commit", "-qm", "feat: mine")
    sha = _git(repo, "rev-parse", "HEAD")
    row, files = _state(repo, [r1, r2])
    assert files["a.py"]["state"] == "committed" and files["a.py"]["commit"] == sha
    assert [c["sha"] for c in row["commits"]] == [sha] and row["commits"][0]["files"] == ["a.py", "new.py"]

    _git(repo, "push", "-q", "origin", "main")
    row, files = _state(repo, [r1, r2])
    assert files["a.py"]["state"] == "pushed" and row["commits"] == []


@pytest.mark.asyncio
async def test_a_rewritten_commit_is_still_found_and_a_later_edit_reads_changed(repo):
    r1 = await _turn([str(repo)], {repo / "a.py": "mine\n"}, "t1")
    _git(repo, "checkout", "-qb", "work"); _git(repo, "commit", "-qam", "wip")
    _git(repo, "checkout", "-q", "main"); _git(repo, "cherry-pick", "work")
    _git(repo, "branch", "-qD", "work")                     # the original sha is gone
    picked = _git(repo, "rev-parse", "HEAD")
    _, files = _state(repo, [r1])
    assert files["a.py"]["commit"] == picked
    (repo / "a.py").write_text("someone else\n"); _git(repo, "commit", "-qam", "theirs")
    _, files = _state(repo, [r1])
    assert files["a.py"]["state"] == "committed" and files["a.py"]["commit"] == picked
    _git(repo, "reset", "-q", "--hard", "origin/main")       # mine never landed
    (repo / "a.py").write_text("other\n")
    _, files = _state(repo, [r1])
    assert files["a.py"]["state"] == "changed"


@pytest.mark.asyncio
async def test_a_file_put_back_is_no_row_and_other_commits_are_not_listed(repo):
    r1 = await _turn([str(repo)], {repo / "a.py": "tmp\n", repo / "b.py": "mine\n"}, "t1")
    r2 = await _turn([str(repo)], {repo / "a.py": "one\ntwo\n"}, "t2")
    (repo / "c.py").write_text("other session\n"); _git(repo, "add", "c.py"); _git(repo, "commit", "-qm", "other")
    row, files = _state(repo, [r1, r2])
    assert set(files) == {"b.py"} and row["commits"] == []


@pytest.mark.asyncio
async def test_an_edit_in_a_linked_worktree_resolves_after_ff_merge_and_removal(repo, tmp_path, monkeypatch):
    import core.needs_human_review as nhr
    monkeypatch.undo()                                     # the real code_roots
    monkeypatch.setattr(nhr, "_worktree_roots", lambda ws: ((str(repo), "swarmai"),))
    wt = tmp_path / "wt"
    _git(repo, "worktree", "add", "-q", "-b", "kiro/x", str(wt))
    wt = Path(os.path.realpath(wt))
    assert str(wt) in tc.code_roots()
    r1 = await _turn(None, {wt / "a.py": "from the worktree\n"}, "t1")
    assert r1[1] and r1[1][0]["files"][0]["gitRoot"] == str(repo)
    _git(wt, "commit", "-qam", "wt work")
    _git(repo, "merge", "-q", "--ff-only", "kiro/x")
    _git(repo, "worktree", "remove", str(wt)); _git(repo, "branch", "-qd", "kiro/x")
    row, files = _state(repo, [r1])
    assert files["a.py"]["state"] == "committed" and files["a.py"]["commit"] == _git(repo, "rev-parse", "HEAD")


def test_linked_worktrees_only_active_ones_capped_and_cached(repo, tmp_path, monkeypatch):
    keep, gone, stale = tmp_path / "keep", tmp_path / "gone", tmp_path / "stale"
    cache = tmp_path / "cache" / "pipeline_test" / "x"
    for p, b in ((keep, "k"), (gone, "g"), (cache, "c"), (stale, "s")):
        _git(repo, "worktree", "add", "-q", "-b", b, str(p))
    import shutil
    import time
    shutil.rmtree(gone)
    old = time.time() - tc.LINKED_ACTIVE_S - 60
    os.utime(_git(stale, "rev-parse", "--path-format=absolute", "--git-path", "index"), (old, old))
    assert tc._linked_worktrees([str(repo)]) == [os.path.realpath(keep)]
    extra = [tmp_path / f"n{i}" for i in range(tc.MAX_LINKED_WORKTREES + 2)]
    for i, p in enumerate(extra):
        _git(repo, "worktree", "add", "-q", "-b", f"n{i}", str(p))
    assert tc._linked_worktrees([str(repo)]) == [os.path.realpath(keep)]      # cached
    tc._linked_cache.clear()
    assert len(tc._linked_worktrees([str(repo)])) == tc.MAX_LINKED_WORKTREES


def test_extract_artifacts_and_runs():
    d = {"type": "turn_deliverables", "files": [{"path": "Knowledge/n.md", "absolutePath": "/ws/Knowledge/n.md", "kind": "knowledge"}]}
    run = {"type": "tool_use", "name": "mcp__swarm__surface_run_outputs", "input": {"run_id": "run_ab12"}}
    ex = cs.extract([("t1", [d]), ("t2", [d, run, {**run, "input": {"run_id": "../x"}}])])
    a = ex["artifacts"]["/ws/Knowledge/n.md"]
    assert (a["firstAt"], a["lastAt"], a["turns"], a["kind"]) == ("t1", "t2", 2, "knowledge")
    assert ex["runs"] == ["run_ab12"]


def test_artifact_rows_role_and_existence(tmp_path):
    ws = tmp_path / "ws"; (ws / "Knowledge" / "Notes").mkdir(parents=True)
    f = ws / "Knowledge" / "Notes" / "n.md"; f.write_text("x")
    rows = cs.artifact_rows({str(f): {"path": "n.md", "absolutePath": str(f), "kind": "knowledge",
                                      "firstAt": "t1", "lastAt": "t1", "turns": 1},
                             "/elsewhere/x.md": {"path": "x.md", "absolutePath": "/elsewhere/x.md",
                                                 "kind": "external-nodiff", "firstAt": "t0", "lastAt": "t0", "turns": 1}},
                            str(ws))
    assert [(r["role"], r["exists"]) for r in rows] == [("Knowledge", True), ("Other", False)]


@pytest.mark.asyncio
async def test_list_session_blocks_reads_only_this_sessions_sent_rows(tmp_path):
    import aiosqlite
    from database.sqlite import SQLiteMessagesTable
    db = tmp_path / "t.db"
    blk = json.dumps([{"type": "turn_changes", "files": []}])
    async with aiosqlite.connect(db) as conn:
        await conn.execute("CREATE TABLE messages (id TEXT PRIMARY KEY, session_id TEXT, role TEXT, "
                           "content TEXT, created_at TEXT, sent INTEGER)")
        for row in (("m1", "s1", blk, "2", 1), ("m0", "s1", blk, "1", None), ("m2", "s2", blk, "1", 1),
                    ("m3", "s1", blk, "3", 0), ("m4", "s1", json.dumps([{"type": "text"}]), "4", 1)):
            await conn.execute("INSERT INTO messages VALUES (?,?,'assistant',?,?,?)", row)
        await conn.commit()
    got = await SQLiteMessagesTable("messages", db).list_session_blocks("s1", cs.NEEDLES)
    assert [at for at, _ in got] == ["1", "2"]
