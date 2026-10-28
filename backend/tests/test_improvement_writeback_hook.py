"""Improvement writeback is a CDF source, never a DDD writer or judge.

Tests capture the ingestion boundary and verify payload routing, stable semantic
slots, conservative provenance, byte preservation, and off-event-loop execution.
"""

import asyncio
import threading
from pathlib import Path

from core.ingestion.api import EnqueueResult
from core.session_hooks import HookContext
from hooks.improvement_writeback_hook import ImprovementWritebackHook


def _ctx(session_id: str = "f1f7201b1234") -> HookContext:
    return HookContext(
        session_id=session_id, agent_id="agent_default", message_count=20,
        session_start_time="2026-07-20T00:00:00Z", session_title="test session")


def _capture(monkeypatch):
    calls = []

    def enqueue(*args, **kwargs):
        calls.append((threading.get_ident(), args, kwargs))
        return EnqueueResult(status="queued", candidate_id=f"cand-{len(calls)}")

    monkeypatch.setattr("core.ingestion.api.enqueue", enqueue)
    return calls


def test_lessons_enqueue_with_writeback_provenance_and_do_not_write(tmp_path, monkeypatch):
    hook = ImprovementWritebackHook(workspace_path="/unused")
    doc = tmp_path / "IMPROVEMENT.md"
    doc.write_text("# Lessons\n\n## What Worked\n\n## What Failed\n\n")
    before = doc.read_bytes()
    calls = _capture(monkeypatch)

    asyncio.run(hook._append_lessons(
        doc, {"worked": ["Single-writer intake keeps ownership clear."],
              "failed": ["A retry loop reused a poisoned subprocess."]}, _ctx()))

    assert len(calls) == 2
    assert all(call[1][0] == "cultivation" for call in calls)
    assert all(call[1][1] == "session_f1f7201b" for call in calls)
    assert [call[1][3]["proposal"]["section_hint"] for call in calls] == [
        "What Worked", "What Failed"]
    assert all(call[1][3]["kind"] == "cultivation" for call in calls)
    assert all(call[2]["envelope"] == {
        "source_event_ids": [], "evidence": "inferred", "tainted": True,
        "writer": "cultivation",
    } for call in calls)
    assert doc.read_bytes() == before
    assert not (tmp_path / ".artifacts").exists()


def test_writeback_does_not_call_the_legacy_judge(tmp_path, monkeypatch):
    import core.ddd_cultivation as dc

    hook = ImprovementWritebackHook(workspace_path="/unused")
    doc = tmp_path / "IMPROVEMENT.md"
    doc.write_text("# Lessons\n\n## What Worked\n\n")
    calls = _capture(monkeypatch)
    monkeypatch.setattr(dc, "admission_band", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("source-side judge must not run")))

    asyncio.run(hook._append_lessons(
        doc, {"worked": ["A durable lesson goes directly to CDF."], "failed": []}, _ctx()))

    assert len(calls) == 1


def test_replayed_writeback_uses_a_stable_semantic_slot(tmp_path, monkeypatch):
    hook = ImprovementWritebackHook(workspace_path="/unused")
    doc = tmp_path / "IMPROVEMENT.md"
    doc.write_text("# Lessons\n\n## What Worked\n\n")
    calls = _capture(monkeypatch)
    lessons = {"worked": ["A repeated lesson keeps one candidate identity."], "failed": []}

    asyncio.run(hook._append_lessons(doc, lessons, _ctx()))
    asyncio.run(hook._append_lessons(doc, lessons, _ctx()))

    assert calls[0][1][2] == calls[1][1][2]
    assert calls[0][1][3] == calls[1][1][3]


def test_ingestion_enqueue_runs_off_event_loop(tmp_path: Path, monkeypatch):
    hook = ImprovementWritebackHook(workspace_path="/unused")
    doc = tmp_path / "IMPROVEMENT.md"
    doc.write_text("# Lessons\n\n## What Worked\n\n")
    calls = _capture(monkeypatch)
    loop_thread = threading.get_ident()

    asyncio.run(hook._append_lessons(
        doc, {"worked": ["Enqueue work stays off the event loop."], "failed": []}, _ctx()))

    assert calls and all(call[0] != loop_thread for call in calls)
