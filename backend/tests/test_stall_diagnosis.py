"""Tests for core.stall_diagnosis and its use by the heartbeat writer.

What is tested:
- classify: the first applicable cause in order low_battery → memory → cpu → busy →
  unknown; macOS "warn" pressure alone is not a memory shortage (it is common at
  8GB free); ``starved`` only for the three outside shortages
- busy_with: the innermost frame of our own code on the loop thread; None when the
  loop thread is waiting (an idle innermost frame)
- the heartbeat: a stall writes `stall` into the file, cached for
  _STALL_DIAG_INTERVAL_S; no stall → no key (and the cache is dropped)
"""
from __future__ import annotations

import json
import sys
import threading
import types

import pytest

from core import heartbeat as hb
from core import stall_diagnosis as sd


@pytest.mark.parametrize("kw,cause", [
    (dict(battery_pct=3, discharging=True, mem_percent=95, load_per_core=4), "low_battery"),
    (dict(battery_pct=3, discharging=False, mem_percent=95), "memory"),
    (dict(mem_level="critical", mem_percent=70), "memory"),
    (dict(mem_level="warn", mem_percent=81, load_per_core=0.4), "unknown"),
    (dict(mem_percent=80, load_per_core=12.9), "cpu"),
    (dict(load_per_core=0.3, busy="routers/artifacts.py:get_artifact_drift"), "busy"),
    (dict(), "unknown"),
])
def test_classify(kw, cause):
    base = dict(load_per_core=None, mem_percent=None, mem_level=None, battery_pct=None, discharging=None, busy=None)
    assert sd.classify(**{**base, **kw}) == cause
    assert (cause in sd.STARVED_CAUSES) == (cause in ("low_battery", "memory", "cpu"))


def _frame_chain(*specs):
    """A fake frame chain, innermost first: (filename, function)."""
    nxt = None
    for filename, func in reversed(specs):
        nxt = types.SimpleNamespace(f_code=types.SimpleNamespace(co_filename=filename, co_name=func), f_back=nxt)
    return nxt


def test_busy_with_names_our_innermost_frame():
    f = _frame_chain(("/x/lib/python3.12/pathlib.py", "read_text"),
                     ("/Users/a/swarmai/backend/core/run_worktree.py", "_iter_runs"),
                     ("/Users/a/swarmai/backend/routers/artifacts.py", "get_artifact_drift"))
    assert sd.busy_with(f, lambda _f: False) == "core/run_worktree.py:_iter_runs"
    assert sd.busy_with(f, lambda _f: True) is None  # loop thread waiting
    assert sd.busy_with(None, lambda _f: False) is None


def test_heartbeat_writes_the_stall_while_stalled(tmp_path, monkeypatch):
    monkeypatch.setattr(hb, "HEARTBEAT_PATH", tmp_path / "heartbeat")
    calls = []
    monkeypatch.setattr(sd, "diagnose", lambda frame, idle: calls.append(1) or {"cause": "cpu", "starved": True, "loadPerCore": 12.9})
    monkeypatch.setattr(hb, "_format_starvation_stacks", lambda *a, **k: "")
    monkeypatch.setattr(hb, "_battery_status", lambda *a, **k: "")
    monkeypatch.setattr(hb, "_stall_cache", None)
    monkeypatch.setattr(hb, "_loop_thread_id", threading.get_ident())
    t = [1000.0]
    monkeypatch.setattr(hb.time, "monotonic", lambda: t[0])
    monkeypatch.setattr(hb, "_last_loop_tick", 990.0)  # 10s stall
    hb._write_heartbeat_once()
    body = json.loads((tmp_path / "heartbeat").read_text())
    assert body["stall"] == {"cause": "cpu", "starved": True, "loadPerCore": 12.9}
    t[0] = 1002.0
    hb._write_heartbeat_once()
    assert len(calls) == 1  # cached within the interval
    t[0] = 1010.0
    hb._write_heartbeat_once()
    assert len(calls) == 2
    monkeypatch.setattr(hb, "_last_loop_tick", 1009.5)  # the loop ticks again
    hb._write_heartbeat_once()
    assert "stall" not in json.loads((tmp_path / "heartbeat").read_text())
    assert hb._stall_cache is None
