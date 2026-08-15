"""DDD Cultivation v2 — Event-Driven Dispatcher.

Replaces batch-on-close with event-driven channel execution. Events flow from
hooks/lifecycle → dispatcher → subscribed channels. Priority queue + dedup +
per-channel timeout ensures responsive, bounded execution.

Public symbols:
    - CultivationEvent    — atomic unit of cultivation work
    - EventType           — enum of all cultivation event types
    - EventDispatcher     — singleton event router with dedup + overflow protection
    - ChannelExecutor     — priority-sorted bounded-concurrency channel runner
    - ChannelTask         — execution unit (channel fn + metadata)
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Callable

from core import executors

logger = logging.getLogger(__name__)


# ── Event Types ────────────────────────────────────────────────────────────


class EventType(Enum):
    """All cultivation event types that can trigger channel execution."""
    GIT_COMMIT = "git_commit"
    DAILY_ACTIVITY = "daily_activity"
    SIGNAL_DIGEST = "signal_digest"
    CODE_INTEL_INDEXED = "code_intel_indexed"
    SESSION_CLOSE = "session_close"
    PROPOSAL_DECIDED = "proposal_decided"
    TIMER_30MIN = "timer_30min"


# ── Event Dataclass ────────────────────────────────────────────────────────


@dataclass
class CultivationEvent:
    """Atomic unit of DDD cultivation work.

    Args:
        type: What kind of event triggered this
        source: Who emitted (hook name, module name)
        payload: Event-specific data (files changed, paths, etc.)
        priority: 0=critical, 1=high, 2=normal, 3=low
        timestamp: When emitted (auto-set if not provided)
    """
    type: EventType
    source: str
    payload: dict[str, Any]
    priority: int = 2
    timestamp: datetime = field(default_factory=datetime.now)


# ── Channel Task ───────────────────────────────────────────────────────────


@dataclass
class ChannelTask:
    """Execution unit for a cultivation channel.

    Wraps a channel function with scheduling metadata (priority, budget).
    """
    name: str
    priority: int
    budget: float  # seconds
    fn: Callable[[Path | None, str], list[str]]
    root: Path | None
    ws_path: str


# ── Event Dispatcher ───────────────────────────────────────────────────────


class EventDispatcher:
    """Routes cultivation events to subscribed channels with dedup + overflow.

    Features:
        - Deduplication: same event type within window → drop
        - Overflow protection: bounded queue, drops with warning
        - Priority drain: events returned sorted by priority
    """

    def __init__(
        self,
        queue_size: int = 50,
        dedup_window_seconds: float = 60.0,
    ) -> None:
        self.queue: asyncio.Queue[CultivationEvent] = asyncio.Queue(maxsize=queue_size)
        self._dedup_window = dedup_window_seconds
        self._last_emit: dict[str, float] = {}  # event_type.value → timestamp
        self.dropped_count: int = 0
        # Store event loop reference for thread-safe emission.
        # Captured lazily on first async emit (when we know a loop is running).
        self.loop: asyncio.AbstractEventLoop | None = None

    async def emit(self, event: CultivationEvent) -> bool:
        """Enqueue an event for processing.

        Returns True if enqueued, False if deduplicated or dropped (overflow).
        """
        # Lazily capture event loop on first async emit
        if self.loop is None:
            try:
                self.loop = asyncio.get_running_loop()
            except RuntimeError:
                pass

        # Dedup check: same event type within window
        now = time.monotonic()
        dedup_key = event.type.value
        last_time = self._last_emit.get(dedup_key, 0.0)
        if (now - last_time) < self._dedup_window:
            logger.debug(
                "cultivation_dispatcher: deduped %s (%.1fs since last)",
                dedup_key, now - last_time,
            )
            return False

        # Queue overflow check
        if self.queue.full():
            self.dropped_count += 1
            logger.warning(
                "cultivation_dispatcher: queue full (%d), dropped %s event "
                "(total dropped: %d)",
                self.queue.maxsize, dedup_key, self.dropped_count,
            )
            return False

        # Enqueue
        await self.queue.put(event)
        self._last_emit[dedup_key] = now
        logger.debug("cultivation_dispatcher: enqueued %s (priority=%d)", dedup_key, event.priority)
        return True

    def emit_nowait(self, event: CultivationEvent) -> bool:
        """Non-yielding enqueue with dedup. Safe to call from sync or async context.

        Same dedup + overflow logic as emit(), but uses put_nowait instead of
        await put. Returns True if enqueued, False if deduped/dropped.
        """
        now = time.monotonic()
        dedup_key = event.type.value
        last_time = self._last_emit.get(dedup_key, 0.0)
        if (now - last_time) < self._dedup_window:
            return False

        if self.queue.full():
            self.dropped_count += 1
            logger.warning(
                "cultivation_dispatcher: queue full (%d), dropped %s event "
                "(total dropped: %d)",
                self.queue.maxsize, dedup_key, self.dropped_count,
            )
            return False

        try:
            self.queue.put_nowait(event)
            self._last_emit[dedup_key] = now
            return True
        except asyncio.QueueFull:
            self.dropped_count += 1
            return False

    async def drain(self) -> list[CultivationEvent]:
        """Drain all queued events, return sorted by priority (0 first).

        Non-blocking: returns immediately with whatever is in the queue.
        """
        events: list[CultivationEvent] = []
        while not self.queue.empty():
            try:
                events.append(self.queue.get_nowait())
            except asyncio.QueueEmpty:
                break
        events.sort(key=lambda e: e.priority)
        return events

    def reset_dedup(self) -> None:
        """Clear dedup history. Useful for testing or manual override."""
        self._last_emit.clear()


# ── Channel Executor ───────────────────────────────────────────────────────


class ChannelExecutor:
    """Executes channel tasks with priority ordering, timeout, and budget.

    Properties:
        - Channels execute in priority order (0 first)
        - Per-channel timeout via asyncio.wait_for wrapping to_thread
        - Total budget cap stops execution when exceeded
        - Exceptions captured as findings (never propagate)

    Note: max_concurrent is accepted for API stability but execution is
    currently sequential (Phase A). Phase B adds asyncio.Semaphore for
    true bounded concurrency when event volume justifies it.
    """

    def __init__(
        self,
        max_concurrent: int = 2,
        total_budget: float = 10.0,
    ) -> None:
        self.max_concurrent = max_concurrent
        self.total_budget = total_budget

    async def execute_batch(self, tasks: list[ChannelTask]) -> list[str]:
        """Execute tasks sorted by priority, respecting budgets.

        Args:
            tasks: Channel tasks to execute

        Returns:
            Merged findings from all channels (including timeout/error notices)

        Note: asyncio.wait_for + to_thread does NOT kill the underlying thread
        on timeout (CPython limitation). Timed-out channels continue in background
        until they return naturally. All channel functions should respect their own
        budget via internal timeouts (e.g., subprocess timeout=N).
        """
        # Sort: lowest priority number first (0=critical)
        tasks.sort(key=lambda t: t.priority)

        findings: list[str] = []
        elapsed = 0.0

        for task in tasks:
            if elapsed >= self.total_budget:
                findings.append(
                    f"BUDGET_EXCEEDED: skipped {task.name} "
                    f"(total elapsed {elapsed:.1f}s >= budget {self.total_budget:.1f}s)"
                )
                logger.info(
                    "cultivation_executor: skipped %s (budget exceeded: %.1fs)",
                    task.name, elapsed,
                )
                continue

            start = time.monotonic()
            try:
                result = await asyncio.wait_for(
                    executors.run_in("io", task.fn, task.root, task.ws_path),
                    timeout=task.budget,
                )
                if result:
                    findings.extend(result)
            except asyncio.TimeoutError:
                findings.append(
                    f"CHANNEL_TIMEOUT: {task.name} exceeded {task.budget:.1f}s budget"
                )
                logger.warning(
                    "cultivation_executor: %s timed out (budget=%.1fs)",
                    task.name, task.budget,
                )
            except Exception as exc:
                findings.append(
                    f"CHANNEL_ERROR: {task.name} — {type(exc).__name__}: {exc}"
                )
                logger.warning(
                    "cultivation_executor: %s raised %s: %s",
                    task.name, type(exc).__name__, exc,
                )
            elapsed += time.monotonic() - start

        return findings


# ── Module-Level Singleton ─────────────────────────────────────────────────

# Shared dispatcher instance. All event sources import and use this.
# Initialized once at module import. Reset via get_dispatcher().reset_dedup()
# for testing.
_dispatcher: EventDispatcher | None = None


def get_dispatcher() -> EventDispatcher:
    """Get or create the module-level singleton EventDispatcher.

    Lazy initialization ensures the asyncio event loop exists when first called.
    All hooks/lifecycle modules should use this, NOT construct their own instance.
    """
    global _dispatcher
    if _dispatcher is None:
        _dispatcher = EventDispatcher(queue_size=50, dedup_window_seconds=60.0)
    return _dispatcher


async def emit_cultivation_event(
    event_type: EventType,
    source: str,
    payload: dict[str, Any] | None = None,
    priority: int = 2,
) -> bool:
    """Convenience function to emit a cultivation event (async context).

    Use this from async code (hooks running in the event loop directly).
    For code running in threads (to_thread, ThreadPoolExecutor), use
    emit_cultivation_event_threadsafe() instead.

    Returns True if enqueued, False if deduped/dropped.
    """
    dispatcher = get_dispatcher()
    event = CultivationEvent(
        type=event_type,
        source=source,
        payload=payload or {},
        priority=priority,
    )
    return await dispatcher.emit(event)


def emit_cultivation_event_threadsafe(
    event_type: EventType,
    source: str,
    payload: dict[str, Any] | None = None,
    priority: int = 2,
) -> None:
    """Emit a cultivation event from a thread context (non-async).

    Safe to call from within asyncio.to_thread(), ThreadPoolExecutor, or
    any non-async function. Uses the stored event loop reference on the
    singleton dispatcher to schedule the emit coroutine.

    Note: Fire-and-forget. Does not return whether the event was enqueued.
    Failures are logged at debug level.
    """
    dispatcher = get_dispatcher()
    loop = dispatcher.loop
    if loop is None or loop.is_closed():
        logger.debug(
            "cultivation_dispatcher: threadsafe emit skipped — no loop (event=%s)",
            event_type.value,
        )
        return

    event = CultivationEvent(
        type=event_type,
        source=source,
        payload=payload or {},
        priority=priority,
    )
    try:
        asyncio.run_coroutine_threadsafe(dispatcher.emit(event), loop)
    except RuntimeError:
        # Loop is closed or not running
        logger.debug(
            "cultivation_dispatcher: threadsafe emit failed — loop not running (event=%s)",
            event_type.value,
        )
