"""Swarm Workspace filesystem operations manager.

This module manages Swarm Workspace filesystem operations including:
- Folder structure creation for workspaces
- Context file creation and reading
- Path validation and security checks
- Default workspace initialization

Swarm Workspaces are user-facing project containers that provide persistent,
structured memory for organizing work by domain or project. They are distinct
from agent workspaces which handle skill isolation per-agent.

Directory structure for each workspace:
    {workspace_path}/
    ├── Artifacts/
    │   ├── Plans/
    │   ├── Reports/
    │   ├── Docs/
    │   └── Decisions/
    ├── ContextFiles/
    │   ├── context.md
    │   └── compressed-context.md
    └── Transcripts/

DB-canonical entities (Tasks, ToDos, PlanItems, Communications, ChatThreads)
are stored in the SQLite database only — no filesystem folders are created for them.
"""
import logging
import os
import shutil
from pathlib import Path
from typing import Optional

import anyio

logger = logging.getLogger(__name__)


class SwarmWorkspaceManager:
    """Manages Swarm Workspace filesystem operations."""

    # Standard folder structure for all workspaces
    # Validates: Requirements 2.3, 2.7, 35.1-35.6
    # Only filesystem-content folders; DB-canonical entities have no folders.
    FOLDER_STRUCTURE = [
        "Artifacts",
        "Artifacts/Plans",
        "Artifacts/Reports",
        "Artifacts/Docs",
        "Artifacts/Decisions",
        "ContextFiles",
        "Transcripts",
    ]

    # Default workspace configuration
    # Validates: Requirements 1.1, 1.2
    # Uses {app_data_dir} placeholder which is expanded at runtime to ~/.swarm-ai/
    DEFAULT_WORKSPACE_CONFIG = {
        "name": "SwarmWS",
        "file_path": "{app_data_dir}/SwarmWS",
        "context": "Default SwarmAI workspace for general tasks and projects.",
        "icon": "🏠",
        "is_default": True,
    }

    # Template for context.md
    # Validates: Requirements 29.1
    OVERALL_CONTEXT_TEMPLATE = """# {workspace_name} Workspace Context

## Workspace Purpose
[Describe the main purpose of this workspace]

## Key Goals
- [Goal 1]
- [Goal 2]

## Important Context
[Add any important background information]

## Notes
[Additional notes and reminders]
"""

    def __init__(self):
        """Initialize the SwarmWorkspaceManager."""
        pass

    def expand_path(self, file_path: str) -> str:
        """Expand path placeholders to actual filesystem paths.

        Handles the following expansions:
        - ~ : User home directory
        - {app_data_dir} : Platform-specific application data directory

        Args:
            file_path: Path that may contain ~ or {app_data_dir} placeholders

        Returns:
            Expanded absolute path string

        Example:
            >>> manager.expand_path("~/Desktop/SwarmAI")
            '/Users/username/Desktop/SwarmAI'
            >>> manager.expand_path("{app_data_dir}/swarm-workspaces/SwarmWS")
            '/Users/username/.swarm-ai/swarm-workspaces/SwarmWS'
        """
        from config import get_app_data_dir
        
        # Expand {app_data_dir} placeholder to platform-specific path
        if "{app_data_dir}" in file_path:
            app_data_path = str(get_app_data_dir())
            file_path = file_path.replace("{app_data_dir}", app_data_path)
        
        # Expand ~ to user home directory
        return os.path.expanduser(file_path)

    def validate_path(self, file_path: str) -> bool:
        """Validate that a file path is safe and properly formatted.

        Validates:
        - Path does not contain path traversal sequences (..)
        - Path is either absolute, starts with ~, or starts with {app_data_dir}

        Args:
            file_path: The file path to validate

        Returns:
            True if path is valid, False otherwise

        Validates: Requirements 8.1, 8.5
        """
        if not file_path:
            logger.warning("Path validation failed: empty path")
            return False

        # Check for path traversal sequences
        # Validates: Requirement 8.1
        if ".." in file_path:
            logger.warning(f"Path validation failed: path traversal detected in '{file_path}'")
            return False

        # Check if path is absolute, starts with ~, or uses {app_data_dir} placeholder
        # Validates: Requirement 8.5
        is_absolute = os.path.isabs(file_path)
        starts_with_tilde = file_path.startswith("~")
        starts_with_app_data_dir = file_path.startswith("{app_data_dir}")

        if not is_absolute and not starts_with_tilde and not starts_with_app_data_dir:
            logger.warning(
                f"Path validation failed: path must be absolute, start with ~, or use {{app_data_dir}}: '{file_path}'"
            )
            return False

        return True

    async def create_folder_structure(self, workspace_path: str) -> None:
        """Create the standard folder structure for a workspace.

        Creates the root directory if it doesn't exist, then creates all
        subdirectories defined in FOLDER_STRUCTURE. Only creates folders for
        filesystem-content storage (Artifacts/, ContextFiles/, Transcripts/).
        DB-canonical entities (Tasks, ToDos, etc.) have no filesystem folders.

        Args:
            workspace_path: Path to the workspace root directory.
                Can contain ~ for home directory expansion.

        Raises:
            ValueError: If the path is invalid (contains path traversal or
                is not absolute/tilde-prefixed).
            OSError: If folder creation fails due to filesystem permissions
                or other OS-level errors.

        Validates: Requirements 2.3, 2.7, 35.1-35.6

        Example:
            >>> await manager.create_folder_structure("~/Desktop/SwarmAI/MyWorkspace")
            # Creates:
            # ~/Desktop/SwarmAI/MyWorkspace/
            # ~/Desktop/SwarmAI/MyWorkspace/Artifacts/
            # ~/Desktop/SwarmAI/MyWorkspace/Artifacts/Plans/
            # ~/Desktop/SwarmAI/MyWorkspace/Artifacts/Reports/
            # ~/Desktop/SwarmAI/MyWorkspace/Artifacts/Docs/
            # ~/Desktop/SwarmAI/MyWorkspace/Artifacts/Decisions/
            # ~/Desktop/SwarmAI/MyWorkspace/ContextFiles/
            # ~/Desktop/SwarmAI/MyWorkspace/Transcripts/
        """
        # Validate path before proceeding
        if not self.validate_path(workspace_path):
            raise ValueError(
                f"Invalid workspace path: '{workspace_path}'. "
                "Path must be absolute or start with ~ and cannot contain '..'"
            )

        # Expand ~ to full path
        expanded_path = self.expand_path(workspace_path)
        root_path = Path(expanded_path)

        # Create root directory if it doesn't exist
        # Validates: Requirement 2.4
        try:
            await anyio.to_thread.run_sync(
                lambda: root_path.mkdir(parents=True, exist_ok=True)
            )
            logger.info(f"Created workspace root directory: {root_path}")
        except OSError as e:
            logger.error(f"Failed to create workspace root directory '{root_path}': {e}")
            raise OSError(
                f"Failed to create workspace root directory: {e}"
            ) from e

        # Create all subdirectories from FOLDER_STRUCTURE
        # Validates: Requirement 2.1
        for folder_name in self.FOLDER_STRUCTURE:
            folder_path = root_path / folder_name
            try:
                await anyio.to_thread.run_sync(
                    lambda fp=folder_path: fp.mkdir(parents=True, exist_ok=True)
                )
                logger.debug(f"Created subdirectory: {folder_path}")
            except OSError as e:
                logger.error(f"Failed to create subdirectory '{folder_path}': {e}")
                raise OSError(
                    f"Failed to create workspace folder '{folder_name}': {e}"
                ) from e

        logger.info(
            f"Successfully created folder structure for workspace at '{expanded_path}'"
        )
    async def create_context_files(self, workspace_path: str, workspace_name: str) -> None:
        """Create context files for a workspace.

        Creates the following files in the ContextFiles subdirectory:
        - context.md: Template with workspace name and placeholder sections
        - compressed-context.md: Empty file for future context compression

        This method handles errors gracefully - it logs warnings but does not
        raise exceptions, allowing workspace creation to succeed even if
        context file creation fails.

        Args:
            workspace_path: Path to the workspace root directory.
                Can contain ~ for home directory expansion.
            workspace_name: Name of the workspace to include in the template.

        Validates: Requirements 2.3, 29.1-29.10, 35.1

        Example:
            >>> await manager.create_context_files(
            ...     "~/Desktop/SwarmAI/MyWorkspace",
            ...     "MyWorkspace"
            ... )
            # Creates:
            # ~/Desktop/SwarmAI/MyWorkspace/ContextFiles/context.md
            # ~/Desktop/SwarmAI/MyWorkspace/ContextFiles/compressed-context.md
        """
        # Expand ~ to full path
        expanded_path = self.expand_path(workspace_path)
        context_dir = Path(expanded_path) / "ContextFiles"

        # Create context.md with template
        # Validates: Requirements 2.3, 29.1
        context_path = context_dir / "context.md"
        try:
            content = self.OVERALL_CONTEXT_TEMPLATE.format(workspace_name=workspace_name)
            await anyio.to_thread.run_sync(
                lambda: context_path.write_text(content, encoding="utf-8")
            )
            logger.info(f"Created context.md for workspace '{workspace_name}'")
        except Exception as e:
            logger.warning(
                f"Failed to create context.md for workspace '{workspace_name}': {e}"
            )

        # Create empty compressed-context.md
        compressed_context_path = context_dir / "compressed-context.md"
        try:
            await anyio.to_thread.run_sync(
                lambda: compressed_context_path.write_text("", encoding="utf-8")
            )
            logger.info(f"Created compressed-context.md for workspace '{workspace_name}'")
        except Exception as e:
            logger.warning(
                f"Failed to create compressed-context.md for workspace '{workspace_name}': {e}"
            )

    async def read_context_files(self, workspace_path: str) -> str:
        """Read and combine context files from a workspace.

        Reads the following files from the ContextFiles subdirectory:
        - context.md: Main workspace context template
        - compressed-context.md: Compressed context for long-term memory

        The contents are combined with a separator between them.
        Missing files are handled gracefully - if a file doesn't exist,
        its content is treated as empty.

        Args:
            workspace_path: Path to the workspace root directory.
                Can contain ~ for home directory expansion.

        Returns:
            Combined content of both context files as a single string.
            Returns empty string if both files are missing or unreadable.

        Validates: Requirement 14.2

        Example:
            >>> content = await manager.read_context_files("~/Desktop/SwarmAI/MyWorkspace")
            >>> print(content)
            # MyWorkspace Workspace Context
            # ...
        """
        # Expand ~ to full path
        expanded_path = self.expand_path(workspace_path)
        context_dir = Path(expanded_path) / "ContextFiles"

        contents = []

        # Read context.md
        context_path = context_dir / "context.md"
        try:
            context_content = await anyio.to_thread.run_sync(
                lambda: context_path.read_text(encoding="utf-8")
                if context_path.exists()
                else ""
            )
            if context_content:
                contents.append(context_content)
        except Exception as e:
            logger.warning(f"Failed to read context.md: {e}")

        # Read compressed-context.md
        compressed_context_path = context_dir / "compressed-context.md"
        try:
            compressed_content = await anyio.to_thread.run_sync(
                lambda: compressed_context_path.read_text(encoding="utf-8")
                if compressed_context_path.exists()
                else ""
            )
            if compressed_content:
                contents.append(compressed_content)
        except Exception as e:
            logger.warning(f"Failed to read compressed-context.md: {e}")

        # Combine contents with separator
        return "\n\n---\n\n".join(contents) if contents else ""



    async def _migrate_default_workspace_path(self, workspace: dict, db) -> None:
        """Migrate default workspace from old nested path to new flat path.

        Handles the path change from {app_data_dir}/swarm-workspaces/SwarmWS
        to {app_data_dir}/SwarmWS. Moves filesystem contents when possible
        and updates the database record.

        Migration scenarios:
        - Old exists, new does not: Move old → new via shutil.move()
        - Both exist: Keep new path, log warning, leave old for manual cleanup
        - Old does not exist: Just update DB record (new may or may not exist)

        Args:
            workspace: The workspace dict from the database with the old file_path.
            db: Database instance with swarm_workspaces table accessor.

        Validates: Requirements 2.3, 2.4, 2.5
        """
        old_expanded = self.expand_path(workspace["file_path"])
        new_file_path = self.DEFAULT_WORKSPACE_CONFIG["file_path"]
        new_expanded = self.expand_path(new_file_path)

        old_exists = Path(old_expanded).exists()
        new_exists = Path(new_expanded).exists()

        if old_exists and not new_exists:
            try:
                shutil.move(old_expanded, new_expanded)
                logger.info(f"Migrated workspace from {old_expanded} to {new_expanded}")
            except OSError as e:
                logger.error(f"Failed to migrate workspace from {old_expanded} to {new_expanded}: {e}")
                # Continue with DB update — the old path still works
        elif old_exists and new_exists:
            logger.warning(
                f"Both old ({old_expanded}) and new ({new_expanded}) paths exist. "
                "Keeping new path. Old path left for manual cleanup."
            )
        # else: old doesn't exist, new may or may not — just update DB

        # Update database record
        workspace["file_path"] = new_file_path
        await db.swarm_workspaces.put(workspace)
        logger.info(f"Updated default workspace DB record to {new_file_path}")

    async def ensure_default_workspace(self, db) -> dict:
        """Ensure the default workspace exists, creating it if necessary.

        This method checks if the default workspace exists in the database.
        If not, it creates the default workspace with:
        - Standard folder structure
        - Context files with templates
        - Database entry with is_default=True

        Args:
            db: Database instance with swarm_workspaces table accessor.
                Must have db.swarm_workspaces.get_default() and
                db.swarm_workspaces.put() methods.

        Returns:
            dict: The default workspace dictionary with all fields including
                id, name, file_path, context, icon, is_default, created_at,
                and updated_at.

        Validates: Requirements 1.1, 1.2, 1.5

        Example:
            >>> from database.sqlite import SQLiteDatabase
            >>> db = SQLiteDatabase()
            >>> await db.initialize()
            >>> workspace = await manager.ensure_default_workspace(db)
            >>> print(workspace["name"])
            'SwarmWS'
        """
        import uuid
        from datetime import datetime, timezone

        # Check if default workspace already exists
        existing_default = await db.swarm_workspaces.get_default()
        if existing_default:
            # Check if migration needed (old path → new path)
            # Validates: Requirements 2.2, 2.3
            old_path_pattern = "{app_data_dir}/swarm-workspaces/SwarmWS"
            if existing_default["file_path"] == old_path_pattern:
                await self._migrate_default_workspace_path(existing_default, db)
                return await db.swarm_workspaces.get_default()
            logger.info("Default workspace already exists")
            return existing_default

        logger.info("Creating default workspace")

        # Generate workspace data from config
        # Validates: Requirements 1.1, 1.2
        now = datetime.now(timezone.utc).isoformat()
        workspace_data = {
            "id": str(uuid.uuid4()),
            "name": self.DEFAULT_WORKSPACE_CONFIG["name"],
            "file_path": self.DEFAULT_WORKSPACE_CONFIG["file_path"],
            "context": self.DEFAULT_WORKSPACE_CONFIG["context"],
            "icon": self.DEFAULT_WORKSPACE_CONFIG["icon"],
            "is_default": True,
            "created_at": now,
            "updated_at": now,
        }

        # Create folder structure
        try:
            await self.create_folder_structure(workspace_data["file_path"])
            logger.info(f"Created folder structure for default workspace at {workspace_data['file_path']}")
        except Exception as e:
            logger.error(f"Failed to create folder structure for default workspace: {e}")
            raise

        # Create context files
        try:
            await self.create_context_files(
                workspace_data["file_path"],
                workspace_data["name"]
            )
            logger.info("Created context files for default workspace")
        except Exception as e:
            # Log warning but don't fail - context file creation is non-critical
            # Validates: Requirement 7.4
            logger.warning(f"Failed to create context files for default workspace: {e}")

        # Store in database
        # Validates: Requirement 1.5 (persistence)
        try:
            stored_workspace = await db.swarm_workspaces.put(workspace_data)
            logger.info(f"Stored default workspace in database with id: {stored_workspace['id']}")
            return stored_workspace
        except Exception as e:
            logger.error(f"Failed to store default workspace in database: {e}")
            raise

    async def archive(self, workspace_id: str, db) -> dict:
        """Archive a workspace, making it read-only and hidden from default lists.

        Sets is_archived=1 and archived_at to the current UTC timestamp.
        The default workspace (SwarmWS) cannot be archived.

        Args:
            workspace_id: The ID of the workspace to archive.
            db: Database instance with swarm_workspaces table accessor.

        Returns:
            dict: The updated workspace dictionary.

        Raises:
            ValueError: If workspace_id is not found.
            PermissionError: If the workspace is the default workspace (SwarmWS).

        Validates: Requirements 36.1, 36.2
        """
        from datetime import datetime, timezone

        workspace = await db.swarm_workspaces.get(workspace_id)
        if not workspace:
            raise ValueError(f"Workspace not found: {workspace_id}")

        if workspace.get("is_default"):
            raise PermissionError("Cannot archive the default workspace (SwarmWS)")

        now = datetime.now(timezone.utc).isoformat()
        updated = await db.swarm_workspaces.update(workspace_id, {
            "is_archived": 1,
            "archived_at": now,
        })
        logger.info(f"Archived workspace '{workspace_id}'")
        return updated

    async def unarchive(self, workspace_id: str, db) -> dict:
        """Unarchive a workspace, restoring full functionality.

        Sets is_archived=0 and archived_at to None.

        Args:
            workspace_id: The ID of the workspace to unarchive.
            db: Database instance with swarm_workspaces table accessor.

        Returns:
            dict: The updated workspace dictionary.

        Raises:
            ValueError: If workspace_id is not found.

        Validates: Requirements 36.10
        """
        workspace = await db.swarm_workspaces.get(workspace_id)
        if not workspace:
            raise ValueError(f"Workspace not found: {workspace_id}")

        updated = await db.swarm_workspaces.update(workspace_id, {
            "is_archived": 0,
            "archived_at": None,
        })
        logger.info(f"Unarchived workspace '{workspace_id}'")
        return updated

    async def delete(self, workspace_id: str, db) -> bool:
        """Delete a workspace permanently.

        The default workspace (SwarmWS) cannot be deleted.

        Args:
            workspace_id: The ID of the workspace to delete.
            db: Database instance with swarm_workspaces table accessor.

        Returns:
            bool: True on successful deletion.

        Raises:
            ValueError: If workspace_id is not found.
            PermissionError: If the workspace is the default workspace (SwarmWS).

        Validates: Requirements 1.2, 2.5
        """
        workspace = await db.swarm_workspaces.get(workspace_id)
        if not workspace:
            raise ValueError(f"Workspace not found: {workspace_id}")

        if workspace.get("is_default"):
            raise PermissionError("Cannot delete the default workspace (SwarmWS)")

        await db.swarm_workspaces.delete(workspace_id)
        logger.info(f"Deleted workspace '{workspace_id}'")
        return True


    async def list_non_archived(self, db) -> list:
        """List all non-archived workspaces, with the default workspace first.

        Returns workspaces where is_archived=0 (or NULL for backward compat),
        sorted with is_default DESC then created_at DESC so SwarmWS is always first.

        Args:
            db: Database instance with swarm_workspaces table accessor.

        Returns:
            list[dict]: List of non-archived workspace dictionaries.

        Validates: Requirements 36.3, 36.5
        """
        all_workspaces = await db.swarm_workspaces.list()
        non_archived = [
            ws for ws in all_workspaces
            if not ws.get("is_archived")
        ]
        # Sort: default workspace first, then by created_at descending
        non_archived.sort(
            key=lambda ws: (not ws.get("is_default", False), ws.get("created_at", "")),
        )
        return non_archived

    async def list_all(self, db, include_archived: bool = False) -> list:
        """List all workspaces with optional archived filtering.

        Returns workspaces sorted with the default workspace first,
        then by created_at descending.

        Args:
            db: Database instance with swarm_workspaces table accessor.
            include_archived: If True, include archived workspaces. Defaults to False.

        Returns:
            list[dict]: List of workspace dictionaries.

        Validates: Requirements 1.1, 36.3
        """
        all_workspaces = await db.swarm_workspaces.list()

        if not include_archived:
            all_workspaces = [
                ws for ws in all_workspaces
                if not ws.get("is_archived")
            ]

        # Sort: default workspace first, then by created_at descending
        all_workspaces.sort(
            key=lambda ws: (not ws.get("is_default", False), ws.get("created_at", "")),
        )
        return all_workspaces


    async def ensure_workspace_folders_exist(self, db) -> None:
        """Ensure the default workspace filesystem folders exist.

        Called after database initialization to create folders for
        pre-seeded workspace records that don't have filesystem folders yet.
        This is particularly important when using a pre-seeded database where
        the workspace record exists but the filesystem folders haven't been
        created yet.

        The method:
        1. Retrieves the default workspace from the database
        2. Expands the {app_data_dir} placeholder to the platform-specific path
        3. Checks if the filesystem folders exist at the expanded path
        4. Creates folders and context files if missing

        Args:
            db: Database instance with swarm_workspaces table accessor.
                Must have db.swarm_workspaces.get_default() method.

        Validates: Requirements 4.2, 4.3, 4.4

        Example:
            >>> from database.sqlite import SQLiteDatabase
            >>> db = SQLiteDatabase()
            >>> await db.initialize()
            >>> await manager.ensure_workspace_folders_exist(db)
        """
        # Get the default workspace from database
        default_workspace = await db.swarm_workspaces.get_default()
        if not default_workspace:
            logger.debug("No default workspace found in database, skipping folder creation")
            return

        # Expand {app_data_dir} placeholder to platform-specific path
        # Validates: Requirement 4.2
        workspace_path = self.expand_path(default_workspace["file_path"])
        
        # Check if filesystem folders already exist
        if Path(workspace_path).exists():
            logger.debug(f"Workspace folders already exist at {workspace_path}")
            return

        logger.info(f"Creating workspace folders at {workspace_path}")

        # Create folder structure
        # Validates: Requirement 4.3
        try:
            await self.create_folder_structure(default_workspace["file_path"])
            logger.info(f"Created folder structure for workspace at {workspace_path}")
        except Exception as e:
            logger.warning(f"Failed to create workspace folder structure: {e}")
            return

        # Create context files
        # Validates: Requirement 4.4
        try:
            await self.create_context_files(
                default_workspace["file_path"],
                default_workspace["name"]
            )
            logger.info(f"Created context files for workspace '{default_workspace['name']}'")
        except Exception as e:
            # Log warning but don't fail - context file creation is non-critical
            logger.warning(f"Failed to create context files for workspace: {e}")


# Global instance
swarm_workspace_manager = SwarmWorkspaceManager()
