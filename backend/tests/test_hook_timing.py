"""core/hook_timing.py — per-hook durations, one summary line per hook per minute.

What is tested: a registered hook's calls are timed (also when it raises); the
summary line carries n / p50 / p95 / max; flush resets; a no_timeout (HITL) hook
is not timed; the wrapper returns and raises exactly what the hook does.
"""
from __future__ import annotations

import asyncio
import logging

import pytest

from core import hook_timing as ht
from core.hook_builder import HookRegistry


@pytest.fixture(autouse=True)
def _fresh():
    ht._reset_for_tests()
    yield
    ht._reset_for_tests()


def test_summary_line_per_hook():
    for ms in (1, 2, 3, 4, 100):
        ht.record("worktree_write_gate", ms / 1000)
    ht.record("bash_syntax_guard", 0.02)
    lines = ht.flush()
    assert lines == [
        "hook_timing name=bash_syntax_guard n=1 p50=20ms p95=20ms max=20ms",
        "hook_timing name=worktree_write_gate n=5 p50=3ms p95=100ms max=100ms",
    ]
    assert ht.flush() == []


def test_registered_hooks_are_timed_hitl_hooks_are_not(caplog):
    async def fast(i, t, c):
        await asyncio.sleep(0.01)
        return {"decision": "approve"}

    async def boom(i, t, c):
        raise ValueError("x")

    reg = HookRegistry()
    reg.register("PostToolUse", fast, "fast_hook")
    reg.register("PostToolUse", boom, "boom_hook", matcher="Bash")
    reg.register("PostToolUse", fast, "hitl_hook", matcher="AskUserQuestion", no_timeout=True)
    hooks = {(e, m): fns for (e, m), fns in reg._hooks.items()}
    f = hooks[("PostToolUse", None)][0][1]
    b = hooks[("PostToolUse", "Bash")][0][1]
    h = hooks[("PostToolUse", "AskUserQuestion")][0][1]
    assert asyncio.run(f({}, None, None)) == {"decision": "approve"}
    with pytest.raises(ValueError):
        asyncio.run(b({}, None, None))
    asyncio.run(h({}, None, None))
    with caplog.at_level(logging.INFO, logger="core.hook_timing"):
        lines = ht.flush()
    names = [l.split()[1] for l in lines]
    assert names == ["name=boom_hook", "name=fast_hook"]
    assert int(lines[1].split("max=")[1].rstrip("ms")) >= 10
