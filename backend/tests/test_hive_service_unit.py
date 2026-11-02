"""The Hive's systemd unit (hive/swarmai-hive.service): a task's command that runs out
of memory must not take the whole Hive down with it.

A Hive runs the daemon and every task's builds and tests in the one service cgroup. A
single heavy command (a large repository's type check) once went over the unit's fixed
6 GB limit: the kernel killed that command and systemd's default OOMPolicy=stop then
stopped the whole service, ending every run on the Hive. The unit keeps the service
running when one of its processes is killed for memory (OOMPolicy=continue: the command
fails, the agent sees it). A Hive's machine is there for its runs: the service has no
memory cap or CPU quota of its own; the daemon's spawn budget decides admission."""
from __future__ import annotations

import re
from pathlib import Path

UNIT = Path(__file__).resolve().parents[2] / "hive" / "swarmai-hive.service"


def _service() -> dict[str, str]:
    out: dict[str, str] = {}
    section = None
    for raw in UNIT.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("["):
            section = line
            continue
        if section == "[Service]" and "=" in line:
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def test_a_command_killed_for_memory_does_not_stop_the_hive():
    assert _service().get("OOMPolicy") == "continue"


def test_the_service_may_use_the_whole_machine():
    svc = _service()
    assert svc.get("MemoryMax", "infinity") == "infinity"
    for key in ("MemoryHigh", "CPUQuota", "MemorySwapMax"):
        assert key not in svc, f"{key}: a Hive's machine is there for its runs"
