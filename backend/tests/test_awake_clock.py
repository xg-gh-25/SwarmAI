"""Liveness durations exclude the time the Mac sleeps (core/awake_clock.py, F1).

What is tested: with a fake wall clock that jumps 900s and a monotonic clock that
moves 10s (a sleep), awake_time() advances 10s; a streaming SessionUnit whose last
progress was stamped before the sleep reports a 10s stall, not 910s, so the
lifecycle watchdog (timeout 600s) does not force-kill it; an NTP step backwards
never makes the clock run ahead of wall time; a small fake time.time (as many tests
patch it) passes through unchanged.

Why: 2026-10-20 15:18-15:30 the Mac slept, at 15:31 stall=842s > 600s force-killed
a turn awake for ~97s; 11 of 15 streaming timeouts that week came within 3 min of
a wake.
"""
from __future__ import annotations

import pytest

import core.awake_clock as ac


@pytest.fixture
def clocks(monkeypatch):
    c = {"wall": ac._WALL0 + 100.0, "mono": ac._MONO0 + 100.0}
    monkeypatch.setattr(ac.time, "time", lambda: c["wall"])
    monkeypatch.setattr(ac.time, "monotonic", lambda: c["mono"])
    return c


def test_sleep_is_not_counted(clocks):
    t1 = ac.awake_time()
    clocks["wall"] += 900.0
    clocks["mono"] += 10.0
    assert ac.awake_time() - t1 == pytest.approx(10.0)


def test_awake_seconds_are_counted(clocks):
    t1 = ac.awake_time()
    clocks["wall"] += 30.0
    clocks["mono"] += 30.0
    assert ac.awake_time() - t1 == pytest.approx(30.0)


def test_wall_clock_stepping_back_never_runs_ahead(clocks):
    clocks["wall"] -= 5.0
    assert ac.awake_time() == clocks["wall"]


def test_small_fake_wall_clock_passes_through(monkeypatch):
    monkeypatch.setattr(ac.time, "time", lambda: 1000.0)
    assert ac.awake_time() == 1000.0


def test_a_turn_stamped_before_sleep_is_not_stalled_after_wake(clocks):
    from core.session_unit import SessionState, SessionUnit

    unit = SessionUnit(session_id="awake-clock", agent_id="default")
    unit._transition(SessionState.IDLE)
    unit._transition(SessionState.STREAMING)
    unit._last_progress_time = ac.awake_time()
    clocks["wall"] += 900.0   # asleep 15 min
    clocks["mono"] += 10.0    # awake 10s
    stall = unit.streaming_stall_seconds
    assert stall == pytest.approx(10.0)
    assert stall < 600  # LifecycleManager's streaming timeout does not fire


def test_every_reader_of_last_used_compares_on_the_awake_clock():
    """unit.last_used is stamped with awake_time(). A `now - x.last_used` whose now
    comes from time.time() is off by every sleep since boot (session_router's
    eviction grace was, after 7046eff8). Structural: in backend code, a function
    that subtracts `.last_used` must not call time.time()."""
    import ast
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    offenders = []
    for path in [*root.glob("core/**/*.py"), *root.glob("routers/*.py"),
                 *root.glob("channels/**/*.py"), *root.glob("jobs/**/*.py"),
                 *root.glob("hooks/*.py"), root / "main.py"]:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            subs_last_used = any(
                isinstance(n, ast.BinOp) and isinstance(n.op, ast.Sub)
                and isinstance(n.right, ast.Attribute) and n.right.attr == "last_used"
                for n in ast.walk(fn))
            if not subs_last_used:
                continue
            wall = any(
                isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                and n.func.attr == "time" and isinstance(n.func.value, ast.Name)
                and n.func.value.id in ("time", "_time")
                for n in ast.walk(fn))
            if wall:
                offenders.append(f"{path.relative_to(root)}:{fn.name}")
    assert offenders == [], offenders


def test_eviction_grace_survives_a_sleep(clocks):
    """A unit used 10s (awake) ago before a 15-min sleep is still inside the
    eviction grace."""
    from core.session_router import SessionRouter
    from core.session_unit import SessionState, SessionUnit
    unit = SessionUnit(session_id="evict-grace", agent_id="default")
    unit._transition(SessionState.IDLE)
    unit.last_used = ac.awake_time()
    clocks["wall"] += 900.0
    clocks["mono"] += 10.0
    import core.session_router as sr
    assert sr.awake_time() - unit.last_used == pytest.approx(10.0)
    assert sr.awake_time() - unit.last_used < SessionRouter.EVICTION_GRACE_SECONDS
