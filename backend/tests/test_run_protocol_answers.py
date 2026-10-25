"""Tests the strict Pipeline v1 review-answer envelope.

Methodology: table-driven textual answers; no model inference or filesystem state.
Invariants: exact verdict location, typed findings, exact R2 roster, and no prose guessing.
"""
from __future__ import annotations

import pytest

from core.run_protocol.answers import AnswerError, parse_answer

A = "CF-aaaaaaaa"
B = "CF-bbbbbbbb"
FINDING = '{"severity":"HIGH","kind":"behavior","file":"a.py","line":12,"claim":"crashes","fix":"guard None"}'


def test_gate0_verdict_must_be_first_and_in_enum():
    good = parse_answer("VERDICT: SUPPORTED\nreason", expert_id="gate0-skeptic", round=1)
    assert good.state == "parsed" and good.verdict == "SUPPORTED"
    late = parse_answer("reason\nVERDICT: SUPPORTED", expert_id="gate0-skeptic", round=1)
    assert late.state == "unparsed"
    bad = parse_answer("VERDICT: PASS", expert_id="gate0-skeptic", round=1)
    assert bad.state == "unparsed"


def test_review_r1_accepts_finding_and_no_findings_anywhere():
    parsed = parse_answer(f"analysis\n```json\n{FINDING}\n```", expert_id="correctness", round=1)
    assert parsed.state == "parsed" and parsed.findings[0]["severity"] == "HIGH"
    clear = parse_answer("NO FINDINGS\n分析完成", expert_id="security", round=1)
    assert clear.state == "parsed" and not clear.findings
    conflict = parse_answer(f"NO FINDINGS\n{FINDING}", expert_id="correctness", round=1)
    assert conflict.state == "unparsed"


def test_review_expert_cannot_emit_gate_verdict():
    answer = parse_answer("VERDICT: SUPPORTED\nNO FINDINGS", expert_id="correctness", round=1)
    assert answer.state == "unparsed"


def test_r2_exact_roster_missing_is_partial_and_foreign_is_integrity():
    text = (
        f'{{"finding_id":"{A}","status":"RESOLVED","kind_confirm":"behavior","note":"fixed"}}\n'
        '{"finding_id":"CF-cccccccc","status":"OPEN"}\nNO FINDINGS'
    )
    result = parse_answer(text, expert_id="correctness", round=2, finding_ids=[A, B])
    assert result.state == "partial"
    assert result.statuses[0]["finding_id"] == A
    assert result.missing_statuses == (B,)
    assert result.integrity == ("foreign R2 status ignored: CF-cccccccc",)


def test_r2_can_report_new_findings_beside_complete_statuses():
    text = (
        f'{{"finding_id":"{A}","status":"ADEQUATE"}}\n'
        f'{{"finding_id":"{B}","status":"OPEN"}}\n{FINDING}'
    )
    result = parse_answer(text, expert_id="correctness", round=2, finding_ids=[A, B])
    assert result.state == "parsed" and len(result.statuses) == 2 and len(result.findings) == 1


@pytest.mark.parametrize("text", [
    '{"severity":"HIGH","kind":"behavior","file":"a.py","line":0,"claim":"x","fix":"y"}',
    '{"severity":"HIGH","kind":"style","file":"a.py","line":1,"claim":"x","fix":"y"}',
    '{"severity":"HIGH","kind":"behavior","file":"a.py","line":1,"claim":"x"}',
    '{bad json}',
    "looks fine",
])
def test_malformed_or_prose_only_answers_are_unparsed(text):
    assert parse_answer(text, expert_id="correctness", round=1).state == "unparsed"


def test_duplicate_status_is_rejected_not_last_write_wins():
    line = f'{{"finding_id":"{A}","status":"OPEN"}}'
    result = parse_answer(f"{line}\n{line}", expert_id="correctness", round=2, finding_ids=[A])
    assert result.state == "unparsed" and any("duplicate" in value for value in result.errors)


def test_round_and_roster_shape_are_strict():
    with pytest.raises(AnswerError, match="R2 requires"):
        parse_answer("NO FINDINGS", expert_id="correctness", round=2)
    with pytest.raises(AnswerError, match="R1 cannot"):
        parse_answer("NO FINDINGS", expert_id="correctness", round=1, finding_ids=[A])
    with pytest.raises(AnswerError, match="unique canonical"):
        parse_answer("NO FINDINGS", expert_id="correctness", round=2, finding_ids=[A, A])


def test_payload_counts_are_generated_not_caller_supplied():
    result = parse_answer(FINDING, expert_id="correctness", round=1)
    assert result.answer_payload == {"state": "parsed", "verdict": None,
                                     "n_findings": 1, "n_status": 0}


def test_a_gate0_answer_with_two_verdicts_has_no_verdict():
    """F-5: a second VERDICT line could flip the outcome; the answer is unparsed."""
    two = parse_answer("VERDICT: SUPPORTED\nwait\nVERDICT: UNSUPPORTED",
                       expert_id="gate0-skeptic", round=1)
    assert two.state == "unparsed" and two.verdict is None


def test_rows_written_with_the_contracts_own_labels_parse():
    """OUTPUT_CONTRACT shows rows as `New finding: {...}` and `R2 status: {...}`; an
    expert that copies the label (every Gate-0 answer in run_bb7b1850) must not be
    unparsed for it."""
    gate = parse_answer(f"VERDICT: SUPPORTED-WITH-CHANGES\nreason\n\nNew finding: {FINDING}",
                        expert_id="gate0-skeptic", round=1)
    assert gate.state == "parsed" and gate.findings[0]["claim"] == "crashes"
    status = (f'R2 status: {{"finding_id":"{A}","status":"RESOLVED",'
              '"kind_confirm":null,"note":"fixed"}')
    r2 = parse_answer(f"{status}\nNO FINDINGS", expert_id="correctness", round=2,
                      finding_ids=[A])
    assert r2.state == "parsed" and r2.statuses[0]["status"] == "RESOLVED"
    bullet = parse_answer(f"- New finding: {FINDING}", expert_id="correctness", round=1)
    assert bullet.state == "parsed"
    # another label is still not a row
    other = parse_answer(f"Note: {FINDING}", expert_id="correctness", round=1)
    assert other.state == "unparsed"
