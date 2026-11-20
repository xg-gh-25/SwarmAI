"""Inbox daily trend summary: one tool-less model call over the last 24 h of trend items.

Design: docs/design/2026-11-20-inbox.md §6.5. STUB: the signature below is the contract the
runner calls; the implementation replaces the body.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from core.inbox.models import Message


@dataclass(frozen=True)
class TrendPointData:
    title: str
    why: str
    source_label: str
    external_id: Optional[str] = None  # the trend item it points at, when it is one item


async def summarize(items: list[Message], user_profile: str) -> list[TrendPointData]:
    """Pick the 3-5 items worth the person's time today, each with one sentence of why."""
    raise NotImplementedError
