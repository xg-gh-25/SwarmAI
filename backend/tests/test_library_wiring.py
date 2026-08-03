"""Wiring closeout tests — the built engines are now reachable from user entries.

Covers the E2E flow-review gaps: POST /api/library/mounts (register + index by
kind), GET /api/library/search (recall path), and the click→index→recall loop.
The freshness JOB + the s_library SKILL are verified separately (job registry test
below; the skill is a thin CLI over the same core fns already tested).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    """FastAPI client with the library router + tmp DB/workspace wired in."""
    db_path = tmp_path / "data.db"
    kdir = tmp_path / "Knowledge"; kdir.mkdir()
    mounts_root = tmp_path / "Knowledge" / "Library" / "mounts"

    # Point every workspace dependency at the tmp tree.
    monkeypatch.setattr("jobs.paths.DB_PATH", db_path, raising=False)
    monkeypatch.setattr("core.library_mounts._mounts_dir", lambda: mounts_root)
    # _knowledge_dir() reads initialization_manager — point it at tmp.
    import routers.library_api as lib
    monkeypatch.setattr(lib, "_knowledge_dir", lambda: kdir)
    # DB_PATH is imported inside functions via `from jobs.paths import DB_PATH`,
    # so patch the source attribute (already done above); ensure the module the
    # endpoint imports sees it.
    import jobs.paths as jp
    monkeypatch.setattr(jp, "DB_PATH", db_path, raising=False)

    from routers.library_api import router
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def _make_code_dir(root: Path) -> Path:
    d = root / "ext-repo"; d.mkdir()
    (d / "widget.py").write_text(
        "def compute_widget_score(x):\n    return x * 2\n\nclass WidgetEngine:\n    pass\n"
    )
    return d


def test_post_mount_code_registers_and_indexes(client, tmp_path):
    """AC1+AC2: +Add Folder → POST /mounts judges code, registers, indexes,
    returns a real id+status (NOT a 404)."""
    ext = _make_code_dir(tmp_path)
    resp = client.post(f"/api/library/mounts?path={ext}&scope=SwarmAI")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["kind"] == "code"
    assert body["status"] == "indexed"
    assert body["symbols"] > 0
    assert body["id"]


def test_post_mount_docs_returns_chat_handoff(client, tmp_path):
    """AC1: a docs dir registers + hands the semantic step to chat (no code index)."""
    d = tmp_path / "docs"; d.mkdir(); (d / "note.md").write_text("# hi\n")
    resp = client.post(f"/api/library/mounts?path={d}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["kind"] == "docs"
    assert body["status"] == "registered"
    assert "brief" in body.get("next", "").lower()


def test_post_mount_rejects_a_file(client, tmp_path):
    """NEGATIVE: a single file is not a mount (goes to the Inbox) → 400."""
    f = tmp_path / "solo.md"; f.write_text("x")
    resp = client.post(f"/api/library/mounts?path={f}")
    assert resp.status_code == 400


def test_mounted_code_surfaces_in_search(client, tmp_path):
    """AC2 full loop: mount a code dir via the endpoint, then GET /search finds its
    symbol through the real recall path."""
    ext = _make_code_dir(tmp_path)
    client.post(f"/api/library/mounts?path={ext}&scope=SwarmAI")
    resp = client.get("/api/library/search?q=widget&scope=SwarmAI")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    names = " ".join(h["title"].lower() for h in body["hits"])
    assert "widget" in names, f"expected a widget hit, got {body}"


def test_search_empty_query_returns_empty(client):
    resp = client.get("/api/library/search?q=%20&scope=SwarmAI")
    assert resp.status_code == 200
    assert resp.json()["hits"] == []


def test_global_mount_visible_from_any_project_scope(client, tmp_path):
    """Gate-2 #2: a mount registered GLOBAL (the +Add Folder default) must surface
    in recall under ANY active project scope — not only 'SwarmAI'."""
    ext = _make_code_dir(tmp_path)
    client.post(f"/api/library/mounts?path={ext}")  # no scope → GLOBAL default
    # search under a DIFFERENT active project → still finds it (union with GLOBAL)
    resp = client.get("/api/library/search?q=widget&scope=SomeOtherProject")
    assert resp.status_code == 200, resp.text
    names = " ".join(h["title"].lower() for h in resp.json()["hits"])
    assert "widget" in names, f"GLOBAL mount invisible from other scope: {resp.json()}"


def test_mount_rejects_system_path(client):
    """Gate-2 #1: a protected system dir cannot be mounted (exfiltration guard),
    consistent with the Inbox endpoint."""
    resp = client.post("/api/library/mounts?path=/etc")
    assert resp.status_code == 400
    assert "system" in resp.json()["detail"].lower()


def test_freshness_job_is_registered():
    """AC5: the library-freshness job is in the system job registry with the right type."""
    from jobs.system_jobs import SYSTEM_JOBS
    job = next((j for j in SYSTEM_JOBS if j.id == "library-freshness"), None)
    assert job is not None, "library-freshness job not registered"
    assert job.type == "library_freshness"
    assert job.enabled


def test_freshness_handler_runs_on_empty(monkeypatch, tmp_path):
    """AC5: the handler runs without a DB (no mounts) — success, not a crash."""
    monkeypatch.setattr("jobs.paths.DB_PATH", tmp_path / "nope.db", raising=False)
    import jobs.paths as jp
    monkeypatch.setattr(jp, "DB_PATH", tmp_path / "nope.db", raising=False)
    from jobs.handlers.library_freshness import run_library_freshness
    result = run_library_freshness()
    assert result["status"] == "success"
