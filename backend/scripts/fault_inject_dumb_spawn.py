#!/usr/bin/env python3
"""Fault-injection harness: dumb-spawn watchdog (run_4596411e, GS_RTH003).

ACTIVELY injects a zero-event STREAMING spawn stalled past
DUMB_SPAWN_TIMEOUT_SECONDS and drives the REAL
``lifecycle_manager._check_streaming_timeout`` watchdog, asserting it calls
``unit.force_unstick_streaming()`` — the STEERING #11 contract (force the real
recovery path to execute). Mirrors the shipped test pattern
``test_lifecycle_watchdog.py::TestDumbSpawnWatchdog`` (RP45: reuse the real
driver, don't reinvent).

Modes:
  (default)    inject stall > threshold → watchdog MUST fire → print
               WATCHDOG_KILLED ok, exit 0. (positive: real kill path executed)
  --negative   inject stall < threshold → watchdog must NOT fire → print
               NON_VACUOUS ok, exit 0. Proves the watchdog DISCRIMINATES (a
               vacuous always-kill would fail this). If the watchdog wrongly
               fires within the window, exit 1.

Isolation: MagicMock unit + mock router; never a live session. Background pid
33855 / session 89b71059 (2026-06-25, run_6c482b10) is the incident this guards.
"""
from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

_BACKEND = Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))


def _make_dumb_unit(*, stall: float, sdk_session_id=None, adaptive_timeout: float = 600.0):
    """Zero-event STREAMING unit stalled `stall`s ago (mirrors the shipped helper)."""
    from core.session_unit import SessionState

    t = time.time() - stall
    unit = MagicMock()
    unit.state = SessionState.STREAMING
    unit.session_id = "dumb-inject"
    unit.pid = 99999
    unit._last_event_time = t
    unit._streaming_start_time = t  # last_event <= start → no event since spawn
    unit._sdk_session_id = sdk_session_id
    unit._consecutive_unstick_timeouts = 0
    unit._open_tool_uses = None  # must be falsy or the open-tool guard skips dumb path
    unit._UNSTICK_CIRCUIT_BREAKER_THRESHOLD = 2
    unit._compute_message_timeout = MagicMock(return_value=adaptive_timeout)
    unit.force_unstick_streaming = AsyncMock()
    # Tool-free CPU-liveness gate (run_dcd668a6): _check_streaming_timeout now
    # reads unit.TOOL_FREE_HARD_CEILING_S and consults _tool_free_hang_verdict
    # before force_unstick, sparing only a PROVABLY CPU-busy ('working') process.
    # A dumb spawn (zero SDK events, silent) is a genuine wedge → the default
    # verdict is 'wedged', so the force-unstick MUST still fire. Mirrors the
    # sibling test_lifecycle_watchdog.py:786 mock contract (RP45 reuse the
    # proven driver). Without these two attrs the gate's `stall <= ceiling`
    # raises TypeError (float <= MagicMock).
    unit.TOOL_FREE_HARD_CEILING_S = 1800.0
    unit._tool_free_hang_verdict = AsyncMock(return_value="wedged")

    def _stall_prop(_self):
        now = time.time()
        if unit._last_event_time is None:
            return (now - unit._streaming_start_time) if unit._streaming_start_time else None
        return now - unit._last_event_time

    type(unit).streaming_stall_seconds = property(_stall_prop)
    return unit


def _drive_watchdog(unit) -> None:
    """Run the REAL lifecycle_manager._check_streaming_timeout against one unit."""
    from core.lifecycle_manager import LifecycleManager

    router = MagicMock()
    router.list_units.return_value = [unit]
    mgr = LifecycleManager(router=router)
    asyncio.run(mgr._check_streaming_timeout())


def main(argv: list[str] | None = None) -> int:
    from core.session_unit import DUMB_SPAWN_TIMEOUT_SECONDS

    argv = argv if argv is not None else sys.argv[1:]
    negative = "--negative" in argv

    if negative:
        # Within the dumb-spawn window → watchdog must NOT kill (discrimination).
        unit = _make_dumb_unit(stall=DUMB_SPAWN_TIMEOUT_SECONDS - 30, sdk_session_id=None)
        _drive_watchdog(unit)
        if unit.force_unstick_streaming.await_count == 0:
            print("NON_VACUOUS ok — watchdog correctly did NOT kill a within-window spawn")
            return 0
        print("VACUOUS FAIL — watchdog killed a healthy within-window spawn")
        return 1

    # Positive: zero-event spawn past the threshold → watchdog MUST fire.
    unit = _make_dumb_unit(stall=DUMB_SPAWN_TIMEOUT_SECONDS + 5, sdk_session_id=None)
    _drive_watchdog(unit)
    if unit.force_unstick_streaming.await_count >= 1:
        print(f"WATCHDOG_KILLED ok — force_unstick_streaming fired "
              f"(stall>{DUMB_SPAWN_TIMEOUT_SECONDS:.0f}s, awaits={unit.force_unstick_streaming.await_count})")
        return 0
    print("RECOVERY_FAILED — dumb-spawn watchdog did NOT fire on a zero-event stalled spawn")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
