"""Tests for freshness.py — git SHA tracking and staleness detection."""

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from core.code_intel.freshness import (
    FreshnessResult,
    GitError,
    _git,
    check_freshness,
)


@pytest.fixture
def mock_graph():
    """Mock GraphStore with meta values."""
    graph = MagicMock()
    graph.get_meta.side_effect = lambda key: {
        "repo_root": "/tmp/test_repo",
        "last_indexed_commit": "abc123def456",
        "last_full_index": "1700000000.0",
    }.get(key)
    return graph


class TestCheckFreshness:
    """Test the main check_freshness function."""

    def test_no_repo_root(self):
        graph = MagicMock()
        graph.get_meta.return_value = None
        result = check_freshness(graph)
        assert result.stale is True
        assert result.suggest_full_rebuild is True

    def test_missing_directory(self, mock_graph):
        mock_graph.get_meta.side_effect = lambda key: {
            "repo_root": "/nonexistent/path",
            "last_indexed_commit": "abc123",
        }.get(key)
        result = check_freshness(mock_graph)
        assert result.stale is True
        assert "not found" in result.reason

    def test_never_indexed(self, mock_graph, tmp_path):
        (tmp_path / ".git").mkdir()
        mock_graph.get_meta.side_effect = lambda key: {
            "repo_root": str(tmp_path),
            "last_indexed_commit": None,
        }.get(key)
        result = check_freshness(mock_graph)
        assert result.stale is True
        assert result.suggest_full_rebuild is True
        assert "Never indexed" in result.reason

    @patch("core.code_intel.freshness._git")
    def test_up_to_date(self, mock_git_fn, mock_graph, tmp_path):
        (tmp_path / ".git").mkdir()
        mock_graph.get_meta.side_effect = lambda key: {
            "repo_root": str(tmp_path),
            "last_indexed_commit": "abc123",
        }.get(key)
        mock_git_fn.return_value = "abc123\n"
        result = check_freshness(mock_graph)
        assert result.stale is False

    @patch("core.code_intel.freshness.subprocess.run")
    @patch("core.code_intel.freshness._git")
    def test_normal_incremental(self, mock_git_fn, mock_subprocess, mock_graph, tmp_path):
        (tmp_path / ".git").mkdir()
        mock_graph.get_meta.side_effect = lambda key: {
            "repo_root": str(tmp_path),
            "last_indexed_commit": "old_sha",
        }.get(key)

        # _git calls: rev-parse, diff, rev-list
        mock_git_fn.side_effect = [
            "new_sha\n",           # rev-parse HEAD
            "file1.py\nfile2.py\n",  # diff --name-only
            "3\n",                  # rev-list --count
        ]
        # merge-base check succeeds (ancestor)
        mock_subprocess.return_value = MagicMock(returncode=0)

        result = check_freshness(mock_graph)
        assert result.stale is True
        assert result.changed_files == ["file1.py", "file2.py"]
        assert result.commits_behind == 3
        assert result.suggest_full_rebuild is False

    @patch("core.code_intel.freshness.subprocess.run")
    @patch("core.code_intel.freshness._git")
    def test_rebase_detected(self, mock_git_fn, mock_subprocess, mock_graph, tmp_path):
        (tmp_path / ".git").mkdir()
        mock_graph.get_meta.side_effect = lambda key: {
            "repo_root": str(tmp_path),
            "last_indexed_commit": "old_sha",
        }.get(key)
        mock_git_fn.return_value = "new_sha\n"
        # merge-base fails (not ancestor)
        mock_subprocess.return_value = MagicMock(returncode=1)

        result = check_freshness(mock_graph)
        assert result.stale is True
        assert result.suggest_full_rebuild is True
        assert "rebased away" in result.reason

    @patch("core.code_intel.freshness.subprocess.run")
    @patch("core.code_intel.freshness._git")
    def test_large_change_suggests_rebuild(self, mock_git_fn, mock_subprocess, mock_graph, tmp_path):
        (tmp_path / ".git").mkdir()
        mock_graph.get_meta.side_effect = lambda key: {
            "repo_root": str(tmp_path),
            "last_indexed_commit": "old_sha",
        }.get(key)

        files = [f"file{i}.py" for i in range(120)]
        mock_git_fn.side_effect = [
            "new_sha\n",
            "\n".join(files) + "\n",
            "55\n",
        ]
        mock_subprocess.return_value = MagicMock(returncode=0)

        result = check_freshness(mock_graph)
        assert result.stale is True
        assert result.suggest_full_rebuild is True
        assert len(result.changed_files) == 120


class TestGitCommand:
    """Test the _git helper."""

    @patch("core.code_intel.freshness.subprocess.run")
    def test_success(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0, stdout="output\n", stderr=""
        )
        result = _git(Path("/tmp"), ["status"])
        assert result == "output\n"

    @patch("core.code_intel.freshness.subprocess.run")
    def test_failure_raises(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=1, stdout="", stderr="error msg"
        )
        with pytest.raises(GitError, match="error msg"):
            _git(Path("/tmp"), ["bad-command"])

    @patch("core.code_intel.freshness.subprocess.run")
    def test_timeout(self, mock_run):
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="git", timeout=5)
        with pytest.raises(GitError, match="timed out"):
            _git(Path("/tmp"), ["slow-command"])

    @patch("core.code_intel.freshness.subprocess.run")
    def test_git_not_found(self, mock_run):
        mock_run.side_effect = FileNotFoundError()
        with pytest.raises(GitError, match="not found"):
            _git(Path("/tmp"), ["status"])


class TestFreshnessResult:
    """Test dataclass defaults."""

    def test_defaults(self):
        result = FreshnessResult(stale=False)
        assert result.changed_files == []
        assert result.commits_behind == 0
        assert result.suggest_full_rebuild is False
        assert result.reason == ""
