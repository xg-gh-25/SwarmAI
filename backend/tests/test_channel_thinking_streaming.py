"""Tests for thinking streaming in channel gateway.

Covers:
  - Thinking tokens never leak into stream_flushed (reply_text safety)
  - end_thinking_phase transitions state correctly
  - cleanup_stream during thinking discards pending tokens
  - text_start without prior thinking_start is a no-op
  - Thinking streaming skipped for legacy (non-native) path
  - Empty thinking block (thinking_start with no deltas)
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from channels.streaming import (
    StreamContext,
    cleanup_stream,
    end_thinking_phase,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_ctx(*, native: bool = True, streaming: bool = True) -> StreamContext:
    """Create a StreamContext with a mock adapter for testing."""
    adapter = MagicMock()
    adapter.append_stream = AsyncMock()
    adapter.start_stream = AsyncMock(return_value="stream_123")
    adapter.stop_stream = AsyncMock()
    ctx = StreamContext(
        adapter=adapter,
        external_chat_id="C123",
        inbound_ts="ts_1",
        sender_user_id="U_TEST",
        streaming=streaming,
        native_streaming=native,
        streaming_msg_id="stream_123",
    )
    return ctx


# ---------------------------------------------------------------------------
# end_thinking_phase
# ---------------------------------------------------------------------------

class TestEndThinkingPhase:
    """Tests for end_thinking_phase()."""

    @pytest.mark.asyncio
    async def test_transitions_state(self):
        """in_thinking is reset to False after end_thinking_phase."""
        ctx = _make_ctx()
        ctx.in_thinking = True

        await end_thinking_phase(ctx)

        assert ctx.in_thinking is False

    @pytest.mark.asyncio
    async def test_flushes_pending_thinking_tokens(self):
        """Pending thinking tokens in native_pending_buf are flushed."""
        ctx = _make_ctx()
        ctx.in_thinking = True
        ctx.native_pending_buf = ["token1", "token2"]

        await end_thinking_phase(ctx)

        # Pending buf drained
        assert ctx.native_pending_buf == []
        # append_stream called with pending tokens + separator
        ctx.adapter.append_stream.assert_awaited_once()
        call_args = ctx.adapter.append_stream.call_args
        chunk = call_args[0][2]  # third positional arg
        assert "token1token2" in chunk
        assert "_\n\n---\n\n" in chunk

    @pytest.mark.asyncio
    async def test_sends_separator(self):
        """Closing italic + visual separator is sent when opener was delivered."""
        ctx = _make_ctx()
        ctx.in_thinking = True
        ctx.thinking_content_sent = True  # opener (💭 _) was delivered
        ctx.native_pending_buf = []

        await end_thinking_phase(ctx)

        ctx.adapter.append_stream.assert_awaited_once()
        chunk = ctx.adapter.append_stream.call_args[0][2]
        assert chunk == "_\n\n---\n\n"

    @pytest.mark.asyncio
    async def test_skips_separator_when_opener_failed(self):
        """No stray separator if thinking opener was never delivered."""
        ctx = _make_ctx()
        ctx.in_thinking = True
        ctx.thinking_content_sent = False  # opener failed
        ctx.native_pending_buf = []

        await end_thinking_phase(ctx)

        ctx.adapter.append_stream.assert_not_awaited()
        assert ctx.in_thinking is False

    @pytest.mark.asyncio
    async def test_noop_when_not_thinking(self):
        """No-op if in_thinking is already False."""
        ctx = _make_ctx()
        ctx.in_thinking = False

        await end_thinking_phase(ctx)

        ctx.adapter.append_stream.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_cancels_pending_native_flush(self):
        """Pending native flush timer is cancelled for clean transition."""
        ctx = _make_ctx()
        ctx.in_thinking = True
        mock_handle = MagicMock()
        ctx.native_flush_handle = mock_handle

        await end_thinking_phase(ctx)

        mock_handle.cancel.assert_called_once()
        assert ctx.native_flush_handle is None

    @pytest.mark.asyncio
    async def test_noop_without_adapter(self):
        """No crash if adapter is None (edge case during shutdown)."""
        ctx = _make_ctx()
        ctx.adapter = None
        ctx.in_thinking = True

        await end_thinking_phase(ctx)

        assert ctx.in_thinking is False


# ---------------------------------------------------------------------------
# cleanup_stream — thinking safety
# ---------------------------------------------------------------------------

class TestCleanupStreamThinking:
    """Tests for cleanup_stream() behavior during thinking phase."""

    @pytest.mark.asyncio
    async def test_discards_thinking_tokens_from_reply(self):
        """If cleanup runs during thinking, pending tokens must NOT enter stream_flushed."""
        ctx = _make_ctx()
        ctx.in_thinking = True
        ctx.native_pending_buf = ["thinking_token_1", "thinking_token_2"]
        ctx.stream_buf = []
        ctx.stream_flushed = "prior_text"

        await cleanup_stream(ctx)

        # Thinking tokens discarded — stream_flushed unchanged
        assert ctx.stream_flushed == "prior_text"
        assert ctx.in_thinking is False

    @pytest.mark.asyncio
    async def test_preserves_text_tokens_normally(self):
        """When NOT in thinking, text tokens in stream_buf drain into stream_flushed.

        Real data flow: text_delta puts tokens in BOTH stream_buf (for reply_text)
        AND native_pending_buf (for visual streaming). cleanup_stream sends
        native_pending_buf visually via append_stream, then drains stream_buf
        into stream_flushed.
        """
        ctx = _make_ctx()
        ctx.in_thinking = False
        # Mirror real gateway: text_delta adds to both buffers
        ctx.native_pending_buf = ["text_a", "text_b"]
        ctx.stream_buf = ["text_a", "text_b", "text_c"]
        ctx.stream_flushed = ""

        await cleanup_stream(ctx)

        # stream_buf tokens merged into stream_flushed
        assert "text_a" in ctx.stream_flushed
        assert "text_b" in ctx.stream_flushed
        assert "text_c" in ctx.stream_flushed

    @pytest.mark.asyncio
    async def test_mixed_scenario_text_after_thinking(self):
        """Normal flow: thinking ended, text in stream_buf at cleanup."""
        ctx = _make_ctx()
        ctx.in_thinking = False  # thinking already ended
        # Real flow: text tokens go to both buffers
        ctx.native_pending_buf = ["final_text"]
        ctx.stream_buf = ["final_text"]
        ctx.stream_flushed = "earlier_text"

        await cleanup_stream(ctx)

        assert ctx.stream_flushed == "earlier_textfinal_text"


# ---------------------------------------------------------------------------
# Thinking token isolation — data flow invariant
# ---------------------------------------------------------------------------

class TestThinkingTokenIsolation:
    """Verify the core invariant: thinking tokens never enter stream_flushed."""

    @pytest.mark.asyncio
    async def test_thinking_tokens_only_in_native_buf(self):
        """Simulating the full thinking→text flow: thinking stays out of stream_flushed."""
        ctx = _make_ctx()

        # Phase 1: thinking_start
        ctx.in_thinking = True

        # Phase 2: thinking_delta tokens arrive
        ctx.native_pending_buf.append("analyzing...")
        ctx.native_pending_buf.append("three approaches...")

        # Phase 3: text_start → end_thinking_phase
        await end_thinking_phase(ctx)

        # Phase 4: text tokens arrive
        ctx.stream_buf.append("Here is my answer")
        ctx.native_pending_buf.append("Here is my answer")

        # Phase 5: cleanup
        await cleanup_stream(ctx)

        # Only text tokens in stream_flushed
        assert ctx.stream_flushed == "Here is my answer"
        assert "analyzing" not in ctx.stream_flushed
        assert "three approaches" not in ctx.stream_flushed

    @pytest.mark.asyncio
    async def test_multiple_thinking_blocks(self):
        """Model can emit multiple thinking blocks in one response."""
        ctx = _make_ctx()

        # First thinking block
        ctx.in_thinking = True
        ctx.native_pending_buf.append("first_thought")
        await end_thinking_phase(ctx)

        # Text
        ctx.stream_buf.append("text_1")
        ctx.native_pending_buf.append("text_1")

        # Second thinking block (e.g., after tool use)
        ctx.in_thinking = True
        ctx.native_pending_buf.append("second_thought")
        await end_thinking_phase(ctx)

        # More text
        ctx.stream_buf.append("text_2")
        ctx.native_pending_buf.append("text_2")

        await cleanup_stream(ctx)

        assert "text_1" in ctx.stream_flushed
        assert "text_2" in ctx.stream_flushed
        assert "first_thought" not in ctx.stream_flushed
        assert "second_thought" not in ctx.stream_flushed


# ---------------------------------------------------------------------------
# Legacy path — thinking silently skipped
# ---------------------------------------------------------------------------

class TestLegacyPathThinking:
    """Verify thinking is gracefully skipped for legacy (non-native) streaming."""

    @pytest.mark.asyncio
    async def test_thinking_skipped_when_not_native(self):
        """Gateway guards on native_streaming — legacy path never touches thinking."""
        ctx = _make_ctx(native=False)

        # Simulating what gateway does: guard checks ctx.native_streaming
        # thinking_start handler: if ctx.streaming and ctx.streaming_msg_id and ctx.native_streaming
        assert ctx.native_streaming is False
        # So in_thinking stays False
        assert ctx.in_thinking is False

        # Text flows normally
        ctx.stream_buf.append("reply")
        await cleanup_stream(ctx)
        assert ctx.stream_flushed == "reply"


# ---------------------------------------------------------------------------
# Empty thinking block (finding #4)
# ---------------------------------------------------------------------------

class TestEmptyThinkingBlock:
    """thinking_start followed immediately by text_start (no deltas)."""

    @pytest.mark.asyncio
    async def test_empty_thinking_block(self):
        """Empty Opus 4.8 thinking block (thinking_start, zero deltas) must NOT
        leave a ghost widget. The opener is lazy-written on first delta, so with
        no deltas thinking_content_sent stays False and end_thinking_phase emits
        no separator — no '💭 ---' ghost."""
        ctx = _make_ctx()

        # thinking_start fired but NO thinking_delta followed (opener deferred).
        ctx.in_thinking = True
        ctx.thinking_content_sent = False  # opener never written (lazy)

        # Immediately text_start — no thinking_delta between them
        await end_thinking_phase(ctx)

        assert ctx.in_thinking is False
        # No opener was sent → no separator → no ghost widget.
        ctx.adapter.append_stream.assert_not_awaited()

        # Text works fine after
        ctx.stream_buf.append("answer")
        await cleanup_stream(ctx)
        assert ctx.stream_flushed == "answer"
