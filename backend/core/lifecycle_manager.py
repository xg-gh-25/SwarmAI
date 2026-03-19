"""LifecycleManager — background maintenance, TTL cleanup, and hook serialization.

Single background loop responsible for:
- TTL-based session cleanup (12hr idle → kill)
- Serialized hook execution (auto-commit, daily activity, distillation, evolution)
- Startup orphan reaper (one-shot, kills unowned claude CLI processes)

This module contains ONLY background maintenance logic.  No prompt building,
routing, or subprocess spawn logic lives here.

Public symbols:

- ``LifecycleManager``  — Main class; manages background loop + hooks.

Design reference:
    ``.kiro/specs/multi-session-rearchitecture/design.md`` §4 LifecycleManager
"""
from __future__ import annotations

import asyncio
import logging
import os
import signal
import subprocess
import time
from typing import Optional, TYPE_CHECKING

from .session_unit import SessionState

if TYPE_CHECKING:
    from .session_router import SessionRouter
    from .session_hooks import BackgroundHookExecutor, HookContext

logger = logging.getLogger(__name__)


class LifecycleManager:
    """Centralized background maintenance for all SessionUnits.

    Invariants:

    - ONE background loop (not 5 tiers).
    - Hooks never block the main request path.
    - Hook failure logged, never propagates.
    - Startup orphan reaper runs ONCE at init.
    """

    TTL_SECONDS: int = 43200  # 12 hours
    LOOP_INTERVAL: float = 60.0  # Check every 60 seconds
    IDLE_HOOK_GRACE: float = 120.0  # Fire hooks after 120s idle (grace period)

    def __init__(
        self,
        router: "SessionRouter",
        hook_executor: Optional["BackgroundHookExecutor"] = None,
    ) -> None:
        self._router = router
        self._hook_executor = hook_executor
        self._loop_task: Optional[asyncio.Task] = None
        self._started = False

    # ── Startup / Shutdown ────────────────────────────────────────

    async def start(self) -> None:
        """Start the background loop and run startup orphan reaper.

        Safe to call multiple times — subsequent calls are no-ops.
        """
        if self._started:
            return
        self._started = True
        await self._reap_orphans()
        await self._scan_unprocessed_sessions()
        self._loop_task = asyncio.create_task(
            self._maintenance_loop(), name="lifecycle-manager-loop",
        )
        logger.info("LifecycleManager started (TTL=%ds, interval=%.0fs)",
                     self.TTL_SECONDS, self.LOOP_INTERVAL)

    async def stop(self) -> None:
        """Stop the background loop. Drain pending hooks via executor."""
        if self._loop_task and not self._loop_task.done():
            self._loop_task.cancel()
            try:
                await self._loop_task
            except asyncio.CancelledError:
                pass
        if self._hook_executor:
            await self._hook_executor.drain(timeout=10.0)
        logger.info("LifecycleManager stopped")

    # ── Hook enqueue ──────────────────────────────────────────────

    def enqueue_hooks(self, context: "HookContext") -> None:
        """Enqueue post-session hooks for serialized execution.

        Fire-and-forget — returns immediately. The BackgroundHookExecutor
        processes hooks one at a time in its worker task.
        """
        if self._hook_executor:
            self._hook_executor.fire(context)

    async def _build_hook_context(self, unit) -> Optional["HookContext"]:
        """Build a HookContext from DB for a SessionUnit.

        Returns None if DB lookup fails (logged, never raises).
        Extracted as DRY helper — used by TTL kill, IDLE hooks,
        eviction, shutdown, and dead cleanup.
        """
        try:
            from .session_hooks import HookContext
            from .session_manager import session_manager
            from database import db

            msg_count = await db.messages.count_by_session(unit.session_id)
            session = await session_manager.get_session(unit.session_id)
            return HookContext(
                session_id=unit.session_id,
                agent_id=unit.agent_id,
                message_count=msg_count,
                session_start_time=session.created_at if session else "",
                session_title=session.title if session else "Unknown",
            )
        except Exception as exc:
            logger.warning(
                "Failed to build hook context for %s: %s",
                unit.session_id, exc,
            )
            return None

    async def enqueue_hooks_for_unit(self, unit) -> None:
        """Build HookContext and enqueue hooks for a SessionUnit.

        Public convenience method used by SessionRouter (eviction, shutdown).
        No-op if hook_executor is not wired or context build fails.
        """
        if not self._hook_executor:
            return
        ctx = await self._build_hook_context(unit)
        if ctx:
            self.enqueue_hooks(ctx)

    # ── Background loop ───────────────────────────────────────────

    async def _maintenance_loop(self) -> None:
        """Single background loop: TTL check + health check + IDLE hooks.

        Every LOOP_INTERVAL seconds:
        1. Health check all units (detect dead subprocesses)
        2. Fire hooks for units idle > IDLE_HOOK_GRACE (Gap 2 fix)
        3. Kill units idle > TTL_SECONDS
        4. Clean up DEAD units → COLD (with hook firing)
        """
        logger.info("Maintenance loop started")
        try:
            while True:
                await asyncio.sleep(self.LOOP_INTERVAL)
                try:
                    await self._health_check_all()
                    await self._fire_idle_hooks()
                    await self._check_ttl()
                    await self._cleanup_dead()
                except Exception as exc:
                    logger.error("Maintenance loop error: %s", exc, exc_info=True)
        except asyncio.CancelledError:
            logger.info("Maintenance loop cancelled")

    async def _health_check_all(self) -> None:
        """Health check all alive units. Detect dead subprocesses."""
        for unit in self._router.list_units():
            if unit.is_alive:
                await unit.health_check()

    async def _fire_idle_hooks(self) -> None:
        """Fire hooks for IDLE units past the grace period (Gap 2 fix).

        After a conversation turn completes (STREAMING → IDLE), we wait
        IDLE_HOOK_GRACE seconds before firing hooks. This prevents
        double-firing during rapid back-and-forth messages.

        Uses ``unit._hooks_enqueued`` flag to fire only once per IDLE
        period. The flag is reset when the unit transitions back to
        STREAMING.
        """
        if not self._hook_executor:
            return

        now = time.time()
        for unit in self._router.list_units():
            if unit.state != SessionState.IDLE:
                continue
            if unit._hooks_enqueued:
                continue
            idle_seconds = now - unit.last_used
            if idle_seconds < self.IDLE_HOOK_GRACE:
                continue

            logger.info(
                "lifecycle_manager.idle_hooks session_id=%s idle=%.0fs",
                unit.session_id, idle_seconds,
            )
            ctx = await self._build_hook_context(unit)
            if ctx:
                self.enqueue_hooks(ctx)
                unit._hooks_enqueued = True

    async def _check_ttl(self) -> None:
        """Kill SessionUnits that have been IDLE longer than TTL."""
        now = time.time()
        for unit in self._router.list_units():
            if unit.state == SessionState.IDLE:
                idle_seconds = now - unit.last_used
                if idle_seconds > self.TTL_SECONDS:
                    logger.info(
                        "lifecycle_manager.ttl_kill session_id=%s idle=%.0fs",
                        unit.session_id, idle_seconds,
                    )
                    # Hooks may already have fired via _fire_idle_hooks.
                    # Only fire again if they haven't (e.g., executor wired late).
                    if not unit._hooks_enqueued:
                        await self.enqueue_hooks_for_unit(unit)
                    await unit.kill()

                    # Clean up system prompt metadata
                    from . import session_registry
                    session_registry.system_prompt_metadata.pop(unit.session_id, None)

    async def _cleanup_dead(self) -> None:
        """Transition DEAD units to COLD. Fire hooks if not yet fired (Gap 3 fix).

        A unit can reach DEAD from a crash (STREAMING → DEAD) without
        going through the normal IDLE hook path. Fire hooks here as a
        last-chance safety net before wiping internal state.
        """
        for unit in self._router.list_units():
            if unit.state == SessionState.DEAD:
                if not unit._hooks_enqueued and self._hook_executor:
                    ctx = await self._build_hook_context(unit)
                    if ctx:
                        self.enqueue_hooks(ctx)
                        unit._hooks_enqueued = True
                unit._cleanup_internal()
                unit._transition(SessionState.COLD)

    # ── Startup unprocessed session scan (Gap 4 fix) ──────────────

    async def _scan_unprocessed_sessions(self) -> None:
        """One-shot startup: find recent sessions that never had hooks fired.

        After a crash, sessions from the previous instance may have messages
        in DB but never got their DailyActivity extraction / auto-commit.
        Check the last 24h of sessions and fire hooks for any with messages
        but no DailyActivity file entry.

        Non-fatal — failures are logged and skipped.
        """
        if not self._hook_executor:
            return
        try:
            from datetime import datetime, timedelta
            from pathlib import Path
            from .session_manager import session_manager
            from database import db
            from config import SWARM_WS_DIR

            cutoff = (datetime.now() - timedelta(hours=24)).isoformat()
            sessions = await session_manager.list_sessions(limit=50)
            if not sessions:
                return

            # Check today's DailyActivity file for already-processed session IDs
            today_str = datetime.now().strftime("%Y-%m-%d")
            da_path = Path(SWARM_WS_DIR) / "Knowledge" / "DailyActivity" / f"{today_str}.md"
            da_content = ""
            if da_path.exists():
                try:
                    da_content = da_path.read_text(encoding="utf-8")
                except Exception:
                    pass

            fired = 0
            for session in sessions:
                created = getattr(session, "created_at", "") or ""
                if created < cutoff:
                    continue  # Too old

                sid = getattr(session, "id", None) or getattr(session, "session_id", None)
                if not sid:
                    continue

                # Skip if already mentioned in today's DailyActivity
                if sid in da_content:
                    continue

                msg_count = await db.messages.count_by_session(sid)
                if msg_count < 2:
                    continue  # No real conversation

                from .session_hooks import HookContext
                ctx = HookContext(
                    session_id=sid,
                    agent_id=getattr(session, "agent_id", "default") or "default",
                    message_count=msg_count,
                    session_start_time=created,
                    session_title=getattr(session, "title", "Unknown") or "Unknown",
                )
                self.enqueue_hooks(ctx)
                fired += 1

            if fired:
                logger.info(
                    "Startup scan: enqueued hooks for %d unprocessed session(s)",
                    fired,
                )
        except Exception as exc:
            logger.warning("Startup unprocessed session scan failed (non-fatal): %s", exc)

    # ── Startup orphan reaper ─────────────────────────────────────

    async def _reap_orphans(self) -> None:
        """One-shot startup: find and kill claude CLI processes not owned
        by any SessionUnit.

        Filters by the bundled CLI binary path (not just process name
        'claude') to avoid killing unrelated processes.  Cross-references
        with router's known PIDs, kills unowned ones.
        """
        try:
            known_pids = {
                u.pid for u in self._router.list_units()
                if u.pid is not None
            }

            # Find claude CLI processes
            result = await asyncio.to_thread(
                subprocess.run,
                ["pgrep", "-f", "claude_agent_sdk/_bundled/claude"],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode != 0:
                return  # No matching processes

            orphan_count = 0
            for line in result.stdout.strip().split("\n"):
                line = line.strip()
                if not line:
                    continue
                try:
                    pid = int(line)
                except ValueError:
                    continue

                # Skip our own process and known session PIDs
                if pid == os.getpid() or pid in known_pids:
                    continue

                try:
                    os.kill(pid, signal.SIGKILL)
                    orphan_count += 1
                    logger.info(
                        "lifecycle_manager.reap_orphan pid=%d", pid,
                    )
                except (ProcessLookupError, PermissionError):
                    pass

            if orphan_count:
                logger.warning(
                    "Startup orphan reaper killed %d claude process(es)",
                    orphan_count,
                )
        except Exception as exc:
            logger.warning("Orphan reaper failed (non-fatal): %s", exc)
