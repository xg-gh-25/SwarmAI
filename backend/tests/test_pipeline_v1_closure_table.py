"""The finding closure table (design 2026-10-26-pipeline-review-closure-and-visibility §2.3).

One table in ``findings.py`` says, for each finding kind, which evidence closes it and the
state it leaves; the R2 words a finding may get follow from its state; one function says
whether a finding counts as closed. The ledger, RV-2's push effect and the audit read it.

What is tested:
- every finding kind has exactly one row; ``reported_test`` closes behavior / contract /
  absence, ``diff_assertion`` closes docs (asserted) and design / security_policy
  (unproven); evidence of the other kind is refused;
- an absence finding is closed by ``reported_test`` only when the report changed tests
  (the proof's ``n_tests_changed`` is above zero);
- the R2 words: ADEQUATE / TEST_INADEQUATE for ``closed_proven``, RESOLVED / OPEN /
  REFUTED otherwise;
- "closed": asserted, refuted, accepted, ``closed_proven`` after R2 ADEQUATE, and
  MEDIUM / LOW ``closed_unproven``; not ``open``, ``invalid_test``, ``closed_proven``
  awaiting R2, HIGH / CRITICAL ``closed_unproven``;
- ``push_effect`` = HIGH / CRITICAL and not closed (a HIGH ``closed_proven`` that R2 has
  not judged ADEQUATE blocks);
- the audit counts closed findings with the same function.
"""
from __future__ import annotations

import pytest

from core.run_protocol import findings as fd
from tests.test_reflect_and_audit_generic import _audit
from tests.test_run_protocol_findings import Stream


def _closed_by(kind, etype, result, **extra):
    stream = Stream()
    event = stream.finding(kind=kind)
    cid = "CF-" + event["event_id"][:8]
    if etype == "diff_assertion":
        stream.answer("correctness", 2)
        stream.add("finding_status", {"canonical_id": cid, "status": "RESOLVED",
                                      "answer_event_id": stream.rows[-1]["event_id"]},
                   producer="hook", round=2)
        extra = {"touches_finding": True, "touches_executable": False, **extra}
    if etype == "reported_test":
        report = stream.add("validation_report", {
            "stage": "review", "repo": "repo", "code_tree": "1" * 40, "n_pass": 1,
            "n_fail": 0, "n_not_run": 0, "report_json": "{}"})
        extra = {"report_event_id": report["event_id"], "repo": "repo",
                 "command": "run tests", **extra}
    stream.proof(cid, etype, result, **extra)
    return stream.projected()[0]


def test_every_kind_has_one_row():
    assert set(fd.CLOSURE) == fd.KINDS
    assert {row["evidence"] for row in fd.CLOSURE.values()} == {"reported_test", "diff_assertion"}


@pytest.mark.parametrize("kind", ["behavior", "contract"])
def test_reported_test_closes_behavior_and_contract(kind):
    assert _closed_by(kind, "reported_test", "proven")["status"] == "closed_proven"


def test_reported_test_closes_absence_only_with_changed_tests():
    assert _closed_by("absence", "reported_test", "proven",
                      n_tests_changed=2)["status"] == "closed_proven"
    for extra in ({}, {"n_tests_changed": 0}):
        with pytest.raises(fd.FindingError, match="absence.*changed tests"):
            _closed_by("absence", "reported_test", "proven", **extra)


@pytest.mark.parametrize("kind", ["docs", "design", "security_policy"])
def test_reported_test_does_not_close_asserted_kinds(kind):
    with pytest.raises(fd.FindingError, match="reported_test"):
        _closed_by(kind, "reported_test", "proven")


@pytest.mark.parametrize("kind,state", [("docs", "closed_asserted"),
                                        ("design", "closed_unproven"),
                                        ("security_policy", "closed_unproven")])
def test_diff_assertion_closes_its_kinds_to_the_table_state(kind, state):
    assert _closed_by(kind, "diff_assertion", "asserted")["status"] == state


@pytest.mark.parametrize("kind", ["behavior", "contract", "absence"])
def test_diff_assertion_does_not_close_test_kinds(kind):
    with pytest.raises(fd.FindingError, match="diff_assertion"):
        _closed_by(kind, "diff_assertion", "asserted")


def test_r2_words_follow_the_state():
    assert fd.r2_allowed("closed_proven") == {"ADEQUATE", "TEST_INADEQUATE"}
    for state in ("open", "invalid_test", "closed_unproven", "closed_asserted"):
        assert fd.r2_allowed(state) == {"RESOLVED", "OPEN", "REFUTED"}
    assert fd.r2_status_allowed("closed_proven", "adequate")
    assert not fd.r2_status_allowed("closed_proven", "RESOLVED")


@pytest.mark.parametrize("severity,status,r2,closed", [
    ("HIGH", "closed_asserted", None, True), ("HIGH", "refuted", "REFUTED", True),
    ("HIGH", "accepted", None, True), ("HIGH", "closed_proven", "ADEQUATE", True),
    ("HIGH", "closed_proven", None, False), ("HIGH", "closed_proven", "OPEN", False),
    ("LOW", "closed_proven", None, False), ("MEDIUM", "closed_unproven", "RESOLVED", True),
    ("LOW", "closed_unproven", "RESOLVED", True), ("HIGH", "closed_unproven", "RESOLVED", False),
    ("CRITICAL", "closed_unproven", "RESOLVED", False), ("LOW", "open", None, False),
    ("HIGH", "invalid_test", "TEST_INADEQUATE", False),
])
def test_closed_and_push_effect(severity, status, r2, closed):
    row = {"severity": severity, "status": status, "r2_status": r2}
    assert fd.is_closed(row) is closed
    assert fd.push_effect(row) is (severity in ("HIGH", "CRITICAL") and not closed)


def test_audit_counts_closed_with_the_same_function():
    base = {"kind": "behavior", "file": "a.go", "line": 1, "claim": "c", "expert": "correctness"}
    rows = [{**base, "canonical_id": "CF-1", "severity": "HIGH", "status": "closed_proven",
             "r2_status": "ADEQUATE"},
            {**base, "canonical_id": "CF-2", "severity": "LOW", "status": "closed_unproven",
             "r2_status": "RESOLVED", "kind": "design"},
            {**base, "canonical_id": "CF-3", "severity": "HIGH", "status": "closed_unproven",
             "r2_status": "RESOLVED", "kind": "design"},
            {**base, "canonical_id": "CF-4", "severity": "HIGH", "status": "closed_proven",
             "r2_status": None}]
    value = _audit(findings=rows)
    q = value["summary"]["quality"]
    assert (q["findings_closed"], q["findings_confirmed"], q["findings_total"]) == (2, 1, 4)
    assert value["details"]["unclosed_high"] == ["CF-3", "CF-4"]
