"""Write-side producers keep semantic text whole (CDF first principle, design
docs/design/2026-10-26-swarm-cognitive-distillation-flow.md §5): a DailyActivity
capture, a distillation candidate, a backflow title and the backflow transcript are
whole or named, never cut. The structural scan is in test_cdf_v2_sources_no_truncation."""
from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _da(ws):
    return ws / "Knowledge" / "DailyActivity" / f"{datetime.now().strftime('%Y-%m-%d')}.md"


@pytest.mark.asyncio
async def test_high_signal_capture_keeps_the_whole_message(tmp_path):
    from core.runtime_hooks import create_high_signal_capture
    ws = tmp_path / "ws"
    hook = create_high_signal_capture({"sdk_session_id": "s1"}, workspace_dir=str(ws))
    prompt = "I decided to always use rebase " + "because the history stays linear " * 20 + "END"
    await hook({"prompt": prompt}, None, MagicMock())
    assert " ".join(prompt.split()) in _da(ws).read_text()


@pytest.mark.asyncio
async def test_a_too_long_high_signal_message_is_named_not_cut(tmp_path):
    from core.runtime_hooks import HIGH_SIGNAL_LINE_MAX, create_high_signal_capture
    ws = tmp_path / "ws"
    hook = create_high_signal_capture({"sdk_session_id": "s9"}, workspace_dir=str(ws))
    prompt = "I decided " + "word " * HIGH_SIGNAL_LINE_MAX
    await hook({"prompt": prompt}, None, MagicMock())
    text = _da(ws).read_text()
    assert "word word" not in text and "it is in session s9" in text


@pytest.mark.asyncio
async def test_two_messages_sharing_a_long_opening_are_both_captured(tmp_path):
    from core.runtime_hooks import create_high_signal_capture
    ws = tmp_path / "ws"
    hook = create_high_signal_capture({"sdk_session_id": "s2"}, workspace_dir=str(ws))
    head = "I decided " + "x" * 120
    await hook({"prompt": head + " ALPHA"}, None, MagicMock())
    await hook({"prompt": head + " BETA"}, None, MagicMock())
    text = _da(ws).read_text()
    assert "ALPHA" in text and "BETA" in text




def test_backflow_title_is_a_whole_line_or_the_default():
    from hooks.knowledge_backflow_hook import _TITLE_MAX, _extract_title
    assert _extract_title("A short first line\nbody") == "A short first line"
    assert _extract_title("x" * (_TITLE_MAX + 1) + "\nbody") == "Session Insight"


@pytest.mark.asyncio
async def test_backflow_reads_every_message_of_a_long_session():
    from hooks.knowledge_backflow_hook import KnowledgeBackflowHook
    msgs = [{"id": f"m{i:04d}", "role": "user", "content": f"msg {i}"} for i in range(650)]

    async def page(session_id, limit=None, before_id=None, **kw):
        end = len(msgs) if before_id is None else int(before_id[1:])
        return msgs[max(0, end - limit):end]
    seen = []
    hook = KnowledgeBackflowHook()
    ctx = MagicMock(); ctx.session_id = "long"
    with patch("hooks.knowledge_backflow_hook.db") as mock_db, \
         patch("hooks.knowledge_backflow_hook._is_high_value_output",
               side_effect=lambda c: seen.append(c) or False):
        mock_db.messages.list_by_session_paginated = AsyncMock(side_effect=page)
        for m in msgs:
            m["role"] = "assistant"
        await hook._execute_inner(ctx)
    assert "msg 0" in seen and "msg 649" in seen and len(seen) == 650
