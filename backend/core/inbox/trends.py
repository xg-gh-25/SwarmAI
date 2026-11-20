"""Inbox daily trend summary: one model call over the last 24 h of trend items whose only
tool is the answer tool.

Design: docs/design/2026-11-20-inbox.md §6.5. The runner calls ``summarize`` once a day, at
the first pull after 7:00 in the person's local time, with the trend lane's items.

The call is ``jobs.bedrock.structured`` (its one answer tool, only parsed). Each point names
its item by ``external_id`` through the schema's ``enum``, so a point always references an
item that was passed in; the title and label shown come from that item when the model gives
none. Item text sits in the user turn inside a nonce fence
(``core.inbox.prompts.ExternalFence``).

Key public symbols: ``TrendPointData``, ``summarize``, ``SummaryRejected``.
"""
from __future__ import annotations

import functools
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import urlparse

from core import executors
from core.inbox.models import Message
from core.inbox.prompts import TRENDS_SYSTEM, ExternalFence, clip, with_profile
from jobs import bedrock

FEATURE = "inbox.trends"        # the usage ledger's name for these calls
WINDOW = timedelta(hours=24)
MAX_POINTS = 5
MIN_POINTS = 3
MAX_ITEMS = 200                 # the newest items shown to the model; older ones are counted
ITEM_TEXT_LIMIT = 500
MAX_TOKENS = 2000


@dataclass(frozen=True)
class TrendPointData:
    title: str
    why: str
    source_label: str
    external_id: Optional[str] = None  # the trend item it points at, when it is one item


class SummaryRejected(Exception):
    """The model answer was unusable or gave fewer distinct points than asked for."""


async def summarize(items: list[Message], user_profile: str) -> list[TrendPointData]:
    """Pick the 3-5 items worth the person's time today, each with one sentence of why.

    Only items received in the last 24 hours count; with none, the result is ``[]`` and no
    model call is made. With fewer than three items, every point may be used. A repeated
    item or a point with an empty why is left out. An answer that is cut, filtered or
    off-schema, or that keeps fewer distinct points than that minimum, raises
    ``SummaryRejected``; a Bedrock error propagates unchanged."""
    since = datetime.now(timezone.utc) - WINDOW
    by_id: dict[str, Message] = {}
    for m in sorted(items, key=_ts, reverse=True):
        if _ts(m) >= since and m.external_id not in by_id:
            by_id[m.external_id] = m
    if not by_id:
        return []
    shown = list(by_id.values())[:MAX_ITEMS]
    hidden = len(by_id) - len(shown)
    call = functools.partial(_ask, _prompt(shown, hidden), schema=_schema([m.external_id for m in shown]),
                             system=with_profile(TRENDS_SYSTEM, user_profile))
    try:
        answer = await executors.run_in("llm", call)  # off the event loop and its default pool
    except bedrock.StructuredOutputError as exc:
        raise SummaryRejected(f"the model answer is not usable ({exc.kind})") from exc
    points: list[TrendPointData] = []
    used: set[str] = set()
    for p in answer.get("points") or []:
        item = by_id.get(p["external_id"])
        if item is None:      # the enum already refuses it; kept so no point can name a non-item
            raise SummaryRejected(f"a point names an item that was not given: {p['external_id']!r}")
        why = " ".join(str(p.get("why") or "").split())
        if item.external_id in used or not why:
            continue          # a repeat, or a point without its one sentence of why
        used.add(item.external_id)
        points.append(TrendPointData(
            title=" ".join(str(p.get("title") or "").split()) or " ".join(item.title.split()),
            why=why,
            source_label=_source_label(item),
            external_id=item.external_id,
        ))
        if len(points) == MAX_POINTS:
            break
    if len(points) < min(MIN_POINTS, len(shown)):
        raise SummaryRejected(f"{len(points)} distinct points, at least {min(MIN_POINTS, len(shown))} needed")
    return points


def _ask(prompt: str, *, schema: dict, system: str) -> dict:
    """The one model call (blocking: run it on the 'llm' pool); the answer is parsed only."""
    return bedrock.structured(prompt, schema=schema, model_id=bedrock.get_model_id(),
                              max_tokens=MAX_TOKENS, system=system,
                              description="Record the picked items.", feature=FEATURE)


def _ts(m: Message) -> datetime:
    dt = m.received_at
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def _source_label(m: Message) -> str:
    """The label shown next to a point: the source's own label when it set one
    (``extra["source_label"]``), else the feed type of its kind ("feed:rss" -> "rss"),
    else the host of its link."""
    label = m.extra.get("source_label") if isinstance(m.extra, dict) else None
    if isinstance(label, str) and label.strip():
        return label.strip()
    kind = m.thread.source_kind
    if kind.startswith("feed:") and kind[5:]:
        return kind[5:]
    return urlparse(m.url).hostname or kind


def _schema(ids: list[str]) -> dict:
    return {
        "type": "object",
        "properties": {
            "points": {
                "type": "array",
                "description": "3 to 5 picks, most worth the person's time first, each item at most once.",
                "items": {
                    "type": "object",
                    "properties": {
                        "external_id": {"type": "string", "enum": list(ids)},
                        "title": {"type": "string"},
                        "why": {"type": "string"},
                    },
                    "required": ["external_id", "title", "why"],
                },
            },
        },
        "required": ["points"],
    }


def _prompt(items: list[Message], hidden: int) -> str:
    texts = [t for m in items for t in (m.external_id, m.title, m.text, m.url, _source_label(m))]
    fence = ExternalFence.for_texts(texts)
    blocks = []
    for m in items:
        lines = [f"<item-{fence.nonce}>", f"external_id: {m.external_id}",
                 f"source: {_source_label(m)}", f"received: {m.received_at.isoformat()}"]
        if m.title:
            lines.append(f"title: {clip(m.title, 300)}")
        if m.url:
            lines.append(f"url: {m.url}")
        if m.text:
            lines.append(f"text: {clip(m.text, ITEM_TEXT_LIMIT)}")
        lines.append(f"</item-{fence.nonce}>")
        blocks.append("\n".join(lines))
    lo, hi = min(MIN_POINTS, len(items)), min(MAX_POINTS, len(items))
    want = str(lo) if lo == hi else f"{lo} to {hi}"
    parts = [f"Pick {want} of these {len(items)} items for today, then answer with the answer tool.",
             fence.preamble()]
    if hidden:
        parts.append(f"({hidden} older items of the same day are not shown.)")
    parts.append("Items, newest first:\n" + fence.wrap("\n\n".join(blocks)))
    return "\n\n".join(parts)
