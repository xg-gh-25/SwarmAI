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

    async def emit(self, event: CultivationEvent) -> bool:
        """Enqueue an event for processing.

        Returns True if enqueued, False if deduplicated or dropped (overflow).
        """
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
                    asyncio.to_thread(task.fn, task.root, task.ws_path),
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
