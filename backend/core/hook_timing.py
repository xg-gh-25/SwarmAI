"""How long each SDK hook takes (F8d of the 2026-10-21 system-stability design).

- ``timed(name, hook)`` — wrap an async hook so each call's duration is recorded
  under *name* (also when it raises or is cancelled).
- ``flush(now=None)`` — write one INFO line per hook seen since the last flush:
  ``hook_timing name=… n=… p50=…ms p95=…ms max=…ms``, then reset. Called by
  ``timed`` itself once ``FLUSH_INTERVAL_S`` has passed, so no task or timer is
  needed and a quiet daemon writes nothing.

Before this, no hook logged its own duration (gate_decisions.jsonl records only
denies), so a slow gate (the worktree gate failing open after 3s, 324 times a
week) could only be inferred. One line per hook per minute keeps it out of the
noise. Durations of human-in-the-loop hooks (registered ``no_timeout``) are not
recorded: they measure how long a person took to answer.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)

FLUSH_INTERVAL_S = 60.0
_MAX_SAMPLES = 2000  # per hook per interval; beyond it only n and max are updated

_lock = threading.Lock()
# name -> [count, max_s, samples]
_stats: "dict[str, list]" = {}
_last_flush = time.monotonic()


def record(name: str, seconds: float) -> None:
    with _lock:
        st = _stats.get(name)
        if st is None:
            st = _stats[name] = [0, 0.0, []]
        st[0] += 1
        if seconds > st[1]:
            st[1] = seconds
        if len(st[2]) < _MAX_SAMPLES:
            st[2].append(seconds)


def _pct(sorted_vals: "list[float]", q: float) -> float:
    if not sorted_vals:
        return 0.0
    i = min(len(sorted_vals) - 1, int(q * (len(sorted_vals) - 1) + 0.5))
    return sorted_vals[i]


def flush(now: "float | None" = None) -> "list[str]":
    """Log and return the summary lines of the interval; reset the counters."""
    global _last_flush
    with _lock:
        stats = dict(_stats)
        _stats.clear()
        _last_flush = time.monotonic() if now is None else now
    lines = []
    for name in sorted(stats):
        n, mx, samples = stats[name]
        s = sorted(samples)
        lines.append(f"hook_timing name={name} n={n} p50={_pct(s, .5) * 1000:.0f}ms "
                     f"p95={_pct(s, .95) * 1000:.0f}ms max={mx * 1000:.0f}ms")
    for line in lines:
        logger.info(line)
    return lines


def _maybe_flush() -> None:
    if time.monotonic() - _last_flush >= FLUSH_INTERVAL_S:
        flush()


def timed(name: str, hook: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
    async def _timed(input_data: Any, tool_use_id: Any, context: Any) -> Any:
        t0 = time.monotonic()
        try:
            return await hook(input_data, tool_use_id, context)
        finally:
            record(name, time.monotonic() - t0)
            try:
                _maybe_flush()
            except Exception:  # noqa: BLE001 — timing must never break a hook
                logger.debug("hook_timing flush failed", exc_info=True)

    _timed.__name__ = getattr(hook, "__name__", name)
    _timed.__wrapped__ = hook  # type: ignore[attr-defined]
    for attr in ("_decision_logged",):
        if hasattr(hook, attr):
            setattr(_timed, attr, getattr(hook, attr))
    return _timed


def _reset_for_tests() -> None:
    global _last_flush
    with _lock:
        _stats.clear()
        _last_flush = time.monotonic()
