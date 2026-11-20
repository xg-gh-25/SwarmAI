"""Shared fakes for the Inbox core tests (not a test module).

``FakeSource`` implements the ``core.inbox.sources.base.Source`` protocol in memory and
counts every call; ``install`` registers it as the factory of kind ``fake``; ``clear``
empties the ``inbox_*`` tables of the suite's test database (the conftest reset does not
know them); ``add_source`` writes one source row.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

from core.inbox import store
from core.inbox.models import (
    Identity,
    Message,
    PullResult,
    ReplyTarget,
    SendReceipt,
    SourceCategory,
    Thread,
    ThreadRef,
)
from core.inbox.sources import base

INBOX_TABLES = ("inbox_sources", "inbox_items", "inbox_drafts", "inbox_sends",
                "inbox_feedback", "inbox_trend_summaries")

ME = Identity(ids=frozenset({"U_ME", "me@example.com"}), display="@me")
T0 = datetime(2026, 11, 20, 1, 0, tzinfo=timezone.utc)


def ref(n: int = 1, kind: str = "fake") -> ThreadRef:
    return ThreadRef(kind, {"thread": f"t{n}"})


def msg(external_id: str, *, thread: int = 1, author: str = "U_PRIYA", minutes: int = 0,
        text: str = "can you review this?", direct: bool = True) -> Message:
    return Message(external_id=external_id, thread=ref(thread), author_id=author,
                   author_name=author.lower(), received_at=T0 + timedelta(minutes=minutes),
                   text=text, title=f"title {external_id}", is_direct=direct)


@dataclass
class FakeSource:
    kind = "fake"
    category = SourceCategory.CONVERSATION
    READ_TOOLS = frozenset({"read"})

    me: Identity = ME
    messages: list = field(default_factory=list)
    cursor: Optional[str] = "c1"
    target: Optional[ReplyTarget] = field(default_factory=lambda: ReplyTarget(
        label="Priya", recipients=("priya@example.com",)))
    sendable: bool = True
    thread_messages: list = field(default_factory=list)
    reply_error: Optional[BaseException] = None
    pull_error: Optional[BaseException] = None
    found: Optional[SendReceipt] = None
    calls: dict = field(default_factory=lambda: {"reply": 0, "pull": 0, "thread": 0,
                                                 "find_sent": 0, "close": 0, "whoami": 0})
    replied: list = field(default_factory=list)

    async def whoami(self) -> Identity:
        self.calls["whoami"] += 1
        return self.me

    async def pull(self, cursor):
        self.calls["pull"] += 1
        if self.pull_error is not None:
            raise self.pull_error
        return PullResult(messages=tuple(self.messages), cursor=self.cursor)

    async def thread(self, r: ThreadRef) -> Thread:
        self.calls["thread"] += 1
        return Thread(ref=r, messages=tuple(self.thread_messages), target=self.target)

    async def can_send(self) -> bool:
        return self.sendable

    async def reply(self, r: ThreadRef, text: str) -> SendReceipt:
        self.calls["reply"] += 1
        self.replied.append((r, text))
        if self.reply_error is not None:
            raise self.reply_error
        return SendReceipt(external_msg_id=f"m{self.calls['reply']}", url="https://x.example/m")

    async def find_sent(self, r: ThreadRef, after: datetime, text: str):
        self.calls["find_sent"] += 1
        return self.found

    async def close(self) -> None:
        self.calls["close"] += 1


def install(fake: FakeSource, kind: str = "fake") -> FakeSource:
    store._loaded = True   # no real connector module is imported by these tests
    base.register_source(kind, lambda cfg: fake)
    return fake


async def clear() -> None:
    import aiosqlite
    from database import db
    from database.sqlite import close_all_pools
    await db.initialize()
    await close_all_pools()
    async with aiosqlite.connect(str(db.db_path)) as conn:
        await conn.execute("PRAGMA busy_timeout=5000")
        await conn.executescript(";\n".join(f"DELETE FROM {t}" for t in INBOX_TABLES))


async def add_source(source_id: str = "s1", kind: str = "fake", *,
                     category: str = "conversation", health: str = "ok",
                     can_send: int = 1, interval_s: int = 900, config: Optional[dict] = None) -> dict:
    from database import db
    stamp = store.ts(store.now())
    await db.inbox_put_source({
        "id": source_id, "kind": kind, "backend": "test", "category": category,
        "config": json.dumps(config or {}), "enabled": 1, "interval_s": interval_s,
        "cursor": None, "me": store.identity_to_json(ME), "can_send": can_send,
        "health": "ok", "fix": "", "last_pull_at": None, "last_attempt_at": None,
        "last_error": "", "created_at": stamp, "updated_at": stamp})
    if health != "ok":
        await db.inbox_update_source(source_id, stamp, health=health)
    return await db.inbox_get_source(source_id)


async def add_item(source_id: str = "s1", external_id: str = "e1", *, lane: str = "reply",
                   state: str = "open", thread: int = 1) -> dict:
    from core.inbox.models import ItemState, Lane
    from database import db
    row = store.message_to_item_row(source_id, msg(external_id, thread=thread), Lane(lane),
                                    None, state=ItemState(state))
    stored = await db.inbox_insert_items([row])
    return await db.inbox_get_item(stored[external_id][0])


def app():
    """A FastAPI app with only the Inbox router (and the Community one, for the feed
    parity test): no lifespan, no runner."""
    from fastapi import FastAPI
    from routers.community_api import router as community_router
    from routers.inbox import router as inbox_router
    application = FastAPI()
    application.include_router(inbox_router)
    application.include_router(community_router)
    return application


def client():
    from httpx import ASGITransport, AsyncClient
    return AsyncClient(transport=ASGITransport(app=app()), base_url="http://127.0.0.1:18321")


UA = {"X-Swarm-User-Action": "send"}
