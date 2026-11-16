"""A PLAN the schema refuses: what DS-6 accepts, what it says, and where the run goes.

A run once had its PLAN refused three times by DS-6 for flows that named the failure
with "failing" and "fails" (the matcher took only fail / failure / failed). The third
refusal then ended the run not landed without BUILD, REVIEW and TEST, and the change
could not land. These tests hold:

- DS-6 takes any form of the failure words the PLAN card names, in a step's
  condition, and its refusal says what a step must look like;
- a third refused PLAN degrades PLAN and opens BUILD, so the change still goes through
  REVIEW and TEST before landing (the audit says the PLAN was refused);
- a log written before that rule (no ``on_exhausted`` on the attempt) still folds to
  not_landed(checks_exhausted), and no other stage may carry the rule;
- LG-1 says when no REVIEW of the run completed at all.
"""
from __future__ import annotations

import copy
import json
from types import SimpleNamespace

import pytest

from core.run_protocol import checks, lifecycle as lc, schemas
from tests.pipeline_v1_rig import documents
from tests.test_run_protocol_lifecycle import Stream
from tests.pipeline_v1_e2e_common import _main, rig  # noqa: F401  (rig is a fixture)

pytestmark = pytest.mark.timeout(300)

# the three flows the refused run submitted, verbatim
FLOW_1 = ("git show a1d2b5689:backend/tests/test_cjk_term_map.py -> file restored; "
          "comment reworded; test_recall_filter run; grep finds term-map wording -> "
          "error, fix before commit")
FLOW_2 = ("restore test file from a1d2b5689 -> reword comment -> run test_recall_filter; "
          "failing test or a grep match for term-map wording -> error, fixed before "
          "commit; clean -> commit")
FLOW_3 = ("restore test file from a1d2b5689 -> reword comment -> run test_recall_filter; "
          "test fails or grep finds term-map wording -> error reported, fixed before "
          "commit; clean -> commit")


def _flow_issues(flow: str) -> list[schemas.SchemaIssue]:
    docs = documents("bugfix")
    design = copy.deepcopy(docs["plan"])
    design["design"]["flow"] = flow
    return [issue for issue in schemas.validate_contract(
        docs["evaluate"], design, requirement_variant="full", design_variant="full",
        profile="bugfix") if issue.check_id == "DS-6" and issue.path == "design.flow"]


# ── DS-6: the failure step ─────────────────────────────────────────────────

@pytest.mark.parametrize("condition", [
    "test fails", "failing check", "on failure", "build failed", "errors found",
    "an error", "input rejected", "it rejects the token", "token invalidated",
    "invalid input", "access denied", "the gate denies it", "构建失败", "参数无效"])
def test_ds6_takes_every_form_of_a_failure_word_in_the_condition(condition):
    flow = f"request -> validate; {condition} -> stop and report; ok -> result"
    assert schemas._has_explicit_failure_edge(flow), flow
    assert _flow_issues(flow) == []


@pytest.mark.parametrize("flow", [FLOW_2, FLOW_3])
def test_the_refused_runs_second_and_third_flows_pass(flow):
    assert _flow_issues(flow) == []


def test_a_failure_named_only_in_the_outcome_gets_a_message_that_says_what_to_change():
    issues = _flow_issues(FLOW_1)
    assert [issue.message for issue in issues] == [schemas.FAILURE_EDGE_MESSAGE]
    message = schemas.FAILURE_EDGE_MESSAGE
    for words in ("';'- or line-separated", "'<failure condition> -> <outcome>'",
                  "left of its first '->'", "fails, failing", "only in the outcome",
                  "e.g. 'tests fail -> "):
        assert words in message, words
    # the message's own example is a flow the check takes
    example = message.split("e.g. '", 1)[1].rstrip("'")
    assert schemas._has_explicit_failure_edge(example), example


@pytest.mark.parametrize("flow", ["call -> validate; valid -> sum",
                                  # words that only start like a failure word
                                  "load -> save; failover node -> standby",
                                  "a -> b; invalidate cache -> rebuild"])
def test_a_flow_without_a_failure_step_is_still_refused(flow):
    assert not schemas._has_explicit_failure_edge(flow)
    assert len(_flow_issues(flow)) == 1


# ── the fold: a third refused PLAN ─────────────────────────────────────────

def _plan_attempt(stream: Stream, *, rule: bool) -> None:
    """One PLAN attempt whose DS-6 fails, as documents.complete_stage records it
    (*rule*: with ``on_exhausted``, as the engine writes it since the rule)."""
    roster = stream.roster("plan")
    number = stream.projection()["stage_attempts"].get("plan", 0) + 1
    payload = {"check_ids": roster, **({"on_exhausted": "continue"} if rule else {})}
    opened = stream.add("stage_attempt", payload, stage="plan", attempt=number,
                        transition=False)
    for check_id in roster:
        stream.add("check", {"stage_attempt_event_id": opened["event_id"],
                             "check_id": check_id, "passed": check_id != "DS-6",
                             "detail": "deterministic result"})


def test_a_third_refused_plan_opens_build_and_sets_no_not_landed_outcome():
    stream = Stream("bugfix")
    stream.to_stage("plan")
    for _ in range(2):
        _plan_attempt(stream, rule=True)
    assert stream.projection()["stage"] == "plan"
    _plan_attempt(stream, rule=True)
    run = stream.projection()
    assert run["status"] == "active" and run["stage"] == "build", run
    assert run["stages"]["plan"] == "degraded" and run["stages"]["build"] == "open"
    assert run["pending_landing"] is None and run["reason"] is None
    # BUILD, REVIEW and TEST follow as in any run
    for stage in ("build", "review", "test"):
        assert stream.projection()["stage"] == stage
        stream.complete_stage()
    assert stream.projection()["stage"] == "deliver"
    assert stream.projection()["pending_landing"] is None


def test_a_log_written_before_the_rule_still_ends_not_landed():
    stream = Stream("bugfix")
    stream.to_stage("plan")
    for _ in range(3):
        _plan_attempt(stream, rule=False)
    run = stream.projection()
    assert run["stage"] == "deliver" and run["stages"]["plan"] == "degraded"
    assert run["pending_landing"]["reason"] == "checks_exhausted"
    assert run["stages"]["review"] == "pending" and run["stages"]["test"] == "pending"


def test_no_other_stage_may_carry_the_rule():
    stream = Stream("bugfix")
    stream.add("stage_attempt", {"check_ids": stream.roster("evaluate"),
                                 "on_exhausted": "continue"},
               stage="evaluate", attempt=1, transition=False)
    with pytest.raises(lc.LifecycleError, match="on_exhausted"):
        stream.projection()


# ── LG-1: a change no REVIEW saw ───────────────────────────────────────────

def test_lg1_says_no_review_completed_when_none_did():
    context = SimpleNamespace(run={"patch_ids": {"repo": "p1"}, "reviewed_patches": []})
    problems = checks._unreviewed_landing(context, ["repo"])
    assert problems == [checks.NEVER_REVIEWED + ": no REVIEW of this run completed; "
                        "it cannot land"]
    assert "changed after REVIEW" not in problems[0]
    context = SimpleNamespace(run={"patch_ids": {"repo": "p2"},
                                   "reviewed_patches": [{"repo": "p1"}]})
    assert "changed after REVIEW" in checks._unreviewed_landing(context, ["repo"])[0]


# ── end to end: the run lands only through REVIEW and TEST ─────────────────

def test_a_run_whose_plan_keeps_failing_lands_only_through_review_and_test(rig):
    from core.run_protocol.audit import PLAN_REFUSED
    from tests.pipeline_v1_rig import _git
    r = rig("bugfix")
    for key, value in (("user.name", "Swarm"), ("user.email", "swarm@swarmai.dev")):
        _git(r.repo, "config", key, value)      # the landing commits; a bare host has none
    r.create()
    base = _main(r)
    r.drive(until="plan")
    r.edit(r.CHANGE)
    refused = copy.deepcopy(r.documents()["plan"])
    refused["design"]["flow"] = "call -> validate; valid -> sum"
    outs = [r.v1("stage-complete", "--artifact", r.artifact(f"design-{i}", refused))
            for i in range(3)]
    assert all(out.get("ok") is False and schemas.FAILURE_EDGE_MESSAGE in out["error"]
               for out in outs), outs
    assert "goes on to BUILD" in outs[-1].get("note", ""), outs[-1]
    assert outs[-1]["next"]["stage"] == "build", outs[-1]["next"]
    state = r.drive()
    assert state["status"] == "completed" and state["landing"] == "landed", state
    assert state["stages"]["plan"] == "degraded"
    for stage in ("build", "review", "test", "deliver"):
        assert state["stages"][stage] == "completed", state["stages"]
    kinds = [(e["kind"], e.get("stage")) for e in r.events()]
    assert kinds.index(("stage_complete", "review")) < kinds.index(("stage_complete", "test")) \
        < next(i for i, k in enumerate(kinds) if k[0] == "land")
    assert _main(r) != base
    # the engine writes the rule on PLAN's attempts only
    rules = {(e.get("stage"), e["payload"].get("on_exhausted")) for e in r.events()
             if e["kind"] == "stage_attempt"}
    assert ("plan", "continue") in rules and ("plan", None) not in rules, rules
    assert all(rule is None for stage, rule in rules if stage != "plan"), rules
    audit = json.loads((r.engine().run_dir / "run_audit.json").read_text())
    assert PLAN_REFUSED in audit["summary"]["quality"]["open"], audit["summary"]
    assert audit["summary"]["quality"]["verdict"] == "WARN"
