"""Inbox HTTP API contract (``/api/inbox``), shared by the router and the desktop client.

Design: ``docs/design/2026-11-20-inbox.md`` §7-§8; the desktop mirror of these shapes is
``desktop/src/services/inbox.ts``. Every route below is served by ``routers/inbox.py``.

Routes:

- ``GET  /api/inbox/counts``                    -> ``InboxCounts``   (tab counts and the nav badge)
- ``GET  /api/inbox/items?lane=reply|proposed|aware|trend`` -> ``list[ItemOut]`` (open items, most urgent first)
- ``GET  /api/inbox/items/{id}/target``         -> ``TargetOut``     (resolved fresh from the source)
- ``GET  /api/inbox/items/{id}/draft``          -> ``DraftOut``      (the latest draft; made on first read)
- ``POST /api/inbox/items/{id}/redraft``        -> ``DraftOut``
- ``POST /api/inbox/items/{id}/send``  ``SendRequest`` -> ``SendOut`` (403 caller is not the desktop UI;
  409 ``target_changed`` / ``redacted`` / ``not_open``; the only route that sends)
- ``POST /api/inbox/items/{id}/action`` ``ItemActionRequest`` -> ``ItemOut`` (later / dismiss / done)
- ``GET  /api/inbox/sends?filter=all|mine|auto`` -> ``SendsOut``     (needs-you first, then sent by day)
- ``POST /api/inbox/sends/{id}/not-sent``       -> ``SendOut``       (the person confirms an unknown send failed)
- ``POST /api/inbox/sends/{id}/resend``         -> ``SendOut``       (only a failed send whose source can send)
- ``GET  /api/inbox/trends/summary``            -> ``TrendSummaryOut``
- ``GET  /api/inbox/sources``                   -> ``list[SourceOut]``
- ``GET  /api/inbox/sources/available``         -> ``list[AvailableSourceOut]`` (what this machine can
  connect without typing anything: a configured MCP server whose tool list matches a shipped
  mapping, ``gh`` when it is logged in; ``?refresh=true`` probes again; needs
  ``X-Swarm-User-Action``, since it starts the servers)
- ``POST /api/inbox/sources``  ``SourceCreateRequest`` -> ``SourceOut`` (stored only after the
  source's ``whoami`` succeeded; 400 ``whoami_failed`` with ``fix``, 409 ``already_added``,
  422 ``invalid_source``: ``SourceRefusalOut``, nothing stored)
- ``PATCH /api/inbox/sources/{id}`` ``SourcePatchRequest`` -> ``SourceOut``
- ``DELETE /api/inbox/sources/{id}``            -> ``SourceDeletedOut`` (the row, its items, drafts,
  sends, feedback and secrets; 409 ``send_in_flight``, 422 ``feed_source``)
- ``POST /api/inbox/sources/{id}/secret`` ``SecretRequest`` -> ``SourceOut`` (``slack-web`` /
  ``imap``; the value is never returned or logged)
- ``POST /api/inbox/sources/{id}/retry``        -> ``SourceOut``
- ``POST /api/inbox/refresh``                   -> ``InboxCounts`` (pull every due source now)
- feed configuration moves here from ``/api/community/feeds`` unchanged in shape:
  ``GET/POST /api/inbox/feeds``, ``PUT/DELETE /api/inbox/feeds/{id}``,
  ``POST/DELETE /api/inbox/feeds/{id}/members``. ``GET /api/inbox/feeds`` -> ``FeedsOut``.

**Writes carry ``X-Swarm-User-Action``.** Every POST, PATCH and DELETE above is refused with
403 without it (a web page cannot set that header), except the feed routes, which keep the
exact behaviour of ``/api/community/feeds`` until the Community page is deleted (the desktop
sends the header there too). The three routes that send or unlock a send, and the source
writes (add, change, remove, store a secret), also pass the caller check
(``core/inbox/caller.py``).

**Errors** use FastAPI's envelope ``{"detail": ...}``. The 403/409 of ``/send``,
``/not-sent`` and ``/resend`` is ``SendRefusalBody`` (``detail`` is a ``SendRefusalOut``);
a refused source write is ``SourceRefusalBody``.

Times are ISO-8601 strings with offset. Ids are strings.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field, SecretStr

LaneName = Literal["reply", "proposed", "aware", "trend"]
SendStatusName = Literal["reserved", "sent", "failed", "unknown"]
SourceHealthName = Literal["ok", "auth_failed", "error", "read_only"]


class InboxCounts(BaseModel):
    reply: int = 0               # open items in REPLY (the nav badge and the first tab's count)
    proposed: int = 0
    aware: int = 0
    trend: int = 0
    sends_need_you: int = 0      # failed + unknown sends: the red dot on the 已发 tab
    skipped: int = 0             # SKIP items in the last 24 h ("另有 N 条没显示")
    unhealthy_sources: list[str] = Field(default_factory=list)  # source ids not ok
    updated_at: Optional[str] = None  # the latest successful pull of any source


class ItemOut(BaseModel):
    id: str
    lane: LaneName
    state: Literal["open", "replied", "done", "dismissed", "snoozed"]
    source_id: str
    source_kind: str             # slack | mail | github | feed:<type>: picks the channel colour
    channel_label: str           # "Slack", "邮件", "GitHub", "信息流": the channel pill text
    author: str
    author_role: str = ""        # "你的经理", a repo, an address: the detail header
    where: str = ""              # "Slack 私信", "邮件 · To 你", "#team-leads"
    received_at: str
    late: bool = False           # waiting longer than two days
    ask: str = ""                # the one sentence: what the person is asked (the detail headline)
    ask_sub: str = ""
    excerpt: str = ""            # the message text, first 2,000 characters
    url: str = ""
    priority: int = 3
    producer: Optional[str] = None   # PROPOSED items: who submitted it ("github-outreach")


class TargetOut(BaseModel):
    label: str                    # after "回复到"
    recipients: list[str]
    public: bool = False
    flags: list[str] = Field(default_factory=list)  # red warnings above the draft
    target_sha: str
    can_send: bool


class DraftOut(BaseModel):
    id: str
    item_id: str
    text: str
    lang: str = ""
    note: str = ""               # "参考了你和周琳的 6 条往来"
    created_at: str


class SendRequest(BaseModel):
    draft_id: Optional[str] = None
    text: str = Field(min_length=1)
    target_sha: str


class SendOut(BaseModel):
    id: str
    item_id: str
    origin: Literal["click", "auto"]
    producer: Optional[str] = None
    status: SendStatusName
    to_label: str
    source_kind: str
    channel_label: str
    text: str
    url: str = ""
    created_at: str
    sent_at: Optional[str] = None
    error: str = ""
    blocked_by_source: bool = False  # failed because the source is unhealthy: resend disabled
    has_reply: bool = False          # a later message in the thread came back as an item
    reply_item_id: Optional[str] = None


class SendsOut(BaseModel):
    need_you: list[SendOut]      # failed + unknown, newest first
    sent: list[SendOut]          # status sent, newest first (the client groups by day)
    total_7d: int = 0
    auto_7d: int = 0


class ItemActionRequest(BaseModel):
    action: Literal["later", "dismiss", "done"]


class TrendPoint(BaseModel):
    title: str
    why: str
    source_label: str
    item_id: Optional[str] = None
    url: str = ""


class TrendSummaryOut(BaseModel):
    generated_at: Optional[str] = None
    points: list[TrendPoint] = Field(default_factory=list)
    feeds: int = 0
    watched_repos: int = 0


class SourceOut(BaseModel):
    id: str
    kind: str
    channel_label: str
    backend: str                 # "mcp:slack-mcp", "slack-web", "imap", "gh", "feed"
    identity: str = ""           # "@xg", an address, a login
    interval_s: int
    health: SourceHealthName
    fix: str = ""                # the action shown for auth_failed ("运行 mwinit -f")
    last_pull_at: Optional[str] = None
    last_error: str = ""
    can_send: bool = False
    detail: str = ""             # "关注 3 个频道", "22 个 feed"
    status_detail: str = ""      # why sending is off (read_only): "邮件发送没开：…"
    enabled: bool = True
    options: dict = Field(default_factory=dict)  # watched_channels, extra_addresses, ... (never a secret)


class RedactionSpan(BaseModel):
    start: int                   # [start, end) in code points of the text sent
    end: int
    replacement: str = ""


class SendRefusalOut(BaseModel):
    """Why a send was not attempted: ``forbidden`` (403: no X-Swarm-User-Action, or the
    caller is not the desktop UI), ``target_changed`` (with the fresh ``target``),
    ``redacted`` (with ``spans``), ``not_open``, ``cannot_send``, ``not_unknown``,
    ``not_failed``, ``source_removed`` (409)."""
    code: str
    message: str = ""
    spans: list[RedactionSpan] = Field(default_factory=list)
    target: Optional[TargetOut] = None
    reason: str = ""             # 403 from the caller check: which check refused


class SendRefusalBody(BaseModel):
    detail: SendRefusalOut


class FeedOut(BaseModel):
    """One feed of config.yaml ``feeds:``, the shape of ``GET /api/community/sources``."""
    id: str
    name: str
    type: str
    tier: str = "engineering"
    enabled: bool = True
    managed_by: str = "manual"
    member_kind: Optional[str] = None
    member_count: int = 0
    members: list[str] = Field(default_factory=list)
    members_truncated: bool = False
    tags: list[str] = Field(default_factory=list)


class FeedsOut(BaseModel):
    count: int
    sources: list[FeedOut]


class AvailableSourceOut(BaseModel):
    """A source this machine can connect with one click (``POST /api/inbox/sources`` with
    ``kind``, ``backend`` and ``options`` as given)."""
    kind: str                    # slack | mail | github
    backend: str                 # "mcp:<server id>", "gh"
    channel_label: str
    label: str                   # what the row shows: the server id, "GitHub CLI"
    options: dict = Field(default_factory=dict)  # e.g. {"tool_family": "slack-mcp"}
    identity: str = ""           # known before adding (gh: the login); MCP: found on 接入
    detail: str = ""             # e.g. the reason a GitHub login can only read trends
    added: bool = False
    source_id: Optional[str] = None  # the row it was added as


class SourceCreateRequest(BaseModel):
    kind: str = Field(min_length=1, max_length=40)
    backend: str = Field(min_length=1, max_length=200)
    options: dict = Field(default_factory=dict)
    # slack-web: user_token; imap: password (and smtp_password): stored in the OS
    # credential store before whoami, deleted again when it fails; never in options
    secrets: dict[str, SecretStr] = Field(default_factory=dict)


class SourcePatchRequest(BaseModel):
    enabled: Optional[bool] = None
    interval_s: Optional[int] = Field(default=None, ge=300, le=604_800)
    watched_channels: Optional[list[str]] = Field(default=None, max_length=50)   # slack
    extra_addresses: Optional[list[str]] = Field(default=None, max_length=50)    # mail


class SecretRequest(BaseModel):
    name: str = Field(min_length=1, max_length=40)
    value: SecretStr


class SourceRefusalOut(BaseModel):
    """A refused source write: ``whoami_failed`` (400, with the source's ``fix``),
    ``already_added`` / ``send_in_flight`` / ``pull_running`` (409), ``invalid_source`` / ``feed_source`` /
    ``invalid_secret`` (422), ``not_found`` (404), ``forbidden`` (403)."""
    code: str
    message: str = ""
    fix: str = ""


class SourceRefusalBody(BaseModel):
    detail: SourceRefusalOut


class SourceDeletedOut(BaseModel):
    id: str
    items: int = 0
    drafts: int = 0
    sends: int = 0
