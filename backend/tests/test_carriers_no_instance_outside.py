"""Only the carriers look a Hive up by id (design docs/design/2026-11-20-swarm-out-multi-carrier.md
§4.3, §13 invariant 2; R2.2).

Question: does any module under backend/core, backend/routers or backend/jobs outside
backend/core/carriers call (or define) ``_instance(`` / ``_instance_up(``? Every per-run
operation must resolve the run's handle and go through its carrier, and every machine-level
one through the carrier's location: a request to an AgentCore session that has already
ended boots a new, empty microVM, so no caller may reach a machine by itself.
"""
from __future__ import annotations

import re
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
PATTERN = re.compile(r"(^|[^a-zA-Z_])_instance(_up)?\(")
ROOTS = ("core", "routers", "jobs")
ALLOWED = BACKEND / "core" / "carriers"


def _offenders() -> list[str]:
    out = []
    for root in ROOTS:
        for path in sorted((BACKEND / root).rglob("*.py")):
            if ALLOWED in path.parents:
                continue
            for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if PATTERN.search(line):
                    out.append(f"{path.relative_to(BACKEND)}:{n}: {line.strip()}")
    return out


def test_no_hive_lookup_outside_the_carriers():
    assert _offenders() == []


def test_the_pattern_finds_what_it_must_and_nothing_else():
    hits = ["inst = await _instance(hive_id)", "await swarm_out._instance(row['hive_id'])",
            "x = await so._instance_up(ref, deadline)", "async def _instance(ref):", "_instance(ref)"]
    misses = ["_validate_instance(inst, acct)", "p._get_instance(id)", "my_instance(x)",
              "from core.carriers.ec2 import instance as _instance", "_instance = other"]
    assert all(PATTERN.search(h) for h in hits)
    assert not any(PATTERN.search(m) for m in misses)
    # the carriers themselves are where the lookup lives
    assert any(PATTERN.search(ln) for ln in (ALLOWED / "ec2.py").read_text(encoding="utf-8").splitlines())
