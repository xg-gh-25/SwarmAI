"""ResourceMonitor — system + per-process resource metrics with spawn budget.

Singleton module providing lightweight, cached system and process metrics
for spawn gating, OOM-aware retry, resource-aware eviction, and API/UI
surface.  Uses ``psutil`` for cross-platform metrics with a pure-macOS
fallback via ``vm_stat`` / ``ps`` if psutil is unavailable.

Public symbols:

- ``resource_monitor`` — Module-level singleton instance.
- ``ResourceMonitor``  — Class (rarely used directly; prefer the singleton).
- ``ResourceMonitor.compute_max_tabs`` — Dynamic tab limit from available RAM.
- ``SystemMemory``     — Frozen dataclass for system RAM state.
- ``ProcessMetrics``   — Frozen dataclass for per-subprocess metrics.
- ``SpawnBudget``      — Frozen dataclass for can-spawn decision + reasoning.

Design reference:
    ``Knowledge/Notes/2026-03-19-resource-observability-design.md`` §1
"""
from __future__ import annotations

import logging
import subprocess
import time
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

# ── Attempt psutil import ──────────────────────────────────────────

try:
    import psutil
    _HAS_PSUTIL = True
except ImportError:
    psutil = None  # type: ignore[assignment]
    _HAS_PSUTIL = False
    logger.warning(
        "psutil not installed — resource_monitor will use macOS fallback "
        "(limited accuracy). Install with: pip install psutil"
    )


# ── Dataclasses ─────────────────────────────────────────────────────

@dataclass(frozen=True)
class SystemMemory:
    """Snapshot of system RAM state.

    All values in bytes except ``percent_used`` (0.0–100.0).

    IMPORTANT: On macOS, ``used`` (active + wired) significantly
    underestimates real memory pressure.  For resource gating decisions
    (spawn budget, tab limits) always use ``effective_used`` which equals
    ``total - available`` — the best proxy for the memory-pressure signal
    jetsam responds to (jetsam tracks the kernel pressure signal, not a fixed
    %-of-total, but this metric correlates with it far better than ``used``).
    """
    total: int
    available: int
    used: int
    percent_used: float

    @property
    def effective_used(self) -> int:
        """Memory considered 'in use' for resource gating.

        On macOS, ``psutil.virtual_memory().used`` returns only
        active + wired pages (~39% on a typical 36GB machine), while
        ``percent`` reports ~72% because it uses ``(total - available) / total``.
        The pressure signal jetsam responds to correlates with the latter, so
        our spawn gates use it too.

        ``effective_used = total - available`` aligns with ``percent_used``
        and is our best proxy for real macOS memory pressure.
        """
        return self.total - self.available

    @property
    def pressure_level(self) -> str:
        """Classify memory pressure: ok / warning / critical.

        Aligned with the 90% tab-creation threshold:
        - >= 90% → critical (no new tabs allowed)
        - >= 80% → warning  (approaching limit)
        - <  80% → ok
        """
        if self.percent_used >= 90.0:
            return "critical"
        elif self.percent_used >= 80.0:
            return "warning"
        return "ok"


@dataclass(frozen=True)
class ProcessMetrics:
    """Per-subprocess resource metrics."""
    pid: int
    session_id: str
    rss_bytes: int
    cpu_percent: float  # 0.0-100.0 per core
    num_threads: int
    state: str  # SessionState name
    uptime_seconds: float


@dataclass(frozen=True)
class SpawnBudget:
    """Decision on whether a new subprocess can be spawned.

    ``can_spawn`` is the gate check; ``reason`` explains why not.
    """
    can_spawn: bool
    reason: str
    available_mb: float
    estimated_cost_mb: float
    headroom_mb: float = 512.0


# ── ResourceMonitor ────────────────────────────────────────────────

class ResourceMonitor:
    """Lightweight, cached system + process resource monitor.

    Invariants:

    - ``system_memory()`` caches for ``_CACHE_TTL`` seconds (default 5s).
    - ``spawn_budget()`` always reads fresh system_memory.
    - ``process_metrics()`` is a one-shot, no caching (called per health check).
    - Module-level singleton pattern (same as session_manager).
    - Never raises — all methods return safe defaults on failure.
    """

    _CACHE_TTL: float = 5.0  # seconds
    # Spawn cost — no-data fallback (fresh boot, before any adaptive sample).
    # The incremental cost of one more session is the CLI MAIN-process RSS
    # (~300-500MB measured), NOT the full tree (tree adds ~1050MB of MCP
    # children → inflates ~5×; see lifecycle_manager._sample_process_memory,
    # which deliberately records main-process RSS for this reason). 1200 is a
    # DELIBERATELY conservative fallback (~2-3× the measured main-RSS cost) so
    # a zero-evidence fresh boot under-provisions rather than over-commits
    # memory before the first lifecycle tick supplies real samples.
    # (Historical note: earlier comments claiming a "verified 1400-1600MB"
    # cost were wrong — that was worst-case tree/launch-spike guesswork; the
    # real OOM cause was retry storms + kill/respawn churn, since fixed.)
    # Adaptive samples take over after the first lifecycle tick.
    # On 16GB: headroom ~2GB / 1200 = 1 → max_tabs=2.
    _DEFAULT_SPAWN_COST_MB: float = 1200.0
    _HEADROOM_MB: float = 512.0  # Always keep this much free
    _MAX_SPAWN_SAMPLES: int = 20  # Rolling window for spawn cost estimation
    # Adaptive estimate must never drop below this — early samples
    # (taken ~60s after spawn, before MCPs fully load) can underestimate
    # Floor for adaptive estimate.  600MB accounts for CLI+MCP launch
    # spike (~500MB) with 20% margin.  Previous value (1200MB) was set
    # during OOM cascade fix (RC03) but overestimated 5× vs actual
    # steady-state (~300MB), blocking 3rd chat tab on 36GB machines.
    # The OOM root causes (retry storms, kill/respawn churn, cost model
    # 3× undercount) are all fixed — this floor just needs to cover the
    # transient launch spike, not the worst-case scenario.
    _MIN_SPAWN_COST_MB: float = 600.0

    # ── Dynamic tab limit constants ─────────────────────────────
    # Ceiling: 4 = 3 chat + 1 channel.  Reverted 6→4 (2026-08-02): the
    # 6-ceiling raised the *tab* count but NOT throughput — MAX_CONCURRENT_STREAMS
    # stayed at 3, so a 4th/5th concurrent stream blocked silently on the
    # 120s stream-admit wait ("静默没 response"). 4 keeps the openable chat
    # count (3) at/below the concurrent-stream cap, so more tabs never
    # outrun throughput. On smaller machines the dynamic RAM formula
    # (headroom / cost_mb) gates BELOW this ceiling anyway — e.g. a 16GB
    # machine caps at 2-3 depending on load (a near-full 16GB box floors at 2).
    _MAX_TABS_CEILING: int = 4
    _MEMORY_THRESHOLD_PCT: float = 90.0  # Never push machine past 90% used
    # Concurrent penalty: each alive session inflates the estimated spawn
    # cost to account for simultaneous peak memory spikes.  On a 16GB
    # machine with 2 sessions alive, cost becomes 1200*(1+2*0.5)=2400MB,
    # which pushes projected usage past 90% and blocks the 3rd spawn.
    # On 36GB machines the extra headroom absorbs the penalty easily.
    # Tuning: 0.3 only blocks at 12.8GB+ used on 16GB; 0.5 blocks at
    # 12.4GB+ — one Chrome tab difference.  0.5 is the safe choice.
    _CONCURRENT_PENALTY_FACTOR: float = 0.5

    def __init__(self) -> None:
        self._cached_memory: Optional[SystemMemory] = None
        self._cache_time: float = 0.0
        self._spawn_cost_samples: list[float] = []  # MB values
        self._last_max_tabs_result: Optional[int] = None  # For log-level optimization

    # ── System memory ───────────────────────────────────────────

    def system_memory(self) -> SystemMemory:
        """Get system memory snapshot (cached for _CACHE_TTL seconds).

        Never raises — returns a pessimistic estimate on failure.
        """
        now = time.time()
        if self._cached_memory and (now - self._cache_time) < self._CACHE_TTL:
            return self._cached_memory

        try:
            mem = self._read_system_memory()
            self._cached_memory = mem
            self._cache_time = now
            return mem
        except Exception as exc:
            logger.warning("Failed to read system memory: %s", exc)
            # Return pessimistic fallback (assume 90% used)
            return SystemMemory(
                total=16 * 1024**3,
                available=1600 * 1024**2,
                used=int(14.4 * 1024**3),
                percent_used=90.0,
            )

    def invalidate_cache(self) -> None:
        """Force cache refresh on next call (after spawn/kill events)."""
        self._cache_time = 0.0

    def _read_system_memory(self) -> SystemMemory:
        """Read system memory via psutil or macOS fallback."""
        if _HAS_PSUTIL:
            vm = psutil.virtual_memory()
            return SystemMemory(
                total=vm.total,
                available=vm.available,
                used=vm.used,
                percent_used=vm.percent,
            )
        # macOS fallback via vm_stat
        return self._read_memory_macos_fallback()

    def _read_memory_macos_fallback(self) -> SystemMemory:
        """Parse ``vm_stat`` output for macOS memory info."""
        try:
            result = subprocess.run(
                ["vm_stat"], capture_output=True, text=True, timeout=5,
            )
            lines = result.stdout.strip().split("\n")
            # First line: "Mach Virtual Memory Statistics: (page size of 16384 bytes)"
            page_size = 16384
            if "page size of" in lines[0]:
                page_size = int(lines[0].split("page size of ")[1].split(" ")[0])

            stats: dict[str, int] = {}
            for line in lines[1:]:
                if ":" in line:
                    key, val = line.split(":", 1)
                    val = val.strip().rstrip(".")
                    try:
                        stats[key.strip()] = int(val) * page_size
                    except ValueError:
                        pass

            free = stats.get("Pages free", 0)
            speculative = stats.get("Pages speculative", 0)
            active = stats.get("Pages active", 0)
            wired = stats.get("Pages wired down", 0)

            # Get total from sysctl
            sysctl_result = subprocess.run(
                ["sysctl", "-n", "hw.memsize"],
                capture_output=True, text=True, timeout=5,
            )
            total = int(sysctl_result.stdout.strip())

            # "used" stores active + wired for Activity Monitor compatibility.
            # But "available" uses free + speculative + inactive for accurate
            # resource gating — this matches psutil.virtual_memory().available
            # and what macOS jetsam considers when killing processes.
            used = active + wired
            available = free + speculative + stats.get("Pages inactive", 0)

            logger.debug(
                "vm_stat: active=%dMB wired=%dMB → used=%dMB (%.1f%%), "
                "available=%dMB",
                active // (1024 * 1024),
                wired // (1024 * 1024),
                used // (1024 * 1024),
                (used / total * 100) if total else 0,
                available // (1024 * 1024),
            )

            # percent_used must match (total - available) / total, NOT
            # used / total — the same metric psutil.percent uses.
            effective_used = total - available
            return SystemMemory(
                total=total,
                available=available,
                used=used,
                percent_used=round((effective_used / total) * 100, 1) if total else 90.0,
            )
        except Exception as exc:
            logger.warning("macOS memory fallback failed: %s", exc)
            raise

    # ── Spawn budget ────────────────────────────────────────────

    def spawn_budget(self, alive_count: int = 0) -> SpawnBudget:
        """Check whether a new subprocess can be safely spawned.

        Uses the same 90% rule as compute_max_tabs: if spawning one
        more session would push the machine past 90% memory usage,
        deny the spawn.

        Args:
            alive_count: Number of currently alive sessions.  Used to
                apply a concurrent penalty — each alive session inflates
                the estimated spawn cost by ``_CONCURRENT_PENALTY_FACTOR``
                to account for simultaneous peak memory spikes.  Without
                this, 3 sessions on a 16GB machine pass the budget check
                at spawn time but trigger macOS jetsam when they all peak.

        Never raises.
        """
        try:
            # OOM cooldown is handled globally in session_unit._oom_cooldown_until.
            # spawn_budget only checks memory numbers — no OOM history here.
            self.invalidate_cache()
            mem = self.system_memory()
            total_mb = mem.total / (1024 * 1024)
            # Use effective_used (total - available) — not mem.used (active + wired).
            # On macOS, mem.used underestimates real pressure by ~30%.
            # See SystemMemory.effective_used docstring for details.
            used_mb = mem.effective_used / (1024 * 1024)
            base_cost_mb = self._estimated_spawn_cost_mb()
            # Concurrent penalty: more alive sessions → higher chance of
            # simultaneous memory peaks → inflate the cost estimate.
            penalty_multiplier = 1.0 + alive_count * self._CONCURRENT_PENALTY_FACTOR
            estimated_mb = base_cost_mb * penalty_multiplier
            projected_pct = (used_mb + estimated_mb) / total_mb * 100

            if projected_pct <= self._MEMORY_THRESHOLD_PCT:
                return SpawnBudget(
                    can_spawn=True,
                    reason="ok",
                    available_mb=round(total_mb - used_mb, 1),
                    estimated_cost_mb=round(estimated_mb, 1),
                    headroom_mb=round(total_mb * (self._MEMORY_THRESHOLD_PCT / 100) - used_mb, 1),
                )
            else:
                headroom = total_mb * (self._MEMORY_THRESHOLD_PCT / 100) - used_mb
                return SpawnBudget(
                    can_spawn=False,
                    reason=(
                        f"Opening a new tab would push memory to {projected_pct:.0f}% "
                        f"(limit: {self._MEMORY_THRESHOLD_PCT:.0f}%). "
                        f"Close an idle tab or other apps to free memory."
                    ),
                    available_mb=round(total_mb - used_mb, 1),
                    estimated_cost_mb=round(estimated_mb, 1),
                    headroom_mb=round(max(0, headroom), 1),
                )
        except Exception as exc:
            logger.warning("spawn_budget check failed: %s", exc)
            # Fail CLOSED — deny spawn if we can't verify resources.
            # The first-tab exception (alive_count == 0) is enforced at
            # the SessionRouter level, not here.
            return SpawnBudget(
                can_spawn=False,
                reason=f"Resource check failed: {exc}. Close tabs or retry.",
                available_mb=0.0,
                estimated_cost_mb=self._DEFAULT_SPAWN_COST_MB,
            )

    def _estimated_spawn_cost_mb(self) -> float:
        """Estimate spawn cost from rolling samples or default.

        Returns the 75th percentile of recorded RSS samples, floored
        at ``_MIN_SPAWN_COST_MB`` to prevent early low-RSS samples
        from undercutting the safety margin.
        """
        if self._spawn_cost_samples:
            sorted_samples = sorted(self._spawn_cost_samples)
            idx = int(len(sorted_samples) * 0.75)
            estimate = sorted_samples[min(idx, len(sorted_samples) - 1)]
            return max(estimate, self._MIN_SPAWN_COST_MB)
        return self._DEFAULT_SPAWN_COST_MB

    def record_spawn_cost(self, rss_bytes: int) -> None:
        """Record actual spawn cost for future estimation.

        Called shortly after spawn when process RSS has stabilized.
        """
        cost_mb = rss_bytes / (1024 * 1024)
        self._spawn_cost_samples.append(cost_mb)
        if len(self._spawn_cost_samples) > self._MAX_SPAWN_SAMPLES:
            self._spawn_cost_samples.pop(0)
        logger.debug("Spawn cost recorded: %.1fMB (samples=%d)",
                     cost_mb, len(self._spawn_cost_samples))

    # ── Dynamic tab limit ───────────────────────────────────────

    def compute_max_tabs(self) -> int:
        """Compute dynamic tab limit: how many tabs can open without
        pushing machine memory past 90%.

        Formula: ``max(2, min(floor(headroom / cost), ceiling))``

        Uses adaptive spawn cost from lifecycle_manager samples when
        available (75th percentile of actual tree RSS, floored at
        ``_MIN_SPAWN_COST_MB``), falls back to ``_DEFAULT_SPAWN_COST_MB``.

        Returns [2, 4]. Always allows at least 2 (1 chat + 1 channel).
        """
        mem = self.system_memory()
        total_mb = mem.total / (1024 * 1024)
        # Use effective_used for correct macOS memory pressure.
        used_mb = mem.effective_used / (1024 * 1024)
        headroom_mb = total_mb * (self._MEMORY_THRESHOLD_PCT / 100.0) - used_mb
        # Use adaptive estimate when available — learned from real RSS data.
        cost_mb = self._estimated_spawn_cost_mb()
        raw = int(headroom_mb / cost_mb)
        # Minimum 2: guarantees 1 chat slot + 1 dedicated channel slot.
        # Without this, channel messages could starve when memory is tight.
        result = max(2, min(raw, self._MAX_TABS_CEILING))
        # Log at INFO only when result changes or headroom is low (<3GB).
        # Avoids 3000+ INFO lines/day when system is stable.
        _LOW_HEADROOM_MB = 3072.0
        changed = (self._last_max_tabs_result is not None
                   and result != self._last_max_tabs_result)
        try:
            low_headroom = float(headroom_mb) < _LOW_HEADROOM_MB
        except (TypeError, ValueError):
            low_headroom = True  # Conservative: if we can't tell, log it
        log_fn = logger.info if (changed or low_headroom
                                 or self._last_max_tabs_result is None) else logger.debug
        log_fn(
            "compute_max_tabs: used=%.0fMB/%.0fMB (%.1f%%) headroom_to_90%%=%.0fMB "
            "cost=%.0fMB raw=%d result=%d pressure=%s",
            used_mb, total_mb, mem.percent_used,
            headroom_mb, cost_mb, raw, result, mem.pressure_level,
        )
        self._last_max_tabs_result = result
        return result

    # ── Process metrics ─────────────────────────────────────────

    def process_metrics(
        self, pid: int, session_id: str, state: str,
    ) -> Optional[ProcessMetrics]:
        """Collect metrics for a single subprocess.

        Returns None if the process is dead or metrics collection fails.
        No caching — called per health check cycle (60s interval).
        """
        if not _HAS_PSUTIL:
            return self._process_metrics_fallback(pid, session_id, state)
        try:
            proc = psutil.Process(pid)
            mem_info = proc.memory_info()
            cpu = proc.cpu_percent(interval=0)  # Non-blocking
            create_time = proc.create_time()
            return ProcessMetrics(
                pid=pid,
                session_id=session_id,
                rss_bytes=mem_info.rss,
                cpu_percent=cpu,
                num_threads=proc.num_threads(),
                state=state,
                uptime_seconds=round(time.time() - create_time, 1),
            )
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            return None
        except Exception as exc:
            logger.debug("process_metrics failed for pid %d: %s", pid, exc)
            return None

    def _process_metrics_fallback(
        self, pid: int, session_id: str, state: str,
    ) -> Optional[ProcessMetrics]:
        """macOS fallback using ``ps`` for process metrics."""
        try:
            result = subprocess.run(
                ["ps", "-o", "rss=,pcpu=,nlwp=,etime=", "-p", str(pid)],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode != 0:
                return None
            line = result.stdout.strip()
            if not line:
                return None
            parts = line.split()
            rss_kb = int(parts[0]) if len(parts) > 0 else 0
            cpu = float(parts[1]) if len(parts) > 1 else 0.0
            return ProcessMetrics(
                pid=pid,
                session_id=session_id,
                rss_bytes=rss_kb * 1024,
                cpu_percent=cpu,
                num_threads=1,  # ps doesn't reliably report threads on macOS
                state=state,
                uptime_seconds=0.0,
            )
        except Exception as exc:  # noqa: BLE001
            # This is the FALLBACK reader (psutil unavailable / ps path). A silent None
            # here means "no metrics for this process", which callers read as nothing to
            # act on — so both the primary and the fallback failing looks identical to a
            # healthy idle process. Log so the fallback's own failures are visible.
            logger.warning("ps-based process metrics fallback failed for pid=%s: %s",
                           pid, exc)
            return None

    def process_rss(self, pid: int) -> int:
        """Get RSS of the main process only (bytes), excluding children.

        Used for spawn cost estimation — the incremental cost of one more
        session is the CLI process itself, not the entire tree (which
        includes MCP children that inflate the estimate 5×).
        Returns 0 on failure.
        """
        if not _HAS_PSUTIL:
            return 0
        try:
            return psutil.Process(pid).memory_info().rss
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            return 0  # expected: process gone / not ours — silence is correct here
        except Exception as exc:  # noqa: BLE001
            # Degrade-OBSERVABLE (GC19). 0 is a LOAD-BEARING LIE for RSS: it reads as
            # "this process uses no memory", which is exactly the input the RSS kill
            # thresholds (3.5GB proactive / 7GB streaming) act on. A silently-failing
            # reader therefore doesn't degrade to "unknown" — it degrades to "healthy",
            # permanently disabling the very guard it feeds. Log the unforeseen case;
            # the narrow handler above still covers the expected one.
            logger.warning("process_rss(%s) failed, reporting 0: %s", pid, exc)
            return 0

    def process_tree_rss(self, pid: int) -> int:
        """Get total RSS of a process and all its children (bytes).

        Useful for measuring actual memory footprint (CLI + MCP subprocesses).
        Returns 0 on failure.
        """
        if not _HAS_PSUTIL:
            return 0
        try:
            parent = psutil.Process(pid)
            total = parent.memory_info().rss
            for child in parent.children(recursive=True):
                try:
                    total += child.memory_info().rss
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
            return total
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            return 0  # expected: process gone / not ours
        except Exception as exc:  # noqa: BLE001
            # Same load-bearing lie as process_rss, but worse: this is the TREE total,
            # the number the streaming RSS check compares against 7GB. Silent 0 → the
            # kill never fires.
            logger.warning("process_tree_rss(%s) failed, reporting 0: %s", pid, exc)
            return 0

    def tree_cpu_seconds(self, pid: int) -> Optional[float]:
        """Sum cumulative CPU seconds (user+system) over a process tree.

        Used as a LIVENESS signal to distinguish a wedged tool from a slow
        one (run_fb6e94a9): sample this twice over a short interval — a
        positive delta means the CLI subprocess OR any of its descendants
        (a Bash child, an Agent sub-agent's own CLI) is actively burning
        CPU, i.e. *working*, not hung. A near-zero delta over the interval
        means nothing in the tree is doing work — a genuine deadlock.

        Returns the cumulative CPU seconds across the tree, or ``None`` when
        the value cannot be obtained (psutil missing, process gone, or
        platform fallback unavailable). Callers MUST treat ``None`` as
        "cannot prove dead" and fail SAFE — never interrupt on ``None``.
        """
        if not _HAS_PSUTIL:
            return self._tree_cpu_seconds_fallback(pid)
        try:
            parent = psutil.Process(pid)
            t = parent.cpu_times()
            total = float(t.user + t.system)
            for child in parent.children(recursive=True):
                try:
                    ct = child.cpu_times()
                    total += float(ct.user + ct.system)
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
            return total
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            return None
        except Exception as exc:
            logger.debug("tree_cpu_seconds failed for pid %d: %s", pid, exc)
            return None

    def _tree_cpu_seconds_fallback(self, pid: int) -> Optional[float]:
        """macOS/no-psutil fallback: cumulative CPU seconds via ``ps``.

        ``ps -o cputime=`` reports cumulative CPU time as ``[dd-]hh:mm:ss``
        for the single PID. Children are NOT summed in the fallback (``ps``
        has no cheap recursive-tree sum), so this is a conservative
        UNDER-estimate of tree CPU — which is the safe direction: it can
        only make a busy tree look less busy, biasing toward NOT interrupting.
        Returns ``None`` on any failure (caller fails safe).
        """
        try:
            result = subprocess.run(
                ["ps", "-o", "cputime=", "-p", str(pid)],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode != 0:
                return None
            raw = result.stdout.strip()
            if not raw:
                return None
            # Parse [dd-]hh:mm:ss or mm:ss into seconds.
            days = 0
            if "-" in raw:
                d, raw = raw.split("-", 1)
                days = int(d)
            parts = [float(p) for p in raw.split(":")]
            secs = 0.0
            for p in parts:
                secs = secs * 60 + p
            return secs + days * 86400
        except Exception as exc:  # noqa: BLE001
            # Parsing ps's elapsed-time format. None reads as "no CPU time known", which
            # silently removes this process from any CPU-based judgement. Low stakes
            # relative to RSS, but the same lying-None shape — log it.
            logger.debug("cpu-seconds fallback parse failed for pid=%s: %s", pid, exc)
            return None


# ── Module-level singleton ──────────────────────────────────────────

resource_monitor = ResourceMonitor()
