"""A governed file the guard refuses to save answers 422 with the reason, on both save
routes (``PUT /api/workspace/file`` and ``PUT /api/workspace/{agent}/write``); before,
the refusal escaped as a 500 and the editor showed no reason.

Methodology: FastAPI TestClient; the governed check and the edit are monkeypatched, so
no workspace file or store is touched."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tests.test_cdf_v2_e2e import cdf_env  # noqa: F401
from pathlib import Path

REASON = "Content rejected by MemoryGuard: print_instructions — reword or remove it to save"


@pytest.fixture
def refusing(tmp_path, monkeypatch):
    from core.memory_guard import MemoryGuardError
    workspace = tmp_path / "SwarmWS"
    workspace.mkdir()
    (workspace / "a.md").write_text("old\n", encoding="utf-8")
    monkeypatch.setattr("core.initialization_manager.initialization_manager.get_cached_workspace_path",
                        lambda: str(workspace))

    async def _ws():
        return str(workspace)
    monkeypatch.setattr("routers.workspace_api._get_workspace_path", _ws)

    def refuse(*_a, **_k):
        raise MemoryGuardError(REASON)
    monkeypatch.setattr("core.ingestion.api.apply_user_edit", refuse)
    monkeypatch.setattr("routers.workspace_api._cdf_is_governed", lambda _p: True)
    monkeypatch.setattr("routers.workspace._cdf_is_governed", lambda _p: True)
    return workspace


def test_the_file_route_answers_422_with_the_reason(client: TestClient, refusing):
    resp = client.put("/api/workspace/file", params={"path": "a.md"},
                      json={"content": "new\n", "expected_revision": "r"})
    assert resp.status_code == 422 and resp.json()["detail"] == REASON
    assert (refusing / "a.md").read_text(encoding="utf-8") == "old\n"


def test_the_agent_write_route_answers_422_with_the_reason(client: TestClient, refusing):
    resp = client.put("/api/workspace/agent1/write",
                      json={"path": "a.md", "content": "new\n", "expected_revision": "r"})
    assert resp.status_code == 422 and resp.json()["detail"] == REASON


def test_the_upload_route_answers_422_with_the_reason(client: TestClient, refusing):
    import base64
    resp = client.post("/api/workspace/agent1/upload",
                       json={"filename": "b.txt", "content": base64.b64encode(b"x\n").decode(),
                             "path": ".", "expected_revision": ""})
    assert resp.status_code == 422 and resp.json()["detail"] == REASON


def test_the_api_wrapper_forwards_moved_from(monkeypatch):
    """The rename route calls core.ingestion.api.apply_user_edit with moved_from; the
    wrapper must accept and forward it (the call raised TypeError before)."""
    from core.ingestion import api, governed_edit
    seen = {}

    def inner(path, data, **kw):
        seen.update(kw)
        return "r"
    monkeypatch.setattr(governed_edit, "apply_user_edit", inner)
    api.apply_user_edit(Path("x.md"), b"x", expected_revision="", moved_from=Path("y.md"))
    assert seen["moved_from"] == Path("y.md")


def test_the_rename_route_answers_422_with_the_reason(cdf_env, monkeypatch):
    import asyncio
    from fastapi import HTTPException
    from core.memory_guard import MemoryGuardError
    from tests.test_cdf_launch_explorer import api_ws
    import core.ingestion.api as ingestion_api
    api, ws = api_ws.__wrapped__(cdf_env, monkeypatch)
    (ws / "Knowledge/Notes/a.md").write_text("first\n", encoding="utf-8")

    def refuse(*_a, **_k):
        raise MemoryGuardError(REASON)
    monkeypatch.setattr(ingestion_api, "apply_user_edit", refuse)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(api.rename_item(api.FolderRenameRequest(
            old_path="Knowledge/Notes/a.md", new_path="Knowledge/Notes/b.md")))
    assert exc.value.status_code == 422 and exc.value.detail == REASON
