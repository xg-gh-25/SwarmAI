"""Tests runtime hook wiring for Pipeline v1 review events.

Methodology: invoke the real PreToolUse tracker and SubagentStop audit hook against
an injected temporary audit store; no production data or SDK process is used.
"""
from __future__ import annotations

import json

import pytest

from core.run_protocol import events as ev
from core.run_protocol.hook_bridge import ReviewHookBridge, install_bridge
from core.run_protocol.store import AuditStore

AT = "2026-10-22T00:00:00+00:00"


@pytest.mark.asyncio
async def test_runtime_hooks_persist_v1_answer_and_finding(tmp_path, monkeypatch):
    import core.runtime_hooks as hooks

    authority = ev.EventAuthority()
    store = AuditStore(tmp_path / "v1-audit", "run_one", authority)
    prompt = "EXPERT-ID: correctness\nReview the change"
    store.append_new(
        writer=authority.writer("cli"), kind="handout", at=AT,
        stage="REVIEW", round=1, attempt=1,
        payload={"expert_id": "correctness",
                 "prompt_sha": ReviewHookBridge.prompt_sha(prompt)},
    )
    context = {"sdk_session_id": "session_1", "_active_pipeline_run_id": "run_one"}
    install_bridge(context, "run_one", ReviewHookBridge(store, "session_1"))
    tracker = hooks.create_v1_review_handout_tracker(context)
    tracked = await tracker(
        {"tool_name": "Agent", "tool_input": {"prompt": prompt}}, "tool-1", None)
    bound_prompt = tracked["hookSpecificOutput"]["updatedInput"]["prompt"]

    transcript = tmp_path / "agent.jsonl"
    transcript.write_text("\n".join([
        json.dumps({"type": "user", "message": {"content": bound_prompt}}),
        json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "done"}]}}),
    ]) + "\n")
    finding = ('{"severity":"HIGH","kind":"behavior","file":"a.py","line":2,'
               '"claim":"crash","fix":"guard"}')
    monkeypatch.setattr(hooks, "_PIPELINE_AUDIT_DIR", tmp_path / "legacy-audit")
    callback = hooks.create_agent_tool_audit_hook(context)
    await callback({"agent_transcript_path": str(transcript), "agent_type": "general-purpose",
                    "last_assistant_message": finding, "cwd": str(tmp_path)}, None, None)

    kinds = [event["kind"] for event in store.read_events()]
    assert kinds == ["handout", "answer", "finding_occ"]
    assert store.read_events()[1]["payload"]["tool_use_id"] == "tool-1"
