"""Property-based tests for workspace path migration.

**Feature: unified-swarm-workspace-cwd, Property 10: Migration preserves workspace contents**

Uses Hypothesis to verify that ``SwarmWorkspaceManager._migrate_default_workspace_path()``
correctly moves all files from the old path to the new path and updates the DB record.

**Validates: Requirements 2.3, 2.4, 2.5**
"""
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from hypothesis import given, strategies as st, settings, HealthCheck, assume

from core.swarm_workspace_manager import SwarmWorkspaceManager


PROPERTY_SETTINGS = settings(
    max_examples=50,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)

# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

# Safe filename characters (avoid OS-reserved chars and path separators)
_safe_filename_chars = st.sampled_from(
    "abcdefghijklmnopqrstuvwxyz"
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "0123456789_-."
)

safe_filename = st.text(
    alphabet=_safe_filename_chars,
    min_size=1,
    max_size=20,
).filter(lambda n: n not in (".", "..") and not n.startswith("."))

# Strategy for a single file entry: (relative_path_parts, content_bytes)
file_entry = st.tuples(
    st.lists(safe_filename, min_size=1, max_size=3),
    st.binary(min_size=0, max_size=256),
)

# Strategy for a file tree: list of (path_parts, content) tuples
file_tree = st.lists(file_entry, min_size=1, max_size=10)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def create_file_tree(root: Path, entries: list[tuple[list[str], bytes]]) -> dict[str, bytes]:
    """Create files on disk from a list of (path_parts, content) entries.

    Skips entries that would conflict (e.g. a file at 'a' and a dir 'a/b').
    Returns a dict mapping relative path strings to their content for
    later verification.
    """
    created: dict[str, bytes] = {}
    used_as_dir: set[str] = set()

    for parts, content in entries:
        rel = "/".join(parts)
        if rel in created or rel in used_as_dir:
            continue  # skip duplicate paths or paths used as directories

        # Check that no prefix of this path was already created as a file
        # and that this path isn't a prefix of an existing directory
        conflict = False
        for i in range(1, len(parts)):
            prefix = "/".join(parts[:i])
            if prefix in created:
                conflict = True
                break
        if conflict:
            continue

        # Mark all parent prefixes as directories
        for i in range(1, len(parts)):
            prefix = "/".join(parts[:i])
            used_as_dir.add(prefix)

        file_path = root / rel
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_bytes(content)
        created[rel] = content
    return created


def read_file_tree(root: Path) -> dict[str, bytes]:
    """Read all files under root into a dict of relative path → content."""
    result = {}
    if not root.exists():
        return result
    for file_path in root.rglob("*"):
        if file_path.is_file():
            rel = str(file_path.relative_to(root))
            result[rel] = file_path.read_bytes()
    return result


def make_workspace_dict(old_file_path: str) -> dict:
    """Create a minimal workspace dict with the old path pattern."""
    return {
        "id": str(uuid4()),
        "name": "SwarmWS",
        "file_path": old_file_path,
        "context": "Default SwarmAI workspace.",
        "icon": "🏠",
        "is_default": True,
    }


def make_mock_db():
    """Create a mock DB with swarm_workspaces.put as an AsyncMock."""
    db = MagicMock()
    db.swarm_workspaces = MagicMock()
    db.swarm_workspaces.put = AsyncMock()
    return db


# ---------------------------------------------------------------------------
# Property Tests
# ---------------------------------------------------------------------------


class TestMigrationPreservesWorkspaceContents:
    """Property 10: Migration preserves workspace contents.

    **Validates: Requirements 2.3, 2.4, 2.5**
    """

    @given(tree=file_tree)
    @PROPERTY_SETTINGS
    @pytest.mark.asyncio
    async def test_all_files_present_at_new_path_after_migration(
        self, tmp_path: Path, tree: list[tuple[list[str], bytes]]
    ):
        """All files from old path are present at new path after migration.

        **Validates: Requirements 2.3, 2.4**

        Generate a random file tree at the old path, run migration,
        verify every file exists at the new path with identical content.
        """
        # Use a unique subdirectory per example to avoid cross-contamination
        example_dir = tmp_path / str(uuid4())
        old_path = example_dir / "swarm-workspaces" / "SwarmWS"
        new_path = example_dir / "SwarmWS"

        # Create the random file tree at the old path
        expected_files = create_file_tree(old_path, tree)
        assume(len(expected_files) > 0)

        # Setup manager and mock expand_path to use tmp_path
        manager = SwarmWorkspaceManager()
        old_file_path = "{app_data_dir}/swarm-workspaces/SwarmWS"
        workspace = make_workspace_dict(old_file_path)
        mock_db = make_mock_db()

        def fake_expand(fp: str) -> str:
            return fp.replace("{app_data_dir}", str(example_dir))

        with patch.object(manager, "expand_path", side_effect=fake_expand):
            await manager._migrate_default_workspace_path(workspace, mock_db)

        # Verify all files are at the new path with same content
        actual_files = read_file_tree(new_path)
        assert set(actual_files.keys()) == set(expected_files.keys()), (
            f"File sets differ. Expected: {set(expected_files.keys())}, "
            f"Got: {set(actual_files.keys())}"
        )
        for rel_path, expected_content in expected_files.items():
            assert actual_files[rel_path] == expected_content, (
                f"Content mismatch for {rel_path}"
            )

    @given(tree=file_tree)
    @PROPERTY_SETTINGS
    @pytest.mark.asyncio
    async def test_old_path_removed_after_migration(
        self, tmp_path: Path, tree: list[tuple[list[str], bytes]]
    ):
        """Old path no longer exists after successful migration.

        **Validates: Requirements 2.3, 2.4**
        """
        example_dir = tmp_path / str(uuid4())
        old_path = example_dir / "swarm-workspaces" / "SwarmWS"
        new_path = example_dir / "SwarmWS"

        expected_files = create_file_tree(old_path, tree)
        assume(len(expected_files) > 0)

        manager = SwarmWorkspaceManager()
        old_file_path = "{app_data_dir}/swarm-workspaces/SwarmWS"
        workspace = make_workspace_dict(old_file_path)
        mock_db = make_mock_db()

        def fake_expand(fp: str) -> str:
            return fp.replace("{app_data_dir}", str(example_dir))

        with patch.object(manager, "expand_path", side_effect=fake_expand):
            await manager._migrate_default_workspace_path(workspace, mock_db)

        assert not old_path.exists(), "Old path should be removed after migration"

    @given(tree=file_tree)
    @PROPERTY_SETTINGS
    @pytest.mark.asyncio
    async def test_db_record_updated_to_new_path(
        self, tmp_path: Path, tree: list[tuple[list[str], bytes]]
    ):
        """DB record is updated to the new file_path pattern after migration.

        **Validates: Requirements 2.3, 2.5**
        """
        example_dir = tmp_path / str(uuid4())
        old_path = example_dir / "swarm-workspaces" / "SwarmWS"

        create_file_tree(old_path, tree)

        manager = SwarmWorkspaceManager()
        old_file_path = "{app_data_dir}/swarm-workspaces/SwarmWS"
        workspace = make_workspace_dict(old_file_path)
        mock_db = make_mock_db()

        def fake_expand(fp: str) -> str:
            return fp.replace("{app_data_dir}", str(example_dir))

        with patch.object(manager, "expand_path", side_effect=fake_expand):
            await manager._migrate_default_workspace_path(workspace, mock_db)

        # Verify DB was called with the new path
        mock_db.swarm_workspaces.put.assert_awaited_once()
        saved_ws = mock_db.swarm_workspaces.put.call_args[0][0]
        assert saved_ws["file_path"] == manager.DEFAULT_WORKSPACE_CONFIG["file_path"], (
            f"DB record should use new path pattern "
            f"'{manager.DEFAULT_WORKSPACE_CONFIG['file_path']}', "
            f"got '{saved_ws['file_path']}'"
        )

    @given(
        old_tree=file_tree,
        new_tree=file_tree,
    )
    @PROPERTY_SETTINGS
    @pytest.mark.asyncio
    async def test_both_paths_exist_keeps_new_path(
        self,
        tmp_path: Path,
        old_tree: list[tuple[list[str], bytes]],
        new_tree: list[tuple[list[str], bytes]],
    ):
        """When both old and new paths exist, new path is kept unchanged.

        **Validates: Requirements 2.5**
        """
        example_dir = tmp_path / str(uuid4())
        old_path = example_dir / "swarm-workspaces" / "SwarmWS"
        new_path = example_dir / "SwarmWS"

        create_file_tree(old_path, old_tree)
        new_expected = create_file_tree(new_path, new_tree)
        assume(len(new_expected) > 0)

        manager = SwarmWorkspaceManager()
        old_file_path = "{app_data_dir}/swarm-workspaces/SwarmWS"
        workspace = make_workspace_dict(old_file_path)
        mock_db = make_mock_db()

        def fake_expand(fp: str) -> str:
            return fp.replace("{app_data_dir}", str(example_dir))

        with patch.object(manager, "expand_path", side_effect=fake_expand):
            await manager._migrate_default_workspace_path(workspace, mock_db)

        # New path should still have its original files, unchanged
        actual_new = read_file_tree(new_path)
        for rel_path, expected_content in new_expected.items():
            assert actual_new.get(rel_path) == expected_content, (
                f"New path file {rel_path} should be unchanged"
            )

        # Old path should still exist (left for manual cleanup)
        assert old_path.exists(), (
            "Old path should be left untouched when both paths exist"
        )

        # DB should still be updated to new path
        saved_ws = mock_db.swarm_workspaces.put.call_args[0][0]
        assert saved_ws["file_path"] == manager.DEFAULT_WORKSPACE_CONFIG["file_path"]
