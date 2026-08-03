"""GET /workspace/file/meta returns absolute_path (run_405d221c).

The FileViewer's UnsupportedRenderer needs the PHYSICAL absolute path to
"Open in System App" / "Reveal in Finder" / "Copy Path" — a workspace-relative
path is meaningless to the OS opener. `/workspace/file/meta` is the endpoint that
already runs for every unsupported file (metadata-only fetch) and already resolves
the path server-side (_resolve_file_path → an absolute Path). This locks that it
now ALSO returns that absolute path, so the frontend never has to re-derive it.
"""
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient


def _mock_ws(tmp_path):
    return patch(
        "routers.workspace_api._get_workspace_path",
        new_callable=AsyncMock,
        return_value=str(tmp_path),
    )


def test_meta_returns_absolute_path(client: TestClient, tmp_path):
    # A real unsupported-type file on disk.
    f = tmp_path / "deck.pptx"
    f.write_bytes(b"PK\x03\x04fake-pptx")
    with _mock_ws(tmp_path):
        resp = client.get("/api/workspace/file/meta", params={"path": "deck.pptx"})
    assert resp.status_code == 200
    data = resp.json()
    # Existing contract unchanged.
    assert data["name"] == "deck.pptx"
    assert data["size"] == len(b"PK\x03\x04fake-pptx")
    # NEW: absolute_path is the physical on-disk path (what the OS opener needs).
    assert "absolute_path" in data
    assert data["absolute_path"] == str(f.resolve())
    # It is absolute, not the workspace-relative input.
    assert data["absolute_path"].startswith("/")
    assert data["absolute_path"] != "deck.pptx"


def test_meta_absolute_path_for_nested_file(client: TestClient, tmp_path):
    sub = tmp_path / "Knowledge" / "Designs"
    sub.mkdir(parents=True)
    f = sub / "report.docx"
    f.write_bytes(b"fake-docx")
    with _mock_ws(tmp_path):
        resp = client.get(
            "/api/workspace/file/meta",
            params={"path": "Knowledge/Designs/report.docx"},
        )
    assert resp.status_code == 200
    assert resp.json()["absolute_path"] == str(f.resolve())
