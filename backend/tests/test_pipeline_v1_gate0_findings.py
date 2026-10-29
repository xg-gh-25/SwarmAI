"""Gate-0 findings in the review findings (core/run_protocol/findings.py GATE0_*,
review.py GATE0_CLOSES).

Questions: does a later passing Gate-0 round close the open Gate-0 findings of an earlier
round, and only those (R1.1, R1.2); do negative, unparsed and the passing answer's own
findings stay open (U1); is a Gate-0 finding named in review-address refused cleanly,
and does the ±10-line cluster rule ignore a Gate-0 neighbour (R2.1, R2.2)?
"""
from __future__ import annotations

import json

import pytest

from core.run_protocol import events as ev
from core.run_protocol import findings as fd
from core.run_protocol.review import GATE0_CLOSES, ReviewError, ReviewOrchestrator, close_with
from core.run_protocol.store import AuditStore
from tests.review_bridge_helper import ReviewHookBridge

AT = "2026-10-22T00:00:00+00:00"


def _rig(tmp_path):
    authority = ev.EventAuthority()
    store = AuditStore(tmp_path / "audit", "run_one", authority)
    return ReviewOrchestrator(store, tmp_path / "run"), ReviewHookBridge(store, "session_1")


def _row(file="REQUIREMENTS.md", line=4, claim="scope misses a reader", kind="absence", severity="MEDIUM"):
    return json.dumps({"severity": severity, "kind": kind, "file": file, "line": line,
                       "claim": claim, "fix": "name it"})


_tool = iter(range(10_000))


def _gate0(review, bridge, attempt, text):
    handout = review.handout(expert_id=fd.GATE0_EXPERT, stage="evaluate", round=1,
                             attempt=attempt, at=AT, expert_instructions="Judge the framing",
                             review_input="{}")
    bound = bridge.track_tool_use(f"tool-g{next(_tool)}", handout["prompt"])
    bridge.record_answer(bound, text, completed_at=AT)


def _code_finding(review, bridge, file="backend/a.py", line=10):
    handout = review.handout(expert_id="correctness", stage="review", round=1, attempt=1,
                             at=AT, expert_instructions="Check", review_input="diff")
    bound = bridge.track_tool_use(f"tool-c{next(_tool)}", handout["prompt"])
    bridge.record_answer(bound, _row(file=file, line=line, kind="behavior", claim="crash"),
                         completed_at=AT)


def _by_expert(review):
    out = {}
    for row in review.findings():
        out.setdefault(row["expert"], []).append(row)
    return out


@pytest.mark.parametrize("verdict", ["SUPPORTED", "SUPPORTED-WITH-CHANGES"])
def test_a_later_passing_round_closes_the_earlier_rounds_gate0_findings(tmp_path, verdict):
    review, bridge = _rig(tmp_path)
    _gate0(review, bridge, 1, "VERDICT: WRONG-FRAME\n" + _row(claim="first"))
    _gate0(review, bridge, 2, f"VERDICT: {verdict}\n" + _row(line=9, claim="second"))
    rows = {row["claim"]: row for row in review.findings()}
    first, second = rows["first"], rows["second"]
    assert first["status"] == "closed_gate0" and fd.is_closed(first)
    passing = [e for e in review.store.read_events() if e["kind"] == "answer"
               and e["payload"].get("verdict") == verdict]
    assert len(passing) == 1 and first["evidence_event_id"] == passing[0]["event_id"]
    # the passing answer's own finding: no later round reads it
    assert second["status"] == "open" and not fd.is_closed(second)


@pytest.mark.parametrize("severity", ["HIGH", "CRITICAL"])
def test_a_high_gate0_finding_is_left_for_a_person(tmp_path, severity):
    # the passing round read the requirements, not this finding: it cannot clear the push gate
    review, bridge = _rig(tmp_path)
    _gate0(review, bridge, 1, "VERDICT: WRONG-FRAME\n" + _row(severity=severity, kind="security_policy"))
    _gate0(review, bridge, 2, "VERDICT: SUPPORTED\n")
    row = review.findings()[0]
    assert row["status"] == "open" and fd.push_effect(row)


@pytest.mark.parametrize("text", ["VERDICT: UNSUPPORTED\n", "VERDICT: WRONG-FRAME\n",
                                  "VERDICT: ALREADY-SATISFIED\n", "no verdict line\n"])
def test_no_other_gate0_answer_closes_a_finding(tmp_path, text):
    review, bridge = _rig(tmp_path)
    _gate0(review, bridge, 1, "VERDICT: UNSUPPORTED\n" + _row(claim="first"))
    _gate0(review, bridge, 2, text)
    row = next(r for r in review.findings() if r["claim"] == "first")
    assert row["status"] == "open" and not fd.is_closed(row)


def test_a_passing_round_closes_no_review_experts_finding(tmp_path):
    review, bridge = _rig(tmp_path)
    _code_finding(review, bridge)
    _gate0(review, bridge, 1, "VERDICT: UNSUPPORTED\n" + _row())
    _gate0(review, bridge, 2, "VERDICT: SUPPORTED\n")
    code = _by_expert(review)["correctness"][0]
    assert code["status"] == "open"


def test_review_address_refuses_a_gate0_finding_and_writes_nothing(tmp_path):
    review, bridge = _rig(tmp_path)
    _gate0(review, bridge, 1, "VERDICT: SUPPORTED\n" + _row())
    cid = review.findings()[0]["canonical_id"]
    before = len(review.store.read_events())
    with pytest.raises(ReviewError, match="Gate-0 finding"):
        review.record_fix_handoff({"pass": 1, "items": [{"finding_id": cid, "hunk": "@@"}]},
                                  at=AT, attempt=1)
    assert len(review.store.read_events()) == before
    assert close_with(review.findings()[0], None, uncovered=False, redispatch=False) == GATE0_CLOSES


def test_the_cluster_rule_does_not_require_a_gate0_neighbour(tmp_path):
    review, bridge = _rig(tmp_path)
    _gate0(review, bridge, 1, "VERDICT: SUPPORTED\n" + _row(file="backend/a.py", line=12))
    _code_finding(review, bridge, file="backend/a.py", line=10)
    code = _by_expert(review)["correctness"][0]["canonical_id"]
    done = review.record_fix_handoff({"pass": 1, "items": [{"finding_id": code, "hunk": "@@"}]},
                                     at=AT, attempt=1)
    assert done["addressed"] == [code]
    assert set(review.r2_roster()) == {"correctness"}
