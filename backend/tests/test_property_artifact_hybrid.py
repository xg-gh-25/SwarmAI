"""Property-based tests for artifact hybrid storage.

**Feature: workspace-refactor, Property 18: Artifact hybrid storage**

Uses Hypothesis to verify that when an artifact is created, the system stores
metadata in the database and content in the filesystem. Also verifies that
deleting metadata without delete_file=True preserves the filesystem content.

**Validates: Requirements 27.1-27.11**
"""
import pytest
from hypothesis import given, strategies as st, settings, HealthCheck
from pathlib import Path

from core.artifact_manager import ArtifactManager, TYPE_FOLDER_MAP
from schemas.artifact import ArtifactCreate, ArtifactType
from database import db
from tests.helpers import create_workspace_with_path


PROPERTY_SETTINGS = settings(
    max_examples=2,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)

# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

artifact_type_strategy = st.sampled_from(list(ArtifactType))

title_strategy = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N")),
    min_size=1,
    max_size=60,
).filter(lambda x: x.strip())

content_strategy = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N", "P", "Z")),
    min_size=0,
    max_size=2000,
)

created_by_strategy = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N")),
    min_size=1,
    max_size=30,
).filter(lambda x: x.strip())

tag_strategy = st.lists(
    st.text(
        alphabet=st.characters(whitelist_categories=("L", "N")),
        min_size=1,
        max_size=20,
    ).filter(lambda x: x.strip()),
    min_size=0,
    max_size=5,
    unique=True,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class FakeWSManager:
    """Workspace manager that returns paths as-is (already absolute in tests)."""
    def expand_path(self, file_path: str):
        return file_path


# ---------------------------------------------------------------------------
# Property Tests
# ---------------------------------------------------------------------------

class TestArtifactHybridStorageMetadata:
    """Property 18: Artifact hybrid storage — metadata in database.

    *For any* artifact created, the database SHALL contain a record with
    matching id, workspace_id, title, artifact_type, version, and created_by.

    **Validates: Requirements 27.2, 27.3, 27.6**
    """

    @given(
        artifact_type=artifact_type_strategy,
        title=title_strategy,
        content=content_strategy,
        created_by=created_by_strategy,
        tags=tag_strategy,
    )
    @PROPERTY_SETTINGS
    @pytest.mark.asyncio
    async def test_create_stores_metadata_in_database(
        self,
        artifact_type: ArtifactType,
        title: str,
        content: str,
        created_by: str,
        tags: list,
        tmp_path: Path,
    ):
        """After creating an artifact, metadata exists in the database.

        **Validates: Requirements 27.2, 27.3, 27.6**
        """
        ws = await create_workspace_with_path(tmp_path)
        manager = ArtifactManager(workspace_manager=FakeWSManager())

        data = ArtifactCreate(
            workspace_id=ws["id"],
            title=title,
            artifact_type=artifact_type,
            file_path=f"Artifacts/placeholder.md",
            created_by=created_by,
            tags=tags if tags else None,
        )

        result = await manager.create(data, content=content)

        # Property: DB record must exist
        db_record = await db.artifacts.get(result.id)
        assert db_record is not None, (
            f"Database record must exist after creating artifact '{title}'"
        )

        # Property: metadata fields must match
        assert db_record["workspace_id"] == ws["id"]
        assert db_record["title"] == title
        assert db_record["artifact_type"] == artifact_type.value
        assert db_record["version"] == 1
        assert db_record["created_by"] == created_by

        # Property: tags must be stored
        if tags:
            stored_tags = await db.artifact_tags.list_by_artifact(result.id)
            stored_tag_names = sorted([t["tag"] for t in stored_tags])
            assert stored_tag_names == sorted(tags), (
                f"Tags mismatch: expected {sorted(tags)}, got {stored_tag_names}"
            )


class TestArtifactHybridStorageFilesystem:
    """Property 18: Artifact hybrid storage — content in filesystem.

    *For any* artifact created, the filesystem SHALL contain a file at the
    expected path with content matching what was provided.

    **Validates: Requirements 27.1, 27.4**
    """

    @given(
        artifact_type=artifact_type_strategy,
        title=title_strategy,
        content=content_strategy,
        created_by=created_by_strategy,
    )
    @PROPERTY_SETTINGS
    @pytest.mark.asyncio
    async def test_create_writes_content_to_filesystem(
        self,
        artifact_type: ArtifactType,
        title: str,
        content: str,
        created_by: str,
        tmp_path: Path,
    ):
        """After creating an artifact, content exists in the filesystem.

        **Validates: Requirements 27.1**
        """
        ws = await create_workspace_with_path(tmp_path)
        manager = ArtifactManager(workspace_manager=FakeWSManager())

        data = ArtifactCreate(
            workspace_id=ws["id"],
            title=title,
            artifact_type=artifact_type,
            file_path=f"Artifacts/placeholder.md",
            created_by=created_by,
        )

        result = await manager.create(data, content=content)

        # Property: file must exist at the stored path
        file_path = Path(ws["file_path"]) / result.file_path
        assert file_path.exists(), (
            f"Content file must exist at {result.file_path} after creation"
        )

        # Property: file content must match what was provided
        stored_content = file_path.read_text(encoding="utf-8")
        assert stored_content == content, (
            f"File content mismatch for artifact '{title}'"
        )

    @given(
        artifact_type=artifact_type_strategy,
        title=title_strategy,
        content=content_strategy,
        created_by=created_by_strategy,
    )
    @PROPERTY_SETTINGS
    @pytest.mark.asyncio
    async def test_content_stored_in_correct_type_folder(
        self,
        artifact_type: ArtifactType,
        title: str,
        content: str,
        created_by: str,
        tmp_path: Path,
    ):
        """Content is stored under the correct Artifacts/{type}/ subfolder.

        **Validates: Requirements 27.1, 27.3**
        """
        ws = await create_workspace_with_path(tmp_path)
        manager = ArtifactManager(workspace_manager=FakeWSManager())

        data = ArtifactCreate(
            workspace_id=ws["id"],
            title=title,
            artifact_type=artifact_type,
            file_path=f"Artifacts/placeholder.md",
            created_by=created_by,
        )

        result = await manager.create(data, content=content)

        # Property: file_path must contain the correct type folder
        expected_folder = TYPE_FOLDER_MAP[artifact_type]
        assert f"Artifacts/{expected_folder}/" in result.file_path, (
            f"file_path '{result.file_path}' should contain 'Artifacts/{expected_folder}/'"
        )


class TestArtifactHybridStorageRoundTrip:
    """Property 18: Artifact hybrid storage — content round-trip.

    *For any* artifact created, reading content back via get_content
    SHALL return exactly what was written.

    **Validates: Requirements 27.1, 27.2, 27.8**
    """

    @given(
        artifact_type=artifact_type_strategy,
        title=title_strategy,
        content=content_strategy,
        created_by=created_by_strategy,
    )
    @PROPERTY_SETTINGS
    @pytest.mark.asyncio
    async def test_get_content_returns_written_content(
        self,
        artifact_type: ArtifactType,
        title: str,
        content: str,
        created_by: str,
        tmp_path: Path,
    ):
        """Reading content via get_content returns exactly what was written.

        **Validates: Requirements 27.1, 27.8**
        """
        ws = await create_workspace_with_path(tmp_path)
        manager = ArtifactManager(workspace_manager=FakeWSManager())

        data = ArtifactCreate(
            workspace_id=ws["id"],
            title=title,
            artifact_type=artifact_type,
            file_path=f"Artifacts/placeholder.md",
            created_by=created_by,
        )

        result = await manager.create(data, content=content)

        # Property: round-trip must preserve content
        retrieved_content = await manager.get_content(result.id)
        assert retrieved_content == content, (
            f"get_content should return exactly what was written for '{title}'"
        )


class TestArtifactDeletePreservesFile:
    """Property 18: Artifact hybrid storage — delete metadata preserves file.

    *For any* artifact, deleting metadata without delete_file=True SHALL
    preserve the content file on the filesystem.

    **Validates: Requirements 27.8**
    """

    @given(
        artifact_type=artifact_type_strategy,
        title=title_strategy,
        content=st.text(
            alphabet=st.characters(whitelist_categories=("L", "N", "P", "Z")),
            min_size=1,
            max_size=500,
        ).filter(lambda x: x.strip()),
        created_by=created_by_strategy,
    )
    @PROPERTY_SETTINGS
    @pytest.mark.asyncio
    async def test_delete_metadata_preserves_filesystem_content(
        self,
        artifact_type: ArtifactType,
        title: str,
        content: str,
        created_by: str,
        tmp_path: Path,
    ):
        """Deleting metadata without delete_file=True preserves the file.

        **Validates: Requirements 27.8**
        """
        ws = await create_workspace_with_path(tmp_path)
        manager = ArtifactManager(workspace_manager=FakeWSManager())

        data = ArtifactCreate(
            workspace_id=ws["id"],
            title=title,
            artifact_type=artifact_type,
            file_path=f"Artifacts/placeholder.md",
            created_by=created_by,
        )

        result = await manager.create(data, content=content)
        file_path = Path(ws["file_path"]) / result.file_path

        # Precondition: file exists
        assert file_path.exists()

        # Delete metadata only (default: delete_file=False)
        deleted = await manager.delete(result.id, delete_file=False)
        assert deleted is True

        # Property: DB record must be gone
        db_record = await db.artifacts.get(result.id)
        assert db_record is None, (
            "Database record should be removed after delete"
        )

        # Property: file must still exist on filesystem
        assert file_path.exists(), (
            f"File at {file_path} should be preserved when delete_file=False"
        )

        # Property: file content must still match
        stored_content = file_path.read_text(encoding="utf-8")
        assert stored_content == content, (
            "File content should be unchanged after metadata-only delete"
        )
