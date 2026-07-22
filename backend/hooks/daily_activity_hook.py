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
from core.ddd_paths import ddd_path
from database import db
from jobs.paths import STATE_DIR, SWARMWS

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


def recover_crash_checkpoint(workspace_dir: Path | None = None) -> bool:
    """Recover session data from an orphaned checkpoint file.

    Called at startup (before any session hook runs).  If a checkpoint
    file exists from a prior crashed session, appends its data to today's
    DailyActivity and deletes the checkpoint.

    Returns True if a checkpoint was recovered, False otherwise.
    """
    checkpoint_path = STATE_DIR / "session_checkpoint.json"
    if not checkpoint_path.exists():
        return False

    try:
        import json
        data = json.loads(checkpoint_path.read_text(encoding="utf-8"))
        session_id = data.get("session_id", "unknown")
        ts = data.get("ts", 0)
        tool_count = data.get("tool_count", 0)
        files_touched = data.get("files_touched", [])

        if not ts or not session_id:
            checkpoint_path.unlink(missing_ok=True)
            return False

        # Build recovery entry with enriched content (git_commits if available)
        from datetime import datetime
        crash_time = datetime.fromtimestamp(ts).strftime("%H:%M")
        git_commits = data.get("git_commits", [])
        corrections_count = data.get("corrections_count", 0)

        entry = (
            f"\n## {crash_time} | {session_id[:8]} | ⚠️ Recovered from crash checkpoint\n"
            f"**What happened:** Session crashed or was evicted after {tool_count} tool calls.\n"
        )
        if files_touched:
            from pathlib import PurePosixPath
            file_names = [PurePosixPath(f).name for f in files_touched[:10]]
            entry += f"**Files:** {', '.join(f'`{f}`' for f in file_names)}\n"
        if git_commits:
            entry += "**Git activity:**\n"
            for c in git_commits[:3]:
                entry += f"- `{c[:72]}`\n"
        if corrections_count:
            entry += f"**Corrections:** {corrections_count}\n"

        # Append to today's DailyActivity
        ws = workspace_dir or SWARMWS
        today = datetime.now().strftime("%Y-%m-%d")
        da_dir = ws / "Knowledge" / "DailyActivity"
        da_dir.mkdir(parents=True, exist_ok=True)
        da_file = da_dir / f"{today}.md"

        with open(da_file, "a", encoding="utf-8") as f:
            f.write(entry)

        checkpoint_path.unlink(missing_ok=True)
        logger.info(
            "Recovered crash checkpoint for session %s (%d tool calls)",
            session_id[:8], tool_count,
        )
        return True

    except Exception:
        logger.exception("Failed to recover crash checkpoint")
        # Delete corrupt checkpoint to prevent repeated failures
        checkpoint_path.unlink(missing_ok=True)
        return False


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
        # 0. Clean up session checkpoint — normal session end means no crash.
        # If the file is not deleted, recover_crash_checkpoint() would
        # incorrectly treat it as a crash on next startup.
        _checkpoint_path = STATE_DIR / "session_checkpoint.json"
        _checkpoint_path.unlink(missing_ok=True)

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

        # 2c. Quality filter — skip noise entries that add no insight
        # Prevents: pure "read file X" logs, empty summaries, trivial sessions
        if self._is_noise_summary(summary):
            logger.info(
                "DailyActivity skipped for session %s — noise (no insight content)",
                context.session_id,
            )
            # PE-2: record gate trigger for noise_filter
            try:
                from core.gate_promotion import GateManager
                from core.initialization_manager import initialization_manager
                ws = initialization_manager.get_cached_workspace_path()
                if ws:
                    from pathlib import Path
                    artifacts = Path(ws) / "Projects" / "SwarmAI" / ".artifacts"
                    if artifacts.is_dir():
                        GateManager(artifacts).record_trigger("noise_filter")
            except Exception:
                pass
            return

        # 2d. Mine observation patterns (if ring available for this session)
        try:
            from core.observation_miner import ObservationMiner, write_patterns
            from core.observation_hooks import get_session_ring, _cleanup_session_ring
            ring = get_session_ring(context.session_id)
            if ring is not None:
                miner = ObservationMiner()
                patterns = miner.mine(ring.all_completed())
                if patterns:
                    write_patterns(patterns, context.session_id)
                    logger.debug(
                        "ObservationMiner: %d patterns extracted for session %s",
                        len(patterns), context.session_id,
                    )
                # Clean up ring from module registry (prevent memory leak)
                _cleanup_session_ring(context.session_id)
        except Exception:
            pass  # Pattern mining is best-effort, never blocks DA extraction

        # 3. Write to DailyActivity file
        try:
            path = await write_daily_activity(summary, context)
            self._tracker.record_success(context.session_id)
            logger.info(
                "DailyActivity extracted for session %s → %s",
                context.session_id,
                path,
            )

            # 3b. Emit DAILY_ACTIVITY event for DDD cultivation v2
            try:
                from core.cultivation_dispatcher import (
                    EventType, emit_cultivation_event,
                )
                await emit_cultivation_event(
                    EventType.DAILY_ACTIVITY,
                    source="daily_activity_hook",
                    payload={"path": str(path), "session_id": context.session_id},
                    priority=2,
                )
            except Exception:
                pass  # Non-blocking: cultivation emit never breaks extraction

        except Exception as exc:
            self._tracker.record_failure(context.session_id, str(exc))
            raise  # Re-raise so hook manager logs it

    @staticmethod
    def _is_noise_summary(summary) -> bool:
        """Check if a summary is pure noise with no insight value.

        Handles StructuredSummary dataclass (has .decisions, .topics, etc).

        Returns True (skip) if:
        - No decisions, lessons, or deliverables recorded
        - Topics are empty or single-item trivial
        - Files modified is empty (no actual work)

        Returns False (keep) if ANY substantive content exists.
        """
        # PE-2 fix: access actual StructuredSummary fields safely
        decisions = getattr(summary, "decisions", None) or []
        lessons = getattr(summary, "lessons", None) or []
        deliverables = getattr(summary, "deliverables", None) or []
        topics = getattr(summary, "topics", None) or []
        files_modified = getattr(summary, "files_modified", None) or []
        open_questions = getattr(summary, "open_questions", None) or []

        # If there are decisions, lessons, or deliverables — always keep
        if decisions or lessons or deliverables:
            return False

        # If there are open questions — keep (continuity value)
        if open_questions:
            return False

        # If files were modified — keep (work evidence)
        if files_modified:
            return False

        # Check topics for substance
        if not topics:
            return True  # Nothing happened

        # Single trivial topic with no other content = noise
        total_topic_chars = sum(len(t) for t in topics)
        if total_topic_chars < 30:
            return True

        return False

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
                    tech_md = ddd_path(project_dir, "TECH.md")
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
