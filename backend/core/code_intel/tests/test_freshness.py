"""Tests for freshness.py — git SHA tracking and staleness detection."""

import os
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

    @patch("core.code_intel.freshness._git")
    def test_never_indexed(self, mock_git_fn, mock_graph, tmp_path):
        (tmp_path / ".git").mkdir()
        mock_graph.get_meta.side_effect = lambda key: {
            "repo_root": str(tmp_path),
            "last_indexed_commit": None,
        }.get(key)
        mock_git_fn.return_value = "head_sha\n"
        result = check_freshness(mock_graph)
        assert result.stale is True
        assert result.suggest_full_rebuild is True
        assert "Never indexed" in result.reason
        # REGRESSION GUARD (run_9a23dd4a): current_head MUST be populated on the
        # never-indexed path so the 3 marker writers (code_intel_reindex.py:73/129,
        # context_health_hook.py:649 — all guarded by `if freshness.current_head:`)
        # can persist last_indexed_commit. Before the fix this was None → marker
        # never persisted → perpetual full rebuild → 120s timeout flap.
        # Revert the freshness fix and THIS assertion goes RED (mutation-proven).
        assert result.current_head == "head_sha"

    @patch("core.code_intel.freshness._git")
    def test_never_indexed_git_failure_keeps_none(self, mock_git_fn, mock_graph, tmp_path):
        """On the never-indexed path, a genuine git rev-parse failure must leave
        current_head=None (the write-guard stays closed) and NOT crash — the
        marker simply isn't persisted this cycle (correct: we can't know HEAD)."""
        from core.code_intel.freshness import GitError
        (tmp_path / ".git").mkdir()
        mock_graph.get_meta.side_effect = lambda key: {
            "repo_root": str(tmp_path),
            "last_indexed_commit": None,
        }.get(key)
        mock_git_fn.side_effect = GitError("rev-parse failed")
        result = check_freshness(mock_graph)
        assert result.stale is True
        assert result.suggest_full_rebuild is True
        assert result.current_head is None  # guard preserved, no crash

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


class TestPersistenceLoopBreaks:
    """Integration (run_9a23dd4a): prove the perpetual-full-rebuild loop breaks.

    Uses a REAL git repo + REAL GraphStore (no mocks) — the round-trip that the
    reindex handler relies on: on a never-indexed graph, check_freshness now
    returns a populated current_head; the caller persists it as
    last_indexed_commit; the NEXT check_freshness sees HEAD==last_commit and
    returns stale=False (no rebuild). Before the fix, current_head was None on
    the never-indexed path, so the marker never persisted and every run was
    'Never indexed' → full rebuild → 120s timeout flap.
    """

    def _init_git_repo(self, tmp_path):
        import subprocess as sp
        env = {
            **os.environ,
            "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
            "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
        }
        sp.run(["git", "init", "-q"], cwd=tmp_path, check=True, env=env)
        (tmp_path / "f.py").write_text("x = 1\n")
        sp.run(["git", "add", "."], cwd=tmp_path, check=True, env=env)
        sp.run(["git", "commit", "-q", "-m", "init"], cwd=tmp_path, check=True, env=env)
        head = sp.run(["git", "rev-parse", "HEAD"], cwd=tmp_path,
                      capture_output=True, text=True, check=True, env=env).stdout.strip()
        return head

    def test_marker_persists_and_second_check_is_fresh(self, tmp_path):
        from core.code_intel.graph_store import GraphStore

        head = self._init_git_repo(tmp_path)
        db = GraphStore(tmp_path / "code_intel.db")
        db.set_meta("repo_root", str(tmp_path))
        # never indexed: no last_indexed_commit yet
        assert db.get_meta("last_indexed_commit") is None

        # First check: never-indexed → stale + full rebuild, BUT current_head now populated
        fr1 = check_freshness(db)
        assert fr1.stale is True
        assert fr1.suggest_full_rebuild is True
        assert fr1.current_head == head  # THE FIX: was None before

        # Caller persists the marker (mirrors code_intel_reindex.py:73 / :129,
        # context_health_hook.py:649 — the `if freshness.current_head:` writers)
        if fr1.current_head:
            db.set_meta("last_indexed_commit", fr1.current_head)
        assert db.get_meta("last_indexed_commit") == head

        # Second check with no new commits: loop is broken → NOT stale, no rebuild
        fr2 = check_freshness(db)
        assert fr2.stale is False
        assert fr2.suggest_full_rebuild is False
        db.close()
