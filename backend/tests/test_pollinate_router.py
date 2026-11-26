"""Tests for the /api/pollinate router: GET /works, GET /works/view, GET /eval.

The numbers themselves are tested in test_pollinate_works_core.py; here: the HTTP shapes,
the broken-file rules (never a 500), works outside the workspace, the 404 for an
unregistered path, every handler on the nav pool, and the pre-v2 run gallery gone.
"""

import json
import threading
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def workspace(tmp_path):
    return tmp_path.resolve()


@pytest.fixture
def client(workspace):
    with patch("routers.pollinate._get_swarmws", return_value=workspace):
        from main import app
        yield TestClient(app)


def _write_work(work_dir: Path, *, kind="deck", title="T", current="v001",
                versions=(("v001", "ok", "2026-11-20T10:00:00Z"),), files=(), open_=None) -> Path:
    """A work folder with a work.json and the given output files. `versions` is
    (id, status, at) in the order the writer appended them."""
    work_dir.mkdir(parents=True, exist_ok=True)
    meta = {"schema": 1, "kind": kind, "title": title, "style": None,
            "size": {"w": 1920, "h": 1080}, "src": "src/deck.html", "slides": ["s-7f3a"],
            "current": current, "open": open_,
            "versions": [{"id": vid, "at": vat, "by": "agent", "status": vst,
                          "hashes": {}, "outputs": {}} for vid, vst, vat in versions]}
    (work_dir / "work.json").write_text(json.dumps(meta), encoding="utf-8")
    for rel in files:
        f = work_dir / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(b"x")
    return work_dir


def _write_registry(ws: Path, entries) -> None:
    reg = ws / ".pollinate" / "works.json"
    reg.parent.mkdir(parents=True, exist_ok=True)
    reg.write_text(json.dumps({"schema": 1, "works": entries}), encoding="utf-8")


def test_works_lists_registered_works(client, workspace):
    deck = _write_work(workspace / "Projects" / "AIDLC" / "assets" / "ai-deck", title="AI deck", current="v002",
                       versions=(("v001", "ok", "2026-11-20T00:00:00Z"), ("v002", "ok", "2026-11-21T00:00:00Z"),
                                 ("v003", "failed", "2026-11-25T07:59:00Z")),
                       open_={"scope": ["s-7f3a"], "note": "bigger title", "base": "v002", "session": "S1"},
                       files=["out/deck.html", "out/slides/01-s-7f3a.png"])
    poster = _write_work(workspace / "Knowledge" / "Pollinate" / "launch", kind="poster", title="Launch",
                         files=["out/poster.png"])
    _write_registry(workspace, [
        {"path": str(poster), "updated": "2026-11-24T09:00:00Z"},
        {"path": str(deck), "updated": "2026-11-25T08:00:00Z"},
    ])
    r = client.get("/api/pollinate/works")
    assert r.status_code == 200
    body = r.json()
    assert body["skipped"] == 0 and [w["title"] for w in body["works"]] == ["AI deck", "Launch"]
    d, p = body["works"]
    assert (d["path"], d["brain"], d["slide_count"], d["width"], d["height"]) == (
        "Projects/AIDLC/assets/ai-deck", "AIDLC", 1, 1920, 1080)
    assert (d["current"], d["latest"], d["status"]) == ("v002", "v003", "failed")
    assert d["open"]["scope"] == ["s-7f3a"] and d["open"]["session"] == "S1" and d["open"]["note"] == "bigger title"
    assert [v["id"] for v in d["versions"]] == ["v001", "v002", "v003"]
    assert d["thumbnail"] == "Projects/AIDLC/assets/ai-deck/out/slides/01-s-7f3a.png"
    assert d["main_output"] == "Projects/AIDLC/assets/ai-deck/out/deck.html"
    assert p["brain"] == "General" and p["open"] is None and p["slide_count"] is None


def test_works_skips_and_counts_broken_entries(client, workspace):
    good = _write_work(workspace / "w" / "good", kind="poster", files=["out/poster.png"])
    no_json = workspace / "w" / "no-json"
    no_json.mkdir(parents=True)
    bad_json = workspace / "w" / "bad-json"
    bad_json.mkdir(parents=True)
    (bad_json / "work.json").write_text("{ not json", encoding="utf-8")
    _write_registry(workspace, [
        {"path": str(workspace / "w" / "missing")}, {"path": str(no_json)}, {"path": str(bad_json)},
        {"path": "w/good"}, "not-an-object", {"kind": "deck"},
        {"path": str(good)}, {"path": str(good)},
    ])
    body = client.get("/api/pollinate/works").json()
    assert [w["path"] for w in body["works"]] == ["w/good"]
    assert body["skipped"] == 6


def test_works_broken_registry_is_not_a_500(client, workspace):
    assert client.get("/api/pollinate/works").json() == {"works": [], "skipped": 0}
    reg = workspace / ".pollinate" / "works.json"
    reg.parent.mkdir(parents=True)
    reg.write_text("{ broken", encoding="utf-8")
    r = client.get("/api/pollinate/works")
    assert r.status_code == 200 and r.json() == {"works": [], "skipped": 1}


def test_works_registry_too_large_is_skipped(client, workspace):
    from core import pollinate_works as mod
    reg = workspace / ".pollinate" / "works.json"
    reg.parent.mkdir(parents=True)
    reg.write_text(json.dumps({"works": [], "pad": "x" * 64}), encoding="utf-8")
    with patch.object(mod, "MAX_JSON_BYTES", 32):
        assert client.get("/api/pollinate/works").json() == {"works": [], "skipped": 1}


def test_works_outside_workspace(client, workspace, tmp_path_factory):
    outside = _write_work(tmp_path_factory.mktemp("desktop") / "deck", files=["out/deck.html"])
    _write_registry(workspace, [{"path": str(outside)}])
    w = client.get("/api/pollinate/works").json()["works"][0]
    assert w["in_workspace"] is False and w["path"] == str(outside.resolve())
    assert w["main_output"] == str(outside.resolve() / "out" / "deck.html")


def test_work_view_and_its_404(client, workspace):
    deck = _write_work(workspace / "Projects" / "P" / "assets" / "d", files=["out/deck.html"])
    _write_registry(workspace, [{"path": str(deck)}])
    r = client.get("/api/pollinate/works/view", params={"path": "Projects/P/assets/d"})
    assert r.status_code == 200
    body = r.json()
    assert body["work"]["title"] == "T" and body["actions"][0] == {
        "key": "open_canvas", "primary": True, "path": "Projects/P/assets/d/out/deck.html", "session": None}
    for bad in ("Projects/P/assets/other", "Projects/P/assets/d/../d", "/etc/passwd"):
        assert client.get("/api/pollinate/works/view", params={"path": bad}).status_code == 404
    assert client.get("/api/pollinate/works/view").status_code == 400  # the app maps validation errors to 400


def test_eval_shape(client, workspace):
    r = client.get("/api/pollinate/eval")
    assert r.status_code == 200
    e = r.json()
    assert e["days"] == 15 and len(e["daily"]) == 30
    assert e["rate"] == {"cur": None, "prev": None}
    assert client.get("/api/pollinate/eval", params={"days": 0}).status_code == 400


def _thread_recorder(monkeypatch, name: str) -> list[str]:
    from core import pollinate_works as mod
    seen: list[str] = []
    real = getattr(mod, name)

    def wrapped(*a, **kw):
        seen.append(threading.current_thread().name)
        return real(*a, **kw)

    monkeypatch.setattr(mod, name, wrapped)
    return seen


@pytest.mark.parametrize("url,name", [
    ("/api/pollinate/works", "read_works"),
    ("/api/pollinate/works/view?path=x", "work_view"),
    ("/api/pollinate/eval", "eval_summary"),
])
def test_every_handler_runs_on_the_nav_pool(client, workspace, monkeypatch, url, name):
    seen = _thread_recorder(monkeypatch, name)
    client.get(url)
    assert seen and all(n.startswith("swarm-nav") for n in seen), seen


def test_assets_endpoint_removed(client, workspace):
    """The pre-v2 run gallery is gone with no backward compatibility."""
    assert client.get("/api/pollinate/assets").status_code == 404
    from routers.pollinate import router
    assert sorted(getattr(rt, "path", "") for rt in router.routes) == ["/eval", "/works", "/works/view"]
    import schemas.pollinate as s
    assert not any(hasattr(s, n) for n in ("PollinateAsset", "PollinateContentCard", "PollinateAssetsResponse"))
