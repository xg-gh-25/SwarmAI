"""Tests for session_scorer — layer-③ real-session quality scoring.

Methodology: session_scorer.score_session is a PURE function with an injected
judge boundary (judge_fn). It scores a REAL past session's (prompt, response,
tool_names) on two axes — goal attainment + tool selection — and attributes a
dimension. Unlike eval_llm_judge (static contract analysis: "would the agent's
rules produce X"), this judges the ACTUAL output that already happened.

Key invariants tested:
- judge_fn is injected → no Bedrock call in tests
- returns {goal_score, tool_score, dimension, reason} with scores in [0,1]
- malformed judge output fails closed (never fabricates a passing score)
- empty session (no prompt) is skipped, not scored
"""
from __future__ import annotations

import json

from core.session_scorer import score_session


def _fake_judge(payload: dict):
    """A fake judge_fn: returns a canned JSON verdict. Signature mirrors the
    real converse-style boundary (takes a prompt str, returns text str)."""
    return json.dumps({
        "goal_score": 0.4,
        "tool_score": 0.3,
        "dimension": "capability",
        "reason": "agent selected Grep when Read was needed; goal not met",
    })


def test_score_session_returns_two_axis_scores():
    r = score_session(
        prompt="Fix the timeout bug in session_unit.py",
        response="I think it's probably fine, didn't change anything.",
        tool_names=["Grep", "Grep"],
        judge_fn=_fake_judge,
    )
    assert set(r.keys()) >= {"goal_score", "tool_score", "dimension", "reason"}
    assert 0.0 <= r["goal_score"] <= 1.0
    assert 0.0 <= r["tool_score"] <= 1.0
    assert r["dimension"] == "capability"
    assert r["reason"]  # non-empty


def test_score_session_low_score_detected():
    r = score_session(
        prompt="x", response="y", tool_names=[], judge_fn=_fake_judge,
    )
    # 0.4 / 0.3 both < 0.6 threshold — this session is a harvest candidate
    assert min(r["goal_score"], r["tool_score"]) < 0.6


def test_score_session_empty_prompt_skipped():
    r = score_session(prompt="", response="whatever", tool_names=[], judge_fn=_fake_judge)
    assert r.get("status") == "skipped"


def test_score_session_malformed_judge_fails_closed():
    """A judge that returns non-JSON garbage must NOT produce a fabricated
    passing score — it fails closed (status=error, no goal/tool score trusted)."""
    r = score_session(
        prompt="x", response="y", tool_names=[],
        judge_fn=lambda p: "not json at all {{{",
    )
    assert r.get("status") == "error"
    # must NOT silently claim a pass
    assert r.get("goal_score") is None or r.get("goal_score") == 0.0


def test_score_session_judge_receives_real_output():
    """The judge prompt MUST contain the real response + tool trajectory — this
    is what makes it evaluate ACTUAL behavior, not a static contract."""
    captured = {}

    def spy_judge(payload: str):
        captured["prompt"] = payload
        return json.dumps({"goal_score": 0.9, "tool_score": 0.9,
                           "dimension": "judgment", "reason": "ok"})

    score_session(
        prompt="What is the fixed port?",
        response="Port 18321.",
        tool_names=["Read", "Grep"],
        judge_fn=spy_judge,
    )
    p = captured["prompt"]
    assert "18321" in p          # real response present
    assert "Read" in p and "Grep" in p  # real tool trajectory present


# ── Dimension normalization (run_2b73a16e / C044 source-fix) ──────────────────
# The judge PROMPT teaches shorthand dimension tokens (factual/judgment/utility),
# but the golden-set canonical dimensions are the long forms
# (factual_accuracy/judgment_quality/context_utility). session_scorer must NORMALIZE
# the judge's shorthand → canonical so it ONLY ever emits a canonical dimension —
# otherwise session_harvest writes shorthand into golden_set drafts and they leak
# into /health per-dimension scores as an off-canonical bucket (the 11 GS_HARVEST_*
# 'judgment' cases). Root fix at the emission point, one place.

def _judge_emitting(dimension: str):
    import json
    def _j(_prompt: str) -> str:
        return json.dumps({"goal_score": 0.4, "tool_score": 0.5,
                           "dimension": dimension, "reason": "x"})
    return _j


import pytest

@pytest.mark.parametrize("shorthand,canonical", [
    ("judgment", "judgment_quality"),
    ("factual", "factual_accuracy"),
    ("utility", "context_utility"),
    # already-canonical inputs pass through unchanged (identity)
    ("judgment_quality", "judgment_quality"),
    ("factual_accuracy", "factual_accuracy"),
    ("context_utility", "context_utility"),
    # the three that have no long/short divergence must map to THEMSELVES,
    # not fall through to the unknown→capability default
    ("capability", "capability"),
    ("compliance", "compliance"),
    ("recovery", "recovery"),
])
def test_score_session_normalizes_dimension_to_canonical(shorthand, canonical):
    r = score_session(prompt="x", response="y", tool_names=[],
                      judge_fn=_judge_emitting(shorthand))
    assert r["dimension"] == canonical, (
        f"scorer emitted {r['dimension']!r} for judge token {shorthand!r} — "
        f"must normalize to canonical {canonical!r}")


def test_score_session_unknown_dimension_defaults_to_capability():
    # An out-of-vocab token still fails closed to 'capability' (never trusted).
    r = score_session(prompt="x", response="y", tool_names=[],
                      judge_fn=_judge_emitting("totally_bogus"))
    assert r["dimension"] == "capability"
