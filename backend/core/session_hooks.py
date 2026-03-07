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
