"""Stop receipt (core/turn_stop): what a user Stop stopped, shown and remembered.

What is tested
- The pure module: snapshot of running calls / foreground Agent labels / background
  agents, the block (a background agent the Stop took down with the process reads
  ``stopped``; a status the CLI reported first is kept), caps, the continuation note.
- SessionUnit: ``interrupt()`` snapshots BEFORE it clears the per-turn tracking, so
  ``take_stop_receipt()`` after the recycle still knows what ran; the receipt is
  one-shot and refused once a new send started (generation guard); the note is
  injected once into the next query (``_apply_stop_note``), and send() calls it.
- SessionRouter.interrupt_session: persists one assistant row (receipt last, the
  turn's changes / deliverables before it) and returns it; a failing receipt never
  fails the stop.
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

def test_snapshot_reads_running_calls_labels_and_background():
    snap = turn_stop.take_snapshot(
        generation=3,
        open_tool_uses={"b1": (100.0, "Bash"), "a1": (130.0, "Agent"), "bad": "x"},
        agent_tools={"a1": {"label": "review the diff"}},
        background=[{"toolUseId": "g1", "description": "phase3 experiment"}, {"description": "no id"}],
        now=352.0,
    )
    assert snap["generation"] == 3
    assert snap["calls"] == [
        {"toolUseId": "b1", "name": "Bash", "elapsedMs": 252000},
        {"toolUseId": "a1", "name": "Agent", "elapsedMs": 222000, "description": "review the diff"},
    ]
    assert snap["background"] == [{"toolUseId": "g1", "description": "phase3 experiment"}]


def test_block_status_after_stop():
    snap = {"calls": [], "at": "T", "background": [
        {"toolUseId": "g1", "description": "a"}, {"toolUseId": "g2", "description": "b"},
        {"toolUseId": "g3", "description": "c"}]}
    now = {"g1": {"status": "lost"}, "g2": {"status": "completed", "summary": " done "}}
    block = turn_stop.build_block(snap, now)
    assert block["type"] == "turn_stop" and block["at"] == "T"
    assert [(a["toolUseId"], a["status"]) for a in block["agents"]] == [
        ("g1", "stopped"), ("g2", "completed"), ("g3", "stopped")]
    assert block["agents"][1]["summary"] == "done"
    assert all(a["background"] for a in block["agents"])
    live = turn_stop.build_block(snap, {"g1": {"status": "running"}}, process_gone=False)
    assert live["agents"][0]["status"] == "running", "an alive process: not claimed stopped"
    gone = turn_stop.build_block(snap, {"g1": {"status": "running"}}, process_gone=True)
    assert gone["agents"][0]["status"] == "stopped"


def test_block_caps_rows_and_counts_the_rest():
    calls = [{"toolUseId": f"c{i}", "name": "Bash"} for i in range(25)]
    block = turn_stop.build_block({"calls": calls, "background": []}, {})
    assert len(block["calls"]) == turn_stop.MAX_ROWS and block["omitted"] == 5


def test_note_names_what_stopped_and_says_check_before_rerun():
    block = {"at": "2026-10-22T09:00:00+00:00",
             "calls": [{"toolUseId": "b1", "name": "Bash", "elapsedMs": 252000}],
             "agents": [{"toolUseId": "g1", "description": "phase3", "status": "stopped"}]}
    note = turn_stop.continuation_note(block)
    assert note.startswith("[App note, not from the user")
    assert "Bash (id b1, after 4m12s)" in note
    assert '"phase3" (stopped)' in note
    assert "do not blindly re-run" in note and note.endswith("]")


# ── SessionUnit ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_interrupt_snapshots_before_clearing_and_receipt_survives_recycle():
    unit = _streaming_unit()
    unit._open_tool_uses = {"b1": (0.0, "Bash")}
    unit.background_agents.launch("g1", "phase3 experiment")
    assert await unit.interrupt() is True
    assert unit.state == SessionState.COLD and unit._open_tool_uses == {}
    block = unit.take_stop_receipt()
    assert block["calls"][0]["toolUseId"] == "b1"
    assert block["agents"] == [{"toolUseId": "g1", "description": "phase3 experiment",
                                "status": "stopped", "background": True}]
    assert unit.take_stop_receipt() is None, "one receipt per Stop"
    assert "Bash" in unit._stop_context


@pytest.mark.asyncio
async def test_autonomous_interrupt_takes_no_snapshot():
    unit = _streaming_unit()
    unit._open_tool_uses = {"b1": (0.0, "Bash")}
    await unit.interrupt(autonomous=True)
    assert unit.take_stop_receipt() is None


@pytest.mark.asyncio
async def test_receipt_refused_once_a_new_send_started():
    unit = _streaming_unit()
    unit._open_tool_uses = {"b1": (0.0, "Bash")}
    await unit.interrupt()
    unit._send_generation += 1  # what send()'s Layer 0 does
    assert unit.take_stop_receipt() is None
    assert unit._stop_context == ""


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


def test_reset_turn_state_drops_a_stale_snapshot():
    unit = SessionUnit(session_id="s-reset", agent_id="default")
    unit._stop_snapshot = {"generation": 0}
    unit._reset_turn_state()
    assert unit._stop_snapshot is None


# ── SessionRouter ───────────────────────────────────────────────────────

def _router_with(unit):
    from core.session_router import SessionRouter
    router = SessionRouter.__new__(SessionRouter)
    router.get_unit = lambda sid: unit
    return router


@pytest.mark.asyncio
async def test_interrupt_session_persists_and_returns_the_receipt():
    unit = MagicMock()
    unit.interrupt = AsyncMock(return_value=True)
    unit.is_alive = False
    unit.take_stop_receipt = MagicMock(return_value={"type": "turn_stop", "calls": [], "agents": []})
    unit._turn_changes = MagicMock()
    unit._turn_changes.finish = AsyncMock(return_value={"type": "turn_changes", "files": [{"path": "a"}]})
    unit._turn_files = {"/x/r.md": {"path": "r.md", "absolutePath": "/x/r.md", "kind": "content"}}
    unit._turn_client_id = "c1"
    router = _router_with(unit)
    persist = AsyncMock(side_effect=lambda sid, blocks, model, **kw: blocks)
    with patch.object(type(router), "_persist_assistant_blocks", persist):
        out = await router.interrupt_session("s1")
    assert [b["type"] for b in out["receipt"]] == ["turn_changes", "turn_deliverables", "turn_stop"]
    assert persist.await_args.kwargs["client_id"] == "c1"
    assert unit._turn_files == {}, "deliverables are written once"


@pytest.mark.asyncio
async def test_a_failing_receipt_never_fails_the_stop():
    unit = MagicMock()
    unit.interrupt = AsyncMock(return_value=True)
    unit.is_alive = False
    unit.take_stop_receipt = MagicMock(side_effect=RuntimeError("boom"))
    out = await _router_with(unit).interrupt_session("s1")
    assert out["success"] is True and out["receipt"] == []


def test_turn_stop_kept_out_of_resume_history():
    from core.context_injector import _TOOL_ONLY_TYPES
    assert "turn_stop" in _TOOL_ONLY_TYPES
