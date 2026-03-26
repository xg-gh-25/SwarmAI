"""Post-session DailyActivity extraction hook.

Retrieves the conversation log from the database, passes it through
the ``SummarizationPipeline``, and appends the result to the
DailyActivity file.  Records success/failure in ``ComplianceTracker``.

Key public symbols:

- ``DailyActivityExtractionHook``  — Implements ``SessionLifecycleHook``.
"""

from __future__ import annotations

import asyncio
import logging
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

from core.session_hooks import HookContext
from core.summarization import SummarizationPipeline
from core.daily_activity_writer import write_daily_activity
from core.compliance import ComplianceTracker
from database import db

logger = logging.getLogger(__name__)


def _get_session_git_commits(
    repo_path: Path,
    since: datetime,
    max_commits: int = 15,
) -> list[str]:
    """Get git commits from a repo since a given time.

    Returns a list of ``"<short_hash> <subject>"`` strings, newest first.
    Gracefully returns an empty list if the path is not a git repo or
    git is unavailable.

    Args:
        repo_path: Path to a git repository (or any directory — non-repos
            return empty).
        since: Only include commits after this timestamp.
        max_commits: Maximum number of commits to return.
    """
    if not repo_path.is_dir():
        return []

    # Verify this directory IS a git repo root (contains .git/).
    # Without this check, git traverses upward and returns commits from
    # a parent repo — producing false positives for arbitrary directories.
    if not (repo_path / ".git").exists():
        return []

    since_str = since.strftime("%Y-%m-%dT%H:%M:%S")
    try:
        result = subprocess.run(
            [
                "git", "log",
                f"--since={since_str}",
                f"--max-count={max_commits}",
                "--oneline",
                "--no-decorate",
            ],
            cwd=str(repo_path),
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            return []
        lines = [line.strip() for line in result.stdout.strip().splitlines() if line.strip()]
        return lines
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as exc:
        logger.debug("Git log failed for %s: %s", repo_path, exc)
        return []


class DailyActivityExtractionHook:
    """Extracts conversation summaries into DailyActivity files.

    Registered as the first post-session-close hook so that
    DailyActivity is written before workspace auto-commit captures it.
    """

    name = "daily_activity_extraction"

    def __init__(
        self,
        summarization_pipeline: SummarizationPipeline,
        compliance_tracker: ComplianceTracker,
    ) -> None:
        self._pipeline = summarization_pipeline
        self._tracker = compliance_tracker
        self._lock = asyncio.Lock()

    async def execute(self, context: HookContext) -> None:
        """Extract DailyActivity from the closed session's conversation."""
        # Acquire lock with 10s timeout to prevent deadlock if holder crashes
        try:
            await asyncio.wait_for(self._lock.acquire(), timeout=10.0)
        except asyncio.TimeoutError:
            logger.warning(
                "DailyActivity lock acquisition timed out after 10s — "
                "skipping extraction for session %s",
                context.session_id,
            )
            return

        try:
            await self._execute_locked(context)
        finally:
            self._lock.release()

    async def _execute_locked(self, context: HookContext) -> None:
        """Core extraction logic, called while holding ``_lock``."""
        # 1. Retrieve conversation log (capped for memory safety)
        messages = await db.messages.list_by_session_paginated(
            context.session_id, limit=500
        )

        if not messages:
            logger.info(
                "No messages for session %s, skipping extraction",
                context.session_id,
            )
            return

        # 2. Summarize — minimal for short conversations
        if len(messages) < 3:
            summary = self._pipeline.minimal_summary(messages)
        else:
            summary = await self._pipeline.summarize(messages)

        # 2b. Capture git ground truth — actual commits during session
        # This prevents COE C005: DailyActivity text claims vs git reality diverging.
        summary.git_commits = await asyncio.to_thread(
            self._capture_git_activity, context.session_start_time
        )

        # 3. Write to DailyActivity file
        try:
            path = await write_daily_activity(summary, context)
            self._tracker.record_success(context.session_id)
            logger.info(
                "DailyActivity extracted for session %s → %s",
                context.session_id,
                path,
            )
        except Exception as exc:
            self._tracker.record_failure(context.session_id, str(exc))
            raise  # Re-raise so hook manager logs it

    @staticmethod
    def _capture_git_activity(session_start_iso: str) -> list[str]:
        """Capture git commits made since session start from source repos.

        Scans ALL projects' TECH.md for repo paths — no hardcoded paths.
        Returns commits from the first repo that has recent activity.
        Empty list on any failure (non-blocking, best-effort).
        """
        try:
            since = datetime.fromisoformat(session_start_iso.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            since = datetime.now() - timedelta(hours=2)

        # Discover repos from all projects' TECH.md files
        candidates: list[Path] = []
        try:
            from core.initialization_manager import initialization_manager
            ws = Path(initialization_manager.get_cached_workspace_path())
            projects_dir = ws / "Projects"
            if projects_dir.is_dir():
                for project_dir in sorted(projects_dir.iterdir()):
                    tech_md = project_dir / "TECH.md"
                    if not tech_md.is_file():
                        continue
                    try:
                        content = tech_md.read_text(encoding="utf-8")
                        for line in content.splitlines():
                            if any(kw in line for kw in (
                                "Clone:", "local:", "Local:",
                                "Codebase", "codebase", "repo",
                                "source", "Source",
                            )):
                                import re
                                paths = re.findall(r"(/[^\s`\"']+)", line)
                                for p in paths:
                                    p = p.rstrip("/),;.")
                                    if len(p) > 5:
                                        candidates.append(Path(p))
                    except (OSError, UnicodeDecodeError):
                        continue
        except Exception:
            pass

        for repo_path in candidates:
            if repo_path.is_dir() and (repo_path / ".git").exists():
                commits = _get_session_git_commits(repo_path, since)
                if commits:
                    return commits
        return []
