"""GET /workspace/{agent_id}/file-meta returns absolute_path (run_a3292e2f).

The agent-scoped, base_path-aware metadata endpoint that powers FilePreviewModal's
unsupported-file card (Open in Default App / Reveal in Finder / Copy Path). Unlike
the global /workspace/file/meta, this one honors the "work in a folder" base_path,
so the absolute path is resolved against the RIGHT root. Content is never read.
"""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def folder(client):
    """A "work in a folder" root. base_path must sit inside the workspace (403
    otherwise, routers/workspace.get_workspace_root, 804abf96), so use a
    subdirectory of the test's workspace rather than an arbitrary folder."""
    from core.initialization_manager import initialization_manager

    d = Path(initialization_manager.get_cached_workspace_path()) / "agent-meta-folder"
    d.mkdir(parents=True, exist_ok=True)
    return d


def test_agent_meta_returns_absolute_path(client: TestClient, folder):
    f = folder / "slides.pptx"
    f.write_bytes(b"PK\x03\x04fake")
    resp = client.get(
        "/api/workspace/agent-x/file-meta",
        params={"path": "slides.pptx", "base_path": str(folder)},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "slides.pptx"
    assert data["size"] == len(b"PK\x03\x04fake")
    assert data["absolute_path"] == str(f.resolve())
    assert data["absolute_path"].startswith("/")


def test_agent_meta_404_for_missing_file(client: TestClient, folder):
    resp = client.get(
        "/api/workspace/agent-x/file-meta",
        params={"path": "nope.docx", "base_path": str(folder)},
    )
    assert resp.status_code == 404


def test_agent_meta_rejects_directory(client: TestClient, folder):
    (folder / "adir").mkdir()
    resp = client.get(
        "/api/workspace/agent-x/file-meta",
        params={"path": "adir", "base_path": str(folder)},
    )
    assert resp.status_code == 400
