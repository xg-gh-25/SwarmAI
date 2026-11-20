"""Inbox daily trend summary (design 2026-11-20-inbox §6.5): 3-5 points, each referencing
an item that was passed in, over the last 24 hours only."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from core.inbox.models import Message, ThreadRef
from core.inbox.trends import SummaryRejected, summarize
from tests.inbox_model_fake import FakeBedrock, tool_reply

NOW = datetime.now(timezone.utc)


def item(eid: str, hours: float = 1, kind: str = "feed:rss", **kw) -> Message:
    return Message(external_id=eid, thread=ThreadRef(kind, {"id": eid}), author_id="feed",
                   author_name="feed", received_at=NOW - timedelta(hours=hours),
                   text=f"body of {eid}", title=f"Title {eid}", url=f"https://news.example.org/{eid}", **kw)


def points(*ids) -> dict:
    return tool_reply({"points": [{"external_id": i, "title": f"T {i}", "why": f"why {i}"} for i in ids]})


async def test_summarize_returns_3_to_5_points_referencing_real_items(monkeypatch):
    items = [item(f"e{i}") for i in range(6)] + [item("old", hours=30)]
    items[0] = item("e0", extra={"source_label": "GitHub Trending"})
    fake = FakeBedrock(monkeypatch, points("e2", "e0", "e2", "e5", "e1"))
    out = await summarize(items, "I build agent runtimes.")
    assert [p.external_id for p in out] == ["e2", "e0", "e5", "e1"]      # the repeat is dropped
    assert {p.source_label for p in out} == {"GitHub Trending", "rss"}
    assert all(p.why and p.title for p in out)
    enum = fake.tool_config["tools"][0]["toolSpec"]["inputSchema"]["json"]["properties"]["points"]["items"]["properties"]["external_id"]["enum"]
    assert "old" not in enum and set(enum) == {f"e{i}" for i in range(6)}
    assert fake.calls[0]["feature"] == "inbox.trends"
    assert "body of e3" not in fake.system and "I build agent runtimes." in fake.system


async def test_summarize_keeps_at_most_five_points(monkeypatch):
    FakeBedrock(monkeypatch, points(*[f"e{i}" for i in range(7)]))
    out = await summarize([item(f"e{i}") for i in range(7)], "")
    assert len(out) == 5


@pytest.mark.parametrize("ids", [("e0", "e1", "nope"), ("e0", "e0", "e1")], ids=["unknown", "too-few"])
async def test_summarize_rejects_unknown_item(monkeypatch, ids):
    FakeBedrock(monkeypatch, points(*ids))
    with pytest.raises(SummaryRejected):
        await summarize([item(f"e{i}") for i in range(4)], "")


async def test_summarize_without_recent_items_makes_no_call(monkeypatch):
    fake = FakeBedrock(monkeypatch, points("x"))
    assert await summarize([item("old", hours=25)], "") == []
    assert await summarize([], "") == []
    assert fake.calls == []


async def test_summarize_drops_points_without_a_why(monkeypatch):
    FakeBedrock(monkeypatch, tool_reply({"points": [
        {"external_id": "e0", "title": "T", "why": "  "},
        *[{"external_id": f"e{i}", "title": "T", "why": "matters"} for i in (1, 2, 3)]]}))
    out = await summarize([item(f"e{i}") for i in range(4)], "")
    assert [p.external_id for p in out] == ["e1", "e2", "e3"]
