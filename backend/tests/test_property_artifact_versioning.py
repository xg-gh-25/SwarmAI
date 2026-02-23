"""Property-based tests for artifact versioning.

**Feature: workspace-refactor, Property 19: Artifact versioning**

Uses Hypothesis to verify that when an artifact is updated with new content,
the version number increments by 1, a new file is created with the versioned
filename, and the previous version's file is preserved on the filesystem.

**Validates: Requirements 27.4, 27.5**
"""
import pytest
from hypothesis import given, strategies as st, settings, HealthCheck
from pathlib import Path

from core.artifact_manager import ArtifactManager, TYPE_FOLDER_MAP
from schemas.artifact import ArtifactCreate, ArtifactUpdate, ArtifactType
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
    min_size=1,
    max_size=500,
).filter(lambda x: x.strip())

created_by_strategy = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N")),
    min_size=1,
    max_size=30,
).filter(lambda x: x.strip())

# Strategy for number of sequential updates (1 to 4)
update_count_strategy = st.integers(min_value=1, max_value=4)


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

class TestArtifactVersionIncrement:
    """Property 19: Artifact versioning — version number increments.

    *For any* artifact updated with new content, the version number in the
    database SHALL increment by exactly 1 from the previous version.

    **Validates: Requirements 27.4, 27.5**
    """

    @given(
        artifact_type=artifact_type_strategy,
        title=title_strategy,
        initial_content=content_strategy,
        updated_content=content_strategy,
        created_by=created_by_strategy,
    )
    @PROPERTY_SETTINGS
    @pytest.mark.asyncio
    async def test_update_increments_version_by_one(
        self,
        artifact_type: ArtifactType,
        title: str,
        initial_content: str,
        updated_content: str,
        created_by: str,
        tmp_path: Path,
    ):
        """Updating an artifact with new content increments version by 1.

        **Validates: Requirements 27.4, 27.5**
        """
        ws = await create_workspace_with_path(tmp_path)
        manager = ArtifactManager(workspace_manager=FakeWSManager())

        data = ArtifactCreate(
            workspace_id=ws["id"],
            title=title,
            artifact_type=artifact_type,
            file_path="Artifacts/placeholder.md",
            created_by=created_by,
        )

        created = await manager.create(data, content=initial_content)
        assert created.version == 1, "Initial version must be 1"

        update_data = ArtifactUpdate()
        updated = await manager.update(created.id, update_data, new_content=updated_content)

        assert updated is not None, "Update must return a result"
        assert updated.version == 2, (
            f"Version must increment from 1 to 2, got {updated.version}"
        )


class TestArtifactVersionNewFileCreated:
    """Property 19: Artifact versioning — new file created for new version.

    *For any* artifact updated with new content, a new file SHALL be created
    with the versioned filename format {filename}_v{NNN}.{ext}.

    **Validates: Requirements 27.4, 27.5**
    """

    @given(
        artifact_type=artifact_type_strategy,
        title=title_strategy,
        initial_content=content_strategy,
        updated_content=content_strategy,
        created_by=created_by_strategy,
    )
    @PROPERTY_SETTINGS
    @pytest.mark.asyncio
    async def test_update_creates_new_versioned_file(
        self,
        artifact_type: ArtifactType,
        title: str,
        initial_content: str,
        updated_content: str,
        created_by: str,
        tmp_path: Path,
    ):
        """Updating an artifact creates a new file with incremented version name.

        **Validates: Requirements 27.4, 27.5**
        """
        ws = await create_workspace_with_path(tmp_path)
        manager = ArtifactManager(workspace_manager=FakeWSManager())

        data = ArtifactCreate(
            workspace_id=ws["id"],
            title=title,
            artifact_type=artifact_type,
            file_path="Artifacts/placeholder.md",
            created_by=created_by,
        )

        created = await manager.create(data, content=initial_content)

        update_data = ArtifactUpdate()
        updated = await manager.update(created.id, update_data, new_content=updated_content)

        # Property: new file must exist at the updated path
        new_file_path = Path(ws["file_path"]) / updated.file_path
        assert new_file_path.exists(), (
            f"New version file must exist at {updated.file_path}"
        )

        # Property: new file must contain the updated content
        new_content_read = new_file_path.read_text(encoding="utf-8")
        assert new_content_read == updated_content, (
            "New version file must contain the updated content"
        )

        # Property: file_path must contain _v002 pattern
        assert "_v002" in updated.file_path, (
            f"Updated file_path '{updated.file_path}' must contain '_v002'"
        )


class TestArtifactVersionPreviousPreserved:
    """Property 19: Artifact versioning — previous version file preserved.

    *For any* artifact updated with new content, the previous version's file
    SHALL remain on the filesystem with its original content intact.

    **Validates: Requirements 27.4, 27.5**
    """

    @given(
        artifact_type=artifact_type_strategy,
        title=title_strategy,
        initial_content=content_strategy,
        updated_content=content_strategy,
        created_by=created_by_strategy,
    )
    @PROPERTY_SETTINGS
    @pytest.mark.asyncio
    async def test_update_preserves_previous_version_file(
        self,
        artifact_type: ArtifactType,
        title: str,
        initial_content: str,
        updated_content: str,
        created_by: str,
        tmp_path: Path,
    ):
        """Previous version file is preserved after creating a new version.

        **Validates: Requirements 27.4, 27.5**
        """
        ws = await create_workspace_with_path(tmp_path)
        manager = ArtifactManager(workspace_manager=FakeWSManager())

        data = ArtifactCreate(
            workspace_id=ws["id"],
            title=title,
            artifact_type=artifact_type,
            file_path="Artifacts/placeholder.md",
            created_by=created_by,
        )

        created = await manager.create(data, content=initial_content)
        original_file_path = Path(ws["file_path"]) / created.file_path

        # Precondition: original file exists
        assert original_file_path.exists(), "Original file must exist before update"

        update_data = ArtifactUpdate()
        await manager.update(created.id, update_data, new_content=updated_content)

        # Property: original v001 file must still exist
        assert original_file_path.exists(), (
            f"Previous version file at {created.file_path} must be preserved after update"
        )

        # Property: original file content must be unchanged
        preserved_content = original_file_path.read_text(encoding="utf-8")
        assert preserved_content == initial_content, (
            "Previous version file content must remain unchanged after update"
        )


class TestArtifactSequentialVersioning:
    """Property 19: Artifact versioning — sequential updates.

    *For any* sequence of N updates to an artifact, the final version SHALL
    be N+1, and all intermediate version files SHALL be preserved.

    **Validates: Requirements 27.4, 27.5**
    """

    @given(
        artifact_type=artifact_type_strategy,
        title=title_strategy,
        created_by=created_by_strategy,
        num_updates=update_count_strategy,
    )
    @PROPERTY_SETTINGS
    @pytest.mark.asyncio
    async def test_sequential_updates_preserve_all_versions(
        self,
        artifact_type: ArtifactType,
        title: str,
        created_by: str,
        num_updates: int,
        tmp_path: Path,
    ):
        """Sequential updates create incrementing versions, all files preserved.

        **Validates: Requirements 27.4, 27.5**
        """
        ws = await create_workspace_with_path(tmp_path)
        manager = ArtifactManager(workspace_manager=FakeWSManager())

        data = ArtifactCreate(
            workspace_id=ws["id"],
            title=title,
            artifact_type=artifact_type,
            file_path="Artifacts/placeholder.md",
            created_by=created_by,
        )

        initial_content = "version-1-content"
        created = await manager.create(data, content=initial_content)

        # Track all version file paths and their expected content
        version_files = [(Path(ws["file_path"]) / created.file_path, initial_content)]

        current = created
        for i in range(num_updates):
            version_content = f"version-{i + 2}-content"
            update_data = ArtifactUpdate()
            current = await manager.update(current.id, update_data, new_content=version_content)
            version_files.append((Path(ws["file_path"]) / current.file_path, version_content))

        # Property: final version must equal 1 + num_updates
        assert current.version == 1 + num_updates, (
            f"After {num_updates} updates, version should be {1 + num_updates}, "
            f"got {current.version}"
        )

        # Property: all version files must exist with correct content
        for idx, (file_path, expected_content) in enumerate(version_files):
            version_num = idx + 1
            assert file_path.exists(), (
                f"Version {version_num} file must exist at {file_path}"
            )
            actual_content = file_path.read_text(encoding="utf-8")
            assert actual_content == expected_content, (
                f"Version {version_num} file content mismatch"
            )
