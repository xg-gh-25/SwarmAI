"""The background jobs that ask a model for a structured answer do it through
jobs.bedrock.structured() (design docs/design/2026-10-28-structured-model-output.md):
each forces its own tool and schema, records token usage, and a reply that is not a
usable answer raises instead of reaching the job as text or a half-parsed dict.

Mock boundary: jobs.bedrock.converse_with_retry (the Converse call) and the usage
recorder.
"""
from __future__ import annotations

import pytest

import jobs.bedrock as jb
from jobs.bedrock import StructuredOutputError

MH_EMPTY = {"stale_memories": [], "resolved_threads": [], "archived_capabilities": [],
            "stale_decisions": [], "ddd_staleness": [], "capability_gaps": [],
            "stale_corrections": [], "summary": "fine"}
SP_OK = {"skill_name": "s_x", "skill_md": "---\nname: s_x\n---\nbody", "trigger_patterns": ["x"],
         "confidence": 8, "reasoning": "r"}


def _mh():
    from jobs.handlers import memory_health as m
    return m._call_llm("prompt")


def _sp():
    from jobs.handlers import skill_proposer as m
    return m._generate_skill_proposal({"pattern": "p", "evidence": []}, {})


CASES = [
    # ddd_refresh asks no model since CDF stage B: it hands commits to S4 (s4_code).
    ("report", _mh, MH_EMPTY),
    ("proposal", _sp, SP_OK),
]


@pytest.fixture
def converse(monkeypatch):
    seen = []
    from jobs.handlers import skill_proposer
    monkeypatch.setattr(skill_proposer, "_build_prompt", lambda g, c: "prompt")

    def _set(reply):
        def fake(**kw):
            seen.append(kw)
            return reply
        monkeypatch.setattr(jb, "converse_with_retry", fake)
    return _set, seen


def _tool(name, answer, stop="tool_use"):
    return {"stopReason": stop, "usage": {"inputTokens": 7, "outputTokens": 3},
            "output": {"message": {"content": [{"toolUse": {"name": name, "input": answer}}]}}}


@pytest.mark.parametrize("name, call, answer", CASES)
def test_each_job_gets_its_answer_through_its_own_tool(converse, name, call, answer):
    set_reply, seen = converse
    set_reply(_tool(name, answer))
    assert call() == answer
    (kw,) = seen
    assert kw["tool_config"]["toolChoice"] == {"tool": {"name": name}}
    # the call's spend reaches the usage ledger under the job's own feature
    assert kw["feature"].startswith("job.")


@pytest.mark.parametrize("name, call, answer", CASES)
@pytest.mark.parametrize("stop, mangle, kind", [
    ("max_tokens", lambda a: {}, "truncated"),
    ("tool_use", lambda a: {k: v for k, v in a.items() if k != next(iter(a))}, "invalid"),
])
def test_an_unusable_reply_raises(converse, name, call, answer, stop, mangle, kind):
    set_reply, _seen = converse
    set_reply(_tool(name, mangle(answer), stop=stop))
    with pytest.raises(StructuredOutputError) as e:
        call()
    assert e.value.kind == kind


def test_memory_health_refuses_an_action_missing_its_key_field(converse):
    # The report drives edits to MEMORY.md: a stale entry named without its prefix is
    # refused whole, so nothing is changed.
    set_reply, _seen = converse
    set_reply(_tool("report", {**MH_EMPTY, "stale_memories": [{"section": "S", "reason": "r"}]}))
    with pytest.raises(StructuredOutputError, match="entry_prefix"):
        _mh()


# ── signal_digest: a markdown call (text) and a scores call (structured) ─────

def _signals(n=3):
    from jobs.models import RawSignal
    return [RawSignal(feed_id="f", title=f"Signal {i}", url=f"https://x/{i}", source="src")
            for i in range(n)]


def _digest_converse(monkeypatch, scores_reply, md_stop="end_turn"):
    seen = []

    def fake(**kw):
        seen.append(kw)
        if kw.get("tool_config"):
            return scores_reply
        return {"stopReason": md_stop, "usage": {"inputTokens": 10, "outputTokens": 20},
                "output": {"message": {"content": [{"text": "## ⚡ TL;DR\n- one"}]}}}
    monkeypatch.setattr(jb, "converse_with_retry", fake)
    return seen


def test_signal_scores_come_through_the_scores_tool(monkeypatch):
    from jobs.handlers import signal_digest as sd
    seen = _digest_converse(monkeypatch, _tool("scores", {"scores": [
        {"idx": 1, "relevance_score": "0.9", "urgency": "high", "summary": "s"},
        {"idx": 99, "relevance_score": 0.5}]}))
    md, items, tokens = sd._llm_digest(_signals(), "ctx")
    assert md.startswith("## ⚡ TL;DR")
    assert [i["title"] for i in items] == ["Signal 1"]
    assert tokens == 10 + 20 + 7 + 3
    assert [kw.get("tool_config") is not None for kw in seen] == [False, True]


def test_unusable_scores_fall_back_to_keyword_scoring(monkeypatch):
    from jobs.handlers import signal_digest as sd
    _digest_converse(monkeypatch, _tool("scores", {}, stop="max_tokens"))
    with pytest.MonkeyPatch.context() as m:
        m.setattr(sd, "_keyword_scored_items", lambda signals, ctx: [{"title": "kw"}])
        _md, items, _t = sd._llm_digest(_signals(), "ctx")
    assert items == [{"title": "kw"}]


def test_a_cut_markdown_digest_is_replaced_and_its_tokens_still_count(monkeypatch):
    # A cut digest is never returned as complete; the tokens it spent still reach the
    # job's token count, and the scores call still runs.
    from jobs.handlers import signal_digest as sd
    _digest_converse(monkeypatch, _tool("scores", {"scores": [
        {"idx": 0, "relevance_score": 0.8}]}), md_stop="max_tokens")
    md, items, tokens = sd._llm_digest(_signals(), "ctx")
    assert md == sd._simple_digest(_signals())
    assert [i["title"] for i in items] == ["Signal 0"]
    assert tokens == 10 + 20 + 7 + 3


def test_a_cut_rollup_is_replaced_and_its_tokens_still_count(monkeypatch):
    from jobs.handlers import signal_digest as sd
    _digest_converse(monkeypatch, None, md_stop="max_tokens")
    md, tokens = sd._llm_rollup("day one\n\nday two", "ctx", 7)
    assert md == sd._concatenated_rollup("day one\n\nday two", 7)
    assert tokens == 10 + 20
