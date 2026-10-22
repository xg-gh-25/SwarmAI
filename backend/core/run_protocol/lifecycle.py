"""Event-sourced Pipeline v1 lifecycle projection (v13 §A1, §A3.1).

Public symbols:
- ``fold`` — derive the run projection only from a complete validated event chain.
- ``quality_result`` — deterministic not-landed reason to Quality result.
- ``PROFILE_STAGES`` — exact profile sequences (there is no skipped stage).
- ``LifecycleError`` — the stream violates the transition table.

A state-changing trigger MUST be followed by its matching ``transition`` event. Callers
cannot pass ``authorized``, ``stage_checks_passed``, attempt counts or RF exemptions.
"""
from __future__ import annotations

import copy
from typing import Any, Mapping

from core.run_protocol import events as ev

STATUSES = frozenset({"active", "awaiting_human", "paused", "reflecting",
                      "completed", "abandoned", "failed"})
TERMINAL = frozenset({"completed", "abandoned", "failed"})
LANDINGS = frozenset({"landed", "not_landed", "human_override", "no_code"})
NOT_LANDED_REASONS = frozenset({
    "tests_red", "tests_incomplete", "validation_failed", "final_stale",
    "report_mismatch", "checks_exhausted", "framing_rejected",
    "already_satisfied", "needs_hooked_session", "land_conflict",
})
QUALITY_BLOCK_REASONS = frozenset({
    "tests_red", "tests_incomplete", "validation_failed", "final_stale", "report_mismatch",
})
RF_CHECKS = frozenset({"RF-1", "RF-2", "RF-3", "RF-4"})
LG_CHECKS = frozenset({"LG-1", "LG-2", "LG-3", "LG-4", "LG-5"})
NORMAL_LAND_CHECKS = LG_CHECKS | {"DV-1"}   # RF-1~4 are DELIVER stage checks, not guards
REPORT_ONLY = frozenset({"DV-1"})            # the not-landed path's DELIVER attempt
SKIP_LAND_CHECKS = frozenset({"LG-3", "LG-4"})
LAND_CHECKS = NORMAL_LAND_CHECKS  # public compatibility name for cycle-1 tests
PROFILE_STAGES = {
    "full": ("evaluate", "think", "plan", "build", "review", "test", "deliver", "reflect"),
    "bugfix": ("evaluate", "think", "plan", "build", "review", "test", "deliver", "reflect"),
    "goal": ("evaluate", "think", "plan", "goal_cycle", "review", "test", "deliver", "reflect"),
    "trivial": ("evaluate", "plan", "build", "review", "test", "deliver", "reflect"),
    "direct": ("build", "review", "test", "deliver", "reflect"),   # no PLAN (exec plan v2 N1)
    "research": ("evaluate", "think", "deliver", "reflect"),
}
IGNORED = frozenset({
    "handout", "answer", "answer_reuse", "finding_occ", "finding_status",
    "reclassify", "address", "proof", "test_call", "metric", "turn", "integrity",
    "owner_transfer",
})


class LifecycleError(ValueError):
    """The typed event stream violates the frozen lifecycle table."""


def _commit_sha(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 40 \
        and all(char in "0123456789abcdef" for char in value)


def quality_result(reason: str) -> str:
    if reason not in NOT_LANDED_REASONS:
        raise LifecycleError(f"unknown not_landed reason {reason!r}")
    return "BLOCK" if reason in QUALITY_BLOCK_REASONS else "N/A"


def _initial(event: Mapping[str, Any]) -> dict[str, Any]:
    payload = event["payload"]
    if payload["run_id"] != event["run_id"]:
        raise LifecycleError("run_create payload/envelope run_id mismatch")
    profile = payload["profile"]
    if profile not in PROFILE_STAGES:
        raise LifecycleError(f"unknown profile {profile!r}")
    cap = payload["decision_cap"]
    if isinstance(cap, bool) or cap < 0:
        raise LifecycleError("decision_cap must be non-negative")
    sequence = PROFILE_STAGES[profile]
    stages = {stage: "pending" for stage in sequence}
    stages[sequence[0]] = "open"
    return {
        "run_id": payload["run_id"], "profile": profile, "status": "active",
        "stage": sequence[0], "stages": stages, "landing": None, "reason": None,
        "reflect": None, "decision_cap": cap, "human_decisions": 0,
        "pending_decision": None, "pending_landing": None,
        "stage_attempts": {}, "attempt_base": {}, "open_attempt": None,
        "last_land_intent": None, "last_report_render": None,
        "land_requests": {}, "auth_grants": {},
        "auth_consumed": {}, "expected_transition": None,
    }


def _schedule(run: dict[str, Any], trigger: Mapping[str, Any], status: str) -> None:
    if run["expected_transition"] is not None:
        raise LifecycleError("previous state change has no transition event")
    old = run["status"]
    run["status"] = status
    run["expected_transition"] = {
        "from": old, "to": status, "trigger_event_id": trigger["event_id"],
    }


def _transition(run: dict[str, Any], event: Mapping[str, Any]) -> None:
    expected = run.get("expected_transition")
    if expected is None:
        raise LifecycleError("unexpected transition event")
    if event["payload"] != expected:
        raise LifecycleError(f"transition does not match expected {expected}")
    run["expected_transition"] = None


def _open_next(run: dict[str, Any]) -> None:
    sequence = PROFILE_STAGES[run["profile"]]
    index = sequence.index(run["stage"])
    if index + 1 >= len(sequence):
        raise LifecycleError("current stage has no next stage")
    run["stages"][run["stage"]] = "completed"
    run["stage"] = sequence[index + 1]
    run["stages"][run["stage"]] = "open"


def _attempt(run: dict[str, Any], event: Mapping[str, Any]) -> None:
    stage = event["stage"]
    if stage not in (run["stage"], "land"):
        raise LifecycleError("stage_attempt is not for the current stage or land")
    previous = run["open_attempt"]
    if previous is not None:
        complete = set(previous["results"]) == set(previous["check_ids"]) \
            and all(value is True for value in previous["results"].values())
        # A passing land / RF attempt waits for its land event or the next step; a
        # new attempt supersedes it. Anything else is genuinely unfinished.
        if not (complete and previous["stage"] in ("land", "deliver")):
            raise LifecycleError("previous stage_attempt is incomplete")
    expected = run["stage_attempts"].get(stage, 0) + 1
    if event["attempt"] != expected:
        raise LifecycleError(f"stage_attempt must be sequential ({expected})")
    check_ids = event["payload"]["check_ids"]
    if not isinstance(check_ids, list) or any(
        not isinstance(check, str) or not check for check in check_ids
    ) or len(set(check_ids)) != len(check_ids):
        raise LifecycleError("stage_attempt check_ids must be unique text")
    if stage == "land":
        valid_land_sets = {NORMAL_LAND_CHECKS, LG_CHECKS, SKIP_LAND_CHECKS}
        if frozenset(check_ids) not in valid_land_sets:
            raise LifecycleError("land attempt check_ids are not a valid action guard set")
    elif stage == "deliver":
        ids = frozenset(check_ids)
        report_only = run.get("pending_landing") is not None or run["profile"] == "research"
        if ids != RF_CHECKS and not (ids == REPORT_ONLY and report_only):
            raise LifecycleError("DELIVER attempts are RF-1~4, or DV-1 on the not-landed path")
    else:
        from core.run_protocol import checks as check_registry
        expected_ids = {spec.check_id for spec in check_registry.stage_checks(
            run["profile"], stage
        )}
        if set(check_ids) != expected_ids:
            raise LifecycleError(
                f"stage_attempt check_ids must equal registry roster {sorted(expected_ids)}"
            )
    run["stage_attempts"][stage] = expected
    run["open_attempt"] = {
        "event_id": event["event_id"], "stage": stage, "attempt": expected,
        "check_ids": tuple(check_ids), "results": {}, "details": {},
    }


def _set_pending(run: dict[str, Any], reason: str) -> None:
    """The not-landed outcome is written once: the first cause is the reason."""
    if run.get("pending_landing") is None:
        run["pending_landing"] = {"landing": "not_landed", "reason": reason}
    if run.get("reason") is None:
        run["reason"] = run["pending_landing"]["reason"]


def _failed_stage(run: dict[str, Any], event: Mapping[str, Any], failed: set[str]) -> None:
    attempt = run["open_attempt"]
    stage, number = attempt["stage"], attempt["attempt"]
    details = attempt.get("details") or {}
    run["open_attempt"] = None
    # a guard recorded "not evaluated" did not fail on its own: classify by the rest
    evaluated = {cid for cid in failed if not details.get(cid, "").startswith("not evaluated")}
    failed = evaluated or failed
    if stage == "deliver":
        if failed <= RF_CHECKS and number >= 3 and run.get("reflect") is None:
            run["reflect"] = "degraded"    # lessons drop to zero; DELIVER continues
        return                             # a DV-1 attempt is simply retried
    if stage == "land":
        # the attempt is closed: its intent and render belong to it
        run["last_land_intent"], run["last_report_render"] = None, None
        if failed & {"LG-3", "DV-1"}:
            _set_pending(run, "report_mismatch")
        elif failed & {"LG-1", "LG-4"}:
            if number >= 3:
                conflict = str(details.get("LG-4", "")).startswith("merge_conflict")
                _set_pending(run, "land_conflict" if conflict else "final_stale")
        elif "LG-2" in failed:
            incomplete = "incomplete" in str(details.get("LG-2", ""))
            _set_pending(run, "tests_incomplete" if incomplete else "tests_red")
        elif "LG-5" in failed:
            _set_pending(run, "validation_failed")
        return
    if number - run["attempt_base"].get(stage, 0) >= 3:
        run["stages"][run["stage"]] = "degraded"
        _set_pending(run, "checks_exhausted")
        run["stage"] = "deliver"
        run["stages"]["deliver"] = "open"
        _schedule(run, event, "active")


def _check(run: dict[str, Any], event: Mapping[str, Any]) -> None:
    attempt = run.get("open_attempt")
    if attempt is None or event["payload"]["stage_attempt_event_id"] != attempt["event_id"]:
        raise LifecycleError("check does not belong to the open stage_attempt")
    check_id = event["payload"]["check_id"]
    if check_id not in attempt["check_ids"] or check_id in attempt["results"]:
        raise LifecycleError("check is undeclared or duplicated")
    attempt["results"][check_id] = event["payload"]["passed"]
    attempt["details"][check_id] = str(event["payload"].get("detail") or "")
    if len(attempt["results"]) == len(attempt["check_ids"]):
        failed = {cid for cid, passed in attempt["results"].items() if passed is not True}
        if failed:
            _failed_stage(run, event, failed)


def _complete(run: dict[str, Any], event: Mapping[str, Any]) -> None:
    attempt = run.get("open_attempt")
    if attempt is None or event["payload"]["stage_attempt_event_id"] != attempt["event_id"]:
        raise LifecycleError("stage_complete does not reference the open attempt")
    if attempt["stage"] != run["stage"]:
        raise LifecycleError("stage_complete is not for the current stage")
    if set(attempt["results"]) != set(attempt["check_ids"]) or not all(
        value is True for value in attempt["results"].values()
    ):
        raise LifecycleError("stage_complete requires all declared checks to pass")
    stage = run["stage"]
    if stage == "test" and event["payload"].get("final_verdict") not in (
        "green", "red", "incomplete"
    ):
        raise LifecycleError("TEST requires a terminal final verdict")
    run["open_attempt"] = None
    if stage == "deliver":
        if frozenset(attempt["check_ids"]) != REPORT_ONLY:
            raise LifecycleError("DELIVER stage_complete is the not-landed path's DV-1 attempt")
        outcome = run.get("pending_landing")
        if run["profile"] == "research" and outcome is None:
            outcome = {"landing": "no_code", "reason": None}
        if not outcome or outcome["landing"] not in ("not_landed", "no_code"):
            raise LifecycleError("DELIVER stage_complete is only for not_landed/no_code")
        run["stages"]["deliver"] = "completed"
        run["landing"], run["reason"] = outcome["landing"], outcome.get("reason")
        run["stage"] = "reflect"
        run["stages"]["reflect"] = "open"
        _schedule(run, event, "reflecting")
        return
    _open_next(run)
    _schedule(run, event, "active")


def _decision(run: dict[str, Any], event: Mapping[str, Any]) -> None:
    if run["status"] != "active" or run["stage"] not in ("evaluate", "think", "plan"):
        raise LifecycleError("decision is legal only in active EVALUATE/THINK/PLAN")
    if run["human_decisions"] >= run["decision_cap"]:
        raise LifecycleError("decision cap reached")
    run["pending_decision"] = event["payload"]["request_id"]
    _schedule(run, event, "awaiting_human")


def _human_answer(run: dict[str, Any], event: Mapping[str, Any]) -> None:
    if run["status"] != "awaiting_human" \
            or event["payload"]["request_id"] != run["pending_decision"]:
        raise LifecycleError("human_answer is not for the current decision")
    run["pending_decision"] = None
    run["human_decisions"] += 1
    # attempt numbers only grow (they are part of event identity); the breaker
    # counts the attempts made since the person answered
    run["attempt_base"][run["stage"]] = run["stage_attempts"].get(run["stage"], 0)
    _schedule(run, event, "active")


def _pause(run: dict[str, Any], event: Mapping[str, Any]) -> None:
    if run["status"] == "awaiting_human" and event["kind"] in ("session_lost", "user_stop"):
        return  # still waiting for the same human answer; any later session re-asks it
    if run["status"] not in ("active", "awaiting_human"):
        raise LifecycleError(f"{event['kind']} cannot pause {run['status']}")
    run["reason"] = event["kind"]
    _schedule(run, event, "paused")


def _resume(run: dict[str, Any], event: Mapping[str, Any]) -> None:
    if run["status"] != "paused":
        raise LifecycleError("resume requires paused")
    if run["reason"] == "worktree_unavailable" and event["payload"]["worktree_ready"] is not True:
        return
    run["reason"] = None
    _schedule(run, event, "active")


def _land(run: dict[str, Any], event: Mapping[str, Any]) -> None:
    payload, landing = event["payload"], event["payload"]["landing"]
    if landing not in LANDINGS:
        raise LifecycleError("land event has invalid landing")
    if landing == "landed":
        if run["status"] != "active" or run["stage"] != "deliver":
            raise LifecycleError("normal land requires active DELIVER")
        if run.get("pending_landing") is not None:
            raise LifecycleError("a run on the not-landed path cannot land "
                                 f"({run['pending_landing']['reason']})")
        attempt = run.get("open_attempt")
        if attempt is None or attempt["stage"] != "land" \
                or set(attempt["check_ids"]) != LAND_CHECKS \
                or payload.get("stage_attempt_event_id") != attempt["event_id"] \
                or set(attempt["results"]) != set(attempt["check_ids"]) \
                or not all(value is True for value in attempt["results"].values()):
            raise LifecycleError("normal land requires a complete passing land attempt")
        intent, render = run.get("last_land_intent"), run.get("last_report_render")
        if intent is None or render is None \
                or intent["stage_attempt_event_id"] != attempt["event_id"] \
                or render["stage_attempt_event_id"] != attempt["event_id"] \
                or not _commit_sha(payload.get("commit")):
            raise LifecycleError("normal land requires current land_intent, REPORT render and commit")
        run["open_attempt"] = None
        run["stages"]["deliver"] = "completed"
        run["landing"], run["reason"] = "landed", None
        run["stage"] = "reflect"
        run["stages"]["reflect"] = "open"
        _schedule(run, event, "reflecting")
        return
    if landing == "human_override":
        if not _commit_sha(payload.get("commit")):
            raise LifecycleError("human override requires a landed commit SHA")
        auth_id = payload.get("auth_consumed_event_id")
        grant = run["auth_consumed"].get(auth_id)
        if grant is None or grant.get("action") not in ("land", "land_skip_tests"):
            raise LifecycleError("human override requires a consumed land authorization")
        request = run["land_requests"].get(grant["request_id"])
        expected_guards = LG_CHECKS if grant["action"] == "land" else SKIP_LAND_CHECKS
        if request is None or set(request.get("guard_check_ids") or []) != expected_guards:
            raise LifecycleError("human override authorization lacks action-specific guards")
        allowed = run["status"] in ("active", "paused", "awaiting_human", "abandoned") \
            or (run["status"] == "reflecting" and run.get("landing") == "not_landed") \
            or (run["status"] == "completed" and run.get("landing") == "not_landed")
        if not allowed:
            raise LifecycleError("human override is illegal from this status/landing")
        old = run["status"]
        run["landing"], run["reason"] = "human_override", None
        run["human_decisions"] += 1
        if old == "completed":
            _schedule(run, event, "completed")
        elif old == "reflecting":
            _schedule(run, event, "reflecting")
        else:
            run["stage"] = "reflect"
            run["stages"]["reflect"] = "open"
            _schedule(run, event, "reflecting")
        return
    if landing == "not_landed":
        if run["status"] != "active" or run["stage"] != "deliver" \
                or payload.get("reason") not in NOT_LANDED_REASONS \
                or payload.get("commit") is not None:
            raise LifecycleError("not_landed requires active DELIVER, reason, and no commit")
        run["pending_landing"] = {"landing": "not_landed", "reason": payload["reason"]}
        return
    raise LifecycleError("no_code is derived only by research DELIVER")


def _restore(run: dict[str, Any], event: Mapping[str, Any]) -> None:
    was_completed_not_landed = (
        run["status"] == "completed" and run.get("landing") == "not_landed"
    )
    allowed = run["status"] == "abandoned" or was_completed_not_landed
    if not allowed or event["payload"]["snapshot_exists"] is not True:
        raise LifecycleError("restore requires abandoned/completed(not_landed) and a snapshot")
    if was_completed_not_landed:
        run["stage"] = "deliver"
        run["stages"]["deliver"] = "open"
        run["stages"]["reflect"] = "pending"
        run["landing"] = None
        run["pending_landing"] = None
        run["reflect"] = None
    run["reason"] = None
    _schedule(run, event, "paused")


def _authorization(run: dict[str, Any], event: Mapping[str, Any]) -> None:
    payload, kind = event["payload"], event["kind"]
    if kind == "land_request":
        request_id = payload["request_id"]
        if request_id in run["land_requests"]:
            raise LifecycleError("land_request id already exists")
        from core.run_protocol import auth as _auth
        request = payload["request"]
        try:
            rebuilt = _auth.make_request(
                request_id=request.get("request_id"), session_id=request.get("session_id"),
                run_id=request.get("run_id"), action=request.get("action"),
                bindings=(request.get("bindings") or {}).get("repos")
                if request.get("action") in ("land", "land_skip_tests")
                else request.get("bindings"), note=request.get("note"))
        except (_auth.AuthorizationError, AttributeError, TypeError) as exc:
            raise LifecycleError(f"land_request carries an invalid request: {exc}") from exc
        if rebuilt != request or request["request_id"] != request_id \
                or request["run_id"] != run["run_id"] or request["action"] != payload["action"] \
                or request["binding_digest"] != payload["binding_digest"]:
            raise LifecycleError("land_request's request does not match its own bindings")
        if payload["action"] in ("land", "land_skip_tests"):
            expected = LG_CHECKS if payload["action"] == "land" else SKIP_LAND_CHECKS
            attempt = run.get("open_attempt")
            if attempt is None or attempt["stage"] != "land" \
                    or set(attempt["check_ids"]) != expected \
                    or set(attempt["results"]) != expected \
                    or not all(value is True for value in attempt["results"].values()) \
                    or payload.get("guard_attempt_event_id") != attempt["event_id"] \
                    or set(payload.get("guard_check_ids") or []) != expected:
                raise LifecycleError("land_request is not bound to its passing action guards")
        run["land_requests"][request_id] = copy.deepcopy(payload)
        return
    if kind == "auth":
        request = run["land_requests"].get(payload["request_id"])
        if request is None or payload["run_id"] != run["run_id"] \
                or payload["action"] != request["action"] \
                or payload["binding_digest"] != request["binding_digest"]:
            raise LifecycleError("auth does not match its land_request")
        run["auth_grants"][event["event_id"]] = copy.deepcopy(payload)
        return
    grant = run["auth_grants"].get(payload["auth_event_id"])
    if grant is None or any(payload[key] != grant[key] for key in (
        "request_id", "session_id", "run_id", "action", "binding_digest"
    )):
        raise LifecycleError("auth_consumed does not match its auth grant")
    if any(row.get("auth_event_id") == payload["auth_event_id"]
           for row in run["auth_consumed"].values()):
        raise LifecycleError("auth grant was already consumed")
    run["auth_consumed"][event["event_id"]] = copy.deepcopy(payload)


def _apply(run: dict[str, Any], event: Mapping[str, Any]) -> None:
    kind = event["kind"]
    if run.get("expected_transition") is not None:
        if kind != "transition":
            raise LifecycleError("state-changing trigger must be followed by transition")
        _transition(run, event)
        return
    if kind == "transition":
        _transition(run, event)
    elif kind in IGNORED:
        return
    elif kind == "stage_attempt":
        stage = event.get("stage")
        human_prepare = stage == "land" and (
            run["status"] in ("paused", "awaiting_human", "abandoned")
            or (run["status"] in ("reflecting", "completed")
                and run.get("landing") == "not_landed")
        )
        if run["status"] != "active" and not human_prepare:
            raise LifecycleError("stage_attempt requires active or run-land eligible state")
        _attempt(run, event)
    elif kind == "check":
        _check(run, event)
    elif kind == "stage_complete":
        if run["status"] != "active":
            raise LifecycleError("stage_complete requires active")
        _complete(run, event)
    elif kind == "decision":
        _decision(run, event)
    elif kind == "human_answer":
        _human_answer(run, event)
    elif kind in ("session_lost", "user_stop", "worktree_unavailable"):
        _pause(run, event)
    elif kind == "resume":
        _resume(run, event)
    elif kind == "gate0_exhausted":
        if run["status"] != "active" or run["stage"] != "evaluate" \
                or run["profile"] not in ("full", "bugfix", "goal") \
                or event["payload"]["reason"] not in ("framing_rejected", "already_satisfied"):
            raise LifecycleError("gate0_exhausted is illegal here")
        run["stages"]["evaluate"] = "degraded"
        run["stage"] = "deliver"
        run["stages"]["deliver"] = "open"
        _set_pending(run, event["payload"]["reason"])
        _schedule(run, event, "active")
    elif kind == "land_intent":
        attempt = run.get("open_attempt")
        human = bool(event["payload"].get("request_id"))
        eligible = run["status"] in ("active", "paused", "awaiting_human", "abandoned") \
            or (run["status"] in ("reflecting", "completed")
                and run.get("landing") == "not_landed")
        normal_ready = (not human and run["status"] == "active" and run["stage"] == "deliver"
                        and attempt is not None and attempt["stage"] == "land"
                        and run.get("pending_landing") is None
                        and set(attempt["results"]) == {"LG-1", "LG-2", "LG-5"}
                        and all(value is True for value in attempt["results"].values())
                        and event["payload"].get("stage_attempt_event_id") == attempt["event_id"])
        human_ready = (human and eligible and attempt is not None and attempt["stage"] == "land"
                       and (not attempt["results"] or all(
                           value is True for value in attempt["results"].values())))
        if not (normal_ready or human_ready):
            raise LifecycleError("land_intent is not bound to current land attempt or passing LG-1/LG-2/LG-5")
        run["last_land_intent"] = {"event_id": event["event_id"],
                                   "stage_attempt_event_id": attempt["event_id"],
                                   "request_id": event["payload"].get("request_id")}
        run["last_report_render"] = None
    elif kind in ("land_request", "auth", "auth_consumed"):
        _authorization(run, event)
    elif kind == "land":
        _land(run, event)
    elif kind == "abandon":
        if run["status"] in TERMINAL:
            raise LifecycleError("cannot abandon a terminal run")
        run["reason"] = event["payload"]["reason"]
        _schedule(run, event, "abandoned")
    elif kind == "restore":
        _restore(run, event)
    elif kind == "render":
        attempt = run.get("open_attempt")
        intent = run.get("last_land_intent")
        if event["payload"]["document"] == "REPORT":
            passed_rf = (attempt is not None and attempt["stage"] == "deliver"
                         and set(attempt["results"]) == set(attempt["check_ids"])
                         and all(value is True for value in attempt["results"].values()))
            terminal_report = (run["status"] == "active" and run["stage"] == "deliver"
                               and (run.get("pending_landing") is not None
                                    or run["profile"] == "research")
                               and (attempt is None or passed_rf) and intent is None)
            human = bool(intent and intent.get("request_id"))
            eligible = run["status"] in ("active", "paused", "awaiting_human", "abandoned") \
                or (run["status"] in ("reflecting", "completed")
                    and run.get("landing") == "not_landed")
            if not terminal_report and (attempt is None or attempt["stage"] != "land" or intent is None \
                    or intent["stage_attempt_event_id"] != attempt["event_id"] \
                    or (not human and (run["status"] != "active" or run["stage"] != "deliver")) \
                    or (human and not eligible) \
                    or event.get("stage") != "deliver" \
                    or event.get("attempt") != attempt["attempt"]):
                raise LifecycleError("REPORT render is not bound to current land_intent")
            run["last_report_render"] = {
                "event_id": event["event_id"],
                "stage_attempt_event_id": attempt["event_id"] if attempt else None,
            }
    elif kind == "infrastructure_failed":
        if run["status"] in TERMINAL:
            raise LifecycleError("terminal run cannot fail")
        run["reason"] = event["payload"]["reason"]
        _schedule(run, event, "failed")
    elif kind in ("reflect_complete", "reflect_exhausted"):
        if run["status"] != "reflecting":
            raise LifecycleError("reflect completion requires reflecting")
        degraded = kind == "reflect_exhausted" or run.get("reflect") == "degraded"
        run["stages"]["reflect"] = "degraded" if degraded else "completed"
        run["reflect"] = "degraded" if degraded else "ok"   # degraded is never undone
        _schedule(run, event, "completed")
    elif kind == "run_create":
        raise LifecycleError("run_create must be the first event")
    else:
        raise LifecycleError(f"unhandled lifecycle event {kind}")


def fold(events: list[Mapping[str, Any]], authority: ev.EventAuthority, *,
         allow_pending_transition: bool = False) -> dict[str, Any]:
    """Derive a run projection from a complete validated hash chain."""
    errors = ev.verify_chain(list(events), authority)
    if errors:
        raise LifecycleError(f"invalid event chain: {errors[0]}")
    if not events or events[0]["kind"] != "run_create":
        raise LifecycleError("the first event must be run_create")
    run = _initial(events[0])
    for event in events[1:]:
        if event["run_id"] != run["run_id"]:
            raise LifecycleError("event belongs to another run")
        _apply(run, event)
    if run.get("expected_transition") is not None and not allow_pending_transition:
        raise LifecycleError("stream ends before required transition event")
    return run
