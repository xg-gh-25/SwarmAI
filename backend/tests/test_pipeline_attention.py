"""Pipeline alignment + needs-you blocks, and the turn's deliverables (chat v7.1).

What is tested:
- run_block: the run's requirement (single-spaced, capped) and its planned stages;
  None without a requirement
- attention_block, from STRUCTURED fields only, on real-shaped run.json + artifacts
  in a temp run dir: self-answered clarifications, assumed requirements, Gate 0 /
  Gate 1 verdicts that are not a pass (PASS / SUPPORTED / SKIPPED / resolved BLOCK
  are quiet), Gate 2 open findings from the deliver artifact, push_ready False with
  its reasons; None when nothing needs the user; unreadable artifacts are skipped
- the orchestrator: a pipeline_run block once per run per session, the attention
  block only with the DELIVER row
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


def _run_dir(tmp: Path, run_id: str, run: dict, artifacts: dict[str, dict]) -> Path:
    d = tmp / "Projects" / "SwarmAI" / ".artifacts" / "runs" / run_id
    d.mkdir(parents=True)
    (d / "run.json").write_text(json.dumps(run), encoding="utf-8")
    for name, data in artifacts.items():
        (d / name).write_text(json.dumps(data), encoding="utf-8")
    return d


RUN = {
    "profile": "full", "status": "running",
    "requirement": "Add  claude-opus-5-5\n to MODEL_REGISTRY after opus-4-8.",
    "stages": [
        {"stage": "evaluate", "status": "completed", "artifact_id": "art_ev1", "gate0_verdict": "WRONG-FRAME → fixed"},
        {"stage": "build", "status": "completed", "gate1_verdict": "WARN"},
        {"stage": "deliver", "status": "completed", "artifact_id": "art_dl1", "push_ready": False,
         "push_ready_reason": "tests: 1 failed; findings: 1 unresolved HIGH"},
    ],
}
EVAL = {"data": {
    "clarifications": [
        {"qid": "Q1", "question": "Where does the guard go?", "answer": "B. prompt_builder at runtime",
         "answered_by": "self", "resolution": "covers stored configs too"},
        {"qid": "Q2", "question": "Ask the user?", "answer": "A", "answered_by": "user"},
    ],
    "requirements": [
        {"id": "FR-1", "statement": "registry has opus-5-5", "status": "met", "source": {"kind": "user"}},
        {"id": "FR-3", "statement": "eval later", "status": "proposed", "source": {"kind": "assumption"}},
        {"id": "FR-5", "statement": "which tab?", "status": "needs-clarification", "source": {"kind": "user"}},
    ],
}}
DELIVER = {"adversarial_review": {"findings_total": 3, "findings_fixed": 2}}


def _state(tmp_path, monkeypatch, run=RUN, arts=None):
    monkeypatch.setattr(pse, "get_swarmws", lambda: str(tmp_path))
    _run_dir(tmp_path, "run_a1", run, arts if arts is not None else {
        "evaluation-20261018-art_ev1.json": EVAL, "delivery-20261018-art_dl1.json": DELIVER})
    return pse.read_stage_state("run_a1")


def test_run_block_is_the_alignment(tmp_path, monkeypatch):
    b = pse.run_block("run_a1", _state(tmp_path, monkeypatch), "T")
    assert b["type"] == "pipeline_run" and b["runId"] == "run_a1"
    assert b["requirement"] == "Add claude-opus-5-5 to MODEL_REGISTRY after opus-4-8."
    assert b["stages"][0] == "evaluate" and "deliver" in b["stages"]
    assert pse.run_block("run_a1", {"stages": {}, "requirement": "  "}, "T") is None


def test_attention_items_come_from_structured_fields_only(tmp_path, monkeypatch):
    b = pse.attention_block("run_a1", _state(tmp_path, monkeypatch), "T")
    kinds = [(i["kind"], i.get("name") or i.get("id")) for i in b["items"]]
    assert kinds == [("self_answered", "Q1"), ("assumption", "FR-3"), ("assumption", "FR-5"), ("gate", "GATE 0"), ("gate", "GATE 1"),
                     ("gate", "GATE 2"), ("not_push_ready", None)]
    q1 = b["items"][0]
    assert (q1["answer"], q1["reason"]) == ("prompt_builder at runtime", "covers stored configs too")
    assert b["items"][5]["verdict"] == "1/3 open"
    assert b["items"][6]["reasons"] == ["tests: 1 failed", "findings: 1 unresolved HIGH"]


@pytest.mark.parametrize("verdict", ["PASS", "SUPPORTED (calibrated)", "SKIPPED", "PROCEED-WITH-FIXES",
                                     "BLOCK-resolved", "PASS (v2, prior BLOCK resolved)"])
def test_quiet_verdicts_need_no_one(tmp_path, monkeypatch, verdict):
    run = {**RUN, "stages": [{"stage": "build", "status": "completed", "gate1_verdict": verdict},
                             {"stage": "deliver", "status": "completed", "push_ready": True}]}
    all_fixed = {"delivery-x-art_dl1.json": {"adversarial_review": {"findings_total": 2, "findings_fixed": 2}}}
    run["stages"][1]["artifact_id"] = "art_dl1"
    assert pse.attention_block("run_a1", _state(tmp_path, monkeypatch, run, all_fixed), "T") is None


def test_unreadable_artifacts_contribute_nothing(tmp_path, monkeypatch):
    st = _state(tmp_path, monkeypatch, arts={"evaluation-x-art_ev1.json": {}})
    (Path(st["run_dir"]) / "evaluation-x-art_ev1.json").write_text("{not json", encoding="utf-8")
    b = pse.attention_block("run_a1", st, "T")
    assert [i["kind"] for i in b["items"]] == ["gate", "gate", "not_push_ready"]


# ── orchestrator: alignment once per run, attention only with DELIVER ────────

from tests.test_pipeline_stage_events import (  # noqa: E402 — shared stream helpers
    CLI, _ScriptedReader, _bash_use, _drive_stream, _isolated_queue, _result,  # noqa: F401
)

_S1 = {"profile": "full", "status": "running", "requirement": "Do X",
       "stages": {"evaluate": {"stage": "evaluate", "status": "completed", "gate0_verdict": "WRONG-FRAME"}}}
_S2 = {**_S1, "stages": {**_S1["stages"], "think": {"stage": "think", "status": "completed"}}}
_S3 = {**_S2, "stages": {**_S2["stages"], "deliver": {
    "stage": "deliver", "status": "completed", "push_ready": False, "push_ready_reason": "tests: 1 failed"}}}


def _blocks(events):
    return [b for e in events if e.get("type") == "assistant"
            for b in e.get("content") or [] if isinstance(b, dict)
            and b.get("type") in ("pipeline_run", "pipeline_stage", "pipeline_attention")]


def _turn(orch, monkeypatch, reader, tu):
    from core.permission_manager import permission_manager
    from tests.test_tool_call_leak_guard import _make_result_message
    monkeypatch.setattr(pse, "read_stage_state", reader)
    permission_manager.remove_session_queue("sess-test")  # each turn = its own loop
    events, raised = _drive_stream(orch, [_bash_use(tu, CLI.format(rid="run_a1")), _result(tu),
                                          _make_result_message()])
    assert raised is None
    return _blocks(events)


@pytest.mark.usefixtures("_isolated_queue")
def test_orchestrator_announces_the_run_once_and_attention_rides_deliver(monkeypatch):
    from tests.test_tool_call_leak_guard import _make_orchestrator
    orch = _make_orchestrator()
    first = _turn(orch, monkeypatch, _ScriptedReader(_S1, _S2), "tu1")
    assert [(b["type"], b.get("stage")) for b in first] == [("pipeline_run", None), ("pipeline_stage", "think")]
    assert first[0]["requirement"] == "Do X"
    later = _turn(orch, monkeypatch, _ScriptedReader(_S3), "tu2")
    assert [(b["type"], b.get("stage")) for b in later] == [("pipeline_stage", "deliver"), ("pipeline_attention", None)]
    assert [i["kind"] for i in later[1]["items"]] == ["gate", "not_push_ready"]  # the WRONG-FRAME waited for DELIVER


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
