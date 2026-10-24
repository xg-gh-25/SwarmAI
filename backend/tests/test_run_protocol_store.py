"""Tests Pipeline v1 per-run audit persistence.

Methodology: real temporary files, threads, and signed event chains only.
Invariants: locked append, fail-closed corruption, immutable blobs/contracts, and isolation.
"""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from core.run_protocol import events as ev
from core.run_protocol.store import AuditStore, StoreError

AT = "2026-10-22T00:00:00+00:00"


def _store(tmp_path, run_id="run_one"):
    authority = ev.EventAuthority()
    return AuditStore(tmp_path / "audit", run_id, authority), authority


def _decision(store, authority, request_id):
    return store.append_new(
        writer=authority.writer("cli"), kind="decision", at=AT,
        payload={"request_id": request_id, "decision_kind": "requirement_ambiguity",
                 "document": "REQUIREMENTS", "decision_json": "{}"},
    )


def test_missing_store_is_empty_but_not_a_positive_fact(tmp_path):
    store, _ = _store(tmp_path)
    assert store.read_events() == []
    assert not store.paths.events.exists()


def test_append_replay_and_large_blob_are_durable(tmp_path):
    store, authority = _store(tmp_path)
    event, status = store.append_new(
        writer=authority.writer("hook"), kind="answer", at=AT,
        payload={"handout_event_id": "h-1", "prompt_sha": "0" * 64,
                 "tool_use_id": "t1", "expert_id": "correctness", "stage": "REVIEW",
                 "round": 1, "state": "parsed", "raw": "x" * 2000},
    )
    assert status == "appended" and len(store.read_events()) == 1
    digest = event["payload"]["raw"]["blob"]
    assert (store.paths.blobs / digest).read_bytes() == b"x" * 2000
    assert store.append(event) == "duplicate"


def test_concurrent_append_keeps_one_valid_chain(tmp_path):
    store, authority = _store(tmp_path)
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda n: _decision(store, authority, f"q{n}"), range(30)))
    assert all(status == "appended" for _event, status in results)
    chain = store.read_events()
    assert len(chain) == 30 and ev.verify_chain(chain, authority) == []


def test_torn_tail_is_ignored_but_corrupt_middle_fails(tmp_path):
    store, authority = _store(tmp_path)
    _decision(store, authority, "q1")
    with open(store.paths.events, "ab") as handle:
        handle.write(b'{"torn":')
    assert len(store.read_events()) == 1
    _decision(store, authority, "q2")
    assert len(store.read_events()) == 2
    store.paths.events.write_bytes(b"not-json\n" + store.paths.events.read_bytes())
    with pytest.raises(StoreError, match="corrupt events line 1"):
        store.read_events()


def test_cross_run_event_and_unsafe_ids_are_rejected(tmp_path):
    store, authority = _store(tmp_path)
    other = AuditStore(tmp_path / "audit", "run_other", authority)
    event, _ = other.append_new(
        writer=authority.writer("cli"), kind="decision", at=AT,
        payload={"request_id": "q", "decision_kind": "x", "document": "d",
                 "decision_json": "{}"})
    with pytest.raises(StoreError, match="another run"):
        store.append(event)
    with pytest.raises(StoreError, match="run_id"):
        AuditStore(tmp_path, "../run_bad", authority)


def test_blob_and_contract_conflicts_fail_closed(tmp_path):
    store, authority = _store(tmp_path)
    store.write_contract_once({"protocol": 1})
    store.write_contract_once({"protocol": 1})
    assert "event_authority_secret" not in store.paths.contract.read_text()
    with pytest.raises(StoreError, match="contract conflict"):
        store.write_contract_once({"protocol": 2})
    event, blobs = ev.make_event(
        writer=authority.writer("hook"), run_id="run_one", kind="answer", at=AT,
        payload={"handout_event_id": "h-1", "prompt_sha": "0" * 64,
                 "tool_use_id": "t1", "expert_id": "correctness", "stage": "REVIEW",
                 "round": 1, "state": "parsed", "raw": "z" * 2000})
    digest = next(iter(blobs))
    store.paths.blobs.mkdir(parents=True, exist_ok=True)
    (store.paths.blobs / digest).write_bytes(b"wrong")
    with pytest.raises(StoreError, match="blob conflict"):
        store.append(event, blobs)


def test_event_with_missing_blob_is_not_appended(tmp_path):
    store, authority = _store(tmp_path)
    event, _blobs = ev.make_event(
        writer=authority.writer("hook"), run_id="run_one", kind="answer", at=AT,
        payload={"handout_event_id": "h-1", "prompt_sha": "0" * 64,
                 "tool_use_id": "t1", "expert_id": "correctness", "stage": "REVIEW",
                 "round": 1, "state": "parsed", "raw": "z" * 2000})
    with pytest.raises(StoreError, match="missing blob"):
        store.append(event, {})
    assert store.read_events() == []


def test_session_copy_is_deduplicated_and_records_run(tmp_path):
    store, authority = _store(tmp_path)
    event, _ = store.append_new(
        writer=authority.writer("hook"), kind="answer", at=AT,
        payload={"handout_event_id": "h-1", "prompt_sha": "0" * 64,
                 "tool_use_id": "t1", "expert_id": "red-team", "stage": "REVIEW",
                 "round": 1, "state": "parsed", "raw": "NO FINDINGS"})
    store.copy_to_session("session_1", event)
    store.copy_to_session("session_1", event)
    directory = store.paths.session("session_1")
    assert len((directory / "events.jsonl").read_text().splitlines()) == 1
    assert json.loads((directory / "runs.json").read_text()) == ["run_one"]
    with pytest.raises(StoreError, match="unsafe session_id"):
        store.copy_to_session("../bad", event)


def test_seals_replace_atomically_and_remain_run_bound(tmp_path):
    store, _ = _store(tmp_path)
    store.write_seals({"files": {"a": "1"}})
    store.write_seals({"files": {"a": "2"}})
    assert json.loads(store.paths.seals.read_text())["files"]["a"] == "2"
    with pytest.raises(StoreError, match="another run"):
        store.write_seals({"run_id": "run_other"})
