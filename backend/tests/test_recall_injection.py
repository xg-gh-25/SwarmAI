"""Tests for pre-response recall injection (G3: post-first-message recall).

Verifies that RecallEngine L2/L3 is triggered by the user's actual first
message (not proactive keywords) and injects results into the system prompt
before it reaches the SDK.

Acceptance criteria under test:
  1. First message triggers recall with actual query keywords
  2. Recalled knowledge injected into system prompt
  3. Second+ messages skip recall (once-per-session)
  4. Channel sessions excluded
  5. 100ms timeout — failure never blocks
  6. Chinese queries extract CJK terms correctly
  7. Short/empty messages skip recall
"""

import asyncio
import re
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ── Keyword extraction tests ─────────────────────────────────────────


class TestExtractQueryKeywords:
    """Test keyword extraction from user messages (no LLM, pure NLP)."""

    def test_extracts_english_keywords(self):
        from core.session_router import _extract_query_keywords

        result = _extract_query_keywords("How does the evolution pipeline handle corrections?")
        assert "evolution" in result.lower()
        assert "pipeline" in result.lower()
        assert "corrections" in result.lower()

    def test_strips_filler_phrases(self):
        from core.session_router import _extract_query_keywords

        result = _extract_query_keywords("Hey please can you help me with the recall engine?")
        # Filler stripped, substantive words kept
        assert "recall" in result.lower()
        assert "engine" in result.lower()
        # Filler words should not dominate
        assert "hey" not in result.lower()
        assert "please" not in result.lower()

    def test_extracts_cjk_terms(self):
        from core.session_router import _extract_query_keywords

        result = _extract_query_keywords("帮我看看 OOM 修复的进展")
        # CJK characters preserved
        assert any(ord(c) >= 0x4E00 for c in result), "Should contain CJK chars"
        # English terms also extracted
        assert "oom" in result.lower()

    def test_empty_message_returns_empty(self):
        from core.session_router import _extract_query_keywords

        assert _extract_query_keywords("") == ""

    def test_short_message_returns_empty(self):
        from core.session_router import _extract_query_keywords

        # Messages too short for meaningful recall
        assert _extract_query_keywords("hi") == ""
        assert _extract_query_keywords("ok") == ""

    def test_caps_at_max_terms(self):
        from core.session_router import _extract_query_keywords

        long_msg = " ".join(f"word{i}" for i in range(50))
        result = _extract_query_keywords(long_msg)
        terms = result.split()
        assert len(terms) <= 15, f"Should cap at 15 terms, got {len(terms)}"


# ── Recall injection tests ────────────────────────────────────────────


class TestMaybeInjectRecall:
    """Test the pre-response recall injection hook."""

    @pytest.fixture
    def mock_unit(self):
        unit = MagicMock()
        unit._recall_injected = False
        unit.is_channel_session = False
        unit.working_directory = "/tmp/test-ws"
        return unit

    @pytest.fixture
    def mock_options(self):
        options = MagicMock()
        options.system_prompt = "## Base system prompt content"
        return options

    @pytest.mark.asyncio
    async def test_first_message_triggers_recall(self, mock_unit, mock_options):
        from core.session_router import _maybe_inject_recall

        with patch("core.session_router._recall_for_query", return_value="Some recalled knowledge"):
            await _maybe_inject_recall(
                user_message="How does the evolution pipeline work?",
                options=mock_options,
                unit=mock_unit,
            )

        assert "Recalled Knowledge" in mock_options.system_prompt
        assert "Some recalled knowledge" in mock_options.system_prompt
        assert mock_unit._recall_injected is True

    @pytest.mark.asyncio
    async def test_second_message_skips_recall(self, mock_unit, mock_options):
        from core.session_router import _maybe_inject_recall

        mock_unit._recall_injected = True  # Already recalled
        original_prompt = mock_options.system_prompt

        await _maybe_inject_recall(
            user_message="Tell me more about corrections",
            options=mock_options,
            unit=mock_unit,
        )

        # Prompt unchanged
        assert mock_options.system_prompt == original_prompt

    @pytest.mark.asyncio
    async def test_channel_session_excluded(self, mock_unit, mock_options):
        from core.session_router import _maybe_inject_recall

        mock_unit.is_channel_session = True
        original_prompt = mock_options.system_prompt

        await _maybe_inject_recall(
            user_message="What about the recall engine?",
            options=mock_options,
            unit=mock_unit,
        )

        assert mock_options.system_prompt == original_prompt
        assert mock_unit._recall_injected is True  # Flag still set to prevent retry

    @pytest.mark.asyncio
    async def test_empty_keywords_skips_recall(self, mock_unit, mock_options):
        from core.session_router import _maybe_inject_recall

        original_prompt = mock_options.system_prompt

        await _maybe_inject_recall(
            user_message="hi",
            options=mock_options,
            unit=mock_unit,
        )

        assert mock_options.system_prompt == original_prompt
        assert mock_unit._recall_injected is True

    @pytest.mark.asyncio
    async def test_recall_timeout_does_not_block(self, mock_unit, mock_options):
        from core.session_router import _maybe_inject_recall

        def slow_recall(*args, **kwargs):
            time.sleep(2)  # Way over 100ms timeout
            return "This should never be injected"

        with patch("core.session_router._recall_for_query", side_effect=slow_recall):
            start = time.monotonic()
            await _maybe_inject_recall(
                user_message="Tell me about the architecture",
                options=mock_options,
                unit=mock_unit,
            )
            elapsed = time.monotonic() - start

        # Should complete in well under 2 seconds (the sleep duration)
        assert elapsed < 1.0, f"Timeout should have fired, but took {elapsed:.1f}s"
        assert "never be injected" not in mock_options.system_prompt
        assert mock_unit._recall_injected is True

    @pytest.mark.asyncio
    async def test_recall_exception_does_not_block(self, mock_unit, mock_options):
        from core.session_router import _maybe_inject_recall

        with patch("core.session_router._recall_for_query", side_effect=RuntimeError("DB corrupt")):
            # Should not raise
            await _maybe_inject_recall(
                user_message="Check the memory pipeline",
                options=mock_options,
                unit=mock_unit,
            )

        # Prompt unchanged, flag set
        assert "Recalled Knowledge" not in mock_options.system_prompt
        assert mock_unit._recall_injected is True

    @pytest.mark.asyncio
    async def test_recall_empty_result_no_injection(self, mock_unit, mock_options):
        from core.session_router import _maybe_inject_recall

        with patch("core.session_router._recall_for_query", return_value=""):
            await _maybe_inject_recall(
                user_message="Tell me about something obscure",
                options=mock_options,
                unit=mock_unit,
            )

        original_base = "## Base system prompt content"
        assert mock_options.system_prompt == original_base
        assert mock_unit._recall_injected is True
