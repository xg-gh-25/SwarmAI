"""Artifact manager for the Artifacts section of the Daily Work Operating Loop.

This module provides the ArtifactManager class for managing Artifact entities,
which represent durable knowledge outputs produced from task execution. Artifacts
use hybrid storage: content stored as files in filesystem, metadata tracked in database.

Type-to-folder mapping:
    plan → Plans/
    report → Reports/
    doc → Docs/
    decision → Decisions/
    other → Docs/

Versioning format: {filename}_v{NNN}.{ext} (e.g., project-plan_v001.md)

Requirements: 27.1-27.11
"""
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional
from uuid import uuid4

import aiosqlite
import anyio

from database import db
from schemas.artifact import (
    ArtifactCreate,
    ArtifactResponse,
    ArtifactType,
    ArtifactUpdate,
)

logger = logging.getLogger(__name__)

# Type-to-folder mapping
# Requirement 27.1: Store content under Artifacts/{type}/ folder
TYPE_FOLDER_MAP = {
    ArtifactType.PLAN: "Plans",
    ArtifactType.REPORT: "Reports",
    ArtifactType.DOC: "Docs",
    ArtifactType.DECISION: "Decisions",
    ArtifactType.OTHER: "Docs",
}


class ArtifactManager:
    """Manages Artifact entities with hybrid storage (DB metadata + filesystem content).

    Artifacts are durable knowledge outputs. Their metadata lives in the database
    while the actual content files live in the workspace filesystem under
    Artifacts/{type}/ subfolders.

    Key Features:
    - CRUD operations with hybrid storage
    - Automatic versioning: {filename}_v{NNN}.{ext}
    - Tagging support via artifact_tags table
    - Default workspace assignment to SwarmWS

    Requirements: 27.1-27.11
    """

    def __init__(self, workspace_manager=None):
        """Initialize ArtifactManager.

        Args:
            workspace_manager: Optional SwarmWorkspaceManager instance for path
                expansion. If None, imports the global instance.
        """
        self._workspace_manager = workspace_manager

    @property
    def workspace_manager(self):
        if self._workspace_manager is None:
            from core.swarm_workspace_manager import swarm_workspace_manager
            self._workspace_manager = swarm_workspace_manager
        return self._workspace_manager

    async def _get_default_workspace_id(self) -> str:
        """Get the default workspace (SwarmWS) ID."""
        workspace = await db.workspace_config.get_config()
        if not workspace:
            raise ValueError("SwarmWS workspace config not found.")
        return workspace["id"]

    def _get_type_folder(self, artifact_type: ArtifactType) -> str:
        """Get the subfolder name for an artifact type."""
        return TYPE_FOLDER_MAP.get(artifact_type, "Docs")

    async def _resolve_workspace_artifacts_dir(self, workspace_id: str, artifact_type: ArtifactType) -> Path:
        """Resolve the filesystem path for storing artifact content.

        Returns the expanded absolute path: {workspace_file_path}/Artifacts/{type_folder}/

        Raises:
            ValueError: If workspace config not found.
        """
        workspace = await db.workspace_config.get_config()
        if not workspace:
            raise ValueError(f"Workspace config not found")

        expanded = self.workspace_manager.expand_path(workspace["file_path"])
        type_folder = self._get_type_folder(artifact_type)
        return Path(expanded) / "Artifacts" / type_folder

    def _build_versioned_filename(self, title: str, version: int, ext: str = ".md") -> str:
        """Build a versioned filename from title and version.

        Format: {sanitized_title}_v{NNN}{ext}
        Requirement 27.4
        """
        # Sanitize title for filesystem use
        safe = "".join(c if c.isalnum() or c in ("-", "_", " ") else "" for c in title)
        safe = safe.strip().replace(" ", "-").lower()
        if not safe:
            safe = "artifact"
        return f"{safe}_v{version:03d}{ext}"

    async def _ensure_directory(self, dir_path: Path) -> None:
        """Ensure a directory exists, creating it if needed."""
        await anyio.to_thread.run_sync(lambda: dir_path.mkdir(parents=True, exist_ok=True))

    async def _write_file(self, file_path: Path, content: str) -> None:
        """Write content to a file."""
        await anyio.to_thread.run_sync(lambda: file_path.write_text(content, encoding="utf-8"))

    async def _read_file(self, file_path: Path) -> Optional[str]:
        """Read content from a file, returning None if not found."""
        def _read():
            if file_path.exists():
                return file_path.read_text(encoding="utf-8")
            return None
        return await anyio.to_thread.run_sync(_read)

    async def _delete_file(self, file_path: Path) -> bool:
        """Delete a file if it exists. Returns True if deleted."""
        def _del():
            if file_path.exists():
                file_path.unlink()
                return True
            return False
        return await anyio.to_thread.run_sync(_del)

    async def _save_tags(self, artifact_id: str, tags: List[str]) -> None:
        """Save tags for an artifact, replacing any existing tags.

        Note: artifact_tags table only has id, artifact_id, tag, created_at
        (no updated_at), so we use raw insert instead of the base put() method.
        """
        await db.artifact_tags.delete_by_artifact(artifact_id)
        now = datetime.now(timezone.utc).isoformat()
        for tag in tags:
            async with aiosqlite.connect(str(db.artifact_tags.db_path)) as conn:
                tag_id = str(uuid4())
                await conn.execute(
                    "INSERT INTO artifact_tags (id, artifact_id, tag, created_at) VALUES (?, ?, ?, ?)",
                    (tag_id, artifact_id, tag, now),
                )
                await conn.commit()

    async def _get_tags(self, artifact_id: str) -> List[str]:
        """Get all tags for an artifact."""
        tag_rows = await db.artifact_tags.list_by_artifact(artifact_id)
        return [row["tag"] for row in tag_rows]


    # -----------------------------------------------------------------------
    # CRUD Methods
    # -----------------------------------------------------------------------

    async def create(
        self,
        data: ArtifactCreate,
        content: str = "",
    ) -> ArtifactResponse:
        """Create a new Artifact with hybrid storage.

        Stores metadata in the database and writes content to the filesystem
        under Artifacts/{type}/ folder.

        Args:
            data: ArtifactCreate schema with artifact details.
            content: The file content to write. Defaults to empty string.

        Returns:
            ArtifactResponse with the created artifact metadata.

        Validates: Requirements 27.1, 27.2, 27.4, 27.6, 27.7
        """
        workspace_id = data.workspace_id
        if not workspace_id:
            workspace_id = await self._get_default_workspace_id()

        artifact_type = data.artifact_type or ArtifactType.OTHER
        version = data.version if data.version else 1

        # Build versioned filename and resolve directory
        artifacts_dir = await self._resolve_workspace_artifacts_dir(workspace_id, artifact_type)
        await self._ensure_directory(artifacts_dir)

        filename = self._build_versioned_filename(data.title, version)
        file_path_abs = artifacts_dir / filename

        # Write content to filesystem
        # Requirement 27.1: content stored as files in workspace filesystem
        await self._write_file(file_path_abs, content)

        # Store relative path from workspace root (Artifacts/{type}/{filename})
        type_folder = self._get_type_folder(artifact_type)
        relative_path = f"Artifacts/{type_folder}/{filename}"

        now = datetime.now(timezone.utc).isoformat()
        artifact_id = str(uuid4())

        artifact_dict = {
            "id": artifact_id,
            "workspace_id": workspace_id,
            "task_id": data.task_id,
            "artifact_type": artifact_type.value,
            "title": data.title,
            "file_path": relative_path,
            "version": version,
            "created_by": data.created_by,
            "created_at": now,
            "updated_at": now,
        }

        await db.artifacts.put(artifact_dict)
        logger.info(f"Created Artifact {artifact_id} at {relative_path}")

        # Save tags if provided
        # Requirement 27.7
        if data.tags:
            await self._save_tags(artifact_id, data.tags)

        tags = await self._get_tags(artifact_id)
        return self._dict_to_response(artifact_dict, tags)

    async def get(self, artifact_id: str) -> Optional[ArtifactResponse]:
        """Get an Artifact by ID.

        Args:
            artifact_id: The ID of the Artifact to retrieve.

        Returns:
            ArtifactResponse if found, None otherwise.

        Validates: Requirements 27.8
        """
        result = await db.artifacts.get(artifact_id)
        if not result:
            return None
        tags = await self._get_tags(artifact_id)
        return self._dict_to_response(result, tags)

    async def get_content(self, artifact_id: str) -> Optional[str]:
        """Get the file content of an Artifact.

        Reads the content from the filesystem using the stored file_path.

        Args:
            artifact_id: The ID of the Artifact.

        Returns:
            File content as string, or None if artifact or file not found.
        """
        result = await db.artifacts.get(artifact_id)
        if not result:
            return None

        workspace = await db.workspace_config.get_config()
        if not workspace:
            return None

        expanded = self.workspace_manager.expand_path(workspace["file_path"])
        file_path = Path(expanded) / result["file_path"]
        return await self._read_file(file_path)

    async def list(
        self,
        workspace_id: Optional[str] = None,
        artifact_type: Optional[ArtifactType] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> List[ArtifactResponse]:
        """List Artifacts with optional filtering.

        Args:
            workspace_id: Filter by workspace ID.
            artifact_type: Filter by artifact type.
            limit: Maximum number of results (default 50).
            offset: Number of results to skip for pagination.

        Returns:
            List of ArtifactResponse objects.

        Validates: Requirements 27.8, 27.9
        """
        if workspace_id:
            type_value = artifact_type.value if artifact_type else None
            results = await db.artifacts.list_by_workspace(workspace_id, type_value)
        else:
            results = await db.artifacts.list()
            if artifact_type:
                results = [r for r in results if r.get("artifact_type") == artifact_type.value]

        paginated = results[offset:offset + limit]

        responses = []
        for r in paginated:
            tags = await self._get_tags(r["id"])
            responses.append(self._dict_to_response(r, tags))
        return responses

    async def update(
        self,
        artifact_id: str,
        data: ArtifactUpdate,
        new_content: Optional[str] = None,
    ) -> Optional[ArtifactResponse]:
        """Update an existing Artifact, optionally creating a new version.

        When new_content is provided, a new versioned file is created while
        preserving the previous version file. The version number in the DB
        is incremented.

        Args:
            artifact_id: The ID of the Artifact to update.
            data: ArtifactUpdate schema with fields to update.
            new_content: If provided, creates a new version file with this content.

        Returns:
            Updated ArtifactResponse if found, None otherwise.

        Validates: Requirements 27.4, 27.5, 27.7
        """
        existing = await db.artifacts.get(artifact_id)
        if not existing:
            return None

        updates = {}
        if data.task_id is not None:
            updates["task_id"] = data.task_id
        if data.artifact_type is not None:
            updates["artifact_type"] = data.artifact_type.value
        if data.title is not None:
            updates["title"] = data.title

        # Handle versioning when new content is provided
        # Requirement 27.4, 27.5: increment version, create new file, preserve previous
        if new_content is not None:
            new_version = existing["version"] + 1
            updates["version"] = new_version

            # Determine artifact type (use updated type if provided, else existing)
            art_type_str = updates.get("artifact_type", existing["artifact_type"])
            art_type = ArtifactType(art_type_str)

            # Determine title (use updated title if provided, else existing)
            title = updates.get("title", existing["title"])

            artifacts_dir = await self._resolve_workspace_artifacts_dir(
                existing["workspace_id"], art_type
            )
            await self._ensure_directory(artifacts_dir)

            filename = self._build_versioned_filename(title, new_version)
            file_path_abs = artifacts_dir / filename
            await self._write_file(file_path_abs, new_content)

            type_folder = self._get_type_folder(art_type)
            updates["file_path"] = f"Artifacts/{type_folder}/{filename}"

        if not updates and data.tags is None:
            tags = await self._get_tags(artifact_id)
            return self._dict_to_response(existing, tags)

        if updates:
            result = await db.artifacts.update(artifact_id, updates)
            if not result:
                return None
        else:
            result = existing

        # Update tags if provided
        if data.tags is not None:
            await self._save_tags(artifact_id, data.tags)

        tags = await self._get_tags(artifact_id)
        logger.info(f"Updated Artifact {artifact_id}")
        return self._dict_to_response(result, tags)

    async def delete(self, artifact_id: str, delete_file: bool = False) -> bool:
        """Delete an Artifact.

        Removes the database record and optionally the filesystem content.

        Args:
            artifact_id: The ID of the Artifact to delete.
            delete_file: If True, also delete the content file from filesystem.

        Returns:
            True if deleted, False if not found.

        Validates: Requirements 27.8
        """
        existing = await db.artifacts.get(artifact_id)
        if not existing:
            return False

        # Delete tags first (cascade should handle this, but be explicit)
        await db.artifact_tags.delete_by_artifact(artifact_id)

        # Optionally delete the file
        if delete_file:
            workspace = await db.workspace_config.get_config()
            if workspace:
                expanded = self.workspace_manager.expand_path(workspace["file_path"])
                file_path = Path(expanded) / existing["file_path"]
                await self._delete_file(file_path)

        await db.artifacts.delete(artifact_id)
        logger.info(f"Deleted Artifact {artifact_id}")
        return True

    # -----------------------------------------------------------------------
    # Tag Operations
    # -----------------------------------------------------------------------

    async def add_tag(self, artifact_id: str, tag: str) -> bool:
        """Add a tag to an artifact.

        Args:
            artifact_id: The artifact ID.
            tag: The tag string to add.

        Returns:
            True if added, False if artifact not found.

        Validates: Requirements 27.7
        """
        existing = await db.artifacts.get(artifact_id)
        if not existing:
            return False

        # Check if tag already exists
        current_tags = await self._get_tags(artifact_id)
        if tag in current_tags:
            return True

        now = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(str(db.artifact_tags.db_path)) as conn:
            tag_id = str(uuid4())
            await conn.execute(
                "INSERT INTO artifact_tags (id, artifact_id, tag, created_at) VALUES (?, ?, ?, ?)",
                (tag_id, artifact_id, tag, now),
            )
            await conn.commit()
        return True

    async def remove_tag(self, artifact_id: str, tag: str) -> bool:
        """Remove a tag from an artifact.

        Args:
            artifact_id: The artifact ID.
            tag: The tag string to remove.

        Returns:
            True if removed, False if artifact or tag not found.

        Validates: Requirements 27.7
        """
        existing = await db.artifacts.get(artifact_id)
        if not existing:
            return False

        tag_rows = await db.artifact_tags.list_by_artifact(artifact_id)
        for row in tag_rows:
            if row["tag"] == tag:
                await db.artifact_tags.delete(row["id"])
                return True
        return False

    async def list_by_tag(self, tag: str, workspace_id: Optional[str] = None) -> List[ArtifactResponse]:
        """List artifacts that have a specific tag.

        Args:
            tag: The tag to search for.
            workspace_id: Optional workspace filter.

        Returns:
            List of ArtifactResponse objects matching the tag.
        """
        # Get all artifact_ids with this tag
        all_tags = await db.artifact_tags.list()
        matching_ids = {t["artifact_id"] for t in all_tags if t.get("tag") == tag}

        if not matching_ids:
            return []

        results = []
        for aid in matching_ids:
            artifact = await db.artifacts.get(aid)
            if artifact:
                if workspace_id and artifact["workspace_id"] != workspace_id:
                    continue
                tags = await self._get_tags(aid)
                results.append(self._dict_to_response(artifact, tags))
        return results

    # -----------------------------------------------------------------------
    # Helpers
    # -----------------------------------------------------------------------

    def _parse_datetime(self, value: Optional[str]) -> Optional[datetime]:
        """Parse a datetime string to datetime object."""
        if not value:
            return None
        try:
            if value.endswith("Z"):
                value = value[:-1] + "+00:00"
            dt = datetime.fromisoformat(value)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except (ValueError, TypeError):
            return None

    def _dict_to_response(self, data: dict, tags: Optional[List[str]] = None) -> ArtifactResponse:
        """Convert a database dict to ArtifactResponse."""
        return ArtifactResponse(
            id=data["id"],
            workspace_id=data["workspace_id"],
            task_id=data.get("task_id"),
            artifact_type=data["artifact_type"],
            title=data["title"],
            file_path=data["file_path"],
            version=data["version"],
            created_by=data["created_by"],
            tags=tags or [],
            created_at=self._parse_datetime(data["created_at"]) or datetime.now(timezone.utc),
            updated_at=self._parse_datetime(data["updated_at"]) or datetime.now(timezone.utc),
        )


# Global instance
artifact_manager = ArtifactManager()
