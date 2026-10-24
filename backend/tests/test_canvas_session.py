"""core/canvas_session — a session's Canvas: Artifacts from its attribution record,
the run index behind working_repo; and core/turn_changes' tree snapshots.

Methodology: real git in tmp_path (a bare origin + a clone as the "main checkout");
a run's worktree is made with `git worktree add` at the derived location
(SWARM_WORKTREE_ROOT) and its run.json written by hand; the messages table is a
throwaway SQLite file. No real repository, run or database is read.

Invariants:
- (Changes moved to core/session_changes: tests/test_session_changes.py)
- working_repo is the newest owned run's main checkout; the run index is re-read
  only for a changed run.json;
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


# ── Changes: the session's runs ─────────────────────────────────────────────

def _run(ws, rid, owner, *, worktrees=(), commits=(), history=(), status="running"):
    d = ws / "Projects" / "P" / ".artifacts" / "runs" / rid
    d.mkdir(parents=True, exist_ok=True)
    (d / "run.json").write_text(json.dumps({
        "id": rid, "session_id": owner, "session_history": list(history), "status": status,
        "project": "P", "requirement": f"Do {rid}\nmore", "created_at": rid,
        "worktrees": list(worktrees), "commits": list(commits)}))
    return d / "run.json"


@pytest.fixture
def runs(repo, tmp_path, monkeypatch):
    wts = Path(os.path.realpath(tmp_path)) / "wts"
    wts.mkdir()
    monkeypatch.setenv("SWARM_WORKTREE_ROOT", str(wts))
    cs._RUN_CACHE.clear()
    ws = tmp_path / "ws"
    ws.mkdir()

    def worktree(rid):
        wt = wts / rid / "swarmai"
        _git(repo, "worktree", "add", "-q", "-b", f"run/{rid}", str(wt))
        return wt
    return ws, worktree


def test_working_repo_is_the_newest_runs_main_checkout_never_a_worktree(repo, runs, tmp_path):
    """The terminal's working repo: the newest owned run that names a repo, its
    worktree's `main`; a linked worktree (`.git` a file) never qualifies; no run or a
    gone repo is None — an older run is not used instead."""
    ws, worktree = runs
    assert cs.working_repo("s1", str(ws)) is None                     # no run: none
    other = tmp_path / "other"
    _git(tmp_path, "init", "-q", str(other))
    _run(ws, "run_w1", "s1", worktrees=[{"repo_name": "other", "main": str(other), "state": "released"}])
    wt = worktree("run_w2")
    _run(ws, "run_w2", "s1", worktrees=[{"repo_name": "swarmai", "main": str(repo), "state": "active"}])
    assert cs.working_repo("s1", str(ws)) == {"path": str(repo), "name": "swarmai"}   # newest wins
    _run(ws, "run_w3", "s1")                                          # names no repo: skipped
    assert cs.working_repo("s1", str(ws))["path"] == str(repo)
    _run(ws, "run_w4", "s1", worktrees=[{"repo_name": "swarmai", "main": str(wt), "state": "active"},
                                         {"repo_name": "other", "main": str(other), "state": "active"}])
    assert cs.working_repo("s1", str(ws)) is None                     # first is a worktree: none, not the 2nd / run_w2's
    _run(ws, "run_w5", "s1", commits=[{"repo": os.path.realpath(other), "sha": "a" * 40}])
    assert cs.working_repo("s1", str(ws)) == {"path": os.path.realpath(other), "name": "other"}
    assert cs.working_repo("s2", str(ws)) is None and cs.working_repo("s1", None) is None


def test_working_repo_endpoint_reads_the_session_newest_run(repo, runs, monkeypatch, client):
    """GET /api/canvas/session/{id}/working-repo: the same answer, null for none."""
    import core.project_registry as pr
    ws, _ = runs
    monkeypatch.setattr(pr, "get_swarmws", lambda: ws)
    _run(ws, "run_x1", "sx", worktrees=[{"repo_name": "swarmai", "main": str(repo), "state": "released"}])
    r = client.get("/api/canvas/session/sx/working-repo")
    assert r.status_code == 200 and r.json() == {"workingRepo": {"path": str(repo), "name": "swarmai"}}
    assert client.get("/api/canvas/session/nobody/working-repo").json() == {"workingRepo": None}


def test_the_run_index_rereads_only_a_changed_run_json(repo, runs):
    ws, _ = runs
    f = _run(ws, "run_e1", "s1")
    _run(ws, "run_e2", "s2", history=[{"session_id": "s1"}])
    assert sorted(r["run"] for r in cs.session_runs("s1", str(ws))) == ["run_e1", "run_e2"]
    f.write_text(json.dumps({"id": "run_e1", "session_id": "s9"}))
    os.utime(f, (1, 1))
    assert [r["run"] for r in cs.session_runs("s1", str(ws))] == ["run_e2"]


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
