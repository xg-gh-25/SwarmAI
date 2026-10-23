"""Workspace locations (design workspace-working-location.md v5, T1-T5).

What is tested, through the REAL router (only the data sources are pointed at temp
dirs: ``_get_workspace_path`` and the DDD bindings ``repo_changes._bindings``):
- T2 /workspace/locations lists SwarmWS first, then declared repos that are a main
  checkout with HEAD activity inside 30 days, newest first, and runs no git;
- T1 the tree answers for swarmws and for a repo; a repo tree skips node_modules/.git,
  flags dot entries hidden, does not follow a symlink out of the repo;
- T4 /workspace/summary counts one location's uncommitted entries, directories carry
  ``changed_count``, a repeat request reuses the scan, and a location's request never
  runs git in another location;
- T5 unknown key 404, traversal / symlink escape refused, writes with ``location`` 422,
  a repo file reads back read-only.
"""
from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


def _git(cwd, *args):
    subprocess.run(["git", "-C", str(cwd), "-c", "user.email=a@b.c", "-c", "user.name=T", *args],
                   check=True, capture_output=True, text=True)


def _repo(path: Path, files: dict[str, str]) -> Path:
    path.mkdir(parents=True)
    _git(path, "init", "-q", "-b", "main")
    for rel, text in files.items():
        (path / rel).parent.mkdir(parents=True, exist_ok=True)
        (path / rel).write_text(text)
    _git(path, "add", ".")
    _git(path, "commit", "-qm", "base")
    return path


async def _ws(path: Path) -> str:
    return str(path)


@pytest.fixture
def env(tmp_path, monkeypatch):
    import routers.workspace_api as wa
    from core import repo_changes, workspace_locations as wl
    ws = _repo(tmp_path / "SwarmWS", {"Knowledge/Notes/a.md": "# a\n"})
    active = _repo(tmp_path / "active", {"pkg/x.py": "x = 1\n", "README.md": "r\n",
                                         ".kiro/s.json": "{}\n", "node_modules/m/i.js": "1\n"})
    older = _repo(tmp_path / "older", {"a.txt": "a\n"})
    stale = _repo(tmp_path / "stale", {"a.txt": "a\n"})
    old = time.time() - 31 * 86400
    os.utime(stale / ".git" / "logs" / "HEAD", (old, old))
    mid = time.time() - 5 * 86400
    os.utime(older / ".git" / "logs" / "HEAD", (mid, mid))
    linked = tmp_path / "linked"
    _git(active, "worktree", "add", "-q", "-b", "side", str(linked))
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("s\n")
    os.symlink(outside, active / "escape")
    (active / "pkg" / "x.py").write_text("x = 2\n")          # modified
    (active / "new.txt").write_text("n\n")                    # untracked
    binds = {str(p.resolve()): (p.name, "P") for p in (older, active, stale, linked)}
    binds[str((tmp_path / "missing").resolve())] = ("missing", "P")
    monkeypatch.setattr(repo_changes, "_bindings", lambda: binds)
    monkeypatch.setattr(wa, "_get_workspace_path", lambda: _ws(ws))
    wl.invalidate()
    wa._invalidate_tree_cache()
    app = FastAPI()
    app.include_router(wa.router, prefix="/api")
    calls: list[str] = []
    real_run = wl.subprocess.run

    def spy(cmd, *a, **kw):
        calls.append(str(kw.get("cwd")))
        return real_run(cmd, *a, **kw)
    monkeypatch.setattr(wl.subprocess, "run", spy)
    return {"c": TestClient(app), "ws": ws, "active": active, "older": older, "calls": calls,
            "key": wl.repo_key(str(active)), "older_key": wl.repo_key(str(older))}


def _by_name(nodes):
    return {n["name"]: n for n in nodes}


def test_locations_lists_swarmws_then_active_main_checkouts(env):
    r = env["c"].get("/api/workspace/locations")
    assert r.status_code == 200, r.text
    rows = r.json()
    assert [x["label"] for x in rows] == ["SwarmWS", "active", "older"]
    assert rows[0] == {"key": "swarmws", "label": "SwarmWS", "kind": "default",
                       "path": str(env["ws"].resolve())}
    assert rows[1] == {"key": env["key"], "label": "active", "kind": "repo",
                       "path": str(env["active"].resolve())}
    assert env["calls"] == []                      # stat only, no git


def test_tree_swarmws_and_repo(env):
    c = env["c"]
    r = c.get("/api/workspace/tree?depth=2&location=swarmws")
    assert r.status_code == 200, r.text
    assert "Knowledge" in _by_name(r.json())
    r = c.get(f"/api/workspace/tree?depth=1&location={env['key']}")
    assert r.status_code == 200, r.text
    top = _by_name(r.json())
    assert "node_modules" not in top and ".git" not in top
    assert {"pkg", "README.md", "new.txt", ".kiro", "escape"} <= set(top)
    assert top[".kiro"].get("hidden") is True and not top["pkg"].get("hidden")
    assert top["pkg"]["changed_count"] == 1 and top["pkg"]["children"] is None
    assert top["new.txt"]["git_status"] == "untracked"
    deep = _by_name(c.get(f"/api/workspace/tree?depth=2&location={env['key']}").json())
    assert deep["escape"]["children"] is None and deep["escape"]["is_symlink"] is True
    assert "x.py" in _by_name(deep["pkg"]["children"])


def test_expand_repo_dir(env):
    r = env["c"].get(f"/api/workspace/tree/expand?path=pkg&depth=1&location={env['key']}")
    assert r.status_code == 200, r.text
    assert _by_name(r.json())["x.py"]["git_status"] == "modified"


def test_summary_is_per_location_and_cached(env):
    c, calls = env["c"], env["calls"]
    r = c.get(f"/api/workspace/summary?location={env['key']}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["changed"] == 3                  # pkg/x.py, new.txt, escape (untracked)
    assert body["last_commit_at"] > time.time() - 3600
    assert calls and all(Path(x).resolve() == env["active"].resolve() for x in calls)
    n = len(calls)
    c.get(f"/api/workspace/summary?location={env['key']}")
    c.get(f"/api/workspace/tree?depth=1&location={env['key']}")
    assert len(calls) == n                        # one scan shared
    r = c.get("/api/workspace/summary?location=swarmws")
    assert r.json() == {"changed": 0}
    assert all(Path(x).resolve() != env["older"].resolve() for x in calls)
    n = len(calls)
    c.get(f"/api/workspace/tree?depth=1&location={env['key']}&fresh=true")
    assert len(calls) == n + 1                    # refresh rescans


def test_unknown_key_and_escapes_refused(env):
    c, key = env["c"], env["key"]
    assert c.get("/api/workspace/tree?location=r_000000000000").status_code == 404
    assert c.get("/api/workspace/summary?location=nope").status_code == 404
    assert c.get(f"/api/workspace/tree/expand?path=../SwarmWS&location={key}").status_code in (400, 403)
    assert c.get(f"/api/workspace/tree/expand?path=escape&location={key}").status_code in (400, 403)
    assert c.get(f"/api/workspace/file?path=../outside/secret.txt&location={key}").status_code == 400
    assert c.get(f"/api/workspace/file?path=escape/secret.txt&location={key}").status_code == 400
    assert c.get(f"/api/workspace/file?path={env['active'] / 'README.md'}&location={key}").status_code == 400


def test_repo_file_reads_back_read_only(env):
    r = env["c"].get(f"/api/workspace/file?path=pkg/x.py&location={env['key']}")
    assert r.status_code == 200, r.text
    assert r.json()["content"] == "x = 2\n" and r.json()["readonly"] is True


def test_writes_refuse_a_location(env):
    c, key = env["c"], env["key"]
    assert c.post("/api/workspace/folders", json={"path": "n", "location": key}).status_code == 422
    assert c.put("/api/workspace/rename", json={"old_path": "a", "new_path": "b",
                                                "location": key}).status_code == 422
    assert c.put(f"/api/workspace/file?path=pkg/x.py&location={key}",
                 json={"content": "boom"}).status_code == 422
    assert (env["active"] / "pkg" / "x.py").read_text() == "x = 2\n"
    assert not (env["ws"] / "n").exists()


def test_locations_are_bound_to_no_run_and_no_session():
    """T3: Workspace reflects local main only; its code names no run or session."""
    import ast
    import inspect
    import routers.workspace_api as wa
    from core import workspace_locations as wl
    srcs = {"workspace_locations": inspect.getsource(wl)}
    for fn in ("list_workspace_locations", "get_workspace_summary", "_location_root"):
        srcs[fn] = inspect.getsource(getattr(wa, fn))
    banned = {"run_index", "canvas_session", "session_id", "run_worktree", "session_registry"}
    for name, src in srcs.items():
        idents = set()
        for n in ast.walk(ast.parse(src)):
            idents |= {getattr(n, a) for a in ("id", "attr", "arg", "name", "module")
                       if isinstance(getattr(n, a, None), str)}
        assert not (idents & banned), (name, idents & banned)


def test_last_commit_is_the_commit_time_not_the_head_log_mtime(tmp_path):
    """logs/HEAD moves on checkout too; the header says "last commit", so it reads HEAD's
    committer time (cached by that mtime)."""
    from core import workspace_locations as wl
    repo = tmp_path / "r"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    (repo / "a").write_text("a\n")
    _git(repo, "add", ".")
    subprocess.run(["git", "-C", str(repo), "-c", "user.email=a@b.c", "-c", "user.name=T", "commit", "-qm", "x"],
                   check=True, capture_output=True,
                   env={**os.environ, "GIT_COMMITTER_DATE": "2020-01-01T00:00:00Z",
                        "GIT_AUTHOR_DATE": "2020-01-01T00:00:00Z"})
    _git(repo, "checkout", "-q", "-b", "side")         # moves logs/HEAD to now
    assert wl.summary(repo, "repo")["last_commit_at"] == 1577836800.0
