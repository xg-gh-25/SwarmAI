"""Tests for DDD Cultivation v2 Event-Driven Dispatcher.

Covers:
- CultivationEvent creation and ordering
- EventDispatcher emit, dedup, queue overflow
- ChannelExecutor priority sorting, timeout enforcement, budget cap
- Orchestrator event subscription filtering
"""
import asyncio
import time
from datetime import datetime

import pytest

from core.cultivation_dispatcher import (
    ChannelExecutor,
    ChannelTask,
    CultivationEvent,
    EventDispatcher,
    EventType,
    emit_cultivation_event,
    emit_cultivation_event_threadsafe,
    get_dispatcher,
)


# ── CultivationEvent ──────────────────────────────────────────────────────


class TestCultivationEvent:
    def test_create_event(self):
        event = CultivationEvent(
            type=EventType.GIT_COMMIT,
            source="auto_commit_hook",
            payload={"files": ["a.py"]},
            priority=2,
        )
        assert event.type == EventType.GIT_COMMIT
        assert event.source == "auto_commit_hook"
        assert event.priority == 2
        assert isinstance(event.timestamp, datetime)

    def test_event_priority_comparison(self):
        high = CultivationEvent(type=EventType.SESSION_CLOSE, source="hook", payload={}, priority=1)
        low = CultivationEvent(type=EventType.TIMER_30MIN, source="lifecycle", payload={}, priority=3)
        # Lower number = higher priority
        assert high.priority < low.priority

    def test_all_event_types_exist(self):
        expected = {
            "git_commit", "daily_activity", "signal_digest",
            "code_intel_indexed", "session_close", "proposal_decided", "timer_30min",
        }
        actual = {e.value for e in EventType}
        assert expected == actual


# ── EventDispatcher ────────────────────────────────────────────────────────


class TestEventDispatcher:
    @pytest.fixture
    def dispatcher(self):
        return EventDispatcher(queue_size=5, dedup_window_seconds=2.0)

    @pytest.mark.asyncio
    async def test_emit_enqueues_event(self, dispatcher):
        event = CultivationEvent(
            type=EventType.GIT_COMMIT, source="test", payload={}, priority=2,
        )
        result = await dispatcher.emit(event)
        assert result is True
        assert dispatcher.queue.qsize() == 1

    @pytest.mark.asyncio
    async def test_dedup_within_window(self, dispatcher):
        event1 = CultivationEvent(
            type=EventType.GIT_COMMIT, source="test", payload={"a": 1}, priority=2,
        )
        event2 = CultivationEvent(
            type=EventType.GIT_COMMIT, source="test", payload={"b": 2}, priority=2,
        )
        await dispatcher.emit(event1)
        result = await dispatcher.emit(event2)
        # Second same-type event within dedup window → dropped
        assert result is False
        assert dispatcher.queue.qsize() == 1

    @pytest.mark.asyncio
    async def test_different_types_not_deduped(self, dispatcher):
        e1 = CultivationEvent(type=EventType.GIT_COMMIT, source="test", payload={}, priority=2)
        e2 = CultivationEvent(type=EventType.SESSION_CLOSE, source="test", payload={}, priority=1)
        await dispatcher.emit(e1)
        result = await dispatcher.emit(e2)
        assert result is True
        assert dispatcher.queue.qsize() == 2

    @pytest.mark.asyncio
    async def test_dedup_expires_after_window(self, dispatcher):
        # Use a very short dedup window for testing
        dispatcher._dedup_window = 0.1
        event1 = CultivationEvent(type=EventType.GIT_COMMIT, source="test", payload={}, priority=2)
        await dispatcher.emit(event1)
        await asyncio.sleep(0.15)  # Wait for dedup window to expire
        event2 = CultivationEvent(type=EventType.GIT_COMMIT, source="test", payload={}, priority=2)
        result = await dispatcher.emit(event2)
        assert result is True
        assert dispatcher.queue.qsize() == 2

    @pytest.mark.asyncio
    async def test_queue_overflow_drops_event(self, dispatcher):
        # Fill queue to capacity (5)
        for i in range(5):
            event = CultivationEvent(
                type=EventType(["git_commit", "daily_activity", "signal_digest",
                                "code_intel_indexed", "session_close"][i]),
                source="test", payload={}, priority=2,
            )
            await dispatcher.emit(event)

        assert dispatcher.queue.qsize() == 5

        # 6th event should be dropped
        overflow_event = CultivationEvent(
            type=EventType.TIMER_30MIN, source="test", payload={}, priority=3,
        )
        result = await dispatcher.emit(overflow_event)
        assert result is False
        assert dispatcher.dropped_count == 1

    @pytest.mark.asyncio
    async def test_drain_returns_priority_sorted(self, dispatcher):
        # Enqueue in reverse priority order
        low = CultivationEvent(type=EventType.TIMER_30MIN, source="t", payload={}, priority=3)
        high = CultivationEvent(type=EventType.SESSION_CLOSE, source="t", payload={}, priority=1)
        mid = CultivationEvent(type=EventType.GIT_COMMIT, source="t", payload={}, priority=2)
        await dispatcher.emit(low)
        await dispatcher.emit(high)
        await dispatcher.emit(mid)

        events = await dispatcher.drain()
        priorities = [e.priority for e in events]
        assert priorities == [1, 2, 3]


# ── ChannelExecutor ────────────────────────────────────────────────────────


class TestChannelExecutor:
    @pytest.fixture
    def executor(self):
        return ChannelExecutor(max_concurrent=2, total_budget=5.0)

    def _make_task(self, name: str, priority: int, budget: float, duration: float = 0.0):
        """Create a ChannelTask with a mock function that sleeps for `duration`."""
        def channel_fn(root, ws_path):
            time.sleep(duration)
            return [f"finding_from_{name}"]
        return ChannelTask(
            name=name, priority=priority, budget=budget,
            fn=channel_fn, root=None, ws_path="",
        )

    @pytest.mark.asyncio
    async def test_executes_in_priority_order(self, executor):
        execution_order = []

        def make_fn(name):
            def fn(root, ws_path):
                execution_order.append(name)
                return []
            return fn

        tasks = [
            ChannelTask(name="low", priority=3, budget=2.0, fn=make_fn("low"), root=None, ws_path=""),
            ChannelTask(name="high", priority=1, budget=2.0, fn=make_fn("high"), root=None, ws_path=""),
            ChannelTask(name="mid", priority=2, budget=2.0, fn=make_fn("mid"), root=None, ws_path=""),
        ]

        await executor.execute_batch(tasks)
        assert execution_order == ["high", "mid", "low"]

    @pytest.mark.asyncio
    async def test_timeout_enforcement(self, executor):
        """Channel exceeding budget gets cancelled."""
        def slow_fn(root, ws_path):
            time.sleep(3.0)  # Exceeds 1s budget
            return ["should_not_appear"]

        tasks = [
            ChannelTask(name="slow", priority=1, budget=0.3, fn=slow_fn, root=None, ws_path=""),
        ]

        findings = await executor.execute_batch(tasks)
        # Should have a timeout finding
        assert any("CHANNEL_TIMEOUT" in f for f in findings)
        assert not any("should_not_appear" in f for f in findings)

    @pytest.mark.asyncio
    async def test_total_budget_enforcement(self):
        """Total budget cap stops executing remaining channels."""
        executor = ChannelExecutor(max_concurrent=2, total_budget=1.0)
        execution_order = []

        def make_fn(name, duration):
            def fn(root, ws_path):
                time.sleep(duration)
                execution_order.append(name)
                return []
            return fn

        tasks = [
            ChannelTask(name="ch1", priority=1, budget=2.0, fn=make_fn("ch1", 0.6), root=None, ws_path=""),
            ChannelTask(name="ch2", priority=2, budget=2.0, fn=make_fn("ch2", 0.6), root=None, ws_path=""),
            ChannelTask(name="ch3", priority=3, budget=2.0, fn=make_fn("ch3", 0.1), root=None, ws_path=""),
        ]

        findings = await executor.execute_batch(tasks)
        # ch3 should be skipped due to total budget exceeded
        assert "ch1" in execution_order
        assert "ch3" not in execution_order
        assert any("BUDGET_EXCEEDED" in f for f in findings)

    @pytest.mark.asyncio
    async def test_channel_exception_captured(self, executor):
        """Channel raising an exception doesn't kill executor."""
        def failing_fn(root, ws_path):
            raise ValueError("boom")

        tasks = [
            ChannelTask(name="bad", priority=1, budget=2.0, fn=failing_fn, root=None, ws_path=""),
        ]

        findings = await executor.execute_batch(tasks)
        assert any("CHANNEL_ERROR" in f and "boom" in f for f in findings)

    @pytest.mark.asyncio
    async def test_collects_findings_from_all(self, executor):
        """Findings from multiple channels are merged."""
        def fn_a(root, ws_path):
            return ["finding_a"]

        def fn_b(root, ws_path):
            return ["finding_b"]

        tasks = [
            ChannelTask(name="a", priority=1, budget=2.0, fn=fn_a, root=None, ws_path=""),
            ChannelTask(name="b", priority=2, budget=2.0, fn=fn_b, root=None, ws_path=""),
        ]

        findings = await executor.execute_batch(tasks)
        assert "finding_a" in findings
        assert "finding_b" in findings


# ── Orchestrator Integration ───────────────────────────────────────────────


class TestOrchestratorSubscriptions:
    def test_channels_have_subscriptions(self):
        from core.ddd_orchestrator import DddCultivationOrchestrator
        orch = DddCultivationOrchestrator()
        # Every channel should have a non-empty event subscription set
        for name, _fn, events in orch.channels:
            assert len(events) > 0, f"Channel {name} has no event subscriptions"
            assert all(isinstance(e, EventType) for e in events)

    def test_session_close_subscribers(self):
        from core.ddd_orchestrator import DddCultivationOrchestrator
        orch = DddCultivationOrchestrator()
        session_close_channels = [
            name for name, _fn, events in orch.channels
            if EventType.SESSION_CLOSE in events
        ]
        # Only a subset should fire on session_close (not every channel).
        # (ddd_knowledge_injection + entity_index_validation were removed
        # 2026-08-14 with the in-prompt index deletion.)
        assert len(session_close_channels) < len(orch.channels)
        assert "auto_apply_proposals" in session_close_channels
        assert "entry_lifecycle" in session_close_channels

    def test_git_commit_subscribers(self):
        from core.ddd_orchestrator import DddCultivationOrchestrator
        orch = DddCultivationOrchestrator()
        git_channels = [
            name for name, _fn, events in orch.channels
            if EventType.GIT_COMMIT in events
        ]
        assert "ddd_staleness" in git_channels
        assert "knowledge_staleness" in git_channels

    def test_get_tasks_for_event_filters_correctly(self):
        from core.ddd_orchestrator import DddCultivationOrchestrator
        from pathlib import Path
        orch = DddCultivationOrchestrator()
        tasks = orch.get_tasks_for_event(
            EventType.SIGNAL_DIGEST, root=Path("/tmp"), ws_path="/tmp"
        )
        # Only signal_ddd_bridge should respond to SIGNAL_DIGEST
        assert len(tasks) == 1
        assert tasks[0].name == "signal_ddd_bridge"


# ── Singleton + Convenience API ────────────────────────────────────────────


class TestSingletonDispatcher:
    def setup_method(self):
        """Reset module singleton between tests.

        Must also clear the dedup state to ensure independent tests.
        """
        import core.cultivation_dispatcher as mod
        mod._dispatcher = None

    def _ensure_dispatcher_in_loop(self):
        """Force dispatcher re-creation within the running async loop context.

        On Python 3.11, asyncio.Queue() must be created while the event loop
        is running. Calling get_dispatcher() from a sync setup_method creates
        the Queue outside the loop context — put() then silently fails on CI.

        We unconditionally re-create the dispatcher here so the Queue is
        always bound to the current running loop (works on both 3.11 and 3.12).
        """
        import core.cultivation_dispatcher as mod
        mod._dispatcher = EventDispatcher(queue_size=50, dedup_window_seconds=60.0)

    def test_get_dispatcher_returns_same_instance(self):
        d1 = get_dispatcher()
        d2 = get_dispatcher()
        assert d1 is d2

    def test_get_dispatcher_has_correct_defaults(self):
        d = get_dispatcher()
        assert d.queue.maxsize == 50
        assert d._dedup_window == 60.0

    @pytest.mark.asyncio
    async def test_emit_cultivation_event_convenience(self):
        self._ensure_dispatcher_in_loop()
        result = await emit_cultivation_event(
            EventType.GIT_COMMIT,
            source="test",
            payload={"test": True},
            priority=2,
        )
        assert result is True
        # Verify it went into the singleton queue
        d = get_dispatcher()
        assert d.queue.qsize() == 1

    @pytest.mark.asyncio
    async def test_emit_convenience_dedup(self):
        self._ensure_dispatcher_in_loop()
        await emit_cultivation_event(EventType.TIMER_30MIN, source="test")
        result = await emit_cultivation_event(EventType.TIMER_30MIN, source="test2")
        assert result is False  # Deduped

    @pytest.mark.asyncio
    async def test_emit_different_types_not_deduped(self):
        self._ensure_dispatcher_in_loop()
        await emit_cultivation_event(EventType.GIT_COMMIT, source="a")
        result = await emit_cultivation_event(EventType.DAILY_ACTIVITY, source="b")
        assert result is True

    @pytest.mark.asyncio
    async def test_emit_captures_loop_on_first_call(self):
        self._ensure_dispatcher_in_loop()
        d = get_dispatcher()
        assert d.loop is None  # Not yet captured
        await emit_cultivation_event(EventType.SIGNAL_DIGEST, source="test")
        assert d.loop is not None  # Now captured

    @pytest.mark.asyncio
    async def test_threadsafe_emit_from_thread(self):
        """emit_cultivation_event_threadsafe works from a background thread."""
        import asyncio
        self._ensure_dispatcher_in_loop()
        # First, warm up the dispatcher so it has a loop reference
        await emit_cultivation_event(EventType.PROPOSAL_DECIDED, source="warmup")
        d = get_dispatcher()
        initial_size = d.queue.qsize()

        # Now emit from a thread (simulating to_thread context)
        def thread_fn():
            emit_cultivation_event_threadsafe(
                EventType.CODE_INTEL_INDEXED,
                source="thread_test",
                payload={"test": True},
            )

        await asyncio.to_thread(thread_fn)
        # Give the event loop a chance to process the scheduled coroutine
        await asyncio.sleep(0.1)
        assert d.queue.qsize() > initial_size
