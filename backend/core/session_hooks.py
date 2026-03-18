"""Session lifecycle hook framework for post-session-close behaviors.

This module provides a general-purpose hook system that fires registered
callbacks when sessions truly close (TTL expiry, explicit delete, backend
shutdown). It is intentionally memory-agnostic — any post-session behavior
can be registered as a hook.

Key public symbols:

- ``HookContext``                   — Frozen dataclass with session metadata
                                      passed to every hook on fire.
- ``SessionLifecycleHook``         — Runtime-checkable Protocol defining the
                                      hook interface (``name`` property +
                                      ``async execute(context)`` method).
- ``SessionLifecycleHookManager``  — Manages registration and sequential,
                                      error-isolated execution of hooks at
                                      session lifecycle events.

Design decisions:
- Hooks execute in registration order so dependencies can be expressed
  (e.g., DailyActivity extraction before distillation trigger).
- Each hook is error-isolated: a failing hook logs the error and does not
  prevent subsequent hooks from running.
- Per-hook timeout via ``asyncio.wait_for`` prevents any single hook from
  blocking session cleanup indefinitely.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class HookContext:
    """Immutable context passed to every post-session-close hook.

    Built from ``_active_sessions`` dict (for TTL / shutdown triggers)
    or from the database (for explicit delete trigger).

    Attributes:
        session_id:         The session being closed.
        agent_id:           Agent that owned the session.
        message_count:      Number of messages in the conversation.
        session_start_time: ISO 8601 timestamp of session creation.
        session_title:      Session title for commit messages / headers.
    """

    session_id: str
    agent_id: str
    message_count: int
    session_start_time: str  # ISO 8601
    session_title: str


@runtime_checkable
class SessionLifecycleHook(Protocol):
    """Protocol that all session lifecycle hooks must implement.

    Hooks are registered with ``SessionLifecycleHookManager`` and executed
    sequentially when a ``post_session_close`` event fires.  Each hook
    receives a ``HookContext`` with session metadata.

    Implementors must provide:
    - A ``name`` property returning a human-readable identifier (used in
      log messages).
    - An ``async execute(context)`` method containing the hook logic.
    """

    @property
    def name(self) -> str:
        """Human-readable hook identifier for logging."""
        ...

    async def execute(self, context: HookContext) -> None:
        """Run the hook logic for the given session context."""
        ...


class SessionLifecycleHookManager:
    """Manages registration and execution of session lifecycle hooks.

    Hooks are registered at startup and executed in registration order
    when a ``post_session_close`` event fires.  Each hook is
    error-isolated: a failing hook logs the error and does not prevent
    subsequent hooks from running.

    Args:
        timeout_seconds: Per-hook timeout enforced via
            ``asyncio.wait_for``.  Defaults to 30 seconds.
    """

    def __init__(self, timeout_seconds: float = 30.0) -> None:
        self._hooks: list[SessionLifecycleHook] = []
        self._timeout = timeout_seconds

    def register(self, hook: SessionLifecycleHook) -> None:
        """Register a hook.  Hooks execute in registration order."""
        self._hooks.append(hook)

    async def fire_post_session_close(self, context: HookContext) -> None:
        """Execute all registered hooks for a session close event.

        Each hook runs in sequence.  If a hook raises, the error is
        logged and execution continues with the next hook.  Per-hook
        timeout is enforced via ``asyncio.wait_for``.
        """
        for hook in self._hooks:
            try:
                await asyncio.wait_for(
                    hook.execute(context),
                    timeout=self._timeout,
                )
                logger.info(
                    "Hook '%s' completed for session %s",
                    hook.name,
                    context.session_id,
                )
            except asyncio.TimeoutError:
                logger.error(
                    "Hook '%s' timed out for session %s",
                    hook.name,
                    context.session_id,
                )
            except Exception as exc:
                logger.error(
                    "Hook '%s' failed for session %s: %s",
                    hook.name,
                    context.session_id,
                    exc,
                    exc_info=True,
                )


class _HookWorkItem:
    """Internal work item for the hook serialization queue.

    Each item represents either a full hook chain (``fire()``) or a
    single hook (``fire_single()``).  The worker processes items one
    at a time, guaranteeing no two hook executions overlap.
    """

    __slots__ = ("context", "skip_hooks", "single_hook", "single_timeout")

    def __init__(
        self,
        context: HookContext,
        skip_hooks: list[str] | None = None,
        single_hook: SessionLifecycleHook | None = None,
        single_timeout: float = 30.0,
    ) -> None:
        self.context = context
        self.skip_hooks = skip_hooks
        self.single_hook = single_hook
        self.single_timeout = single_timeout


class BackgroundHookExecutor:
    """Fire-and-forget hook execution, serialized through a single queue.

    All hook work — whether a full hook chain (``fire()``) or a single
    hook (``fire_single()``) — is enqueued and processed one at a time
    by a single background worker.  This prevents concurrent hook runs
    across sessions, eliminating race conditions on shared resources
    (git index, DailyActivity files, EVOLUTION_CHANGELOG).

    The chat path (session cleanup, shutdown, idle check) never blocks
    waiting for hook completion — ``fire()`` / ``fire_single()`` return
    immediately after enqueuing.

    A shared ``git_lock`` serializes git operations within hooks that
    need it (e.g. ``WorkspaceAutoCommitHook``).

    On shutdown, ``drain()`` signals the worker to stop, gives pending
    hooks a bounded grace period, then cancels stragglers.

    Key design decisions:
    - ``fire()`` / ``fire_single()`` enqueue and return immediately —
      caller is never blocked.
    - A single ``_worker`` task processes items sequentially — no two
      hook executions overlap across sessions.
    - Each item is fully error-isolated — a failing hook does not
      affect the chat experience or other hooks.
    - ``drain()`` is only called once during shutdown, with a bounded
      timeout.  Hooks that don't finish are cancelled — they are
      designed to be idempotent and will retry next session/startup.
    """

    QUEUE_MAX_SIZE: int = 100

    def __init__(self, hook_manager: SessionLifecycleHookManager) -> None:
        self._hook_manager = hook_manager
        self._pending: set[asyncio.Task] = set()
        self._git_lock = asyncio.Lock()
        self._queue: asyncio.Queue[_HookWorkItem | None] = asyncio.Queue(
            maxsize=self.QUEUE_MAX_SIZE
        )
        self._worker_task: asyncio.Task | None = None
        self._started = False

    def start(self) -> None:
        """Start the background worker that processes the hook queue.

        Must be called once after construction (typically at app startup).
        Safe to call multiple times — subsequent calls are no-ops.
        """
        if self._started:
            return
        self._started = True
        self._worker_task = asyncio.create_task(
            self._worker(), name="hook-queue-worker"
        )

    def _ensure_started(self) -> None:
        """Auto-start the worker on first enqueue if not already started.

        This provides backward compatibility — callers that don't
        explicitly call ``start()`` still get a working executor.
        """
        if not self._started:
            self.start()

    @property
    def hooks(self) -> list[SessionLifecycleHook]:
        """Access registered hooks (for selective firing)."""
        return self._hook_manager._hooks

    @property
    def git_lock(self) -> asyncio.Lock:
        """Shared lock for serializing git operations across hooks."""
        return self._git_lock

    def fire(self, context: HookContext, skip_hooks: list[str] | None = None) -> None:
        """Enqueue all hooks for serialized background execution.

        Returns immediately — the caller is never blocked.  The work
        item is processed by the single background worker, ensuring
        hooks from different sessions do not run concurrently.

        Args:
            context: Session metadata for the hooks.
            skip_hooks: Optional list of hook names to skip (e.g.
                ``["daily_activity_extraction"]`` when activity was
                already extracted by the idle trigger).
        """
        self._ensure_started()
        item = _HookWorkItem(context=context, skip_hooks=skip_hooks)
        try:
            self._queue.put_nowait(item)
        except asyncio.QueueFull:
            logger.warning(
                "Hook queue full (%d items) — dropping hooks for session %s",
                self.QUEUE_MAX_SIZE,
                context.session_id,
            )

    def fire_single(
        self,
        hook: SessionLifecycleHook,
        context: HookContext,
        timeout: float = 30.0,
    ) -> None:
        """Enqueue a single hook for serialized background execution.

        Returns immediately — the caller is never blocked.
        """
        self._ensure_started()
        item = _HookWorkItem(
            context=context, single_hook=hook, single_timeout=timeout
        )
        try:
            self._queue.put_nowait(item)
        except asyncio.QueueFull:
            logger.warning(
                "Hook queue full (%d items) — dropping hook '%s' for session %s",
                self.QUEUE_MAX_SIZE,
                hook.name,
                context.session_id,
            )

    @property
    def pending_count(self) -> int:
        """Number of hook items queued plus any currently executing.

        Includes: items waiting in the queue + 1 if the worker is
        actively processing an item (worker task alive and queue was
        non-empty when it last dequeued).
        """
        queued = self._queue.qsize()
        worker_active = (
            self._worker_task is not None
            and not self._worker_task.done()
        )
        # If worker is active, it's either processing an item or waiting
        # for the next one.  Count it as 1 additional pending item when
        # there are queued items or the worker just started.
        return queued + (1 if worker_active else 0)

    async def drain(self, timeout: float = 10.0) -> tuple[int, int]:
        """Signal the worker to stop and wait for pending hooks.

        Called once during shutdown.  Returns ``(completed, cancelled)``
        counts.  Most hooks are idempotent and will naturally retry on
        next session close (auto-commit, distillation, evolution).
        DailyActivity extraction is NOT idempotent — a cancelled
        extraction means that session's summary is permanently lost.
        """
        # Send sentinel to tell worker to exit after current queue
        try:
            self._queue.put_nowait(None)
        except asyncio.QueueFull:
            # Queue is full — force-cancel the worker
            pass

        tasks_to_wait: set[asyncio.Task] = set()
        if self._worker_task and not self._worker_task.done():
            tasks_to_wait.add(self._worker_task)
        tasks_to_wait.update(self._pending)

        if not tasks_to_wait:
            return (0, 0)

        logger.info(
            "Draining hook executor (queue=%d, in-flight=%d, timeout=%ds)",
            self._queue.qsize(),
            len(self._pending),
            timeout,
        )

        done, still_pending = await asyncio.wait(
            tasks_to_wait, timeout=timeout
        )

        for task in still_pending:
            task.cancel()

        # Brief wait for cancellation to propagate
        if still_pending:
            await asyncio.wait(still_pending, timeout=2.0)

        logger.info(
            "Hook drain complete: %d done, %d cancelled",
            len(done),
            len(still_pending),
        )
        return (len(done), len(still_pending))

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _worker(self) -> None:
        """Single background worker that processes hook items sequentially.

        Runs forever until a ``None`` sentinel is received (from
        ``drain()``) or the task is cancelled.  Each item is executed
        in its own error-isolated wrapper — a failing hook never
        crashes the worker.
        """
        logger.info("Hook queue worker started")
        try:
            while True:
                item = await self._queue.get()
                if item is None:
                    # Sentinel — drain remaining items then exit
                    await self._drain_remaining()
                    break
                await self._process_item(item)
                self._queue.task_done()
        except asyncio.CancelledError:
            logger.info("Hook queue worker cancelled (shutdown)")
            return
        except Exception as exc:
            logger.error("Hook queue worker crashed: %s", exc, exc_info=True)
        finally:
            logger.info("Hook queue worker stopped")

    async def _drain_remaining(self) -> None:
        """Process any remaining items in the queue after sentinel."""
        while not self._queue.empty():
            try:
                item = self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            if item is None:
                continue
            await self._process_item(item)
            self._queue.task_done()

    async def _process_item(self, item: _HookWorkItem) -> None:
        """Execute a single work item (full chain or single hook)."""
        if item.single_hook is not None:
            await self._run_single_safe(
                item.single_hook, item.context, item.single_timeout
            )
        else:
            await self._run_all_safe(item.context, item.skip_hooks)

    async def _run_all_safe(
        self,
        context: HookContext,
        skip_hooks: list[str] | None = None,
    ) -> None:
        """Run hooks sequentially with full error isolation.

        Adds duration tracking and explicit CancelledError handling
        for observability during shutdown drain.  Since Python 3.9,
        CancelledError is a BaseException — the existing ``except
        Exception`` does NOT catch it, so cancellation already works
        correctly.  The explicit handler adds logging only.
        """
        skip_set = set(skip_hooks) if skip_hooks else set()
        t0 = time.monotonic()
        completed = 0
        try:
            for hook in self._hook_manager._hooks:
                if hook.name in skip_set:
                    continue
                try:
                    await asyncio.wait_for(
                        hook.execute(context),
                        timeout=self._hook_manager._timeout,
                    )
                    completed += 1
                    logger.info(
                        "Background hook '%s' completed for session %s",
                        hook.name,
                        context.session_id,
                    )
                except asyncio.TimeoutError:
                    logger.error(
                        "Background hook '%s' timed out for session %s",
                        hook.name,
                        context.session_id,
                    )
                except asyncio.CancelledError:
                    logger.info(
                        "Background hook '%s' cancelled for session %s (shutdown)",
                        hook.name,
                        context.session_id,
                    )
                    raise  # Re-raise to let task cancellation propagate
                except Exception as exc:
                    logger.error(
                        "Background hook '%s' failed for session %s: %s",
                        hook.name,
                        context.session_id,
                        exc,
                        exc_info=True,
                    )
        except asyncio.CancelledError:
            elapsed = time.monotonic() - t0
            logger.info(
                "Hook task cancelled for session %s after %.1fs (%d hooks completed)",
                context.session_id,
                elapsed,
                completed,
            )
            return
        elapsed = time.monotonic() - t0
        logger.info(
            "All hooks completed for session %s in %.1fs (%d hooks)",
            context.session_id,
            elapsed,
            completed,
        )

    async def _run_single_safe(
        self,
        hook: SessionLifecycleHook,
        context: HookContext,
        timeout: float,
    ) -> None:
        """Run a single hook with timeout and error isolation."""
        try:
            await asyncio.wait_for(hook.execute(context), timeout=timeout)
            logger.info(
                "Background hook '%s' completed for session %s",
                hook.name,
                context.session_id,
            )
        except asyncio.TimeoutError:
            logger.error(
                "Background hook '%s' timed out for session %s",
                hook.name,
                context.session_id,
            )
        except asyncio.CancelledError:
            logger.info(
                "Background hook '%s' cancelled for session %s (shutdown)",
                hook.name,
                context.session_id,
            )
            raise  # Re-raise to let task cancellation propagate
        except Exception as exc:
            logger.error(
                "Background hook '%s' failed for session %s: %s",
                hook.name,
                context.session_id,
                exc,
                exc_info=True,
            )
