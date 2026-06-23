"""Tests for AskQuestionManager — the block/signal store for AskUserQuestion.

Mirrors PermissionManager's wait/set/has_live_waiter semantics, but the result
payload is the user's answers dict (not an approve/deny string), and the waiter
is keyed on the SDK tool_use_id (block.id) — the same id surfaced via the
ask_user_question SSE event and passed back by continue_with_answer.

Validates the AskUserQuestion block-hook fix (run_594233bb):
- AC1: question blocks (no self-resolution) until the answer is set
- AC2: the exact answers dict round-trips from set_answer to the waiter
"""

import asyncio
import inspect

import pytest

from core.ask_question_manager import AskQuestionManager, ask_question_manager


class TestAnswerSetWaitRoundTrip:
    """set_answer before/concurrent-with wait_for_answer returns the exact dict."""

    @pytest.mark.asyncio
    async def test_set_then_wait_returns_exact_answers(self):
        mgr = AskQuestionManager()
        tool_use_id = "toolu_bdrk_block123"
        answers = {"Pick a color": "Red", "Pick a size": "Large"}

        async def set_after_brief_delay():
            # Yield so wait_for_answer registers the event first.
            await asyncio.sleep(0.01)
            mgr.set_answer(tool_use_id, answers)

        wait_task = asyncio.create_task(mgr.wait_for_answer(tool_use_id, timeout=5))
        set_task = asyncio.create_task(set_after_brief_delay())

        result = await wait_task
        await set_task
        assert result == answers

    @pytest.mark.asyncio
    async def test_distinct_ids_do_not_cross(self):
        mgr = AskQuestionManager()

        async def answer_b():
            await asyncio.sleep(0.01)
            mgr.set_answer("id_b", {"q": "B"})

        wait_b = asyncio.create_task(mgr.wait_for_answer("id_b", timeout=5))
        asyncio.create_task(answer_b())
        # id_a is never answered; only id_b should resolve.
        result_b = await wait_b
        assert result_b == {"q": "B"}


class TestHasLiveWaiter:
    """has_live_waiter is the respawn-immune liveness signal (mirrors permission)."""

    @pytest.mark.asyncio
    async def test_has_live_waiter_true_while_blocked(self):
        mgr = AskQuestionManager()
        tool_use_id = "toolu_live"
        assert mgr.has_live_waiter(tool_use_id) is False

        async def check_then_answer():
            # Give the waiter a tick to register.
            await asyncio.sleep(0.01)
            assert mgr.has_live_waiter(tool_use_id) is True
            mgr.set_answer(tool_use_id, {"q": "x"})

        wait_task = asyncio.create_task(mgr.wait_for_answer(tool_use_id, timeout=5))
        await check_then_answer()
        await wait_task
        # After resolution the waiter is cleaned up.
        assert mgr.has_live_waiter(tool_use_id) is False

    def test_no_waiter_for_unknown_id(self):
        mgr = AskQuestionManager()
        assert mgr.has_live_waiter("never_seen") is False


class TestTimeout:
    """Timeout returns a DISTINCT sentinel — never silently an empty answers dict."""

    def test_default_timeout_is_300(self):
        sig = inspect.signature(AskQuestionManager.wait_for_answer)
        assert sig.parameters["timeout"].default == 300

    @pytest.mark.asyncio
    async def test_timeout_returns_distinct_sentinel(self):
        mgr = AskQuestionManager()
        result = await mgr.wait_for_answer("never_answered", timeout=0.05)
        # Must be distinguishable from a real (possibly empty) answers dict.
        assert result == "timeout"

    @pytest.mark.asyncio
    async def test_timeout_cleans_up_waiter(self):
        mgr = AskQuestionManager()
        await mgr.wait_for_answer("req_x", timeout=0.05)
        assert mgr.has_live_waiter("req_x") is False


class TestSetAnswerNoWaiter:
    """set_answer with no live waiter must not leak state."""

    def test_set_answer_without_waiter_is_cleaned(self):
        mgr = AskQuestionManager()
        mgr.set_answer("orphan", {"q": "y"})
        # No waiter existed → result is dropped immediately, no leak.
        assert mgr.has_live_waiter("orphan") is False


def test_module_singleton_exists():
    """A module-level singleton is exported (mirrors permission_manager)."""
    assert ask_question_manager is not None
    assert isinstance(ask_question_manager, AskQuestionManager)
