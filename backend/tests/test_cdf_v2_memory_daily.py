"""CDF v2 DailyActivity generation invariants.

Tests complete transcript paging, semantic completeness, stable Next identity,
and explicit resolution admission without an LLM or live workspace.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from core.daily_activity_writer import _format_session_entry
from core.session_hooks import HookContext
from core.summarization import StructuredSummary, SummarizationPipeline
from hooks.daily_activity_hook import _read_complete_transcript


def _context() -> HookContext:
    return HookContext(
        session_id="session-full-identity",
        agent_id="default",
        message_count=3,
        session_start_time="2026-10-27T00:00:00+00:00",
        session_title="完整活动记录",
    )


@pytest.mark.asyncio
async def test_complete_transcript_reads_every_page_in_order():
    newest = [{"id": f"n{i}", "role": "user", "content": str(i)} for i in range(200)]
    older = [{"id": "old", "role": "user", "content": "oldest"}]
    reader = AsyncMock(side_effect=[newest, older])
    with patch("hooks.daily_activity_hook.db") as mocked:
        mocked.messages.list_by_session_paginated = reader
        result = await _read_complete_transcript("session")
    assert result[0]["id"] == "old" and result[-1]["id"] == "n199"
    assert reader.await_args_list[1].kwargs["before_id"] == "n0"


def test_no_semantic_cut_and_stable_next_ids():
    pipeline = SummarizationPipeline()
    long_text = "完整语义" * 100 + "。"
    assert pipeline._extract_topics([{"role": "user", "content": long_text}]) == [long_text]
    summary = StructuredSummary(next_items=[{"text": long_text}])
    pipeline._assign_next_ids(summary, "session-a")
    first = summary.next_items[0]["next_id"]
    summary.next_items = [{"text": long_text}]
    pipeline._assign_next_ids(summary, "session-a")
    assert summary.next_items[0]["next_id"] == first


def test_render_has_only_required_sections_and_full_identity():
    summary = StructuredSummary(
        summary_sentence="本次完整记录了交付、输出、验证和下一步，没有截断任何语义字段。",
        deliverables=["完成完整记录"],
        key_outputs=["file: /work/result.md"],
        validation_status="targeted tests passed",
        next_items=[{"next_id": "next-1", "text": "继续完整验证"}],
        topics=["the user's own words must not render"],
        session_title="活动记录",
        timestamp="13:00",
    )
    rendered = _format_session_entry(summary, _context())
    assert "session-full-identity" in rendered
    assert "Summary: 本次完整记录" in rendered
    assert all(section in rendered for section in (
        "**Delivered:**", "**Outputs:**", "**Validation:**", "**Next:**"
    ))
    assert "must not render" not in rendered
    assert "[next-1] 继续完整验证" in rendered


@pytest.mark.asyncio
async def test_resolves_accepts_only_ids_presented_to_generator(monkeypatch):
    pipeline = SummarizationPipeline()
    enriched = {
        "summary_sentence": "本次明确完成了前一会话留下的验证工作，并保留了完整的完成证据。",
        "resolves": ["open-1", "invented"],
        "next_items": [],
    }

    async def fake_enrich(summary, messages, *, open_next_items, **_kwargs):
        summary.summary_sentence = enriched["summary_sentence"]
        summary.resolves = enriched["resolves"]
        return summary

    monkeypatch.setattr(pipeline, "_enrich_with_llm", fake_enrich)
    messages = [{"role": "user", "content": "完成 open-1"}] * 9
    result = await pipeline.summarize(
        messages,
        session_id="session-b",
        open_next_items=[{"next_id": "open-1", "text": "验证工作"}],
    )
    assert result.resolves == ["open-1"]


def test_delivered_lists_only_deliverables_never_topics_or_decisions():
    summary = StructuredSummary(topics=["看看库为什么膨胀"], decisions=["decided to look"],
                                session_title="库膨胀排查", timestamp="10:00")
    rendered = _format_session_entry(summary, _context())
    delivered = rendered.split("**Delivered:**", 1)[1].split("**Outputs:**", 1)[0]
    assert delivered.strip() == "- None recorded"
    assert "Summary:" not in rendered          # no sentence: no line, never the user's words


@pytest.mark.asyncio
async def test_a_model_sentence_outside_the_old_length_window_is_kept(monkeypatch):
    pipeline = SummarizationPipeline()
    long = "本次把库膨胀的两个根因修掉了，一个是旧 S2 每次空闲都从头重读整个会话，另一个是每次写入都保留整份文件副本。"

    async def fake_enrich(summary, messages, **_kwargs):
        summary.summary_sentence = long + "\n"
        return summary
    monkeypatch.setattr(pipeline, "_enrich_with_llm", fake_enrich)
    result = await pipeline.summarize([{"role": "user", "content": "修库"}] * 9, session_id="s")
    assert result.summary_sentence == long