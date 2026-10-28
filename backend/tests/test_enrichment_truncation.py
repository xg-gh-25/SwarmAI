"""Session enrichment asks the model through jobs.bedrock.structured() (design
docs/design/2026-10-28-structured-model-output.md). Properties checked:
- a complete answer reaches the summary normalized;
- a reply cut at max_tokens, with no answer or off-schema, leaves the rule-based
  summary unchanged and logs which kind it was (the old text parser dropped a cut
  reply without a line, and its 13 recorded failures were unescaped strings);
- the output budget and read timeout fit a full-length generation and the 180 s
  hook budget.

Mock boundary: the bedrock-runtime client (jobs.bedrock.build_timeout_client).
"""

import asyncio
import logging
from unittest.mock import patch

from core import summarization as S
from core.summarization import StructuredSummary, SummarizationPipeline

FULL = {
    "summary_sentence": "本次修复了召回截断，并验证了结果。",
    "next_items": [], "resolves": [],
    "deliverables": ["修复召回截断", "Fixed Y"],
    "key_outputs": ["code: a.py → b"],
    "lessons": ["先量再改", "Never trust char counts"],
    "lesson_types": ["guideline", "pitfall"],
    "rejected_approaches": [],
    "corrections": [],
    "process_reflection": "",
    "signal_driven_actions": [],
    "continue_from": "next",
    "validation_status": "tests green",
    "coe_signal": "",
    "coe_topic": "",
}


class _Client:
    def __init__(self, reply):
        self.reply, self.calls = reply, []

    def converse(self, **kw):
        self.calls.append(kw)
        return self.reply


def _tool(answer, stop="tool_use"):
    return {"stopReason": stop, "output": {"message": {"content": [
        {"toolUse": {"toolUseId": "t", "name": "summary", "input": answer}}]}}}


def _enrich(reply):
    client = _Client(reply)
    summary = StructuredSummary(topics=["t"], decisions=[], files_modified=[],
                                open_questions=[], session_title="t", timestamp="10:00")
    msgs = [{"role": "user", "content": "fix the recall"}, {"role": "assistant", "content": "done"}]

    async def _run_in(_pool, fn, *a):
        return fn(*a)
    with patch("jobs.bedrock.build_timeout_client", return_value=client), \
         patch("core.executors.run_in", _run_in):
        out = asyncio.run(SummarizationPipeline()._enrich_with_llm(summary, msgs))
    return out, client


def test_a_complete_answer_reaches_the_summary():
    out, client = _enrich(_tool(FULL))
    assert out.deliverables == ["修复召回截断", "Fixed Y"]
    assert list(zip(out.lessons, out.lesson_types)) == [("先量再改", "guideline"),
                                                        ("Never trust char counts", "pitfall")]
    assert out.validation_status == "tests green"
    (kw,) = client.calls
    assert kw["toolConfig"]["toolChoice"] == {"tool": {"name": "summary"}}
    assert kw["inferenceConfig"]["maxTokens"] == S._ENRICHMENT_MAX_TOKENS


def test_a_cut_reply_keeps_the_rule_based_summary_and_says_so(caplog):
    with caplog.at_level(logging.WARNING, logger="core.summarization"):
        out, _ = _enrich(_tool({}, stop="max_tokens"))
    assert out.deliverables == [] and out.lessons == []
    assert "Enrichment reply truncated" in caplog.text


def test_an_answer_off_its_schema_keeps_the_rule_based_summary(caplog):
    with caplog.at_level(logging.WARNING, logger="core.summarization"):
        out, _ = _enrich(_tool({**FULL, "lessons": "not a list"}))
    assert out.lessons == [] and "Enrichment reply invalid" in caplog.text


def test_a_text_reply_is_missing(caplog):
    reply = {"stopReason": "end_turn", "output": {"message": {"content": [{"text": "{\"deliverables\": [\"x\"]}"}]}}}
    with caplog.at_level(logging.WARNING, logger="core.summarization"):
        out, _ = _enrich(reply)
    assert out.deliverables == [] and "Enrichment reply missing" in caplog.text


def test_budget_fits_a_full_generation():
    assert S._ENRICHMENT_MAX_TOKENS >= 2048
    # The reply is not streamed: the read waits for the whole output.
    assert 60 <= S._ENRICHMENT_READ_TIMEOUT_S < 180
