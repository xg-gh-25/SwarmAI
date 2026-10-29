"""The code intelligence API (routers/code_intel.py).

``/summary`` is the status line Brain Hub reads on every open: counts from the graph
and fresh/stale from ONE ``git rev-parse`` against the graph's commit marker. It never
refreshes the graph and never runs the dead-code scan (design
2026-10-29-code-intel-unification §0.2). ``/reindex`` starts a child process and
returns. Driven with a real git repo and a real GraphStore.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.code_intel import CodeIntelDb
from core.code_intel.graph_store import GraphStore


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(cwd), *args], check=True,
                          capture_output=True, text=True).stdout.strip()


@pytest.fixture
def client():
    from routers.code_intel import router
    app = FastAPI()
    app.include_router(router, prefix="/api/code-intel")
    return TestClient(app)


@pytest.fixture
def graph(tmp_path):
    """A graph built from a one-commit repo, its marker at HEAD."""
    from core.code_intel.build import rebuild
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    (repo / "m.py").write_text("def f():\n    return g()\n\ndef g():\n    return 1\n")
    (repo / "u.ts").write_text("export function h() { return 1 }\n")
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.email=t@e", "-c", "user.name=t", "commit", "-q", "-m", "init")
    g = GraphStore(tmp_path / "g.db")
    rebuild(g, repo, head=_git(repo, "rev-parse", "HEAD"))
    yield repo, g
    g.close()


def _serve(g):
    return patch("routers.code_intel.project_graph",
                 return_value=(CodeIntelDb(db_path=Path("g.db")), g))


def test_the_summary_reports_what_the_graph_holds(client, graph):
    repo, g = graph
    with _serve(g):
        data = client.get("/api/code-intel/P/summary").json()
    assert data["symbol_count"] == g.counts()["nodes"] > 0
    assert data["file_count"] == 2
    assert set(data["languages"]) == {"python", "typescript"}
    assert data["indexed_commit"] == _git(repo, "rev-parse", "HEAD")
    assert data["last_indexed_at"]
    assert data["freshness_status"] == "fresh"


def test_a_commit_after_the_build_reads_stale(client, graph):
    repo, g = graph
    (repo / "m.py").write_text("def f():\n    return 2\n")
    _git(repo, "-c", "user.email=t@e", "-c", "user.name=t", "commit", "-qam", "edit")
    with _serve(g):
        assert client.get("/api/code-intel/P/summary").json()["freshness_status"] == "stale"
    assert g.get_meta("last_indexed_commit") != _git(repo, "rev-parse", "HEAD"), \
        "a status read refreshed the graph"


def test_the_summary_stamp_is_one_git_call_and_no_dead_code_scan(client, graph):
    import core.code_intel.freshness as fr
    _repo, g = graph
    calls = []
    real = fr._git

    def spy(root, args, *a, **k):
        calls.append(args)
        return real(root, args, *a, **k)
    with _serve(g), patch.object(fr, "_git", spy), \
         patch.object(GraphStore, "get_codebase_summary",
                      side_effect=AssertionError("the dead-code scan ran")):
        assert client.get("/api/code-intel/P/summary").status_code == 200
    assert calls == [["rev-parse", "HEAD"]]


def test_a_graph_without_a_repo_reads_unknown(client, tmp_path):
    g = GraphStore(tmp_path / "bare.db")
    try:
        with _serve(g):
            assert client.get("/api/code-intel/P/summary").json()["freshness_status"] == "unknown"
    finally:
        g.close()


def test_the_summary_404s_without_a_graph_and_400s_a_bad_name(client):
    with patch("routers.code_intel.project_graph", return_value=None):
        resp = client.get("/api/code-intel/NonExistent/summary")
    assert resp.status_code == 404 and "not found" in resp.json()["detail"].lower()
    assert client.get("/api/code-intel/a..b/summary").status_code == 400


class _Child:
    def __init__(self):
        self.alive = True

    def poll(self):
        return None if self.alive else 0


def test_reindex_starts_one_child_at_a_time(client, monkeypatch):
    import routers.code_intel as mod
    child = _Child()
    started = []
    monkeypatch.setattr(mod, "owned_graphs", lambda d: [object()])
    monkeypatch.setattr(mod, "_reindex_children", {})
    monkeypatch.setattr("core.code_intel.build.spawn_build",
                        lambda project, **kw: started.append((project, kw)) or child)
    first = client.post("/api/code-intel/P/reindex")
    assert first.status_code == 202 and first.json()["status"] == "indexing"
    assert client.post("/api/code-intel/P/reindex").json()["status"] == "already_indexing"
    child.alive = False
    assert client.post("/api/code-intel/P/reindex").json()["status"] == "indexing"
    assert started == [("P", {"full": True})] * 2


def test_reindex_404s_a_project_that_owns_no_repo(client, monkeypatch):
    import routers.code_intel as mod
    monkeypatch.setattr(mod, "owned_graphs", lambda d: [])
    assert client.post("/api/code-intel/P/reindex").status_code == 404


def test_reindex_that_cannot_start_is_a_500_without_detail(client, monkeypatch):
    import routers.code_intel as mod
    monkeypatch.setattr(mod, "owned_graphs", lambda d: [object()])
    monkeypatch.setattr(mod, "_reindex_children", {})

    def boom(project, **kw):
        raise OSError("/secret/path: no such file")
    monkeypatch.setattr("core.code_intel.build.spawn_build", boom)
    resp = client.post("/api/code-intel/P/reindex")
    assert resp.status_code == 500 and "/secret" not in resp.text
