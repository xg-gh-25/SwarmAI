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
        # Fresh path now carries current_head (Gate-2 MED, run_9a23dd4a): the
        # field is set whenever git succeeded, so a --full rebuild on an
        # already-fresh repo can still refresh the marker.
        assert result.current_head == "abc123"

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


class TestFullRebuildDelegation:
    """Gate-2 HIGH (run_9a23dd4a): the INCREMENTAL job (full=False, 120s) must
    NOT run a full rebuild inline — a full reparse hugs/exceeds 120s and gets
    killed before persisting the marker. When suggest_full_rebuild is True and
    full=False, delegate to the code_intel_full_reindex event (300s job).
    The --full job itself (full=True) still runs inline.
    """

    def _make_project(self, tmp_path, monkeypatch):
        """A Projects/ dir with one never-indexed code_intel.db on a git repo.

        Redirects BOTH path sources the handler uses: Path.home() (for the
        Projects/ iteration) AND load_project_graph (for the DB load, which
        otherwise resolves via the frozen jobs.paths.PROJECTS_DIR).
        """
        import subprocess as sp
        from core.code_intel.graph_store import GraphStore
        proj = tmp_path / ".swarm-ai" / "SwarmWS" / "Projects" / "P1"
        proj.mkdir(parents=True)
        env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
               "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
        sp.run(["git", "init", "-q"], cwd=proj, check=True, env=env)
        (proj / "f.py").write_text("x = 1\n")
        sp.run(["git", "add", "."], cwd=proj, check=True, env=env)
        sp.run(["git", "commit", "-q", "-m", "i"], cwd=proj, check=True, env=env)
        db = GraphStore(proj / "code_intel.db")
        db.set_meta("repo_root", str(proj))  # never indexed: no last_indexed_commit
        db.close()
        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
        # load_project_graph is imported function-locally from core.code_intel,
        # and resolves DB via the frozen PROJECTS_DIR — patch it at the source
        # so the handler operates on OUR never-indexed tmp db.
        import core.code_intel as ci
        monkeypatch.setattr(
            ci, "load_project_graph",
            lambda name: GraphStore(proj / "code_intel.db"),
        )
        import jobs.handlers.code_intel_reindex as handler
        return handler

    def test_incremental_job_delegates_full_rebuild(self, tmp_path, monkeypatch):
        from unittest.mock import patch
        handler = self._make_project(tmp_path, monkeypatch)
        # Run AB: the full-rebuild path now parses via parse_repo_with_coverage
        # (coverage-aware). Patch that entry point so "delegated → nothing parsed
        # inline" stays an exact assertion.
        with patch("jobs.scheduler.emit_event_atomic") as mock_emit, \
             patch("core.code_intel.parser.parse_repo_with_coverage") as mock_parse:
            result = handler.reindex_projects(full=False)
            # Delegated → event emitted, no inline parse
            mock_emit.assert_called_once()
            assert mock_emit.call_args[0][0] == "code_intel_full_reindex"
            mock_parse.assert_not_called()
        statuses = {r["project"]: r["status"] for r in result["projects"]}
        assert statuses.get("P1") == "delegated_full_reindex"

    def test_full_job_runs_inline_not_delegated(self, tmp_path, monkeypatch):
        from unittest.mock import patch
        from core.code_intel.parser import ParseRepoResult
        handler = self._make_project(tmp_path, monkeypatch)
        # full=True → inline parse via the coverage-aware entry point (Run AB).
        with patch("jobs.scheduler.emit_event_atomic") as mock_emit, \
             patch("core.code_intel.parser.parse_repo_with_coverage",
                   return_value=ParseRepoResult(results=[], coverage_holes=[], status="complete")) as mock_parse:
            handler.reindex_projects(full=True)
            # full=True → inline (parse called), NOT delegated
            mock_emit.assert_not_called()
            mock_parse.assert_called_once()


# ─── Run 4b (run_2bad039d, §8.6): spec-details staleness detector ───

class TestSpecDetailsStaleness:
    """detect_spec_details_staleness: spec.md older than code-intel.json = stale."""

    def _setup(self, tmp_path, spec_older: bool):
        import os, time
        proj = tmp_path / "P"; proj.mkdir()
        sd = proj / "spec-details"; sd.mkdir()
        spec = sd / "orders.spec.md"
        ci = proj / "code-intel.json"
        if spec_older:
            spec.write_text("# old", encoding="utf-8")
            time.sleep(0.01)
            ci.write_text("{}", encoding="utf-8")  # ci newer → spec stale
        else:
            ci.write_text("{}", encoding="utf-8")
            time.sleep(0.01)
            spec.write_text("# fresh", encoding="utf-8")  # spec newer → fresh
        return proj

    def test_stale_spec_detected(self, tmp_path):
        from core.code_intel.freshness import detect_spec_details_staleness
        proj = self._setup(tmp_path, spec_older=True)
        assert detect_spec_details_staleness(proj) == ["orders.spec.md"]

    def test_fresh_spec_not_flagged(self, tmp_path):
        from core.code_intel.freshness import detect_spec_details_staleness
        proj = self._setup(tmp_path, spec_older=False)
        assert detect_spec_details_staleness(proj) == []

    def test_no_code_intel_returns_empty(self, tmp_path):
        from core.code_intel.freshness import detect_spec_details_staleness
        proj = tmp_path / "P"; (proj / "spec-details").mkdir(parents=True)
        (proj / "spec-details" / "x.spec.md").write_text("x", encoding="utf-8")
        assert detect_spec_details_staleness(proj) == []  # no code-intel.json

    def test_no_spec_dir_returns_empty(self, tmp_path):
        from core.code_intel.freshness import detect_spec_details_staleness
        proj = tmp_path / "P"; proj.mkdir()
        (proj / "code-intel.json").write_text("{}", encoding="utf-8")
        assert detect_spec_details_staleness(proj) == []
