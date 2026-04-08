"""LifecycleManager — background maintenance, TTL cleanup, and hook serialization.

Single background loop responsible for:
- Per-session memory sampling (CLI + MCP tree RSS, peak watermark, 1.5GB warning)
- Streaming timeout watchdog (5min no SDK events → force-unstick)
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
import json
import logging
import os
import signal
import subprocess
import time
from pathlib import Path
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
    STREAMING_TIMEOUT_SECONDS: float = 300.0  # 5 min no SDK events → stuck stream
    STARTUP_BACKLOG_CAP: int = 5  # Max sessions to process on startup scan

    # Memory pressure thresholds (configurable via env vars).
    # SWARMAI_MEMORY_EVICT_PCT: % used → start evicting IDLE sessions
    # SWARMAI_MEMORY_CIRCUIT_BREAKER_PCT: % used → kill heaviest STREAMING session
    MEMORY_EVICT_PCT: float = float(os.environ.get("SWARMAI_MEMORY_EVICT_PCT", "90"))
    MEMORY_CIRCUIT_BREAKER_PCT: float = float(
        os.environ.get("SWARMAI_MEMORY_CIRCUIT_BREAKER_PCT", "95")
    )

    def __init__(
        self,
        router: "SessionRouter",
        hook_executor: Optional["BackgroundHookExecutor"] = None,
    ) -> None:
        self._router = router
        self._hook_executor = hook_executor
        self._loop_task: Optional[asyncio.Task] = None
        self._started = False
        self._tracked_child_pids: set[int] = set()

    # ── Startup / Shutdown ────────────────────────────────────────

    async def start(self) -> None:
        """Start the background loop and run startup orphan reaper.

        Safe to call multiple times — subsequent calls are no-ops.
        Orphan reaping and unprocessed session scan run as background
        tasks to avoid blocking startup (each pgrep call can take up
        to 5s, and with 8+ patterns that's 40s worst case).
        """
        if self._started:
            return
        self._started = True
        # Defer reaping to background — never block startup
        asyncio.create_task(self._startup_background_tasks())
        self._loop_task = asyncio.create_task(
            self._maintenance_loop(), name="lifecycle-manager-loop",
        )
        logger.info("LifecycleManager started (TTL=%ds, interval=%.0fs)",
                     self.TTL_SECONDS, self.LOOP_INTERVAL)

    async def _startup_background_tasks(self) -> None:
        """Run startup orphan reaper and unprocessed session scan in background.

        Non-fatal — failures are logged and skipped.
        """
        try:
            await self._reap_orphans()
        except Exception as exc:
            logger.warning("Startup orphan reap failed (non-fatal): %s", exc)
        try:
            await self._scan_unprocessed_sessions()
        except Exception as exc:
            logger.warning("Startup session scan failed (non-fatal): %s", exc)

    async def stop(self) -> None:
        """Stop the background loop. Kill tracked children. Drain hooks."""
        if self._loop_task and not self._loop_task.done():
            self._loop_task.cancel()
            try:
                await self._loop_task
            except asyncio.CancelledError:
                pass
        await self._kill_tracked_pids()
        if self._hook_executor:
            await self._hook_executor.drain(timeout=10.0)
        logger.info("LifecycleManager stopped")

    # ── Child PID tracking ───────────────────────────────────────

    def track_pid(self, pid: int) -> None:
        """Register a child PID for cleanup at shutdown.

        Called by subsystems that spawn long-running child processes
        (e.g., background jobs, signal fetchers). Tracked PIDs are
        SIGKILL'd during ``stop()`` as a last-resort safety net.
        """
        self._tracked_child_pids.add(pid)

    def untrack_pid(self, pid: int) -> None:
        """Remove a PID from the tracked set (e.g., after normal exit)."""
        self._tracked_child_pids.discard(pid)

    async def _kill_tracked_pids(self) -> None:
        """SIGKILL all tracked child PIDs at shutdown. Best-effort."""
        if not self._tracked_child_pids:
            return
        killed = 0
        for pid in list(self._tracked_child_pids):
            try:
                os.kill(pid, signal.SIGKILL)
                killed += 1
                logger.info("lifecycle_manager.kill_tracked pid=%d", pid)
            except (ProcessLookupError, PermissionError):
                pass  # Already dead or not ours
        self._tracked_child_pids.clear()
        if killed:
            logger.warning(
                "Shutdown: killed %d tracked child process(es)", killed,
            )

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
        2. Sample per-session memory (CLI + MCP tree RSS)
        3. Fire hooks for units idle > IDLE_HOOK_GRACE (Gap 2 fix)
        4. Kill units idle > TTL_SECONDS
        5. Clean up DEAD units → COLD (with hook firing)
        """
        logger.info("Maintenance loop started")
        cycle = 0
        try:
            while True:
                await asyncio.sleep(self.LOOP_INTERVAL)
                cycle += 1
                try:
                    await self._health_check_all()
                    await self._sample_process_memory()
                    await self._proactive_rss_restart()
                    await self._check_streaming_timeout()
                    await self._fire_idle_hooks()
                    await self._check_ttl()
                    await self._cleanup_dead()
                    await self._check_memory_pressure()
                    # Reap orphans every 3rd cycle (~3 min) — was 10th (~10 min).
                    # Reduced after zombie process incident (248% CPU, 2026-04-01).
                    if cycle % 3 == 0:
                        await self._reap_orphans()
                    # Heavier cleanup every 10th cycle (~10 min)
                    if cycle % 10 == 0:
                        await self._purge_stale_cold()
                        await self._cleanup_stale_channel_sessions()
                        await self._cleanup_expired_messages()
                except Exception as exc:
                    logger.error("Maintenance loop error: %s", exc, exc_info=True)
        except asyncio.CancelledError:
            logger.info("Maintenance loop cancelled")

    async def _health_check_all(self) -> None:
        """Health check all alive units. Detect dead subprocesses."""
        for unit in self._router.list_units():
            if unit.is_alive:
                await unit.health_check()

    async def _sample_process_memory(self) -> None:
        """Sample per-session memory (CLI + MCP children) for observability.

        Runs every maintenance cycle (60s).  For each alive unit:
        1. Calls ``resource_monitor.process_tree_rss(pid)`` to get total
           RSS of the CLI subprocess + all its MCP children.
        2. Updates the unit's ``_peak_tree_rss_bytes`` watermark.
        3. Logs a per-session summary line for post-mortem analysis.

        Non-fatal — failures are logged and skipped.  The cost is one
        ``psutil.Process(pid).children(recursive=True)`` per alive unit
        per cycle, which is ~1ms each.

        Note: to_thread calls are intentionally sequential, not parallel.
        At ~1ms per unit in a 60s maintenance loop, the ~4ms total for
        MAX_CONCURRENT=2 units doesn't justify asyncio.gather complexity.
        """
        try:
            from .resource_monitor import resource_monitor

            alive_units = [u for u in self._router.list_units() if u.is_alive and u.pid]
            if not alive_units:
                return

            total_tree_rss = 0
            entries = []

            for unit in alive_units:
                tree_rss = await asyncio.to_thread(
                    resource_monitor.process_tree_rss, unit.pid,
                )
                if tree_rss <= 0:
                    continue

                # Update peak watermark; warn on first 1.5GB crossing.
                # On first sample (peak was 0), record the spawn cost
                # for adaptive spawn budget estimation (G4 fix).
                prev_peak = unit._peak_tree_rss_bytes
                if prev_peak == 0 and tree_rss > 0:
                    resource_monitor.record_spawn_cost(tree_rss)
                if tree_rss > prev_peak:
                    unit._peak_tree_rss_bytes = tree_rss
                    if tree_rss > 1_500_000_000 and prev_peak <= 1_500_000_000:
                        logger.warning(
                            "lifecycle_manager.memory_warning session=%s "
                            "tree_rss=%dMB — crossed 1.5GB threshold",
                            unit.session_id[:8],
                            tree_rss // (1024 * 1024),
                        )

                total_tree_rss += tree_rss
                rss_mb = tree_rss / (1024 * 1024)
                peak_mb = unit._peak_tree_rss_bytes / (1024 * 1024)
                entries.append(
                    f"{unit.session_id[:8]}={rss_mb:.0f}MB"
                    f"(peak={peak_mb:.0f}MB,{unit.state.name})"
                )

            if entries:
                total_mb = total_tree_rss / (1024 * 1024)
                logger.info(
                    "lifecycle_manager.memory_sample total=%dMB sessions=[%s]",
                    int(total_mb),
                    ", ".join(entries),
                )
        except Exception as exc:
            logger.debug("_sample_process_memory failed (non-fatal): %s", exc)

    async def _proactive_rss_restart(self) -> None:
        """Proactive compact→kill for IDLE sessions with high RSS (Trigger A).

        Fallback to the post-turn check in SessionUnit (Trigger B).
        Runs every 60s maintenance cycle.  For each IDLE session:
        1. Check per-unit cooldown (3 minutes between restarts)
        2. Measure process tree RSS
        3. If > 1.2GB: compact → kill → lazy resume on next send()

        Only touches IDLE sessions — STREAMING/WAITING_INPUT are never
        interrupted.  Non-fatal — failures logged and skipped.
        """
        try:
            from .resource_monitor import resource_monitor
            from .session_unit import SessionUnit

            for unit in self._router.list_units():
                if unit.state != SessionState.IDLE:
                    continue
                if not unit.pid:
                    continue
                if (
                    time.monotonic() - unit._last_proactive_restart
                    < SessionUnit.PROACTIVE_COOLDOWN
                ):
                    continue

                tree_rss = await asyncio.to_thread(
                    resource_monitor.process_tree_rss, unit.pid,
                )
                if tree_rss <= SessionUnit.PROACTIVE_RSS_THRESHOLD:
                    continue

                logger.warning(
                    "lifecycle.proactive_rss_restart session=%s "
                    "rss=%dMB > threshold=%dMB — compact → kill → lazy resume",
                    unit.session_id[:8],
                    tree_rss // (1024 * 1024),
                    SessionUnit.PROACTIVE_RSS_THRESHOLD // (1024 * 1024),
                )

                # Fire hooks before killing (same pattern as Tier 1 eviction)
                if not unit._hooks_enqueued and self._hook_executor:
                    ctx = await self._build_hook_context(unit)
                    if ctx:
                        self.enqueue_hooks(ctx)
                        unit._hooks_enqueued = True

                # compact → kill (preserves _sdk_session_id for lazy resume)
                # 30s timeout prevents compact hang from blocking maintenance loop.
                try:
                    await asyncio.wait_for(unit.compact(), timeout=30.0)
                except asyncio.TimeoutError:
                    logger.warning(
                        "lifecycle.proactive_rss_restart compact timed out "
                        "session=%s — proceeding to kill",
                        unit.session_id[:8],
                    )
                except Exception as exc:
                    logger.warning(
                        "lifecycle.proactive_rss_restart compact failed "
                        "session=%s: %s — proceeding to kill",
                        unit.session_id[:8], exc,
                    )
                await unit.kill()
                unit._last_proactive_restart = time.monotonic()
        except Exception as exc:
            logger.debug("_proactive_rss_restart failed (non-fatal): %s", exc)

    async def _check_streaming_timeout(self) -> None:
        """Force-unstick sessions that have been STREAMING with no SDK
        events for longer than ``STREAMING_TIMEOUT_SECONDS``.

        Root cause: The SDK subprocess accepted a query but never
        returned a ``ResultMessage`` — the session state machine stays
        stuck in STREAMING forever, rejecting all subsequent messages
        with "Cannot send() in state streaming".

        Fix: Detect the stall via ``unit.streaming_stall_seconds``,
        kill the subprocess, and transition back to COLD.  The next
        user message will trigger a fresh spawn with ``--resume`` to
        restore conversation context.

        Only touches STREAMING sessions — IDLE, WAITING_INPUT, etc.
        are handled by other maintenance methods.
        """
        for unit in self._router.list_units():
            if unit.state != SessionState.STREAMING:
                continue
            stall = unit.streaming_stall_seconds
            if stall is None:
                continue
            if stall > self.STREAMING_TIMEOUT_SECONDS:
                logger.warning(
                    "lifecycle_manager.streaming_timeout session_id=%s "
                    "stall=%.0fs > timeout=%.0fs — forcing unstick",
                    unit.session_id,
                    stall,
                    self.STREAMING_TIMEOUT_SECONDS,
                )
                await unit.force_unstick_streaming()

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
                        "lifecycle_manager.ttl_kill session_id=%s idle=%.0fs "
                        "peak_rss=%dMB",
                        unit.session_id, idle_seconds,
                        unit._peak_tree_rss_bytes // (1024 * 1024),
                    )
                    # Hooks may already have fired via _fire_idle_hooks.
                    # Only fire again if they haven't (e.g., executor wired late).
                    if not unit._hooks_enqueued:
                        await self.enqueue_hooks_for_unit(unit)
                    await unit.kill()
                    self._release_session_state(unit.session_id)

    @staticmethod
    def _release_session_state(session_id: str) -> None:
        """Release all per-session state outside SessionUnit.

        Called on every session end path (TTL kill, crash→COLD, purge).
        Prevents unbounded growth of module-level dicts that key by session_id.

        Targets:
        - session_registry.system_prompt_metadata  (prompt text, ~50KB each)
        - permission_manager._approved_commands    (command hashes)
        - permission_manager._session_queues       (asyncio.Queue)
        """
        try:
            from . import session_registry
            session_registry.system_prompt_metadata.pop(session_id, None)
        except Exception as exc:
            logger.debug("_release_session_state metadata cleanup failed: %s", exc)
        try:
            from .permission_manager import permission_manager
            permission_manager.clear_session_approvals(session_id)
            permission_manager.remove_session_queue(session_id)
        except Exception as exc:
            logger.debug("_release_session_state permission cleanup failed: %s", exc)

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
                self._release_session_state(unit.session_id)
                unit._cleanup_internal()
                unit._transition(SessionState.COLD)

    # ── Stale COLD unit purge ──────────────────────────────────────

    async def _purge_stale_cold(self) -> None:
        """Remove COLD units idle > 1 hour from the router's unit dict.

        Prevents unbounded growth of the _units dict from sessions that
        were evicted or killed and never returned to.
        """
        now = time.time()
        stale_ids = [
            u.session_id for u in self._router.list_units()
            if u.state == SessionState.COLD
            and (now - u.last_used) > 3600  # 1 hour
        ]
        for sid in stale_ids:
            self._release_session_state(sid)
            self._router._units.pop(sid, None)
        if stale_ids:
            logger.info(
                "lifecycle_manager.purge_stale_cold removed %d stale unit(s)",
                len(stale_ids),
            )

    async def _cleanup_stale_channel_sessions(self) -> None:
        """Delete channel_session rows idle beyond the gateway TTL.

        Without this, stale rows accumulate indefinitely — they only get
        cleaned on the next message from the same user to the same
        conversation.  This sweep runs every ~10 min and removes rows
        that have been idle for >2× the gateway TTL (4 hours), giving
        generous headroom before cleanup.
        """
        try:
            from database import db

            # 2× gateway TTL = 24 hours.  Conservative: avoids racing with
            # a user who comes back just after the 12h mark.
            CLEANUP_TTL_S = 24 * 60 * 60

            stale = await db.channel_sessions.find_stale(CLEANUP_TTL_S)
            if not stale:
                return

            deleted = 0
            for row in stale:
                try:
                    await db.channel_sessions.delete(row["id"])
                    deleted += 1
                except Exception:
                    logger.debug(
                        "Failed to delete stale channel_session %s", row["id"]
                    )

            if deleted:
                logger.info(
                    "lifecycle_manager.channel_session_cleanup "
                    "deleted %d stale row(s) (>%ds idle)",
                    deleted,
                    CLEANUP_TTL_S,
                )
        except Exception as exc:
            logger.debug("Channel session cleanup skipped: %s", exc)

    async def _cleanup_expired_messages(self) -> None:
        """Delete messages past their 7-day TTL.

        Runs every ~10 minutes (cycle % 10 block).  Non-fatal — failures
        are logged and skipped so they never block the maintenance loop.
        """
        try:
            from database import db

            deleted = await db.cleanup_expired_messages()
            if deleted > 0:
                logger.info(
                    "lifecycle_manager.ttl_cleanup deleted=%d expired messages",
                    deleted,
                )
        except Exception as exc:
            logger.warning("lifecycle_manager.ttl_cleanup failed: %s", exc)

    # ── Memory pressure relief ─────────────────────────────────────

    # Two-tier memory thresholds (defaults from MEMORY_EVICT_PCT / MEMORY_CIRCUIT_BREAKER_PCT):
    #  tier 1 → evict IDLE only (gentle — session can resume cheaply)
    #  tier 2 → KILL heaviest STREAMING session (circuit breaker —
    #            sacrificing one session beats macOS killing everything)

    async def _check_memory_pressure(self) -> None:
        """Two-tier memory pressure relief.

        Tier 1 (>85%): Evict ALL IDLE units (heaviest first) until memory
          drops below threshold or no IDLE units remain.  Previous behavior
          evicted only one per 60s cycle — too slow when memory spikes.
        Tier 2 (>92%): Circuit breaker — kill heaviest STREAMING session.
          Losing one streaming session is better than macOS jetsam killing
          the entire app (and losing ALL sessions' in-flight data).

        Non-fatal — failures are logged and skipped.
        """
        try:
            from .resource_monitor import resource_monitor
            mem = resource_monitor.system_memory()
            if mem.percent_used < self.MEMORY_EVICT_PCT:
                return

            def _rss(u) -> int:
                metrics = getattr(u, "_last_metrics", None)
                return metrics.rss_bytes if metrics else 0

            # ── Tier 1: evict IDLE units until headroom restored ────
            # Sort heaviest first, evict in a loop until memory is OK
            # or no IDLE units remain.
            idle_units = sorted(
                [u for u in self._router.list_units()
                 if u.state == SessionState.IDLE],
                key=_rss, reverse=True,
            )
            evicted = 0
            for victim in idle_units:
                logger.warning(
                    "lifecycle.memory_pressure_tier1: %.1f%% — evicting "
                    "IDLE session %s (rss=%dMB) [%d/%d]",
                    mem.percent_used,
                    victim.session_id,
                    _rss(victim) // (1024 * 1024),
                    evicted + 1,
                    len(idle_units),
                )

                if not victim._hooks_enqueued and self._hook_executor:
                    ctx = await self._build_hook_context(victim)
                    if ctx:
                        self.enqueue_hooks(ctx)
                        victim._hooks_enqueued = True

                await victim.kill()
                evicted += 1
                resource_monitor.invalidate_cache()

                # Re-check: if memory dropped below eviction threshold, stop
                mem = resource_monitor.system_memory()
                if mem.percent_used < self.MEMORY_EVICT_PCT:
                    logger.info(
                        "lifecycle.memory_pressure_tier1: pressure relieved "
                        "after evicting %d session(s) (now %.1f%%)",
                        evicted, mem.percent_used,
                    )
                    return

            if evicted > 0:
                return  # Evicted everything we could

            # ── Tier 2: LOG ONLY — never kill STREAMING sessions ────
            # Previous design had a circuit breaker that killed the heaviest
            # STREAMING session at 92%.  Removed: killing STREAMING causes
            # data loss, context truncation, and broken responses.  If we
            # reach this point, all IDLE units are evicted and memory is
            # still high.  Let macOS jetsam decide — our L3 continuation
            # hint in session_unit handles the recovery.  The spawn settle
            # window (L1) prevents over-commitment in the first place.
            if mem.percent_used >= self.MEMORY_CIRCUIT_BREAKER_PCT:
                streaming_count = sum(
                    1 for u in self._router.list_units()
                    if u.state in (SessionState.STREAMING, SessionState.WAITING_INPUT)
                )
                logger.warning(
                    "lifecycle.memory_critical: %.1f%% > %.0f%% with "
                    "%d STREAMING sessions — NOT killing (L1/L3 handles). "
                    "If jetsam kills a subprocess, retry+continuation will recover.",
                    mem.percent_used,
                    self.MEMORY_CIRCUIT_BREAKER_PCT,
                    streaming_count,
                )
        except Exception as exc:
            logger.error("_check_memory_pressure failed: %s", exc)

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
            from .initialization_manager import initialization_manager

            cutoff = (datetime.now() - timedelta(hours=24)).isoformat()
            sessions = await session_manager.list_sessions(limit=50)
            if not sessions:
                return

            # Check today's DailyActivity file for already-processed session IDs
            today_str = datetime.now().strftime("%Y-%m-%d")
            ws_path = Path(initialization_manager.get_cached_workspace_path())
            da_path = ws_path / "Knowledge" / "DailyActivity" / f"{today_str}.md"
            da_content = ""
            if da_path.exists():
                try:
                    da_content = da_path.read_text(encoding="utf-8")
                except Exception:
                    pass

            fired = 0
            # Cap startup backlog to prevent flooding the hook queue.
            # 19+ unprocessed sessions × 7 hooks each = 133 items serial.
            # Process only the most recent — older sessions are less
            # valuable and the next startup will catch them.
            for session in sessions:
                if fired >= self.STARTUP_BACKLOG_CAP:
                    break
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

    # ── Ownership-based orphan detection ──────────────────────────
    #
    # Design: every child process inherits SWARMAI_OWNER_PID=<backend_pid>
    # via os.environ.  The reaper uses this to answer three questions:
    #
    # 1. Is this process MINE?  (SWARMAI_OWNER_PID == my_pid)
    #    → Yes: check if parent is alive.  Dead parent = orphan.
    #    → No:  not my process.  Never touch it.
    #
    # 2. Is this process from a PREVIOUS instance?
    #    (SWARMAI_OWNER_PID set but != my_pid, and that PID is dead)
    #    → Yes: stale orphan from a crashed previous backend.  Kill.
    #
    # 3. No SWARMAI_OWNER_PID at all?
    #    → Not a SwarmAI-managed process.  Never touch it.
    #
    # This eliminates ALL heuristic-based orphan detection (ancestor
    # chain walking, ppid==1 checks) and replaces it with a definitive
    # ownership tag.  No more false positives.

    async def _is_owned_orphan(self, pid: int) -> bool:
        """Check if a process is an orphan owned by this or a previous backend.

        Reads the process's environment to find SWARMAI_OWNER_PID.
        Returns True only if the process is definitively a SwarmAI orphan:
        - Has SWARMAI_OWNER_PID set (it's a SwarmAI child process)
        - The owner PID is dead (the backend that spawned it has exited)

        Returns False (safe — don't kill) if:
        - No SWARMAI_OWNER_PID → not a SwarmAI process
        - Owner PID is alive → legitimate child of a running backend
        - Can't read env → assume not orphan (fail safe)
        """
        try:
            # Read the process's environment via /proc or ps
            owner_pid = await self._read_process_owner_pid(pid)
            if owner_pid is None:
                return False  # No ownership tag → not ours, don't touch

            # If the owner is us, check if the process's direct parent is alive
            # (it should be — if it's not, the process was reparented = orphan)
            my_pid = os.getpid()
            if owner_pid == my_pid:
                # Our child — check if it's in known_pids (active session).
                # If not in known_pids, it's a leaked child from a crashed
                # session within THIS instance.  But we only kill it if its
                # parent is dead (reparented to launchd).
                try:
                    ppid_result = await asyncio.to_thread(
                        subprocess.run,
                        ["ps", "-o", "ppid=", "-p", str(pid)],
                        capture_output=True, text=True, timeout=5,
                    )
                    ppid = int(ppid_result.stdout.strip())
                    return ppid == 1  # Reparented to launchd = orphan
                except (ValueError, subprocess.TimeoutExpired):
                    return False

            # Owner is a different PID — check if that backend is still alive
            try:
                os.kill(owner_pid, 0)  # Signal 0 = existence check
                return False  # Owner is alive → legitimate child
            except ProcessLookupError:
                return True  # Owner is dead → orphan from previous instance
            except PermissionError:
                return False  # Can't check → assume alive (fail safe)

        except Exception:
            return False  # Any error → don't kill (fail safe)

    async def _read_process_owner_pid(self, pid: int) -> int | None:
        """Read SWARMAI_OWNER_PID from a process's environment.

        Delegates to ``session_utils.read_owner_pid()`` (single source
        of truth) via ``asyncio.to_thread`` for non-blocking I/O.
        """
        from .session_utils import read_owner_pid
        return await asyncio.to_thread(read_owner_pid, pid)

    async def _reap_by_pattern(
        self,
        pattern: str,
        label: str,
        known_pids: set[int],
        require_orphaned: bool = False,
    ) -> int:
        """Find and kill processes matching *pattern* via ``pgrep -f``.

        Args:
            pattern: Regex passed to ``pgrep -f``.
            label: Human-readable name for log messages.
            known_pids: PIDs to skip (active session PIDs + self).
            require_orphaned: If True, only kill processes confirmed as
                SwarmAI orphans via ownership tag (SWARMAI_OWNER_PID).
                Processes without the tag are never killed.

        Returns:
            Number of processes killed.
        """
        result = await asyncio.to_thread(
            subprocess.run,
            ["pgrep", "-f", pattern],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode != 0:
            return 0

        killed = 0
        for line in result.stdout.strip().split("\n"):
            line = line.strip()
            if not line:
                continue
            try:
                pid = int(line)
            except ValueError:
                continue

            if pid == os.getpid() or pid in known_pids:
                continue

            if require_orphaned:
                if not await self._is_owned_orphan(pid):
                    continue

            try:
                os.kill(pid, signal.SIGKILL)
                killed += 1
                logger.info("lifecycle_manager.reap_%s pid=%d", label, pid)
            except (ProcessLookupError, PermissionError):
                pass

        if killed:
            logger.warning(
                "Orphan reaper killed %d %s process(es)", killed, label,
            )
        return killed

    def _snapshot_known_pids(self) -> set[int]:
        """Snapshot PIDs from active SessionUnits + tracked children + self.

        Re-snapshot before each reap call to close the TOCTOU window
        where a new subprocess spawns between snapshot and kill.
        Includes our own PID and parent PID to prevent self-kill.
        """
        pids = {
            u.pid for u in self._router.list_units()
            if u.pid is not None
        }
        pids.update(self._tracked_child_pids)
        # Always protect ourselves and our parent (caffeinate wrapper)
        pids.add(os.getpid())
        try:
            ppid = os.getppid()
            if ppid > 1:
                pids.add(ppid)
        except OSError:
            pass
        return pids

    def _get_mcp_server_patterns(self) -> list[str]:
        """Get MCP server process name patterns for orphan reaping.

        Reads mcp-dev.json to extract command basenames dynamically.
        Falls back to a static list if config read fails.
        """
        _FALLBACK_PATTERNS = [
            "builder-mcp", "aws-sentral-mcp", "aws-outlook-mcp",
            "slack-mcp", "taskei-p-mcp",
        ]
        try:
            from core.initialization_manager import initialization_manager

            ws_path = initialization_manager.get_cached_workspace_path()
            if not ws_path:
                return _FALLBACK_PATTERNS

            mcp_config_path = Path(ws_path) / ".claude" / "mcps" / "mcp-dev.json"
            if not mcp_config_path.exists():
                return _FALLBACK_PATTERNS

            config = json.loads(mcp_config_path.read_text(encoding="utf-8"))

            # mcp-dev.json can be either:
            # - dict with "mcpServers" key (new format)
            # - list of server objects (legacy format)
            patterns = []
            if isinstance(config, dict):
                servers = config.get("mcpServers", {})
                for name, server_config in servers.items():
                    cmd = server_config.get("command", "")
                    if cmd:
                        basename = Path(cmd).name
                        if basename and basename not in patterns:
                            patterns.append(basename)
            elif isinstance(config, list):
                for server_config in config:
                    cmd = server_config.get("config", {}).get("command", "") if isinstance(server_config, dict) else ""
                    if cmd:
                        basename = Path(cmd).name
                        if basename and basename not in patterns:
                            patterns.append(basename)
            return patterns if patterns else _FALLBACK_PATTERNS
        except Exception as exc:
            logger.debug("Failed to read MCP config for reaper patterns: %s", exc)
            return _FALLBACK_PATTERNS

    async def _reap_orphans(self) -> None:
        """Find and kill orphaned SwarmAI processes via ownership tags.

        Guarded by a 30s total timeout — individual pgrep calls have 5s
        timeouts, but the aggregate (5 patterns × N PIDs × ownership checks)
        could take much longer in pathological cases.  The timeout prevents
        the maintenance loop from stalling.

        Only kills processes that have ``SWARMAI_OWNER_PID`` set in their
        environment AND whose owner backend is dead.  Processes without
        the tag are never touched — this is the core safety guarantee.
        """
        try:
            await asyncio.wait_for(self._reap_orphans_impl(), timeout=30.0)
        except asyncio.TimeoutError:
            logger.warning("Orphan reaper timed out after 30s — skipping cycle")
        except Exception as exc:
            logger.warning("Orphan reaper failed (non-fatal): %s", exc)

    async def _reap_orphans_impl(self) -> None:
        """Inner implementation of orphan reaping (called with timeout guard).

        Categories:
        1. Claude CLI + SDK workers
        2. Dev backend (``python main.py``)
        3. Zombie pytest (including xdist workers)
        4. MCP server processes (dynamic from config)

        Re-snapshots known_pids before each pattern to minimize the
        TOCTOU window between PID discovery and kill.
        """
        try:
            known = self._snapshot_known_pids()

            await self._reap_by_pattern(
                "claude_agent_sdk/(_bundled/claude|-c.*from claude_agent_sdk)",
                "claude", known, require_orphaned=True,
            )
            await self._reap_by_pattern(
                "python main.py", "dev_backend",
                self._snapshot_known_pids(), require_orphaned=True,
            )
            await self._reap_by_pattern(
                "pytest", "pytest",
                self._snapshot_known_pids(), require_orphaned=True,
            )

            # ── MCP server orphan reaping ──────────────────────────────────
            # Merge all MCP patterns into a single pgrep call to reduce
            # fork overhead (was: one pgrep per MCP server name).
            mcp_patterns = self._get_mcp_server_patterns()
            if mcp_patterns:
                merged_pattern = "|".join(mcp_patterns)
                await self._reap_by_pattern(
                    merged_pattern, "mcp",
                    self._snapshot_known_pids(),
                    require_orphaned=True,
                )
        except Exception as exc:
            logger.warning("Orphan reaper failed (non-fatal): %s", exc)
