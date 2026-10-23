"""core/canvas_session — a session's Canvas: Artifacts from its attribution record,
Changes from the pipeline runs it owns; and core/turn_changes' tree snapshots.

Methodology: real git in tmp_path (a bare origin + a clone as the "main checkout");
a run's worktree is made with `git worktree add` at the derived location
(SWARM_WORKTREE_ROOT) and its run.json written by hand; the messages table is a
throwaway SQLite file. No real repository, run or database is read.

Invariants:
- Changes are the runs the session owns (run.json session_id / session_history) and
  nothing else: an edit in the shared checkout, another session's run, a worktree git
  does not list on the run's branch, and a path written into run.json are not read;
- a live worktree shows its change set against its HEAD, untracked new files
  included and the run's deliverables dir left out, with trees the on-demand diff
  reads; a landed commit shows its own files, and "pushed" once the upstream has it;
- a worktree released without landing is said, not guessed;
- the run index is re-read only for a changed run.json;
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
    cs._RUN_CACHE.clear(); cs._COMMITS.clear(); cs._PUSHED.clear()
    ws = tmp_path / "ws"
    ws.mkdir()

    def worktree(rid):
        wt = wts / rid / "swarmai"
        _git(repo, "worktree", "add", "-q", "-b", f"run/{rid}", str(wt))
        return wt
    return ws, worktree


def test_a_live_run_worktree_is_its_change_set_and_nothing_else(repo, runs):
    ws, worktree = runs
    wt = worktree("run_a1")
    _run(ws, "run_a1", "s1", worktrees=[{"repo_name": "swarmai", "main": str(repo), "state": "active"}])
    (wt / "a.py").write_text("one\nTWO\nthree\n")
    (wt / "new.py").write_text("x\n")
    (wt / ".aidlc" / "deliverables" / "run_a1").mkdir(parents=True)
    (wt / ".aidlc" / "deliverables" / "run_a1" / "REPORT.md").write_text("report\n")
    (repo / "b.py").write_text("another session, in the shared checkout\n")
    [run] = cs.changes_of("s1", str(ws))
    assert (run["run"], run["title"], run["project"]) == ("run_a1", "Do run_a1", "P")
    [row] = run["repos"]
    assert row["root"] == str(repo) and row["worktree"] == str(wt) and row["branch"] == "run/run_a1"
    files = {f["path"]: f for f in row["files"]}
    assert set(files) == {"a.py", "new.py"}
    assert (files["a.py"]["status"], files["a.py"]["added"], files["a.py"]["deleted"]) == ("modified", 2, 1)
    assert files["new.py"]["status"] == "added" and row["commits"] == []
    diff = tc.diff_text(str(repo), files["a.py"]["start"], files["a.py"]["end"], "a.py")
    assert "+TWO" in diff and "-two" in diff
    assert cs.changes_of("s2", str(ws)) == []


def test_a_worktree_git_does_not_list_or_a_path_in_run_json_is_not_read(repo, runs, tmp_path):
    ws, worktree = runs
    elsewhere = tmp_path / "elsewhere"
    _git(repo, "worktree", "add", "-q", "-b", "kiro/x", str(elsewhere))
    (elsewhere / "a.py").write_text("not a run's\n")
    _run(ws, "run_b1", "s1", worktrees=[{"repo_name": "swarmai", "main": str(repo), "state": "active",
                                          "path": str(elsewhere)}])
    assert cs.changes_of("s1", str(ws)) == []           # nothing at the derived location
    wt = worktree("run_b2")
    _git(wt, "checkout", "-q", "-b", "other")            # no longer on run/run_b2
    (wt / "a.py").write_text("x\n")
    _run(ws, "run_b2", "s1", worktrees=[{"repo_name": "swarmai", "main": str(repo), "state": "active"}])
    assert cs.changes_of("s1", str(ws)) == []
    _run(ws, "run_b3", "s1", worktrees=[{"repo_name": "swarmai", "main": "/etc", "state": "active"}])
    assert cs.changes_of("s1", str(ws)) == []           # not a code repo Canvas may read


def test_landed_commits_show_their_files_and_turn_pushed(repo, runs):
    ws, _ = runs
    (repo / "a.py").write_text("landed\n"); (repo / "c.py").write_text("c\n")
    _git(repo, "add", "-A"); _git(repo, "commit", "-qm", "feat: landed")
    sha = _git(repo, "rev-parse", "HEAD")
    _run(ws, "run_c1", "s0", history=[{"session_id": "s1"}], status="completed",
         worktrees=[{"repo_name": "swarmai", "main": str(repo), "state": "released",
                     "integrated": {"sha": sha}}],
         commits=[{"repo": str(repo), "sha": sha[:12]}, {"repo": str(repo), "sha": "f" * 40}])
    [run] = cs.changes_of("s1", str(ws))                # a run it took over is its own
    [row] = run["repos"]
    [c] = row["commits"]
    assert (c["sha"], c["short"], c["subject"], c["pushed"]) == (sha, sha[:8], "feat: landed", False)
    assert c["files"] == [{"path": "a.py", "added": 1, "deleted": 2}, {"path": "c.py", "added": 1, "deleted": 0}]
    assert row["released"] is False and row["files"] == []
    _git(repo, "push", "-q", "origin", "main")
    [run] = cs.changes_of("s1", str(ws))
    assert run["repos"][0]["commits"][0]["pushed"] is True


def test_a_worktree_released_without_landing_is_said(repo, runs):
    ws, _ = runs
    _run(ws, "run_d1", "s1", status="failed",
         worktrees=[{"repo_name": "swarmai", "main": str(repo), "state": "released"}])
    [run] = cs.changes_of("s1", str(ws))
    assert run["repos"][0]["released"] is True and run["repos"][0]["files"] == []


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
