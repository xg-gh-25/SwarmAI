"""Property-based tests for workspace folder creation.

**Feature: swarmws-restructure-git-init, Property 4: Workspace creation creates required folders**

Uses Hypothesis to verify that ``create_folder_structure()`` creates exactly
the required filesystem folders (``Knowledge/``, ``Projects/``) and does NOT
create folders for DB-canonical entities (Tasks, ToDos, etc.).

Updated during SwarmWS restructure to reflect the simplified folder structure:
only ``Knowledge/`` and ``Projects/`` are created (no subfolders).

**Validates: Requirements 1.1, 1.7, 1.8**
"""
import os
import shutil
import tempfile

import pytest
from hypothesis import given, strategies as st, settings, HealthCheck

from core.swarm_workspace_manager import SwarmWorkspaceManager
from tests.helpers import PROPERTY_SETTINGS





# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

REQUIRED_FOLDERS = [
    "Knowledge",
    "Projects",
    "Attachments",
    "Services",
]

# Knowledge subdirectories created by create_folder_structure
# Must match KNOWLEDGE_SUBDIRS in swarm_workspace_manager.py
REQUIRED_KNOWLEDGE_SUBDIRS = [
    "Knowledge/Notes",
    "Knowledge/Reports",
    "Knowledge/Meetings",
    "Knowledge/Library",
    "Knowledge/Archives",
    "Knowledge/DailyActivity",
    "Knowledge/Handoffs",
    "Knowledge/Designs",
    "Knowledge/Signals",
    "Knowledge/JobResults",
]

FORBIDDEN_FOLDERS = [
    "Tasks",
    "ToDos",
    "Plans",
    "Communications",
    "ChatThreads",
    "PlanItems",
]

# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

workspace_name_strategy = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N")),
    min_size=1,
    max_size=20,
).filter(lambda t: t.strip() and "/" not in t and ".." not in t)


# ---------------------------------------------------------------------------
# Property Tests
# ---------------------------------------------------------------------------


class TestWorkspaceFolderCreation:
    """Property 4: Workspace creation creates required folders.

    Validates: Requirements 1.1, 1.7, 1.8
    """

    @given(name=workspace_name_strategy)
    @PROPERTY_SETTINGS
    @pytest.mark.asyncio
    async def test_all_required_folders_exist(self, name: str):
        """All required folders exist after create_folder_structure.

        **Validates: Requirements 1.7**
        """
        tmp_dir = tempfile.mkdtemp()
        try:
            workspace_path = os.path.join(tmp_dir, name)
            manager = SwarmWorkspaceManager()
            await manager.create_folder_structure(workspace_path)

            for folder in REQUIRED_FOLDERS:
                folder_path = os.path.join(workspace_path, folder)
                assert os.path.isdir(folder_path), (
                    f"Required folder '{folder}' must exist after "
                    f"create_folder_structure, but was not found at {folder_path}"
                )
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    @given(name=workspace_name_strategy)
    @PROPERTY_SETTINGS
    @pytest.mark.asyncio
    async def test_no_db_entity_folders_exist(self, name: str):
        """No DB-entity folders exist (forbidden folders).

        **Validates: Requirements 2.7**
        """
        tmp_dir = tempfile.mkdtemp()
        try:
            workspace_path = os.path.join(tmp_dir, name)
            manager = SwarmWorkspaceManager()
            await manager.create_folder_structure(workspace_path)

            for folder in FORBIDDEN_FOLDERS:
                folder_path = os.path.join(workspace_path, folder)
                assert not os.path.exists(folder_path), (
                    f"DB-entity folder '{folder}' must NOT exist at workspace "
                    f"root level, but was found at {folder_path}. "
                    f"DB-canonical entities should not have filesystem folders."
                )
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    @given(name=workspace_name_strategy)
    @PROPERTY_SETTINGS
    @pytest.mark.asyncio
    async def test_exact_folder_set_matches_structure(self, name: str):
        """Exact folder set matches FOLDER_STRUCTURE — no more, no less.

        **Validates: Requirements 2.3, 2.7, 35.1-35.6**
        """
        tmp_dir = tempfile.mkdtemp()
        try:
            workspace_path = os.path.join(tmp_dir, name)
            manager = SwarmWorkspaceManager()
            await manager.create_folder_structure(workspace_path)

            # Collect all directories created relative to workspace root
            actual_folders = set()
            for dirpath, dirnames, _ in os.walk(workspace_path):
                for d in dirnames:
                    rel = os.path.relpath(
                        os.path.join(dirpath, d), workspace_path
                    )
                    actual_folders.add(rel)

            expected_folders = (
                set(REQUIRED_FOLDERS)
                | set(REQUIRED_KNOWLEDGE_SUBDIRS)
                # Default SwarmAI project provisioned during folder creation
                | {"Projects/SwarmAI", "Projects/SwarmAI/.artifacts"}
                # .context/ created by refresh_projects_index for PROJECTS.md
                | {".context"}
            )

            assert actual_folders == expected_folders, (
                f"Folder set mismatch.\n"
                f"  Expected: {sorted(expected_folders)}\n"
                f"  Actual:   {sorted(actual_folders)}\n"
                f"  Missing:  {sorted(expected_folders - actual_folders)}\n"
                f"  Extra:    {sorted(actual_folders - expected_folders)}"
            )
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)
