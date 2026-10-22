"""Tests Pipeline v1 run audit and shared report/card rendering.

Methodology: synthetic validated facts; audit is a projection and performs no I/O.
"""
from __future__ import annotations

from core.run_protocol.audit import build, render


def _facts(events=None, landing="landed", push=True):
    projection = {"run_id": "run_x", "status": "completed", "landing": landing,
                  "reason": None, "human_decisions": 0}
    contract = {"run_id": "run_x", "mode": "isolated", "expected_repos": ["repo"],
                "budget_seconds": 100, "budget_tokens": 1000}
    final = {"repos": {"repo": {"tests": {"verdict": "green"},
                                  "validations": [{"id": "lint", "verdict": "pass"}]}}}
    return build(run_id="run_x", projection=projection, events=events or [], final=final,
                 findings=[], contract=contract,
                 push={"push_ready": push, "reasons": {} if push else {"RV-2": "open"}},
                 audit_through="COMPLETED", commits={"repo": "a" * 40})


def test_five_dimensions_and_completed_card_share_numbers():
    audit = _facts()
    assert set(audit["summary"]) == {"isolation", "speed", "quality", "autonomy", "token_cost"}
    assert all(row["verdict"] == "PASS" for row in audit["summary"].values())
    card = audit["card"]
    report = render(audit, fmt="report")
    for name in ("Run Isolation", "Speed", "Quality", "Autonomy", "Token Cost"):
        assert name in card and name in report
    assert "PUSH READY" in card and "Push 前还需要" in report


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
    events = [
        {"kind":"handout","at":"2026-10-22T00:00:00+00:00","payload":{}},
        {"kind":"metric","at":"2026-10-22T00:00:01+00:00","payload":{"name":"input_tokens","value":100}},
        {"kind":"metric","at":"2026-10-22T00:00:01+00:00","payload":{"name":"output_tokens","value":20}},
        {"kind":"metric","at":"2026-10-22T00:00:01+00:00","payload":{"name":"accounted_calls","value":1}},
    ]
    audit = _facts(events=events)
    assert audit["summary"]["token_cost"]["verdict"] == "PASS"
    assert audit["summary"]["token_cost"]["total_tokens"] == 120
