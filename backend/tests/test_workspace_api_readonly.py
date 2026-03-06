"""Unit tests for readonly API response on context files.

Tests that the ``GET /workspace/file`` endpoint includes a ``readonly``
field in its response, correctly mapping ``user_customized=False`` to
``readonly: true`` for system-default context files and ``readonly: false``
for user-customized files and non-context files (Requirement 9.4).

Also tests the ``_is_readonly_context_file()`` helper directly.

Testing methodology: unit tests for the helper function plus integration
tests via FastAPI TestClient with a temporary workspace directory.
"""

import os
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from core.context_directory_loader import CONTEXT_FILES
from routers.workspace_api import _is_readonly_context_file


# ── Helper function unit tests ────────────────────────────────────────


class TestIsReadonlyContextFile:
    """Unit tests for _is_readonly_context_file() helper."""

    def test_system_default_files_are_readonly(self):
        """System-default context files (user_customized=False) → True."""
        for spec in CONTEXT_FILES:
            if not spec.user_customized:
                path = f".context/{spec.filename}"
                assert _is_readonly_context_file(path) is True, (
                    f"Expected readonly=True for system file {spec.filename}"
                )

    def test_user_customized_files_are_not_readonly(self):
        """User-customized context files (user_customized=True) → False."""
        for spec in CONTEXT_FILES:
            if spec.user_customized:
                path = f".context/{spec.filename}"
                assert _is_readonly_context_file(path) is False, (
                    f"Expected readonly=False for user file {spec.filename}"
                )

    def test_non_context_files_are_not_readonly(self):
        """Files outside .context/ are never readonly."""
        assert _is_readonly_context_file("README.md") is False
        assert _is_readonly_context_file("Knowledge/Notes/note.md") is False
        assert _is_readonly_context_file("Projects/myproject/file.py") is False

    def test_empty_path_is_not_readonly(self):
        """Empty path returns False."""
        assert _is_readonly_context_file("") is False

    def test_backslash_paths_normalized(self):
        """Backslash paths are normalized to forward slashes."""
        assert _is_readonly_context_file(".context\\SWARMAI.md") is True
        assert _is_readonly_context_file(".context\\USER.md") is False

    def test_unknown_context_file_is_not_readonly(self):
        """A file in .context/ not in CONTEXT_FILES → False."""
        assert _is_readonly_context_file(".context/UNKNOWN_FILE.md") is False
        assert _is_readonly_context_file(".context/BOOTSTRAP.md") is False

    def test_nested_context_path_not_readonly(self):
        """Deeply nested paths under .context/ still check filename only."""
        # This shouldn't match because the actual context files are
        # directly under .context/, not in subdirectories
        assert _is_readonly_context_file(".context/sub/SWARMAI.md") is True

    def test_context_prefix_required(self):
        """Files with same name but outside .context/ are not readonly."""
        assert _is_readonly_context_file("SWARMAI.md") is False
        assert _is_readonly_context_file("other/SWARMAI.md") is False
        assert _is_readonly_context_file("my.context/SWARMAI.md") is False


# ── API integration tests ─────────────────────────────────────────────


class TestGetWorkspaceFileReadonly:
    """Integration tests for readonly field in GET /workspace/file."""

    @pytest.fixture
    def workspace_with_context(self, tmp_path):
        """Create a temp workspace with .context/ files for testing."""
        context_dir = tmp_path / ".context"
        context_dir.mkdir()

        # Create system-default files
        for spec in CONTEXT_FILES:
            (context_dir / spec.filename).write_text(
                f"# {spec.section_name}\nTest content", encoding="utf-8"
            )

        # Create a non-context file
        (tmp_path / "README.md").write_text("# README", encoding="utf-8")

        return tmp_path

    def test_system_default_file_has_readonly_true(
        self, client: TestClient, workspace_with_context
    ):
        """GET /workspace/file for a system-default file returns readonly: true."""
        with patch(
            "routers.workspace_api._get_workspace_path",
            new_callable=AsyncMock,
            return_value=str(workspace_with_context),
        ):
            response = client.get(
                "/api/workspace/file",
                params={"path": ".context/SWARMAI.md"},
            )
            assert response.status_code == 200
            data = response.json()
            assert "readonly" in data
            assert data["readonly"] is True

    def test_user_customized_file_has_readonly_false(
        self, client: TestClient, workspace_with_context
    ):
        """GET /workspace/file for a user-customized file returns readonly: false."""
        with patch(
            "routers.workspace_api._get_workspace_path",
            new_callable=AsyncMock,
            return_value=str(workspace_with_context),
        ):
            response = client.get(
                "/api/workspace/file",
                params={"path": ".context/USER.md"},
            )
            assert response.status_code == 200
            data = response.json()
            assert "readonly" in data
            assert data["readonly"] is False

    def test_non_context_file_has_readonly_false(
        self, client: TestClient, workspace_with_context
    ):
        """GET /workspace/file for a non-context file returns readonly: false."""
        with patch(
            "routers.workspace_api._get_workspace_path",
            new_callable=AsyncMock,
            return_value=str(workspace_with_context),
        ):
            response = client.get(
                "/api/workspace/file",
                params={"path": "README.md"},
            )
            assert response.status_code == 200
            data = response.json()
            assert "readonly" in data
            assert data["readonly"] is False

    def test_all_system_defaults_readonly_in_api(
        self, client: TestClient, workspace_with_context
    ):
        """Every system-default file returns readonly: true via the API."""
        system_files = [s for s in CONTEXT_FILES if not s.user_customized]
        with patch(
            "routers.workspace_api._get_workspace_path",
            new_callable=AsyncMock,
            return_value=str(workspace_with_context),
        ):
            for spec in system_files:
                response = client.get(
                    "/api/workspace/file",
                    params={"path": f".context/{spec.filename}"},
                )
                assert response.status_code == 200
                data = response.json()
                assert data["readonly"] is True, (
                    f"Expected readonly=True for {spec.filename}"
                )

    def test_all_user_customized_not_readonly_in_api(
        self, client: TestClient, workspace_with_context
    ):
        """Every user-customized file returns readonly: false via the API."""
        user_files = [s for s in CONTEXT_FILES if s.user_customized]
        with patch(
            "routers.workspace_api._get_workspace_path",
            new_callable=AsyncMock,
            return_value=str(workspace_with_context),
        ):
            for spec in user_files:
                response = client.get(
                    "/api/workspace/file",
                    params={"path": f".context/{spec.filename}"},
                )
                assert response.status_code == 200
                data = response.json()
                assert data["readonly"] is False, (
                    f"Expected readonly=False for {spec.filename}"
                )


# ── Property-based tests ──────────────────────────────────────────────

from hypothesis import given, settings
import hypothesis.strategies as st


# Feature: context-files-enhancement, Property 10: Readonly API Response for System Default Files
# **Validates: Requirements 9.4**
class TestReadonlyPropertyBased:
    """Property-based tests for readonly API response mapping."""

    @given(idx=st.integers(min_value=0, max_value=len(CONTEXT_FILES) - 1))
    @settings(max_examples=100)
    def test_readonly_matches_user_customized_inverse(self, idx: int):
        """For any ContextFileSpec, readonly == (not user_customized).

        **Validates: Requirements 9.4**

        The readonly field in the API response should be the logical
        inverse of the user_customized flag: system defaults (False)
        become readonly (True), user files (True) become not-readonly
        (False).
        """
        spec = CONTEXT_FILES[idx]
        path = f".context/{spec.filename}"
        result = _is_readonly_context_file(path)
        expected = not spec.user_customized
        assert result == expected, (
            f"For {spec.filename}: readonly={result}, "
            f"expected={expected} (user_customized={spec.user_customized})"
        )

    @given(filename=st.text(min_size=1, max_size=50).filter(
        lambda f: f not in {s.filename for s in CONTEXT_FILES}
    ))
    @settings(max_examples=100)
    def test_unknown_files_in_context_dir_not_readonly(self, filename: str):
        """Files in .context/ not in CONTEXT_FILES are never readonly.

        **Validates: Requirements 9.4**
        """
        path = f".context/{filename}"
        assert _is_readonly_context_file(path) is False

    @given(prefix=st.text(min_size=1, max_size=30).filter(
        lambda p: not p.replace("\\", "/").startswith(".context/")
    ))
    @settings(max_examples=100)
    def test_files_outside_context_dir_never_readonly(self, prefix: str):
        """Files outside .context/ are never readonly regardless of name.

        **Validates: Requirements 9.4**
        """
        # Use a known system-default filename but with a non-.context prefix
        path = f"{prefix}/SWARMAI.md"
        assert _is_readonly_context_file(path) is False
