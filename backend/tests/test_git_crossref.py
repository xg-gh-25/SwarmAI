"""Tests for memory consistency fix (COE C005): git cross-reference in
DailyActivity capture and git-verified distillation.

Tests two new behaviors:
1. DailyActivity entries include git commits from session timeframe
2. Distillation flags unverified implementation claims with [UNVERIFIED]

TDD RED phase: all tests should FAIL before implementation.
"""

from __future__ import annotations

import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

from core.session_hooks import HookContext

_CTX = HookContext(session_id="s-crossref", agent_id="a", message_count=1,
                   session_start_time="2026-10-27T00:00:00+00:00", session_title="t")



# ---------------------------------------------------------------------------
# Fixture: minimal git repo for testing
# ---------------------------------------------------------------------------

@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    """Create a minimal git repo with a few commits."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, capture_output=True, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=repo, capture_output=True, check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=repo, capture_output=True, check=True,
    )
    # Create initial commit
    (repo / "file1.py").write_text("# initial")
    subprocess.run(["git", "add", "."], cwd=repo, capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "-m", "feat: implement proactive intelligence L0-L4"],
        cwd=repo, capture_output=True, check=True,
    )
    # Second commit
    (repo / "signal_fetcher.py").write_text("# signal fetcher")
    subprocess.run(["git", "add", "."], cwd=repo, capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "-m", "feat: add signal fetcher service"],
        cwd=repo, capture_output=True, check=True,
    )
    return repo


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """Create a minimal SwarmWS-like workspace."""
    ws = tmp_path / "SwarmWS"
    ws.mkdir()
    (ws / "Knowledge" / "DailyActivity").mkdir(parents=True)
    (ws / ".context").mkdir(parents=True)
    # Minimal MEMORY.md with required sections
    (ws / ".context" / "MEMORY.md").write_text(
        "## Key Decisions\n\n## Lessons Learned\n\n## COE Registry\n\n"
        "## Open Threads\n\n### P0 — Blocking\n_(None — all clear)_\n\n"
        "### Resolved (archive)\n- (none)\n"
    )
    # Minimal EVOLUTION.md
    (ws / ".context" / "EVOLUTION.md").write_text(
        "## Corrections Captured\n\n## Competence Learned\n\n"
    )
    return ws


# ===========================================================================
# Test Group 1: Git snapshot in DailyActivity capture
# ===========================================================================

class TestGitSnapshotCapture:
    """DailyActivity entries should include git commits from session timeframe."""

    def test_get_session_git_commits_returns_commits(self, git_repo: Path):
        """_get_session_git_commits should return recent commits from a repo."""
        from hooks.daily_activity_hook import _get_session_git_commits

        # Get commits from the last hour (both test commits are within that window)
        since = datetime.now() - timedelta(hours=1)
        commits = _get_session_git_commits(git_repo, since)

        assert len(commits) >= 1
        # Should contain commit messages
        assert any("proactive intelligence" in c.lower() for c in commits)

    def test_get_session_git_commits_empty_for_old_window(self, git_repo: Path):
        """No commits returned when time window is in the future."""
        from hooks.daily_activity_hook import _get_session_git_commits

        future = datetime.now() + timedelta(hours=1)
        commits = _get_session_git_commits(git_repo, future)
        assert commits == []

    def test_get_session_git_commits_no_repo_returns_empty(self, tmp_path: Path):
        """Gracefully returns empty list for non-git directories."""
        from hooks.daily_activity_hook import _get_session_git_commits

        since = datetime.now() - timedelta(hours=1)
        commits = _get_session_git_commits(tmp_path, since)
        assert commits == []

    def test_attribution_by_run_sha_run_id_and_own_git_calls(self, git_repo: Path):
        """Other sessions commit to the same repo. A commit is this session's
        only on evidence: a run it owns landed it (SHA, or run id in the
        message), or it was made inside one of its own git-writing calls."""
        from hooks.daily_activity_hook import _get_session_git_commits

        since = datetime.now() - timedelta(hours=1)
        log = subprocess.run(["git", "log", "--format=%H %ct %s"], cwd=git_repo,
                             capture_output=True, text=True, check=True).stdout.splitlines()
        (sha2, ct2, _), (sha1, ct1, _) = (ln.split(" ", 2) for ln in log[:2])
        ct1, ct2 = int(ct1), int(ct2)

        # a landed SHA
        got = _get_session_git_commits(git_repo, since, session_shas={sha1}, call_windows=[])
        assert len(got) == 1 and "proactive intelligence" in got[0]
        # a git-writing call whose window covers the commit
        got = _get_session_git_commits(git_repo, since, call_windows=[(ct2 - 1, ct2 + 1, True)])
        assert any("signal fetcher" in g for g in got)
        assert _get_session_git_commits(
            git_repo, since, call_windows=[(ct2 + 1000, ct2 + 1010, True)]) == []
        # a read-only call at the same second is not evidence
        assert _get_session_git_commits(
            git_repo, since, call_windows=[(ct2 - 1, ct2 + 1, False)]) == []
        # touched files are not evidence once the transcript is known
        assert _get_session_git_commits(
            git_repo, since, session_files=[str(git_repo / "signal_fetcher.py")],
            call_windows=[]) == []
        # a run id named in the message
        subprocess.run(["git", "commit", "--allow-empty", "-m", "land it",
                        "-m", "(auto local-commit, run run_abcdef12)"],
                       cwd=git_repo, capture_output=True, check=True)
        got = _get_session_git_commits(git_repo, since, session_run_ids={"run_abcdef12"},
                                       call_windows=[])
        assert len(got) == 1 and "land it" in got[0]
        assert _get_session_git_commits(git_repo, since, session_run_ids={"run_00000000"},
                                        call_windows=[]) == []

    def test_file_fallback_without_transcript(self, git_repo: Path):
        """No transcript: touched files decide, absolute paths only, and a
        top-level file must be in the repo itself (README.md is everywhere)."""
        from hooks.daily_activity_hook import _get_session_git_commits

        since = datetime.now() - timedelta(hours=1)
        assert _get_session_git_commits(
            git_repo, since, session_files=[str(git_repo / "signal_fetcher.py")])
        for other in (["/elsewhere/signal_fetcher.py"], ["signal_fetcher.py"], []):
            assert _get_session_git_commits(git_repo, since, session_files=other) == [], other

    def test_bash_call_windows_from_transcript(self):
        from hooks.daily_activity_hook import _bash_call_windows

        msgs = [
            {"created_at": "2026-10-22T10:00:00", "content": [
                {"type": "tool_use", "id": "t1", "name": "Bash", "category": "bash",
                 "summary": "Running: cd wt && git -C /r commit -q -m x"}]},
            {"created_at": "2026-10-22T10:00:05", "content": [
                {"type": "tool_result", "tool_use_id": "t1", "outcome": ""}]},
            {"created_at": "2026-10-22T10:01:00", "content": [
                {"type": "tool_use", "id": "t2", "name": "Bash", "category": "bash",
                 "summary": "Running: git log --oneline | grep commit"}]},
        ]
        (a1, b1, g1), (a2, b2, g2) = _bash_call_windows(msgs)
        assert b1 - a1 == 5 and g1 is True
        assert a2 == b2 and g2 is False

    def test_subject_is_safe_markdown(self):
        from hooks.daily_activity_hook import _clean_subject
        assert _clean_subject("fix `x`\tthing") == "fix 'x' thing"
        assert len(_clean_subject("y" * 500)) == 120

    def test_format_entry_includes_git_outputs(self):
        """Git commits are preserved as complete items in the Outputs section."""
        from core.daily_activity_writer import _format_session_entry
        from core.summarization import StructuredSummary
        from core.session_hooks import HookContext

        summary = StructuredSummary(
            topics=["implemented signal fetcher"],
            session_title="Signal fetcher",
            timestamp="14:30",
            git_commits=[
                "abc1234 feat: implement proactive intelligence L0-L4",
                "def5678 feat: add signal fetcher service",
            ],
        )
        context = HookContext(
            session_id="abc12345-test",
            agent_id="default",
            message_count=10,
            session_start_time="2026-03-25T14:00:00",
            session_title="Signal fetcher",
        )

        entry = _format_session_entry(summary, context)
        assert "**Outputs:**" in entry
        assert "commit: abc1234" in entry
        assert "commit: def5678" in entry

    def test_format_entry_no_git_section_when_empty(self):
        """No **Git activity:** section when git_commits is empty."""
        from core.daily_activity_writer import _format_session_entry
        from core.summarization import StructuredSummary
        from core.session_hooks import HookContext

        summary = StructuredSummary(
            topics=["discussed design"],
            session_title="Design",
            timestamp="15:00",
        )
        context = HookContext(
            session_id="xyz12345-test",
            agent_id="default",
            message_count=5,
            session_start_time="2026-03-25T15:00:00",
            session_title="Design",
        )

        entry = _format_session_entry(summary, context)
        assert "**Git activity:**" not in entry


# ===========================================================================
# Test Group 2: Git-verified distillation
# ===========================================================================



# ===========================================================================
# Test Group 3: Multi-project repo discovery
# ===========================================================================



# ===========================================================================
# Test Group 4: Regression -- existing behavior preserved
# ===========================================================================

