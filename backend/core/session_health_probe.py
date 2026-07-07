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
signal — (a) CPU-delta over an interval AND (b) log-progress: did this session emit
a REAL turn event (streaming/transition/result_usage — NOT the ~60s
lifecycle_manager.memory_sample housekeeping heartbeat, which fires for wedged
sessions too) RECENTLY (within _PROGRESS_RECENCY_S). See _session_log_progressed.
We FAIL SAFE: if either signal is unreadable (None / no log access), we DO NOT
alarm. A muted probe is worse than a quiet one.
"""
from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from typing import Callable, Optional

logger = logging.getLogger(__name__)

# Defaults — mirror the production hang-probe so behavior is consistent.
DEFAULT_RSS_THRESHOLD_MB = 3500          # PROACTIVE reclaim threshold (36GB machine)
CPU_PROBE_INTERVAL_S = 2.0               # same window as session_unit._tool_hang_probe
CPU_LIVE_EPSILON = 0.05                  # cpu-seconds delta above which = "working"

# Log events that indicate a GENUINE TERMINAL failure in the window. A bare match
# is not enough — see scan_unrecovered_events for the "followed by recovery" logic.
#
# ⚠️ These are FAILURES, NOT recovery actions (run_67a391a4). Do NOT add
# `force_unstick` / `stuck` here: `force_unstick*` (session_unit.force_unstick /
# force_unstick_waiting_input) is the daemon's own self-heal ACTION — it logs the
# marker then UNCONDITIONALLY runs `_arm_recovery_checkpoint → _crash_to_cold_async`
# (session_unit.py:4378), so the line IS the recovery, never a fault. `stuck` only
# ever appears inside `recovery_checkpoint_armed ... trigger=stuck_*` (a recovery
# REASON field), never a standalone failure. Both were false-positive markers that
# flagged the daemon self-healing itself as "unrecovered failures". The recovery
# vocab below (force_unstick / recovery_checkpoint_armed) absolves them.
_FAILURE_MARKERS = ("streaming_timeout", "SIGKILL", "dumb-spawn-kill", "output_liveness_timeout")
# Recovery markers (M2): specific, low-false-positive SELF-HEAL EVENTS — never a
# bare generic transition. Two self-heal paths are absolved:
#   • streaming_timeout → Retry N/N ... --resume  (retry path)
#   • {streaming_timeout|force_unstick*} → recovery_checkpoint_armed → transition
#     ... to=cold → force_kill_tree            (crash-to-COLD path, NO Retry line)
# Both crash-to-COLD sequences carry `force_unstick` AND `recovery_checkpoint_armed`
# for the same sid — those two specific events ARE the proof of self-heal, so we
# match THEM, not the generic `to=cold` transition.
#
# ⚠️ Do NOT add bare `to=cold` here (Gate-2 multi-specialist HIGH, run_67a391a4):
# `transition ... to=cold` fires for EVERY cold transition — routine idle→cold
# reclaim, dead→cold cleanup, user-closed-tab — not just crash-to-cold recovery.
# Matching it would let a benign same-sid cold transition within the recovery
# window silently absolve a GENUINE unrecovered failure (a false-negative that
# blinds the monitor). `force_unstick`/`recovery_checkpoint_armed` are specific
# self-heal events that a routine reclaim does NOT emit — they close the loop
# without the blinding hole.
_RECOVERY_PATTERN = re.compile(
    r"Retry \d+/\d+|--resume|recovered|_crash_to_cold|HealingLoop"
    r"|force_unstick|recovery_checkpoint_armed"
)
# How many lines AFTER a failure marker count as "the recovery window". A
# recovery for an UNRELATED later session must not absolve an earlier failure.
_RECOVERY_WINDOW_LINES = 40
# Extract a session-id-ish token to correlate failure↔recovery on the same session.
_SID_RE = re.compile(r"\b([0-9a-f]{8})\b")

# ── Progress-signal tuning (run_6b10ea1c) ──────────────────────────────────
# The ONLY per-session line the daemon emits BETWEEN turn events is
# lifecycle_manager.memory_sample (~60s cadence) — and it fires for EVERY live
# session, wedged ones included. So it is NOT a turn-progress signal: it is
# session-independent housekeeping that both (a) can't be seen in a 2s window
# and (b) would falsely "rescue" a genuinely wedged session. We EXCLUDE it and
# judge progress by whether a REAL turn event for the session appeared within a
# recency window LARGER than the heartbeat period.
_HOUSEKEEPING_MARKERS = ("lifecycle_manager", "memory_sample")
# > memory_sample cadence (~60s measured) so a genuinely-progressing turn whose
# last event predates one heartbeat is not missed; small enough that a real
# wedge (no event for minutes) is still caught.
_PROGRESS_RECENCY_S = 90.0
# Leading "YYYY-MM-DD HH:MM:SS" stamp on daemon.log lines (comma-millis ignored).
_LOG_TS_RE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})")


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
        if not any(m in line for m in _FAILURE_MARKERS):
            continue
        # Recovery must appear WITHIN a bounded window after the failure (M2) —
        # an unrelated later session's Retry must not absolve this failure.
        window = lines[i + 1 : i + 1 + _RECOVERY_WINDOW_LINES]
        fail_sid = _SID_RE.search(line)
        recovered = False
        for wl in window:
            if not _RECOVERY_PATTERN.search(wl):
                continue
            # If the failure line carried a session id, the recovery in the
            # window must mention the SAME id (correlated). If no id was present,
            # a windowed recovery marker is accepted (best effort).
            #
            # Token-equality, NOT substring (Gate-2 HIGH, run_67a391a4): compare
            # the failure sid against the recovery line's OWN extracted sids, so a
            # coincidental hex fragment (pid, uuid slice) sharing that 8-hex run
            # can't mis-absolve. `fail_sid.group(1) in wl` would match e.g.
            # "deadbeef" inside "deadbeef-1234-…" of an UNRELATED session.
            if fail_sid is None or fail_sid.group(1) in _SID_RE.findall(wl):
                recovered = True
                break
        if not recovered:
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
        # Read the log window ONCE; progress is now a recency check over it
        # (real turn event within _PROGRESS_RECENCY_S), not a 2s before/after delta.
        log_now = log_reader()
        for s in streaming:
            pid = s.get("pid") or s.get("claude_pid")
            sid = str(s.get("session_id", "?"))[:8]
            if not pid:
                continue  # no pid → cannot probe → skip (fail-safe)
            progressed = _session_log_progressed(sid, log_now)
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
    log_text: str,
    *,
    now: Optional[datetime] = None,
    recency_s: float = _PROGRESS_RECENCY_S,
) -> Optional[bool]:
    """Has this session emitted a REAL turn event recently?

    Recency-based (run_6b10ea1c), replacing the old 2s before/after delta. The
    daemon's only per-session line between turn events is the ~60s
    ``lifecycle_manager.memory_sample`` heartbeat — which (a) can't be seen in a
    2s window and (b) fires for wedged sessions too. So the old delta declared a
    healthy Bedrock-IO-wait turn "no progress" (→ false wedge) and could let a
    housekeeping line mask a genuine wedge.

    Now: scan the log for lines that mention ``sid`` AND are NOT housekeeping
    (``lifecycle_manager`` / ``memory_sample``). Of those real turn-event lines,
    take the most recent parseable timestamp.

    Returns:
      - ``True``  — a real event for this session within ``recency_s`` → working.
      - ``False`` — real events exist but the latest is older than ``recency_s``
        (stale → candidate wedge), OR the session appears ONLY in housekeeping
        lines (no real turn activity at all).
      - ``None``  — the session id never appears in the log, or no real line has
        a parseable timestamp → cannot measure → caller FAILS SAFE (no alarm).

    Pure over ``log_text`` — no sleep, no second read. ``now`` is injectable for
    tests; defaults to wall clock.
    """
    now = now or datetime.now()
    seen_sid = False
    latest: Optional[datetime] = None
    for ln in log_text.splitlines():
        if sid not in ln:
            continue
        seen_sid = True
        if any(m in ln for m in _HOUSEKEEPING_MARKERS):
            continue  # housekeeping heartbeat — not a turn-progress signal
        m = _LOG_TS_RE.match(ln)
        if not m:
            continue
        try:
            ts = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
        if latest is None or ts > latest:
            latest = ts

    if latest is not None:
        return (now - latest) <= timedelta(seconds=recency_s)
    if seen_sid:
        return False  # only housekeeping lines → no real turn activity
    return None       # not visible at all → unknown → fail safe
