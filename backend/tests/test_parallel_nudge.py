"""parallel_nudge: one batching reminder per round of tool results, never a deny."""

from unittest.mock import MagicMock, patch

import pytest

from core import parallel_nudge
from core.parallel_nudge import NUDGE, create_parallel_nudge_hook


def _ctx_of(result: dict) -> str | None:
    return (result.get("hookSpecificOutput") or {}).get("additionalContext")


@pytest.mark.asyncio
async def test_first_call_of_a_round_carries_the_nudge():
    hook = create_parallel_nudge_hook({})
    out = await hook({"tool_name": "Read"}, "t1", None)
    assert out["hookSpecificOutput"]["hookEventName"] == "PostToolUse"
    assert _ctx_of(out) == NUDGE
    assert "doesn't depend on another's result" in NUDGE


@pytest.mark.asyncio
async def test_calls_of_one_parallel_step_get_one_copy_and_the_next_round_gets_another():
    ctx: dict = {}
    hook = create_parallel_nudge_hook(ctx)
    clock = iter([100.0, 100.4, 100.9, 109.0])
    with patch.object(parallel_nudge.time, "monotonic", lambda: next(clock)):
        first = await hook({"tool_name": "Read"}, "t1", None)
        second = await hook({"tool_name": "Grep"}, "t2", None)
        third = await hook({"tool_name": "Bash"}, "t3", None)
        next_round = await hook({"tool_name": "Read"}, "t4", None)
    assert _ctx_of(first) == NUDGE
    assert second == {} and third == {}
    assert _ctx_of(next_round) == NUDGE


@pytest.mark.asyncio
async def test_sessions_do_not_share_the_window():
    a = create_parallel_nudge_hook({})
    b = create_parallel_nudge_hook({})
    assert _ctx_of(await a({"tool_name": "Read"}, "t1", None)) == NUDGE
    assert _ctx_of(await b({"tool_name": "Read"}, "t2", None)) == NUDGE


@pytest.mark.asyncio
async def test_fails_open_without_injecting():
    class Broken(dict):
        def get(self, *a, **k):
            raise RuntimeError("boom")

    hook = create_parallel_nudge_hook(Broken())
    assert await hook({"tool_name": "Read"}, "t1", None) == {}


@pytest.mark.asyncio
async def test_build_hooks_delivers_the_nudge_after_any_tool():
    from core.hook_builder import build_hooks

    pm = MagicMock()
    pm.is_command_approved = MagicMock(return_value=False)
    ctx: dict = {"sdk_session_id": "s"}
    hooks, _, _ = await build_hooks(
        agent_config={"enable_tool_logging": False, "global_user_mode": True},
        enable_skills=False, enable_mcp=False, resume_session_id=None,
        session_context=ctx, permission_manager=pm,
    )
    all_tools = [m for m in hooks["PostToolUse"] if m.matcher in (None, "", "*")]
    assert all_tools, "the nudge must be registered for every tool, not one matcher"
    got = []
    for m in all_tools:
        for fn in m.hooks:
            got.append(_ctx_of(await fn({"hook_event_name": "PostToolUse",
                                         "tool_name": "WebFetch", "tool_input": {}},
                                        "t1", None)))
    assert NUDGE in got
