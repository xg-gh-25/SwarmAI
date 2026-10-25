"""Stop receipt (core/turn_stop): what a user Stop stopped, shown and remembered.

What is tested
- The pure module: running calls / foreground Agent labels / background agents
  (``stopped``), a call behind an open permission marked ``ran: False``, caps, the
  continuation note; a user Stop makes BackgroundAgents write ``stopped``, not ``lost``.
- SessionUnit: ``begin_stop_receipt()`` reads what runs before ``interrupt()`` clears
  it; none when nothing runs; ``stop_background()`` recycles an IDLE process that runs
  background agents; the note is injected once (``_apply_stop_note``), send() calls it.
- SessionRouter.interrupt_session: the turn_stop row is persisted BEFORE the stop is
  awaited; the turn's changes / products follow; an idle session with background
  agents is stopped through stop_background; a failing receipt never fails the stop.
- context_injector keeps ``turn_stop`` out of resume history.
"""

import inspect
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core import turn_stop
from core.session_unit import SessionState, SessionUnit


def _streaming_unit(session_id: str = "s-stop") -> SessionUnit:
    unit = SessionUnit(session_id=session_id, agent_id="default")
    unit._transition(SessionState.IDLE)
    unit._transition(SessionState.STREAMING)
    client = AsyncMock()
    client.interrupt = AsyncMock()
    wrapper = MagicMock()
    wrapper.pid = 99999
    unit._client = client
    unit._wrapper = wrapper
    return unit


# ── pure module ──────────────────────────────────────────────────────────

def test_build_reads_running_calls_labels_and_background():
    block = turn_stop.build(
        open_tool_uses={"b1": (100.0, "Bash"), "a1": (130.0, "Agent"), "bad": "x"},
        agent_tools={"a1": {"label": "review the diff"}},
        background=[{"toolUseId": "g1", "description": "phase3 experiment"}, {"description": "no id"}],
        now=352.0,
    )
    assert block["type"] == "turn_stop" and block["at"]
    assert block["calls"] == [
        {"toolUseId": "b1", "name": "Bash", "elapsedMs": 252000},
        {"toolUseId": "a1", "name": "Agent", "elapsedMs": 222000, "description": "review the diff"},
    ]
    assert block["agents"] == [{"toolUseId": "g1", "description": "phase3 experiment",
                                "status": "stopped", "background": True}]


def test_a_call_waiting_for_approval_never_ran():
    block = turn_stop.build(open_tool_uses={"b1": (0.0, "Bash")}, agent_tools={},
                            background=[], now=10.0, not_run="b1")
    assert block["calls"] == [{"toolUseId": "b1", "name": "Bash", "ran": False}]
    note = turn_stop.continuation_note(block)
    assert "Never ran (it was waiting for the user's approval): Bash (id b1)" in note
    assert "Stopped mid-call" not in note


def test_the_note_disowns_the_cli_resume_placeholder():
    # 2026-10-22 e4f3397f: on --resume after a Stop the CLI writes a synthetic
    # "No response requested." the model then apologised for as its own reply.
    note = turn_stop.continuation_note({"at": "t", "calls": [], "agents": []})
    assert '"No response requested."' in note and "not a reply you gave" in note

def test_build_caps_rows_and_counts_the_rest():
    calls = {f"c{i}": (0.0, "Bash") for i in range(25)}
    block = turn_stop.build(open_tool_uses=calls, agent_tools={}, background=[], now=1.0)
    assert len(block["calls"]) == turn_stop.MAX_ROWS and block["omitted"] == 5


# ── leftovers: what stopped work leaves (measured: no output; transcripts kept) ──

def _sub_transcript(tmp_path, monkeypatch, sid="11111111-2222-3333-4444-555555555555", task="a56b2f871d7a776d7", calls=3,
                    cwd="/Users/x/SwarmWS"):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    proj = tmp_path / "projects" / turn_stop.project_dir_name(cwd)
    (proj / sid / "subagents").mkdir(parents=True)
    (proj / f"{sid}.jsonl").write_text("{}\n")
    p = proj / sid / "subagents" / f"agent-{task}.jsonl"
    p.write_text("".join('{"message":{"content":[{"type":"tool_use","id":"t%d"}]}}\n' % i for i in range(calls)))
    return sid, task, p


def test_agent_transcript_found_and_counted(tmp_path, monkeypatch):
    sid, task, p = _sub_transcript(tmp_path, monkeypatch)
    cwd = "/Users/x/SwarmWS"
    assert turn_stop.agent_transcript(sid, task, cwd) == {"path": str(p), "toolCalls": 3}
    assert turn_stop.agent_transcript(sid, "../../etc", cwd) is None, "an id never becomes a path"
    assert turn_stop.agent_transcript(sid, "missing0", cwd) is None
    assert turn_stop.agent_transcript(None, task, cwd) is None
    assert turn_stop.agent_transcript(sid, task, None) is None, "no cwd: never a scan"


def test_agent_transcript_never_scans(tmp_path, monkeypatch):
    """It runs on the event loop inside a Stop (2026-10-24: a glob over every project
    dir starved the loop 3.2 s): the path is built, never searched."""
    sid, task, p = _sub_transcript(tmp_path, monkeypatch)
    from pathlib import Path

    def no_glob(*_a, **_k):
        raise AssertionError("agent_transcript must not glob")
    monkeypatch.setattr(Path, "glob", no_glob)
    monkeypatch.setattr(Path, "rglob", no_glob)
    assert turn_stop.agent_transcript(sid, task, "/Users/x/SwarmWS") == {"path": str(p), "toolCalls": 3}
    assert turn_stop.project_dir_name("/Users/gawan/.swarm-ai/SwarmWS") == "-Users-gawan--swarm-ai-SwarmWS"


def test_agent_transcript_big_file_is_not_read(tmp_path, monkeypatch):
    sid, task, p = _sub_transcript(tmp_path, monkeypatch)
    monkeypatch.setattr(turn_stop, "_TRANSCRIPT_READ_MAX", 10)
    assert turn_stop.agent_transcript(sid, task, "/Users/x/SwarmWS") == {"path": str(p)}


def test_existing_write_targets(tmp_path):
    (tmp_path / "out.log").write_text("half")
    got = turn_stop.existing_write_targets(["out.log", str(tmp_path / "nope.txt"), "", str(tmp_path / "out.log")], str(tmp_path))
    assert got == [str(tmp_path / "out.log")]
    assert turn_stop.existing_write_targets(["out.log"], None) == [], "a relative path needs the cwd"


def test_build_and_note_carry_the_leftovers():
    block = turn_stop.build(
        open_tool_uses={"b1": (0.0, "Bash"), "a1": (0.0, "Agent")}, agent_tools={},
        background=[{"toolUseId": "g1", "description": "phase3"}], now=300.0,
        writes={"b1": ["/w/out.log"]},
        transcripts={"a1": {"path": "/t/a.jsonl", "toolCalls": 4}, "g1": {"path": "/t/g.jsonl", "toolCalls": 55}})
    rows = {c["toolUseId"]: c for c in block["calls"]}
    assert rows["b1"]["writes"] == ["/w/out.log"] and rows["a1"]["transcript"]["toolCalls"] == 4
    assert block["agents"][0]["transcript"] == {"path": "/t/g.jsonl", "toolCalls": 55}
    note = turn_stop.continuation_note(block)
    assert "A stopped call's output was not kept." in note
    assert "may have written, fully or partly: /w/out.log" in note
    assert "/t/a.jsonl" in note and "its work so far: /t/g.jsonl" in note


def test_note_names_what_stopped_and_says_check_before_rerun():
    block = {"at": "2026-10-22T09:00:00+00:00",
             "calls": [{"toolUseId": "b1", "name": "Bash", "elapsedMs": 252000}],
             "agents": [{"toolUseId": "g1", "description": "phase3", "status": "stopped"}]}
    note = turn_stop.continuation_note(block)
    assert note.startswith("[App note, not from the user")
    assert "Bash (id b1, after 4m12s)" in note
    assert '"phase3" (stopped)' in note
    assert "do not blindly re-run" in note and note.endswith("]")


def test_user_stop_marks_background_agents_stopped_not_lost():
    from core.background_agents import BackgroundAgents
    seen = []
    reg = BackgroundAgents(on_change=seen.append)
    reg.launch("g1", "x")
    reg.user_stop(True)
    reg.lose_all()
    reg.user_stop(False)
    assert reg.snapshot()[0]["status"] == "stopped" and seen[-1]["status"] == "stopped"
    reg.launch("g2", "y")
    reg.lose_all()
    assert reg.snapshot()[1]["status"] == "lost", "a crash is still lost"


# ── SessionUnit ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_receipt_is_read_before_the_interrupt_clears_tracking():
    unit = _streaming_unit()
    unit._open_tool_uses = {"b1": (0.0, "Bash")}
    block = unit.begin_stop_receipt()
    assert await unit.interrupt() is True
    assert unit.state == SessionState.COLD and unit._open_tool_uses == {}
    assert block["calls"][0]["toolUseId"] == "b1"
    assert "Bash" in unit._stop_context


def test_receipt_marks_the_call_behind_an_open_permission_not_run():
    unit = _streaming_unit()
    unit._transition(SessionState.WAITING_INPUT)
    unit._open_tool_uses = {"b1": (0.0, "Bash"), "b0": (0.0, "Read")}
    unit._pending_question = {"request_id": "r1", "tool_use_id": "r1", "tool_call_id": "b1"}
    calls = {c["toolUseId"]: c for c in unit.begin_stop_receipt()["calls"]}
    assert calls["b1"].get("ran") is False and "ran" not in calls["b0"]


def test_receipt_reads_the_leftovers_from_the_turn(tmp_path, monkeypatch):
    sid, task, p = _sub_transcript(tmp_path, monkeypatch, calls=2, cwd=str(tmp_path))
    (tmp_path / "half.txt").write_text("x")
    unit = _streaming_unit()
    unit._sdk_session_id = sid
    unit._cwd = str(tmp_path)
    unit._open_tool_uses = {"b1": (0.0, "Bash"), "a1": (0.0, "Agent")}
    unit._streaming_orchestrator._turn_carry["pending_file_changes"]["b1"] = ["half.txt", "gone.txt"]
    unit.background_agents.task_started(task, "a1")
    calls = {c["toolUseId"]: c for c in unit.begin_stop_receipt()["calls"]}
    assert calls["b1"]["writes"] == [str(tmp_path / "half.txt")]
    assert calls["a1"]["transcript"] == {"path": str(p), "toolCalls": 2}


def test_no_receipt_when_nothing_runs():
    unit = SessionUnit(session_id="s-idle", agent_id="default")
    assert unit.begin_stop_receipt() is None


@pytest.mark.asyncio
async def test_idle_stop_recycles_the_process_that_runs_background_agents():
    unit = _streaming_unit()
    unit._transition(SessionState.IDLE)
    unit.background_agents.launch("g1", "phase3")
    block = unit.begin_stop_receipt()
    assert block["calls"] == [] and block["agents"][0]["status"] == "stopped"
    assert block["turn"] is False, "no turn ran: the turn it lands on is not stopped"
    unit.background_agents.user_stop(True)
    assert await unit.stop_background() is True
    unit.background_agents.user_stop(False)
    assert unit.state == SessionState.COLD and unit._client is None
    assert unit.background_agents.snapshot()[0]["status"] == "stopped"


@pytest.mark.asyncio
async def test_idle_stop_with_nothing_running_does_nothing():
    unit = _streaming_unit()
    unit._transition(SessionState.IDLE)
    assert await unit.stop_background() is False
    assert unit.state == SessionState.IDLE


def test_stop_note_is_injected_once():
    unit = SessionUnit(session_id="s-note", agent_id="default")
    unit._stop_context = "[note]"
    assert unit._apply_stop_note("continue") == "[note]\n\n---\n\ncontinue"
    assert unit._apply_stop_note("continue") == "continue"
    unit._stop_context = "[note]"
    q = [{"type": "text", "text": "x"}]
    assert unit._apply_stop_note(q) is q and unit._stop_context == ""


def test_send_applies_the_stop_note():
    src = inspect.getsource(SessionUnit.send)
    assert "self._apply_stop_note(query_content)" in src


def test_permission_prompts_carry_the_gated_call_id():
    import core.security_hooks as sh
    import core.streaming_orchestrator as so
    assert inspect.getsource(sh).count('"toolUseId": tool_use_id,') >= 2
    assert '"tool_call_id": perm_request.get("toolUseId")' in inspect.getsource(so)


# ── SessionRouter ───────────────────────────────────────────────────────

def _router_with(unit):
    from core.session_router import SessionRouter
    router = SessionRouter.__new__(SessionRouter)
    router.get_unit = lambda sid: unit
    return router


def _fake_unit(state):
    unit = MagicMock()
    unit.state = state
    unit.is_alive = False
    unit.background_agents = MagicMock()
    unit._turn_client_id = "c1"
    unit._turn_changes = None
    unit._turn_files = {}
    return unit


@pytest.mark.asyncio
async def test_interrupt_session_persists_the_receipt_before_the_stop_and_returns_it():
    unit = _fake_unit(SessionState.STREAMING)
    order = []
    unit.begin_stop_receipt = MagicMock(return_value={"type": "turn_stop", "calls": [], "agents": []})
    async def _interrupt():
        order.append("interrupt")
        return True
    unit.interrupt = _interrupt
    unit._turn_changes = MagicMock()
    unit._turn_changes.finish = AsyncMock(return_value={"type": "turn_changes", "files": [{"path": "a"}]})
    unit._turn_files = {"/x/r.md": {"path": "r.md", "absolutePath": "/x/r.md", "kind": "content"}}
    router = _router_with(unit)
    async def _persist(sid, blocks, model, **kw):
        order.append(blocks[-1]["type"])
        return blocks
    with patch.object(type(router), "_persist_assistant_blocks", AsyncMock(side_effect=_persist)) as persist:
        out = await router.interrupt_session("s1")
    assert order[0] == "turn_stop" and order.index("interrupt") > 0
    assert [b["type"] for b in out["receipt"]] == ["turn_changes", "turn_deliverables", "turn_stop"]
    assert persist.await_args.kwargs["client_id"] == "c1"
    assert unit._turn_files == {}, "deliverables are written once"
    assert [c.args for c in unit.background_agents.user_stop.call_args_list] == [(True,), (False,)]


@pytest.mark.asyncio
async def test_a_stopped_bash_writes_join_the_changes_card():
    unit = _fake_unit(SessionState.STREAMING)
    unit.begin_stop_receipt = MagicMock(return_value={"type": "turn_stop", "agents": [],
        "calls": [{"toolUseId": "b1", "name": "Bash", "writes": ["/w/out.log"]}]})
    unit.interrupt = AsyncMock(return_value=True)
    unit._turn_changes = MagicMock()
    unit._turn_changes.finish = AsyncMock(return_value=None)
    router = _router_with(unit)
    with patch.object(type(router), "_persist_assistant_blocks", AsyncMock(side_effect=lambda s, b, m, **k: b)):
        await router.interrupt_session("s1")
    unit._turn_changes.note.assert_called_once_with({"absolutePath": "/w/out.log", "operation": "written"})


@pytest.mark.asyncio
async def test_idle_session_with_background_agents_stops_them():
    unit = _fake_unit(SessionState.IDLE)
    unit.begin_stop_receipt = MagicMock(return_value={"type": "turn_stop", "calls": [],
                                                       "agents": [{"toolUseId": "g1", "status": "stopped"}]})
    unit.stop_background = AsyncMock(return_value=True)
    unit.interrupt = AsyncMock()
    router = _router_with(unit)
    with patch.object(type(router), "_persist_assistant_blocks", AsyncMock(side_effect=lambda s, b, m, **k: b)):
        out = await router.interrupt_session("s1")
    unit.stop_background.assert_awaited_once()
    unit.interrupt.assert_not_awaited()
    assert out["message"] == "Stopped background agents"
    assert [b["type"] for b in out["receipt"]] == ["turn_stop"]


@pytest.mark.asyncio
async def test_a_failing_receipt_never_fails_the_stop():
    unit = _fake_unit(SessionState.STREAMING)
    unit.interrupt = AsyncMock(return_value=True)
    unit.begin_stop_receipt = MagicMock(side_effect=RuntimeError("boom"))
    out = await _router_with(unit).interrupt_session("s1")
    unit.interrupt.assert_awaited_once()
    assert out["success"] is True and out["receipt"] == []


def test_turn_stop_kept_out_of_resume_history():
    from core.context_injector import _TOOL_ONLY_TYPES
    assert "turn_stop" in _TOOL_ONLY_TYPES


@pytest.mark.asyncio
async def test_a_second_stop_while_one_runs_writes_no_second_receipt():
    """2026-10-24: one slow Stop and the user's repeated clicks wrote 5 identical
    receipts. Only one Stop per session runs at a time; a later one works again."""
    import asyncio
    unit = _fake_unit(SessionState.IDLE)
    unit.begin_stop_receipt = MagicMock(return_value={"type": "turn_stop", "calls": [], "agents": [{"toolUseId": "g"}]})
    gate = asyncio.Event()
    async def _stop_background():
        await gate.wait()
        return True
    unit.stop_background = _stop_background
    router = _router_with(unit)
    persisted = []
    async def _persist(sid, blocks, model, **kw):
        persisted.append(blocks[-1]["type"])
        return blocks
    with patch.object(type(router), "_persist_assistant_blocks", AsyncMock(side_effect=_persist)):
        first = asyncio.ensure_future(router.interrupt_session("s1"))
        await asyncio.sleep(0)
        try:
            second = await asyncio.wait_for(router.interrupt_session("s1"), 2)
        except asyncio.TimeoutError:
            gate.set()
            await first
            raise AssertionError("the second Stop ran a stop of its own instead of returning")
        assert second["receipt"] == [] and "already" in second["message"]
        gate.set()
        out = await first
        assert out["message"] == "Stopped background agents"
        assert persisted == ["turn_stop"], "one Stop, one receipt"
        third = await router.interrupt_session("s1")
        assert third["message"] == "Stopped background agents", "a later Stop works"
    assert persisted == ["turn_stop", "turn_stop"]


# ── the order of a Stop's rows and a message sent meanwhile (core/transcript_order) ──

async def _user_row(sid: str):
    """A user row written the way the send path writes it."""
    from datetime import datetime
    from uuid import uuid4
    from core import transcript_order
    from database import db
    async with transcript_order.user_row(sid):
        await db.messages.put({"id": str(uuid4()), "session_id": sid, "role": "user",
                               "content": [{"type": "text", "text": "next"}],
                               "created_at": datetime.now().isoformat()})


async def _kinds(sid: str) -> list:
    from database import db
    rows = await db.messages.list_by_session(sid)
    return [r["content"][0]["type"] if r["role"] == "assistant" else "user" for r in rows]


def _stopping_unit(interrupt):
    unit = _fake_unit(SessionState.STREAMING)
    unit.begin_stop_receipt = MagicMock(return_value={"type": "turn_stop", "calls": [], "agents": []})
    unit.interrupt = interrupt
    unit._turn_changes = MagicMock()
    unit._turn_changes.finish = AsyncMock(return_value={"type": "turn_changes", "files": [{"path": "a"}]})
    return unit


@pytest.mark.asyncio
async def test_a_message_sent_during_the_interrupt_lands_after_the_stopped_turns_products():
    """Real SQLite (conftest). The products are written after the interrupt; a message
    sent while it runs must come after them, or a reload shows the stopped turn's card
    under the next turn (history merges only consecutive assistant rows)."""
    import asyncio
    from uuid import uuid4
    sid = f"s-order-{uuid4().hex[:8]}"
    sent = []
    async def _interrupt():
        sent.append(asyncio.ensure_future(_user_row(sid)))
        await asyncio.sleep(0.05)              # the send gets every chance to write first
        return True
    await _router_with(_stopping_unit(_interrupt)).interrupt_session(sid)
    await sent[0]
    assert await _kinds(sid) == ["turn_stop", "turn_changes", "user"]


@pytest.mark.asyncio
async def test_a_message_sent_while_the_running_turn_is_flushed_lands_after_the_receipt():
    """The window before the receipt: Stop first flushes the running turn's held rows."""
    import asyncio
    from uuid import uuid4
    from core.turn_writer import TurnWriter
    sid = f"s-order-{uuid4().hex[:8]}"
    release = asyncio.Event()
    flushing = asyncio.Event()
    class _Running:
        async def flush(self):
            flushing.set()
            await release.wait()
    router = _router_with(_stopping_unit(AsyncMock(return_value=True)))
    with patch.object(TurnWriter, "running", staticmethod(lambda s: _Running())):
        stop = asyncio.ensure_future(router.interrupt_session(sid))
        await flushing.wait()
        send = asyncio.ensure_future(_user_row(sid))
        await asyncio.sleep(0.05)
        release.set()
        await stop
        await send
    assert await _kinds(sid) == ["turn_stop", "turn_changes", "user"]


@pytest.mark.asyncio
async def test_a_user_row_never_waits_past_its_bound():
    import asyncio
    from core import transcript_order
    async with transcript_order.stop_writes("s-bound"):
        async def _write():
            async with transcript_order.user_row("s-bound", wait_s=0.05):
                return "written"
        assert await asyncio.wait_for(_write(), 1) == "written"
    transcript_order.forget("s-bound")


def test_the_send_path_writes_the_user_row_under_the_order_lock():
    import inspect
    from core.session_router import SessionRouter
    src = inspect.getsource(SessionRouter)
    at = src.index('"role": "user",')
    assert "transcript_order.user_row(" in src[max(0, at - 1500):at]


def test_the_order_lock_is_released_with_the_session():
    import inspect
    from core.lifecycle_manager import LifecycleManager
    assert "transcript_order" in inspect.getsource(LifecycleManager._release_session_state)
