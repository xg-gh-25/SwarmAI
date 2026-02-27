"""Property-based tests for non-destructive folder structure integrity.

**Feature: unified-swarm-workspace-cwd, Property 8: Non-destructive folder structure integrity**

Uses Hypothesis to verify that ``SwarmWorkspaceManager.create_folder_structure()``
creates all standard folders and does not modify any pre-existing files.

**Validates: Requirements 8.1, 8.2**
"""
import asyncio
from pathlib import Path
from uuid import uuid4

import pytest
from hypothesis import given, strategies as st, settings, HealthCheck

from core.swarm_workspace_manager import SwarmWorkspaceManager


PROPERTY_SETTINGS = settings(
    max_examples=50,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)

FOLDER_STRUCTURE = SwarmWorkspaceManager.FOLDER_STRUCTURE

# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

# Strategy: safe relative path segments (no .., no empty, no slashes)
_path_segment = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N"), whitelist_characters="_-"),
    min_size=1,
    max_size=12,
)

# Strategy: random file content
_file_content = st.binary(min_size=0, max_size=256)

# Strategy: a random relative file path (1-3 segments deep)
_relative_path = st.lists(_path_segment, min_size=1, max_size=3).map(
    lambda parts: "/".join(parts)
)


@st.composite
def _file_tree(draw: st.DrawFn) -> dict[str, bytes]:
    """Generate a conflict-free file tree.

    Ensures no file path is a prefix of another (which would make
    mkdir fail because a file already occupies that path segment).
    Also handles case-insensitive filesystems (macOS) by comparing
    lowercased paths.
    """
    raw = draw(
        st.dictionaries(
            keys=_relative_path,
            values=_file_content,
            min_size=0,
            max_size=10,
        )
    )
    # Remove entries where one path is a parent of another
    # Use case-insensitive comparison for macOS compatibility
    paths = sorted(raw.keys())
    result: dict[str, bytes] = {}
    for p in paths:
        p_lower = p.lower()
        conflict = False
        for existing in list(result.keys()):
            existing_lower = existing.lower()
            if (existing_lower.startswith(p_lower + "/")
                    or p_lower.startswith(existing_lower + "/")
                    or existing_lower == p_lower):
                conflict = True
                break
        if not conflict:
            result[p] = raw[p]
    return result


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def populate_file_tree(root: Path, tree: dict[str, bytes]) -> None:
    """Write a dict of {relative_path: content} into the root directory."""
    for rel_path, content in tree.items():
        file_path = root / rel_path
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_bytes(content)


def snapshot_files(root: Path) -> dict[str, bytes]:
    """Return a mapping of relative-path -> content for all files under root."""
    result = {}
    if not root.exists():
        return result
    for p in root.rglob("*"):
        if p.is_file():
            result[str(p.relative_to(root))] = p.read_bytes()
    return result


# ---------------------------------------------------------------------------
# Property Tests
# ---------------------------------------------------------------------------


class TestNonDestructiveFolderStructure:
    """Property 8: Non-destructive folder structure integrity.

    **Feature: unified-swarm-workspace-cwd, Property 8: Non-destructive folder structure integrity**

    After ``create_folder_structure()`` completes, the standard folders
    (Artifacts/, ContextFiles/, Transcripts/ and subdirectories) should exist,
    and any pre-existing files should remain unmodified.

    **Validates: Requirements 8.1, 8.2**
    """

    @given(file_tree=_file_tree())
    @PROPERTY_SETTINGS
    @pytest.mark.asyncio
    async def test_standard_folders_exist_after_call(
        self,
        tmp_path: Path,
        file_tree: dict[str, bytes],
    ):
        """All standard folders from FOLDER_STRUCTURE exist after create_folder_structure().

        **Validates: Requirements 8.1**

        1. Create a workspace directory with a random file tree.
        2. Call create_folder_structure().
        3. Verify every folder in FOLDER_STRUCTURE exists.
        """
        workspace = tmp_path / str(uuid4())
        workspace.mkdir(parents=True, exist_ok=True)

        populate_file_tree(workspace, file_tree)

        manager = SwarmWorkspaceManager()
        await manager.create_folder_structure(str(workspace))

        for folder in FOLDER_STRUCTURE:
            folder_path = workspace / folder
            assert folder_path.exists(), (
                f"Standard folder '{folder}' does not exist after create_folder_structure()"
            )
            assert folder_path.is_dir(), (
                f"'{folder}' should be a directory, not a file"
            )

    @given(file_tree=_file_tree())
    @PROPERTY_SETTINGS
    @pytest.mark.asyncio
    async def test_preexisting_files_untouched(
        self,
        tmp_path: Path,
        file_tree: dict[str, bytes],
    ):
        """Pre-existing files remain unmodified after create_folder_structure().

        **Validates: Requirements 8.2**

        1. Create a workspace directory with a random file tree.
        2. Snapshot all file contents.
        3. Call create_folder_structure().
        4. Verify every pre-existing file still has the same content.
        """
        workspace = tmp_path / str(uuid4())
        workspace.mkdir(parents=True, exist_ok=True)

        populate_file_tree(workspace, file_tree)
        before = snapshot_files(workspace)

        manager = SwarmWorkspaceManager()
        await manager.create_folder_structure(str(workspace))

        for rel_path, original_content in before.items():
            # System folders take precedence over conflicting user files
            if rel_path.lower() in {f.lower() for f in FOLDER_STRUCTURE}:
                continue
            file_path = workspace / rel_path
            assert file_path.exists(), (
                f"Pre-existing file '{rel_path}' was deleted by create_folder_structure()"
            )
            actual_content = file_path.read_bytes()
            assert actual_content == original_content, (
                f"Pre-existing file '{rel_path}' was modified by create_folder_structure()"
            )

    @given(file_tree=_file_tree())
    @PROPERTY_SETTINGS
    @pytest.mark.asyncio
    async def test_combined_integrity(
        self,
        tmp_path: Path,
        file_tree: dict[str, bytes],
    ):
        """Standard folders exist AND pre-existing files are untouched (combined check).

        **Validates: Requirements 8.1, 8.2**

        1. Create a workspace with a random file tree.
        2. Snapshot file contents.
        3. Call create_folder_structure().
        4. Verify all standard folders exist.
        5. Verify all pre-existing files have unchanged content.
        """
        workspace = tmp_path / str(uuid4())
        workspace.mkdir(parents=True, exist_ok=True)

        populate_file_tree(workspace, file_tree)
        before = snapshot_files(workspace)

        manager = SwarmWorkspaceManager()
        await manager.create_folder_structure(str(workspace))

        # Standard folders must exist
        for folder in FOLDER_STRUCTURE:
            assert (workspace / folder).is_dir(), (
                f"Standard folder '{folder}' missing after create_folder_structure()"
            )

        # Pre-existing files must be unchanged (skip files replaced by system folders)
        system_folder_set = {f.lower() for f in FOLDER_STRUCTURE}
        for rel_path, original_content in before.items():
            # System folders take precedence over conflicting user files
            if rel_path.lower() in system_folder_set:
                continue
            file_path = workspace / rel_path
            assert file_path.exists(), (
                f"Pre-existing file '{rel_path}' was deleted"
            )
            assert file_path.read_bytes() == original_content, (
                f"Pre-existing file '{rel_path}' was modified"
            )
