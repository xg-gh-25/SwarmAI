"""Tests for the Slack Human Experience modules.

Tests cover:
1. MessageQueue — FIFO, merge, redirect detection
2. HeartbeatManager — phase progression, ack lifecycle
3. HumanResponseFormatter — splitting long responses
4. Effort Calibration system prompt injection (channel-only)
"""
import pytest

from channels.message_queue import (
    ChannelMessageQueue,
    QueuedMessage,
    is_redirect,
    extract_post_redirect,
)
from channels.heartbeat import (
    HeartbeatManager,
    estimate_complexity,
    pick_ack,
    ACK_TEMPLATES,
)
from channels.response_formatter import HumanResponseFormatter


# ===========================================================================
# MessageQueue tests
# ===========================================================================

class TestRedirectDetection:
    """Test redirect keyword detection."""

    def test_chinese_redirect_detected(self):
        assert is_redirect("算了，看别的") is True
        assert is_redirect("不用了") is True
        assert is_redirect("换个问题") is True
        assert is_redirect("别查了") is True

    def test_english_redirect_detected(self):
        assert is_redirect("never mind") is True
        assert is_redirect("forget it, let's do something else") is True
        assert is_redirect("cancel") is True

    def test_normal_message_not_redirect(self):
        assert is_redirect("帮我查下 CI 状态") is False
        assert is_redirect("what's the bug?") is False
        assert is_redirect("顺便看下 backend") is False

    def test_extract_post_redirect(self):
        assert extract_post_redirect("算了，看下 CI") == "看下 CI"
        assert extract_post_redirect("forget it, check the deploy") == "check the deploy"
        # Bare cancel with no follow-up returns empty string
        assert extract_post_redirect("算了") == ""
        assert extract_post_redirect("cancel") == ""

    def test_no_false_positive_on_substrings(self):
        """English keywords require word boundaries — no mid-word match."""
        assert is_redirect("nonstop service") is False
        assert is_redirect("the cancellation policy") is False
        assert is_redirect("stopwatch timer") is False
        # But exact words still match
        assert is_redirect("please stop") is True
        assert is_redirect("cancel that") is True


class TestMessageQueue:
    """Test FIFO queue with merge semantics."""

    @pytest.fixture
    def queue(self):
        return ChannelMessageQueue(session_id="test-session")

    @pytest.mark.asyncio
    async def test_enqueue_when_not_processing(self, queue):
        msg = QueuedMessage(text="hello")
        result = await queue.enqueue(msg)
        assert result == "queued"
        assert queue.qsize() == 1

    @pytest.mark.asyncio
    async def test_merge_when_processing(self, queue):
        queue.processing = True
        msg = QueuedMessage(text="also check CI")
        result = await queue.enqueue(msg)
        assert result == "merged"
        assert queue.qsize() == 0  # not in queue
        assert queue.drain_supplements() == "[追加] also check CI"

    @pytest.mark.asyncio
    async def test_redirect_cancels_current(self, queue):
        queue.processing = True
        msg = QueuedMessage(text="算了，看下 deploy")
        result = await queue.enqueue(msg)
        assert result == "redirect"
        assert queue.cancelled is True
        # New request should be in queue
        assert queue.qsize() == 1
        next_msg = await queue.get()
        assert "deploy" in next_msg.text

    @pytest.mark.asyncio
    async def test_multiple_supplements_merge(self, queue):
        queue.processing = True
        await queue.enqueue(QueuedMessage(text="also check CI"))
        await queue.enqueue(QueuedMessage(text="focus on backend"))
        merged = queue.drain_supplements()
        assert "[追加] also check CI" in merged
        assert "[追加] focus on backend" in merged

    @pytest.mark.asyncio
    async def test_drain_returns_none_when_empty(self, queue):
        queue.processing = True
        assert queue.drain_supplements() is None

    @pytest.mark.asyncio
    async def test_processing_reset_clears_state(self, queue):
        queue.processing = True
        await queue.enqueue(QueuedMessage(text="supplement"))
        queue._cancel_event.set()
        # Reset
        queue.processing = False
        assert queue.cancelled is False
        assert queue.drain_supplements() is None


# ===========================================================================
# Heartbeat tests
# ===========================================================================

class MockHeartbeatFns:
    """Mock callables for heartbeat testing."""

    def __init__(self):
        self.messages: list[tuple[str, str]] = []  # (action, text)
        self.deleted: list[str] = []

    async def post(self, channel: str, text: str):
        self.messages.append(("post", text))
        return "ack_ts_123"

    async def update(self, channel: str, ts: str, text: str):
        self.messages.append(("update", text))

    async def delete(self, channel: str, ts: str):
        self.deleted.append(ts)


class TestHeartbeatManager:
    """Test heartbeat lifecycle."""

    @pytest.mark.asyncio
    async def test_post_ack(self):
        fns = MockHeartbeatFns()
        hb = HeartbeatManager(
            post_fn=fns.post, update_fn=fns.update,
            delete_fn=fns.delete, channel="C123",
        )
        ts = await hb.post_ack("看一下")
        assert ts == "ack_ts_123"
        assert hb.ack_ts == "ack_ts_123"
        assert fns.messages[0] == ("post", "看一下")

    @pytest.mark.asyncio
    async def test_delete_ack(self):
        fns = MockHeartbeatFns()
        hb = HeartbeatManager(
            post_fn=fns.post, update_fn=fns.update,
            delete_fn=fns.delete, channel="C123",
        )
        await hb.post_ack("看一下")
        await hb.delete_ack()
        assert "ack_ts_123" in fns.deleted
        assert hb.ack_ts is None

    @pytest.mark.asyncio
    async def test_update_final(self):
        fns = MockHeartbeatFns()
        hb = HeartbeatManager(
            post_fn=fns.post, update_fn=fns.update,
            delete_fn=fns.delete, channel="C123",
        )
        await hb.post_ack("看一下")
        await hb.update_final("好的，停了。")
        assert ("update", "好的，停了。") in fns.messages


class TestComplexityEstimation:
    """Test request complexity classification."""

    def test_quick_short_question(self):
        assert estimate_complexity("几点了?") == "quick"
        assert estimate_complexity("CI green?") == "quick"

    def test_medium_normal_question(self):
        assert estimate_complexity("帮我看下昨天的 deploy 有没有问题") == "medium"

    def test_heavy_research_keywords(self):
        assert estimate_complexity("dive deep into the compaction issue") == "heavy"
        assert estimate_complexity("全面分析一下这个 bug") == "heavy"
        assert estimate_complexity("深入看看这个问题") == "heavy"

    def test_pick_ack_returns_string(self):
        for level in ("quick", "medium", "heavy"):
            ack = pick_ack(level)
            assert isinstance(ack, str)
            assert len(ack) > 0
            assert ack in ACK_TEMPLATES[level]


# ===========================================================================
# ResponseFormatter tests
# ===========================================================================

class TestHumanResponseFormatter:
    """Test response splitting."""

    @pytest.fixture
    def formatter(self):
        return HumanResponseFormatter(max_single_msg=200, max_segment=300)

    def test_short_response_single_message(self, formatter):
        text = "The bug is in session_unit.py line 42."
        segments = formatter.format(text)
        assert len(segments) == 1
        assert segments[0] == text

    def test_empty_response(self, formatter):
        segments = formatter.format("")
        assert segments == ["(No response)"]

    def test_long_response_splits_on_paragraphs(self, formatter):
        text = "First paragraph with enough content to exceed the limit." + "x" * 100
        text += "\n\n"
        text += "Second paragraph with more details." + "y" * 100
        text += "\n\n"
        text += "Third paragraph conclusion." + "z" * 100
        segments = formatter.format(text)
        assert len(segments) >= 2

    def test_never_splits_code_block(self):
        formatter = HumanResponseFormatter(max_single_msg=50, max_segment=100)
        text = "Here's the code:\n\n```python\ndef foo():\n    return 42\n```\n\nDone."
        segments = formatter.format(text)
        # Code block should be intact in one segment
        found_code = False
        for seg in segments:
            if "```python" in seg and "```" in seg[seg.index("```python") + 10:]:
                found_code = True
        assert found_code

    def test_splits_on_headers(self):
        formatter = HumanResponseFormatter(max_single_msg=50, max_segment=100)
        text = "Introduction text here.\n\n## Section One\n\nContent of section one.\n\n## Section Two\n\nContent of section two."
        segments = formatter.format(text)
        assert len(segments) >= 2

    def test_default_max_values(self):
        formatter = HumanResponseFormatter()
        assert formatter.max_single_msg == 2000
        assert formatter.max_segment == 3000
