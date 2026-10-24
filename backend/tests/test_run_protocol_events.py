"""Pipeline v1 event-envelope tests (v13 §A3.1, §D4).

Methodology: pure in-process construction; no filesystem, clock or application data.
Invariants: occurrence identities are kind-specific, events are immutable, large
content leaves the 4 KB line, replays deduplicate, conflicts record integrity, and
``prev_hash`` links every appended event.
"""
from __future__ import annotations

import copy
import json

import pytest

from core.run_protocol import auth, events as ev

AT = "2026-10-21T00:00:00+00:00"
AUTH = ev.EventAuthority()


def _sample(expected):
    types = expected if isinstance(expected, tuple) else (expected,)
    if str in types:
        return "x"
    if bool in types:
        return True
    if int in types:
        return 1
    if float in types:
        return 1.0
    if list in types:
        return []
    if dict in types:
        return {}
    if type(None) in types:
        return None
    raise AssertionError(expected)


def _make(kind: str, *, previous=None, **overrides):
    spec = ev.EVENT_SPECS[kind]
    producer = overrides.pop("producer", None) or (
        spec.producer if isinstance(spec.producer, str) else spec.producer[0]
    )
    payload = {key: _sample(value) for key, value in spec.required.items()}
    custom_payload = overrides.pop("payload", {})

    if kind == "proof":
        payload.update({"code_tree": "1" * 40})
    if kind == "validation_report":
        payload.update({"stage": "build", "repo": "repo", "code_tree": "1" * 40})
    if kind == "run_create":
        payload["run_id"] = overrides.get("run_id", "run_abc")
    if kind == "auth_consumed":
        bindings = {"repo": {"code_tree": "1" * 40, "delivery_tree": "2" * 40,
                             "report_sha": "3" * 64}}
        request_id = custom_payload.get("request_id", "q1")
        request = auth.make_request(request_id=request_id, session_id="s1",
            run_id=overrides.get("run_id", "run_abc"), action="land", bindings=bindings)
        oracle = auth.AuthorizationOracle()
        oracle.issue(request, auth_event_id=custom_payload.get("auth_event_id", "a" * 64),
                     decided_at=0.0, expires_at=1800.0)
        result = oracle.consume(request_id=request_id, session_id="s1",
            run_id=overrides.get("run_id", "run_abc"), action="land",
            bindings=bindings, now=1.0)
        payload = result.consumption
    else:
        payload.update(custom_payload)
    if kind == "land_intent" and not ({"stage_attempt_event_id", "request_id"} & set(custom_payload)):
        payload["stage_attempt_event_id"] = "stage-1"
    base = {
        "run_id": "run_abc", "kind": kind, "at": AT,
        "payload": payload, "stage": "stage" if "stage" in spec.occurrence else None,
        "round": 1 if "round" in spec.occurrence else None,
        "attempt": 1 if "attempt" in spec.occurrence else None,
        "ordinal": 1 if "ordinal" in spec.occurrence else None,
        "ckey": None,
        "prev_hash": ev.event_digest(previous) if previous else None,
    }
    base.update(overrides)
    return ev.make_event(writer=AUTH.writer(producer), **base)


CASES = {
    "handout": ({"stage": "review", "round": 1, "attempt": 1,
                 "payload": {"expert_id": "correctness"}}, "attempt", 2),
    "answer": ({"payload": {"tool_use_id": "tool-1"}, "producer": "hook"},
               "payload.tool_use_id", "tool-2"),
    "finding_occ": ({"ordinal": 1, "payload": {"answer_event_id": "a1"},
                     "producer": "hook"}, "ordinal", 2),
    "finding_status": ({"ordinal": 1, "payload": {"answer_event_id": "a1"},
                        "producer": "hook"}, "ordinal", 2),
    "reclassify": ({"attempt": 1, "payload": {"canonical_id": "CF-1", "pass": 1}},
                   "attempt", 2),
    "address": ({"attempt": 1, "payload": {"canonical_id": "CF-1", "pass": 1, "hunk": "h"}},
                "payload.pass", 2),
    "proof": ({"attempt": 1, "payload": {"canonical_id": "CF-1", "pass": 1}},
              "attempt", 2),
    "stage_attempt": ({"stage": "plan", "attempt": 1}, "attempt", 2),
    "check": ({"payload": {"stage_attempt_event_id": "s1", "check_id": "RQ-1"}},
              "payload.check_id", "RQ-2"),
    "transition": ({"payload": {"from": "active", "to": "paused",
                                "trigger_event_id": "t1"}}, "payload.to", "failed"),
}
CASES.update({
    "render": ({"stage": "deliver", "attempt": 1, "payload": {"document": "REPORT"}},
               "payload.document", "REVIEW"),
    "render_failed": ({"payload": {"document": "REVIEW", "source_tail_event_id": "e1"}},
                      "payload.source_tail_event_id", "e2"),
    "land_intent": ({"payload": {"stage_attempt_event_id": "s1"}},
                    "payload.stage_attempt_event_id", "s2"),
    "land_request": ({"payload": {"request_id": "q1"}}, "payload.request_id", "q2"),
    "auth": ({"payload": {"request_id": "q1"}, "producer": "hook"},
             "payload.request_id", "q2"),
    "auth_consumed": ({"payload": {"auth_event_id": "a" * 64}},
                      "payload.auth_event_id", "b" * 64),
    "decision": ({"payload": {"request_id": "q1"}}, "payload.request_id", "q2"),
    "human_answer": ({"payload": {"request_id": "q1"}, "producer": "hook"},
                     "payload.request_id", "q2"),
    "land": ({"ordinal": 1}, "ordinal", 2),
    "abandon": ({"ordinal": 1}, "ordinal", 2),
    "restore": ({"ordinal": 1}, "ordinal", 2),
    "metric": ({"stage": "test", "attempt": 1, "payload": {"name": "tokens"}},
               "payload.name", "wall_seconds"),
    "run_create": ({"payload": {"run_id": "run_abc"}}, "payload.run_id", "run_def"),
    "stage_complete": ({"stage": "plan", "attempt": 1}, "attempt", 2),
    "infrastructure_failed": ({"ordinal": 1}, "ordinal", 2),
    "session_lost": ({"ordinal": 1}, "ordinal", 2),
    "validation_report": ({"ordinal": 1}, "ordinal", 2),
    "user_stop": ({"ordinal": 1}, "ordinal", 2),
    "review_reopen": ({"ordinal": 1}, "ordinal", 2),
    "worktree_unavailable": ({"ordinal": 1}, "ordinal", 2),
    "reflect_complete": ({"ordinal": 1}, "ordinal", 2),
    "reflect_exhausted": ({"ordinal": 1}, "ordinal", 2),
    "turn": ({"payload": {"message_id": "m1"}}, "payload.message_id", "m2"),
    "integrity": ({"ordinal": 1, "payload": {"conflict_id": "x1"}},
                  "payload.conflict_id", "x2"),
    "owner_transfer": ({"ordinal": 1, "producer": "hook"}, "ordinal", 2),
    "partial_land": ({"ordinal": 1}, "ordinal", 2),
    "gate0_exhausted": ({"ordinal": 1}, "ordinal", 2),
    "resume": ({"ordinal": 1}, "ordinal", 2),
})


def test_every_event_kind_has_an_occurrence_case():
    """A new kind without a CASES row fails here, not silently (conformance F24)."""
    assert set(CASES) == set(ev.EVENT_SPECS)


def _changed(kwargs, path, value):
    out = copy.deepcopy(kwargs)
    if path.startswith("payload."):
        out["payload"][path[8:]] = value
    else:
        out[path] = value
    return out


@pytest.mark.parametrize("kind", sorted(CASES))
def test_every_kind_has_a_distinct_occurrence_identity(kind):
    kwargs, path, value = CASES[kind]
    first, _ = _make(kind, **kwargs)
    changed = _changed(kwargs, path, value)
    if kind == "run_create":
        changed["run_id"] = changed["payload"]["run_id"]
    second, _ = _make(kind, **changed)
    assert first["event_id"] != second["event_id"]
    assert len(first["event_id"]) == 64


def test_kind_and_run_are_part_of_identity():
    decision, _ = _make("decision", payload={"request_id": "same"})
    answer, _ = _make("human_answer", payload={"request_id": "same"})
    other_run, _ = _make("decision", run_id="run_other", payload={"request_id": "same"})
    assert len({decision["event_id"], answer["event_id"], other_run["event_id"]}) == 3


def test_land_intent_accepts_request_id_for_run_land():
    a, _ = _make("land_intent", payload={"request_id": "q1"})
    b, _ = _make("land_intent", payload={"request_id": "q2"})
    assert a["event_id"] != b["event_id"]


def test_invalid_envelopes_and_writer_capabilities_are_rejected():
    with pytest.raises(ev.EventError, match="run_id"):
        _make("decision", run_id="")
    with pytest.raises(ev.EventError, match="producer"):
        AUTH.writer("model")
    with pytest.raises(ev.EventError, match="at"):
        _make("decision", at="")
    with pytest.raises(ev.EventError, match="kind"):
        ev.make_event(writer=AUTH.writer("cli"), run_id="run_x", kind="unknown",
                      at=AT, payload={})
    with pytest.raises(ev.EventError, match="attempt"):
        _make("handout", stage="review", round=1, attempt=None,
              payload={"expert_id": "e"})
    with pytest.raises(ev.EventError, match="writer capability"):
        ev.make_event(writer=None, run_id="run_x", kind="decision", at=AT,
                      payload={"request_id": "q", "decision_kind": "x", "document": "d"})


def test_large_and_forced_strings_become_content_addressed_blobs():
    large = "汉" * 500  # 1500 UTF-8 bytes, not merely 500 characters
    event, blobs = _make("answer", payload={
        "raw": large,
    })
    question = event["payload"]["raw"]
    raw = question
    assert set(question) == {"blob"} and set(raw) == {"blob"}
    assert blobs[question["blob"]] == large.encode("utf-8")
    assert len(ev.encode_event(event)) <= ev.MAX_EVENT_LINE_BYTES


def test_payload_is_copied_and_only_json_values_are_accepted():
    payload = {"refs": [1, True, None]}
    event, _ = _make("proof", payload=payload)
    payload["refs"].append("later")
    assert event["payload"]["refs"] == [1, True, None]
    with pytest.raises(ev.EventError, match="unsupported"):
        _make("proof", payload={"refs": [object()]})


def test_many_small_values_can_still_exceed_the_line_limit():
    payload = {"refs": ["x" * 900 for _ in range(6)]}
    with pytest.raises(ev.EventError, match="maximum"):
        _make("proof", payload=payload)


def test_jsonl_is_canonical_and_one_line():
    event, _ = _make("decision", payload={"request_id": "q1"})
    line = ev.encode_event(event)
    assert line.endswith(b"\n") and line.count(b"\n") == 1
    assert json.loads(line) == event


def test_append_and_exact_replay_are_immutable():
    first, _ = _make("decision", payload={"request_id": "q1"})
    original = copy.deepcopy(first)
    chain, status, blobs = ev.add_event(AUTH, [], first)
    assert status == "appended" and blobs == {} and chain == [first]
    replay, status, blobs = ev.add_event(AUTH, chain, copy.deepcopy(first))
    assert status == "duplicate" and blobs == {} and replay == chain
    assert first == original


def test_same_id_with_different_content_records_integrity_and_keeps_first():
    first, _ = _make("decision", payload={"request_id": "q1", "decision_kind": "one"})
    chain, _, _ = ev.add_event(AUTH, [], first)
    forged, _ = _make("decision", payload={"request_id": "q1", "decision_kind": "two"})
    out, status, blobs = ev.add_event(
        AUTH, chain, forged, integrity_ordinal=1, detected_by="hook",
    )
    assert status == "integrity" and blobs == {}
    assert out[0] == first and len(out) == 2
    conflict = out[1]
    assert conflict["kind"] == "integrity" and conflict["producer"] == "hook"
    assert conflict["payload"]["conflict_id"] == first["event_id"]
    assert conflict["prev_hash"] == ev.event_digest(first)
    assert ev.verify_chain(out, AUTH) == []


def test_new_event_must_link_to_the_tail():
    first, _ = _make("decision", payload={"request_id": "q1"})
    chain, _, _ = ev.add_event(AUTH, [], first)
    unlinked, _ = _make("decision", payload={"request_id": "q2"})
    with pytest.raises(ev.EventError, match="prev_hash"):
        ev.add_event(AUTH, chain, unlinked)
    linked, _ = _make("decision", payload={"request_id": "q2"}, previous=first)
    chain, status, _ = ev.add_event(AUTH, chain, linked)
    assert status == "appended" and ev.verify_chain(chain, AUTH) == []


def test_verify_chain_reports_identity_duplicate_size_and_link_errors():
    first, _ = _make("decision", payload={"request_id": "q1"})
    second, _ = _make("decision", payload={"request_id": "q2"}, previous=first)
    broken = [first, copy.deepcopy(second)]
    broken[1]["event_id"] = first["event_id"]
    broken[1]["prev_hash"] = "bad"
    errors = ev.verify_chain(broken, AUTH)
    assert any("event_id mismatch" in e for e in errors)
    assert any("duplicate event_id" in e for e in errors)
    assert any("prev_hash mismatch" in e for e in errors)


def test_bad_integrity_ordinal_does_not_silently_overwrite_a_conflict():
    first, _ = _make("decision", payload={"request_id": "q1"})
    chain, _, _ = ev.add_event(AUTH, [], first)
    changed, _ = _make("decision", payload={"request_id": "q1",
                                               "document": "different"})
    out, status, _ = ev.add_event(AUTH, chain, changed, integrity_ordinal=1)
    assert status == "integrity"
    with pytest.raises(ev.EventError, match="ordinal 1 already used"):
        # Reusing ordinal 1 creates the same integrity identity with different chain
        # position; the caller must allocate the next ordinal under the run lock.
        ev.add_event(AUTH, out, changed, integrity_ordinal=1)


def test_writer_rejects_a_forged_event_id():
    event, _ = _make("decision", payload={"request_id": "q1"})
    event["event_id"] = "0" * 64
    with pytest.raises(ev.EventError, match="event_id mismatch"):
        ev.add_event(AUTH, [], event)


@pytest.mark.parametrize("payload", [
    {"request_id": "q1", 1: "numeric key"},
    {"request_id": "q1", "value": float("nan")},
    {"request_id": "q1", "value": float("inf")},
])
def test_payload_rejects_non_json_object_keys_and_floats(payload):
    with pytest.raises(ev.EventError):
        _make("decision", payload=payload)


def test_every_kind_rejects_the_wrong_producer_and_ckey():
    for kind in ev.EVENT_SPECS:
        event, _ = _make(kind)
        wrong = copy.deepcopy(event)
        allowed = ev.EVENT_SPECS[kind].producer
        allowed = (allowed,) if isinstance(allowed, str) else allowed
        candidates = [p for p in ev.PRODUCERS if p not in allowed]
        if candidates:
            wrong["producer"] = candidates[0]
            with pytest.raises(ev.EventError, match="producer"):
                ev.validate_event(wrong, AUTH)
        if ev.EVENT_SPECS[kind].ckey_path:
            wrong = copy.deepcopy(event)
            wrong["ckey"] = "wrong"
            with pytest.raises(ev.EventError, match="ckey"):
                ev.validate_event(wrong, AUTH)


def test_payload_is_strict_per_kind():
    event, _ = _make("decision")
    missing = copy.deepcopy(event)
    del missing["payload"]["document"]
    with pytest.raises(ev.EventError, match="lacks fields"):
        ev.validate_event(missing, AUTH)
    extra = copy.deepcopy(event)
    extra["payload"]["tests_pass"] = True
    with pytest.raises(ev.EventError, match="unknown fields"):
        ev.validate_event(extra, AUTH)
    with pytest.raises(ev.EventError, match="wrong type"):
        _make("run_create", payload={"decision_cap": True})


def test_append_and_replay_refuse_an_already_corrupted_chain():
    first, _ = _make("decision", payload={"request_id": "q1"})
    second, _ = _make("decision", payload={"request_id": "q2"}, previous=first)
    chain, _, _ = ev.add_event(AUTH, [], first)
    chain, _, _ = ev.add_event(AUTH, chain, second)
    chain[0]["payload"]["decision_kind"] = "tampered"
    assert ev.verify_chain(chain, AUTH)
    third, _ = _make("decision", payload={"request_id": "q3"}, previous=second)
    with pytest.raises(ev.EventError, match="existing event chain"):
        ev.add_event(AUTH, chain, third)
    with pytest.raises(ev.EventError, match="existing event chain"):
        ev.add_event(AUTH, chain, second)


@pytest.mark.parametrize("run_id", ["..", "run_../x", "run_x\n", "other", "run_x/y"])
def test_event_entry_uses_the_shared_safe_run_id(run_id):
    with pytest.raises(ev.EventError, match="run_id"):
        _make("decision", run_id=run_id)


def test_occurrence_identity_field_cannot_be_externalized_into_a_different_identity():
    with pytest.raises(ev.EventError, match="occurrence field exceeds"):
        _make("answer", payload={"tool_use_id": "x" * 1025})


def test_run_create_envelope_and_payload_run_ids_must_match():
    with pytest.raises(ev.EventError, match="must equal"):
        _make("run_create", run_id="run_envelope",
              payload={"run_id": "run_payload"})


def test_auth_consumed_cannot_be_created_from_an_ordinary_payload():
    with pytest.raises(ev.EventError, match="oracle Consumption"):
        ev.make_event(writer=AUTH.writer("cli"), run_id="run_abc",
            kind="auth_consumed", at=AT, payload={
                "auth_event_id": "a" * 64, "request_id": "q",
                "session_id": "s", "run_id": "run_abc", "action": "land",
                "binding_digest": "d" * 64})
