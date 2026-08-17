"""LifecycleManager — background maintenance, TTL cleanup, and hook serialization.

Single background loop responsible for:
- Per-session memory sampling (CLI + MCP tree RSS, peak watermark, 1.5GB warning)
- Streaming timeout watchdog (5min no SDK events → force-unstick)
- TTL-based session cleanup (24hr idle → kill)
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
import functools
import json
import logging
import os
import signal
import subprocess
import time
from pathlib import Path
from typing import Optional, TYPE_CHECKING

from .session_unit import (
    SessionState,
    subprocess_executor as _subprocess_executor,
    rss_executor as _rss_executor,
)

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

    TTL_SECONDS: int = 86400  # 24 hours (R6: bumped 12h→24h — the orphan reaper
    # (_check_orphan_sessions, 10min) now reclaims unowned sessions, so TTL is a
    # pure long-idle backstop for OWNED sessions and can be generous. Safe ONLY
    # because the reaper landed first — bumping TTL without it would just extend
    # an orphan's lifeline.
    LOOP_INTERVAL: float = 60.0  # Check every 60 seconds
    IDLE_HOOK_GRACE: float = 120.0  # Fire hooks after 120s idle (grace period)
    STREAMING_TIMEOUT_SECONDS: float = 300.0  # 5 min no SDK events → stuck stream
    # Must stay STRICTLY GREATER than ask_question_manager.ASK_ANSWER_TIMEOUT_SECONDS
    # (4h) so a blocked AskUserQuestion expires GRACEFULLY (hook denies → "question
    # expired, re-ask") before this watchdog force-kills the whole session. 14700s
    # = 4h05m: 5 min of headroom above the 4h hook wait, comfortably clearing the
    # 60s watchdog loop granularity. Guarded by test_lifecycle_watchdog.py
    # ::TestWaitingInputTimeoutVsAskTimeout. (Was 120 min — too short, killed the
    # session before the answer-wait could even fire.)
    WAITING_INPUT_TIMEOUT_SECONDS: float = 14700.0  # 4h05m — see note above
    STARTUP_BACKLOG_CAP: int = 5  # Max sessions to process on startup scan
    # R6 (§9.9): an IDLE chat session that is owned by NO live window — not in
    # open_tabs.json — and has sat unowned longer than this is an orphan
    # (closed-window / crashed-frontend / SSE-drop). Reaped so it can't squat a
    # concurrency slot once cross-tab eviction is deleted (R6 Step C). Generous
    # vs the loop interval so a tab that is merely between open_tabs writes is
    # never mistaken for an orphan; far shorter than the 24h TTL it backstops.
    ORPHAN_GRACE_SECONDS: float = 600.0  # 10 min unowned + IDLE → orphan

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
        # PE-3: Warm up cultivation dispatcher with event loop reference
        # so threadsafe emitters (auto_commit, code_intel) work from session 1.
        try:
            from core.cultivation_dispatcher import get_dispatcher
            dispatcher = get_dispatcher()
            if dispatcher.loop is None:
                dispatcher.loop = asyncio.get_running_loop()
        except Exception:
            pass
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

        Pre-warmed (unadopted) sessions are skipped: they are empty-shell
        subprocesses with no conversation, so the ~11 post-session hooks
        (auto-commit / evolution / context-health / distillation / …) are pure
        no-op noise (~6.5s each). The single ``prewarm-`` prefix is the canonical
        marker (minted in ``session_router.prewarm_channel_session``); on adoption
        the unit's ``session_id`` is re-keyed to a real id, so hooks resume
        automatically — no extra state to reset. This is the single chokepoint all
        reap/idle/TTL/eviction call sites funnel through, so the guard here covers
        every hook-firing path.
        """
        from .session_router import PREWARM_SESSION_PREFIX

        if context.session_id and context.session_id.startswith(
            PREWARM_SESSION_PREFIX
        ):
            logger.debug(
                "enqueue_hooks: skipping lifecycle hooks for prewarm session %s",
                context.session_id,
            )
            return
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
                    await self._streaming_rss_check()
                    await self._check_streaming_timeout()
                    await self._check_waiting_input_timeout()
                    await self._fire_idle_hooks()
                    await self._check_ttl()
                    await self._check_orphan_sessions()
                    await self._cleanup_dead()
                    await self._check_memory_pressure()
                    # Persist session state every cycle (~60s) for crash recovery (§2B PE F7)
                    await self._persist_session_state()
                    # Reap orphans every 3rd cycle (~3 min) — was 10th (~10 min).
                    # Reduced after zombie process incident (248% CPU, 2026-04-01).
                    if cycle % 3 == 0:
                        await self._reap_orphans()
                    # Heavier cleanup every 10th cycle (~10 min)
                    if cycle % 10 == 0:
                        await self._purge_stale_cold()
                        await self._cleanup_stale_channel_sessions()
                        await self._cleanup_expired_messages()
                    # Radar ToDo sweep every 30th cycle (~30 min)
                    if cycle % 30 == 0 and cycle > 0:
                        await self._sweep_todos()
                        # Emit TIMER_30MIN for DDD cultivation v2
                        try:
                            from core.cultivation_dispatcher import (
                                EventType, emit_cultivation_event,
                            )
                            await emit_cultivation_event(
                                EventType.TIMER_30MIN,
                                source="lifecycle_manager",
                                payload={"cycle": cycle},
                                priority=3,
                            )
                        except Exception:
                            pass  # Non-blocking
                        # Process queued cultivation events (PE-1 fix)
                        await self._process_cultivation_events()
                    # Workspace backup check every 60th cycle (~60 min)
                    if cycle % 60 == 0 and cycle > 0:
                        await self._run_daily_backup()
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

        Non-fatal — failures are logged and skipped.  Each per-unit cost is one
        ``psutil.Process(pid).children(recursive=True)`` tree walk, MEASURED at
        ~107ms (run_409392d4), NOT the ~1ms once assumed.

        Concurrency (run_409392d4): the per-unit RSS reads are gathered with
        ``asyncio.gather`` on ``_rss_executor`` so the maintenance-loop coroutine
        is suspended for ~1x the tree-walk (parallel), NOT SUM(N x 107ms). The
        old serial ``for unit: await run_in_executor(...)`` loop suspended the
        loop for the full sum, delaying every SSE reader task on the same event
        loop -> simultaneous multi-tab SSE stalls. ``return_exceptions=True``
        keeps one unit's psutil failure from aborting the rest. Per-unit
        mutations (peak watermark, spawn-cost record, logging) run AFTER the
        gather, iterating ``zip(alive_units, results)`` — order-safe.
        """
        try:
            from .resource_monitor import resource_monitor

            alive_units = [u for u in self._router.list_units() if u.is_alive and u.pid]
            if not alive_units:
                return

            total_tree_rss = 0
            entries = []

            loop = asyncio.get_running_loop()
            # Gather all per-unit tree-RSS reads CONCURRENTLY (dedicated pool).
            rss_results = await asyncio.gather(
                *(
                    loop.run_in_executor(
                        _rss_executor,
                        resource_monitor.process_tree_rss, unit.pid,
                    )
                    for unit in alive_units
                ),
                return_exceptions=True,
            )

            for unit, tree_rss in zip(alive_units, rss_results):
                # return_exceptions=True: skip a unit whose psutil walk raised.
                if isinstance(tree_rss, BaseException) or tree_rss <= 0:
                    continue

                # Update peak watermark; warn on first 1.5GB crossing.
                # On first sample (peak was 0), record the spawn cost
                # for adaptive spawn budget estimation (G4 fix).
                # Record MAIN PROCESS RSS only (not tree) — the tree includes
                # MCP children (~1050MB) which inflates the estimate 5× vs
                # incremental cost of one more session (~300-500MB).
                prev_peak = unit._peak_tree_rss_bytes
                if prev_peak == 0 and tree_rss > 0:
                    main_rss = await loop.run_in_executor(
                        _rss_executor,
                        resource_monitor.process_rss, unit.pid,
                    )
                    if main_rss > 0:
                        resource_monitor.record_spawn_cost(main_rss)
                    else:
                        resource_monitor.record_spawn_cost(tree_rss)
                if tree_rss > prev_peak:
                    unit._peak_tree_rss_bytes = tree_rss
                    # Advisory warning only — no kill. Useful for tracking
                    # growth trends in logs. Threshold = midpoint between
                    # normal IDLE (1-2GB) and proactive kill (3.5GB).
                    if tree_rss > 2_500_000_000 and prev_peak <= 2_500_000_000:
                        logger.warning(
                            "lifecycle_manager.memory_warning session=%s "
                            "tree_rss=%dMB — crossed 2.5GB advisory threshold",
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

            loop = asyncio.get_running_loop()

            # Eligibility filter FIRST (state / pid / cooldown) — do NOT spend an
            # RSS read on a unit we won't act on. Then gather the survivors' RSS
            # reads concurrently on _rss_executor (run_409392d4: was a serial
            # await loop = SUM(N x 107ms) suspending the maintenance loop; now
            # ~1x). Per-unit compact/kill mutations run AFTER the gather, serially
            # (one kill at a time, semantics preserved).
            # P-a AC3 — prewarm units are INTENTIONALLY *not* exempt here (unlike
            # the orphan reaper + TTL). Like Tier-1 memory pressure, this is a RAM
            # survival path: a heavy prewarm subprocess crossing the RSS threshold
            # must be reclaimable. Exempting it would let an unadopted prewarm grow
            # into an unreclaimable memory black hole. Do NOT add a prewarm skip.
            eligible = [
                unit for unit in self._router.list_units()
                if unit.state == SessionState.IDLE
                and unit.pid
                and (time.monotonic() - unit._last_proactive_restart
                     >= SessionUnit.PROACTIVE_COOLDOWN)
            ]
            if not eligible:
                return

            rss_results = await asyncio.gather(
                *(
                    loop.run_in_executor(
                        _rss_executor,
                        resource_monitor.process_tree_rss, unit.pid,
                    )
                    for unit in eligible
                ),
                return_exceptions=True,
            )

            for unit, tree_rss in zip(eligible, rss_results):
                if isinstance(tree_rss, BaseException):
                    continue
                if tree_rss <= SessionUnit.PROACTIVE_RSS_THRESHOLD:
                    continue
                # Re-check state: a unit may have left IDLE between the RSS
                # gather and now (TOCTOU) — never interrupt a STREAMING turn.
                if unit.state != SessionState.IDLE:
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

    async def _streaming_rss_check(self) -> None:
        """Kill STREAMING sessions with dangerously high RSS or under system pressure.

        Closes the blind spot where adaptive timeout (up to 900s) lets a
        bloating STREAMING subprocess grow unchecked — proactive_rss_restart
        only touches IDLE sessions.

        Two triggers (either one fires):
        - Per-session: tree RSS > STREAMING_RSS_KILL_THRESHOLD (7GB)
        - System-wide: memory pressure > MEMORY_EVICT_PCT (90%) → kill heaviest STREAMING session

        Non-fatal — failures logged and skipped.
        """
        try:
            from .resource_monitor import resource_monitor
            from .session_unit import SessionUnit

            streaming_units = [
                u for u in self._router.list_units()
                if u.state == SessionState.STREAMING and u.pid
            ]
            if not streaming_units:
                return

            mem = resource_monitor.system_memory()
            loop = asyncio.get_running_loop()

            # Collect RSS for each streaming session — gathered CONCURRENTLY on
            # _rss_executor (run_409392d4: was a serial await loop suspending the
            # maintenance loop for SUM(N x 107ms) → SSE stalls; now ~1x). The
            # kill logic below (Trigger 1/2) already runs after collection, so
            # only this read loop needed parallelizing.
            rss_results = await asyncio.gather(
                *(
                    loop.run_in_executor(
                        _rss_executor,
                        resource_monitor.process_tree_rss, unit.pid,
                    )
                    for unit in streaming_units
                ),
                return_exceptions=True,
            )
            rss_map: dict = {}  # unit → rss_bytes
            for unit, tree_rss in zip(streaming_units, rss_results):
                if isinstance(tree_rss, BaseException):
                    continue
                if tree_rss > 0:
                    rss_map[unit] = tree_rss

            # Trigger 1: Per-session threshold (7GB, STREAMING_RSS_KILL_THRESHOLD)
            for unit, rss in rss_map.items():
                if rss > SessionUnit.STREAMING_RSS_KILL_THRESHOLD:
                    # Re-check state: session may have completed between
                    # RSS sampling and now (TOCTOU mitigation).
                    if unit.state != SessionState.STREAMING:
                        continue
                    # R3b (M1): the kill DECISION routes through the one recovery
                    # authority (BareThresholdPolicy). The caller still owns the
                    # RSS threshold measurement above; the Coordinator owns the
                    # may-I-kill verdict (universal guard: enabled / user_stopped).
                    # eligible_states=None — the STREAMING-only unit list already
                    # gates state.
                    from .session_healing import RecoveryVerdict
                    _decision = unit._recovery_coordinator.decide_bare(
                        trigger="rss_streaming",
                        enabled=True,
                        user_stopped=unit._user_stopped_current_turn,
                        state=unit.state.value,
                    )
                    if _decision.verdict is not RecoveryVerdict.PROCEED_KILL:
                        continue  # SKIP (user stopped this turn) — leave it alone
                    logger.warning(
                        "lifecycle.streaming_rss_kill session=%s "
                        "rss=%dMB > threshold=%dMB — killing bloated STREAMING session",
                        unit.session_id[:8],
                        rss // (1024 * 1024),
                        SessionUnit.STREAMING_RSS_KILL_THRESHOLD // (1024 * 1024),
                    )
                    await unit._arm_recovery_checkpoint("rss_streaming")
                    await unit.kill()
                    return  # One kill per cycle to avoid cascade

            # Trigger 2: System pressure > MEMORY_EVICT_PCT → kill heaviest
            # Only fires AFTER IDLE eviction threshold (90% default).
            # This ensures IDLE sessions are evicted first; STREAMING kill
            # is the LAST resort when IDLE eviction wasn't enough.
            #
            # DELIBERATELY NOT routed through the RecoveryCoordinator (design
            # §9 membership analysis): this is FLEET ARBITRATION (max()-over-N
            # picks a victim under system-wide OOM pressure), NOT single-unit
            # recovery. It fails the coordinator's membership test (it sacrifices
            # one of N, not "recover THIS unit"). Crucially it must IGNORE the
            # user_stopped guard that Trigger 1 honors: at >90% system memory a
            # user-Stop does not free the leaked RSS, so refusing to kill the
            # heaviest would let the whole machine OOM (COE05). The boundary is
            # intentional — the per-session 7GB leak (Trigger 1) is recovery and
            # is coordinated; the system-pressure last-resort kill is arbitration
            # and is not. See design §9.2 + §9 "RSS-streaming fleet-pressure → OUT".
            if mem.percent_used > self.MEMORY_EVICT_PCT and rss_map:
                heaviest = max(rss_map, key=rss_map.get)
                logger.warning(
                    "lifecycle.streaming_pressure_kill session=%s "
                    "system_used=%.1f%% > evict_pct=%.0f%% rss=%dMB "
                    "— killing heaviest STREAMING session (last resort)",
                    heaviest.session_id[:8],
                    mem.percent_used,
                    self.MEMORY_EVICT_PCT,
                    rss_map[heaviest] // (1024 * 1024),
                )
                await heaviest._arm_recovery_checkpoint("rss_streaming")
                await heaviest.kill()

        except Exception as exc:
            # GC19: surface the failure type, not a bare swallow. This block now
            # depends on unit._recovery_coordinator / _user_stopped_current_turn
            # (R3b); if a future unit reaches STREAMING without them wired, the
            # OOM-protection kill would silently no-op. Log at warning so a
            # swallowed kill is observable (the loop must still not crash — the
            # 7GB kill is best-effort, the next 60s tick retries).
            logger.warning(
                "_streaming_rss_check failed (non-fatal, kill skipped this tick): %s: %s",
                type(exc).__name__, exc,
            )

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
            # Open-tool guard (mirrors session_unit output-liveness backstop,
            # ~line 950): while a tool is open, event-silence is EXPECTED — a
            # healthy long tool (Bash build, Agent sub-agent) is CPU-busy but
            # emits no SDK events. Killing here on silence alone resurrects the
            # bug run_fb6e94a9 fixed: a healthy long tool force-killed at the
            # 300s silence mark, before the in-session 1800s CPU-liveness probe
            # ever runs. Tool-liveness is judged by the in-session PID watchdog
            # (CPU probe + 1h hard ceiling), which OWNS the wedged-open-tool
            # case. The lifecycle loop has no CPU probe, so it must not judge
            # tool liveness — only the pure-API-hang case (no open tool) below.
            if getattr(unit, "_open_tool_uses", None):
                logger.debug(
                    "lifecycle_manager.streaming_timeout_skip session_id=%s "
                    "stall=%.0fs — tool open, deferring to in-session CPU probe",
                    unit.session_id, stall,
                )
                continue
            # Two distinct stall classes — different thresholds:
            #  (a) DUMB SPAWN: the subprocess entered STREAMING but produced
            #      ZERO SDK events (not even a first token), no open tool.
            #      "Alive but silent" → recover fast. Reusing the 600-1800s
            #      adaptive timeout left the frontend spinner spinning 15+ min
            #      (run_6c482b10: pid 33855). Resume gets 2x (replays the full
            #      conversation before the first token — GUI66).
            #  (b) SLOW INFERENCE: events ARE flowing, the turn is just slow.
            #      Keep the adaptive 600-1800s tolerance — killing here would
            #      false-abort a healthy large-context turn.
            #
            #  Discriminator: _transition() sets BOTH _streaming_start_time and
            #  _last_event_time to the SAME timestamp on STREAMING entry
            #  (session_unit.py); the orchestrator then advances ONLY
            #  _last_event_time on each real SDK event. So "no event since
            #  spawn" == _last_event_time has not moved past _streaming_start_time.
            #  (NOT `is None` — that is only true AFTER leaving STREAMING.)
            start_t = getattr(unit, "_streaming_start_time", None)
            last_t = getattr(unit, "_last_event_time", None)
            # "No event since spawn" == _last_event_time has not advanced past
            # _streaming_start_time. Numeric-safe: only the (float, float) case
            # can be a dumb spawn; anything else (None, or non-numeric) falls
            # through to the adaptive path — the conservative default that never
            # applies the aggressive short timeout to an ambiguous unit.
            no_event_since_spawn = (
                last_t is None and start_t is not None
            ) or (
                isinstance(last_t, (int, float))
                and isinstance(start_t, (int, float))
                and last_t <= start_t
            )
            if no_event_since_spawn:
                from .session_unit import DUMB_SPAWN_TIMEOUT_SECONDS
                adaptive = (
                    unit._compute_message_timeout()
                    if hasattr(unit, "_compute_message_timeout")
                    else self.STREAMING_TIMEOUT_SECONDS
                )
                if getattr(unit, "_sdk_session_id", None):
                    # A --resume spawn replays the FULL conversation before the
                    # first token (GUI66) — a large replay legitimately emits no
                    # SDK event for a long time. Killing it at a flat short
                    # timeout would false-abort a healthy large resume → respawn
                    # → replay → kill loop (run_6c482b10 adversarial MED). So a
                    # dumb resume gets the SAME replay budget as a slow resume:
                    # the adaptive timeout (up to 1800s), floored at 2x the
                    # dumb threshold so a TINY resume still recovers reasonably
                    # fast. The incident (89b71059) was a WARM continuation
                    # (is_cold_resume=False), not a replay — it falls in the
                    # non-resume branch below and gets the fast 120s.
                    effective_timeout = max(DUMB_SPAWN_TIMEOUT_SECONDS * 2, adaptive)
                else:
                    # Fresh (non-resume) spawn: no replay, first token should
                    # arrive in seconds. Zero events past 120s = genuinely dumb.
                    effective_timeout = DUMB_SPAWN_TIMEOUT_SECONDS
            else:
                # Use adaptive timeout from the unit (context-aware) if available,
                # otherwise fall back to static threshold.
                effective_timeout = (
                    unit._compute_message_timeout()
                    if hasattr(unit, "_compute_message_timeout")
                    else self.STREAMING_TIMEOUT_SECONDS
                )
            if stall > effective_timeout:
                # Circuit breaker: if this session has already been unstuck
                # multiple times without success, don't keep trying — it's
                # structurally doomed (context too large for timeout window).
                unstick_count = getattr(unit, "_consecutive_unstick_timeouts", 0)
                cb_threshold = getattr(unit, "_UNSTICK_CIRCUIT_BREAKER_THRESHOLD", 2)
                if unstick_count > cb_threshold:
                    logger.warning(
                        "lifecycle_manager.streaming_timeout_circuit_break "
                        "session_id=%s unstick_count=%d > threshold=%d "
                        "— skipping (circuit breaker tripped)",
                        unit.session_id,
                        unstick_count,
                        cb_threshold,
                    )
                    continue
                logger.warning(
                    "lifecycle_manager.streaming_timeout session_id=%s "
                    "stall=%.0fs > timeout=%.0fs — forcing unstick",
                    unit.session_id,
                    stall,
                    effective_timeout,
                )
                await unit.force_unstick_streaming()

    async def _check_waiting_input_timeout(self) -> None:
        """Recover WAITING_INPUT sessions stuck beyond timeout.

        When a session enters WAITING_INPUT (permission prompt), the hang
        detector and PID watchdog correctly exclude it. But if the user
        disappears (closes laptop, browser tab crashes, frontend disconnect),
        the session stays in WAITING_INPUT permanently — consuming a slot
        (max_tabs=2 → 50% capacity loss) until the 24h TTL kills it.

        This check provides a 120-minute fallback: long enough for a user
        to think about a permission or attend a meeting, short enough to
        reclaim stuck slots within the same workday.

        Uses unit.last_used (set on state transitions) as the activity marker.

        Race safety: force_unstick_waiting_input() has an internal state guard
        (returns immediately if state != WAITING_INPUT). If the user answers
        the permission between our check and the call, the unit will already
        be in STREAMING → unstick is a no-op. No lock needed because this is
        cooperative async (no await between state check and transition in
        continue_with_permission).
        """
        now = time.time()
        for unit in self._router.list_units():
            if unit.state != SessionState.WAITING_INPUT:
                continue
            # Dead-waiter reap FIRST (run_65f317db, SSA Gate-1): a WAITING_INPUT
            # session whose waiter coroutine is DEAD (outstanding tool_use but no
            # live hook to receive a decision — the approve-into-void deadlock) can
            # NEVER be answered. Reap it on THIS ~60s tick instead of waiting the
            # full 4h05m WAITING_INPUT_TIMEOUT — this is the self-heal path for a
            # session that receives NEITHER a new send NOR an approve-endpoint hit
            # (the arm the send-path/endpoint reaps alone would leave stuck till
            # TTL). reap_dead_waiting_input is idempotent + live-waiter-guarded, so
            # a genuinely-open prompt is NOT reaped.
            try:
                if await unit.reap_dead_waiting_input():
                    continue  # reaped to COLD — nothing more to do this tick
            except Exception:
                logger.exception(
                    "lifecycle_manager.reap_dead_waiting_input failed session_id=%s",
                    unit.session_id,
                )
            waiting_seconds = now - unit.last_used
            if waiting_seconds > self.WAITING_INPUT_TIMEOUT_SECONDS:
                # R3c (M2): the recovery DECISION routes through the one authority
                # (BareThresholdPolicy). The caller owns the timeout measurement
                # above; the Coordinator owns the may-I-recover verdict. Unlike
                # self-heal (which PROTECTS waiting_input), stuck-WAITING TARGETS
                # it — eligible_states={"waiting_input"} encodes exactly that.
                #
                # user_stopped=False DELIBERATELY (adversarial final-gate Q3): a
                # session sitting in WAITING_INPUT for 4h+ carries a STALE
                # _user_stopped_current_turn from a prior STREAMING turn (the flag
                # is cleared only at the next send(), which by definition never
                # came for an abandoned session). Consulting it would let a
                # stopped-then-abandoned session block its OWN 4h reclamation
                # forever — the exact wedge this watchdog exists to clear. A
                # genuinely stuck WAITING_INPUT with no client IS the target; the
                # user's intent from hours ago is irrelevant to reclaiming the slot.
                from .session_healing import RecoveryVerdict
                _decision = unit._recovery_coordinator.decide_bare(
                    trigger="stuck_waiting",
                    enabled=True,
                    user_stopped=False,
                    state=unit.state.value,
                    eligible_states=frozenset({"waiting_input"}),
                )
                if _decision.verdict is not RecoveryVerdict.PROCEED_KILL:
                    continue  # ineligible state (race: user answered) — leave it
                logger.warning(
                    "lifecycle_manager.waiting_input_timeout session_id=%s "
                    "waiting=%.0fs > timeout=%.0fs — forcing unstick",
                    unit.session_id,
                    waiting_seconds,
                    self.WAITING_INPUT_TIMEOUT_SECONDS,
                )
                await unit.force_unstick_waiting_input()

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
        """Kill SessionUnits that have been IDLE longer than TTL.

        Channel sessions (is_channel_session=True) are exempt — they live
        as long as the daemon.  Their context continuity depends on the
        subprocess staying alive; TTL-killing them causes context loss on
        follow-up messages.
        """
        from .session_router import PREWARM_SESSION_PREFIX

        now = time.time()
        for unit in self._router.list_units():
            if unit.state == SessionState.IDLE:
                # Channel sessions are never TTL-killed — they persist
                # for the lifetime of the daemon.  Context continuity
                # is maintained by the long-lived subprocess + --resume.
                if unit.is_channel_session:
                    continue
                # P-a AC1: an unadopted prewarm unit is a warm subprocess
                # awaiting adoption, not a stale chat — exempt from TTL kill
                # (same non-competitive-GC exemption as the orphan reaper).
                if unit.session_id.startswith(PREWARM_SESSION_PREFIX):
                    continue
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
    def _owned_session_ids() -> set[str] | None:
        """Delegate to the canonical ownership reader (routers.settings).

        Single source of truth shared with session_router's orphan-only
        eviction — see ``routers.settings.owned_session_ids`` for the full
        None-vs-empty-set fail-safe contract. Wrapped here so a settings-import
        failure degrades to "unknowable" (None → reap nothing) rather than
        crashing the maintenance loop.
        """
        try:
            from routers.settings import owned_session_ids
            return owned_session_ids()
        except Exception as exc:  # GC19: surface, never silently swallow
            logger.warning("orphan reaper: ownership lookup failed (%s) — skipping cycle", exc)
            return None

    async def _check_orphan_sessions(self) -> None:
        """Reap IDLE chat sessions owned by no live window (R6 §9.9).

        The orphan class: a chat session left behind by a closed window, a
        crashed frontend, or an SSE drop — it has no tab in any window to close
        and no client to drive it, yet today only ``_evict_idle`` (cross-tab
        kill, being deleted in R6 Step C) and the 24h TTL ever reclaim it. This
        reaper is the replacement: it GC's a session *nobody owns*, which is NOT
        the cross-tab eviction of a *user's* tab that the Multi-Tab Isolation
        principle forbids.

        Strict safety gates (each mirrors a Gate-1 skeptic finding):
        - **IDLE only.** STREAMING and WAITING_INPUT are NEVER touched — a user
          who stepped away mid-question (WAITING_INPUT, SSE closed, answer
          arrives on a separate request) is NOT an orphan. Mirrors ``_check_ttl``
          and ``_evict_idle``'s Rule-3 protection.
        - **Channel-exempt.** Channel sessions have no window/tab and are owned
          by the daemon for its lifetime.
        - **Ownership fail-safe.** If open_tabs is unknowable (``None``), reap
          NOTHING — never infer "no tabs ⇒ all orphans" from a read error.
        - **Grace.** Must be unowned AND idle > ORPHAN_GRACE_SECONDS, so a tab
          merely between open_tabs writes (or a just-created session whose id has
          not yet been persisted to open_tabs) is never mistaken for an orphan.
        """
        from .session_router import PREWARM_SESSION_PREFIX

        owned = self._owned_session_ids()
        if owned is None:
            return  # ownership unknowable this cycle — fail safe, reap nothing

        now = time.time()
        for unit in self._router.list_units():
            if unit.state != SessionState.IDLE:
                continue  # protect STREAMING / WAITING_INPUT / COLD / DEAD
            if unit.is_channel_session:
                continue  # daemon-owned, no window
            # P-a AC1: an unadopted prewarm unit is a warm subprocess awaiting
            # adoption, never in open_tabs (temp id) and non-channel — it would
            # hit every orphan criterion below, but it is NOT an orphan. This is
            # the root-cause fix for Slack prewarm adopt=0 (reaped before adopt).
            if unit.session_id.startswith(PREWARM_SESSION_PREFIX):
                continue
            if unit.session_id in owned:
                continue  # a live window holds this tab
            if (now - unit.last_used) <= self.ORPHAN_GRACE_SECONDS:
                continue  # within grace — not yet an orphan
            logger.info(
                "lifecycle_manager.orphan_reap session_id=%s idle=%.0fs "
                "owned_tabs=%d — reaping unowned IDLE chat session",
                unit.session_id, now - unit.last_used, len(owned),
            )
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
        - session_registry.recall_snapshot          (recalled block, TSCC panel)
        - session_registry.surfaced_paths           (Canvas-surfaced abs paths, run_c014a4f3)
        - permission_manager._approved_commands    (command hashes)
        - permission_manager._session_queues       (asyncio.Queue)
        """
        try:
            from . import session_registry
            session_registry.system_prompt_metadata.pop(session_id, None)
            session_registry.recall_snapshot.pop(session_id, None)
            session_registry.surfaced_paths.pop(session_id, None)
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
                # TOCTOU re-check (run_ace705df Gate-2 HIGH): the hook-context
                # build above is an `await` — during it a concurrent send()
                # DEAD-recovery (session_unit send() auto_recover_dead) can drive
                # this SAME unit DEAD→COLD→IDLE→STREAMING lock-free (spawn holds
                # _spawn_lock, not self._lock). If we blindly ran the old
                # _cleanup_internal()+_transition(COLD) here we would wipe a unit
                # that send() already recovered and is actively streaming —
                # orphaning the freshly-spawned subprocess (no kill) and
                # corrupting _streaming_count. Re-check state AFTER the await; if
                # it is no longer DEAD, send() owns it now — leave it alone.
                if unit.state != SessionState.DEAD:
                    continue
                self._release_session_state(unit.session_id)
                # Route through the idempotent, self._lock-holding recovery
                # transaction instead of hand-rolling cleanup+transition. This
                # serializes DEAD→COLD against send()'s _crash_to_cold_async so
                # the two DEAD→COLD drivers are mutually exclusive (the old
                # hand-rolled path was lock-free, defeating R4 atomicity). If a
                # send() recovery is mid-flight holding the lock, this awaits it
                # and then no-ops on the resulting COLD state.
                await unit._crash_to_cold_async(clear_identity=False)

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
        """Delete messages past their 90-day TTL.

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

    # ── Radar ToDo sweep ─────────────────────────────────────────────

    async def _sweep_todos(self) -> None:
        """Periodic sweep: mark overdue todos + handle completed pipeline todos.

        Non-fatal — failures are logged and skipped. Called from
        maintenance loop every 30th cycle (~30 min).

        Three sweep actions:
        1. check_overdue() — pending todos past due_date → overdue
        2. Pipeline-bound todos — if run.json shows completed → handled
        3. Evolution proposals — unchanged >30 days → expired
        """
        try:
            from core.todo_manager import todo_manager
            from database import db

            # 1. Mark overdue todos
            overdue_count = await todo_manager.check_overdue()

            # 2. Pipeline-bound todos: check if linked pipeline run completed
            # PE-1 fix: file I/O in thread to avoid blocking event loop
            handled_count = 0
            try:
                import json as _json
                from config import get_app_data_dir

                pending = await db.todos.list_by_status("pending")
                todo_ids_to_handle: list[str] = []

                def _check_pipeline_todos(todos_list):
                    """Sync file I/O — runs in thread pool."""
                    completed_ids = []
                    for todo in todos_list:
                        metadata = todo.get("metadata")
                        if isinstance(metadata, str):
                            try:
                                metadata = _json.loads(metadata)
                            except (_json.JSONDecodeError, TypeError):
                                metadata = {}
                        if not isinstance(metadata, dict):
                            continue
                        run_id = metadata.get("pipeline_run_id") or metadata.get("run_id")
                        if not run_id:
                            continue
                        project = metadata.get("project", "SwarmAI")
                        run_path = (
                            get_app_data_dir() / "SwarmWS" / "Projects" / project
                            / ".artifacts" / "runs" / run_id / "run.json"
                        )
                        if run_path.exists():
                            try:
                                run_data = _json.loads(run_path.read_text())
                                if run_data.get("status") == "completed":
                                    completed_ids.append(todo["id"])
                            except (OSError, _json.JSONDecodeError):
                                pass
                    return completed_ids

                import asyncio
                todo_ids_to_handle = await asyncio.to_thread(
                    _check_pipeline_todos, pending
                )
                for tid in todo_ids_to_handle:
                    await db.todos.update(tid, {"status": "handled"})
                    handled_count += 1
            except Exception as exc:
                logger.debug("todo_sweep: pipeline check error: %s", exc)

            # 3. Evolution proposals >30 days → expire
            expired_count = 0
            try:
                from datetime import datetime, timezone, timedelta
                now = datetime.now(timezone.utc)
                cutoff = now - timedelta(days=30)
                pending = await db.todos.list_by_status("pending")
                for todo in pending:
                    title = todo.get("title", "")
                    if "Evolution proposal" not in title:
                        continue
                    created_str = todo.get("created_at", "")
                    try:
                        created = datetime.fromisoformat(
                            created_str.replace("Z", "+00:00")
                        )
                        if created < cutoff:
                            await db.todos.update(todo["id"], {"status": "expired"})
                            expired_count += 1
                    except (ValueError, TypeError):
                        pass
            except Exception as exc:
                logger.debug("todo_sweep: evolution expire error: %s", exc)

            # 4. ToDo flow-closure: auto-complete + 7-day auto-confirm (run_d28de5fd).
            # XG decision: this is a background JOB (this ~30-min sweep), NOT a hook —
            # hooks cost per-turn session resources; ToDo does not need to be real-time.
            # DECOUPLING: NONE of this touches the SSE/streaming path (one-way invariant).
            auto_completed = 0
            auto_confirmed = 0
            try:
                auto_completed = await self._sweep_auto_complete_todos()
                auto_confirmed = await self._sweep_auto_confirm_todos()
            except Exception as exc:
                logger.debug("todo_sweep: flow-closure error: %s", exc)

            total = (overdue_count + handled_count + expired_count
                     + auto_completed + auto_confirmed)
            if total > 0:
                logger.info(
                    "lifecycle_manager.todo_sweep: %d transitions "
                    "(overdue=%d, pipeline_handled=%d, expired=%d, "
                    "auto_completed=%d, auto_confirmed=%d)",
                    total, overdue_count, handled_count, expired_count,
                    auto_completed, auto_confirmed,
                )
        except Exception as exc:
            logger.warning("lifecycle_manager.todo_sweep failed: %s", exc)

    async def _sweep_auto_complete_todos(self) -> int:
        """Auto-complete dispatched todos whose session got an AI reply after dispatch.

        Candidate = dispatched_session_id set AND review_state IS NULL (i.e. ②
        In Progress). Done-check = an assistant message in that session created after
        dispatched_at (index-hit LIMIT-1 on idx_messages_session_created — cheap).
        On completion: set review_state='completed' + completed_at ONLY. status STAYS
        'pending' per the LOCKED status invariant (status→handled happens at Confirm).

        Gate-1 B (accepted): an UNRELATED later assistant reply in the same session
        also trips this. The human Confirm/Reject is the backstop — a false 'completed'
        is only an early review prompt, never an auto-action. We do NOT turn-scope
        (no reliable dispatch-turn id).

        Batch: one candidate query + one LIMIT-1 check per candidate + batch UPDATE.
        No network/file call (STEERING #2 — keeps the maintenance loop fast).
        """
        from database import db
        from datetime import datetime, timezone

        rows = await db.todos.list()  # small set; we re-filter for dispatched+unreviewed
        candidates = [
            r for r in rows
            if r.get("dispatched_session_id")
            and r.get("review_state") is None
            and r.get("dispatched_at")
        ]
        if not candidates:
            return 0

        completed_ids: list[str] = []
        for todo in candidates:
            sid = todo["dispatched_session_id"]
            dispatched_at = todo["dispatched_at"]
            replied = await db.messages.assistant_replied_since(sid, dispatched_at)
            if replied:
                completed_ids.append(todo["id"])

        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")
        for tid in completed_ids:
            # review_state + completed_at ONLY — status stays pending (locked invariant)
            await db.todos.update(tid, {"review_state": "completed", "completed_at": now})
        return len(completed_ids)

    async def _sweep_auto_confirm_todos(self) -> int:
        """7-day auto-confirm: a completed-awaiting-review todo not reviewed within
        7 days → review_state='confirmed', review_kind='auto', reviewed_at=now.
        This resolves the review BEFORE the 14-day purge, so every archived row
        carries a final review_state (no 'never reviewed' gaps). Batch UPDATE."""
        from database import db
        from datetime import datetime, timedelta, timezone

        now = datetime.now(timezone.utc)
        cutoff = (now - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%S+00:00")
        rows = await db.todos.list()
        stale = [
            r for r in rows
            if r.get("review_state") == "completed"
            and (r.get("completed_at") or "") < cutoff
            and r.get("completed_at")
        ]
        now_iso = now.strftime("%Y-%m-%dT%H:%M:%S+00:00")
        for todo in stale:
            await db.todos.update(
                todo["id"],
                {"review_state": "confirmed", "review_kind": "auto", "reviewed_at": now_iso},
            )
        return len(stale)

    # ── Cultivation event consumer (PE-1) ────────────────────────────

    async def _process_cultivation_events(self) -> None:
        """Drain queued cultivation events and execute subscribed channels.

        This is the CONSUMER side of the event-driven pipeline. Events are
        produced by hooks (auto_commit, daily_activity, etc.) and queued in
        the singleton dispatcher. This method drains the queue every 30 min
        (maintenance cycle), maps events to channel tasks, and executes them
        via ChannelExecutor with priority + budget enforcement.

        Also handles SESSION_CLOSE events that accumulated between ticks.
        """
        try:
            from core.cultivation_dispatcher import (
                ChannelExecutor, get_dispatcher,
            )
            from core.ddd_orchestrator import DddCultivationOrchestrator
            from core.initialization_manager import initialization_manager

            dispatcher = get_dispatcher()
            events = await dispatcher.drain()
            if not events:
                return

            ws_path = initialization_manager.get_cached_workspace_path()
            if not ws_path:
                return

            from pathlib import Path
            root = Path(ws_path)
            orch = DddCultivationOrchestrator()
            executor = ChannelExecutor(total_budget=10.0)

            # Collect tasks from all events, deduplicate by channel name
            all_tasks = []
            seen_channels: set[str] = set()
            for event in events:
                tasks = orch.get_tasks_for_event(event.type, root, ws_path)
                for task in tasks:
                    if task.name not in seen_channels:
                        seen_channels.add(task.name)
                        all_tasks.append(task)

            if not all_tasks:
                return

            findings = await executor.execute_batch(all_tasks)

            if findings:
                logger.info(
                    "lifecycle_manager.cultivation_events: processed %d events → "
                    "%d channels → %d findings",
                    len(events), len(all_tasks), len(findings),
                )
                # Log non-trivial findings
                for f in findings:
                    if "CHANNEL_ERROR" in f or "CHANNEL_TIMEOUT" in f:
                        logger.warning("cultivation: %s", f)

            # M0 (run_abf49550): persist workspace-level drain health durably so a
            # channel timeout/error or a queue overflow is VISIBLE, not just a debug
            # log nobody reads. The except-swallow below STILL stands (correct-by-
            # design: cultivation must never crash the maintenance loop) — this only
            # ADDS a surfaced record. Best-effort inside the recorder itself.
            try:
                from core.ddd_cultivation import record_workspace_cultivation_health
                # dropped_count is CUMULATIVE (only ever += , never reset —
                # cultivation_dispatcher.py:130/157/170). Record the DELTA since the
                # last drain, then reset, else read_workspace_cultivation_health would
                # SUM the running total across every drain → massive over-count
                # (Gate-2 HIGH). Safe to reset: the ONLY readers are this recorder +
                # a log line (verified — no other consumer relies on the cumulative).
                dropped_delta = getattr(dispatcher, "dropped_count", 0)
                record_workspace_cultivation_health(
                    root, findings=findings, dropped=dropped_delta
                )
                if dropped_delta:
                    dispatcher.dropped_count = 0
            except Exception:  # observability must never break the organ (O030)
                pass

        except Exception as exc:
            logger.debug("lifecycle_manager.cultivation_events failed: %s", exc)

    # ── Workspace backup ────────────────────────────────────────────

    async def _run_daily_backup(self) -> None:
        """Run workspace backup if >24h since last backup.

        Non-fatal — failures are logged and skipped. Called from the
        maintenance loop every 60th cycle (~60 min). Checks last_backup
        timestamp and skips if <24h ago.
        """
        try:
            from core.backup_manager import BackupManager
            from datetime import datetime, timedelta

            if not hasattr(self, "_backup_manager"):
                self._backup_manager = BackupManager()

            # Skip if <24h since last backup
            status = self._backup_manager.get_status()
            last = status.get("last_backup")
            if last:
                try:
                    last_dt = datetime.fromisoformat(last)
                    if datetime.now() - last_dt < timedelta(hours=24):
                        return  # Too recent, skip
                except (ValueError, TypeError):
                    pass  # Malformed timestamp — run backup

            if not status.get("enabled", True):
                return  # Backup disabled

            result = await self._backup_manager.backup()
            logger.info(
                "lifecycle_manager.daily_backup: %s (tables=%d, push=%s)",
                result.get("status"),
                result.get("tables_exported", 0),
                result.get("push_status"),
            )
        except Exception as exc:
            logger.warning("lifecycle_manager.daily_backup failed: %s", exc)

    # ── Memory pressure relief ─────────────────────────────────────

    # ── Session state persistence (Design §2B, PE F7) ──────────────────────
    # Persist IDLE session sdk_session_ids every 60s for crash recovery.
    # On daemon restart, restored IDs enable fast --resume instead of cold resume.

    async def _persist_session_state(self) -> None:
        """Persist IDLE session identities to disk for crash recovery.

        Called every maintenance cycle (~60s). Atomic write ensures no
        corruption if daemon crashes mid-write. Merges unconsumed cached IDs
        so sessions not yet re-opened survive a second restart. Non-fatal —
        failures are logged and skipped.
        """
        try:
            from .session_state_persistence import persist_session_state
            from jobs.paths import APP_DATA_DIR

            state_file = APP_DATA_DIR / "session_state.json"
            units = self._router._units if self._router else {}
            # Pass unconsumed cached IDs so they survive overwrite (PE gap fix).
            pending = getattr(self._router, "_persisted_sdk_ids", None) or {}
            persist_session_state(units, state_file, pending_ids=pending)
        except Exception as exc:
            # Non-fatal: persistence is best-effort, never blocks maintenance
            logger.debug("Session state persistence skipped: %s", exc)

    # Two-tier memory thresholds (defaults from MEMORY_EVICT_PCT / MEMORY_CIRCUIT_BREAKER_PCT):
    #  tier 1 → evict IDLE only (gentle — session can resume cheaply)
    #  tier 2 → LOG ONLY at critical pressure (the old "kill heaviest STREAMING"
    #            circuit breaker was removed — killing STREAMING caused data loss;
    #            L1 spawn-settle + L3 continuation + macOS jetsam handle recovery)

    async def _check_memory_pressure(self) -> None:
        """Two-tier memory pressure relief.

        Tier 1 (>90%): Evict ALL IDLE units (heaviest first) until memory
          drops below threshold or no IDLE units remain.  Previous behavior
          evicted only one per 60s cycle — too slow when memory spikes.
        Tier 2 (>95%): LOG ONLY — all IDLE evicted but memory still critical.
          Does NOT kill STREAMING sessions (the old circuit breaker that killed
          the heaviest STREAMING session was removed — it caused data loss,
          context truncation, and broken responses). Recovery is delegated to
          L1 (spawn-settle window), L3 (session_unit continuation hint), and
          macOS jetsam as the last resort.

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
            # P-a AC3 — prewarm units are INTENTIONALLY *not* exempt here (unlike
            # the orphan reaper + TTL, which exempt `prewarm-`). This is a RAM
            # SURVIVAL path: a prewarm is a luxury that must yield to real memory
            # pressure. Exempting it would turn an unadopted prewarm into an
            # unreclaimable memory black hole (the regression XG's "B" forbids).
            # Do NOT add a prewarm skip to this loop.
            idle_units = sorted(
                [u for u in self._router.list_units()
                 if u.state == SessionState.IDLE],
                key=_rss, reverse=True,
            )
            evicted = 0
            for victim in idle_units:
                # AC5 (TOCTOU): idle_units was snapshotted above; a victim may have
                # left IDLE since (adopted by a desktop tab → STREAMING, or a drained
                # pending message started a turn) across the awaits in this loop
                # (_build_hook_context, a prior victim's kill). Re-read live state and
                # skip a unit that is no longer IDLE — never kill a now-active turn.
                # Mirrors the recheck in _proactive_rss_restart (:490).
                if victim.state != SessionState.IDLE:
                    continue
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
            # OFF-LOOP (run_a1f4c2d8): exists + read in one hop. A DailyActivity file
            # grows all day, so this is not a trivially small read.
            def _read_da() -> str:
                if not da_path.exists():
                    return ""
                return da_path.read_text(encoding="utf-8")

            da_content = ""
            try:
                da_content = await asyncio.to_thread(_read_da)
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
                    loop = asyncio.get_running_loop()
                    ppid_result = await loop.run_in_executor(
                        _subprocess_executor,
                        functools.partial(
                            subprocess.run,
                            ["ps", "-o", "ppid=", "-p", str(pid)],
                            capture_output=True, text=True, timeout=5,
                        ),
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

        except Exception as exc:  # noqa: BLE001
            # Fail-safe (never kill on uncertainty) is correct and stays. But False
            # also means "not an orphan", so a systematically failing check silently
            # retires the orphan reaper and leaks processes with nothing to show why.
            logger.warning("orphan-ownership check failed, NOT reaping (fail safe): %s",
                           exc)
            return False

    async def _read_process_owner_pid(self, pid: int) -> int | None:
        """Read SWARMAI_OWNER_PID from a process's environment.

        Delegates to ``session_utils.read_owner_pid()`` (single source
        of truth) via dedicated subprocess executor for non-blocking I/O.
        """
        from .session_utils import read_owner_pid
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(_subprocess_executor, read_owner_pid, pid)

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
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            _subprocess_executor,
            functools.partial(
                subprocess.run,
                ["pgrep", "-f", pattern],
                capture_output=True, text=True, timeout=5,
            ),
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
