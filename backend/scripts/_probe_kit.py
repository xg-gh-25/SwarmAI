"""_probe_kit — the shared contract of the product eval probes (scripts/probe_*.py).

A probe checks one product capability. Each check function returns
``(holds, why)``; a probe runs it in one of two modes:

- positive: the check on the real code with a working fixture. Prints
  ``<NAME>_OK`` (exit 0) or ``<NAME>_FAIL <why>`` (exit 1).
- ``negative``: the same check on input where it must fail (a broken fixture,
  or the capability switched off). Prints ``<NAME>_TEETH`` (exit 0) when the
  check failed as it should, ``<NAME>_FAIL negative passed on broken input``
  (exit 1) when it did not. Never prints ``<NAME>_OK``.

eval_runner._run_case_command runs probes with a temporary SWARM_DATA_DIR,
SWARM_WORKSPACE / SWARMWS and HOME, so a probe can build its fixtures under
those and never touches a user's real data. Fixtures that must not depend on
that isolation use ``tempdir()``.
"""
from __future__ import annotations

import asyncio
import contextlib
import sys
import tempfile
from pathlib import Path
from typing import Callable

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

Check = Callable[[], "tuple[bool, str]"]


def verdict(name: str, negative: bool, positive: Check, broken: Check) -> int:
    if not negative:
        holds, why = positive()
        if holds:
            print(f"{name}_OK")
            return 0
        print(f"{name}_FAIL {why}")
        return 1
    holds, why = broken()
    if holds:
        print(f"{name}_FAIL negative passed on broken input")
        return 1
    print(f"{name}_TEETH")
    return 0


def run(probes: dict[str, Callable[[bool], int]], argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    negative = bool(args) and args[-1] == "negative"
    if negative:
        args = args[:-1]
    if not args or args[0] not in probes:
        print(f"usage: <{'|'.join(probes)}> [negative]", file=sys.stderr)
        return 2
    return probes[args[0]](negative)


@contextlib.contextmanager
def tempdir():
    with tempfile.TemporaryDirectory(prefix="swarm-probe-") as d:
        yield Path(d)


def arun(coro):
    return asyncio.run(coro)


def all_hold(*checks: "tuple[bool, str]") -> "tuple[bool, str]":
    for holds, why in checks:
        if not holds:
            return False, why
    return True, ""
