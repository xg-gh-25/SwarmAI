"""Post-session IMPROVEMENT.md write-back hook.

After a session closes, scans the conversation for lessons learned,
patterns that worked/failed, and bugs encountered.  Appends findings
to the active project's IMPROVEMENT.md if it exists.

This closes the DDD learning loop: sessions produce knowledge that
compounds in the project's historical patterns document.  Without this
hook, IMPROVEMENT.md stays frozen at its template content.

Key public symbols:

- ``ImprovementWritebackHook``  -- Implements ``SessionLifecycleHook``.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from core.session_hooks import HookContext
from core.ddd_paths import ddd_path
from database import db

logger = logging.getLogger(__name__)

# Minimum message count to justify extraction -- short sessions
# rarely produce meaningful lessons.
MIN_MESSAGES_FOR_EXTRACTION = 8


class ImprovementWritebackHook:
    """Extracts lessons from closed sessions into project IMPROVEMENT.md.

    Registered after DailyActivity extraction and auto-commit so that
    it runs on a settled workspace state.  Skips gracefully if:
    - No active project detected from the session
    - Project has no IMPROVEMENT.md (L0/L1 -- not enforced)
    - Session too short (< MIN_MESSAGES_FOR_EXTRACTION)
    - No actionable lessons found in conversation
    """

    name = "improvement_writeback"

    def __init__(self, workspace_path: str) -> None:
        self._workspace = Path(workspace_path)
        self._lock = asyncio.Lock()

    async def execute(self, context: HookContext) -> None:
        """Extract lessons from session and append to IMPROVEMENT.md."""
        if context.message_count < MIN_MESSAGES_FOR_EXTRACTION:
            return

        try:
            await asyncio.wait_for(self._lock.acquire(), timeout=10.0)
        except asyncio.TimeoutError:
            logger.warning(
                "Improvement writeback lock timeout for session %s",
                context.session_id,
            )
            return

        try:
            await self._execute_locked(context)
        finally:
            self._lock.release()

    async def _execute_locked(self, context: HookContext) -> None:
        """Core extraction logic, called while holding ``_lock``."""
        # 1. Detect active project from session messages
        project_name = await self._detect_project(context.session_id)
        if not project_name:
            return

        # 2. Check IMPROVEMENT.md exists (don't create it -- respect L0/L1)
        improvement_path = ddd_path(
            self._workspace / "Projects" / project_name, "IMPROVEMENT.md"
        )
        if not improvement_path.exists():
            return

        # 3. Extract lessons from conversation
        lessons = await self._extract_lessons(context.session_id)
        if not lessons:
            return

        # 4. Append to IMPROVEMENT.md
        await self._append_lessons(improvement_path, lessons, context)

        logger.info(
            "Wrote %d lessons to %s/IMPROVEMENT.md from session %s",
            len(lessons.get("worked", [])) + len(lessons.get("failed", [])),
            project_name,
            context.session_id,
        )

    async def _detect_project(self, session_id: str) -> str | None:
        """Detect which project a session was working on.

        Priority:
          1. Session's chat thread binding (most reliable)
          2. Most-edited file paths under Projects/ (from tool_use blocks)
          3. Most-referenced project name in message text (fallback)

        Returns the project name, or None.
        """
        # Priority 1: check chat thread binding for explicit project
        try:
            session = await db.sessions.get(session_id)
            if session:
                thread_id = session.get("chat_thread_id") if isinstance(session, dict) else getattr(session, "chat_thread_id", None)
                if thread_id:
                    thread = await db.chat_threads.get(thread_id)
                    if thread:
                        project = thread.get("project") if isinstance(thread, dict) else getattr(thread, "project", None)
                        if project:
                            return project
        except Exception:
            pass  # Thread binding not available — fall through to heuristics

        messages = await db.messages.list_by_session_paginated(
            session_id, limit=100
        )

        project_counts: dict[str, int] = {}
        projects_dir = self._workspace / "Projects"

        if not projects_dir.is_dir():
            return None

        for msg in messages:
            content = msg.get("content", "") if isinstance(msg, dict) else ""
            if isinstance(content, list):
                # Handle content blocks
                content = " ".join(
                    b.get("text", "")
                    for b in content
                    if isinstance(b, dict) and b.get("type") == "text"
                )

            # Look for Projects/<name>/ path patterns (not bare substring)
            for project_dir in projects_dir.iterdir():
                if not project_dir.is_dir() or project_dir.name.startswith("."):
                    continue
                # Match actual path references: Projects/Name/ or /full/path/Projects/Name
                name = project_dir.name
                if f"Projects/{name}/" in content or f"Projects/{name}" in content.split():
                    project_counts[name] = (
                        project_counts.get(name, 0) + 1
                    )

        if not project_counts:
            return None

        # Return most-referenced project
        return max(project_counts, key=project_counts.get)

    async def _extract_lessons(self, session_id: str) -> dict | None:
        """Extract what worked and what failed from a session.

        Returns dict with 'worked' and 'failed' lists of strings,
        or None if nothing actionable found.

        Uses pattern matching on assistant messages -- no LLM call needed.
        Looks for explicit markers: COE, bug, fix, lesson, mistake, etc.
        """
        messages = await db.messages.list_by_session_paginated(
            session_id, limit=200
        )

        worked: list[str] = []
        failed: list[str] = []

        for msg in messages:
            role = msg.get("role", "") if isinstance(msg, dict) else ""
            if role != "assistant":
                continue

            content = msg.get("content", "")
            if isinstance(content, list):
                content = " ".join(
                    b.get("text", "")
                    for b in content
                    if isinstance(b, dict) and b.get("type") == "text"
                )

            content_lower = content.lower()

            # Detect "what worked" patterns — require explicit lesson-like
            # phrasing, not routine test output like "all 80 tests pass"
            if any(
                marker in content_lower
                for marker in [
                    "this worked",
                    "fix verified",
                    "shipped successfully",
                    "clean implementation",
                    "lesson learned",
                    "pattern that worked",
                    "approach worked",
                ]
            ):
                # Extract a one-line summary from the surrounding context
                summary = self._extract_summary(content, "worked")
                if summary:
                    worked.append(summary)

            # Detect "what failed" patterns — require explicit failure
            # analysis, not routine test failures
            if any(
                marker in content_lower
                for marker in [
                    "root cause",
                    "coe",
                    "regression caused",
                    "broke because",
                    "failed because",
                    "bug: ",
                    "the real issue",
                    "should have",
                    "lesson: ",
                ]
            ):
                summary = self._extract_summary(content, "failed")
                if summary:
                    failed.append(summary)

        if not worked and not failed:
            return None

        return {"worked": worked[:3], "failed": failed[:3]}  # Cap at 3 each

    def _extract_summary(self, content: str, category: str) -> str | None:
        """Extract a one-line summary from a content block.

        Takes the first sentence that contains a lesson-like pattern.
        Returns None if nothing meaningful found.
        """
        sentences = content.replace("\n", " ").split(". ")
        keywords = {
            "worked": [
                "fixed", "shipped", "pass", "clean", "resolved",
                "implemented", "verified", "works",
            ],
            "failed": [
                "root cause", "broke", "regression", "bug", "failed",
                "should have", "mistake", "coe", "wrong",
            ],
        }

        for sentence in sentences:
            sentence = sentence.strip()
            if len(sentence) < 20 or len(sentence) > 200:
                continue
            if any(kw in sentence.lower() for kw in keywords.get(category, [])):
                # Clean up: remove markdown formatting, truncate
                clean = sentence.replace("**", "").replace("`", "").strip()
                if not clean or clean.startswith("#"):
                    continue
                # M2 quality gate (reuse, don't reinvent — R25): reject instance-logs
                # and first-person narration ("I have enough to diagnose the root
                # cause") that keyword-match but carry no durable lesson.
                from core.ddd_cultivation import is_quality_lesson
                if not is_quality_lesson(clean):
                    continue
                return clean[:150]

        return None

    async def _append_lessons(
        self,
        improvement_path: Path,
        lessons: dict,
        context: HookContext,
    ) -> None:
        """Route extracted lessons through the SHARED cultivation admission path.

        UNIFIED INTAKE (run_4c5f81ce): this hook no longer writes IMPROVEMENT.md
        with its own ``_insert_after_header`` + bespoke dedup. It builds an append
        ``CultivationProposal`` per lesson and applies it via ``apply_to_ddd`` —
        the SAME single chokepoint pipeline REFLECT uses. Why:
          - After this change there is ONE live writer format (cultivation's), so
            this hook's lessons dedup against REFLECT entries via the shared
            content_signature. The old split — this hook's OWN front-prefix format
            + blind dedup — silted 43K archive dups; content_signature still
            normalizes that legacy front-prefix so incoming lessons also dedup
            against the pre-existing corpus.
          - ``source_stage="writeback"`` keeps the attribution honest (NOT
            "auto-cultivated"/reflect — that would misattribute hook output).
          - ``apply_to_ddd`` is SYNC (fcntl.flock + read + atomic rename); this
            hook is awaited directly on the event loop (session_hooks.py:577), so
            each call is offloaded with ``asyncio.to_thread`` to avoid blocking it.
        """
        from core.ddd_cultivation import CultivationProposal, apply_to_ddd

        project_dir = improvement_path.parent
        # session_id is NOT a pipeline run_id; tag it distinctly so the changelog /
        # weekly report can tell writeback-sourced entries from REFLECT ones.
        source_run_id = f"session_{context.session_id[:8]}"

        section_for = {
            "worked": "What Worked",
            "failed": "What Failed",
        }
        for key, section in section_for.items():
            for item in lessons.get(key, []):
                proposal = CultivationProposal(
                    target_doc="IMPROVEMENT.md",
                    target_section=section,
                    content=item,
                    source_run_id=source_run_id,
                    confidence=0.5,
                    source_stage="writeback",
                    change_type="append",
                )
                # Offload the sync file I/O off the event loop (hook is awaited on it).
                status = await asyncio.to_thread(apply_to_ddd, proposal, project_dir)
                # "duplicate" and "rejected_low_value" are the gate WORKING as intended
                # (the chokepoint dedup + value floor doing their job) — expected, not a
                # fault. Only an unexpected status (doc_missing / locked / not_safe)
                # warrants a warning.
                if status not in ("applied", "created_section", "duplicate", "rejected_low_value"):
                    logger.warning(
                        "writeback: apply_to_ddd returned %s for a %s lesson "
                        "(session %s)",
                        status, section, context.session_id[:8],
                    )
