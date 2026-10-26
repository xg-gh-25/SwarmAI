"""Tests Pipeline v1 run audit and shared report/card rendering.

Methodology: synthetic validated facts; audit is a projection and performs no I/O.
"""
from __future__ import annotations

from core.run_protocol.audit import build, render


def _metric(name, value, producer="cli"):
    return {"kind": "metric", "producer": producer, "at": "2026-10-22T00:00:01+00:00",
            "payload": {"name": name, "value": value}}


MEASURED = [_metric("input_tokens", 100), _metric("output_tokens", 20),
            _metric("accounted_calls", 3)]


def _facts(events=None, landing="landed", push=True):
    projection = {"run_id": "run_x", "status": "completed", "landing": landing,
                  "reason": None, "human_decisions": 0}
    contract = {"run_id": "run_x", "mode": "isolated", "expected_repos": ["repo"],
                "budget_seconds": 100, "budget_tokens": 1000}
    events = events or []
    # the agent's TEST report (S6-1): Quality counts a repo verified when its report
    # passes commands and fails none
    report = {"kind": "validation_report", "at": "2026-10-22T00:00:20+00:00",
              "payload": {"stage": "test", "repo": "repo", "code_tree": "1" * 40,
                          "n_pass": 2, "n_fail": 0, "n_not_run": 0}}
    events = [{"kind": "run_create", "at": "2026-10-22T00:00:00+00:00", "payload": {}},
              *events, report, {"kind": "land", "at": "2026-10-22T00:00:30+00:00", "payload": {}}]
    return build(run_id="run_x", projection=projection, events=events, 
                 repos_isolated=1,
                 findings=[], contract=contract,
                 push={"push_ready": push, "reasons": {} if push else {"RV-2": "open"}},
                 audit_through="COMPLETED", commits={"repo": "a" * 40})


def test_five_dimensions_and_completed_card_share_numbers():
    audit = _facts(events=list(MEASURED))
    assert set(audit["summary"]) == {"isolation", "speed", "quality", "autonomy", "token_cost"}
    assert all(row["verdict"] == "PASS" for row in audit["summary"].values())
    card = audit["card"]
    report = render(audit, fmt="report")
    for name in ("Run Isolation", "Speed", "Quality", "Autonomy", "Token Cost"):
        assert name in card and name in report
    assert "PUSH READY" in card and "deploy" not in report.lower()


def test_token_calls_without_measurement_block_and_manual_landing_warns():
    events = [{"kind": "handout", "at": "2026-10-22T00:00:00+00:00", "payload": {}}]
    audit = _facts(events=events, landing="human_override")
    assert audit["summary"]["token_cost"]["verdict"] == "BLOCK"
    assert audit["summary"]["quality"]["verdict"] == "WARN"


def test_contamination_is_isolation_block_and_push_reason_renders():
    events = [{"kind": "integrity", "at": "2026-10-22T00:00:00+00:00",
               "payload": {"conflict_type": "cross_run_cache"}}]
    audit = _facts(events=events, push=False)
    assert audit["summary"]["isolation"]["verdict"] == "BLOCK"
    assert "RV-2: open" in render(audit, fmt="report")


def test_measured_review_tokens_are_accounted_and_pass_budget():
    events = [{"kind":"handout","at":"2026-10-22T00:00:00+00:00","payload":{}}, *MEASURED,
              _metric("input_tokens", 5, "runner")]    # the expert's own usage record
    audit = _facts(events=events)
    assert audit["summary"]["token_cost"]["verdict"] == "PASS"
    assert audit["summary"]["token_cost"]["total_tokens"] == 120


# ── D3: one measurement replaces the previous; experts are not added twice ──

def test_a_later_measurement_replaces_and_experts_are_not_added_twice():
    events = [*MEASURED,
              _metric("input_tokens", 40, "runner"), _metric("output_tokens", 5, "runner"),
              _metric("accounted_calls", 1, "runner"),
              _metric("input_tokens", 150), _metric("output_tokens", 30),
              _metric("accounted_calls", 4)]
    events.insert(0, _ANSWER)
    token = _facts(events=events)["summary"]["token_cost"]
    assert token["total_tokens"] == 180
    assert token["accounted_calls"] == 2 == token["total_calls"]   # main agent + 1 expert


_ANSWER = {"kind": "handout", "at": "2026-10-22T00:00:01+00:00", "payload": {}}


def test_without_a_measurement_the_main_agent_is_unaccounted():
    events = [_ANSWER, _metric("input_tokens", 40, "runner"), _metric("output_tokens", 5, "runner"),
              _metric("accounted_calls", 1, "runner")]
    token = _facts(events=events)["summary"]["token_cost"]
    assert token["verdict"] == "WARN"
    assert token["accounted_calls"] == 1 < token["total_calls"] == 2


def test_partial_metering_cannot_read_as_complete():
    """Conformance F7: total_calls is counted from events, never set to what was
    measured. Three experts spawned (answered or not) with no usage record: 1 of 4
    sessions accounted."""
    events = [_ANSWER, _ANSWER, _ANSWER, *MEASURED]
    token = _facts(events=events)["summary"]["token_cost"]
    assert token["accounted_calls"] == 1 and token["total_calls"] == 4
    assert token["verdict"] == "WARN"
    nothing = _facts(events=[])["summary"]["token_cost"]
    assert nothing["verdict"] == "BLOCK" and nothing["total_tokens"] is None


def test_accept_finding_is_a_human_decision_and_approvals_are_counted():
    events = [*MEASURED,
              {"kind": "auth_consumed", "at": "2026-10-22T00:00:02+00:00",
               "payload": {"action": "accept_finding"}},
              _metric("required_approval", 1, "hook")]
    autonomy = _facts(events=events)["summary"]["autonomy"]
    assert autonomy["human_decisions"] == 1 and autonomy["verdict"] == "WARN"
    assert autonomy["required_approvals"] == 1


# ── D5: REPORT and card follow §B8 / §E9 ────────────────────────────────────

def test_report_has_one_conclusion_no_guard_line_and_the_card_its_label():
    audit = _facts(events=list(MEASURED))
    report = render(audit, fmt="report")
    assert "Landed (" in report and "Landing guards" not in report
    assert "Full machine-readable audit is stored with this run." in report
    assert audit["card"].rstrip().endswith("Final audit through COMPLETED")
    assert "% budget" in report


def test_missing_time_is_null_and_quality_na_shows_the_reason():
    projection = {"run_id": "run_x", "status": "completed", "landing": "not_landed",
                  "reason": "framing_rejected", "human_decisions": 0}
    contract = {"run_id": "run_x", "mode": "isolated", "expected_repos": ["repo"]}
    audit = build(run_id="run_x", projection=projection, events=[], findings=[],
                  contract=contract, push={"push_ready": False, "reasons": {}},
                  audit_through="COMPLETED", repos_isolated=None,
                  retained=[{"repo_name": "repo", "ledger": "/l/run_x/repo"}])
    assert audit["summary"]["speed"]["wall_seconds"] is None
    assert "Quality        N/A · framing_rejected" in audit["card"]
    assert audit["summary"]["isolation"]["verdict"] == "WARN"      # not measured
    report = render(audit, fmt="report")
    assert "Not landed:" in report and "/l/run_x/repo" in report and "time incomplete" in report


def test_a_high_security_policy_finding_is_listed_first():
    finding = {"canonical_id": "CF-aaaaaaaa", "kind": "security_policy", "severity": "HIGH",
               "status": "open", "file": "a.py", "line": 1, "claim": "c", "expert": "security"}
    projection = {"run_id": "run_x", "status": "completed", "landing": "landed",
                  "reason": None, "human_decisions": 0}
    contract = {"run_id": "run_x", "mode": "isolated", "expected_repos": ["repo"]}
    audit = build(run_id="run_x", projection=projection, events=[], 
                  findings=[finding], contract=contract,
                  push={"push_ready": False, "reasons": {"MD-1": "a", "RV-2": "b"}},
                  audit_through="DELIVER", repos_isolated=1)
    report = render(audit, fmt="report")
    section = report.split("## Look at these before you push")[1]
    assert section.strip().startswith("- CF-aaaaaaaa"), section


def test_quality_counts_the_agents_test_reports_and_reports_them():
    audit = _facts()
    q = audit["summary"]["quality"]
    assert (q["verification_passed"], q["verification_required"]) == (1, 1)
    report = render(audit, fmt="report")
    # no review expert answered in these events: the reports are the agent's word alone
    assert "reported by the agent only" in report and "2 pass, 0 fail" in report

