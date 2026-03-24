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
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from core.session_hooks import HookContext
from database import db

logger = logging.getLogger(__name__)

# Minimum message count to justify extraction -- short sessions
# rarely produce meaningful lessons.
MIN_MESSAGES_FOR_EXTRACTION = 8

# Sections in IMPROVEMENT.md that we append to.
SECTION_WHAT_WORKED = "## What Worked"
SECTION_WHAT_FAILED = "## What Failed"
SECTION_KNOWN_ISSUES = "## Known Issues"


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
        improvement_path = (
            self._workspace / "Projects" / project_name / "IMPROVEMENT.md"
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

        Heuristic: scan assistant messages for file paths under Projects/.
        Returns the most-referenced project name, or None.
        """
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

            # Look for Projects/<name>/ references in file paths
            for project_dir in projects_dir.iterdir():
                if not project_dir.is_dir() or project_dir.name.startswith("."):
                    continue
                if project_dir.name in content:
                    project_counts[project_dir.name] = (
                        project_counts.get(project_dir.name, 0) + 1
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

            # Detect "what worked" patterns
            if any(
                marker in content_lower
                for marker in [
                    "this worked",
                    "fix verified",
                    "tests pass",
                    "shipped",
                    "all pass",
                    "all passed",
                    "clean implementation",
                ]
            ):
                # Extract a one-line summary from the surrounding context
                summary = self._extract_summary(content, "worked")
                if summary:
                    worked.append(summary)

            # Detect "what failed" patterns
            if any(
                marker in content_lower
                for marker in [
                    "root cause",
                    "coe",
                    "regression",
                    "broke",
                    "failed because",
                    "bug:",
                    "the real issue",
                    "should have",
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
                if clean and not clean.startswith("#"):
                    return clean[:150]

        return None

    async def _append_lessons(
        self,
        improvement_path: Path,
        lessons: dict,
        context: HookContext,
    ) -> None:
        """Append extracted lessons to IMPROVEMENT.md under the right sections."""
        content = improvement_path.read_text(encoding="utf-8")
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        modified = False

        for item in lessons.get("worked", []):
            entry = f"- **{today}** (session {context.session_id[:8]}): {item}"
            content, changed = self._insert_after_header(
                content, SECTION_WHAT_WORKED, entry
            )
            modified = modified or changed

        for item in lessons.get("failed", []):
            entry = f"- **{today}** (session {context.session_id[:8]}): {item}"
            content, changed = self._insert_after_header(
                content, SECTION_WHAT_FAILED, entry
            )
            modified = modified or changed

        if modified:
            improvement_path.write_text(content, encoding="utf-8")

    @staticmethod
    def _insert_after_header(
        content: str, header: str, entry: str
    ) -> tuple[str, bool]:
        """Insert an entry after a markdown section header.

        Handles edge cases: header at end of file (no trailing newline),
        header followed by blank line, etc.  Returns (new_content, changed).
        """
        if header not in content:
            return content, False

        idx = content.index(header) + len(header)
        next_newline = content.find("\n", idx)

        if next_newline == -1:
            # Header is the last line — append to end
            content = content + "\n\n" + entry + "\n"
        else:
            content = (
                content[: next_newline + 1]
                + "\n" + entry + "\n"
                + content[next_newline + 1:]
            )

        return content, True
