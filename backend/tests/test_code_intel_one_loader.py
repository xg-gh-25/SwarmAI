"""One way in: every reader and writer of a project's code graph reaches every graph.

A project that binds two repos has two graphs. Before ``load_project_graphs`` was the
only loader, every reader and writer read the first graph only, so the second repo
was never re-indexed (design docs/design/2026-10-29-code-intel-unification.md §3.3,
B2). Driven with real git repos and real GraphStores.
"""
from __future__ import annotations

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


def test_the_job_leaves_a_clone_alone_while_it_is_being_bound(two_repos):
    """A graph whose clone a bind holds is not written: the bind rebuilds it, and a
    concurrent write could undo that."""
    from core.ddd_bindings import _bind_lock
    from jobs.handlers.code_intel_reindex import reindex_projects
    _project_dir, repos = two_repos
    _commit_both(repos)
    with _bind_lock(repos["svc"], "svc"):
        out = reindex_projects()
    assert _ids(_graph("svc"), "svc.py") == ["svc.py::svc_one"]
    assert _ids(_graph("lib"), "lib.py") == ["lib.py::lib_two"]
    (svc_row,) = [r for r in out["projects"] if r["repo"] == "svc"]
    assert svc_row["status"] == "busy"


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


def test_the_api_reindex_starts_a_child_and_parses_nothing_itself(two_repos, monkeypatch):
    """The daemon never parses: the re-index endpoint starts the build child and
    returns; a second request while that child lives is ``already_indexing``."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import core.code_intel.build as build
    import routers.code_intel as mod

    class Child:
        alive = True
        def poll(self):
            return None if self.alive else 0
    started = []
    child = Child()
    monkeypatch.setattr(build, "spawn_build",
                        lambda project, **kw: started.append((project, kw)) or child)
    monkeypatch.setattr(build, "rebuild", lambda *a, **k: pytest.fail("parsed in-process"))
    monkeypatch.setattr(mod, "_reindex_children", {})
    app = FastAPI()
    app.include_router(mod.router, prefix="/api/code-intel")
    client = TestClient(app)
    assert client.post("/api/code-intel/Demo/reindex").json()["status"] == "indexing"
    assert client.post("/api/code-intel/Demo/reindex").json()["status"] == "already_indexing"
    child.alive = False
    assert client.post("/api/code-intel/Demo/reindex").json()["status"] == "indexing"
    assert started == [("Demo", {"full": True})] * 2
    assert client.post("/api/code-intel/Nope/reindex").status_code == 404


def test_the_build_child_rebuilds_every_graph(two_repos):
    """What the re-index child runs (``python -m core.code_intel.build --full``)."""
    from core.code_intel.build import main
    _project_dir, repos = two_repos
    _commit_both(repos)
    assert main(["--project", "Demo", "--full"]) == 0
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
