"""`/workspace/file/resolve` with a session id: the session's own products first
(design docs/design/2026-10-26-session-products.md §3.4).

Methodology: the real endpoint over ASGI; the workspace is a temp dir and the session's
Canvas record (core/session_outputs) a temp store with rows written through
``session_outputs.record`` (the same writer the stream uses). Properties:

- a relative reference that is a whole-segment tail of exactly one product opens it,
  even when the workspace has a file at the same relative path;
- an absolute reference equal to a product opens it;
- several products match → 409 listing them, no pick;
- a deleted product or one no longer on disk is skipped;
- no match → the existing cascade, and no ``session_id`` → today's answer exactly;
- a partial segment (``ign.md`` vs ``design.md``) is not a tail match;
- traversal is still 400.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

SID = "11111111-2222-3333-4444-555555555555"


@pytest.fixture
def env(tmp_path, monkeypatch):
    from core import session_outputs
    ws = tmp_path / "ws"
    (ws / "Projects").mkdir(parents=True)
    (ws / "docs" / "design").mkdir(parents=True)
    (ws / "docs" / "design" / "plan.md").write_text("workspace copy\n")
    store = tmp_path / "canvas_sessions"
    monkeypatch.setattr(session_outputs, "store_dir", lambda: store)
    wt = tmp_path / "home" / ".swarm-ai" / "session-worktrees" / "n1" / "repo"
    (wt / "docs" / "design").mkdir(parents=True)
    doc = wt / "docs" / "design" / "plan.md"
    doc.write_text("session copy\n")

    def add(path: Path, op: str = "written") -> None:
        session_outputs.record(SID, [{"type": "file_changed", "path": str(path), "absolutePath": str(path),
                                      "kind": "external-diff", "operation": op}])

    async def _ws():
        return str(ws)

    from main import app
    with patch("routers.workspace_api._get_workspace_path", new=_ws):
        yield app, ws, wt, doc, add


async def _get(app, **params):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        return await c.get("/api/workspace/file/resolve", params=params)


@pytest.mark.asyncio
async def test_relative_tail_opens_the_session_product_over_the_workspace_file(env):
    app, ws, wt, doc, add = env
    add(doc)
    r = await _get(app, path="docs/design/plan.md", session_id=SID)
    assert r.status_code == 200
    assert r.json()["resolved_path"] == str(doc.resolve())
    assert r.json().get("session_product") is True


@pytest.mark.asyncio
async def test_without_session_id_the_answer_is_unchanged(env):
    app, ws, wt, doc, add = env
    add(doc)
    r = await _get(app, path="docs/design/plan.md")
    assert r.status_code == 200
    assert r.json() == {"resolved_path": "docs/design/plan.md"}


@pytest.mark.asyncio
async def test_absolute_reference_equal_to_a_product(env):
    app, ws, wt, doc, add = env
    add(doc)
    r = await _get(app, path=str(doc), session_id=SID)
    assert r.status_code == 200 and r.json()["resolved_path"] == str(doc.resolve())
    assert r.json().get("session_product") is True


@pytest.mark.asyncio
async def test_several_matches_are_a_conflict_not_a_pick(env, tmp_path):
    app, ws, wt, doc, add = env
    other = tmp_path / "home" / "Desktop" / "docs" / "design" / "plan.md"
    other.parent.mkdir(parents=True)
    other.write_text("desktop\n")
    add(doc)
    add(other)
    r = await _get(app, path="plan.md", session_id=SID)
    assert r.status_code == 409
    assert sorted(r.json()["detail"]["candidates"]) == sorted([str(doc.resolve()), str(other.resolve())])


@pytest.mark.asyncio
async def test_deleted_or_missing_products_are_skipped(env):
    app, ws, wt, doc, add = env
    add(doc)
    add(doc, "deleted")
    r = await _get(app, path="docs/design/plan.md", session_id=SID)
    assert r.json() == {"resolved_path": "docs/design/plan.md"}   # the cascade
    add(doc)
    doc.unlink()
    r = await _get(app, path="docs/design/plan.md", session_id=SID)
    assert r.json() == {"resolved_path": "docs/design/plan.md"}


@pytest.mark.asyncio
async def test_partial_segment_is_not_a_tail(env):
    app, ws, wt, doc, add = env
    add(doc)
    r = await _get(app, path="an.md", session_id=SID)
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_a_swarmws_product_keeps_the_workspace_relative_form(env):
    app, ws, wt, doc, add = env
    note = ws / "Knowledge" / "Notes" / "q3.md"
    note.parent.mkdir(parents=True)
    note.write_text("q3\n")
    add(note)
    r = await _get(app, path="Notes/q3.md", session_id=SID)
    assert r.json() == {"resolved_path": "Knowledge/Notes/q3.md", "session_product": True}


@pytest.mark.asyncio
async def test_traversal_still_rejected(env):
    app, ws, wt, doc, add = env
    add(doc)
    r = await _get(app, path="../etc/passwd", session_id=SID)
    assert r.status_code == 400
