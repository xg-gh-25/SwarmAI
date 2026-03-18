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

from core.session_hooks import HookContext
from core.summarization import SummarizationPipeline
from core.daily_activity_writer import write_daily_activity
from core.compliance import ComplianceTracker
from database import db

logger = logging.getLogger(__name__)


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
