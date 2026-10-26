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
    assert r["dimension"] == "quality"   # "capability" is an older token for quality
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


# ── Dimension normalization ──────────────────────────────────────────────────
# The judge names one of the three judged dimensions of the five-dimension model
# (cognition / quality / responsibility). Older tokens map onto them, and an
# unknown token is quality, so the Online tab never shows a dimension outside them.

def _judge_emitting(dimension: str):
    import json
    def _j(_prompt: str) -> str:
        return json.dumps({"goal_score": 0.4, "tool_score": 0.5,
                           "dimension": dimension, "reason": "x"})
    return _j


import pytest

@pytest.mark.parametrize("token,dimension", [
    ("cognition", "cognition"), ("quality", "quality"), ("responsibility", "responsibility"),
    ("factual", "cognition"), ("utility", "cognition"), ("context_utility", "cognition"),
    ("judgment", "quality"), ("capability", "quality"), ("recovery", "quality"),
    ("compliance", "responsibility"),
    ("totally_bogus", "quality"), ("", "quality"),
])
def test_score_session_names_one_of_the_three_dimensions(token, dimension):
    r = score_session(prompt="x", response="y", tool_names=[],
                      judge_fn=_judge_emitting(token))
    assert r["dimension"] == dimension


@pytest.mark.parametrize("lang, needle", [
    ("cjk", "in Chinese"), ("latin", "in English"), (None, None),
])
def test_the_reason_is_asked_for_in_the_users_language(lang, needle):
    seen = []
    score_session(prompt="x", response="y", tool_names=[], reason_language=lang,
                  judge_fn=lambda p: (seen.append(p), _fake_judge({}))[1])
    if needle:
        assert needle in seen[0]
    else:
        assert "Write the reason" not in seen[0]


def test_the_prompt_sha_names_the_prompt_text():
    import hashlib
    from core import session_scorer as ss
    assert len(ss.JUDGE_PROMPT_SHA) == 12
    assert ss.JUDGE_PROMPT_SHA != hashlib.sha256(b"").hexdigest()[:12]
