"""Stall diagnosis — WHY the backend's event loop is not scheduling, in a form the
UI can say ("the CPU is fully used", "the Mac is low on memory", "busy with X").

The heartbeat writer thread (core/heartbeat.py) calls :func:`diagnose` when the
loop has not ticked for a few seconds and writes the result into the heartbeat
file as ``stall``; the Tauri watchdog passes it to the UI with its
degraded / not-responding events. Runs on that writer thread only, never the loop.

Probes (each bounded, each optional — a failed probe leaves its field out):
- CPU: 1-minute load average per core, and the busiest processes (``ps``, 2s cap).
- memory: percent used (psutil) and, on macOS, the kernel's own pressure level
  (``kern.memorystatus_vm_pressure_level``: 1 normal, 2 warn, 4 critical).
- power: battery percent and whether it is discharging (``pmset -g batt``).
- the loop thread: the innermost frame in our own code (``busyWith``), or none
  when the loop thread is waiting (then something else has the CPU).

``cause`` (first that applies): ``low_battery`` (≤10% and discharging) →
``memory`` (kernel critical, or ≥92% used) → ``cpu`` (load ≥1.5 per core) →
``busy`` (the loop thread is running our code) → ``unknown``.
``starved`` is True for the first three: an outside shortage, not our code stuck.

@exports diagnose, classify, STARVED_CAUSES
"""
from __future__ import annotations

import logging
import os
import re
import subprocess
import sys
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

STARVED_CAUSES = frozenset({"low_battery", "memory", "cpu"})
_PROBE_TIMEOUT_S = 2.0
_APP_DIRS = ("core", "routers", "hooks", "jobs", "channels", "database", "skills", "middleware", "utils")
_APP_FRAME_RE = re.compile(r"(?:^|/)(?:backend/)?((?:%s)/[\w/]+\.py)$" % "|".join(_APP_DIRS))


def _cpu_count() -> int:
    return os.cpu_count() or 1


def _load_per_core() -> Optional[float]:
    try:
        return round(os.getloadavg()[0] / _cpu_count(), 2)
    except (OSError, AttributeError):
        return None


def _memory() -> tuple[Optional[float], Optional[str]]:
    pct: Optional[float] = None
    level: Optional[str] = None
    try:
        import psutil
        pct = round(float(psutil.virtual_memory().percent), 1)
    except Exception as exc:  # noqa: BLE001 — optional probe
        logger.debug("stall_diagnosis: probe failed: %s", exc)
        pct = None
    if sys.platform == "darwin":
        try:
            out = subprocess.run(["sysctl", "-n", "kern.memorystatus_vm_pressure_level"],
                                 capture_output=True, text=True, timeout=_PROBE_TIMEOUT_S).stdout.strip()
            level = {"1": "normal", "2": "warn", "4": "critical"}.get(out)
        except Exception as exc:  # noqa: BLE001 — optional probe
            logger.debug("stall_diagnosis: probe failed: %s", exc)
            level = None
    return pct, level


def _top_processes(limit: int = 3) -> list[dict]:
    try:
        out = subprocess.run(["ps", "-Aro", "pcpu=,comm="], capture_output=True, text=True,
                             timeout=_PROBE_TIMEOUT_S).stdout
    except Exception as exc:  # noqa: BLE001 — optional probe
        logger.debug("stall_diagnosis: probe failed: %s", exc)
        return []
    rows: list[dict] = []
    for line in out.splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) != 2:
            continue
        try:
            cpu = float(parts[0])
        except ValueError:
            continue
        if cpu < 20:
            break  # ps -r sorts by CPU: the rest is quieter
        rows.append({"name": os.path.basename(parts[1].strip())[:40], "cpu": round(cpu)})
        if len(rows) >= limit:
            break
    return rows


def _battery() -> tuple[Optional[int], Optional[bool]]:
    if sys.platform != "darwin":
        return None, None
    try:
        out = subprocess.run(["pmset", "-g", "batt"], capture_output=True, text=True,
                             timeout=_PROBE_TIMEOUT_S).stdout
    except Exception as exc:  # noqa: BLE001 — optional probe
        logger.debug("stall_diagnosis: probe failed: %s", exc)
        return None, None
    m = re.search(r"(\d+)%;\s*([a-zA-Z ]+);", out)
    if not m:
        return None, None
    return int(m.group(1)), "discharging" in m.group(2).lower()


def busy_with(loop_frame: Any, is_idle: Callable[[Any], bool]) -> Optional[str]:
    """``core/x.py:function`` — the innermost frame of our own code on the loop
    thread, or None when the loop thread is waiting (idle innermost frame)."""
    if loop_frame is None or is_idle(loop_frame):
        return None
    f = loop_frame
    while f is not None:
        m = _APP_FRAME_RE.search(f.f_code.co_filename.replace("\\", "/"))
        if m:
            return f"{m.group(1)}:{f.f_code.co_name}"
        f = f.f_back
    return None


def classify(*, load_per_core: Optional[float], mem_percent: Optional[float], mem_level: Optional[str],
             battery_pct: Optional[int], discharging: Optional[bool], busy: Optional[str]) -> str:
    if battery_pct is not None and discharging and battery_pct <= 10:
        return "low_battery"
    if mem_level == "critical" or (mem_percent is not None and mem_percent >= 92):
        return "memory"
    if load_per_core is not None and load_per_core >= 1.5:
        return "cpu"
    if busy:
        return "busy"
    return "unknown"


def diagnose(loop_frame: Any, is_idle: Callable[[Any], bool]) -> dict:
    """The stall's cause and evidence (camelCase: it goes to the UI as is)."""
    load = _load_per_core()
    pct, level = _memory()
    batt, discharging = _battery()
    busy = busy_with(loop_frame, is_idle)
    cause = classify(load_per_core=load, mem_percent=pct, mem_level=level,
                     battery_pct=batt, discharging=discharging, busy=busy)
    out: dict = {"cause": cause, "starved": cause in STARVED_CAUSES}
    if load is not None:
        out["loadPerCore"] = load
    if pct is not None:
        out["memPercent"] = pct
    if level is not None:
        out["memPressure"] = level
    if batt is not None:
        out["battery"] = batt
        out["discharging"] = bool(discharging)
    if busy:
        out["busyWith"] = busy
    if cause == "cpu":
        top = _top_processes()
        if top:
            out["topCpu"] = top
    return out
