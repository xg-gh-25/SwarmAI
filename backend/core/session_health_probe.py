"""Runtime session-health probe (run_f646b175).

A ZERO-LLM, deterministic snapshot of "is the daemon + its live sessions
healthy RIGHT NOW". This is the RUNTIME / dynamic-liveness axis — distinct from:
  - s_loops-health      → static self-cognition / memory / governance
  - s_health-check      → post-build assertion of critical assumptions
  - s_chat-brain-check  → frontend chat-experience contract
This probe answers only: daemon up? sessions progressing (not wedged)? RAM under
budget? deployed binary == expected? any unrecovered failure events in the window?

Wedged-detection reuses the PRODUCTION-TESTED interval CPU-delta primitive
(`resource_monitor.tree_cpu_seconds`, the same signal `session_unit._tool_hang_probe`
uses) — NOT a single-frame CPU read. RP41: a single sample reads identically for a
healthy turn waiting on Bedrock IO and a genuinely wedged one. We require a DOUBLE
signal — (a) CPU-delta over an interval AND (b) log-progress (did this session emit
new events in the window) — and we FAIL SAFE: if either signal is unreadable
(None / no log access), we DO NOT alarm. A muted probe is worse than a quiet one.
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

logger = logging.getLogger(__name__)

# Defaults — mirror the production hang-probe so behavior is consistent.
DEFAULT_RSS_THRESHOLD_MB = 3500          # PROACTIVE reclaim threshold (36GB machine)
CPU_PROBE_INTERVAL_S = 2.0               # same window as session_unit._tool_hang_probe
CPU_LIVE_EPSILON = 0.05                  # cpu-seconds delta above which = "working"

# Log events that indicate an UNRECOVERED failure in the window. A bare match is
# not enough — see _scan_unrecovered_events for the "followed by recovery" logic.
_FAILURE_MARKERS = ("force_unstick", "streaming_timeout", "SIGKILL", "stuck", "dumb-spawn-kill")
_RECOVERY_MARKERS = ("Retry", "--resume", "recovered", "heal", "COLD")


@dataclass
class Check:
    name: str
    ok: bool
    detail: str = ""


@dataclass
class ProbeResult:
    status: str                          # "healthy" | "degraded" | "error"
    checks: list[Check] = field(default_factory=list)
    summary: str = ""

    @property
    def red(self) -> bool:
        return self.status != "healthy"


def is_session_wedged(
    pid: int,
    *,
    cpu_sampler: Callable[[int], Optional[float]],
    log_progressed: Optional[bool],
    interval_s: float = CPU_PROBE_INTERVAL_S,
    epsilon: float = CPU_LIVE_EPSILON,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> bool:
    """Return True ONLY when a session is genuinely wedged (double-signal).

    RP41-safe discriminator. A session is declared wedged ONLY when BOTH hold:
      1. CPU-delta over ``interval_s`` is below ``epsilon`` (nothing in the tree
         is burning CPU), AND
      2. ``log_progressed`` is explicitly False (no new log events in the window).

    FAIL SAFE: if the CPU sample is unreadable (None) OR log progress is unknown
    (None), we CANNOT prove death → return False (do not alarm). A healthy long
    turn waiting on Bedrock IO either burns some CPU (delta >= epsilon) or emits
    streaming events (log_progressed True) — either one clears it.
    """
    cpu0 = cpu_sampler(pid)
    if cpu0 is None:
        return False  # cannot prove dead → fail safe
    sleep_fn(interval_s)
    cpu1 = cpu_sampler(pid)
    if cpu1 is None:
        return False  # cannot prove dead → fail safe
    cpu_idle = (cpu1 - cpu0) < epsilon

    # Secondary signal MUST be orthogonal and present. Unknown → fail safe.
    if log_progressed is None:
        return False
    if log_progressed:
        return False  # emitted new events → working, not wedged

    # Wedged only when CPU idle AND no log progress (both signals agree).
    return cpu_idle


def scan_unrecovered_events(log_text: str) -> list[str]:
    """Return failure markers in the window that are NOT followed by a recovery.

    A `streaming_timeout` immediately followed by `Retry 1/3 ... --resume` is the
    self-healing path working — NOT a fault. We only flag a failure marker when no
    recovery marker appears AFTER it in the remaining text.
    """
    lines = log_text.splitlines()
    unrecovered: list[str] = []
    for i, line in enumerate(lines):
        if any(m in line for m in _FAILURE_MARKERS):
            tail = "\n".join(lines[i + 1 :])
            if not any(r in tail for r in _RECOVERY_MARKERS):
                unrecovered.append(line.strip()[:160])
    return unrecovered


def session_health_probe(
    *,
    health_fetcher: Callable[[], dict],
    streaming_fetcher: Callable[[], list[dict]],
    rss_fetcher: Callable[[], float],
    cpu_sampler: Callable[[int], Optional[float]],
    log_reader: Callable[[], str],
    expected_commit: Optional[str] = None,
    deployed_commit_fetcher: Optional[Callable[[], Optional[str]]] = None,
    rss_threshold_mb: float = DEFAULT_RSS_THRESHOLD_MB,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> ProbeResult:
    """Zero-LLM runtime health snapshot. All I/O is injected for testability.

    Dependencies are passed in (not imported) so the function is pure-logic and
    unit-testable without a live daemon. The job handler wires the real fetchers.
    """
    checks: list[Check] = []

    # 1. Daemon health: /health up + db_healthy + channel_gateway started.
    try:
        h = health_fetcher()
        db_ok = bool(h.get("database", {}).get("healthy", h.get("db_healthy", False)))
        gw = h.get("channel_gateway", {})
        gw_ok = (gw.get("startup_state") in ("started", "ready")) if isinstance(gw, dict) else bool(gw)
        checks.append(Check("daemon_health", db_ok and gw_ok,
                            f"db_healthy={db_ok} channel_gateway={gw_ok}"))
    except Exception as e:
        checks.append(Check("daemon_health", False, f"/health unreachable: {type(e).__name__}: {e}"))

    # 2. Deployed commit == expected (catch "half-branch" regressions).
    if expected_commit and deployed_commit_fetcher:
        try:
            dep = deployed_commit_fetcher()
            ok = dep is not None and dep.startswith(expected_commit[:12])
            checks.append(Check("deployed_commit", ok, f"deployed={dep} expected={expected_commit[:12]}"))
        except Exception as e:
            checks.append(Check("deployed_commit", False, f"commit read failed: {e}"))

    # 3. Total RSS under budget.
    try:
        rss = rss_fetcher()
        checks.append(Check("total_rss", rss < rss_threshold_mb,
                            f"{rss:.0f}MB / {rss_threshold_mb:.0f}MB"))
    except Exception as e:
        checks.append(Check("total_rss", True, f"RSS unreadable (fail-safe pass): {e}"))

    # 4. Per-STREAMING-session wedged check (double-signal, fail-safe).
    try:
        sessions = streaming_fetcher()
        streaming = [s for s in sessions if s.get("state") in ("streaming", "STREAMING") or s.get("streaming")]
        wedged: list[str] = []
        # Read the log window ONCE before, to compute per-session progress.
        log_before = log_reader()
        for s in streaming:
            pid = s.get("pid") or s.get("claude_pid")
            sid = str(s.get("session_id", "?"))[:8]
            if not pid:
                continue  # no pid → cannot probe → skip (fail-safe)
            progressed = _session_log_progressed(sid, log_before, log_reader, sleep_fn)
            if is_session_wedged(pid, cpu_sampler=cpu_sampler, log_progressed=progressed,
                                 sleep_fn=sleep_fn):
                wedged.append(sid)
        checks.append(Check("no_wedged_sessions", not wedged,
                            f"{len(streaming)} streaming, wedged={wedged or 'none'}"))
    except Exception as e:
        checks.append(Check("no_wedged_sessions", True, f"session scan unreadable (fail-safe): {e}"))

    # 5. No UNRECOVERED failure events in the window.
    try:
        unrec = scan_unrecovered_events(log_reader())
        checks.append(Check("no_unrecovered_events", not unrec,
                            f"unrecovered={unrec or 'none'}"))
    except Exception as e:
        checks.append(Check("no_unrecovered_events", True, f"log unreadable (fail-safe): {e}"))

    failed = [c for c in checks if not c.ok]
    status = "healthy" if not failed else "degraded"
    summary = ("all checks pass" if not failed
               else "FAILED: " + ", ".join(c.name for c in failed))
    return ProbeResult(status=status, checks=checks, summary=summary)


def _session_log_progressed(
    sid: str,
    log_before: str,
    log_reader: Callable[[], str],
    sleep_fn: Callable[[float], None],
) -> Optional[bool]:
    """Did this session emit NEW log lines across the probe window?

    Returns True/False, or None when the session id never appears in the log
    (can't measure → caller fails safe). Counts lines mentioning the session id.
    """
    def _count(text: str) -> int:
        return sum(1 for ln in text.splitlines() if sid in ln)

    before = _count(log_before)
    if before == 0:
        return None  # session not visible in log → unknown → fail safe
    sleep_fn(CPU_PROBE_INTERVAL_S)
    after = _count(log_reader())
    return after > before
