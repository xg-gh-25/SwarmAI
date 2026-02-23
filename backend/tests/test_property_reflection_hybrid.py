"""Property-based tests for reflection hybrid storage.

**Feature: workspace-refactor, Property 20: Reflection hybrid storage**

Uses Hypothesis to verify that when a reflection is created, the system stores
metadata in the database and content as a markdown file in the filesystem under
Artifacts/Reports/ with naming convention {reflection_type}_{date}.md. Also
verifies that deleting metadata without delete_file=True preserves the file.

**Validates: Requirements 28.1-28.11**
"""
import pytest
from hypothesis import given, strategies as st, settings, HealthCheck
from datetime import datetime, timezone, timedelta
from pathlib import Path

from core.reflection_manager import ReflectionManager, REFLECTIONS_SUBFOLDER
from schemas.reflection import ReflectionCreate, ReflectionType, GeneratedBy
from database import db
from tests.helpers import create_workspace_with_path


PROPERTY_SETTINGS = settings(
    max_examples=2,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)

# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

reflection_type_strategy = st.sampled_from(list(ReflectionType))

generated_by_strategy = st.sampled_from(list(GeneratedBy))

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

# Generate dates within a reasonable range for period_start
period_start_strategy = st.datetimes(
    min_value=datetime(2024, 1, 1),
    max_value=datetime(2026, 12, 31),
    timezones=st.just(timezone.utc),
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

class TestReflectionHybridStorageMetadata:
    """Property 20: Reflection hybrid storage — metadata in database.

    *For any* reflection created, the database SHALL contain a record with
    matching id, workspace_id, title, reflection_type, generated_by,
    period_start, and period_end.

    **Validates: Requirements 28.2, 28.3, 28.4**
    """

    @given(
        reflection_type=reflection_type_strategy,
        title=title_strategy,
        content=content_strategy,
        generated_by=generated_by_strategy,
        period_start=period_start_strategy,
    )
    @PROPERTY_SETTINGS
    @pytest.mark.asyncio
    async def test_create_stores_metadata_in_database(
        self,
        reflection_type: ReflectionType,
        title: str,
        content: str,
        generated_by: GeneratedBy,
        period_start: datetime,
        tmp_path: Path,
    ):
        """After creating a reflection, metadata exists in the database.

        **Validates: Requirements 28.2, 28.3, 28.4**
        """
        ws = await create_workspace_with_path(tmp_path)
        manager = ReflectionManager(workspace_manager=FakeWSManager())

        period_end = period_start + timedelta(days=1)

        data = ReflectionCreate(
            workspace_id=ws["id"],
            reflection_type=reflection_type,
            title=title,
            file_path="Artifacts/Reports/placeholder.md",
            period_start=period_start,
            period_end=period_end,
            generated_by=generated_by,
        )

        result = await manager.create(data, content=content)

        # Property: DB record must exist
        db_record = await db.reflections.get(result.id)
        assert db_record is not None, (
            f"Database record must exist after creating reflection '{title}'"
        )

        # Property: metadata fields must match
        assert db_record["workspace_id"] == ws["id"]
        assert db_record["title"] == title
        assert db_record["reflection_type"] == reflection_type.value
        assert db_record["generated_by"] == generated_by.value
        assert db_record["period_start"] == period_start.isoformat()
        assert db_record["period_end"] == period_end.isoformat()


class TestReflectionHybridStorageFilesystem:
    """Property 20: Reflection hybrid storage — content in filesystem.

    *For any* reflection created, the filesystem SHALL contain a markdown file
    at the expected path under Artifacts/Reports/ with content matching what
    was provided, using naming convention {reflection_type}_{date}.md.

    **Validates: Requirements 28.1**
    """

    @given(
        reflection_type=reflection_type_strategy,
        title=title_strategy,
        content=content_strategy,
        generated_by=generated_by_strategy,
        period_start=period_start_strategy,
    )
    @PROPERTY_SETTINGS
    @pytest.mark.asyncio
    async def test_create_writes_content_to_filesystem(
        self,
        reflection_type: ReflectionType,
        title: str,
        content: str,
        generated_by: GeneratedBy,
        period_start: datetime,
        tmp_path: Path,
    ):
        """After creating a reflection, content exists in the filesystem.

        **Validates: Requirements 28.1**
        """
        ws = await create_workspace_with_path(tmp_path)
        manager = ReflectionManager(workspace_manager=FakeWSManager())

        period_end = period_start + timedelta(days=1)

        data = ReflectionCreate(
            workspace_id=ws["id"],
            reflection_type=reflection_type,
            title=title,
            file_path="Artifacts/Reports/placeholder.md",
            period_start=period_start,
            period_end=period_end,
            generated_by=generated_by,
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
            f"File content mismatch for reflection '{title}'"
        )

    @given(
        reflection_type=reflection_type_strategy,
        title=title_strategy,
        content=content_strategy,
        generated_by=generated_by_strategy,
        period_start=period_start_strategy,
    )
    @PROPERTY_SETTINGS
    @pytest.mark.asyncio
    async def test_content_stored_in_reports_folder_with_correct_naming(
        self,
        reflection_type: ReflectionType,
        title: str,
        content: str,
        generated_by: GeneratedBy,
        period_start: datetime,
        tmp_path: Path,
    ):
        """Content is stored under Artifacts/Reports/ with {type}_{date}.md naming.

        **Validates: Requirements 28.1**
        """
        ws = await create_workspace_with_path(tmp_path)
        manager = ReflectionManager(workspace_manager=FakeWSManager())

        period_end = period_start + timedelta(days=1)

        data = ReflectionCreate(
            workspace_id=ws["id"],
            reflection_type=reflection_type,
            title=title,
            file_path="Artifacts/Reports/placeholder.md",
            period_start=period_start,
            period_end=period_end,
            generated_by=generated_by,
        )

        result = await manager.create(data, content=content)

        # Property: file_path must be under Artifacts/Reports/
        assert result.file_path.startswith(f"{REFLECTIONS_SUBFOLDER}/"), (
            f"file_path '{result.file_path}' should start with '{REFLECTIONS_SUBFOLDER}/'"
        )

        # Property: filename must follow {reflection_type}_{date}.md convention
        date_str = period_start.strftime("%Y-%m-%d")
        expected_filename = f"{reflection_type.value}_{date_str}.md"
        actual_filename = Path(result.file_path).name
        assert actual_filename == expected_filename, (
            f"Filename '{actual_filename}' should be '{expected_filename}'"
        )


class TestReflectionHybridStorageRoundTrip:
    """Property 20: Reflection hybrid storage — content round-trip.

    *For any* reflection created, reading content back via get_content
    SHALL return exactly what was written.

    **Validates: Requirements 28.1, 28.2, 28.9**
    """

    @given(
        reflection_type=reflection_type_strategy,
        title=title_strategy,
        content=content_strategy,
        generated_by=generated_by_strategy,
        period_start=period_start_strategy,
    )
    @PROPERTY_SETTINGS
    @pytest.mark.asyncio
    async def test_get_content_returns_written_content(
        self,
        reflection_type: ReflectionType,
        title: str,
        content: str,
        generated_by: GeneratedBy,
        period_start: datetime,
        tmp_path: Path,
    ):
        """Reading content via get_content returns exactly what was written.

        **Validates: Requirements 28.1, 28.9**
        """
        ws = await create_workspace_with_path(tmp_path)
        manager = ReflectionManager(workspace_manager=FakeWSManager())

        period_end = period_start + timedelta(days=1)

        data = ReflectionCreate(
            workspace_id=ws["id"],
            reflection_type=reflection_type,
            title=title,
            file_path="Artifacts/Reports/placeholder.md",
            period_start=period_start,
            period_end=period_end,
            generated_by=generated_by,
        )

        result = await manager.create(data, content=content)

        # Property: round-trip must preserve content
        retrieved_content = await manager.get_content(result.id)
        assert retrieved_content == content, (
            f"get_content should return exactly what was written for '{title}'"
        )


class TestReflectionDeletePreservesFile:
    """Property 20: Reflection hybrid storage — delete metadata preserves file.

    *For any* reflection, deleting metadata without delete_file=True SHALL
    preserve the content file on the filesystem.

    **Validates: Requirements 28.9**
    """

    @given(
        reflection_type=reflection_type_strategy,
        title=title_strategy,
        content=st.text(
            alphabet=st.characters(whitelist_categories=("L", "N", "P", "Z")),
            min_size=1,
            max_size=500,
        ).filter(lambda x: x.strip()),
        generated_by=generated_by_strategy,
        period_start=period_start_strategy,
    )
    @PROPERTY_SETTINGS
    @pytest.mark.asyncio
    async def test_delete_metadata_preserves_filesystem_content(
        self,
        reflection_type: ReflectionType,
        title: str,
        content: str,
        generated_by: GeneratedBy,
        period_start: datetime,
        tmp_path: Path,
    ):
        """Deleting metadata without delete_file=True preserves the file.

        **Validates: Requirements 28.9**
        """
        ws = await create_workspace_with_path(tmp_path)
        manager = ReflectionManager(workspace_manager=FakeWSManager())

        period_end = period_start + timedelta(days=1)

        data = ReflectionCreate(
            workspace_id=ws["id"],
            reflection_type=reflection_type,
            title=title,
            file_path="Artifacts/Reports/placeholder.md",
            period_start=period_start,
            period_end=period_end,
            generated_by=generated_by,
        )

        result = await manager.create(data, content=content)
        file_path = Path(ws["file_path"]) / result.file_path

        # Precondition: file exists
        assert file_path.exists()

        # Delete metadata only (default: delete_file=False)
        deleted = await manager.delete(result.id, delete_file=False)
        assert deleted is True

        # Property: DB record must be gone
        db_record = await db.reflections.get(result.id)
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
