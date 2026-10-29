"""The file explorer can create, trash and rename knowledge files again
(2026-10-29-cdf-launch §4). Every governed path refused these with 409 because the
explorer sends no revision; each is now a journaled governed edit (archived), so the
single-writer guarantee holds."""
from __future__ import annotations

import asyncio

import pytest

from tests.test_cdf_v2_e2e import cdf_env  # noqa: F401


@pytest.fixture
def api_ws(cdf_env, monkeypatch):
    from routers import workspace_api
    ws = cdf_env["workspace"]

    async def root():
        return str(ws)
    monkeypatch.setattr(workspace_api, "_get_workspace_path", root)
    monkeypatch.setattr(workspace_api, "_invalidate_tree_cache", lambda: None)
    return workspace_api, ws


def _plans():
    from core.ingestion import api
    conn = api.connect_rw()
    rows = conn.execute("SELECT op_kind,target_path,step FROM ingestion_writes ORDER BY created_at").fetchall()
    conn.close()
    return [tuple(r) for r in rows]


def test_create_trash_and_rename_a_knowledge_note(api_ws):
    api, ws = api_ws
    note = "Knowledge/Notes/idea.md"
    asyncio.run(api.create_file(api.FolderCreateRequest(path=note)))
    assert (ws / note).is_file()
    (ws / note).write_text("# Idea\n\nkeep this\n", encoding="utf-8")
    asyncio.run(api.rename_item(api.FolderRenameRequest(old_path=note, new_path="Knowledge/Notes/idea2.md")))
    assert not (ws / note).exists()
    assert (ws / "Knowledge/Notes/idea2.md").read_text(encoding="utf-8") == "# Idea\n\nkeep this\n"
    asyncio.run(api.trash_item(api.FolderDeleteRequest(path="Knowledge/Notes/idea2.md")))
    assert not (ws / "Knowledge/Notes/idea2.md").exists()
    plans = _plans()
    assert len(plans) >= 4 and all(step == "done" for _k, _p, step in plans)


def test_a_stale_revision_is_still_refused_when_one_is_sent(api_ws):
    from fastapi import HTTPException
    api, ws = api_ws
    note = ws / "Knowledge/Notes/x.md"
    note.write_text("a\n", encoding="utf-8")
    with pytest.raises(HTTPException) as exc:
        asyncio.run(api.trash_item(api.FolderDeleteRequest(path="Knowledge/Notes/x.md",
                                                           expected_revision="0" * 64)))
    assert exc.value.status_code == 409 and note.exists()


def test_moving_a_note_out_of_the_knowledge_folders_is_refused(api_ws):
    from fastapi import HTTPException
    api, ws = api_ws
    (ws / "Knowledge/Notes/y.md").write_text("b\n", encoding="utf-8")
    (ws / "Scratch").mkdir()
    with pytest.raises(HTTPException) as exc:
        asyncio.run(api.rename_item(api.FolderRenameRequest(old_path="Knowledge/Notes/y.md",
                                                            new_path="Scratch/y.md")))
    assert exc.value.status_code == 409 and (ws / "Knowledge/Notes/y.md").exists()


def test_a_new_empty_file_is_created_and_an_empty_file_can_be_deleted(api_ws):
    """Absent and empty both read as b"": a governed "new file" was reported done
    without being created."""
    api, ws = api_ws
    asyncio.run(api.create_file(api.FolderCreateRequest(path="Knowledge/Notes/empty.md")))
    assert (ws / "Knowledge/Notes/empty.md").is_file()
    asyncio.run(api.trash_item(api.FolderDeleteRequest(path="Knowledge/Notes/empty.md")))
    assert not (ws / "Knowledge/Notes/empty.md").exists()
