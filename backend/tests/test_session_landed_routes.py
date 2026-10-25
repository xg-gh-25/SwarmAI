"""The read routes open a removed session worktree's file from the main checkout
(design docs/design/2026-10-25-landings-consumers.md D1).

What is tested (the real workspace_api router on a bare FastAPI app; a real landing):
- GET /workspace/file (with session_id), /file/meta, /file/raw (a binary file) and
  /file/committed serve the mapped file once its worktree is removed;
- the mapping grants no access: a removed path that was allowlisted for the session
  but maps outside $HOME is refused (the allowlist is checked on the mapped path);
- the mapping runs off the event loop (asyncio.to_thread).
"""
from __future__ import annotations

import os
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.test_session_landed_path import SID, _iso, _land_and_remove, _repo  # noqa: F401

from core import session_landings as sl


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    repo = _repo(tmp_path / "proj")
    monkeypatch.setattr(sl, "_bound_checkouts", lambda: [repo])
    wt = _land_and_remove(monkeypatch, repo, "fix1", fname="b.png")
    from routers import workspace_api
    app = FastAPI()
    app.include_router(workspace_api.router, prefix="/api")
    ws = tmp_path / "appdata" / "SwarmWS"
    with patch("routers.workspace_api._get_workspace_path", new_callable=AsyncMock,
               return_value=str(ws)):
        yield TestClient(app), repo, os.path.join(wt, "b.png")


def test_each_read_route_serves_the_mapped_file(setup):
    client, repo, gone = setup
    r = client.get("/api/workspace/file", params={"path": gone, "session_id": SID})
    assert r.status_code == 200, r.text
    r = client.get("/api/workspace/file/meta", params={"path": gone})
    assert r.status_code == 200 and r.json()["absolute_path"] == os.path.join(repo, "b.png")
    r = client.get("/api/workspace/file/raw", params={"path": gone})
    assert r.status_code == 200 and r.content == b"b\n"
    r = client.get("/api/workspace/file/committed", params={"path": gone})
    assert r.status_code == 200 and r.json()["in_head"] is True


def test_another_sessions_id_is_not_mapped(setup):
    client, _repo_, gone = setup
    other = "99999999-2222-3333-4444-555555555555"
    r = client.get("/api/workspace/file", params={"path": gone, "session_id": other})
    assert r.status_code == 404


def test_the_mapping_grants_no_access(setup, tmp_path, monkeypatch):
    """Allowlisted for the session as the removed path, its checkout outside $HOME:
    not served (landed_path maps no outside-home checkout; the allowlist entry for
    the removed path does not carry over)."""
    client, _repo_, gone = setup
    monkeypatch.setenv("HOME", str(tmp_path / "elsewhere"))
    from routers import workspace_api
    with patch.object(workspace_api, "_allowed_external_for", new_callable=AsyncMock,
                      return_value={os.path.realpath(gone)}):
        r = client.get("/api/workspace/file", params={"path": gone, "session_id": SID})
    assert r.status_code == 404


def test_the_mapping_runs_off_the_event_loop(setup, monkeypatch):
    client, _repo_, gone = setup
    import asyncio
    seen = []
    real = asyncio.to_thread

    async def spy(fn, *a, **k):
        seen.append(getattr(fn, "__name__", ""))
        return await real(fn, *a, **k)
    monkeypatch.setattr(asyncio, "to_thread", spy)
    assert client.get("/api/workspace/file/raw", params={"path": gone}).status_code == 200
    assert "landed_path" in seen


def test_preview_maps_too(setup):
    """b.png has no Office preview: a 415 (not 404) shows the mapped file was found."""
    client, _repo_, gone = setup
    r = client.get("/api/workspace/file/preview", params={"path": gone, "session_id": SID})
    assert r.status_code == 415


@pytest.mark.parametrize("route", ["/api/workspace/file/meta", "/api/workspace/file/raw",
                                   "/api/workspace/file/committed"])
def test_a_released_pipeline_worktree_maps_on_every_read_route(setup, tmp_path, monkeypatch, route):
    client, repo, _gone = setup
    from core import run_worktree
    gone = str(tmp_path / "worktrees" / "run_x" / "proj" / "b.png")
    monkeypatch.setattr(run_worktree, "landed_path",
                        lambda p, ws: __import__("pathlib").Path(repo, "b.png") if str(p) == gone else None)
    assert client.get(route, params={"path": gone}).status_code == 200
