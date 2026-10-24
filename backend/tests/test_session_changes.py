"""core/session_changes — Canvas Changes from the session's own transcript
(docs/design/canvas-changes-from-turns.md v4).

Methodology: a throwaway SQLite file with the real schema-v15 revision triggers
(database.sqlite._TRANSCRIPT_REV_DDL) over a minimal messages table, rows written with
plain sqlite3; real git in tmp_path for commits and tree diffs, bound through a patched
repo_changes.allowed_roots. No real repository or database is read.

Invariants:
- cards are the persisted turn_changes blocks, newest first, as recorded;
- commit candidates come only from markdown code spans in assistant text blocks
  (not fences, tool results, user rows, thinking), and a commit is listed only when
  git confirms it in exactly one bound repo's object store;
- the read is incremental by transcript rev: a new or edited row is picked up, a
  deletion or a change older than the window re-reads it, an unchanged session reads
  no message row; the window is bounded and says when earlier rows were not scanned;
- no run.json is read; diffs are authorized by bindings only.
"""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
from pathlib import Path

import pytest

from core import repo_changes as rc
from core import session_changes as sc


def _git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture(autouse=True)
def _fresh():
    sc.clear_cache()
    rc._IDENT_CACHE = ((), [])
    yield
    sc.clear_cache()


@pytest.fixture
def db(tmp_path):
    from database.sqlite import _TRANSCRIPT_REV_DDL
    path = tmp_path / "data.db"
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE messages (id TEXT PRIMARY KEY, session_id TEXT, role TEXT, content TEXT, "
                 "sent INTEGER NOT NULL DEFAULT 1, metadata TEXT DEFAULT '{}', created_at TEXT)")
    conn.execute("CREATE TABLE compactions (id TEXT PRIMARY KEY, session_id TEXT, summary TEXT)")
    for stmt in _TRANSCRIPT_REV_DDL:
        conn.execute(stmt)
    conn.commit()
    conn.close()
    return str(path)


def put(db, row_id, at, blocks, *, role="assistant", sent=1, session="s1"):
    conn = sqlite3.connect(db)
    conn.execute("INSERT OR REPLACE INTO messages (id, session_id, role, content, sent, created_at) "
                 "VALUES (?,?,?,?,?,?)", (row_id, session, role, json.dumps(blocks), sent, at))
    conn.commit()
    conn.close()


def edit(db, row_id, blocks):
    conn = sqlite3.connect(db)
    conn.execute("UPDATE messages SET content = ? WHERE id = ?", (json.dumps(blocks), row_id))
    conn.commit()
    conn.close()


def delete(db, row_id):
    conn = sqlite3.connect(db)
    conn.execute("DELETE FROM messages WHERE id = ?", (row_id,))
    conn.commit()
    conn.close()


def card(*paths, root="/r", omitted=0):
    b = {"type": "turn_changes", "files": [
        {"path": p, "absolutePath": f"{root}/{p}", "repo": "r", "status": "modified", "added": 1,
         "deleted": 0, "binary": False, "diffable": True, "gitRoot": root, "start": "a" * 40, "end": "b" * 40}
        for p in paths]}
    if omitted:
        b["omitted"] = omitted
    return b


def text(t):
    return {"type": "text", "text": t}


# ── extraction ──────────────────────────────────────────────────────────────

def test_code_span_shas_takes_inline_spans_only():
    t = ("landed `abc1234`, see ``DEF5678ab`` and `not-hex` and plain 1234567abc\n"
         "~~~\nin a fence: `fedcba9876`\n~~~\n\\`aaaaaaa\\` and `" + "e" * 64 + "`")
    assert sc.code_span_shas(t) == ["abc1234", "def5678ab", "e" * 64]


def test_code_span_shas_ignores_too_short_and_too_long():
    assert sc.code_span_shas("`abc123` `" + "a" * 41 + "` `abcdef0`") == ["abcdef0"]


def test_row_facts_reads_cards_and_text_but_not_tool_results_or_thinking():
    blocks = [card("a.py", omitted=3), text("did `abcdef1`"),
              {"type": "thinking", "thinking": "`abcdef2`"},
              {"type": "tool_result", "content": "`abcdef3`"}]
    f = sc.row_facts("m1", "t1", json.dumps(blocks))
    assert [c["id"] for c in f["cards"]] == ["m1:0"]
    assert f["cards"][0]["omitted"] == 3 and f["cards"][0]["files"][0]["gitRoot"] == "/r"
    assert f["shas"] == ["abcdef1"]
    assert sc.row_facts("m2", "t", json.dumps([text("nothing here")])) is None
    assert sc.row_facts("m3", "t", "not json") is None


# ── the read ────────────────────────────────────────────────────────────────

def test_turns_are_the_cards_newest_first_from_assistant_sent_rows_only(db, monkeypatch):
    monkeypatch.setattr(rc, "allowed_roots", lambda: {})
    put(db, "m1", "2026-10-24T10:00", [card("a.py")])
    put(db, "m2", "2026-10-24T11:00", [card("b.py")])
    put(db, "u1", "2026-10-24T11:30", [card("user.py")], role="user")
    put(db, "p1", "2026-10-24T12:00", [card("pending.py")], sent=0)
    put(db, "o1", "2026-10-24T12:30", [card("other.py")], session="s2")
    out = sc.read("s1", db)
    assert [t["files"][0]["path"] for t in out["turns"]] == ["b.py", "a.py"]
    assert out["scannedAll"] is True and out["olderTurns"] == 0 and out["commits"] == []


def test_a_new_row_an_edit_and_a_delete_are_each_picked_up(db, monkeypatch):
    monkeypatch.setattr(rc, "allowed_roots", lambda: {})
    put(db, "m1", "t1", [card("a.py")])
    assert len(sc.read("s1", db)["turns"]) == 1
    put(db, "m2", "t2", [card("b.py")])
    assert [t["files"][0]["path"] for t in sc.read("s1", db)["turns"]] == ["b.py", "a.py"]
    edit(db, "m1", [card("a2.py")])
    assert [t["files"][0]["path"] for t in sc.read("s1", db)["turns"]] == ["b.py", "a2.py"]
    delete(db, "m2")
    assert [t["files"][0]["path"] for t in sc.read("s1", db)["turns"]] == ["a2.py"]


def test_an_unchanged_session_reads_no_message_row(db, monkeypatch):
    monkeypatch.setattr(rc, "allowed_roots", lambda: {})
    put(db, "m1", "t1", [card("a.py")])
    sc.read("s1", db)
    calls = []
    real = sc._read_window
    monkeypatch.setattr(sc, "_read_window", lambda *a: calls.append("window") or real(*a))
    monkeypatch.setattr(sc, "_apply_increment", lambda *a: calls.append("incr") or True)
    sc.read("s1", db)
    assert calls == []


def test_the_window_is_bounded_and_says_earlier_rows_were_not_scanned(db, monkeypatch):
    monkeypatch.setattr(rc, "allowed_roots", lambda: {})
    monkeypatch.setattr(sc, "PAGE_ROWS", 2)
    monkeypatch.setattr(sc, "WINDOW_ROWS", 4)
    for i in range(7):
        put(db, f"m{i}", f"t{i}", [card(f"f{i}.py")])
    out = sc.read("s1", db)
    assert [t["files"][0]["path"] for t in out["turns"]] == ["f6.py", "f5.py", "f4.py", "f3.py"]
    assert out["scannedAll"] is False


def test_an_edit_older_than_the_window_rereads_it(db, monkeypatch):
    monkeypatch.setattr(rc, "allowed_roots", lambda: {})
    monkeypatch.setattr(sc, "PAGE_ROWS", 2)
    monkeypatch.setattr(sc, "WINDOW_ROWS", 2)
    for i in range(4):
        put(db, f"m{i}", f"t{i}", [card(f"f{i}.py")])
    sc.read("s1", db)
    edit(db, "m0", [card("old.py")])
    windows = []
    real = sc._read_window
    monkeypatch.setattr(sc, "_read_window", lambda *a: windows.append(1) or real(*a))
    sc.read("s1", db)
    assert windows == [1]


def test_turn_cap_counts_older_turns(db, monkeypatch):
    monkeypatch.setattr(rc, "allowed_roots", lambda: {})
    monkeypatch.setattr(sc, "MAX_TURNS", 2)
    for i in range(3):
        put(db, f"m{i}", f"t{i}", [card(f"f{i}.py")])
    out = sc.read("s1", db)
    assert len(out["turns"]) == 2 and out["olderTurns"] == 1


# ── commits and diffs (real git) ─────────────────────────────────────────────

@pytest.fixture
def repo(tmp_path):
    origin = tmp_path / "origin.git"
    _git(tmp_path, "init", "-q", "--bare", "-b", "main", str(origin))
    work = tmp_path / "proj"
    _git(tmp_path, "clone", "-q", str(origin), str(work))
    for k, v in (("user.email", "t@t"), ("user.name", "t")):
        _git(work, "config", k, v)
    (work / "a.py").write_text("one\n")
    _git(work, "add", "-A"); _git(work, "commit", "-qm", "first")
    _git(work, "push", "-q", "-u", "origin", "main")
    (work / "b.py").write_text("two\n")
    _git(work, "add", "-A"); _git(work, "commit", "-qm", "second (local)")
    return Path(os.path.realpath(work))


def test_a_mentioned_commit_is_listed_only_when_git_confirms_it(db, repo, monkeypatch):
    monkeypatch.setattr(rc, "allowed_roots", lambda: {str(repo): "proj"})
    first = _git(repo, "rev-parse", "HEAD~1")
    second = _git(repo, "rev-parse", "HEAD")
    tree = _git(repo, "rev-parse", "HEAD^{tree}")
    put(db, "m1", "t1", [text(f"pushed `{first[:9]}`, tree `{tree[:9]}`, bogus `deadbee0`")])
    put(db, "m2", "t2", [text(f"then `{second[:12]}` and again `{first[:7]}`")])
    put(db, "u1", "t3", [text(f"`{second}`")], role="user")
    out = sc.read("s1", db)["commits"]
    assert [(c["sha"], c["subject"]) for c in out] == [(second, "second (local)"), (first, "first")]
    assert [c["mentionedAt"] for c in out] == ["t2", "t2"]
    assert out[0]["pushed"] is False and out[1]["pushed"] is True
    assert out[0]["files"] == [{"path": "b.py", "added": 1, "deleted": 0}]
    assert out[0]["root"] == str(repo) and out[0]["repo"] == "proj" and out[0]["omittedFiles"] == 0


def test_a_commit_in_two_bound_repos_is_left_off(db, repo, tmp_path, monkeypatch):
    other = tmp_path / "copy"
    _git(tmp_path, "clone", "-q", str(repo), str(other))
    sha = _git(repo, "rev-parse", "HEAD~1")
    monkeypatch.setattr(rc, "allowed_roots", lambda: {str(repo): "proj", os.path.realpath(other): "copy"})
    put(db, "m1", "t1", [text(f"`{sha[:9]}`")])
    assert sc.read("s1", db)["commits"] == []


def test_two_worktrees_of_one_store_are_one_repo(db, repo, tmp_path, monkeypatch):
    wt = tmp_path / "wt"
    _git(repo, "worktree", "add", "-q", "-b", "side", str(wt))
    sha = _git(repo, "rev-parse", "HEAD")
    monkeypatch.setattr(rc, "allowed_roots", lambda: {str(repo): "proj", os.path.realpath(wt): "proj-wt"})
    put(db, "m1", "t1", [text(f"`{sha[:9]}`")])
    out = sc.read("s1", db)["commits"]
    assert [c["sha"] for c in out] == [sha] and out[0]["root"] == str(repo)
    assert rc.authorized_root(str(repo)) == str(repo)


def test_tree_diff_is_authorized_by_bindings_only(repo, tmp_path, monkeypatch):
    start = _git(repo, "rev-parse", "HEAD~1^{tree}")
    end = _git(repo, "rev-parse", "HEAD^{tree}")
    monkeypatch.setattr(rc, "allowed_roots", lambda: {})
    assert sc.tree_diff(str(repo), start, end, "b.py") is None
    monkeypatch.setattr(rc, "allowed_roots", lambda: {str(repo): "proj"})
    rc._IDENT_CACHE = ((), [])
    assert "+two" in sc.tree_diff(str(repo), start, end, "b.py")
    assert sc.tree_diff(str(repo), start, end, "../x") is None
    assert sc.tree_diff(str(repo), "bad", end, "b.py") is None


def test_commit_diff_no_longer_falls_back_to_code_roots(repo, monkeypatch):
    from core import turn_changes as tc
    monkeypatch.setattr(rc, "allowed_roots", lambda: {})
    monkeypatch.setattr(tc, "allowed_repo", lambda r: True)
    with pytest.raises(rc.RepoChangesError, match="unknown repo"):
        rc.commit_diff(str(repo), _git(repo, "rev-parse", "HEAD"), "b.py")


def test_the_changes_path_reads_no_run_json(db, repo, monkeypatch):
    import builtins
    real_open = builtins.open

    def guarded(file, *a, **k):
        assert "run.json" not in str(file), "the Changes path must not read run.json"
        return real_open(file, *a, **k)
    monkeypatch.setattr(builtins, "open", guarded)
    monkeypatch.setattr(rc, "allowed_roots", lambda: {str(repo): "proj"})
    put(db, "m1", "t1", [card("a.py", root=str(repo)), text(f"`{_git(repo, 'rev-parse', 'HEAD')[:9]}`")])
    out = sc.read("s1", db)
    assert len(out["turns"]) == 1 and len(out["commits"]) == 1


def test_endpoint_returns_changes_through_fastapi(db, repo, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routers import canvas_changes as router
    import database
    monkeypatch.setattr(rc, "allowed_roots", lambda: {str(repo): "proj"})
    put(db, "m1", "t1", [card("a.py", root=str(repo))])

    class _Sessions:
        async def get(self, sid):
            return {"id": sid}

    class _DB:
        db_path = db
        sessions = _Sessions()
    monkeypatch.setattr(database, "db", _DB())
    app = FastAPI()
    app.include_router(router.router)
    r = TestClient(app).get("/api/canvas/session/s1", params={"parts": "changes"})
    assert r.status_code == 200
    body = r.json()
    assert body["artifacts"] is None
    assert [t["files"][0]["path"] for t in body["changes"]["turns"]] == ["a.py"]
    start = _git(repo, "rev-parse", "HEAD~1^{tree}")
    end = _git(repo, "rev-parse", "HEAD^{tree}")
    r = TestClient(app).get("/api/canvas/changes/tree-diff",
                            params={"root": str(repo), "start": start, "end": end, "path": "b.py"})
    assert r.status_code == 200 and "+two" in r.json()["diff"]
    r = TestClient(app).get("/api/canvas/changes/tree-diff",
                            params={"root": "/elsewhere", "start": start, "end": end, "path": "b.py"})
    assert r.status_code == 404
