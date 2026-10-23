"""The Workspace overlay's tree endpoints actually run (regression: nav spec S4
switched them to executors.run_nav without importing executors, so every GET
/api/workspace/tree returned 500 'name executors is not defined').

What is tested: GET /api/workspace/tree and /api/workspace/tree/expand over a temp
workspace answer 200 with the files on disk, through the real router (no
patching of the offload call).
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


async def _ws(path: Path) -> str:
    return str(path)


@pytest.fixture
def client(tmp_path, monkeypatch):
    import routers.workspace_api as wa
    (tmp_path / "Knowledge" / "Notes").mkdir(parents=True)
    (tmp_path / "Knowledge" / "Notes" / "a.md").write_text("# a\n")
    (tmp_path / "README.md").write_text("# ws\n")
    monkeypatch.setattr(wa, "_get_workspace_path", lambda: _ws(tmp_path))
    app = FastAPI()
    app.include_router(wa.router, prefix="/api")
    return TestClient(app)


def _names(node) -> set[str]:
    out = set()
    stack = node if isinstance(node, list) else [node]
    while stack:
        n = stack.pop()
        if isinstance(n, dict):
            if "name" in n:
                out.add(n["name"])
            stack.extend(n.get("children") or [])
        elif isinstance(n, list):
            stack.extend(n)
    return out


def test_tree_loads(client):
    r = client.get("/api/workspace/tree?depth=3")
    assert r.status_code == 200, r.text
    body = r.json()
    names = _names(body.get("tree", body) if isinstance(body, dict) else body)
    assert "Knowledge" in names


def test_expand_loads(client):
    r = client.get("/api/workspace/tree/expand?path=Knowledge")
    assert r.status_code == 200, r.text
    assert "Notes" in _names(r.json())
