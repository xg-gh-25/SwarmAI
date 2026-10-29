"""The one full rebuild and the one incremental update (core.code_intel.build).

Each entry point used to write the graph its own way: two rebuilds stored no routes,
the Brain Hub re-index marked an emptied graph fresh, clear() kept the routes of
deleted handlers, an incremental writer moved its commit marker past files it never
indexed and kept a renamed file's old nodes. Now the rebuild child and the job call
``rebuild``, and every incremental write is ``ensure_fresh`` → ``update_files``.
Driven with real repos and real GraphStores; only the parse is replaced where a test
needs it empty.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from core.code_intel.graph_store import GraphStore
from core.code_intel import CodeIntelDb as _CIDb
_REF = _CIDb(db_path=Path("code_intel.db"))  # a project db ref for loader mocks

_ROUTER = ('from fastapi import APIRouter\n\nrouter = APIRouter()\n\n'
           '@router.get("/items")\ndef list_items():\n    return []\n')


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(cwd), *args], check=True,
                          capture_output=True, text=True).stdout


def _repo(path: Path, files: dict[str, str]) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-q", "-b", "main")
    _git(path, "config", "user.email", "t@example.com")
    _git(path, "config", "user.name", "t")
    for rel, text in files.items():
        (path / rel).parent.mkdir(parents=True, exist_ok=True)
        (path / rel).write_text(text, encoding="utf-8")
    _git(path, "add", "-A")
    _git(path, "commit", "-q", "-m", "init")
    return path


def _route_paths(graph: GraphStore) -> set[tuple[str, str]]:
    return {(r[0], r[1]) for r in graph._conn.execute(
        "SELECT path, file_path FROM code_routes").fetchall()}


def _empty_parse():
    from core.code_intel.parser import ParseRepoResult
    return patch("core.code_intel.parser.parse_repo_with_coverage",
                 return_value=ParseRepoResult(results=[], coverage_holes=[], status="complete"))


# ── rebuild ──────────────────────────────────────────────────────────────────

class TestRebuild:
    def test_an_empty_parse_leaves_the_graph_and_its_stamp(self, tmp_path):
        from core.code_intel.build import rebuild
        repo = _repo(tmp_path / "r", {"a.py": "def a():\n    return 1\n"})
        g = GraphStore(tmp_path / "g.db")
        try:
            rebuild(g, repo, head="old")
            with _empty_parse():
                out = rebuild(g, repo, head="new")
            assert out.empty is True
            assert g.get_nodes_by_file("a.py")
            assert g.get_meta("last_indexed_commit") == "old"
        finally:
            g.close()

    def test_a_first_empty_parse_still_records_the_repo(self, tmp_path):
        from core.code_intel.build import rebuild
        repo = _repo(tmp_path / "r", {"README.md": "docs only\n"})
        g = GraphStore(tmp_path / "g.db")
        try:
            assert rebuild(g, repo, head="h").empty is True
            assert g.get_meta("repo_root") == str(repo.resolve())
            assert g.get_meta("last_indexed_commit") is None   # never stamped fresh
        finally:
            g.close()

    def test_a_repo_with_no_parseable_source_records_why(self, tmp_path):
        """A graph that never had a node says why: the language no parser reads."""
        import json
        from core.code_intel.build import rebuild
        repo = _repo(tmp_path / "r", {"model/main.smithy": "service S {}\n"})
        g = GraphStore(tmp_path / "g.db")
        try:
            assert rebuild(g, repo, head="h").empty is True
            holes = json.loads(g.get_meta("coverage_holes") or "[]")
            assert g.get_meta("parse_status") == "partial"
            assert any(h["ref"] == "model/main.smithy" for h in holes), holes
            assert g.get_meta("coverage_commit") == "h"
        finally:
            g.close()

    def test_an_empty_parse_keeps_the_coverage_of_a_graph_with_nodes(self, tmp_path):
        from core.code_intel.build import rebuild
        repo = _repo(tmp_path / "r", {"a.py": "def a():\n    return 1\n"})
        g = GraphStore(tmp_path / "g.db")
        try:
            rebuild(g, repo, head="old")
            before = (g.get_meta("parse_status"), g.get_meta("coverage_holes"))
            from core.code_intel.parser import ParseRepoResult
            hole = {"ref": str(repo), "kind": "repo", "reason": "nothing to parse"}
            with patch("core.code_intel.parser.parse_repo_with_coverage",
                       return_value=ParseRepoResult(results=[], coverage_holes=[hole],
                                                    status="partial")):
                rebuild(g, repo, head="new")
            assert (g.get_meta("parse_status"), g.get_meta("coverage_holes")) == before
        finally:
            g.close()

    def test_routes_are_replaced_and_coverage_is_stored(self, tmp_path):
        from core.code_intel.build import rebuild
        repo = _repo(tmp_path / "r", {"api/items.py": _ROUTER})
        g = GraphStore(tmp_path / "g.db")
        try:
            g.insert_routes([{"id": "gone", "method": "GET", "path": "/gone",
                              "handler_node_id": "x::h", "framework": "fastapi",
                              "file_path": "api/deleted.py", "line_number": 1,
                              "middleware": None}])
            out = rebuild(g, repo, head="h")
            assert _route_paths(g) == {("/items", "api/items.py")}
            assert out.routes == 1
            assert g.get_meta("parse_status") and g.get_meta("coverage_holes") is not None
            assert g.get_meta("last_indexed_commit") == "h"
        finally:
            g.close()

    def test_routes_false_and_stamp_false(self, tmp_path):
        from core.code_intel.build import rebuild
        repo = _repo(tmp_path / "r", {"api/items.py": _ROUTER})
        g = GraphStore(tmp_path / "g.db")
        try:
            rebuild(g, repo, head="h", routes=False, stamp=False)
            assert _route_paths(g) == set()
            assert g.get_meta("last_full_index") is None
            assert g.get_nodes_by_file("api/items.py")
        finally:
            g.close()


def test_clear_empties_routes(tmp_path):
    g = GraphStore(tmp_path / "g.db")
    try:
        g.insert_routes([{"id": "r", "method": "GET", "path": "/x", "handler_node_id": "x::h",
                          "framework": "fastapi", "file_path": "a.py", "line_number": 1,
                          "middleware": None}])
        g.clear()
        assert _route_paths(g) == set()
    finally:
        g.close()


# ── the entry points ─────────────────────────────────────────────────────────

def test_bind_stores_routes(tmp_path):
    from core.ddd_bindings import Binding, DeliveryContract, bind_repo
    src = _repo(tmp_path / "src", {"api/items.py": _ROUTER})
    binding = Binding(repo="svc", kind="external", clone=str(src),
                      delivery_contract=DeliveryContract(
                          remote_kind="github-pr", branch="main",
                          review_path="s_internal-crux-review", auto_send="on-clean-review"))
    result = bind_repo(binding, tmp_path / "bindings")
    g = GraphStore(Path(result.code_intel_db))
    try:
        assert ("/items", "api/items.py") in _route_paths(g)
        assert g.get_meta("last_indexed_commit") == _git(src, "rev-parse", "HEAD").strip()
    finally:
        g.close()


@pytest.fixture
def local_project(tmp_path, monkeypatch, own_local_root):
    """A project that owns a local checkout (a confirmed local root) with a built graph."""
    from core.code_intel import invalidate_cache
    from core.code_intel.build import rebuild
    from core.code_intel.local_roots import db_path
    projects = tmp_path / "ws" / "Projects"   # a local root may not sit inside SwarmWS
    proj = projects / "P"
    proj.mkdir(parents=True)
    repo = _repo(tmp_path / "repo", {"api/items.py": _ROUTER, "a.py": "def a():\n    return 1\n"})
    own_local_root("P", repo)
    g = GraphStore(db_path(repo))
    rebuild(g, repo, head="first")
    g.close()
    monkeypatch.setattr("jobs.paths.PROJECTS_DIR", projects)
    invalidate_cache("P")
    yield proj, repo
    invalidate_cache("P")


def _reopen(repo: Path) -> GraphStore:
    from core.code_intel.local_roots import db_path
    return GraphStore(db_path(repo))


def test_the_rebuild_child_leaves_an_empty_parse_unstamped(local_project):
    """``python -m core.code_intel.build --full`` (what the re-index button starts)."""
    from core.code_intel.build import main
    proj, repo = local_project
    with _empty_parse():
        assert main(["--project", "P", "--full"]) == 0
    g = _reopen(repo)
    try:
        assert g.get_nodes_by_file("a.py"), "an empty parse cleared the graph"
        assert g.get_meta("last_indexed_commit") == "first"
    finally:
        g.close()


def test_the_rebuild_child_stores_routes(local_project):
    from core.code_intel.build import main
    proj, repo = local_project
    g = _reopen(repo)
    g._conn.execute("DELETE FROM code_routes")
    g._conn.commit()
    g.close()
    assert main(["--project", "P", "--full"]) == 0
    g = _reopen(repo)
    try:
        assert ("/items", "api/items.py") in _route_paths(g)
    finally:
        g.close()


def test_the_library_mount_is_rebuilt_without_routes_or_stamp(tmp_path, monkeypatch):
    import core.library_mounts as lm
    src = _repo(tmp_path / "lib", {"api/items.py": _ROUTER})
    store = MagicMock()
    store.get_mount.return_value = {"kind": "code", "path": str(src)}
    monkeypatch.setattr(lm, "_mount_db_path", lambda mid: tmp_path / "m" / f"{mid}.db")
    out = lm.index_code_mount(store, "m1")
    assert out["status"] == "indexed" and out["symbols"] > 0
    g = GraphStore(tmp_path / "m" / "m1.db")
    try:
        assert _route_paths(g) == set()
        assert g.get_meta("mount_id") == "m1"
        assert g.get_meta("last_full_index") is None
    finally:
        g.close()
        with lm._mount_cache_lock:
            cached = lm._mount_graph_cache.pop("m1", None)
        if cached is not None:
            cached.close()


# ── update_files and its callers ─────────────────────────────────────────────

class TestUpdateFiles:
    def test_routes_are_stored_for_updated_files(self, tmp_path):
        from core.code_intel.build import rebuild, update_files
        repo = _repo(tmp_path / "r", {"a.py": "def a():\n    return 1\n"})
        g = GraphStore(tmp_path / "g.db")
        try:
            rebuild(g, repo, head="h")
            (repo / "api").mkdir()
            (repo / "api" / "items.py").write_text(_ROUTER, encoding="utf-8")
            out = update_files(g, repo, ["api/items.py"])
            assert out.complete and out.updated == ["api/items.py"]
            assert ("/items", "api/items.py") in _route_paths(g)
            (repo / "api" / "items.py").unlink()
            update_files(g, repo, ["api/items.py"])
            assert _route_paths(g) == set()
        finally:
            g.close()

    def test_too_many_files_store_nothing_and_ask_for_a_rebuild(self, tmp_path):
        from core.code_intel.build import FULL_REBUILD_FILES, update_files
        repo = tmp_path / "r"
        repo.mkdir()
        g = GraphStore(tmp_path / "g.db")
        try:
            files = [f"m{i}.py" for i in range(FULL_REBUILD_FILES + 1)]
            for f in files:
                (repo / f).write_text("def x():\n    return 1\n", encoding="utf-8")
            out = update_files(g, repo, files)
            assert out.needs_full and not out.complete
            assert g.count_files() == 0
        finally:
            g.close()


def _behind(tmp_path, monkeypatch, changed, fail=()):
    """Run ``ensure_fresh`` over a graph that is behind by *changed* files (freshness
    stubbed); *fail* names files whose store fails. Returns (graph, Freshness, spawned)."""
    import core.code_intel.build as build
    import core.code_intel.freshness as fr
    proj = tmp_path / "Projects" / "P"
    repo = proj.parent.parent / "repo"
    repo.mkdir(parents=True)
    proj.mkdir(parents=True)
    for f in changed:
        (repo / f).write_text(f"def {f[:-3]}():\n    return 1\n", encoding="utf-8")
    db = tmp_path / "g.code_intel.db"
    g = GraphStore(db)
    g.set_meta("repo_root", str(repo.resolve()))
    g.set_meta("last_indexed_commit", "old")
    stale = MagicMock(stale=True, suggest_full_rebuild=False, commits_behind=1,
                      changed_files=list(changed), current_head="new", reason="behind")
    monkeypatch.setattr(fr, "check_freshness", lambda graph: stale)
    monkeypatch.setattr(build, "_sync_docs", lambda *a: None)
    spawned = []
    monkeypatch.setattr(build, "spawn_build", lambda *a, **k: spawned.append((a, k)))
    real = g.store_file_nodes_edges

    def store(path, *a, **k):
        if path in fail:
            import sqlite3
            raise sqlite3.OperationalError("database is locked")
        return real(path, *a, **k)
    monkeypatch.setattr(g, "store_file_nodes_edges", store)
    ref = _CIDb(db_path=db, repo="repo", repo_root=repo.resolve())
    return g, build.ensure_fresh(proj, ref, g), spawned


def test_ensure_fresh_indexes_every_changed_file(tmp_path, monkeypatch):
    changed = [f"m{i}.py" for i in range(60)]
    g, fresh, spawned = _behind(tmp_path, monkeypatch, changed)
    try:
        assert fresh.state == "updated" and spawned == []
        assert all(g.get_nodes_by_file(f) for f in changed), "files after the 50th were dropped"
        assert g.get_meta("last_indexed_commit") == "new"
    finally:
        g.close()


def test_ensure_fresh_keeps_its_marker_when_a_file_failed(tmp_path, monkeypatch):
    """The marker stays, and a full rebuild child is started: the same file would fail
    again on every read, and the full parse records it as a coverage hole."""
    g, fresh, spawned = _behind(tmp_path, monkeypatch, ["a.py", "b.py"], fail=("b.py",))
    try:
        assert fresh.state == "rebuilding" and "1 changed files" in fresh.message
        assert spawned == [(("P",), {"repo": "repo", "full": True, "only_if_stale": True})]
        assert g.get_nodes_by_file("a.py")
        assert g.get_meta("last_indexed_commit") == "old"
    finally:
        g.close()


def test_ensure_fresh_drops_a_renamed_files_old_path(tmp_path):
    """Real git rename, real freshness: the old path's nodes go with the marker."""
    import core.code_intel.build as build
    from core.code_intel.build import ensure_fresh, rebuild
    repo = _repo(tmp_path / "r", {"old/mod.py": "def f():\n    return 1\n"})
    g = GraphStore(tmp_path / "g.db")
    try:
        rebuild(g, repo, head=_git(repo, "rev-parse", "HEAD").strip())
        (repo / "new").mkdir()
        _git(repo, "mv", "old/mod.py", "new/mod.py")
        _git(repo, "commit", "-q", "-m", "mv")
        with patch.object(build, "_sync_docs", lambda *a: None):
            fresh = ensure_fresh(tmp_path, _CIDb(db_path=tmp_path / "g.db", repo="r",
                                                 repo_root=repo), g)
        assert fresh.state == "updated"
        assert g.get_nodes_by_file("old/mod.py") == []
        assert g.get_nodes_by_file("new/mod.py")
        assert g.get_meta("last_indexed_commit") == _git(repo, "rev-parse", "HEAD").strip()
    finally:
        g.close()


# ── review round 1 (correctness) ─────────────────────────────────────────────

def test_freshness_lists_a_renamed_files_old_path(tmp_path):
    """ensure_fresh takes its files from check_freshness: with rename
    detection the old path was missing, so its nodes outlived the marker."""
    from core.code_intel.freshness import check_freshness
    repo = _repo(tmp_path / "r", {"old/mod.py": "def f():\n    return 1\n"})
    g = GraphStore(tmp_path / "g.db")
    try:
        g.set_meta("repo_root", str(repo.resolve()))
        g.set_meta("last_indexed_commit", _git(repo, "rev-parse", "HEAD").strip())
        (repo / "new").mkdir()
        _git(repo, "mv", "old/mod.py", "new/mod.py")
        _git(repo, "commit", "-q", "-m", "mv")
        changed = check_freshness(g).changed_files
        assert "old/mod.py" in changed and "new/mod.py" in changed
    finally:
        g.close()


def test_a_prefix_failure_does_not_fail_the_rebuild(tmp_path):
    from core.code_intel.build import rebuild
    repo = _repo(tmp_path / "r", {"api/items.py": _ROUTER})
    g = GraphStore(tmp_path / "g.db")
    try:
        with patch("core.code_intel.resolve_router_prefixes",
                   side_effect=__import__("sqlite3").OperationalError("database is locked")):
            out = rebuild(g, repo, head="h")
        assert not out.empty
        assert ("/items", "api/items.py") in _route_paths(g)
        assert g.get_meta("last_indexed_commit") == "h"
    finally:
        g.close()


def test_one_files_route_store_error_does_not_stop_the_others(tmp_path):
    import sqlite3
    from core.code_intel.build import routes_for
    repo = _repo(tmp_path / "r", {"a.py": _ROUTER, "b.py": _ROUTER.replace("/items", "/other")})
    g = GraphStore(tmp_path / "g.db")
    try:
        real = g.delete_routes_for_file

        def delete(rel):
            if rel == "a.py":
                raise sqlite3.OperationalError("database is locked")
            return real(rel)
        with patch.object(g, "delete_routes_for_file", side_effect=delete):
            routes_for(g, repo, ["a.py", "b.py"])
        assert ("/other", "b.py") in _route_paths(g)
    finally:
        g.close()


def test_a_directory_path_is_skipped_not_failed(tmp_path):
    """A submodule bump lists a directory: as a failure it kept the marker forever."""
    from core.code_intel.build import update_files
    repo = _repo(tmp_path / "r", {"a.py": "def a():\n    return 1\n"})
    (repo / "sub").mkdir()
    g = GraphStore(tmp_path / "g.db")
    try:
        out = update_files(g, repo, ["sub", "a.py"])
        assert out.complete and "sub" in out.skipped
    finally:
        g.close()


def test_ensure_fresh_starts_a_rebuild_child_when_an_update_is_too_big(tmp_path, monkeypatch):
    from core.code_intel.build import FULL_REBUILD_FILES
    changed = [f"m{i}.py" for i in range(FULL_REBUILD_FILES + 1)]
    g, fresh, spawned = _behind(tmp_path, monkeypatch, changed)
    try:
        assert fresh.state == "rebuilding"
        assert spawned == [(("P",), {"repo": "repo", "full": True, "only_if_stale": True})]
        assert not g.get_nodes_by_file("m0.py"), "a big update was parsed in-process"
        assert g.get_meta("last_indexed_commit") == "old"
    finally:
        g.close()


def test_a_rebind_with_an_empty_parse_reports_no_nodes(tmp_path):
    from core.ddd_bindings import Binding, DeliveryContract, bind_repo
    src = _repo(tmp_path / "src", {"a.py": "def a():\n    return 1\n"})
    binding = Binding(repo="svc", kind="external", clone=str(src),
                      delivery_contract=DeliveryContract(
                          remote_kind="github-pr", branch="main",
                          review_path="s_internal-crux-review", auto_send="on-clean-review"))
    assert bind_repo(binding, tmp_path / "bindings").node_count > 0
    with _empty_parse():
        assert bind_repo(binding, tmp_path / "bindings").node_count == 0


def _run_job(tmp_path, monkeypatch, full=True):
    from jobs.handlers.code_intel_reindex import reindex_projects
    monkeypatch.setattr("config.get_swarmws", lambda: tmp_path / "ws")
    return reindex_projects(full=full)


def test_the_job_leaves_an_empty_parse_unstamped(local_project, tmp_path, monkeypatch):
    proj, repo = local_project
    with _empty_parse():
        out = _run_job(tmp_path, monkeypatch)
    assert [r["status"] for r in out["projects"] if r["project"] == "P"] == ["empty"], out
    g = _reopen(repo)
    try:
        assert g.count_files() > 0 and g.get_meta("last_indexed_commit") == "first"
    finally:
        g.close()


def test_the_job_rebuild_stores_routes(local_project, tmp_path, monkeypatch):
    proj, repo = local_project
    g = _reopen(repo)
    g._conn.execute("DELETE FROM code_routes")
    g._conn.commit()
    g.close()
    out = _run_job(tmp_path, monkeypatch)
    assert [r["status"] for r in out["projects"] if r["project"] == "P"] == ["built"], out
    g = _reopen(repo)
    try:
        assert ("/items", "api/items.py") in _route_paths(g)
    finally:
        g.close()


def test_ensure_fresh_never_applies_one_repos_diff_to_another(tmp_path, monkeypatch):
    """The changed files come from the stored repo_root's history: a graph built from
    another directory is rebuilt, not updated file by file."""
    import core.code_intel.build as build
    g, _fresh, spawned = _behind(tmp_path, monkeypatch, ["a.py"])
    try:
        other = tmp_path / "other"
        other.mkdir()
        (other / "a.py").write_text("def other():\n    return 0\n", encoding="utf-8")
        g.set_meta("last_indexed_commit", "old")
        g.remove_file("a.py")
        ref = _CIDb(db_path=tmp_path / "g.code_intel.db", repo="repo", repo_root=other)
        spawned.clear()
        fresh = build.ensure_fresh(tmp_path / "Projects" / "P", ref, g)
        assert fresh.state == "rebuilding" and "another directory" in fresh.message
        assert g.get_nodes_by_file("a.py") == [] and len(spawned) == 1
    finally:
        g.close()


def test_a_rebuild_child_that_got_the_lock_after_a_bind_started_skips(tmp_path, monkeypatch):
    import core.code_intel.build as build
    import core.ddd_bindings as db
    repo = _repo(tmp_path / "r", {"a.py": "def a():\n    return 1\n"})
    calls = iter([False, True])                 # free when asked, bound once locked
    monkeypatch.setattr(db, "bind_in_progress", lambda root: next(calls))
    monkeypatch.setattr(build, "rebuild", lambda *a, **k: pytest.fail("rebuilt during a bind"))
    assert build.build_graph(_CIDb(db_path=tmp_path / "g.db", repo="r", repo_root=repo)) is None
