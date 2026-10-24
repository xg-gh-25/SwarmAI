"""Background agent progress (docs/design/2026-10-24-bg-agent-stop-livereload.md D1).

What is tested:
- ``BackgroundAgents.progress``: counts a running agent's tool calls, keeps the newest
  tool and the last activity time; an unknown or finished agent is untouched; it calls
  ``on_progress`` (never ``on_change``, which persists terminal results).
- ``SessionUnit``: a background frame from the reader reaches the registry; progress
  publishes are coalesced per session (one at once, then at most one trailing per
  window); a terminal change cancels the trailing timer, and the terminal snapshot
  still goes out at once.
Method: real BackgroundAgents and SessionUnit; the unit's cli-event callback records
what the router would publish; the window is shrunk to keep the test fast.
"""
from __future__ import annotations

import asyncio

import pytest
from claude_agent_sdk import AssistantMessage, ToolUseBlock, UserMessage

from core import session_unit as su
from core.background_agents import BackgroundAgents
from core.session_unit import SessionUnit


def _bash(n: int) -> AssistantMessage:
    return AssistantMessage(content=[ToolUseBlock(id=f"t{n}", name="Bash", input={})],
                            model="m", parent_tool_use_id="ag")


def test_progress_counts_tools_of_a_running_agent_only():
    changes, progress = [], []
    bg = BackgroundAgents(on_change=changes.append, on_progress=progress.append)
    bg.launch("ag", "review")
    changes.clear()
    assert bg.progress("ag", ["Bash"]) is True
    assert bg.progress("ag", ["Read", "Grep"]) is True
    assert bg.progress("ag", []) is True  # a frame with no call: still activity
    e = bg.snapshot()[0]
    assert e["toolUses"] == 3 and e["lastTool"] == "Grep" and e["lastActivityAt"] >= e["startedAt"]
    assert changes == [], "progress never goes through on_change (that one persists ends)"
    assert len(progress) == 3
    assert bg.progress("nope", ["Bash"]) is False
    bg.task_started("k", "ag")
    bg.task_finished("k", "completed")
    assert bg.progress("ag", ["Bash"]) is False, "a finished agent is not touched"
    assert bg.snapshot()[0]["toolUses"] == 3


@pytest.mark.asyncio
async def test_unit_coalesces_progress_and_terminal_goes_out_at_once(monkeypatch):
    monkeypatch.setattr(su, "BG_PROGRESS_PUBLISH_S", 0.05)
    events: list[str] = []
    unit = SessionUnit(session_id="s-bg", agent_id="default",
                       on_cli_event=lambda sid, kind: events.append(kind))
    unit.background_agents.launch("ag", "review")
    events.clear()
    for n in range(10):
        unit._on_reader_bg_frame("ag", _bash(n))
    assert events == ["background_agent"], "leading edge only"
    await asyncio.sleep(0.12)
    assert events == ["background_agent", "background_agent"], "one trailing publish"
    assert unit.background_agents.snapshot()[0]["toolUses"] == 10
    # a frame, then the end inside the window: the end goes out now, no stale trailing
    events.clear()
    unit._on_reader_bg_frame("ag", UserMessage(content="x", parent_tool_use_id="ag"))
    unit._on_reader_bg_frame("ag", _bash(11))
    unit.background_agents.task_started("k", "ag")
    unit.background_agents.task_finished("k", "completed")
    n_after_end = len(events)
    await asyncio.sleep(0.12)
    assert len(events) == n_after_end, "the trailing timer was cancelled by the end"
    assert unit._bg_agent_changes and unit._bg_agent_changes[-1]["status"] == "completed"


def test_unit_ignores_a_frame_of_a_foreground_agent():
    events: list[str] = []
    unit = SessionUnit(session_id="s-fg", agent_id="default",
                       on_cli_event=lambda sid, kind: events.append(kind))
    unit._on_reader_bg_frame("not-background", _bash(1))
    assert events == []
