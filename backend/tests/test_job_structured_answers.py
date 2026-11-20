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
    ("content_filtered", None, "filtered"),
])
def test_an_unusable_reply_raises(converse, name, call, answer, stop, mangle, kind):
    set_reply, _seen = converse
    # a filtered reply carries no content at all (Bedrock: stopReason content_filtered)
    set_reply({"stopReason": stop, "output": {"message": {"content": []}}} if mangle is None
              else _tool(name, mangle(answer), stop=stop))
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
