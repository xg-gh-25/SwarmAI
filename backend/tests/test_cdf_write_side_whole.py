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
