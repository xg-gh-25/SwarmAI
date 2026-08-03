"""GET /workspace/{agent_id}/file-meta returns absolute_path (run_a3292e2f).

The agent-scoped, base_path-aware metadata endpoint that powers FilePreviewModal's
unsupported-file card (Open in Default App / Reveal in Finder / Copy Path). Unlike
the global /workspace/file/meta, this one honors the "work in a folder" base_path,
so the absolute path is resolved against the RIGHT root. Content is never read.
"""
from fastapi.testclient import TestClient


def test_agent_meta_returns_absolute_path(client: TestClient, tmp_path):
    # base_path bypasses the cached-workspace resolution → resolves under tmp_path.
    f = tmp_path / "slides.pptx"
    f.write_bytes(b"PK\x03\x04fake")
    resp = client.get(
        "/api/workspace/agent-x/file-meta",
        params={"path": "slides.pptx", "base_path": str(tmp_path)},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "slides.pptx"
    assert data["size"] == len(b"PK\x03\x04fake")
    assert data["absolute_path"] == str(f.resolve())
    assert data["absolute_path"].startswith("/")


def test_agent_meta_404_for_missing_file(client: TestClient, tmp_path):
    resp = client.get(
        "/api/workspace/agent-x/file-meta",
        params={"path": "nope.docx", "base_path": str(tmp_path)},
    )
    assert resp.status_code == 404


def test_agent_meta_rejects_directory(client: TestClient, tmp_path):
    (tmp_path / "adir").mkdir()
    resp = client.get(
        "/api/workspace/agent-x/file-meta",
        params={"path": "adir", "base_path": str(tmp_path)},
    )
    assert resp.status_code == 400
