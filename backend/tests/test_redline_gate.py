"""Tests for the red-line (zero-tolerance) eval gate (run_21490939).

The red-line gate closes a structural hole in the eval: the ONLY hard gate
(compute_bvt / bvt.green) is keyed on MECHANISM — it skips every
eval_method=='llm' case (eval_runner.py) and non-deterministic evaluators — so a
semantic red-line (refusal / political-sensitivity / tone, which is LLM-judged)
can only ever touch compute_scores' flat equal-weight percentage, where a single
failure is averaged away. compute_redline is the SEVERITY-keyed veto: ANY case
marked `redline: true` that FAILS or ERRORS forces violated=True, independent of
eval_method, tier, or the aggregate %.

Design invariants under test:
  AC1 — a redline case that FAILS -> violated=True, regardless of eval_method
        (incl. 'llm', which bvt structurally excludes).
  AC2 — backward compat: 0 redline cases -> violated=False, total=0; compute_bvt
        over the SAME set is unchanged (redline is additive, not a re-weighting).
  AC3 — a redline case SKIPPED (e.g. an llm redline in a programmatic_only canary
        run) is reported in `skipped[]`, NOT a violation — fail-closed on
        FAIL/ERROR, never on not-run.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.eval_runner import compute_redline, compute_bvt  # noqa: E402
from scripts.golden_case_validator import compute_case_stamp  # noqa: E402


def _case(cid, redline=None, eval_method="programmatic", evaluators=None):
    c = {
        "id": cid,
        "eval_method": eval_method,
        "evaluators": evaluators if evaluators is not None else ["file_contains"],
        "tier": "active",
        "verification": {"file": "README.md", "expected_contains": "Swarm"},
    }
    if redline is not None:
        c["redline"] = redline
    return c


def _result(cid, status="passed"):
    return {"id": cid, "status": status}


# ── AC1: a failing red-line forces NO-GO, independent of eval_method ──────────

def test_failing_redline_forces_violation():
    cases = [_case("GS_RL", redline=True)]
    rl = compute_redline(cases, [_result("GS_RL", "failed")])
    assert rl["violated"] is True
    assert rl["total"] == 1
    assert "GS_RL" in [v["id"] for v in rl["violations"]]


def test_failing_redline_llm_case_still_gates():
    """The crux: an eval_method=='llm' red-line case is EXCLUDED from bvt, but
    compute_redline must still gate it — that is the entire point of this gate."""
    cases = [_case("GS_LLM_RL", redline=True, eval_method="llm", evaluators=["goal_success"])]
    results = [_result("GS_LLM_RL", "failed")]
    # bvt structurally ignores it (proving the hole exists):
    assert compute_bvt(cases, results)["total"] == 0
    # redline catches it:
    rl = compute_redline(cases, results)
    assert rl["violated"] is True


def test_erroring_redline_forces_violation():
    """ERROR (misconfigured / crashed evaluator) is a violation, not a free pass —
    a red-line that cannot even run is not proven safe."""
    cases = [_case("GS_RL", redline=True)]
    rl = compute_redline(cases, [_result("GS_RL", "error")])
    assert rl["violated"] is True


def test_redline_not_diluted_by_high_aggregate():
    """1 failing red-line among 99 passing non-redline cases -> still violated."""
    cases = [_case("GS_RL", redline=True)] + [_case(f"GS_{i}") for i in range(99)]
    results = [_result("GS_RL", "failed")] + [_result(f"GS_{i}", "passed") for i in range(99)]
    rl = compute_redline(cases, results)
    assert rl["violated"] is True
    assert rl["total"] == 1  # only redline cases counted


# ── AC2: backward compatibility — no redline cases = no-op ────────────────────

def test_no_redline_cases_is_vacuous_pass():
    cases = [_case(f"GS_{i}") for i in range(5)]
    results = [_result(f"GS_{i}", "passed") for i in range(5)]
    rl = compute_redline(cases, results)
    assert rl["violated"] is False
    assert rl["total"] == 0
    assert rl["violations"] == []


def test_redline_false_is_not_a_redline_case():
    """redline: false (explicitly opted out) must not count."""
    cases = [_case("GS_OFF", redline=False)]
    rl = compute_redline(cases, [_result("GS_OFF", "failed")])
    assert rl["violated"] is False
    assert rl["total"] == 0


def test_bvt_unchanged_by_presence_of_compute_redline():
    """compute_redline is additive: computing it does not alter what bvt sees."""
    cases = [_case("GS_A"), _case("GS_B")]
    for c in cases:
        c["validated_by_4gate"] = compute_case_stamp(c)
    results = [_result("GS_A", "passed"), _result("GS_B", "passed")]
    before = compute_bvt(cases, results)
    _ = compute_redline(cases, results)
    after = compute_bvt(cases, results)
    assert before == after
    assert before["green"] is True


# ── AC3: skipped red-line is reported, not a violation ────────────────────────

def test_skipped_redline_is_not_a_violation():
    """An llm red-line in a programmatic_only canary run returns 'skipped' — it did
    not run, so it is reported distinctly and does NOT flip the gate red."""
    cases = [_case("GS_RL", redline=True, eval_method="llm", evaluators=["goal_success"])]
    rl = compute_redline(cases, [_result("GS_RL", "skipped")])
    assert rl["violated"] is False
    assert "GS_RL" in rl["skipped"]
    assert rl["violations"] == []


def test_skipped_and_failed_mix():
    """One redline skipped, one redline failed -> violated (the failure gates)."""
    cases = [_case("GS_SKIP", redline=True), _case("GS_FAIL", redline=True)]
    results = [_result("GS_SKIP", "skipped"), _result("GS_FAIL", "failed")]
    rl = compute_redline(cases, results)
    assert rl["violated"] is True
    assert "GS_SKIP" in rl["skipped"]
    assert "GS_FAIL" in [v["id"] for v in rl["violations"]]
    assert rl["total"] == 2


def test_passing_redline_is_clean():
    cases = [_case("GS_RL", redline=True)]
    rl = compute_redline(cases, [_result("GS_RL", "passed")])
    assert rl["violated"] is False
    assert rl["total"] == 1
    assert rl["violations"] == []
    assert rl["skipped"] == []
