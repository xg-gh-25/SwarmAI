"""/workspace/file/raw serves large media: the viewer's <video> streams it with Range
requests, so the preview size cap must not refuse a long film (a 9-minute film is
about 160 MB). Types a browser runs as a page keep the cap."""
from fastapi import FastAPI
from fastapi.testclient import TestClient


def _client(tmp_path, monkeypatch):
    from routers import workspace_api

    async def ws():
        return str(tmp_path)

    async def landed(p, _s):
        return p

    monkeypatch.setattr(workspace_api, "_get_workspace_path", ws)
    monkeypatch.setattr(workspace_api, "_landed_session_path", landed)
    monkeypatch.setattr(workspace_api, "MAX_PREVIEW_SIZE", 16)
    app = FastAPI()
    app.include_router(workspace_api.router, prefix="/api")
    return TestClient(app)


def test_large_video_streams_with_range(tmp_path, monkeypatch):
    (tmp_path / "film.mp4").write_bytes(bytes(range(64)))
    c = _client(tmp_path, monkeypatch)
    r = c.get("/api/workspace/file/raw", params={"path": "film.mp4"})
    assert r.status_code == 200 and len(r.content) == 64
    r = c.get("/api/workspace/file/raw", params={"path": "film.mp4"}, headers={"Range": "bytes=10-19"})
    assert r.status_code == 206 and r.content == bytes(range(10, 20))


def test_large_pdf_and_image_are_served(tmp_path, monkeypatch):
    (tmp_path / "big.pdf").write_bytes(b"%PDF-1.4" + b"\0" * 64)
    (tmp_path / "big.png").write_bytes(b"\x89PNG" + b"\0" * 64)
    c = _client(tmp_path, monkeypatch)
    for name in ("big.pdf", "big.png"):
        assert c.get("/api/workspace/file/raw", params={"path": name}).status_code == 200, name


def test_large_page_type_keeps_the_cap(tmp_path, monkeypatch):
    (tmp_path / "big.html").write_text("<p>" + "x" * 64 + "</p>")
    (tmp_path / "big.svg").write_text("<svg>" + "x" * 64 + "</svg>")
    c = _client(tmp_path, monkeypatch)
    for name in ("big.html", "big.svg"):
        assert c.get("/api/workspace/file/raw", params={"path": name}).status_code == 413, name
