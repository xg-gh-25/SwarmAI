"""Tests for CodeChangeFeed code_intel re-index extension.

Verifies that after detecting changed files, the hook also
re-indexes them into the code_intel graph if a project graph exists.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from hooks.code_change_feed import _reindex_changed_files


class TestReindexChangedFiles:
    """P3: Git hook → code_intel graph auto-rebuild."""

    def test_skips_when_no_graph(self, tmp_path):
        """No-op when project has no code_intel.db."""
        changed_files = [("A", "backend/core/new_module.py")]
        # load_project_graph returns None → no db
        with patch("hooks.code_change_feed.load_project_graph", return_value=None):
            result = _reindex_changed_files(changed_files, str(tmp_path))
        assert result == 0

    def test_reindexes_python_files(self, tmp_path):
        """Parses and inserts changed .py files into graph."""
        # Create a fake source file
        src = tmp_path / "backend" / "core" / "foo.py"
        src.parent.mkdir(parents=True)
        src.write_text("def hello(): pass\n")

        changed_files = [("A", "backend/core/foo.py"), ("M", "backend/core/bar.py")]

        mock_graph = MagicMock()
        mock_parse_result = MagicMock()
        mock_parse_result.nodes = [MagicMock()]
        mock_parse_result.edges = []

        with patch("hooks.code_change_feed.load_project_graph", return_value=mock_graph), \
             patch("hooks.code_change_feed.parse_file", return_value=mock_parse_result) as mock_parse:
            result = _reindex_changed_files(changed_files, str(tmp_path))

        # Should have attempted to parse foo.py (exists) but not bar.py (doesn't exist)
        assert mock_parse.call_count == 1
        mock_graph.bulk_insert.assert_called_once()
        assert result == 1

    def test_skips_non_code_files(self, tmp_path):
        """Only .py and .ts/.tsx files are re-indexed."""
        changed_files = [
            ("A", "README.md"),
            ("M", "config.yaml"),
            ("A", "assets/logo.png"),
        ]

        mock_graph = MagicMock()
        with patch("hooks.code_change_feed.load_project_graph", return_value=mock_graph):
            result = _reindex_changed_files(changed_files, str(tmp_path))

        # No code files → no parse calls
        assert result == 0
        mock_graph.bulk_insert.assert_not_called()

    def test_skips_test_files(self, tmp_path):
        """Test files are not indexed into the code graph."""
        test_file = tmp_path / "tests" / "test_foo.py"
        test_file.parent.mkdir(parents=True)
        test_file.write_text("def test_x(): pass\n")

        changed_files = [("A", "tests/test_foo.py")]

        mock_graph = MagicMock()
        with patch("hooks.code_change_feed.load_project_graph", return_value=mock_graph):
            result = _reindex_changed_files(changed_files, str(tmp_path))

        assert result == 0

    def test_exception_does_not_propagate(self, tmp_path):
        """Re-index errors are swallowed (non-blocking)."""
        src = tmp_path / "backend" / "foo.py"
        src.parent.mkdir(parents=True)
        src.write_text("x = 1\n")

        changed_files = [("A", "backend/foo.py")]

        with patch("hooks.code_change_feed.load_project_graph", side_effect=Exception("boom")):
            # Should NOT raise
            result = _reindex_changed_files(changed_files, str(tmp_path))
        assert result == 0

    def test_handles_deleted_files(self, tmp_path):
        """Deleted files (status D) are skipped — can't parse what doesn't exist."""
        changed_files = [("D", "backend/old_module.py")]

        mock_graph = MagicMock()
        with patch("hooks.code_change_feed.load_project_graph", return_value=mock_graph):
            result = _reindex_changed_files(changed_files, str(tmp_path))

        assert result == 0

    def test_skips_when_code_intel_unavailable(self, tmp_path):
        """Early return when code_intel module is not importable."""
        with patch("hooks.code_change_feed._CODE_INTEL_AVAILABLE", False):
            result = _reindex_changed_files([("A", "foo.py")], str(tmp_path))
        assert result == 0
