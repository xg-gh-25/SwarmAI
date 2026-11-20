"""Inbox domain model: the shared contract every Inbox part builds on.

Design: ``docs/design/2026-11-20-inbox.md``. The Inbox pulls the person's own messages from
pluggable sources (Slack, mail, GitHub, feeds), triages them into lanes, drafts replies and
sends a reply only on the person's click in the desktop UI.

This module holds plain data only: no I/O, no database, no model calls. The store
(``core/inbox/store.py``), the sources (``core/inbox/sources/``), triage and drafting
(``core/inbox/triage.py``, ``core/inbox/drafts.py``) and the API (``routers/inbox.py``,
``schemas/inbox.py``) all exchange these types, so they can be built and tested apart.

Key public symbols:

- ``Lane``, ``ItemState``, ``SendStatus``, ``SendOrigin``, ``SourceCategory``, ``SourceHealth``
- ``ThreadRef``   where a reply goes: the source's own address of a thread, opaque to callers
- ``Identity``    who the person is inside one source (ids, handles, mail aliases)
- ``Message``     one message as a source returns it (normalised)
- ``Thread``      a thread fetched fresh, with the resolved reply target
- ``ReplyTarget`` the resolved recipients of a reply and the flags a person must see
- ``PullResult``  what one ``pull`` returns: new messages and the next cursor
- ``SendReceipt`` what a source returns after sending, or after finding a sent reply
- ``Candidate``   a message the deterministic rules kept, handed to model triage
- ``TriageResult`` the model's verdict for one candidate
- ``target_sha``  the fingerprint the UI shows and the send endpoint re-checks
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Optional


class Lane(str, Enum):
    """Where an item shows. ``SKIP`` items are stored and counted, never listed."""

    REPLY = "reply"        # 等你回复: someone expects an answer from the person
    PROPOSED = "proposed"  # 代拟: a draft a job or skill submitted; sent only on a click
    AWARE = "aware"        # 需要知道: worth knowing, no answer expected
    TREND = "trend"        # 趋势: feed and watched-repo items
    SKIP = "skip"          # bots, bulk mail, calendar invites: counted only


class ItemState(str, Enum):
    OPEN = "open"
    REPLIED = "replied"      # sent from the Inbox, or the person replied elsewhere (seen on pull)
    DONE = "done"            # 知道了 / 已完成
    DISMISSED = "dismissed"  # 不用回 / 不发
    SNOOZED = "snoozed"      # 稍后: back to OPEN at ``snooze_until``


class SendStatus(str, Enum):
    RESERVED = "reserved"  # written before the source is called
    SENT = "sent"
    FAILED = "failed"      # the source said it did not send
    UNKNOWN = "unknown"    # timed out or crashed after reserving: never resent automatically


class SendOrigin(str, Enum):
    CLICK = "click"  # the person clicked 发送 in the desktop UI
    AUTO = "auto"    # a job or skill sent it itself and only recorded it (``inbox submit --sent``)


class SourceCategory(str, Enum):
    CONVERSATION = "conversation"  # can be replied to: Slack, mail, GitHub
    FEED = "feed"                  # read only: RSS, Hacker News, trending, watched repos


class SourceHealth(str, Enum):
    OK = "ok"
    AUTH_FAILED = "auth_failed"  # credentials expired: shown in Needs You with the fix
    ERROR = "error"              # anything else; retried at the next interval
    READ_ONLY = "read_only"      # can pull but cannot send (e.g. mail writes disabled)


@dataclass(frozen=True)
class ThreadRef:
    """The source's own address of a thread. Only the source that made it reads ``data``."""

    source_kind: str
    data: dict[str, Any]

    def key(self) -> str:
        """A stable key for grouping items of one thread."""
        return f"{self.source_kind}:{json.dumps(self.data, sort_keys=True, ensure_ascii=False)}"


@dataclass(frozen=True)
class Identity:
    """Who the person is in one source. ``ids`` holds every form a message may carry
    (user id, login, mail address and aliases); a message whose author is in ``ids`` is the
    person's own."""

    ids: frozenset[str]
    display: str = ""

    def is_me(self, author_id: str) -> bool:
        return author_id.strip().lower() in {i.strip().lower() for i in self.ids}


@dataclass(frozen=True)
class Message:
    """One message as a source returns it. ``external_id`` is assigned by the source and is
    the item's identity (one row per ``(source_id, external_id)``); never derive identity
    from text."""

    external_id: str
    thread: ThreadRef
    author_id: str
    author_name: str
    received_at: datetime
    text: str
    url: str = ""
    title: str = ""  # a mail subject, an issue title, a feed headline
    # Facts the deterministic rules read; a source fills what it knows.
    is_direct: bool = False        # a DM, or mail with the person in To
    mentions_me: bool = False      # @me, review requested, GitHub reason "mention"
    is_bot: bool = False           # bot, no-reply, automated sender
    is_bulk: bool = False          # list mail, the person only in Cc/Bcc, large DL
    is_invite: bool = False        # calendar invite
    watched: bool = False          # from a watched channel / repo
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ReplyTarget:
    """Who a reply would reach, resolved fresh from the source. ``label`` is what the UI shows
    after "回复到"; ``recipients`` are the concrete addresses; ``flags`` are warnings the
    person must see before sending (Reply-To differs from From, an address outside the
    person's domain, a public thread)."""

    label: str
    recipients: tuple[str, ...]
    public: bool = False
    flags: tuple[str, ...] = ()


@dataclass(frozen=True)
class Thread:
    """A thread fetched fresh: every message in order and the resolved reply target."""

    ref: ThreadRef
    messages: tuple[Message, ...]
    target: Optional[ReplyTarget]  # None when the source cannot reply here


@dataclass(frozen=True)
class PullResult:
    messages: tuple[Message, ...]
    cursor: Optional[str]  # stored and passed to the next pull; None keeps the old one


@dataclass(frozen=True)
class SendReceipt:
    external_msg_id: str
    url: str = ""
    sent_at: Optional[datetime] = None


@dataclass(frozen=True)
class Candidate:
    """A message the deterministic rules kept for model triage. ``id`` is issued by the
    system (the item row id) and is the only id the model may answer with."""

    id: str
    source_kind: str
    message: Message
    rule_lane: Lane            # the lane the rules suggest
    floor: Optional[Lane]      # the lowest lane the model may move it to (REPLY items: AWARE)
    thread_context: tuple[Message, ...] = ()


@dataclass(frozen=True)
class TriageResult:
    id: str
    lane: Lane
    priority: int   # 0 (highest) .. 3
    reason: str     # one sentence: why it matters to the person (shown as "要你做什么")
    reply_expected: bool


def target_sha(target: ReplyTarget, ref: ThreadRef) -> str:
    """The fingerprint of a reply target. The UI receives it with the target it shows; the
    send endpoint resolves the target again and refuses when the fingerprints differ."""
    payload = json.dumps({"ref": ref.key(), "target": asdict(target)}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
