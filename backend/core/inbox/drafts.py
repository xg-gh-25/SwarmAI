"""Inbox reply drafting: one tool-less model call per item.

Design: docs/design/2026-11-20-inbox.md §6.3. STUB: the signature below is the contract the
API calls; the implementation replaces the body.
"""
from __future__ import annotations

from dataclasses import dataclass

from core.inbox.models import Identity, Message, Thread


@dataclass(frozen=True)
class DraftText:
    text: str
    lang: str   # "zh", "en", ... the language of the thread
    note: str   # shown under the draft, e.g. "参考了你和周琳的 6 条往来"


async def make_draft(thread: Thread, me: Identity, history_with_counterparty: list[Message]) -> DraftText:
    """Draft a reply to ``thread``. The only context is the thread itself and the person's
    own recent messages to the same counterparty or thread; never another thread's text."""
    raise NotImplementedError
