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


class BackgroundHookExecutor:
    """Fire-and-forget hook execution, fully decoupled from the chat path.

    Hooks run as background asyncio.Tasks.  The chat path (session
    cleanup, shutdown, idle check) never blocks waiting for hook
    completion.  On shutdown, ``drain()`` gives pending hooks a
    bounded grace period before cancellation.

    A shared ``git_lock`` serializes all git operations across hooks
    to prevent ``.git/index.lock`` contention when multiple sessions
    close concurrently.

    Key design decisions:
    - ``fire()`` / ``fire_single()`` return immediately — caller is
      never blocked.
    - Each task is fully error-isolated — a failing hook does not
      affect the chat experience or other hooks.
    - ``drain()`` is only called once during shutdown, with a bounded
      timeout.  Hooks that don't finish are cancelled — they are
      designed to be idempotent and will retry next session/startup.
    """

    def __init__(self, hook_manager: SessionLifecycleHookManager) -> None:
        self._hook_manager = hook_manager
        self._pending: set[asyncio.Task] = set()
        self._git_lock = asyncio.Lock()

    @property
    def hooks(self) -> list[SessionLifecycleHook]:
        """Access registered hooks (for selective firing)."""
        return self._hook_manager._hooks

    @property
    def git_lock(self) -> asyncio.Lock:
        """Shared lock for serializing git operations across hooks."""
        return self._git_lock

    def fire(self, context: HookContext, skip_hooks: list[str] | None = None) -> None:
        """Queue all hooks for background execution.  Returns immediately.

        Args:
            context: Session metadata for the hooks.
            skip_hooks: Optional list of hook names to skip (e.g.
                ``["daily_activity_extraction"]`` when activity was
                already extracted by the idle trigger).
        """
        task = asyncio.create_task(
            self._run_all_safe(context, skip_hooks),
            name=f"hooks-{context.session_id[:8]}",
        )
        self._pending.add(task)
        task.add_done_callback(self._pending.discard)

    def fire_single(
        self,
        hook: SessionLifecycleHook,
        context: HookContext,
        timeout: float = 30.0,
    ) -> None:
        """Fire a single hook in the background.  Returns immediately."""
        task = asyncio.create_task(
            self._run_single_safe(hook, context, timeout),
            name=f"hook-{hook.name}-{context.session_id[:8]}",
        )
        self._pending.add(task)
        task.add_done_callback(self._pending.discard)

    @property
    def pending_count(self) -> int:
        """Number of hook tasks currently in flight."""
        return len(self._pending)

    async def drain(self, timeout: float = 10.0) -> tuple[int, int]:
        """Wait for pending hooks to complete; cancel stragglers.

        Called once during shutdown.  Returns ``(completed, cancelled)``
        counts.  Most hooks are idempotent and will naturally retry on
        next session close (auto-commit, distillation, evolution).
        DailyActivity extraction is NOT idempotent — a cancelled
        extraction means that session's summary is permanently lost.
        """
        if not self._pending:
            return (0, 0)

        pending_snapshot = set(self._pending)
        logger.info(
            "Draining %d pending hook tasks (timeout=%ds)",
            len(pending_snapshot),
            timeout,
        )

        done, still_pending = await asyncio.wait(
            pending_snapshot, timeout=timeout
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
