"""core/session_changes — Canvas Changes from the session's own transcript
(docs/design/canvas-changes-from-turns.md v4).

Methodology: a throwaway SQLite file with the real schema-v15 revision triggers
(database.sqlite._TRANSCRIPT_REV_DDL) over a minimal messages table, rows written with
plain sqlite3; real git in tmp_path for commits and tree diffs, bound through a patched
repo_changes.allowed_roots. No real repository or database is read.

Invariants:
- cards are the persisted turn_changes blocks, newest first, as recorded;
- landings come only from core/session_landings (Canvas limits, cached by the bound
  checkouts' branch tips); prose is never read for commits, so a text-only row is not
  even a candidate;
- the read is incremental by transcript rev: a new or edited row is picked up, a
  deletion or a change older than the window re-reads it, an unchanged session reads
  no message row; the window is bounded and says when earlier rows were not scanned;
- no run.json is read; diffs are authorized by bindings only;
- working_repo (the terminal's default) is the newest card's first gitRoot that is a
  bound main checkout; unbound roots are skipped, none found is None.
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





def test_row_facts_reads_cards_only():
    blocks = [card("a.py", omitted=3), text("did `abcdef1`"),
              {"type": "thinking", "thinking": "`abcdef2`"},
              {"type": "tool_result", "content": "`abcdef3`"}]
    f = sc.row_facts("m1", "t1", json.dumps(blocks))
    assert f == {"cards": [f["cards"][0]]} and f["cards"][0]["id"] == "m1:0"
    assert f["cards"][0]["omitted"] == 3 and f["cards"][0]["files"][0]["gitRoot"] == "/r"
    assert sc.row_facts("m2", "t", json.dumps([text("landed `abcdef1`")])) is None
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
    assert out["scannedAll"] is True and out["olderTurns"] == 0
    assert out["landed"] == [] and out["unknown"] == [] and "commits" not in out


def test_landed_comes_from_session_landings_with_canvas_limits(db, monkeypatch):
    from core import session_landings
    asked = []
    def _read(session_id, *, deadline, turn_id=None, cap, since_days, limit):
        asked.append((session_id, turn_id, cap, since_days, limit))
        return {"landed": [{"branch": "session/x", "new": "n" * 40}], "unverified": 2,
                "truncated": True, "unknown": [{"repo": "r", "reason": "no-reflog"}]}
    monkeypatch.setattr(session_landings, "read_session", _read)
    monkeypatch.setattr(sc, "_tips", lambda: (("/r", "refs/heads/main", "a" * 40),))
    out = sc.read("s1", db)
    assert asked == [("s1", None, session_landings.CANVAS_CAP, sc.LANDED_DAYS, sc.LANDED_MAX)]
    assert out["landed"] == [{"branch": "session/x", "new": "n" * 40}]
    assert out["landedTruncated"] is True and out["unverified"] == 2
    assert out["unknown"] == [{"repo": "r", "reason": "no-reflog"}]
    sc.read("s1", db)
    assert len(asked) == 1, "unchanged tips: cached"
    monkeypatch.setattr(sc, "_tips", lambda: (("/r", "refs/heads/main", "b" * 40),))
    sc.read("s1", db)
    assert len(asked) == 2, "a tip moved: read again"


def test_a_text_only_row_is_not_a_candidate(db, monkeypatch):
    """Commit ids in prose are no longer read (design 2026-10-25-turn-result-card.md §7)."""
    monkeypatch.setattr(rc, "allowed_roots", lambda: {})
    put(db, "m1", "t1", [text("landed `abcdef1`")])
    import sqlite3 as _s
    conn = _s.connect(db)
    hits = conn.execute("SELECT count(*) FROM messages m WHERE " + sc._CANDIDATE, ("s1",)).fetchone()[0]
    conn.close()
    assert hits == 0


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


def test_a_row_over_the_byte_cap_is_not_read_and_counts_as_not_scanned(db, monkeypatch):
    monkeypatch.setattr(rc, "allowed_roots", lambda: {})
    monkeypatch.setattr(sc, "ROW_MAX_BYTES", 2000)
    put(db, "m1", "t1", [card("small.py")])
    put(db, "m2", "t2", [card("big.py"), text("x" * 5000)])
    out = sc.read("s1", db)
    assert [t["files"][0]["path"] for t in out["turns"]] == ["small.py"]
    assert out["scannedAll"] is False
    put(db, "m3", "t3", [card("later.py"), text("y" * 5000)])        # by increment too
    assert [t["files"][0]["path"] for t in sc.read("s1", db)["turns"]] == ["small.py"]


def test_the_byte_budget_is_checked_before_content_is_fetched(db, monkeypatch):
    monkeypatch.setattr(rc, "allowed_roots", lambda: {})
    monkeypatch.setattr(sc, "WINDOW_BYTES", 1500)
    for i in range(4):
        put(db, f"m{i}", f"t{i}", [card(f"f{i}.py"), text("z" * 400)])
    out = sc.read("s1", db)
    assert 1 <= len(out["turns"]) < 4 and out["scannedAll"] is False


def test_row_facts_are_capped_per_row():
    many = {"type": "turn_changes", "files": [{"path": f"f{i}.py"} for i in range(70)], "omitted": 5}
    f = sc.row_facts("m", "t", json.dumps([many] * 5))
    assert len(f["cards"]) == sc.ROW_MAX_CARDS
    assert len(f["cards"][0]["files"]) == 60 and f["cards"][0]["omitted"] == 15


def test_the_session_cache_is_bounded_by_count(db, monkeypatch):
    monkeypatch.setattr(rc, "allowed_roots", lambda: {})
    monkeypatch.setattr(sc, "MAX_SESSIONS", 3)
    for i in range(6):
        sc.read(f"empty{i}", db)
    assert len(sc._SESSIONS) == 3




def test_concurrent_reads_of_one_session_are_consistent(db, monkeypatch):
    import threading
    monkeypatch.setattr(rc, "allowed_roots", lambda: {})
    for i in range(30):
        put(db, f"m{i}", f"t{i:02d}", [card(f"f{i}.py")])
    errors, results = [], []

    def worker():
        try:
            for _ in range(20):
                results.append(len(sc.read("s1", db)["turns"]))
        except Exception as exc:  # noqa: BLE001 — the test records it
            errors.append(exc)
    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for i in range(30, 40):
        put(db, f"m{i}", f"t{i:02d}", [card(f"f{i}.py")])
    for t in threads:
        t.join()
    assert errors == []
    assert all(30 <= n <= 40 for n in results)
    assert len(sc.read("s1", db)["turns"]) == 40




# ── diffs and repo identity (real git) ─────────────────────────────────────────────

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






def test_two_worktrees_of_one_store_are_one_repo(repo, tmp_path, monkeypatch):
    wt = tmp_path / "wt"
    _git(repo, "worktree", "add", "-q", "-b", "side", str(wt))
    monkeypatch.setattr(rc, "allowed_roots", lambda: {str(repo): "proj", os.path.realpath(wt): "proj-wt"})
    [ident] = rc.repo_identities()
    assert ident["root"] == str(repo) and rc.authorized_root(str(repo)) == str(repo)


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




def test_bad_input_costs_no_git(repo, monkeypatch):
    called = []
    monkeypatch.setattr(rc, "repo_identities", lambda: called.append(1) or [])
    assert sc.tree_diff(str(repo), "bad", "b" * 40, "b.py") is None
    assert sc.tree_diff(str(repo), "a" * 40, "b" * 40, "../x") is None
    assert called == []


def test_a_replaced_repo_at_the_same_path_is_looked_up_again(repo, tmp_path, monkeypatch):
    import shutil
    monkeypatch.setattr(rc, "allowed_roots", lambda: {str(repo): "proj"})
    first = rc.repo_identities()[0]["store"]
    backup = tmp_path / "gone"
    shutil.move(str(repo / ".git"), str(backup))
    _git(repo, "init", "-q")
    assert rc.repo_identities()[0]["store"] != first


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
    assert len(out["turns"]) == 1 and "commits" not in out


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


def test_working_repo_is_the_newest_cards_first_bound_main_checkout(db, repo, tmp_path, monkeypatch):
    """The terminal's working repo comes from the transcript, not a run: newest card
    first, its files in order; a root outside the bindings (SwarmWS, a scratch clone)
    or one that is not a main checkout is skipped; nothing bound is None."""
    other = Path(os.path.realpath(tmp_path)) / "other"
    _git(tmp_path, "init", "-q", str(other))
    wt = Path(os.path.realpath(tmp_path)) / "wt"
    _git(repo, "worktree", "add", "-q", "-b", "side", str(wt))
    monkeypatch.setattr(rc, "allowed_roots", lambda: {str(repo): "proj", str(other): "other", str(wt): "proj"})
    assert sc.working_repo("s1", db) is None                                   # no card: none
    put(db, "m1", "t1", [card("a.py", root=str(other))])
    assert sc.working_repo("s1", db) == {"path": str(other), "name": "other"}
    put(db, "m2", "t2", [card("x.md", root="/not/bound"), card("a.py", root=str(repo))])
    assert sc.working_repo("s1", db) == {"path": str(repo), "name": "proj"}    # newest card, unbound skipped
    put(db, "m3", "t3", [card("a.py", root=str(wt))])                          # a linked worktree: never
    assert sc.working_repo("s1", db)["path"] == str(repo)
    put(db, "m4", "t4", [card("x.md", root="/not/bound")])
    assert sc.working_repo("s1", db)["path"] == str(repo)
    assert sc.working_repo("s2", db) is None


def test_working_repo_endpoint_through_fastapi(db, repo, monkeypatch):
    """GET /api/canvas/session/{id}/working-repo, unmocked down to the transcript."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routers import canvas_changes as router
    import database
    monkeypatch.setattr(rc, "allowed_roots", lambda: {str(repo): "proj"})
    put(db, "m1", "t1", [card("a.py", root=str(repo))])

    class _DB:
        db_path = db
    monkeypatch.setattr(database, "db", _DB())
    app = FastAPI()
    app.include_router(router.router)
    r = TestClient(app).get("/api/canvas/session/s1/working-repo")
    assert r.status_code == 200 and r.json() == {"workingRepo": {"path": str(repo), "name": "proj"}}
    assert TestClient(app).get("/api/canvas/session/nobody/working-repo").json() == {"workingRepo": None}


def test_reads_of_one_session_are_serialized_and_other_sessions_do_not_wait(db, monkeypatch):
    import threading
    import time as _time
    monkeypatch.setattr(rc, "allowed_roots", lambda: {})
    real = sc._read_window
    live: dict[str, int] = {}
    peak: dict[str, int] = {}
    overlap = []
    lock = threading.Lock()

    def slow(conn, sid, rev):
        with lock:
            live[sid] = live.get(sid, 0) + 1
            peak[sid] = max(peak.get(sid, 0), live[sid])
            if len([s for s, n in live.items() if n]) > 1:
                overlap.append(True)
        _time.sleep(0.15)
        try:
            return real(conn, sid, rev)
        finally:
            with lock:
                live[sid] -= 1
    monkeypatch.setattr(sc, "_read_window", slow)
    put(db, "a1", "t1", [card("a.py")], session="A")
    put(db, "b1", "t1", [card("b.py")], session="B")

    def go(sid):
        with sc._SESSIONS_LOCK:
            sc._SESSIONS.pop(sid, None)      # force a window read
        sc.read(sid, db)
    threads = [threading.Thread(target=go, args=(s,)) for s in ("A", "A", "B")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert peak["A"] == 1            # the two reads of A never ran at once
    assert overlap                   # B ran while A did: no global lock