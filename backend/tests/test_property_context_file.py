"""Property-based tests for context file creation.

**Feature: workspace-refactor, Property 21: Context file creation**

Uses Hypothesis to verify that when a workspace is created, the system
creates ContextFiles/context.md and that it is readable and contains valid
content. Also verifies compressed-context.md is created when compression
is triggered.

**Validates: Requirements 29.1-29.10**
"""
import os
import pytest
from hypothesis import given, strategies as st, settings, HealthCheck
from pathlib import Path

from core.context_manager import context_manager, estimate_tokens, DEFAULT_TOKEN_BUDGET
from tests.helpers import create_workspace_with_path


PROPERTY_SETTINGS = settings(
    max_examples=2,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)

# Strategy for workspace names: printable, non-empty, reasonable length
workspace_name_strategy = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N")),
    min_size=1,
    max_size=50,
).filter(lambda x: x.strip())

# Strategy for context content: non-empty markdown-like text
context_content_strategy = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N", "P", "Z")),
    min_size=1,
    max_size=2000,
).filter(lambda x: x.strip())


async def _create_workspace_with_context_dir(tmp_path: Path, name: str) -> dict:
    """Create a workspace record with ContextFiles/ directory on disk."""
    ws = await create_workspace_with_path(tmp_path, name=name)
    # Create ContextFiles/ directory (new refactored structure)
    context_dir = Path(ws["file_path"]) / "ContextFiles"
    context_dir.mkdir(parents=True, exist_ok=True)
    return ws


class TestContextFileCreation:
    """Property 21: Context file creation.

    *For any* workspace created, the filesystem SHALL contain
    ContextFiles/context.md after context is written. The content
    SHALL be readable and match what was written.

    **Validates: Requirements 29.1, 29.3, 29.5, 29.9**
    """

    @given(
        name=workspace_name_strategy,
        content=context_content_strategy,
    )
    @PROPERTY_SETTINGS
    @pytest.mark.asyncio
    async def test_update_context_creates_context_md(
        self,
        name: str,
        content: str,
        tmp_path: Path,
    ):
        """Writing context creates ContextFiles/context.md with correct content.

        **Validates: Requirements 29.1, 29.3, 29.9**
        """
        ws = await _create_workspace_with_context_dir(tmp_path, name)

        # Write context via ContextManager
        await context_manager.update_context(ws["id"], content)

        # Property: ContextFiles/context.md must exist
        context_file = Path(ws["file_path"]) / "ContextFiles" / "context.md"
        assert context_file.exists(), (
            f"ContextFiles/context.md should exist after update_context for workspace '{name}'"
        )

        # Property: content must be readable and match what was written
        stored_content = context_file.read_text(encoding="utf-8")
        assert stored_content == content, (
            f"context.md content mismatch for workspace '{name}'"
        )

    @given(
        name=workspace_name_strategy,
        content=context_content_strategy,
    )
    @PROPERTY_SETTINGS
    @pytest.mark.asyncio
    async def test_get_context_returns_written_content(
        self,
        name: str,
        content: str,
        tmp_path: Path,
    ):
        """Reading context via API returns exactly what was written.

        **Validates: Requirements 29.1, 29.9**
        """
        ws = await _create_workspace_with_context_dir(tmp_path, name)

        # Write context
        await context_manager.update_context(ws["id"], content)

        # Read back via get_context
        result = await context_manager.get_context(ws["id"])

        # Property: round-trip must preserve content
        assert result == content, (
            f"get_context should return exactly what was written for workspace '{name}'"
        )


class TestCompressedContextCreation:
    """Property 21: Compressed context file creation.

    *For any* workspace with context, triggering compression SHALL create
    ContextFiles/compressed-context.md.

    **Validates: Requirements 29.2, 29.4, 29.6, 29.10**
    """

    @given(
        name=workspace_name_strategy,
        content=context_content_strategy,
    )
    @PROPERTY_SETTINGS
    @pytest.mark.asyncio
    async def test_compress_creates_compressed_context_md(
        self,
        name: str,
        content: str,
        tmp_path: Path,
    ):
        """Compressing context creates ContextFiles/compressed-context.md.

        **Validates: Requirements 29.2, 29.4, 29.10**
        """
        ws = await _create_workspace_with_context_dir(tmp_path, name)

        # Write context first
        await context_manager.update_context(ws["id"], content)

        # Trigger compression
        compressed = await context_manager.compress_context(ws["id"])

        # Property: compressed-context.md must exist
        compressed_file = Path(ws["file_path"]) / "ContextFiles" / "compressed-context.md"
        assert compressed_file.exists(), (
            f"compressed-context.md should exist after compress_context for workspace '{name}'"
        )

        # Property: compressed content must fit within token budget
        assert estimate_tokens(compressed) <= DEFAULT_TOKEN_BUDGET + 20, (
            f"Compressed context should fit within token budget for workspace '{name}'"
        )

        # Property: compressed file content must match returned value
        file_content = compressed_file.read_text(encoding="utf-8")
        assert file_content == compressed, (
            f"compressed-context.md file content should match returned value"
        )

    @given(
        name=workspace_name_strategy,
        content=context_content_strategy,
    )
    @PROPERTY_SETTINGS
    @pytest.mark.asyncio
    async def test_inject_context_uses_context_files(
        self,
        name: str,
        content: str,
        tmp_path: Path,
    ):
        """Context injection reads from ContextFiles/ and includes workspace header.

        **Validates: Requirements 29.1, 29.7**
        """
        ws = await _create_workspace_with_context_dir(tmp_path, name)

        # Write context
        await context_manager.update_context(ws["id"], content)

        # Inject context
        injected = await context_manager.inject_context(ws["id"])

        # Property: injected context must include workspace name header
        assert f"Current Workspace: {name}" in injected, (
            f"Injected context should include workspace header for '{name}'"
        )

        # Property: injected context must contain the written content (or truncated version)
        # Note: inject_context strips whitespace from content before inclusion
        stripped_content = content.strip()
        # For short content, it should be fully included
        if stripped_content and estimate_tokens(stripped_content) <= DEFAULT_TOKEN_BUDGET - 50:
            assert stripped_content in injected, (
                f"Short content should be fully included in injected context"
            )
