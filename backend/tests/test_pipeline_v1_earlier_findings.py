"""Which earlier findings a later REVIEW visit answers for, and when the audit folds TS-1
into the carry line (design docs/design/2026-10-31-pipeline-review-close-and-landing-route.md
§3 change 1, 2026-11-02 addendum; 2026-11-01-pipeline-delivery-friction E2).

Questions each test answers:
- does a visit TEST reopened take the earlier findings a fixing pass can address, and
  leave out a Gate-0 finding, one a person decides, and a closed one?
- does a visit a doc-only change opened (not counted against the reopen cap) take none?
- is TS-1 folded into the carry line only when every TS-1 problem is a stale tree and
  LG-1 is not open, and kept when it names a failed command or a missing report?
"""
from __future__ import annotations

from core.run_protocol import audit, findings as fd, review


def _events(reopen_payload):
    return [{"event_id": "a0", "kind": "answer"},
            {"event_id": "re", "kind": "review_reopen", "payload": reopen_payload},
            {"event_id": "a2", "kind": "answer"}]


ROWS = [
    {"canonical_id": "this", "answer_event_id": "a2", "status": "open", "expert": "correctness"},
    {"canonical_id": "open", "answer_event_id": "a0", "status": "open", "expert": "correctness"},
    {"canonical_id": "inval", "answer_event_id": "a0", "status": "invalid_test", "expert": "security"},
    {"canonical_id": "gate0", "answer_event_id": "a0", "status": "open", "expert": fd.GATE0_EXPERT},
    {"canonical_id": "person", "answer_event_id": "a0", "status": "closed_unproven", "expert": "correctness",
     "severity": "HIGH"},
    {"canonical_id": "refuted", "answer_event_id": "a0", "status": "refuted", "expert": "correctness"},
]


def _ids(events):
    return {row["canonical_id"] for row in review._visit_rows(events, ROWS, review.visit_start(events))}


def test_a_test_reopened_visit_takes_the_earlier_findings_a_pass_can_address():
    assert review.visit_start(_events({"reason": "the code changed in TEST"})) == 2
    assert _ids(_events({"reason": "the code changed in TEST"})) == {"this", "open", "inval"}


def test_a_doc_only_reopen_takes_no_earlier_finding():
    assert _ids(_events({"reason": "only doc-class files changed in TEST",
                         "doc_paths": {"r": ["docs/a.md"]}})) == {"this"}


def test_the_first_visit_is_unchanged():
    events = [{"event_id": "a0", "kind": "answer"}]
    rows = [dict(ROWS[1])]
    assert [r["canonical_id"] for r in review._visit_rows(events, rows, 0)] == ["open"]


STALE = "r: the test report is for tree aaaa, the code is now bbbb; test again and report"


def test_ts1_is_folded_into_the_carry_only_when_it_is_the_carry():
    carried = "tests carried over a main move: r (main x->y)"
    assert "TS-1" not in audit._merged_into_carry({"TS-1": STALE, "RV-6": "x"}, carried)
    assert "TS-1" not in audit._merged_into_carry({"TS-1": STALE + "; " + STALE.replace("r:", "s:")}, carried)
    # no carry: TS-1 is its own line
    assert "TS-1" in audit._merged_into_carry({"TS-1": STALE}, None)
    # a failed command or a missing report is another fact: kept
    failed = STALE + "; r: the TEST report has 2 failed command(s); fix them and report again"
    assert "TS-1" in audit._merged_into_carry({"TS-1": failed}, carried)
    missing = "s: no test validation report; run your tests"
    assert "TS-1" in audit._merged_into_carry({"TS-1": missing}, carried)
    # LG-1 open too: the carry no longer describes the landing
    assert "TS-1" in audit._merged_into_carry({"TS-1": STALE, "LG-1": "x"}, carried)
