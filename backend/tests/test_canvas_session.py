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
- Artifacts are the session's attribution rows joined with the product catalog;
  pipeline runs are the ones the session owned (run.json) or surfaced (baseRef);
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


def test_linked_worktrees_are_all_live_ones_and_cached(repo, tmp_path):
    keep, gone, old = tmp_path / "keep", tmp_path / "gone", tmp_path / "old"
    cache = tmp_path / "cache" / "pipeline_test" / "x"
    for p, b in ((keep, "k"), (gone, "g"), (cache, "c"), (old, "o")):
        _git(repo, "worktree", "add", "-q", "-b", b, str(p))
    import shutil
    shutil.rmtree(gone)
    os.utime(_git(old, "rev-parse", "--path-format=absolute", "--git-path", "index"), (1, 1))
    assert tc._linked_worktrees([str(repo)]) == [os.path.realpath(keep), os.path.realpath(old)]
    _git(repo, "worktree", "add", "-q", "-b", "n", str(tmp_path / "n"))
    assert len(tc._linked_worktrees([str(repo)])) == 2          # cached
    tc._linked_cache.clear()
    assert len(tc._linked_worktrees([str(repo)])) == 3


@pytest.mark.asyncio
async def test_a_worktree_created_during_the_turn_diffs_from_its_head(repo, tmp_path, monkeypatch):
    import core.needs_human_review as nhr
    monkeypatch.undo()
    monkeypatch.setattr(nhr, "_worktree_roots", lambda ws: ((str(repo), "swarmai"),))
    tc._linked_cache.clear()
    t = TurnChanges.start(None)
    await t.ready()
    wt = tmp_path / "fresh"
    _git(repo, "worktree", "add", "-q", "-b", "kiro/fresh", str(wt))   # made by the turn
    tc._linked_cache.clear()
    (wt / "a.py").write_text("new in fresh\n")
    t.note({"type": "file_changed", "absolutePath": str(wt / "a.py"), "path": "a.py", "operation": "written"})
    block = await t.finish()
    [row] = block["files"]
    assert row["path"] == "a.py" and row["status"] == "modified" and row["gitRoot"] == str(repo)


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
    assert (a["role"], a["product"], a["starred"], a["exists"], a["turns"]) == ("Knowledge", True, True, True, 2)
    assert (b["role"], b["product"], b["exists"], b["displayLabel"]) == ("Other", False, False, "x.md")


def test_owned_runs_come_from_run_json_and_surfaced_ones_from_base_ref(tmp_path):
    runs = tmp_path / "ws" / "Projects" / "P" / ".artifacts" / "runs"
    for rid, owner, hist in (("run_a", "s1", []), ("run_b", "s2", [{"session_id": "s1"}]), ("run_c", "s2", [])):
        (runs / rid).mkdir(parents=True)
        (runs / rid / "run.json").write_text(json.dumps({
            "id": rid, "session_id": owner, "session_history": hist,
            "commits": [{"repo": "/r", "sha": "a" * 7 + rid[-1] * 33, "files": ["x.py"]}]}))
    got = cs.session_runs("s1", str(tmp_path / "ws"))
    assert sorted(c["run"] for c in got) == ["run_a", "run_b"]
    (runs / "run_a" / "run.json").write_text(json.dumps({"id": "run_a", "session_id": "s9"}))
    os.utime(runs / "run_a" / "run.json", (1, 1))         # a changed file is re-read
    assert [c["run"] for c in cs.session_runs("s1", str(tmp_path / "ws"))] == ["run_b"]
    rows = [{"kind": "source-final", "path": "src/a.py", "absolutePath": "/r/src/a.py", "baseRef": "b" * 40 + "^"},
            {"kind": "source-final", "path": "src/b.py", "absolutePath": "/r/src/b.py", "baseRef": "b" * 40 + "^"},
            {"kind": "knowledge", "path": "n.md", "absolutePath": "/ws/n.md"}]
    assert cs.surfaced_commits(rows) == [{"repoPath": "/r", "sha": "b" * 40, "files": ["src/a.py", "src/b.py"], "run": None}]


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
