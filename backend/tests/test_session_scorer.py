"""Tests for session_scorer — layer-③ real-session quality scoring.

Methodology: session_scorer.score_session is a PURE function with an injected
judge boundary (judge_fn). It scores a REAL past session's (prompt, response,
tool_names) on two axes — goal attainment + tool selection — and attributes a
dimension. Unlike eval_llm_judge (static contract analysis: "would the agent's
rules produce X"), this judges the ACTUAL output that already happened.

Key invariants tested:
- judge_fn is injected → no Bedrock call in tests; it returns the judge's answer
  (a dict shaped by JUDGE_SCHEMA, see default_judge_fn) or raises
  StructuredOutputError for an unusable reply
- returns {goal_score, tool_score, dimension, reason} with scores in [0,1]
- an unusable judge reply fails closed (never fabricates a passing score)
- empty session (no prompt) is skipped, not scored
"""
from __future__ import annotations

import pytest

from core.session_scorer import score_session
from jobs.bedrock import StructuredOutputError


def _fake_judge(payload: str) -> dict:
    """A fake judge_fn: returns a canned answer (the shape default_judge_fn returns)."""
    return {
        "goal_score": 0.4,
        "tool_score": 0.3,
        "dimension": "quality",
        "reason": "agent selected Grep when Read was needed; goal not met",
    }


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
    assert r["dimension"] == "quality"
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


@pytest.mark.parametrize("kind", StructuredOutputError.KINDS)
def test_score_session_unusable_judge_reply_fails_closed(kind):
    """An unusable judge reply must NOT produce a fabricated passing score — it
    fails closed (status=error, no goal/tool score trusted) and says which kind."""
    def judge(_p):
        raise StructuredOutputError(kind, "raw", "why")
    r = score_session(prompt="x", response="y", tool_names=[], judge_fn=judge)
    assert r.get("status") == "error" and r.get("goal_score") is None
    assert f"judge reply {kind}" in r["notes"]


def test_score_session_judge_receives_real_output():
    """The judge prompt MUST contain the real response + tool trajectory — this
    is what makes it evaluate ACTUAL behavior, not a static contract."""
    captured = {}

    def spy_judge(payload: str):
        captured["prompt"] = payload
        return {"goal_score": 0.9, "tool_score": 0.9, "dimension": "quality", "reason": "ok"}

    score_session(
        prompt="What is the fixed port?",
        response="Port 18321.",
        tool_names=["Read", "Grep"],
        judge_fn=spy_judge,
    )
    p = captured["prompt"]
    assert "18321" in p          # real response present
    assert "Read" in p and "Grep" in p  # real tool trajectory present


# ── Dimension ────────────────────────────────────────────────────────────────
# The judge names one of the three judged dimensions of the five-dimension model
# (cognition / quality / responsibility); its schema allows only those. An injected
# judge that names something else is read as quality, so the Online tab never shows
# a dimension outside them.

def _judge_emitting(dimension: str):
    def _j(_prompt: str) -> dict:
        return {"goal_score": 0.4, "tool_score": 0.5, "dimension": dimension, "reason": "x"}
    return _j


@pytest.mark.parametrize("token,dimension", [
    ("cognition", "cognition"), ("quality", "quality"), ("responsibility", "responsibility"),
    ("capability", "quality"), ("totally_bogus", "quality"), ("", "quality"),
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


def test_the_prompt_sha_also_names_the_answer_schema():
    # A calibration label counts only for the judge version that produced the score;
    # a changed schema is a changed judge.
    import hashlib
    import json
    from core import session_scorer as ss
    parts = [ss.JUDGE_PROMPT, ss.JUDGE_SYSTEM, json.dumps(ss.JUDGE_SCHEMA, sort_keys=True),
             ss.JUDGE_TOOL_NAME, ss.JUDGE_TOOL_DESCRIPTION, *sorted(ss._REASON_LANGUAGE.values())]
    assert ss.JUDGE_PROMPT_SHA == hashlib.sha256("\n".join(parts).encode()).hexdigest()[:12]


def test_the_default_judge_forces_the_score_tool(monkeypatch):
    import jobs.bedrock
    from core import session_scorer as ss
    seen = {}

    def fake(**kw):
        seen.update(kw)
        return {"stopReason": "tool_use", "output": {"message": {"content": [{"toolUse": {
            "name": "score", "input": {"goal_score": "0.5", "tool_score": 1, "dimension": "quality",
                                       "reason": "r"}}}]}}}
    monkeypatch.setattr(jobs.bedrock, "converse_with_retry", fake)
    got = ss.default_judge_fn("judge this")
    assert got["goal_score"] == 0.5
    assert seen["tool_config"]["toolChoice"] == {"tool": {"name": "score"}}
    assert seen["tool_config"]["tools"][0]["toolSpec"]["inputSchema"]["json"] is ss.JUDGE_SCHEMA
    assert seen["inference_config"]["maxTokens"] == ss.JUDGE_MAX_TOKENS
    assert seen["read_timeout"] == ss.JUDGE_READ_TIMEOUT == 30
    assert seen["tool_config"]["tools"][0]["toolSpec"]["description"] == ss.JUDGE_TOOL_DESCRIPTION


@pytest.mark.parametrize("answer", ['{"goal_score": 1, "tool_score": 1}', None, ["x"]])
def test_an_answer_that_is_not_an_object_fails_closed(answer):
    r = score_session(prompt="x", response="y", tool_names=[], judge_fn=lambda p: answer)
    assert r["status"] == "error" and r["goal_score"] is None


@pytest.mark.parametrize("v", [float("nan"), float("inf"), "nan"])
def test_a_non_finite_score_fails_closed(v):
    r = score_session(prompt="x", response="y", tool_names=[],
                      judge_fn=lambda p: {"goal_score": v, "tool_score": 0.5,
                                          "dimension": "quality", "reason": "r"})
    assert r["status"] == "error" and r["goal_score"] is None
