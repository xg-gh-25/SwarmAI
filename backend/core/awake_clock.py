"""The clock for liveness decisions: wall-clock time that does not advance while
the Mac sleeps.

- ``awake_time()`` — a ``time.time()``-like float (epoch seconds) for stamping and
  comparing "how long since X" when the answer decides a timeout or a stall.

Why: on 2026-10-20 the Mac slept 15:18-15:30; at 15:31 the lifecycle watchdog
computed stall=842s from ``time.time()`` and force-killed a turn that had been
awake for ~97s. 11 of 15 streaming timeouts that week came within 3 minutes of a
wake. ``time.monotonic()`` does not count sleep on macOS (measured 2026-10-21:
151h of 364h uptime), so this clock is wall time minus the sleep since import.

Use it for durations that DECIDE something (stall, timeout, idle, hang). Keep
``time.time()`` for instants that are stored, shown, or compared with file mtimes.
Both sides of a comparison must use the same clock.

A test that monkeypatches ``time.time`` to a small fake clock keeps working: the
computed sleep is then negative, and the fake time is returned unchanged.
"""
from __future__ import annotations

import time

_WALL0 = time.time()
_MONO0 = time.monotonic()


def awake_time() -> float:
    """Epoch seconds, minus the time the machine has slept since import."""
    now = time.time()
    slept = (now - _WALL0) - (time.monotonic() - _MONO0)
    return now - slept if slept > 0 else now
