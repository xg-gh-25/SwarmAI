"""The workspace file API never shows or overwrites a binary file as text
(docs/design/canvas-file-types.md, P0).

What is tested:
- ``looks_like_text``: UTF-8 text (incl. a multi-byte char cut at the sniff edge) is
  text; a NUL byte or invalid UTF-8 is not.
- ``PUT /api/workspace/file`` and ``PUT /api/workspace/{agent}/write`` refuse (409)
  to overwrite a file on disk that is not text — the file's bytes stay exactly as
  they were; text files and new files still write.
- ``GET /api/workspace/file?text_only=1`` on a binary returns 415 ``binary`` (no
  base64 body); without the flag the old base64 fallback stays, now marked readonly;
  a text file is unaffected by the flag.

Methodology: FastAPI TestClient against a tmp workspace (the workspace path lookups
of both routers monkeypatched); no user data is touched.
"""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from utils.text_sniff import SNIFF_BYTES, looks_like_text

BINARY = bytes(range(256)) * 8  # NULs and invalid UTF-8


@pytest.fixture
def tmp_ws(tmp_path, monkeypatch):
    workspace = tmp_path / "SwarmWS"
    workspace.mkdir()
    monkeypatch.setattr(
        "core.initialization_manager.initialization_manager.get_cached_workspace_path",
        lambda: str(workspace),
    )

    async def _fake_ws_path():
        return str(workspace)
    monkeypatch.setattr("routers.workspace_api._get_workspace_path", _fake_ws_path)
    return workspace


def test_looks_like_text():
    assert looks_like_text("héllo — 你好\n".encode())
    assert not looks_like_text(b"abc\x00def")
    assert not looks_like_text(b"\xff\xfe\xfa bad")
    # a 3-byte char cut by the sniff window is still text
    cut = ("a" * (SNIFF_BYTES - 1) + "你").encode()[:SNIFF_BYTES]
    assert looks_like_text(cut)


def test_put_refuses_to_overwrite_a_binary(client: TestClient, tmp_ws: Path):
    f = tmp_ws / "model.safetensors"
    f.write_bytes(BINARY)
    resp = client.put("/api/workspace/file", params={"path": "model.safetensors"},
                      json={"content": "AAECAwQF"})
    assert resp.status_code == 409
    assert f.read_bytes() == BINARY


def test_agent_write_refuses_to_overwrite_a_binary(client: TestClient, tmp_ws: Path):
    f = tmp_ws / "data.parquet"
    f.write_bytes(BINARY)
    resp = client.put("/api/workspace/agent1/write", json={"path": "data.parquet", "content": "x"})
    assert resp.status_code == 409
    assert f.read_bytes() == BINARY


def test_put_still_writes_text_and_new_files(client: TestClient, tmp_ws: Path):
    (tmp_ws / "a.md").write_text("old\n", encoding="utf-8")
    assert client.put("/api/workspace/file", params={"path": "a.md"}, json={"content": "new\n"}).status_code == 200
    assert (tmp_ws / "a.md").read_text(encoding="utf-8") == "new\n"
    assert client.put("/api/workspace/file", params={"path": "b.md"}, json={"content": "fresh\n"}).status_code == 200
    assert (tmp_ws / "b.md").read_text(encoding="utf-8") == "fresh\n"


def test_get_text_only_on_a_binary_is_415(client: TestClient, tmp_ws: Path):
    (tmp_ws / "x.npy").write_bytes(BINARY)
    resp = client.get("/api/workspace/file", params={"path": "x.npy", "text_only": "1"})
    assert resp.status_code == 415
    assert "content" not in resp.text


def test_get_without_flag_keeps_base64_but_readonly(client: TestClient, tmp_ws: Path):
    (tmp_ws / "x.npy").write_bytes(BINARY)
    data = client.get("/api/workspace/file", params={"path": "x.npy"}).json()
    assert data["encoding"] == "base64"
    assert data["readonly"] is True


def test_get_text_only_on_text_is_unchanged(client: TestClient, tmp_ws: Path):
    (tmp_ws / "a.py").write_text("print(1)\n", encoding="utf-8")
    data = client.get("/api/workspace/file", params={"path": "a.py", "text_only": "1"}).json()
    assert data["content"] == "print(1)\n"
    assert data["encoding"] == "utf-8"
