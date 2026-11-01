"""Explorer delete goes to the OS trash on every platform (R4.3).

``trash_item`` hands the file to ``send2trash`` (the platform's own trash: macOS Trash,
Windows Recycle Bin, the freedesktop trash on Linux) after the existing workspace path
checks, instead of moving it into ``~/.Trash`` (macOS only). A path outside the
workspace is still refused and nothing is trashed.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastapi import HTTPException


@pytest.fixture
def api_ws(tmp_path, monkeypatch):
    from routers import workspace_api
    ws = tmp_path / "SwarmWS"
    (ws / "Scratch").mkdir(parents=True)
    monkeypatch.setenv("SWARM_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SWARM_WORKSPACE", str(ws))
    # the old code moved into ~/.Trash: keep any such move inside the test's folder
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))

    async def root():
        return str(ws)
    monkeypatch.setattr(workspace_api, "_get_workspace_path", root)
    monkeypatch.setattr(workspace_api, "_invalidate_tree_cache", lambda: None)

    trashed: list[str] = []
    bin_dir = tmp_path / "os-trash"
    bin_dir.mkdir()

    def fake_send2trash(path):
        trashed.append(str(path))
        Path(path).rename(bin_dir / Path(path).name)
    monkeypatch.setattr(workspace_api, "send2trash", fake_send2trash, raising=False)
    return workspace_api, ws, trashed, bin_dir


def test_a_file_goes_to_the_os_trash(api_ws):
    api, ws, trashed, bin_dir = api_ws
    note = ws / "Scratch" / "draft.txt"
    note.write_text("draft\n", encoding="utf-8")
    result = asyncio.run(api.trash_item(api.FolderDeleteRequest(path="Scratch/draft.txt")))
    assert result["trashed"] is True
    assert not note.exists()
    assert trashed == [str(note.resolve())]
    assert (bin_dir / "draft.txt").read_text(encoding="utf-8") == "draft\n"


def test_a_folder_goes_to_the_os_trash(api_ws):
    api, ws, trashed, _bin = api_ws
    folder = ws / "Scratch" / "old"
    folder.mkdir()
    (folder / "a.txt").write_text("a\n", encoding="utf-8")
    asyncio.run(api.trash_item(api.FolderDeleteRequest(path="Scratch/old")))
    assert not folder.exists() and trashed == [str(folder.resolve())]


def test_a_path_outside_the_workspace_is_refused(api_ws, tmp_path):
    api, ws, trashed, _bin = api_ws
    outside = tmp_path / "outside.txt"
    outside.write_text("keep\n", encoding="utf-8")
    with pytest.raises(HTTPException) as exc:
        asyncio.run(api.trash_item(api.FolderDeleteRequest(path="../outside.txt")))
    assert 400 <= exc.value.status_code < 500
    assert outside.exists() and trashed == []


def test_a_trash_failure_is_reported_not_deleted(api_ws, monkeypatch):
    api, ws, _trashed, _bin = api_ws
    note = ws / "Scratch" / "stuck.txt"
    note.write_text("x\n", encoding="utf-8")

    def failing(path):
        raise OSError("no trash here")
    monkeypatch.setattr(api, "send2trash", failing, raising=False)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(api.trash_item(api.FolderDeleteRequest(path="Scratch/stuck.txt")))
    assert exc.value.status_code == 500 and note.exists()
