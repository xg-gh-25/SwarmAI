"""One way in: every reader and writer of a project's code graph reaches every graph.

A project that binds two repos has two graphs. Before ``load_project_graphs`` was the
only loader, the watcher, the reindex job, the health hook, the commit hook and the
API all read the first graph only, so the second repo was never watched or
re-indexed; and a re-bind was handled by closing the cached store under a watcher
that might be querying it (design docs/design/2026-10-29-code-intel-unification.md
§3.3, B2). Driven with real git repos and real GraphStores.
"""
from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

import pytest

from core.code_intel.graph_store import GraphStore


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(cwd), *args], check=True,
                          capture_output=True, text=True).stdout


def _commit(repo: Path, files: dict[str, str], msg: str) -> str:
    for rel, text in files.items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(text, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", msg)
    return _git(repo, "rev-parse", "HEAD").strip()


_BINDING = ("  - repo: {name}\n    kind: external\n    clone: \"https://example.com/{name}.git\"\n"
            "    delivery_contract:\n      remote_kind: github-pr\n      branch: main\n"
            "      review_path: github-pr\n      auto_send: manual-push\n")


@pytest.fixture
def two_repos(tmp_path, monkeypatch):
    """Project Demo binding lib and svc, each cloned and indexed at its HEAD."""
    from core.code_intel import invalidate_cache
    from core.code_intel.build import rebuild
    bindings_root = tmp_path / "bindings"
    monkeypatch.setattr("core.ddd_bindings._default_bindings_root", lambda: bindings_root)
    projects = tmp_path / "Projects"
    project_dir = projects / "Demo"
    project_dir.mkdir(parents=True)
    (project_dir / "bindings.yaml").write_text(
        "bindings:\n" + _BINDING.format(name="lib") + _BINDING.format(name="svc"),
        encoding="utf-8")
    repos = {}
    for name in ("lib", "svc"):
        clone = bindings_root / name
        clone.mkdir(parents=True)
        _git(clone, "init", "-q", "-b", "main")
        _git(clone, "config", "user.email", "t@example.com")
        _git(clone, "config", "user.name", "t")
        head = _commit(clone, {f"{name}.py": f"def {name}_one():\n    return 1\n"}, "init")
        g = GraphStore(bindings_root / f"{name}.code_intel.db")
        rebuild(g, clone, head=head)
        g.close()
        repos[name] = clone
    monkeypatch.setattr("jobs.paths.PROJECTS_DIR", projects)
    monkeypatch.setattr("config.get_swarmws", lambda: tmp_path)
    invalidate_cache()
    yield project_dir, repos
    invalidate_cache()


def _graph(repo: str) -> GraphStore:
    from core.code_intel import project_graph
    return project_graph("Demo", repo)[1]


def _ids(graph: GraphStore, rel: str) -> list[str]:
    return [n["id"] for n in graph.get_nodes_by_file(rel)]


def test_every_graph_is_reached_and_one_can_be_picked(two_repos):
    from core.code_intel import load_project_graphs, project_graph
    assert [ref.repo for ref, _g in load_project_graphs("Demo")] == ["lib", "svc"]
    assert project_graph("Demo")[0].repo == "lib"
    assert _ids(_graph("svc"), "svc.py") == ["svc.py::svc_one"]
    assert project_graph("Demo", "nope") is None


def test_the_job_reindexes_the_second_bound_repo(two_repos):
    from jobs.handlers.code_intel_reindex import reindex_projects
    _project_dir, repos = two_repos
    _commit(repos["svc"], {"svc.py": "def svc_one():\n    return 1\n\ndef svc_two():\n    return 2\n"},
            "add")
    out = reindex_projects()
    assert "svc.py::svc_two" in _ids(_graph("svc"), "svc.py")
    assert {r.get("repo") for r in out["projects"]} == {"lib", "svc"}


def test_the_health_hook_refreshes_the_second_bound_repo(two_repos, tmp_path):
    from hooks.context_health_hook import ContextHealthHook
    _project_dir, repos = two_repos
    head = _commit(repos["svc"], {"svc.py": "def svc_two():\n    return 2\n"}, "edit")
    ContextHealthHook()._refresh_code_intel(tmp_path)
    svc = _graph("svc")
    assert _ids(svc, "svc.py") == ["svc.py::svc_two"]
    assert svc.get_meta("last_indexed_commit") == head


def test_a_commit_lands_in_its_own_repos_graph(two_repos):
    from hooks.code_change_feed import _reindex_changed_files
    _project_dir, repos = two_repos
    (repos["svc"] / "lib.py").write_text("def not_lib():\n    return 0\n", encoding="utf-8")
    assert _reindex_changed_files([("A", "lib.py")], str(repos["svc"]), "Demo") == 1
    assert _ids(_graph("svc"), "lib.py") == ["lib.py::not_lib"]
    assert _ids(_graph("lib"), "lib.py") == ["lib.py::lib_one"]


def test_startup_starts_one_watcher_per_graph(two_repos, monkeypatch):
    import main
    started = []

    async def fake_start(project, root, graph):
        started.append((project, Path(root).name, graph._db_path.name))
        return True

    async def no_sleep(_s):
        return None
    monkeypatch.setattr("core.code_intel.watcher.start_watcher", fake_start)
    monkeypatch.setattr(main.asyncio, "sleep", no_sleep)
    asyncio.run(main._start_code_intel_watchers())
    assert started == [("Demo", "lib", "lib.code_intel.db"),
                       ("Demo", "svc", "svc.code_intel.db")]


def test_the_registry_holds_a_watcher_per_graph_and_stop_stops_them_all(two_repos,
                                                                       monkeypatch):
    import core.code_intel.watcher as w
    from core.code_intel import load_project_graphs
    _project_dir, repos = two_repos

    async def idle(self):
        await asyncio.Event().wait()
    monkeypatch.setattr(w.CodeIntelWatcher, "_watch_loop", idle)
    monkeypatch.setattr(w, "_watchers", {})
    monkeypatch.setattr(w, "_watcher_lock", None)

    async def run():
        for ref, g in load_project_graphs("Demo"):
            assert await w.start_watcher("Demo", ref.repo_root, g)
        assert len(w._watchers) == 2
        await w.stop_watcher("Demo")
        assert w._watchers == {}
    asyncio.run(run())


def test_after_a_rebind_the_watcher_writes_the_new_store_and_the_old_is_closed(two_repos):
    """A db file deleted and re-created gets a new store; the watcher writes that one
    (the one readers see) and the old store is closed, so nothing can write into the
    deleted file or keep it and its WAL alive."""
    from core.code_intel import load_project_graphs
    from core.code_intel.watcher import CodeIntelWatcher
    _project_dir, repos = two_repos
    old = _graph("svc")
    db = old._db_path
    watcher = CodeIntelWatcher("Demo", repos["svc"], old)
    watcher._running = True
    for suffix in ("", "-wal", "-shm"):
        Path(str(db) + suffix).unlink(missing_ok=True)
    GraphStore(db).close()                               # the re-created, empty db
    (repos["svc"] / "svc.py").write_text("def svc_new():\n    return 3\n", encoding="utf-8")
    asyncio.run(watcher._trigger_incremental([repos["svc"] / "svc.py"]))
    current = dict((ref.repo, g) for ref, g in load_project_graphs("Demo"))["svc"]
    assert current is not old
    assert _ids(current, "svc.py") == ["svc.py::svc_new"]
    assert old._conn is None                              # closed


def test_the_watcher_resolves_its_store_off_the_event_loop(two_repos, monkeypatch):
    import threading
    import core.code_intel as ci
    from core.code_intel.watcher import CodeIntelWatcher
    _project_dir, repos = two_repos
    real = ci.load_project_graphs
    threads = []

    def spy(name):
        threads.append(threading.current_thread())
        return real(name)
    monkeypatch.setattr(ci, "load_project_graphs", spy)
    watcher = CodeIntelWatcher("Demo", repos["svc"], _graph("svc"))
    watcher._running = True
    threads.clear()                                       # the test's own lookup above
    (repos["svc"] / "svc.py").write_text("def svc_x():\n    return 1\n", encoding="utf-8")
    asyncio.run(watcher._trigger_incremental([repos["svc"] / "svc.py"]))
    assert threads and threading.main_thread() not in threads


def test_the_api_looks_the_graph_up_off_the_event_loop(two_repos, monkeypatch):
    import threading
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import routers.code_intel as mod
    real = mod.project_graph
    seen = []

    def spy(project, repo=None):
        seen.append(threading.current_thread())
        return real(project, repo)
    monkeypatch.setattr(mod, "project_graph", spy)
    app = FastAPI()
    app.include_router(mod.router, prefix="/api/code-intel")
    loop_threads = []

    @app.get("/loop-thread")
    async def loop_thread():
        loop_threads.append(threading.current_thread())
        return {}
    with TestClient(app) as client:                      # one portal: one loop thread
        client.get("/loop-thread")
        assert client.get("/api/code-intel/Demo/summary").status_code == 200
    assert seen and loop_threads and loop_threads[0] not in seen


def _commit_both(repos):
    for name in ("lib", "svc"):
        _commit(repos[name], {f"{name}.py": f"def {name}_two():\n    return 2\n"}, "edit")


def test_writers_leave_a_clone_alone_while_it_is_being_bound(two_repos, tmp_path):
    """The job, the health hook and the commit hook skip a graph whose clone a bind
    holds: the bind rebuilds it, and a concurrent write could undo that."""
    from core.ddd_bindings import _bind_lock
    from hooks.code_change_feed import _reindex_changed_files
    from hooks.context_health_hook import ContextHealthHook
    from jobs.handlers.code_intel_reindex import reindex_projects
    _project_dir, repos = two_repos
    _commit_both(repos)
    with _bind_lock(repos["svc"], "svc"):
        out = reindex_projects()
        ContextHealthHook()._refresh_code_intel(tmp_path)
        assert _reindex_changed_files([("M", "svc.py")], str(repos["svc"]), "Demo") == 0
    assert _ids(_graph("svc"), "svc.py") == ["svc.py::svc_one"]
    assert _ids(_graph("lib"), "lib.py") == ["lib.py::lib_two"]
    assert {"repo": "svc", "status": "bind_in_progress", "project": "Demo"} in out["projects"]


def test_one_graphs_failure_does_not_stop_the_job(two_repos, monkeypatch):
    import core.code_intel.freshness as fr
    import sqlite3
    from jobs.handlers.code_intel_reindex import reindex_projects
    _project_dir, repos = two_repos
    _commit_both(repos)
    real = fr.check_freshness

    def check(graph):
        if graph._db_path.name.startswith("lib"):
            raise sqlite3.OperationalError("database is locked")
        return real(graph)
    monkeypatch.setattr(fr, "check_freshness", check)
    out = reindex_projects()
    assert {"project": "Demo", "status": "error", "error": "OperationalError",
            "repo": "lib"} in out["projects"]
    assert _ids(_graph("svc"), "svc.py") == ["svc.py::svc_two"]


def test_the_api_shows_the_picked_repo_and_404s_an_unknown_one(two_repos):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import routers.code_intel as mod
    app = FastAPI()
    app.include_router(mod.router, prefix="/api/code-intel")
    client = TestClient(app)
    assert client.get("/api/code-intel/Demo/summary").json()["file_count"] == 1
    r = client.get("/api/code-intel/Demo/graph?repo=svc").json()
    assert [n["id"] for n in r["nodes"]] == ["svc.py::svc_one"]
    assert client.get("/api/code-intel/Demo/summary?repo=nope").status_code == 404


def test_the_api_reindex_rebuilds_every_graph(two_repos):
    import routers.code_intel as mod
    _project_dir, repos = two_repos
    for name in ("lib", "svc"):
        _commit(repos[name], {f"{name}.py": f"def {name}_two():\n    return 2\n"}, "edit")
    mod._run_reindex("Demo")
    assert _ids(_graph("lib"), "lib.py") == ["lib.py::lib_two"]
    assert _ids(_graph("svc"), "svc.py") == ["svc.py::svc_two"]


def test_the_analysis_cli_picks_a_repo_and_names_the_others(two_repos, capsys):
    import json
    from core.code_intel.analysis import main
    assert main(["Demo", "--layers", "structure", "--repo", "svc"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["repo"] == "svc" and out["structure"]["repo"]["name"] == "svc"
    assert main(["Demo", "--layers", "structure"]) == 0
    assert "svc" in capsys.readouterr().err
