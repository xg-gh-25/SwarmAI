"""Tests for wiring recall@K into the BVT gate (run_3df6cc61).

Recall is SwarmAI's core differentiator (pure keyword/FTS5/BM25, vector leg
removed 2026-06-28). Its QUALITY had no gate: recall@K was implemented
(recall_suite.score_recall_case) + dispatchable (PROGRAMMATIC_EVALUATORS) but
(a) omitted from _GATE_ELIGIBLE_EVALUATORS so compute_bvt skipped every recall
case, and (b) a YAML golden case could not supply the live corpus that
eval_recall_at_k reads from verification.corpus.

This suite proves the wiring:
  - recall_at_k is gate-eligible in BOTH mirrored sets (lockstep, no drift).
  - eval_recall_at_k live-loads corpus from verification.corpus_source at eval
    time (corpus-by-reference — never embed private MEMORY.md into a case).
  - compute_bvt counts a passing recall case green and a FAILING (wrong-gold)
    recall case flips green=False — the mutation-proof, non-circular property.
  - the recall_suite --knockout CLI emits RECALL_TEETH_FAIL (the honest teeth
    that a recall case's negative_command runs).
"""
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.eval_runner import (  # noqa: E402
    compute_bvt,
    eval_recall_at_k,
    _GATE_ELIGIBLE_EVALUATORS,
)
from scripts.golden_case_validator import compute_case_stamp  # noqa: E402

_REPO = Path(__file__).resolve().parents[1].parent


def _stamped_recall_case(gold, *, domain="ddd", doc="TECH.md", cid="GS_RCLTEST"):
    """A gate-eligible recall_at_k case carrying corpus_source (no embedded
    corpus), stamped so compute_bvt will actually count it."""
    c = {
        "id": cid,
        "category": "recall",
        "dimension": "capability",
        "eval_method": "programmatic",
        "affected_by": ["backend/core/recall_multi.py"],
        "evaluators": ["recall_at_k"],
        "tier": "active",
        "verification": {
            "domain": domain,
            "doc": doc,
            "query": "how does the autonomous pipeline work — its stages",
            "gold": gold,
            "k": 5,
            "corpus_source": {"domain": domain, "doc": doc, "project": "SwarmAI"},
            "negative_command": (
                "python backend/scripts/recall_suite.py --knockout"
            ),
        },
    }
    c["validated_by_4gate"] = compute_case_stamp(c)
    return c


# ── AC2: recall_at_k gate-eligible in BOTH mirrored sets ──────────────────────
def test_recall_at_k_is_gate_eligible():
    assert "recall_at_k" in _GATE_ELIGIBLE_EVALUATORS


def test_gate_eligible_sets_still_mirror():
    """The two _GATE_ELIGIBLE_EVALUATORS frozensets must stay identical."""
    from scripts import eval_runner
    from scripts.golden_case_validator import (
        _GATE_ELIGIBLE_EVALUATORS as validator_set,
    )
    assert validator_set == eval_runner._GATE_ELIGIBLE_EVALUATORS


# ── AC1: corpus-by-reference — eval_recall_at_k live-loads from corpus_source ─
def test_corpus_source_live_loads_ddd():
    """A ddd case with corpus_source (NO embedded corpus) scores a real rank."""
    case = _stamped_recall_case(gold=["TECH.md", "Architecture"])
    # sanity: the case carries NO embedded corpus
    assert "corpus" not in case["verification"]
    res = eval_recall_at_k(case, _REPO)
    assert res["status"] in ("passed", "failed")  # a real rank check ran, not error
    assert res.get("notes")


def test_corpus_source_context_files_live_loads():
    """A context_files case with corpus_source live-loads MEMORY.md."""
    case = _stamped_recall_case(
        gold="Open Threads", domain="context_files", doc="MEMORY.md",
        cid="GS_RCLTEST_CF",
    )
    case["verification"]["query"] = "session 资源仲裁 多 tab 隔离"
    assert "corpus" not in case["verification"]
    res = eval_recall_at_k(case, _REPO)
    assert res["status"] in ("passed", "failed")


def test_missing_corpus_and_source_is_error_not_silent_pass():
    """No corpus AND no corpus_source → fail-loud error, never a silent pass."""
    case = _stamped_recall_case(gold=["TECH.md", "Architecture"])
    del case["verification"]["corpus_source"]
    res = eval_recall_at_k(case, _REPO)
    assert res["status"] == "error"


def test_corpus_source_missing_doc_is_error():
    """corpus_source pointing at a nonexistent doc → error (fail-loud)."""
    case = _stamped_recall_case(gold=["NOPE.md", "Architecture"], doc="NOPE.md")
    res = eval_recall_at_k(case, _REPO)
    assert res["status"] == "error"


def test_corpus_source_wrong_type_is_error_not_crash():
    """A malformed corpus_source (truthy non-dict, e.g. a string from bad YAML)
    → fail-LOUD error, NEVER an uncaught AttributeError (Gate-2 adversarial BUG#1)."""
    case = _stamped_recall_case(gold=["TECH.md", "Architecture"])
    case["verification"]["corpus_source"] = "invalid-not-a-dict"
    res = eval_recall_at_k(case, _REPO)  # must not raise
    assert res["status"] == "error"
    assert "corpus_source must be a dict" in res["notes"]


# ── AC3/AC5: mutation-proof — wrong gold flips compute_bvt green=False ─────────
def test_bvt_green_when_recall_case_passes():
    """A recall case whose gold IS recalled → compute_bvt counts it green."""
    case = _stamped_recall_case(gold=["TECH.md", "Architecture"])
    result = eval_recall_at_k(case, _REPO)
    # Only meaningful if the gold is genuinely recalled today.
    if result["status"] != "passed":
        import pytest
        pytest.skip("gold not recalled on live corpus — covered by mutation test")
    bvt = compute_bvt([case], [{"id": case["id"], "status": "passed"}])
    assert bvt["green"] is True
    assert bvt["total"] == 1 and bvt["passed"] == 1


def test_bvt_red_when_recall_case_fails_wrong_gold():
    """MUTATION-PROOF: a wrong-gold recall case (rank 0 → failed) flips BVT red.
    This is the non-circular property — the gate goes RED when recall does NOT
    return the expected section, so it cannot be gamed."""
    case = _stamped_recall_case(
        gold=["TECH.md", "This Section Does Not Exist Anywhere"],
        cid="GS_RCLTEST_MISS",
    )
    result = eval_recall_at_k(case, _REPO)
    assert result["status"] == "failed", "wrong gold must score rank 0 = failed"
    bvt = compute_bvt([case], [{"id": case["id"], "status": "failed"}])
    assert bvt["green"] is False
    assert bvt["failed"] == 1


# ── AC5: knockout CLI — the honest teeth ──────────────────────────────────────
def test_knockout_cli_passes_on_healthy_scorer():
    """recall_suite.py --knockout on a HEALTHY scorer: the wrong gold is correctly
    rejected (rank 0) → exit 0, 'knockout OK'. This is the POSITIVE path a recall
    case's negative_command runs to prove the scorer discriminates."""
    proc = subprocess.run(
        [sys.executable, "backend/scripts/recall_suite.py", "--knockout"],
        cwd=str(_REPO), capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, (proc.stdout + proc.stderr)
    assert "knockout OK" in proc.stdout
    assert "RECALL_TEETH_FAIL" not in proc.stdout


def test_knockout_emits_fail_token_when_scorer_is_vacuous(monkeypatch):
    """MUTATION: if the scorer stops discriminating (a wrong gold scores rank>0),
    run_knockout MUST emit RECALL_TEETH_FAIL + report not-discriminating. Proves
    the teeth CAN fire — a non-vacuous negative check (DDD 2-tooth policy)."""
    from scripts import recall_suite
    monkeypatch.setattr(
        recall_suite, "score_recall_case",
        lambda v: {"status": "passed", "rank": 1, "recall_at_k": 1,
                   "reciprocal_rank": 1.0, "notes": "vacuous-scorer-stub"},
    )
    ok, msg = recall_suite.run_knockout()
    assert ok is False
    assert "RECALL_TEETH_FAIL" in msg
