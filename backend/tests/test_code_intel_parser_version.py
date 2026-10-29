"""A graph written by an older parser rebuilds itself (design 2026-10-29-code-intel-auto D3).

Every rebuild stores ``PARSER_VERSION``. A graph with symbols and a lower (or no)
version reads stale with a full rebuild suggested, before every other freshness
answer: HEAD equal to the marker, the diff cache, the mtime path. Readers then do
what they do for any stale graph: ``ensure_fresh`` starts the full child and updates
nothing inline; the daemon's summary asks for a child (throttled) and says
"rebuilding"; the daemon recall leg stamps the hits. Build children take one
machine-wide turn to parse; an outdated library code mount is re-indexed by the
mount sweep.
"""
from __future__ import annotations

import subprocess
import threading
import time
from pathlib import Path

import pytest

from core.code_intel import CodeIntelDb
from core.code_intel.graph_store import GraphStore


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(cwd), "-c", "user.email=t@e", "-c", "user.name=t",
                           *args], check=True, capture_output=True, text=True).stdout.strip()


def _repo(path: Path, files: dict[str, str]) -> Path:
    path.mkdir(parents=True)
    _git(path, "init", "-q", "-b", "main")
    for rel, text in files.items():
        (path / rel).write_text(text, encoding="utf-8")
    _git(path, "add", "-A")
    _git(path, "commit", "-q", "-m", "init")
    return path


@pytest.fixture
def built(tmp_path):
    from core.code_intel.build import rebuild
    repo = _repo(tmp_path / "repo", {"a.py": "def a():\n    return 1\n"})
    db = tmp_path / "g.code_intel.db"
    g = GraphStore(db)
    rebuild(g, repo, head=_git(repo, "rev-parse", "HEAD"))
    yield repo, db, g
    g.close()


def _age(g: GraphStore, version: str | None) -> None:
    """Make the graph look written by an older parser."""
    if version is None:
        g._conn.execute("DELETE FROM graph_meta WHERE key = 'parser_version'")
        g._conn.commit()
    else:
        g.set_meta("parser_version", version)


def test_a_rebuild_stores_the_parser_version(built):
    from core.code_intel.build import PARSER_VERSION, parser_outdated
    _repo_, _db, g = built
    assert g.get_meta("parser_version") == str(PARSER_VERSION)
    assert not parser_outdated(g)


@pytest.mark.parametrize("version", [None, "0"])
def test_an_older_graph_at_head_reads_stale_with_a_full_rebuild(built, version):
    from core.code_intel.freshness import check_freshness
    repo, _db, g = built
    assert check_freshness(g).stale is False
    _age(g, version)
    fr = check_freshness(g)
    assert fr.stale and fr.suggest_full_rebuild, fr


def test_the_diff_cache_does_not_hide_an_older_graph(built):
    from core.code_intel.freshness import check_freshness
    repo, _db, g = built
    (repo / "b.py").write_text("def b():\n    return 2\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "b")
    first = check_freshness(g)                       # cached: one file behind
    assert first.stale and not first.suggest_full_rebuild and first.changed_files == ["b.py"]
    _age(g, "0")
    assert check_freshness(g).suggest_full_rebuild


def test_a_non_git_repo_reads_an_older_graph_stale(tmp_path):
    from core.code_intel.build import rebuild
    from core.code_intel.freshness import check_freshness
    src = tmp_path / "plain"
    src.mkdir()
    (src / "a.py").write_text("def a():\n    return 1\n", encoding="utf-8")
    g = GraphStore(tmp_path / "plain.db")
    try:
        rebuild(g, src, head=None)
        _age(g, None)
        fr = check_freshness(g)
        assert fr.stale and fr.suggest_full_rebuild, fr
    finally:
        g.close()


def test_a_graph_with_no_symbols_is_not_outdated(tmp_path):
    """An empty parse never stamps: version-driven staleness there would never end."""
    from core.code_intel.build import parser_outdated
    g = GraphStore(tmp_path / "empty.db")
    try:
        assert not parser_outdated(g)
    finally:
        g.close()


def test_ensure_fresh_starts_the_full_child_and_updates_nothing(built, tmp_path, monkeypatch):
    import core.code_intel.build as build
    repo, db, g = built
    (repo / "b.py").write_text("def b():\n    return 2\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "b")                # one file behind: would update inline
    _age(g, "0")
    spawned = []
    monkeypatch.setattr(build, "spawn_build", lambda *a, **k: spawned.append((a, k)))
    monkeypatch.setattr(build, "update_files", lambda *a, **k: pytest.fail("mixed two parsers"))
    ref = CodeIntelDb(db_path=db, repo="repo", repo_root=repo.resolve())
    assert build.ensure_fresh(tmp_path / "P", ref, g).state == "rebuilding"
    assert spawned == [(("P",), {"repo": "repo", "full": True, "only_if_stale": True})]


def test_the_child_rebuilds_an_older_graph_at_head(built):
    """The ``--if-stale`` child does not skip it as current."""
    from core.code_intel.build import PARSER_VERSION, build_graph
    repo, db, g = built
    _age(g, "0")
    ref = CodeIntelDb(db_path=db, repo="repo", repo_root=repo.resolve())
    assert build_graph(ref, only_if_stale=True) is not None
    assert g.get_meta("parser_version") == str(PARSER_VERSION)


def test_the_summary_says_rebuilding_and_asks_once_per_window(built, monkeypatch, tmp_path):
    import core.code_intel.build as build
    from fastapi.testclient import TestClient
    from fastapi import FastAPI
    import routers.code_intel as rci
    repo, db, g = built
    _age(g, "0")
    ref = CodeIntelDb(db_path=db, repo="repo", repo_root=repo.resolve())
    monkeypatch.setattr(rci, "project_graph", lambda project, repo=None: (ref, g))
    spawned = []
    monkeypatch.setattr(build, "spawn_build", lambda *a, **k: spawned.append((a, k)))
    app = FastAPI()
    app.include_router(rci.router)
    client = TestClient(app)
    for _ in range(3):
        r = client.get("/P/summary")
        assert r.status_code == 200 and r.json()["freshness_status"] == "rebuilding"
    assert spawned == [(("P",), {"repo": "repo", "full": True, "only_if_stale": True})]
    _age(g, str(build.PARSER_VERSION))
    assert client.get("/P/summary").json()["freshness_status"] == "fresh"


def test_the_daemon_recall_leg_stamps_an_older_graph(built, monkeypatch):
    from core import recall_multi
    repo, db, g = built
    _age(g, "0")
    ref = CodeIntelDb(db_path=db, repo="repo", repo_root=repo.resolve())
    hits = recall_multi._graph_codeintel_hits("a", ref, g, 5)
    assert hits and all(h.get("graph_stale") for h in hits), hits
    assert "older parser" in hits[0]["stale_reason"]


def test_build_children_parse_one_at_a_time(built, monkeypatch, tmp_path):
    import core.code_intel.build as build
    repo, db, g = built
    _age(g, "0")
    monkeypatch.setattr(build, "_rebuild_turn_path", lambda: tmp_path / "turn.lock")
    monkeypatch.setattr(build, "REBUILD_TURN_WAIT_S", 30)
    ref = CodeIntelDb(db_path=db, repo="repo", repo_root=repo.resolve())
    done = []
    with build.rebuild_turn() as held:                   # another child is parsing
        assert held
        t = threading.Thread(target=lambda: done.append(build.build_graph(
            ref, only_if_stale=True, queue=True)))
        t.start()
        time.sleep(1.5)
        assert done == [], "parsed while another child held the turn"
        assert g.get_meta("parser_version") == "0"
    t.join(20)
    assert done and done[0] is not None
    assert g.get_meta("parser_version") == str(build.PARSER_VERSION)


def test_a_child_that_never_gets_its_turn_gives_up(built, monkeypatch, tmp_path):
    import core.code_intel.build as build
    repo, db, g = built
    _age(g, "0")
    monkeypatch.setattr(build, "_rebuild_turn_path", lambda: tmp_path / "turn.lock")
    monkeypatch.setattr(build, "REBUILD_TURN_WAIT_S", 1)
    monkeypatch.setattr(build, "rebuild", lambda *a, **k: pytest.fail("parsed without a turn"))
    ref = CodeIntelDb(db_path=db, repo="repo", repo_root=repo.resolve())
    holder = subprocess.Popen(["python3", "-c", (
        "import fcntl,sys,time; f=open(sys.argv[1],'a+'); fcntl.flock(f,fcntl.LOCK_EX); "
        "print('held',flush=True); time.sleep(30)"), str(tmp_path / "turn.lock")],
        stdout=subprocess.PIPE, text=True)
    try:
        assert holder.stdout.readline().strip() == "held"
        assert build.build_graph(ref, only_if_stale=True, queue=True) is None
    finally:
        holder.kill()
        holder.wait()


def test_the_mount_sweep_reindexes_an_outdated_code_mount(tmp_path, monkeypatch):
    import sqlite3
    import core.code_intel.build as build
    import core.library_mounts as lm
    from core.code_intel.build import rebuild
    src = _repo(tmp_path / "lib", {"a.py": "def a():\n    return 1\n"})
    monkeypatch.setattr(lm, "_mounts_dir", lambda: tmp_path / "mounts")
    conn = sqlite3.connect(str(tmp_path / "data.db"))
    conn.row_factory = sqlite3.Row
    store = lm.LibraryMounts(conn)
    store.ensure_table()
    mount_id = store.add_mount(scope="library", path=str(src), kind="code")
    db = lm._mount_db_path(mount_id)
    db.parent.mkdir(parents=True)
    g = GraphStore(db)
    rebuild(g, src, head=None, routes=False, stamp=False)
    spawned = []
    monkeypatch.setattr(build, "spawn_child", lambda args: spawned.append(args))
    lm.refresh_all_mounts(store)
    assert spawned == []                                 # current: nothing to do
    _age(g, "0")
    g.close()
    summary = lm.refresh_all_mounts(store)
    assert spawned == [["-m", "core.library_mounts", "index-code", mount_id]]
    assert summary.get("reindexing") == 1
    conn.close()


def test_a_queued_child_does_not_hold_the_graphs_lock_while_it_waits(built, monkeypatch, tmp_path):
    """A bind (which waits for the graph's lock) or a reader's update must not wait
    behind the whole queue: the child takes its turn first, the graph's lock after."""
    import core.code_intel.build as build
    repo, db, g = built
    _age(g, "0")
    monkeypatch.setattr(build, "_rebuild_turn_path", lambda: tmp_path / "turn.lock")
    monkeypatch.setattr(build, "REBUILD_TURN_WAIT_S", 30)
    ref = CodeIntelDb(db_path=db, repo="repo", repo_root=repo.resolve())
    done = []
    with build.rebuild_turn() as held:
        assert held
        t = threading.Thread(target=lambda: done.append(build.build_graph(
            ref, only_if_stale=True, queue=True)))
        t.start()
        time.sleep(1.0)
        with build.write_lock(db, wait=False) as free:
            assert free, "the queued child holds the graph's lock while it waits"
    t.join(20)
    assert done and done[0] is not None


def test_repeated_reads_of_a_stale_graph_ask_for_one_child(built, tmp_path, monkeypatch):
    import core.code_intel.build as build
    repo, db, g = built
    _age(g, "0")
    spawned = []
    monkeypatch.setattr(build, "spawn_build", lambda *a, **k: spawned.append((a, k)))
    ref = CodeIntelDb(db_path=db, repo="repo", repo_root=repo.resolve())
    for _ in range(3):
        assert build.ensure_fresh(tmp_path / "P", ref, g).state == "rebuilding"
    assert len(spawned) == 1


def test_a_cut_report_says_how_many_signature_changes_were_left(tmp_path):
    from core.code_intel.changed import Report, render
    rep = Report(repo="r", project="P", graph_repo="r", indexed_commit="abc", files=["a.py"],
                 unexamined=5, unexamined_signature=2)
    text = render(rep)
    assert "5 more changed symbols were not looked up, 2 of them signature changes" in text


def test_a_mount_child_waits_for_its_turn(monkeypatch, tmp_path):
    import contextlib
    import core.code_intel.build as build
    import core.library_mounts as lm
    monkeypatch.setattr("jobs.paths.DB_PATH", tmp_path / "data.db")
    monkeypatch.setattr(build, "rebuild_turn", lambda: contextlib.nullcontext(False))
    monkeypatch.setattr(lm, "index_code_mount", lambda *a: pytest.fail("parsed without a turn"))
    assert lm.main(["index-code", "m1"]) == 1
