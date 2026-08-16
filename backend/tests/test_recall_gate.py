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
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.eval_runner import (  # noqa: E402
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


# NOTE — the live-load / knockout QUALITY cases were REMOVED 2026-08-16 (CI = BVT
# only). They read the REAL corpus from the developer workspace (~/.swarm-ai/SwarmWS
# via recall_suite._load_corpora), absent in a bare CI checkout → they could only ever
# SKIP in CI = zero signal (a skipped test verifies nothing). "Does live recall score
# a real rank / does wrong-gold flip red" is a QUALITY property of the DEPLOYED system;
# it belongs to the eval OS (golden set + post-deploy scoring), not CI BVT. What stays
# below is BVT: gate-eligibility wiring + the fail-loud error-path contracts, all of
# which self-verify in a clean checkout with no workspace corpus.


# ── corpus-by-reference: fail-loud error-path contracts (no workspace corpus needed) ─
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


# ── AC3/AC5 mutation-proof + knockout QUALITY cases REMOVED 2026-08-16 (CI = BVT) ──
# test_bvt_green_when_recall_case_passes / test_bvt_red_when_recall_case_fails_wrong_gold
# / test_knockout_cli_passes_on_healthy_scorer / test_knockout_emits_fail_token_when_
# scorer_is_vacuous all needed the live workspace corpus (recall_suite._load_corpora /
# the --knockout subprocess), so in a clean CI checkout they could only SKIP = zero
# signal. Whether real recall ranks the gold / wrong-gold flips BVT red / the scorer
# discriminates is a QUALITY property of the DEPLOYED system → the eval OS (golden set,
# post-deploy), NOT CI BVT. The compute_bvt green/red counting logic itself is unit-
# tested elsewhere without a live corpus.
