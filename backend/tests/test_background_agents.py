"""Tests for core.background_agents and its wiring: a background Agent's status
outlives the turn that started it.

What is tested:
- the registry: launch → running; the CLI's terminal task frame → completed /
  failed / stopped (killed counts as stopped), with duration, tools and summary; a
  finish seen before the launch is applied at launch; a foreground agent's task never
  becomes an entry; lose_all marks only running agents lost; a finish is announced
  once (take_unannounced), a loss never; bounded
- the stream reader hands task frames to on_task with no turn open
- SessionUnit: the reader's task frames reach the registry; a closed reader loses the
  running agents
- SessionRouter: a change publishes a `background_agents` envelope outside any turn and
  a final status is written into the stored Agent result
- messages.update_agent_result: rewrites only the matching top-level Agent result
"""
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from claude_agent_sdk import SystemMessage
from claude_agent_sdk._internal.message_parser import parse_message

from core.background_agents import MAX_AGENTS, BackgroundAgents, normalize_status


def _reg():
    seen: list[dict] = []
    return BackgroundAgents(on_change=seen.append), seen


def test_launch_then_finish_reports_running_then_completed():
    r, seen = _reg()
    r.launch("ag1", "Measure REFLECT")
    r.task_started("t1", "ag1")
    r.task_finished("t1", "completed", summary=" done ", usage={"duration_ms": 252000, "tool_uses": 47})
    assert [e["status"] for e in seen] == ["running", "completed"]
    assert seen[-1]["durationMs"] == 252000 and seen[-1]["toolUses"] == 47 and seen[-1]["summary"] == "done"
    assert r.running() == []


def test_killed_is_stopped_and_non_terminal_is_ignored():
    assert normalize_status("killed") == "stopped"
    assert normalize_status("running") is None
    assert normalize_status("CANCELLED") == "stopped"


def test_the_terminal_status_list_is_one_definition():
    """B2: every CLI terminal status (the SDK's set included) maps to a registry
    status, and the stream reader keeps no list of its own."""
    import inspect

    from claude_agent_sdk.types import TERMINAL_TASK_STATUSES

    import core.cli_stream_reader as csr
    from core.background_agents import CLI_TERMINAL_STATUSES, TERMINAL
    assert TERMINAL_TASK_STATUSES <= CLI_TERMINAL_STATUSES
    assert {normalize_status(s) for s in CLI_TERMINAL_STATUSES} <= TERMINAL
    assert not hasattr(csr, "_TERMINAL")
    assert "normalize_status" in inspect.getsource(csr)
    r, seen = _reg()
    r.launch("ag1")
    r.task_started("t1", "ag1")
    r.task_finished("t1", "running")
    assert [e["status"] for e in seen] == ["running"]
    r.task_finished("t1", "killed")
    assert seen[-1]["status"] == "stopped"


def test_a_finish_before_the_launch_is_applied_at_launch():
    """The reader routes frames before the orchestrator reads them."""
    r, seen = _reg()
    r.task_started("t1", "ag1")
    r.task_finished("t1", "failed")
    assert seen == []
    r.launch("ag1", "x")
    assert [e["status"] for e in seen] == ["failed"]


def test_a_foreground_agent_never_becomes_an_entry():
    r, seen = _reg()
    r.task_started("t9", "fg")
    r.task_finished("t9", "completed")
    assert r.snapshot() == [] and seen == []


def test_lose_all_marks_only_running_agents_and_is_never_announced():
    r, seen = _reg()
    r.launch("a"), r.launch("b")
    r.task_finished("tb", "completed", tool_use_id="b")
    r.lose_all()
    st = {e["toolUseId"]: e["status"] for e in r.snapshot()}
    assert st == {"a": "lost", "b": "completed"}
    assert [a["toolUseId"] for a in r.take_unannounced()] == ["b"]
    assert r.take_unannounced() == []


def test_the_registry_is_bounded():
    r, _ = _reg()
    for i in range(MAX_AGENTS + 10):
        r.launch(f"a{i}")
    assert len(r.snapshot()) == MAX_AGENTS


# ── wiring ──────────────────────────────────────────────────────────────


class _Client:
    def __init__(self) -> None:
        self.q: asyncio.Queue = asyncio.Queue()

    async def receive_messages(self):
        while True:
            item = await self.q.get()
            if item is None:
                return
            msg = parse_message(item)
            if msg is not None:
                yield msg


def _task_frame(subtype: str, **kw) -> dict:
    return {"type": "system", "subtype": subtype, "uuid": "u", "session_id": "S", **kw}


async def test_reader_hands_task_frames_to_on_task_with_no_turn_open():
    from core.cli_stream_reader import CliStreamReader
    c = _Client()
    got: list = []
    r = CliStreamReader(c, "s", on_task=lambda sub, m: got.append((sub, m)))
    r.start()
    c.q.put_nowait(_task_frame("task_started", task_id="t1", description="d", tool_use_id="ag1"))
    c.q.put_nowait(_task_frame("task_notification", task_id="t1", status="completed",
                               output_file="", summary="ok", tool_use_id="ag1"))
    for _ in range(50):
        if len(got) == 2:
            break
        await asyncio.sleep(0.01)
    assert [s for s, _ in got] == ["task_started", "task_notification"]
    assert r._open is None
    r.close()


def test_session_unit_feeds_its_registry_and_a_closed_reader_loses_agents():
    from core.session_unit import SessionUnit
    unit = SessionUnit(session_id="bg-unit", agent_id="default")
    events: list = []
    unit._on_cli_event = lambda sid, kind: events.append(kind)
    unit.background_agents.launch("ag1", "d")
    unit._on_reader_task("task_started", parse_message(
        _task_frame("task_started", task_id="t1", description="d", tool_use_id="ag1")))
    unit._on_reader_task("task_updated", parse_message(
        {"type": "system", "subtype": "task_updated", "task_id": "t1", "patch": {"status": "killed"}}))
    assert unit.background_agents.snapshot()[0]["status"] == "stopped"
    unit.background_agents.launch("ag2", "e")
    unit._on_reader_error()
    assert {e["toolUseId"]: e["status"] for e in unit.background_agents.snapshot()}["ag2"] == "lost"
    assert events.count("background_agent") >= 3


async def test_router_publishes_outside_any_turn_and_stores_the_final_status():
    from core import session_events
    from core.session_router import SessionRouter
    from core.session_unit import SessionUnit
    router = SessionRouter.__new__(SessionRouter)
    unit = SessionUnit(session_id="bg-router", agent_id="default")
    router._units = {"bg-router": unit}
    unit._on_cli_event = router._on_unit_cli_event
    hub = session_events.SessionEventHub()
    store = AsyncMock(return_value=True)
    with patch.object(session_events, "hub", return_value=hub), \
            patch("database.db.messages.update_agent_result", store):
        unit.background_agents.launch("ag1", "Measure")
        unit.background_agents.task_finished("t1", "completed", tool_use_id="ag1",
                                             usage={"duration_ms": 1000})
        await asyncio.sleep(0.05)
    envs = list(hub._logs["bg-router"].buf)
    assert [e["type"] for e in envs] == ["background_agents", "background_agents"]
    assert envs[-1]["turnId"] == "" and envs[-1]["agents"][0]["status"] == "completed"
    store.assert_awaited_once()
    sid, tid, sub = store.await_args.args
    assert (sid, tid, sub["status"], sub["durationMs"]) == ("bg-router", "ag1", "completed", 1000)


async def test_update_agent_result_rewrites_only_the_top_level_agent_result(tmp_path):
    from database.sqlite import SQLiteMessagesTable
    from database import sqlite as sq
    db_path = tmp_path / "t.db"
    import aiosqlite
    async with aiosqlite.connect(db_path) as conn:
        await conn.execute("CREATE TABLE messages (id TEXT PRIMARY KEY, session_id TEXT, role TEXT, "
                           "content TEXT, created_at TEXT, updated_at TEXT)")
        blocks = [{"type": "tool_result", "tool_use_id": "ag1", "outcome": "done",
                   "subagent": {"status": "background"}},
                  {"type": "tool_result", "tool_use_id": "ag1x", "parent_tool_use_id": "ag1"}]
        await conn.execute("INSERT INTO messages VALUES (?,?,?,?,?,?)",
                           ("m1", "s1", "assistant", json.dumps(blocks), "2026-10-21T18:51", "x"))
        await conn.execute("INSERT INTO messages VALUES (?,?,?,?,?,?)",
                           ("m2", "s2", "assistant", json.dumps(blocks), "2026-10-21T18:51", "x"))
        await conn.commit()
    t = SQLiteMessagesTable("messages", db_path)
    assert await t.update_agent_result("s1", "ag1", {"status": "completed", "durationMs": 5}) is True
    assert await t.update_agent_result("s1", "nope", {"status": "completed"}) is False
    async with aiosqlite.connect(db_path) as conn:
        rows = dict(await (await conn.execute("SELECT id, content FROM messages")).fetchall())
    m1 = json.loads(rows["m1"])
    assert m1[0]["subagent"] == {"status": "completed", "durationMs": 5}
    assert "subagent" not in m1[1]
    assert json.loads(rows["m2"])[0]["subagent"] == {"status": "background"}  # other session untouched
