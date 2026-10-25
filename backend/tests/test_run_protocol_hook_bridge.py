"""Tests binding review experts' answers to v1 handouts (``core.run_protocol.hook_bridge``).

Methodology: a real temporary AuditStore; handouts carry their nonce and the prompt its
``PIPELINE-HANDOUT`` line, as ``review.ReviewOrchestrator.handout`` writes them.
Invariants (standalone §4.3): a spawn binds only with the exact handout prompt; an
answer from a transcript head needs that spawn; an exact host prompt needs none; one
answer per handout; no session memory is involved (any process binds the same).
"""
from __future__ import annotations

from core.run_protocol import events as ev
from core.run_protocol import hook_bridge as hb
from core.run_protocol.store import AuditStore

AT = "2026-10-22T00:00:00+00:00"
FINDING = '{"severity":"HIGH","kind":"behavior","file":"a.py","line":12,"claim":"crash","fix":"guard"}'
NONCE = "a" * 32


def _store(tmp_path, run_id="run_one"):
    return AuditStore(tmp_path / "audit", run_id, ev.EventAuthority())


def _handout(store, body="Review this diff", *, expert="correctness", round=1, ids=None,
             nonce=NONCE):
    prompt = f"EXPERT-ID: {expert}\n{hb.handout_line(store.paths.run_id, nonce)}\n\n{body}\n"
    payload = {"expert_id": expert, "prompt_sha": hb.prompt_sha(prompt), "nonce": nonce}
    if ids is not None:
        payload["finding_ids"] = ids
    store.append_new(writer=store.authority.writer("cli"), kind="handout", at=AT,
                     stage="review", round=round, attempt=1, payload=payload)
    return prompt


def _kinds(store):
    return [row["kind"] for row in store.read_events()]


def test_the_handout_line_names_the_run_and_nonce():
    line = hb.handout_line("run_ab12", NONCE)
    assert hb.parse_binding(f"EXPERT-ID: x\n{line}\nbody") == ("run_ab12", NONCE)
    assert hb.parse_binding("no line") is None
    assert hb.parse_binding(f"{line}\n{hb.handout_line('run_cd34', NONCE)}") is None


def test_a_bound_spawn_becomes_answer_and_finding_events(tmp_path):
    store = _store(tmp_path)
    prompt = _handout(store)
    assert hb.bind_spawn(store, prompt, "tool-1")
    result = hb.record_answer(store, prompt, FINDING, completed_at=AT, session_id="session_1")
    assert result and result["parsed"].state == "parsed"
    chain = store.read_events()
    assert _kinds(store) == ["handout", "spawn", "answer", "finding_occ"]
    assert chain[2]["payload"]["tool_use_id"] == "tool-1"
    assert chain[2]["payload"]["source"] == "host"
    assert chain[3]["payload"]["answer_event_id"] == chain[2]["event_id"]
    session = store.paths.session("session_1") / "events.jsonl"
    assert len(session.read_text().splitlines()) == 1


def test_a_changed_prompt_binds_nothing_and_an_unbound_answer_is_dropped(tmp_path):
    store = _store(tmp_path)
    prompt = _handout(store)
    assert hb.bind_spawn(store, prompt + " changed", "tool-1") is None
    assert hb.record_answer(store, prompt, "NO FINDINGS", completed_at=AT) is None
    assert _kinds(store) == ["handout"]


def test_a_transcript_head_binds_through_the_spawn(tmp_path):
    store = _store(tmp_path)
    prompt = _handout(store, "large diff line\n" * 400)
    assert hb.bind_spawn(store, prompt, "tool-long")
    result = hb.record_answer(store, prompt[:2000], "NO FINDINGS",
                              completed_at="2026-10-22T00:01:30+00:00")
    assert result and result["answer"]["payload"]["tool_use_id"] == "tool-long"
    assert result["answer"]["at"] == "2026-10-22T00:01:30+00:00"


def test_an_exact_host_prompt_needs_no_spawn_but_must_be_exact(tmp_path):
    store = _store(tmp_path)
    prompt = _handout(store)
    assert hb.record_answer(store, prompt[:30], "NO FINDINGS", completed_at=AT,
                            exact=True) is None
    result = hb.record_answer(store, prompt, "NO FINDINGS", completed_at=AT, exact=True)
    assert result and result["answer"]["payload"]["tool_use_id"] == f"nonce:{NONCE}"


def test_one_answer_per_handout(tmp_path):
    store = _store(tmp_path)
    prompt = _handout(store)
    assert hb.bind_spawn(store, prompt, "tool-1") and hb.bind_spawn(store, prompt, "tool-2")
    assert hb.record_answer(store, prompt, "NO FINDINGS", completed_at=AT)
    assert hb.record_answer(store, prompt, "NO FINDINGS", completed_at=AT) is None
    assert _kinds(store).count("answer") == 1


def test_a_tampered_or_foreign_nonce_binds_nothing(tmp_path):
    store = _store(tmp_path)
    prompt = _handout(store)
    hb.bind_spawn(store, prompt, "tool-1")
    assert hb.record_answer(store, prompt.replace(NONCE, "b" * 32), "NO FINDINGS",
                            completed_at=AT) is None
    other = _store(tmp_path, "run_two")
    assert hb.bind_spawn(other, prompt, "tool-9") is None        # names run_one
    assert hb.record_answer(store, prompt, "NO FINDINGS", completed_at=AT) is not None


def test_r2_status_and_foreign_status_create_typed_and_integrity_events(tmp_path):
    store = _store(tmp_path)
    finding_id = "CF-aaaaaaaa"
    prompt = _handout(store, "Recheck one finding", round=2, ids=[finding_id])
    hb.bind_spawn(store, prompt, "tool-r2")
    result = hb.record_answer(
        store, prompt,
        f'{{"finding_id":"{finding_id}","status":"RESOLVED"}}\n'
        '{"finding_id":"CF-bbbbbbbb","status":"OPEN"}\nNO FINDINGS',
        completed_at="2026-10-22T00:01:30+00:00")
    assert result and result["parsed"].integrity
    assert _kinds(store) == ["handout", "spawn", "answer", "finding_status", "integrity"]


def test_usage_metrics_are_recorded_by_the_hook(tmp_path):
    store = _store(tmp_path)
    hb.record_usage(store, {"input_tokens": 3, "cache_read_tokens": 4,
                            "output_tokens": 2, "responses": 1}, completed_at=AT)
    metrics = store.read_events()
    assert [row["producer"] for row in metrics] == ["hook", "hook", "hook"]
    assert [row["payload"]["value"] for row in metrics] == [7, 2, 1]
