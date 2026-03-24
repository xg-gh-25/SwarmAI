"""Tests for memory consistency fix (COE C005): git cross-reference in
DailyActivity capture and git-verified distillation.

Tests two new behaviors:
1. DailyActivity entries include git commits from session timeframe
2. Distillation flags unverified implementation claims with [UNVERIFIED]

TDD RED phase: all tests should FAIL before implementation.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest


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

    def test_format_entry_includes_git_section(self):
        """Formatted DailyActivity entry should include **Git activity:** section
        when git commits are available."""
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
        assert "**Git activity:**" in entry
        assert "abc1234" in entry
        assert "def5678" in entry

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

class TestGitVerifiedDistillation:
    """Distillation should verify implementation claims against git."""

    def test_verify_claim_found_in_git(self, git_repo: Path):
        """Claims matching git log should pass verification."""
        from hooks.distillation_hook import DistillationTriggerHook

        hook = DistillationTriggerHook()
        result = hook._verify_claim_against_git(
            "Proactive Intelligence L0-L4 fully implemented",
            git_repo,
        )
        assert result is True

    def test_verify_claim_not_found_in_git(self, git_repo: Path):
        """Claims NOT matching git log should fail verification."""
        from hooks.distillation_hook import DistillationTriggerHook

        hook = DistillationTriggerHook()
        result = hook._verify_claim_against_git(
            "MCP Gateway implemented and deployed",
            git_repo,
        )
        assert result is False

    def test_non_implementation_claims_skip_verification(self, git_repo: Path):
        """Claims without implementation keywords should not be verified
        (return True by default -- they're not making code claims)."""
        from hooks.distillation_hook import DistillationTriggerHook

        hook = DistillationTriggerHook()
        # This is a decision, not an implementation claim
        result = hook._verify_claim_against_git(
            "Design principle: prevent, don't handle",
            git_repo,
        )
        assert result is True

    def test_verify_claim_via_file_name(self, git_repo: Path):
        """Claims should also be verified via git ls-files (file existence)."""
        from hooks.distillation_hook import DistillationTriggerHook

        hook = DistillationTriggerHook()
        # signal_fetcher.py exists in the repo
        result = hook._verify_claim_against_git(
            "Signal fetcher module created",
            git_repo,
        )
        assert result is True

    def test_verify_claim_via_code_content(self, git_repo: Path):
        """Claims should be verified via git grep (code content search).

        Even if commit messages and file names don't match, finding the
        subject word in actual code content should verify the claim.
        """
        from hooks.distillation_hook import DistillationTriggerHook

        # Write code content that mentions "proactive" but filename doesn't
        import subprocess
        (git_repo / "core.py").write_text("class ProactiveIntelligence:\n    pass\n")
        subprocess.run(["git", "add", "."], cwd=git_repo, capture_output=True, check=True)
        subprocess.run(
            ["git", "commit", "-m", "add core module"],
            cwd=git_repo, capture_output=True, check=True,
        )

        hook = DistillationTriggerHook()
        # "ProactiveIntelligence" is in code content but not in
        # commit message "add core module" or filename "core.py"
        result = hook._verify_claim_against_git(
            "ProactiveIntelligence class implemented",
            git_repo,
        )
        assert result is True

    def test_unverified_claims_tagged(self, workspace: Path, git_repo: Path):
        """Implementation claims that fail git verification should be tagged
        [UNVERIFIED] in the promoted MEMORY.md entry."""
        from hooks.distillation_hook import DistillationTriggerHook

        hook = DistillationTriggerHook()

        # Create a DailyActivity file with a false claim
        da_dir = workspace / "Knowledge" / "DailyActivity"
        da_file = da_dir / "2026-03-25.md"
        da_file.write_text(
            '---\ndate: "2026-03-25"\nsessions_count: 1\ndistilled: false\n---\n'
            "## 14:30 | test1234 | MCP Gateway\n\n"
            "**Decisions:**\n"
            "- MCP Gateway fully implemented and shipped\n\n"
            "**Lessons:**\n"
            "- Always test under memory pressure\n"
        )

        # Distill with git verification against our test repo
        with patch.object(
            DistillationTriggerHook, '_get_source_repo_path',
            return_value=git_repo,
        ):
            hook._distill_files([da_file], workspace)

        memory = (workspace / ".context" / "MEMORY.md").read_text()
        # The false implementation claim should be tagged
        assert "[UNVERIFIED]" in memory
        # The lesson (not an implementation claim) should NOT be tagged
        assert "Always test under memory pressure" in memory
        assert memory.count("[UNVERIFIED]") == 1  # Only the false claim

    def test_verified_claims_not_tagged(self, workspace: Path, git_repo: Path):
        """Implementation claims that pass git verification should NOT be tagged."""
        from hooks.distillation_hook import DistillationTriggerHook

        hook = DistillationTriggerHook()

        da_dir = workspace / "Knowledge" / "DailyActivity"
        da_file = da_dir / "2026-03-25.md"
        da_file.write_text(
            '---\ndate: "2026-03-25"\nsessions_count: 1\ndistilled: false\n---\n'
            "## 14:30 | test1234 | Signal fetcher\n\n"
            "**Decisions:**\n"
            "- Signal fetcher service implemented\n\n"
        )

        with patch.object(
            DistillationTriggerHook, '_get_source_repo_path',
            return_value=git_repo,
        ):
            hook._distill_files([da_file], workspace)

        memory = (workspace / ".context" / "MEMORY.md").read_text()
        # This claim IS in git, so should NOT be tagged
        assert "signal fetcher" in memory.lower()
        assert "[UNVERIFIED]" not in memory

    def test_tag_unverified_preserves_entry_format(self, git_repo: Path):
        """Tagged entries should preserve the '- YYYY-MM-DD: ' prefix format."""
        from hooks.distillation_hook import DistillationTriggerHook

        hook = DistillationTriggerHook()
        entries = [
            "- 2026-03-25: MCP Gateway fully implemented and shipped",
        ]
        tagged = hook._tag_unverified_claims(entries, git_repo)
        assert len(tagged) == 1
        # Should start with the date prefix and contain [UNVERIFIED]
        assert tagged[0].startswith("- 2026-03-25:")
        assert "[UNVERIFIED]" in tagged[0]


# ===========================================================================
# Test Group 3: Multi-project repo discovery
# ===========================================================================

class TestRepoDiscovery:
    """Repo path discovery should scan all projects, not just SwarmAI."""

    def test_extract_repo_paths_from_tech_md(self):
        """_extract_repo_paths should find paths from TECH.md content."""
        from hooks.distillation_hook import _extract_repo_paths

        content = (
            "## Codebase Location\n"
            "- **Local:** `/Users/dev/projects/myapp`\n"
            "- **Clone:** `git clone https://github.com/org/myapp.git`\n"
        )
        paths = _extract_repo_paths(content)
        assert any(str(p) == "/Users/dev/projects/myapp" for p in paths)

    def test_extract_repo_paths_empty_for_no_paths(self):
        """No paths extracted from content without repo references."""
        from hooks.distillation_hook import _extract_repo_paths

        content = "# My Project\n\nJust a description, no paths.\n"
        paths = _extract_repo_paths(content)
        assert paths == []

    def test_get_source_repo_scans_all_projects(self, tmp_path: Path, git_repo: Path):
        """_get_source_repo_path should find repos from any project's TECH.md."""
        from hooks.distillation_hook import DistillationTriggerHook

        # Create a fake workspace with two projects
        ws = tmp_path / "ws"
        (ws / "Projects" / "ProjectA").mkdir(parents=True)
        (ws / "Projects" / "ProjectB").mkdir(parents=True)

        # ProjectA has no TECH.md
        # ProjectB has TECH.md pointing to our git_repo
        (ws / "Projects" / "ProjectB" / "TECH.md").write_text(
            f"## Codebase\n- **Local:** `{git_repo}`\n"
        )

        with patch(
            "hooks.distillation_hook.initialization_manager"
        ) as mock_init:
            mock_init.get_cached_workspace_path.return_value = str(ws)
            result = DistillationTriggerHook._get_source_repo_path()

        assert result == git_repo


# ===========================================================================
# Test Group 4: Regression -- existing behavior preserved
# ===========================================================================

class TestDistillationRegression:
    """Existing distillation behavior must not regress."""

    def test_supersede_by_topic_still_works(self):
        """_supersede_by_topic should still deduplicate entries by topic."""
        from hooks.distillation_hook import DistillationTriggerHook

        entries = [
            "- 2026-03-14: Proactive Intelligence L0+L1 implemented",
            "- 2026-03-19: Proactive Intelligence L0-L4 fully implemented",
        ]
        result = DistillationTriggerHook._supersede_by_topic(entries)
        assert len(result) == 1
        assert "L0-L4" in result[0]

    def test_distill_marks_files_as_distilled(self, workspace: Path):
        """Files should still get distilled:true frontmatter after processing."""
        from hooks.distillation_hook import DistillationTriggerHook

        hook = DistillationTriggerHook()

        da_dir = workspace / "Knowledge" / "DailyActivity"
        da_file = da_dir / "2026-03-25.md"
        da_file.write_text(
            '---\ndate: "2026-03-25"\nsessions_count: 1\ndistilled: false\n---\n'
            "## 14:30 | test1234 | Test session\n\n"
            "**Decisions:**\n"
            "- Design principle: keep it simple\n\n"
        )

        # Distill — non-implementation claim, no git needed
        with patch.object(
            DistillationTriggerHook, '_get_source_repo_path',
            return_value=None,
        ):
            hook._distill_files([da_file], workspace)

        content = da_file.read_text()
        assert "distilled: true" in content

    def test_extract_decisions_unchanged(self):
        """_extract_decisions should still work with existing format."""
        from hooks.distillation_hook import DistillationTriggerHook

        body = (
            "## 14:30 | test | Session\n\n"
            "**Decisions:**\n"
            "- Use pytest for all backend tests\n"
            "- Design principle: prevent errors structurally\n"
        )
        decisions = DistillationTriggerHook._extract_decisions(body)
        assert len(decisions) == 2

    def test_extract_lessons_unchanged(self):
        """_extract_lessons should still work with existing format."""
        from hooks.distillation_hook import DistillationTriggerHook

        body = (
            "## 14:30 | test | Session\n\n"
            "**Lessons:**\n"
            "- Constants correct at one scale become bugs at another\n"
        )
        lessons = DistillationTriggerHook._extract_lessons(body)
        assert len(lessons) == 1
