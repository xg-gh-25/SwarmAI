"""Pipeline alignment + needs-you blocks, and the turn's deliverables (chat v7.1).

What is tested:
- run_block: the run's requirement (single-spaced, capped) and its planned stages;
  None without a requirement
- attention_block, from STRUCTURED fields only, on real-shaped run.json + artifacts
  in a temp run dir: self-answered clarifications, assumed requirements, Gate 0 /
  Gate 1 verdicts that are not a pass (PASS / SUPPORTED / SKIPPED / resolved BLOCK
  are quiet), Gate 2 open findings from the deliver artifact, push_ready False with
  its reasons; None when nothing needs the user; unreadable artifacts are skipped
- the orchestrator: a pipeline_run block once per run per TURN (send() admission
  resets the announced set, so a turn that continues a run shows its plan again),
  the attention block only with the DELIVER row
- SessionRouter._persist_stream_event: content/knowledge file_changed events become
  ONE persisted turn_deliverables block delivered just before `result`; source and
  deleted files are not products
- context_injector keeps all three block types out of a resumed prompt
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from core import pipeline_stage_events as pse
from core.context_injector import _filter_tool_only_messages
from core.session_router import SessionRouter


def test_run_block_is_the_alignment():
    state = {"profile": "full", "stages": {}, "requirement":
             "Add  claude-opus-5-5\n to MODEL_REGISTRY after opus-4-8."}
    b = pse.run_block("run_a1", state, "T")
    assert b["type"] == "pipeline_run" and b["runId"] == "run_a1"
    assert b["requirement"] == "Add claude-opus-5-5 to MODEL_REGISTRY after opus-4-8."
    assert b["stages"][0] == "evaluate" and "deliver" in b["stages"]
    assert b["checklist"] == [{"stage": s, "gate": False} for s in b["stages"]]
    assert pse.run_block("run_a1", {"stages": {}, "requirement": "  "}, "T") is None


def test_attention_names_only_a_change_that_did_not_land():
    """v1: from the projection's landing only (no artifact is read). There is no push
    verdict (XG 2026-10-26): open audit checks are not a "needs you" item."""
    not_landed = pse.attention_block("run_a1", {"landing": "not_landed",
                                                "reason": "tests_red"}, "T")
    assert [(i["kind"], i["reasons"]) for i in not_landed["items"]] == [
        ("not_landed", ["tests_red"])]
    assert pse.attention_block("run_a1", {"landing": "landed", "push_ready": False}, "T") is None
    assert pse.attention_block("run_a1", {"landing": "landed"}, "T") is None
    assert pse.attention_block("run_a1", None, "T") is None


# ── orchestrator: alignment once per run, attention only with DELIVER ────────

from tests.test_pipeline_stage_events import (  # noqa: E402 — shared stream helpers
    CLI, _ScriptedReader, _bash_use, _drive_stream, _isolated_queue, _result,  # noqa: F401
)

_S1 = {"profile": "full", "status": "running", "requirement": "Do X",
       "stages": {"evaluate": {"stage": "evaluate", "status": "completed"}}}
_S2 = {**_S1, "stages": {**_S1["stages"], "think": {"stage": "think", "status": "completed"}}}
_S3 = {**_S2, "landing": "not_landed", "reason": "tests_red", "stages": {**_S2["stages"], "deliver": {
    "stage": "deliver", "status": "completed"}}}


def _blocks(events):
    return [b for e in events if e.get("type") == "assistant"
            for b in e.get("content") or [] if isinstance(b, dict)
            and b.get("type") in ("pipeline_run", "pipeline_stage", "pipeline_attention")]


def _turn_on(orch, monkeypatch, reader, tu, rid):
    from core.permission_manager import permission_manager
    from tests.test_tool_call_leak_guard import _make_result_message
    monkeypatch.setattr(pse, "read_stage_state", reader)
    permission_manager.clear_session("sess-test")  # each turn = its own loop
    events, raised = _drive_stream(orch, [_bash_use(tu, CLI.format(rid=rid)), _result(tu),
                                          _make_result_message()])
    assert raised is None
    return _blocks(events)


def _turn(orch, monkeypatch, reader, tu):
    return _turn_on(orch, monkeypatch, reader, tu, "run_a1")


@pytest.mark.usefixtures("_isolated_queue")
def test_orchestrator_announces_the_run_once_and_attention_rides_deliver(monkeypatch):
    from tests.test_tool_call_leak_guard import _make_orchestrator
    orch = _make_orchestrator()
    first = _turn(orch, monkeypatch, _ScriptedReader(_S1, _S2), "tu1")
    assert [(b["type"], b.get("stage")) for b in first] == [("pipeline_run", None), ("pipeline_stage", "think")]
    assert first[0]["requirement"] == "Do X"
    # the same turn calling the CLI again: no second announce
    later = _turn(orch, monkeypatch, _ScriptedReader(_S3), "tu2")
    assert [(b["type"], b.get("stage")) for b in later] == [("pipeline_stage", "deliver"), ("pipeline_attention", None)]
    assert [i["kind"] for i in later[1]["items"]] == ["not_landed"]   # waited for DELIVER


@pytest.mark.usefixtures("_isolated_queue")
def test_an_isolated_run_shows_the_card_only_when_its_change_lands(monkeypatch):
    """run_e451e4a0: deliver was recorded, the landing was refused (changed since
    review) and the run re-reviewed for minutes under a card that read as the end."""
    from tests.test_tool_call_leak_guard import _make_orchestrator
    orch = _make_orchestrator()
    s2 = {**_S2, "isolated": True, "landed": False}
    s3 = {**_S3, "isolated": True, "landed": False}
    s4 = {**s3, "landed": True}
    _turn(orch, monkeypatch, _ScriptedReader(_S1, s2), "tu1")
    refused = _turn(orch, monkeypatch, _ScriptedReader(s3), "tu2")
    assert [b["type"] for b in refused] == ["pipeline_stage"]          # the row, no card
    landed = _turn(orch, monkeypatch, _ScriptedReader(s4), "tu3")
    assert [b["type"] for b in landed] == ["pipeline_attention"]       # the card, once
    again = _turn(orch, monkeypatch, _ScriptedReader(s4), "tu4")
    assert again == []


@pytest.mark.usefixtures("_isolated_queue")
def test_a_new_turn_announces_a_running_run_again(monkeypatch):
    """After send() admission (which resets the unit's announced set) the next call
    naming a still-running run carries its alignment and plan again — the turn that
    continues a run shows what it is working on; stage rows still never repeat."""
    from tests.test_tool_call_leak_guard import _make_orchestrator
    orch = _make_orchestrator()
    _turn(orch, monkeypatch, _ScriptedReader(_S1, _S2), "tu1")
    orch._parent._pipeline_runs_announced = set()  # what send() admission does
    again = _turn(orch, monkeypatch, _ScriptedReader(_S2), "tu2")
    assert [(b["type"], b.get("stage")) for b in again] == [("pipeline_run", None)]
    assert again[0]["requirement"] == "Do X"


RUN_CREATE = ("cd /x/swarmai && \"$SWARM_BACKEND_BIN\" pipeline-v1 "
              "create --project SwarmAI --requirement 'Do X'")


def _created(tu, out):
    from claude_agent_sdk import ToolResultBlock, UserMessage
    return UserMessage(content=[ToolResultBlock(tool_use_id=tu, content=out, is_error=False)])


def _create_turn(orch, monkeypatch, reader, result):
    from core.permission_manager import permission_manager
    from tests.test_tool_call_leak_guard import _make_result_message
    monkeypatch.setattr(pse, "read_stage_state", reader)
    permission_manager.clear_session("sess-test")
    events, raised = _drive_stream(orch, [_bash_use("tc1", RUN_CREATE), result, _make_result_message()])
    assert raised is None
    return _blocks(events)


@pytest.mark.usefixtures("_isolated_queue")
def test_run_create_shows_the_alignment_at_once_and_seeds_the_baseline(monkeypatch):
    """XG 2026-10-18: the understanding card must appear when the run is created, not
    after EVALUATE's first --run-id call."""
    from tests.test_tool_call_leak_guard import _make_orchestrator
    orch = _make_orchestrator()
    reader = _ScriptedReader(_S1)
    out = 'warning: x\n{"pipeline_id": "run_new1", "project": "SwarmAI", "file": "/p/run.json"}'
    blocks = _create_turn(orch, monkeypatch, reader, _created("tc1", out))
    assert [(b["type"], b["runId"]) for b in blocks] == [("pipeline_run", "run_new1")]
    assert blocks[0]["requirement"] == "Do X"
    assert reader.calls == ["run_new1"]  # no pre-read: the run did not exist yet
    # The next call on that run diffs against the seeded baseline and does not re-announce.
    later = _turn_on(orch, monkeypatch, _ScriptedReader(_S2), "tu2", "run_new1")
    assert [(b["type"], b.get("stage")) for b in later] == [("pipeline_stage", "think")]


@pytest.mark.usefixtures("_isolated_queue")
def test_run_create_text_blocks_output_and_failures(monkeypatch):
    from claude_agent_sdk import ToolResultBlock, UserMessage
    from tests.test_tool_call_leak_guard import _make_orchestrator
    reader = _ScriptedReader(_S1)
    as_blocks = _created("tc1", [{"type": "text", "text": '{"pipeline_id": "run_new2"}'}])
    assert [b["runId"] for b in _create_turn(_make_orchestrator(), monkeypatch, reader, as_blocks)] == ["run_new2"]
    failed = UserMessage(content=[ToolResultBlock(tool_use_id="tc1", content='{"pipeline_id": "run_x"}', is_error=True)])
    reader = _ScriptedReader(_S1)
    assert _create_turn(_make_orchestrator(), monkeypatch, reader, failed) == [] and reader.calls == []
    reader = _ScriptedReader(_S1)
    assert _create_turn(_make_orchestrator(), monkeypatch, reader, _created("tc1", "Traceback ...")) == []
    assert reader.calls == []


@pytest.mark.usefixtures("_isolated_queue")
def test_run_create_result_inside_an_assistant_message_also_announces(monkeypatch):
    from claude_agent_sdk import AssistantMessage, ToolResultBlock
    from tests.test_tool_call_leak_guard import _make_orchestrator
    reader = _ScriptedReader(_S1)
    am = AssistantMessage(content=[ToolResultBlock(tool_use_id="tc1", content='{"pipeline_id": "run_new3"}',
                                                   is_error=False)], model="test-model")
    assert [b["runId"] for b in _create_turn(_make_orchestrator(), monkeypatch, reader, am)] == ["run_new3"]
    assert reader.calls == ["run_new3"]


@pytest.mark.parametrize("command,expected", [
    (RUN_CREATE, True),
    ("python-backend pipeline-v1 create --project P --requirement R", True),
    ('python-backend pipeline-v1 create --project P', True),
    ("python-backend pipeline-v1 run-create --project P --requirement R", False),
    ("python-backend pipeline-v1 run-update --project P --run-id run_a --stage-json '{\"s\":\"run-create\"}'", False),
    ("python-backend pipeline-v1 create --project P && python-backend pipeline-v1 status --run-id run_a", False),
    ("echo run-create", False),
    (None, False),
])
def test_is_run_create(command, expected):
    assert pse.is_run_create(command) is expected


@pytest.mark.parametrize("content,expected", [
    ('{"pipeline_id": "run_ab12"}', "run_ab12"),
    ('{"pipeline_id": "run_old"}\n{"pipeline_id": "run_new"}', "run_new"),
    ('{"pipeline_id": "../../etc"}', None),
    ([{"type": "text", "text": '{"pipeline_id": "run_c"}'}], "run_c"),
    ("no json here", None),
    (None, None),
])
def test_run_id_from_create_output(content, expected):
    assert pse.run_id_from_create_output(content) == expected
    assert pse.resolve_watch("run_literal", content) == "run_literal"


@pytest.mark.usefixtures("_isolated_queue")
def test_orchestrator_announces_on_a_missing_baseline_and_skips_finished_runs(monkeypatch):
    from tests.test_tool_call_leak_guard import _make_orchestrator
    # run-create: no run.json at the pre-read; the post-read sees it → alignment, no replay
    blocks = _turn(_make_orchestrator(), monkeypatch, _ScriptedReader(None, _S2), "tu1")
    assert [b["type"] for b in blocks] == ["pipeline_run"]
    done = {**_S2, "status": "completed"}
    assert _turn(_make_orchestrator(), monkeypatch, _ScriptedReader(done, done), "tu1") == []


# ── router: the turn's products, one persisted block before `result` ─────────

class _Router:
    _compact_tool_results = staticmethod(SessionRouter._compact_tool_results)
    _persist_assistant_blocks = staticmethod(SessionRouter._persist_assistant_blocks)
    _RESULT_FLUSH_EVENTS = SessionRouter._RESULT_FLUSH_EVENTS
    _DELIVERABLE_KINDS = SessionRouter._DELIVERABLE_KINDS
    _note_deliverable = classmethod(SessionRouter._note_deliverable.__func__)
    _persist_stream_event = SessionRouter._persist_stream_event
    _record_canvas_output = staticmethod(SessionRouter._record_canvas_output)


def _fc(path, kind, op="created"):
    return {"type": "file_changed", "path": path, "absolutePath": f"/ws/{path}", "kind": kind, "operation": op}


@pytest.mark.asyncio
async def test_products_become_one_persisted_block_before_result():
    put, router, pending, files = AsyncMock(), _Router(), [], {}
    events = [_fc("Projects/P/deck.pptx", "content"), _fc("docs/notes.md", "knowledge"),
              _fc("backend/core/x.py", "source"), _fc("tmp/draft.html", "content"),
              _fc("tmp/draft.html", "content", "deleted"), _fc("docs/notes.md", "knowledge")]
    with patch("database.db.messages.put", put):
        for ev in events:
            assert await router._persist_stream_event("sid", ev, pending, files=files) == []
        assert put.await_count == 0
        before = await router._persist_stream_event("sid", {"type": "result"}, pending, files=files)
    expected = {"type": "turn_deliverables", "files": [
        {"path": "Projects/P/deck.pptx", "absolutePath": "/ws/Projects/P/deck.pptx", "kind": "content",
         "operation": "written"},
        {"path": "docs/notes.md", "absolutePath": "/ws/docs/notes.md", "kind": "knowledge",
         "operation": "written"}]}
    assert before == [{"type": "assistant", "content": [expected], "model": None}]
    assert put.await_count == 1 and put.await_args.args[0]["content"] == [expected]
    assert files == {} and pending == []


@pytest.mark.asyncio
async def test_no_products_no_block_and_the_cap_holds():
    put, router = AsyncMock(), _Router()
    with patch("database.db.messages.put", put):
        assert await router._persist_stream_event("sid", {"type": "result"}, [], files={}) == []
        files: dict = {}
        for i in range(20):
            await router._persist_stream_event("sid", _fc(f"out/{i}.md", "content"), [], files=files)
        (ev,) = await router._persist_stream_event("sid", {"type": "result"}, [], files=files)
    assert len(ev["content"][0]["files"]) == 12
    assert put.await_count == 1


# ── none of the three re-enters a resumed prompt ─────────────────────────────

def test_display_blocks_stay_out_of_the_resume_history():
    rows = [{"role": "assistant", "content": [{"type": t, "runId": "run_a1"}]}
            for t in ("pipeline_run", "pipeline_attention", "turn_deliverables")]
    mixed = {"role": "assistant", "content": [{"type": "turn_deliverables", "files": []},
                                              {"type": "text", "text": "done"}]}
    assert _filter_tool_only_messages([*rows, mixed]) == [mixed]


# ── products survive a question / permission pause (A1) ──────────────────────

def _paused_router(unit_events):
    """A SessionRouter whose get_unit returns a unit with a REAL _turn_files dict
    and a continuation that yields ``unit_events``."""
    from types import SimpleNamespace
    router = SessionRouter.__new__(SessionRouter)
    unit = SimpleNamespace(_turn_client_id="cid", _turn_files={})

    async def _cont(*_a, **_k):
        for ev in unit_events:
            yield ev

    unit.continue_with_answer = _cont
    unit.continue_with_permission = _cont
    router.get_unit = lambda _sid: unit
    return router, unit


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["answer", "permission"])
async def test_products_before_a_pause_reach_the_turns_result(path):
    """The first loop ended at ask_user_question / cmd_permission_request (no
    `result`): what it produced is still on the unit, and the continuation's
    `result` delivers ONE block with both segments' files."""
    router, unit = _paused_router([_fc("out/after.md", "content"), {"type": "result"}])
    put = AsyncMock()
    with patch("database.db.messages.put", put):
        # segment 1 (main loop), ending at the pause
        for ev in (_fc("out/before.pptx", "content"), {"type": "ask_user_question", "toolUseId": "q"}):
            await router._persist_stream_event("sid", ev, [], files=router._turn_files_of(unit))
        assert set(unit._turn_files) == {"/ws/out/before.pptx"}
        cont = (router.continue_with_answer("sid", "a", tool_use_id="q") if path == "answer"
                else router.continue_with_cmd_permission("sid", "r", True))
        out = [ev async for ev in cont]
    (deliv,) = [e for e in out if e.get("type") == "assistant"]
    assert [f["absolutePath"] for f in deliv["content"][0]["files"]] == ["/ws/out/before.pptx", "/ws/out/after.md"]
    assert out[-1]["type"] == "result" and unit._turn_files == {}


def test_turn_files_of_tolerates_units_without_the_dict():
    from types import SimpleNamespace
    from unittest.mock import MagicMock
    assert SessionRouter._turn_files_of(SimpleNamespace()) is None
    assert SessionRouter._turn_files_of(MagicMock()) is None
    d: dict = {}
    assert SessionRouter._turn_files_of(SimpleNamespace(_turn_files=d)) is d


@pytest.mark.asyncio
async def test_send_admission_resets_the_turn_products():
    """A NEW turn starts clean: send() zeroes _turn_files at admission (the same
    slot as _turn_client_id), so a prior turn's products never leak into it."""
    from core.session_unit import SessionState
    from tests.test_session_unit_cleanliness import _make_unit, _StopAfterDecisionError
    from unittest.mock import MagicMock
    unit = _make_unit(state=SessionState.IDLE)
    unit._client = MagicMock()
    unit._last_turn_clean = True
    unit._turn_files = {"/ws/old.md": {"path": "old.md"}}
    unit._pipeline_runs_announced = {"run_old"}
    seen = {}

    async def _capture():
        seen["files"] = dict(unit._turn_files)
        seen["announced"] = set(unit._pipeline_runs_announced)
        raise _StopAfterDecisionError("stop")

    with patch.object(unit, "_crash_to_cold_async", AsyncMock()), \
         patch.object(unit, "_arm_recovery_checkpoint", AsyncMock()), \
         patch.object(unit, "_await_streaming_slot", _capture):
        with pytest.raises(_StopAfterDecisionError):
            async for _ in unit.send("hi", MagicMock(), app_session_id="app-1"):
                pass
    assert seen["files"] == {}
    assert seen["announced"] == set()


# ── plain reasons + when the card is due ─────────────────────────────────────

# run_e451e4a0's real push_ready_reason (2026-10-19), as the card showed it raw
REAL = ("unverified: plan: plan completed DEGRADED: 1 gate error(s) recorded as push_ready reasons "
        "(REPORT §10), not a stop; landing is decided by the test evidence; unverified: plan: completed "
        "with 1 open gate error(s): Design spec: design.states is empty — give no_state_reason (>= 20 "
        "chars: why the change adds and touches no state); unverified: build: build completed DEGRADED: "
        "2 gate error(s) recorded as push_ready reasons (REPORT §10), not a stop; landing is decided by "
        "the test evidence; unverified: build: completed with 2 open gate error(s): smoke_tests=0 but 2 "
        "files changed — run smoke tests with real objects; SMOKE step skipped: build touched 2 code "
        "files but smoke_tests=0 — runtime crashes (Attribut; unverified: plan: completed degraded after "
        "None refused attempts; unverified: build: completed degraded after None refused attempts; "
        "unverified: test: 15 continued test(s) passed without the earlier tests of their file; gate(s) "
        "passed on a recorded reason, not verified: deliver:gate2_recheck, deliver:gate2_findings")


def test_attention_due_waits_for_an_isolated_run_to_land():
    shared = {"isolated": False}
    assert pse.attention_due(shared, shared, True) is True
    assert pse.attention_due(shared, shared, False) is False
    iso, landed = {"isolated": True, "landed": False}, {"isolated": True, "landed": True}
    # deliver recorded, landing refused (changed_since_review): no card yet
    assert pse.attention_due(iso, iso, True) is False
    # the landing happened in this call: the card, once
    assert pse.attention_due(iso, landed, False) is True
    assert pse.attention_due(landed, landed, True) is False
    assert pse.attention_due(iso, None, True) is False


# ── one sentence per cause, with a code for the UI language (XG 2026-10-20) ──


