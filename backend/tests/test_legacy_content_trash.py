"""Legacy-content cleanup must never destroy a user's files.

``SwarmWorkspaceManager._cleanup_legacy_content`` removes names left over from
the pre-restructure SwarmWS layout (``index.md``, ``chats/``, ``workspace/``,
``Knowledge/Memory/``, ...).  Those are ordinary names a user can pick, so:

1. A workspace this code creates has no legacy content: the ``.legacy_cleaned``
   marker is written when the workspace root is first created, on the fresh
   creation path and on the seed-DB path (config row present, directory not yet
   on disk).  A second start therefore never touches files the user created.
2. Even on a real legacy workspace nothing is deleted: each item is moved into
   ``<workspace>/.trash/legacy-<stamp>/`` under its own relative path, and an
   item whose move fails stays where it is.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

import pytest

import core.swarm_workspace_manager as swm_module
from core.swarm_workspace_manager import SwarmWorkspaceManager
from database.sqlite import SQLiteDatabase

# Every legacy name the cleanup targets, created as a user would: relative path
# -> file content ("<dir>/" entries are folders holding one file).
USER_FILES = {
    "index.md": "my own index",
    "Knowledge/index.md": "my knowledge index",
    "chats/notes.md": "a chat I kept",
    "workspace/draft.md": "a draft",
    "Knowledge/Memory/today.md": "my memory notes",
    "ContextFiles/ctx.md": "context I wrote",
    "_tmp_transfer/payload.txt": "a transfer",
    "Projects/MyProj/context-L0.md": "level zero",
    "Projects/MyProj/context-L1.md": "level one",
}

# The top-level items the cleanup moves for USER_FILES (relative to the root).
LEGACY_ITEMS = [
    "index.md", "Knowledge/index.md", "chats", "workspace", "Knowledge/Memory",
    "ContextFiles", "_tmp_transfer",
    "Projects/MyProj/context-L0.md", "Projects/MyProj/context-L1.md",
]


def _write_files(root: Path) -> None:
    for rel, content in USER_FILES.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def _assert_files_intact(root: Path) -> None:
    for rel, content in USER_FILES.items():
        path = root / rel
        assert path.is_file(), f"{rel} was removed by the second start"
        assert path.read_text(encoding="utf-8") == content


@pytest.fixture
async def workspace_env(tmp_path, monkeypatch):
    """A real SQLiteDatabase and DEFAULT_WORKSPACE_CONFIG pointing at tmp_path."""
    workspace_path = str(tmp_path / "SwarmWS")
    monkeypatch.setitem(swm_module.DEFAULT_WORKSPACE_CONFIG, "file_path", workspace_path)
    db = SQLiteDatabase(db_path=str(tmp_path / "test.db"))
    await db.initialize()
    return db, Path(workspace_path)


@pytest.mark.asyncio
async def test_fresh_install_second_start_keeps_user_files(workspace_env):
    db, root = workspace_env
    assert not root.exists()
    manager = SwarmWorkspaceManager()

    await manager.ensure_default_workspace(db)  # first start: creates the workspace
    marked_at_creation = (root / ".legacy_cleaned").is_file()

    _write_files(root)
    await manager.ensure_default_workspace(db)  # second start

    _assert_files_intact(root)
    assert not (root / ".trash").exists()
    assert marked_at_creation


@pytest.mark.asyncio
async def test_seed_path_second_start_keeps_user_files(workspace_env):
    """Seed-DB fast path: the config row exists before the directory does."""
    db, root = workspace_env
    now = datetime.now(timezone.utc).isoformat()
    await db.workspace_config.put({
        "id": "swarmws",
        "name": swm_module.DEFAULT_WORKSPACE_CONFIG["name"],
        "file_path": str(root),
        "icon": swm_module.DEFAULT_WORKSPACE_CONFIG["icon"],
        "created_at": now,
        "updated_at": now,
    })
    assert not root.exists()
    manager = SwarmWorkspaceManager()

    await manager.ensure_default_workspace(db)  # first start on the seed path
    marked_at_creation = (root / ".legacy_cleaned").is_file()

    _write_files(root)
    await manager.ensure_default_workspace(db)  # second start

    _assert_files_intact(root)
    assert not (root / ".trash").exists()
    assert marked_at_creation


@pytest.mark.asyncio
async def test_legacy_items_moved_to_dated_trash(tmp_path):
    root = tmp_path / "SwarmWS"
    (root / "Projects").mkdir(parents=True)
    _write_files(root)

    await SwarmWorkspaceManager()._cleanup_legacy_content(str(root))

    trash_dirs = list((root / ".trash").glob("legacy-*"))
    assert len(trash_dirs) == 1, trash_dirs
    trash = trash_dirs[0]
    for rel in LEGACY_ITEMS:
        assert not (root / rel).exists(), f"{rel} still at its original path"
        assert (trash / rel).exists(), f"{rel} not in {trash}"
    for rel, content in USER_FILES.items():
        assert (trash / rel).read_text(encoding="utf-8") == content
    assert (root / ".legacy_cleaned").is_file()
    # The project folder itself is not legacy: only its two context files moved.
    assert (root / "Projects" / "MyProj").is_dir()


@pytest.mark.asyncio
async def test_move_failure_leaves_item_in_place(tmp_path, monkeypatch, caplog):
    root = tmp_path / "SwarmWS"
    (root / "Projects").mkdir(parents=True)
    _write_files(root)
    blocked = root / "chats"
    real_rename = Path.rename

    def _rename(self, target):
        if self == blocked:
            raise OSError("simulated rename failure")
        return real_rename(self, target)

    monkeypatch.setattr(Path, "rename", _rename)
    with caplog.at_level(logging.WARNING, logger=swm_module.logger.name):
        await SwarmWorkspaceManager()._cleanup_legacy_content(str(root))

    assert (root / "chats" / "notes.md").read_text(encoding="utf-8") == "a chat I kept"
    assert any("chats" in r.getMessage() and "simulated rename failure" in r.getMessage()
               for r in caplog.records), [r.getMessage() for r in caplog.records]
    trash = next((root / ".trash").glob("legacy-*"))
    assert (trash / "index.md").read_text(encoding="utf-8") == "my own index"
    assert not (root / "index.md").exists()


@pytest.mark.asyncio
async def test_knowledge_base_still_migrated_to_library(tmp_path):
    root = tmp_path / "SwarmWS"
    kb = root / "Knowledge" / "Knowledge Base"
    kb.mkdir(parents=True)
    (kb / "ref.md").write_text("reference", encoding="utf-8")

    await SwarmWorkspaceManager()._cleanup_legacy_content(str(root))

    assert (root / "Knowledge" / "Library" / "ref.md").read_text(encoding="utf-8") == "reference"
    assert not kb.exists()


@pytest.mark.asyncio
async def test_marker_present_touches_nothing(tmp_path):
    root = tmp_path / "SwarmWS"
    (root / "Projects").mkdir(parents=True)
    _write_files(root)
    (root / ".legacy_cleaned").write_text("done")

    await SwarmWorkspaceManager()._cleanup_legacy_content(str(root))

    _assert_files_intact(root)
    assert not (root / ".trash").exists()


@pytest.mark.asyncio
async def test_trashed_private_files_stay_out_of_git(tmp_path):
    """Moved items must not become stageable: the root .gitignore keeps
    ``Projects/<name>/`` private only at its original location."""
    import subprocess

    root = tmp_path / "SwarmWS"
    (root / "Projects").mkdir(parents=True)
    (root / ".gitignore").write_text(swm_module.GITIGNORE_CONTENT, encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    _write_files(root)

    await SwarmWorkspaceManager()._cleanup_legacy_content(str(root))

    assert next((root / ".trash").glob("legacy-*/Projects/MyProj/context-L0.md"))
    status = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=all"],
        cwd=root, check=True, capture_output=True, text=True,
    ).stdout
    assert ".trash" not in status, status


@pytest.mark.asyncio
async def test_new_config_over_existing_dir_is_cleaned_on_this_start(workspace_env):
    """No config row but the directory survived (e.g. the database was reset):
    the cleanup runs on this start, so files the user creates afterwards are
    not moved by the next one."""
    db, root = workspace_env
    (root / "chats").mkdir(parents=True)
    (root / "chats" / "old.md").write_text("legacy chat", encoding="utf-8")
    manager = SwarmWorkspaceManager()

    await manager.ensure_default_workspace(db)  # creates the config row

    trash = next((root / ".trash").glob("legacy-*"))
    assert (trash / "chats" / "old.md").read_text(encoding="utf-8") == "legacy chat"
    assert (root / ".legacy_cleaned").is_file()

    _write_files(root)
    await manager.ensure_default_workspace(db)  # next start

    _assert_files_intact(root)
