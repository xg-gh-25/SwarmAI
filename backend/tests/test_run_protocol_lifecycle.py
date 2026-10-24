"""Event-sourced Pipeline v1 lifecycle tests (v13 §A1, §A3.1, §D4).

All projections fold a validated hash chain. State-changing triggers carry a matching
transition event. Attempts come from stage_attempt events; failed checks, land outcomes
and authorization are never caller booleans passed to the lifecycle.
"""
from __future__ import annotations

import copy

import pytest

from core.run_protocol import auth, events as ev
from core.run_protocol import lifecycle as lc

AT = "2026-10-22T00:00:00+00:00"


class Stream:
    def __init__(self, profile="direct", run_id="run_x", decision_cap=2):
        self.run_id, self.rows, self.ordinal = run_id, [], 0
        self.authority = ev.EventAuthority(b"l" * 32)
        self.oracle = auth.AuthorizationOracle()
        self.add("run_create", {"run_id": run_id, "profile": profile,
                                "decision_cap": decision_cap}, transition=False)

    def __deepcopy__(self, memo):
        clone = object.__new__(type(self))
        clone.run_id = self.run_id
        clone.rows = copy.deepcopy(self.rows, memo)
        clone.ordinal = self.ordinal
        clone.authority = self.authority
        clone.oracle = self.oracle
        return clone

    def add(self, kind, payload=None, *, stage=None, attempt=None, round=None,
            producer=None, transition=True):
        self.ordinal += 1
        spec = ev.EVENT_SPECS[kind]
        if producer is None:
            producer = spec.producer if isinstance(spec.producer, str) else spec.producer[0]
        if "ordinal" in spec.occurrence and attempt is None:
            ordinal = self.ordinal
        else:
            ordinal = None
        row, _ = ev.make_event(
            writer=self.authority.writer(producer),
            run_id=self.run_id, kind=kind, at=AT,
            payload=payload or {}, stage=stage, attempt=attempt, round=round,
            ordinal=ordinal, prev_hash=ev.event_digest(self.rows[-1]) if self.rows else None,
        )
        self.rows.append(row)
        if transition:
            projection = lc.fold(self.rows, self.authority, allow_pending_transition=True)
            expected = projection.get("expected_transition")
            if expected:
                self.add("transition", expected, transition=False)
        return row

    def projection(self):
        return lc.fold(self.rows, self.authority)

    def roster(self, stage=None):
        from core.run_protocol import checks as registry
        stage = stage or self.projection()["stage"]
        state = self.projection()
        if stage == "land":
            return sorted(lc.LAND_CHECKS)
        if stage == "deliver":   # the not-landed path's DV-1, else RF-1~4
            report_only = state["pending_landing"] is not None or state["profile"] == "research"
            return sorted(lc.REPORT_ONLY if report_only else lc.RF_CHECKS)
        return sorted({spec.check_id for spec in registry.stage_checks(state["profile"], stage)})

    def attempt(self, stage, checks=None, results=None):
        if checks is None:
            checks = self.roster(stage)
        projection = self.projection()
        number = projection["stage_attempts"].get(stage, 0) + 1
        opened = self.add("stage_attempt", {"check_ids": list(checks)}, stage=stage,
                          attempt=number, transition=False)
        for check_id in checks:
            self.add("check", {"stage_attempt_event_id": opened["event_id"],
                               "check_id": check_id,
                               "passed": (results or {}).get(check_id, True),
                               "detail": "deterministic result"}, transition=True)
        return opened

    def complete_stage(self, *, checks=None):
        stage = self.projection()["stage"]
        opened = self.attempt(stage, checks)
        payload = {"stage_attempt_event_id": opened["event_id"]}
        self.add("stage_complete", payload, stage=stage, attempt=opened["attempt"])

    def to_stage(self, wanted):
        while self.projection()["stage"] != wanted:
            stage = self.projection()["stage"]
            self.complete_stage()


@pytest.mark.parametrize("profile,sequence", sorted(lc.PROFILE_STAGES.items()))
def test_profile_opens_exact_sequence_without_skipped(profile, sequence):
    run = Stream(profile).projection()
    assert run["stage"] == sequence[0] and run["stages"][sequence[0]] == "open"
    assert set(run["stages"]) == set(sequence) and "skipped" not in run["stages"].values()


def test_projection_rejects_missing_transition_and_cross_run_or_corrupt_events():
    stream = Stream("full")
    stream.add("user_stop", {"reason": "stop"}, transition=False)
    with pytest.raises(lc.LifecycleError, match="required transition"):
        stream.projection()
    stream = Stream("full")
    other = Stream("full", run_id="run_other").rows[0]
    other["prev_hash"] = ev.event_digest(stream.rows[-1])
    with pytest.raises(lc.LifecycleError):
        lc.fold(stream.rows + [other], stream.authority)
    stream = Stream("full")
    stream.add("turn", {"message_id": "m1"}, transition=False)
    stream.rows[0]["payload"]["profile"] = "tampered"
    with pytest.raises(lc.LifecycleError, match="invalid event chain"):
        stream.projection()


def test_stage_attempts_are_sequential_not_caller_selected():
    stream = Stream("trivial")
    roster = stream.roster("evaluate")
    stream.attempt("evaluate", roster, {roster[0]: False})
    with pytest.raises((lc.LifecycleError, ev.EventError)):
        stream.add("stage_attempt", {"check_ids": roster}, stage="evaluate", attempt=3,
                   transition=False)
        stream.projection()


def test_stage_complete_requires_declared_check_events_all_passed():
    stream = Stream("trivial")
    roster = stream.roster("evaluate")
    opened = stream.add("stage_attempt", {"check_ids": roster}, stage="evaluate",
                        attempt=1, transition=False)
    with pytest.raises(lc.LifecycleError, match="all declared"):
        stream.add("stage_complete", {"stage_attempt_event_id": opened["event_id"]},
                   stage="evaluate", attempt=1)


def test_attacker_check_id_or_incomplete_roster_cannot_advance_plan():
    for roster in (["ATTACKER-CHECK"], ["RQ-1"]):
        stream = Stream("trivial")
        stream.add("stage_attempt", {"check_ids": roster}, stage="evaluate", attempt=1,
                   transition=False)
        with pytest.raises(lc.LifecycleError, match="registry roster"):
            stream.projection()


def test_test_completes_without_a_final_verdict():
    """S6-1: TEST's outcome is its validation report (checked by TS-1 before the
    stage_complete), so stage_complete carries no final verdict."""
    stream = Stream("direct")
    stream.to_stage("test")
    stream.complete_stage()
    assert stream.projection()["stage"] == "deliver"


def test_worktree_failure_pauses_and_resume_requires_ready_true():
    stream = Stream()
    stream.add("worktree_unavailable", {"reason": "cannot create"})
    assert stream.projection()["status"] == "paused"
    stream.add("resume", {"worktree_ready": False})
    assert stream.projection()["status"] == "paused"
    stream.add("resume", {"worktree_ready": True})
    assert stream.projection()["status"] == "active"


def test_validated_decision_and_answer_return_to_same_stage_and_reset_attempts():
    stream = Stream("full")
    stream.attempt("evaluate", results={"RQ-1": False})
    stream.add("decision", {"request_id": "q1", "decision_kind": "requirement_ambiguity",
                            "document": "decision-doc", "decision_json": "{}"})
    assert stream.projection()["status"] == "awaiting_human"
    stream.add("session_lost", {"reason": "closed"}, transition=False)
    assert stream.projection()["status"] == "awaiting_human"
    stream.add("human_answer", {"request_id": "q1", "option_id": "A"})
    run = stream.projection()
    assert run["status"] == "active" and run["stage"] == "evaluate"
    # numbers keep growing (event identity); the breaker restarts from the answer
    assert run["stage_attempts"]["evaluate"] == 1 and run["attempt_base"]["evaluate"] == 1
    assert run["human_decisions"] == 1
    for _ in range(2):
        stream.attempt("evaluate", results={"RQ-1": False})
    assert stream.projection()["pending_landing"] is None, "two attempts since the answer"
    stream.attempt("evaluate", results={"RQ-1": False})
    assert stream.projection()["pending_landing"]["reason"] == "checks_exhausted"


def test_decision_cap_is_enforced_from_folded_answers():
    stream = Stream("full", decision_cap=1)
    stream.add("decision", {"request_id": "q1", "decision_kind": "requirement_ambiguity",
                            "document": "decision-doc", "decision_json": "{}"})
    stream.add("human_answer", {"request_id": "q1", "option_id": "A"})
    with pytest.raises(lc.LifecycleError, match="cap"):
        stream.add("decision", {"request_id": "q2", "decision_kind": "design_tradeoff",
                                "document": "decision-doc", "decision_json": "{}"})


def test_third_failed_stage_attempt_enters_not_landed_without_asking_human():
    stream = Stream("full")
    for _ in range(3):
        stream.attempt("evaluate", results={"RQ-1": False})
    run = stream.projection()
    assert run["status"] == "active" and run["stage"] == "deliver"
    assert run["pending_landing"]["reason"] == "checks_exhausted"


def test_rf_exception_is_derived_only_from_actual_failed_check_ids():
    stream = Stream("direct")
    stream.to_stage("deliver")
    for _ in range(3):
        stream.attempt("deliver", results={"RF-1": False})
    run = stream.projection()
    assert run["stage"] == "deliver" and run["reflect"] == "degraded"
    # RF never trips the breaker, and a DV-1-only DELIVER attempt exists only on the
    # not-landed path (design §A1.2): on a landable run it is refused outright
    stream = Stream("direct")
    stream.to_stage("deliver")
    with pytest.raises(lc.LifecycleError, match="DV-1 on the not-landed path"):
        stream.attempt("deliver", checks=["DV-1"])


@pytest.mark.parametrize("profile", ["direct", "trivial", "research"])
def test_gate0_exhausted_is_illegal_for_profiles_without_gate0(profile):
    stream = Stream(profile)
    with pytest.raises(lc.LifecycleError, match="illegal|eligible state"):
        stream.add("gate0_exhausted", {"reason": "framing_rejected"})


@pytest.mark.parametrize("reason", ["framing_rejected", "already_satisfied"])
def test_gate0_exhausted_only_from_evaluate_enters_unlanded_deliver(reason):
    stream = Stream("full")
    stream.add("gate0_exhausted", {"reason": reason})
    run = stream.projection()
    assert run["stage"] == "deliver" and run["pending_landing"]["reason"] == reason


def _land_checks(stream):
    run = stream.projection()
    number = run["stage_attempts"].get("land", 0) + 1
    opened = stream.add("stage_attempt", {"check_ids": sorted(lc.LAND_CHECKS)},
                        stage="land", attempt=number, transition=False)
    for check_id in ("LG-1",):
        stream.add("check", {"stage_attempt_event_id": opened["event_id"],
                             "check_id": check_id, "passed": True, "detail": "pass"},
                   transition=False)
    stream.add("land_intent", {"stage_attempt_event_id": opened["event_id"]},
               transition=False)
    stream.add("render", {"document": "REPORT", "blob_sha": "a" * 64},
               stage="deliver", attempt=opened["attempt"], transition=False)
    for check_id in ("LG-3", "LG-4", "DV-1"):
        stream.add("check", {"stage_attempt_event_id": opened["event_id"],
                             "check_id": check_id, "passed": True, "detail": "pass"},
                   transition=False)
    return opened


def test_normal_land_requires_passing_land_checks_intent_and_commit():
    stream = Stream("direct")
    stream.to_stage("deliver")
    opened = _land_checks(stream)
    stream.add("land", {"landing": "landed", "commit": "a" * 40,
                        "stage_attempt_event_id": opened["event_id"]})
    run = stream.projection()
    assert run["status"] == "reflecting" and run["landing"] == "landed"
    stream.add("reflect_complete", {})
    assert stream.projection()["status"] == "completed"


def test_stage_complete_cannot_claim_landed_or_human_override():
    stream = Stream("direct")
    stream.to_stage("deliver")
    opened = stream.attempt("deliver")
    with pytest.raises(ev.EventError, match="unknown fields"):
        stream.add("stage_complete", {"stage_attempt_event_id": opened["event_id"],
                                      "landing": "landed"},
                   stage="deliver", attempt=opened["attempt"])


def test_not_landed_is_recorded_then_deliver_renders_and_reflects():
    stream = Stream("direct")
    stream.to_stage("deliver")
    stream.add("land", {"landing": "not_landed", "commit": None,
                        "reason": "checks_exhausted"}, transition=False)
    stream.complete_stage()
    run = stream.projection()
    assert run["status"] == "reflecting" and run["landing"] == "not_landed"
    assert run["reason"] == "checks_exhausted"


def test_research_deliver_derives_no_code_without_a_land_event():
    stream = Stream("research")
    stream.to_stage("deliver")
    stream.complete_stage()
    assert stream.projection()["landing"] == "no_code"


def test_reflect_exhaustion_completes_degraded():
    stream = Stream("research")
    stream.to_stage("deliver")
    stream.complete_stage()
    stream.add("reflect_exhausted", {"reason": "judge unavailable"})
    run = stream.projection()
    assert run["status"] == "completed" and run["reflect"] == "degraded"


def _authorize_land(stream, action="land"):
    bindings = {"repo": {"code_tree": "1" * 40, "delivery_tree": "2" * 40,
                         "report_sha": "3" * 64}}
    request = auth.make_request(request_id="q-land", session_id="s1",
                                run_id=stream.run_id, action=action, bindings=bindings)
    guards = lc.LG_CHECKS if action == "land" else lc.SKIP_LAND_CHECKS
    opened = stream.attempt("land", sorted(guards))
    stream.add("land_request", {"request_id": "q-land", "action": action,
                                "binding_digest": request["binding_digest"],
                                "request": request,
                                "guard_attempt_event_id": opened["event_id"],
                                "guard_check_ids": sorted(guards)}, transition=False)
    grant = stream.add("auth", {"request_id": "q-land", "session_id": "s1",
                                "run_id": stream.run_id, "action": action,
                                "binding_digest": request["binding_digest"],
                                "expires_at": 1800.0}, transition=False)
    stream.oracle.issue(request, auth_event_id=grant["event_id"],
                        decided_at=0.0, expires_at=1800.0)
    result = stream.oracle.consume(request_id="q-land", session_id="s1",
        run_id=stream.run_id, action=action, bindings=bindings, now=1.0)
    assert result.ok and result.consumption is not None
    return stream.add("auth_consumed", result.consumption, transition=False)


def _human_land(stream):
    consumed = _authorize_land(stream)
    stream.add("land", {"landing": "human_override", "commit": "a" * 40,
                        "auth_consumed_event_id": consumed["event_id"]})


def test_string_authorized_cannot_exist_in_typed_land_payload():
    stream = Stream()
    with pytest.raises(ev.EventError, match="unknown fields"):
        stream.add("land", {"landing": "human_override", "commit": "x",
                            "authorized": "false"})


def test_human_land_and_skip_tests_require_action_specific_guard_sets():
    for action, expected in (("land", lc.LG_CHECKS),
                             ("land_skip_tests", lc.SKIP_LAND_CHECKS)):
        stream = Stream()
        consumed = _authorize_land(stream, action)
        request = stream.projection()["land_requests"]["q-land"]
        assert set(request["guard_check_ids"]) == expected
        stream.add("land", {"landing": "human_override", "commit": "a" * 40,
                            "auth_consumed_event_id": consumed["event_id"]})
        assert stream.projection()["landing"] == "human_override"


def test_land_request_without_guards_cannot_reach_authorization():
    stream = Stream()
    bindings = {"repo": {"code_tree": "1" * 40, "delivery_tree": "2" * 40,
                         "report_sha": "3" * 64}}
    request = auth.make_request(request_id="q", session_id="s1", run_id=stream.run_id,
                                action="land", bindings=bindings)
    stream.add("land_request", {"request_id": "q", "action": "land", "request": request,
                                "binding_digest": request["binding_digest"]}, transition=False)
    with pytest.raises(lc.LifecycleError, match="action guards"):
        stream.projection()


def test_a_land_request_whose_request_does_not_match_its_digest_is_refused():
    stream = Stream()
    bindings = {"repo": {"code_tree": "1" * 40, "delivery_tree": "2" * 40,
                         "report_sha": "3" * 64}}
    request = auth.make_request(request_id="q", session_id="s1", run_id=stream.run_id,
                                action="land", bindings=bindings)
    forged = {**request, "bindings": {"repos": {"repo": {**bindings["repo"],
                                                         "code_tree": "9" * 40}}}}
    stream.add("land_request", {"request_id": "q", "action": "land", "request": forged,
                                "binding_digest": request["binding_digest"]}, transition=False)
    with pytest.raises(lc.LifecycleError, match="request"):
        stream.projection()


def test_human_override_requires_matching_consumed_auth_chain_and_counts_decision():
    stream = Stream()
    _human_land(stream)
    run = stream.projection()
    assert run["status"] == "reflecting" and run["landing"] == "human_override"
    assert run["human_decisions"] == 1


def test_forged_or_reused_auth_consumption_is_rejected():
    stream = Stream()
    with pytest.raises(ev.EventError, match="oracle Consumption"):
        stream.add("auth_consumed", {"auth_event_id": "a" * 64, "request_id": "q",
                   "session_id": "s1", "run_id": "run_x", "action": "land",
                   "binding_digest": "d" * 64}, transition=False)
    stream = Stream()
    consumed = _authorize_land(stream)
    with pytest.raises(ev.EventError, match="oracle Consumption"):
        stream.add("auth_consumed", consumed["payload"], transition=False)


def _completed(landing):
    stream = Stream("direct")
    stream.to_stage("deliver")
    if landing == "landed":
        opened = _land_checks(stream)
        stream.add("land", {"landing": "landed", "commit": "a" * 40,
                            "stage_attempt_event_id": opened["event_id"]})
    else:
        stream.add("land", {"landing": "not_landed", "commit": None,
                            "reason": "checks_exhausted"}, transition=False)
        stream.complete_stage()
    stream.add("reflect_complete", {})
    return stream


def test_human_land_allowed_from_abandoned_and_completed_not_landed_only():
    abandoned = Stream()
    abandoned.add("abandon", {"reason": "stop"})
    _human_land(abandoned)
    assert abandoned.projection()["landing"] == "human_override"
    ended = _completed("not_landed")
    _human_land(ended)
    run = ended.projection()
    assert run["status"] == "completed" and run["landing"] == "human_override"
    assert run["human_decisions"] == 1
    landed = _completed("landed")
    with pytest.raises(lc.LifecycleError, match="illegal|eligible state"):
        _human_land(landed)


def test_restore_only_abandoned_or_completed_not_landed():
    abandoned = Stream()
    abandoned.add("abandon", {"reason": "stop"})
    abandoned.add("restore", {"snapshot_exists": True})
    assert abandoned.projection()["status"] == "paused"
    landed = _completed("landed")
    with pytest.raises(lc.LifecycleError, match="restore"):
        landed.add("restore", {"snapshot_exists": True})


@pytest.mark.parametrize("kind", ["infrastructure_failed", "abandon"])
def test_terminal_events_end_active_without_human_question(kind):
    stream = Stream()
    stream.add(kind, {"reason": "broken"})
    assert stream.projection()["status"] == ("failed" if kind == "infrastructure_failed"
                                               else "abandoned")


def test_every_not_landed_reason_has_one_quality_result():
    got = {reason: lc.quality_result(reason) for reason in lc.NOT_LANDED_REASONS}
    assert {r for r, result in got.items() if result == "BLOCK"} == lc.QUALITY_BLOCK_REASONS
    assert set(got.values()) == {"BLOCK", "N/A"}


def test_only_decision_can_enter_awaiting_human():
    stream = Stream("full")
    candidates = [
        ("user_stop", {"reason": "stop"}),
        ("worktree_unavailable", {"reason": "no disk"}),
        ("abandon", {"reason": "stop"}),
        ("infrastructure_failed", {"reason": "broken"}),
    ]
    for kind, payload in candidates:
        candidate = copy.deepcopy(stream)
        candidate.add(kind, payload)
        assert candidate.projection()["status"] != "awaiting_human"
    stream.add("decision", {"request_id": "q", "decision_kind": "requirement_ambiguity",
                            "document": "decision-doc", "decision_json": "{}"})
    assert stream.projection()["status"] == "awaiting_human"


def test_land_requires_all_five_checks_and_land_intent_in_the_frozen_order():
    stream = Stream("direct")
    stream.to_stage("deliver")
    stream.add("stage_attempt", {"check_ids": ["LG-1"]}, stage="land", attempt=1,
               transition=False)
    with pytest.raises(lc.LifecycleError, match="action guard set"):
        stream.projection()

    stream = Stream("direct")
    stream.to_stage("deliver")
    opened = stream.add("stage_attempt", {"check_ids": sorted(lc.LAND_CHECKS)},
                        stage="land", attempt=1, transition=False)
    # LG-3 before land_intent violates §A3.7 ordering.
    for check_id in ("LG-1", "LG-3"):
        stream.add("check", {"stage_attempt_event_id": opened["event_id"],
                             "check_id": check_id, "passed": True, "detail": "pass"},
                   transition=False)
    stream.add("land_intent", {"stage_attempt_event_id": opened["event_id"]},
               transition=False)
    with pytest.raises(lc.LifecycleError, match="passing LG-1"):
        stream.projection()


def test_reflecting_landed_cannot_be_human_overridden():
    stream = Stream("direct")
    stream.to_stage("deliver")
    opened = _land_checks(stream)
    stream.add("land", {"landing": "landed", "commit": "a" * 40,
                        "stage_attempt_event_id": opened["event_id"]})
    assert stream.projection()["status"] == "reflecting"
    with pytest.raises(lc.LifecycleError, match="illegal|eligible state"):
        _human_land(stream)


def test_stale_land_intent_from_failed_attempt_cannot_authorize_next_attempt():
    stream = Stream("direct")
    stream.to_stage("deliver")
    # Attempt 1 reaches intent/render, then LG-4 fails (retryable: no not-landed
    # outcome; LG-3 would end the run as report_mismatch).
    opened1 = stream.add("stage_attempt", {"check_ids": sorted(lc.LAND_CHECKS)},
                         stage="land", attempt=1, transition=False)
    for cid in ("LG-1",):
        stream.add("check", {"stage_attempt_event_id": opened1["event_id"],
                             "check_id": cid, "passed": True, "detail": "pass"},
                   transition=False)
    stream.add("land_intent", {"stage_attempt_event_id": opened1["event_id"]},
               transition=False)
    stream.add("render", {"document": "REPORT", "blob_sha": "a" * 64},
               stage="deliver", attempt=1, transition=False)
    for cid, passed in (("LG-3", True), ("LG-4", False), ("DV-1", True)):
        stream.add("check", {"stage_attempt_event_id": opened1["event_id"],
                             "check_id": cid, "passed": passed, "detail": "result"},
                   transition=False)
    assert stream.projection()["open_attempt"] is None
    # Attempt 2 passes, but has no intent/render of its own.
    opened2 = stream.attempt("land")
    with pytest.raises(lc.LifecycleError, match="current land_intent"):
        stream.add("land", {"landing": "landed", "commit": "a" * 40,
                            "stage_attempt_event_id": opened2["event_id"]})


def test_human_override_requires_a_landed_commit_sha():
    stream = Stream()
    consumed = _authorize_land(stream)
    stream.add("land", {"landing": "human_override", "commit": None,
                        "auth_consumed_event_id": consumed["event_id"]},
               transition=False)
    with pytest.raises(lc.LifecycleError, match="landed commit SHA"):
        stream.projection()


def test_a_decision_logged_before_its_content_was_stored_folds_and_fails_dc1():
    """Conformance K1 review round 1 #4: an awaiting_human run whose decision event
    predates ``decision_json`` stays readable (it can be abandoned or answered by
    nobody), and DC-1 does not pass a decision whose content it cannot see."""
    from core.run_protocol import checks
    stream = Stream("full")
    stream.add("decision", {"request_id": "q1", "decision_kind": "requirement_ambiguity",
                            "document": "decision-doc"})
    assert stream.projection()["status"] == "awaiting_human"
    ctx = checks.make_context(profile="full", requirement_variant="full", requirements={},
                              design_variant="full", design={}, events=stream.rows,
                              authority=stream.authority)
    ok, detail = checks.evaluate("DC-1", ctx)
    assert ok is False and "q1" in detail, detail
