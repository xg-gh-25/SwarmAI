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
    ``total - available`` — the metric macOS jetsam actually considers.
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
        Jetsam kills based on the latter, so our spawn gates must too.

        ``effective_used = total - available`` aligns with ``percent_used``
        and matches what macOS considers real memory pressure.
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
    # Spawn cost: actual CLI tree RSS is 1400-1600MB (verified from
    # lifecycle_manager logs 2026-04-12).  Old value of 500MB allowed 3
    # concurrent chat sessions → 4.5GB → macOS jetsam SIGKILL cascade.
    # No-data default (fresh boot): conservative because we have zero
    # evidence.  Adaptive samples take over after first lifecycle tick.
    # On 16GB machine: headroom ~2GB / 1200 = 1 → max_tabs=2 (safe).
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
    # Ceiling: 4 = 3 chat + 1 channel.  On 36GB machines this is safe
    # as long as retry/proactive-restart bugs are fixed (the real OOM
    # cause was retry storms + kill/respawn churn, not 3 sessions).
    # On smaller machines the dynamic formula gates via cost_mb.
    _MAX_TABS_CEILING: int = 4
    _MEMORY_THRESHOLD_PCT: float = 90.0  # Never push machine past 90% used

    def __init__(self) -> None:
        self._cached_memory: Optional[SystemMemory] = None
        self._cache_time: float = 0.0
        self._spawn_cost_samples: list[float] = []  # MB values

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

    def spawn_budget(self) -> SpawnBudget:
        """Check whether a new subprocess can be safely spawned.

        Uses the same 90% rule as compute_max_tabs: if spawning one
        more session (~500MB) would push the machine past 90% memory
        usage, deny the spawn.

        Also denies spawns during the OOM cooldown period (Fix 4) —
        after a recent OOM kill, the system needs time to reclaim
        memory before we try spawning another heavy process.

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
            estimated_mb = self._estimated_spawn_cost_mb()
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
        1200MB), falls back to ``_SPAWN_COST_MB`` (1500MB).

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
        logger.info(
            "compute_max_tabs: used=%.0fMB/%.0fMB (%.1f%%) headroom_to_90%%=%.0fMB "
            "cost=%.0fMB raw=%d result=%d pressure=%s",
            used_mb, total_mb, mem.percent_used,
            headroom_mb, cost_mb, raw, result, mem.pressure_level,
        )
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
        except Exception:
            return None

    def process_tree_rss(self, pid: int) -> int:
        """Get total RSS of a process and all its children (bytes).

        Useful for measuring actual spawn cost (CLI + MCP subprocesses).
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
            return 0
        except Exception:
            return 0


# ── Module-level singleton ──────────────────────────────────────────

resource_monitor = ResourceMonitor()
