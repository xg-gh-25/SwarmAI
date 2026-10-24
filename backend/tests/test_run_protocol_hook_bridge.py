"""Tests SDK-to-v1 review event bridging.

Methodology: real temporary AuditStore plus synthetic Agent tool/final output.
Invariants: exact prompt binding, typed events, session copy, and run isolation.
"""
from __future__ import annotations

from core.run_protocol import events as ev
from core.run_protocol.hook_bridge import ReviewHookBridge, bridge_for, install_bridge
from core.run_protocol.store import AuditStore

AT = "2026-10-22T00:00:00+00:00"
FINDING = '{"severity":"HIGH","kind":"behavior","file":"a.py","line":12,"claim":"crash","fix":"guard"}'


def _bridge(tmp_path, run_id="run_one"):
    authority = ev.EventAuthority()
    store = AuditStore(tmp_path / "audit", run_id, authority)
    return ReviewHookBridge(store, "session_1"), store, authority


def _handout(store, authority, prompt, *, expert="correctness", round=1, ids=None):
    payload = {"expert_id": expert, "prompt_sha": ReviewHookBridge.prompt_sha(prompt)}
    if ids is not None:
        payload["finding_ids"] = ids
    return store.append_new(
        writer=authority.writer("cli"), kind="handout", at=AT, stage="REVIEW",
        round=round, attempt=1, payload=payload,
    )[0]


def test_exact_prompt_tool_use_becomes_answer_and_finding_events(tmp_path):
    bridge, store, authority = _bridge(tmp_path)
    prompt = "EXPERT-ID: correctness\nReview this diff"
    _handout(store, authority, prompt)
    bound = bridge.track_tool_use("tool-1", prompt)
    assert bound
    result = bridge.record_answer(bound, FINDING, completed_at=AT)
    assert result and result["parsed"].state == "parsed"
    chain = store.read_events()
    assert [row["kind"] for row in chain] == ["handout", "answer", "finding_occ"]
    assert chain[1]["payload"]["tool_use_id"] == "tool-1"
    assert chain[2]["payload"]["answer_event_id"] == chain[1]["event_id"]
    session = store.paths.session("session_1") / "events.jsonl"
    assert len(session.read_text().splitlines()) == 1


def test_untracked_or_changed_prompt_never_writes_an_answer(tmp_path):
    bridge, store, authority = _bridge(tmp_path)
    prompt = "EXPERT-ID: correctness\nReview this diff"
    _handout(store, authority, prompt)
    assert not bridge.track_tool_use("tool-1", prompt + " changed")
    assert bridge.record_answer(prompt, "NO FINDINGS", completed_at=AT) is None
    assert [row["kind"] for row in store.read_events()] == ["handout"]


def test_r2_status_and_foreign_status_create_typed_and_integrity_events(tmp_path):
    bridge, store, authority = _bridge(tmp_path)
    finding_id = "CF-aaaaaaaa"
    prompt = "EXPERT-ID: correctness\nRecheck one finding"
    _handout(store, authority, prompt, round=2, ids=[finding_id])
    bound = bridge.track_tool_use("tool-r2", prompt)
    assert bound
    result = bridge.record_answer(
        bound,
        f'{{"finding_id":"{finding_id}","status":"RESOLVED"}}\n'
        '{"finding_id":"CF-bbbbbbbb","status":"OPEN"}\nNO FINDINGS',
        completed_at="2026-10-22T00:01:30+00:00",
    )
    assert result and result["parsed"].integrity
    assert [row["kind"] for row in store.read_events()] == [
        "handout", "answer", "finding_status", "integrity"]


def test_transcript_head_prefix_still_binds_a_long_prompt(tmp_path):
    bridge, store, authority = _bridge(tmp_path)
    prompt = "EXPERT-ID: correctness\n" + "large diff line\n" * 400
    _handout(store, authority, prompt)
    bound = bridge.track_tool_use("tool-long", prompt)
    assert bound
    truncated_head = bound[:2000]
    result = bridge.record_answer(
        truncated_head, "NO FINDINGS", completed_at="2026-10-22T00:01:30+00:00")
    assert result and result["answer"]["payload"]["tool_use_id"] == "tool-long"
    assert result["answer"]["at"] == "2026-10-22T00:01:30+00:00"


def test_same_prompt_retries_bind_by_nonce_not_fifo(tmp_path):
    bridge, store, authority = _bridge(tmp_path)
    prompt = "EXPERT-ID: correctness\nReview this diff"
    _handout(store, authority, prompt)
    first_prompt = bridge.track_tool_use("tool-1", prompt)
    second_prompt = bridge.track_tool_use("tool-2", prompt)
    assert first_prompt and second_prompt and first_prompt != second_prompt
    second = bridge.record_answer(second_prompt, "NO FINDINGS", completed_at=AT)
    first = bridge.record_answer(first_prompt, "NO FINDINGS", completed_at="2026-10-22T00:01:00+00:00")
    # bound by nonce (not FIFO), and one handout yields exactly one answer
    assert second["answer"]["payload"]["tool_use_id"] == "tool-2"
    assert second["answer"]["payload"]["handout_event_id"]
    assert first is None
    assert [row["kind"] for row in store.read_events()].count("answer") == 1


def test_tampered_spawn_identity_is_rejected_without_consuming_it(tmp_path):
    bridge, store, authority = _bridge(tmp_path)
    prompt = "EXPERT-ID: correctness\nReview this diff"
    _handout(store, authority, prompt)
    bound = bridge.track_tool_use("tool-1", prompt)
    assert bound
    marker = bound.splitlines()[1]
    tampered = bound.replace(marker[-8:], "0" * 8, 1)
    assert bridge.record_answer(tampered, "NO FINDINGS", completed_at=AT) is None
    assert bridge.record_answer(bound, "NO FINDINGS", completed_at=AT) is not None


def test_usage_metrics_are_recorded_by_the_daemon(tmp_path):
    bridge, store, _ = _bridge(tmp_path)
    bridge.record_usage({"input_tokens": 3, "cache_read_tokens": 4,
                         "output_tokens": 2, "responses": 1}, completed_at=AT)
    metrics = store.read_events()
    assert [row["producer"] for row in metrics] == ["hook", "hook", "hook"]
    assert [row["payload"]["value"] for row in metrics] == [7, 2, 1]




def test_context_registry_is_per_run(tmp_path):
    one, _, _ = _bridge(tmp_path, "run_one")
    two, _, _ = _bridge(tmp_path, "run_two")
    context = {}
    install_bridge(context, "run_one", one)
    install_bridge(context, "run_two", two)
    assert bridge_for(context, "run_one") is one
    assert bridge_for(context, "run_two") is two
    assert bridge_for(context, "run_other") is None
