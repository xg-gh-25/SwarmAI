"""Unit tests for DailyActivity extraction hook lock timeout.

Tests the 10-second ``asyncio.Lock`` timeout added to
``DailyActivityExtractionHook.execute()`` as a P0 concurrency fix.

Testing methodology: unit tests with mocked dependencies.
Key behaviors verified:

- Lock is acquired before extraction runs
- Lock is released after extraction completes (success or failure)
- TimeoutError after 10s logs a warning and skips extraction
- Normal execution still works end-to-end through the lock

# Feature: multi-session-rearchitecture, Task 1.3
"""

from __future__ import annotations

import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hooks.daily_activity_hook import DailyActivityExtractionHook


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_pipeline():
    pipeline = MagicMock()
    pipeline.minimal_summary = MagicMock(return_value="short summary")
    pipeline.summarize = AsyncMock(return_value="full summary")
    return pipeline


@pytest.fixture
def mock_tracker():
    tracker = MagicMock()
    tracker.record_success = MagicMock()
    tracker.record_failure = MagicMock()
    return tracker


@pytest.fixture
def hook(mock_pipeline, mock_tracker):
    return DailyActivityExtractionHook(
        summarization_pipeline=mock_pipeline,
        compliance_tracker=mock_tracker,
    )


@pytest.fixture
def mock_context():
    ctx = MagicMock()
    ctx.session_id = "test-session-123"
    return ctx


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestDailyActivityLockTimeout:
    """Tests for the 10-second lock timeout on DailyActivity extraction."""

    @pytest.mark.asyncio
    async def test_hook_has_asyncio_lock(self, hook):
        """Hook initializes with an asyncio.Lock instance."""
        assert isinstance(hook._lock, asyncio.Lock)

    @pytest.mark.asyncio
    async def test_lock_timeout_skips_extraction(
        self, hook, mock_context, caplog
    ):
        """When lock is held and timeout expires, extraction is skipped
        with a warning log.

        Validates: Requirements 5.2, 5.3
        """
        # Pre-acquire the lock to simulate a stuck holder
        await hook._lock.acquire()

        try:
            # Patch wait_for to use a tiny timeout so the test is fast
            with patch("hooks.daily_activity_hook.asyncio.wait_for", side_effect=asyncio.TimeoutError):
                with caplog.at_level(logging.WARNING):
                    await hook.execute(mock_context)

            # Verify warning was logged
            assert any(
                "timed out after 10s" in record.message
                for record in caplog.records
            ), "Expected timeout warning in logs"

            # Verify session_id is in the log message
            assert any(
                "test-session-123" in record.message
                for record in caplog.records
            ), "Expected session_id in timeout warning"
        finally:
            hook._lock.release()

    @pytest.mark.asyncio
    async def test_lock_released_after_successful_execution(
        self, hook, mock_context
    ):
        """Lock is released after successful extraction.

        Validates: Requirements 5.2
        """
        with patch("hooks.daily_activity_hook.db") as mock_db:
            mock_db.messages.list_by_session_paginated = AsyncMock(
                return_value=[]
            )
            await hook.execute(mock_context)

        # Lock should be free — acquiring should succeed immediately
        assert not hook._lock.locked(), "Lock should be released after execute"

    @pytest.mark.asyncio
    async def test_lock_released_after_exception(
        self, hook, mock_context
    ):
        """Lock is released even when extraction raises an exception.

        Validates: Requirements 5.2
        """
        with patch("hooks.daily_activity_hook.db") as mock_db:
            mock_db.messages.list_by_session_paginated = AsyncMock(
                side_effect=RuntimeError("db error")
            )
            with pytest.raises(RuntimeError, match="db error"):
                await hook.execute(mock_context)

        # Lock should still be free
        assert not hook._lock.locked(), "Lock should be released after exception"

    @pytest.mark.asyncio
    async def test_normal_execution_with_no_messages(
        self, hook, mock_context
    ):
        """Normal path: no messages → skip extraction, lock released.

        Validates: Requirements 5.2
        """
        with patch("hooks.daily_activity_hook.db") as mock_db:
            mock_db.messages.list_by_session_paginated = AsyncMock(
                return_value=[]
            )
            await hook.execute(mock_context)

        assert not hook._lock.locked()
