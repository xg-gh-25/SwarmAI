"""Tests runtime hook wiring for Pipeline v1 review events.

Methodology: invoke the real PreToolUse tracker and SubagentStop audit hook (a SwarmAI
session's in-process hooks) against a temporary audit store that
``hook_bridge.store_for`` resolves to; no production data or SDK process is used. The
prompt reaches the expert unchanged: its PIPELINE-HANDOUT line binds it (standalone T2).
"""
from __future__ import annotations

import json

import pytest

from core.run_protocol import events as ev
from core.run_protocol import hook_bridge as hb
from core.run_protocol.store import AuditStore

AT = "2026-10-22T00:00:00+00:00"
NONCE = "c" * 32


@pytest.mark.asyncio
async def test_runtime_hooks_persist_v1_answer_and_finding(tmp_path, monkeypatch):
    import core.runtime_hooks as hooks

    store = AuditStore(tmp_path / "v1-audit", "run_one", ev.EventAuthority())
    prompt = f"EXPERT-ID: correctness\n{hb.handout_line('run_one', NONCE)}\nReview the change"
    store.append_new(
        writer=store.authority.writer("cli"), kind="handout", at=AT,
        stage="review", round=1, attempt=1,
        payload={"expert_id": "correctness", "prompt_sha": hb.prompt_sha(prompt),
                 "nonce": NONCE},
    )
    monkeypatch.setattr(hb, "store_for", lambda text: (store, "session_1")
                        if hb.parse_binding(text) == ("run_one", NONCE) else None)
    context = {"sdk_session_id": "session_1", "_active_pipeline_run_id": "run_one"}
    tracked = await hooks.create_v1_review_handout_tracker(context)(
        {"tool_name": "Agent", "tool_input": {"prompt": prompt}}, "tool-1", None)
    assert tracked == {}                    # the prompt is not rewritten

    transcript = tmp_path / "agent.jsonl"
    transcript.write_text("\n".join([
        json.dumps({"type": "user", "message": {"content": prompt}}),
        json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "done"}]}}),
    ]) + "\n")
    finding = ('{"severity":"HIGH","kind":"behavior","file":"a.py","line":2,'
               '"claim":"crash","fix":"guard"}')
    monkeypatch.setattr(hooks, "_PIPELINE_AUDIT_DIR", tmp_path / "legacy-audit")
    callback = hooks.create_agent_tool_audit_hook(context)
    await callback({"agent_transcript_path": str(transcript), "agent_type": "general-purpose",
                    "last_assistant_message": finding, "cwd": str(tmp_path)}, None, None)

    kinds = [event["kind"] for event in store.read_events()]
    assert kinds == ["handout", "spawn", "answer", "finding_occ"]
    assert store.read_events()[2]["payload"]["tool_use_id"] == "tool-1"
