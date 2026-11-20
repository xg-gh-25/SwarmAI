"""The mail source (``kind="mail"``): the person's inbox, replies into the same thread.

Design: ``docs/design/2026-11-20-inbox.md`` §4. Two backends, picked by ``SourceConfig.backend``:

- ``mcp:<server id>``: a mail MCP server the person configured, with the tool mapping of
  its server family. The ``aws-outlook-mcp`` mapping ships here (``OutlookMcpSource``):
  ``email_folders`` lists the inbox since the cursor's day, ``email_read`` fetches whole
  conversations, and every conversation is **split into one message per message id**.
  ``email_reply`` answers one message (never reply-all, never extra recipients).
  ``can_send`` is true only when the server's ``tools/list`` offers ``email_reply`` *and*
  its environment sets ``OUTLOOK_MCP_ENABLE_WRITES`` to a true value (the switch that
  server family uses to allow sending); either missing reads as a read-only source.
- ``imap``: IMAP4 over TLS for reading, SMTP for the reply. Reading never changes the
  mailbox: the folder is opened with ``EXAMINE`` and bodies are fetched with
  ``BODY.PEEK[]``, so nothing is marked read. A reply carries ``In-Reply-To`` and
  ``References`` and goes only to the sender (``Reply-To`` when the mail has one). The
  password is read from the OS credential store under ``<source id>:password`` (and
  ``<source id>:smtp_password`` when SMTP needs another one).

Per message: ``is_direct`` when one of the person's addresses is in To; ``is_bulk`` when
it is only in Cc/Bcc (or not listed at all) or the mail has list headers
(``List-Id``/``List-Unsubscribe``/``Precedence: bulk``); ``is_invite`` for calendar
invites; ``is_bot`` for no-reply and auto-submitted senders.

The reply target resolves ``Reply-To``. ``ReplyTarget.flags`` gets ``reply_to_differs``
when ``Reply-To`` is not the sender, and ``external_domain`` when a recipient is outside
the domains of the person's own addresses.

Identity: the From address of the person's own latest sent mail (Outlook) or the IMAP
login when it is an address, plus ``options["addresses"]`` (the person's aliases).

IMAP options: ``host``, ``port`` (993), ``username``, ``folder`` (INBOX), ``sent_folder``
(found by its ``\\Sent`` flag when not given), ``smtp_host``, ``smtp_port`` (465),
``smtp_security`` (``ssl`` | ``starttls``), ``max_messages`` (50). Both backends:
``addresses``, ``first_pull_hours`` (24), ``pull_budget_s`` (120).

Key public symbols: ``OutlookMcpSource``, ``ImapSmtpSource``, ``MailRecord``,
``split_conversations``, ``MCP_FAMILIES``, ``create``.
"""
from __future__ import annotations

import asyncio
import email
import email.policy
import html
import imaplib
import logging
import os
import re
import smtplib
import ssl
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from typing import Any, Callable, ClassVar, Optional

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
from core.inbox.sources.base import SendFailed, SourceAuthError, SourceConfig, register_source
from core.inbox.sources.common import (
    BudgetExhausted,
    Deadline,
    ReadToolRefused,
    SourceUnavailable,
    ToolGate,
    auth_fix_for,
    call_timeout,
    domain_of,
    looks_like_auth_failure,
    matches_sent,
    normalize_text,
    parse_address,
    parse_addresses,
    parse_time,
    strip_mail_quotes,
)

logger = logging.getLogger(__name__)

KIND = "mail"
_CALL_CAP_S = 60.0
_AUTOMATED = re.compile(r"^(no-?reply|do-?not-?reply|donotreply|mailer-daemon|postmaster|bounces?|notifications?)([+.\-_].*)?@", re.I)
_TRUE = {"1", "true", "yes", "on"}


@dataclass
class MailRecord:
    """One mail message, as either backend returns it."""

    message_id: str  # the item's identity: the Internet Message-ID, else the backend's own id
    conversation: str  # the thread key: Outlook's conversation id, or the root Message-ID
    subject: str = ""
    from_name: str = ""
    from_addr: str = ""
    reply_to: list[str] = field(default_factory=list)
    to: list[str] = field(default_factory=list)
    cc: list[str] = field(default_factory=list)
    bcc: list[str] = field(default_factory=list)
    received_at: Optional[datetime] = None
    body: str = ""
    headers: dict[str, str] = field(default_factory=dict)  # lower-cased names
    is_invite: bool = False
    item_id: str = ""  # Outlook: what email_reply needs
    change_key: str = ""
    url: str = ""
    references: list[str] = field(default_factory=list)  # IMAP: the References chain


def _get(d: dict, *names: str) -> Any:
    for name in names:
        if name in d and d[name] not in (None, "", [], {}):
            return d[name]
    return None


def _body_text(value: Any) -> str:
    if isinstance(value, dict):
        value = _get(value, "content", "text", "Content", "value") or ""
        # an HTML body: keep the words
    text = str(value or "")
    if re.search(r"<(html|body|div|p|br|table)\b", text, re.I):
        text = re.sub(r"<(br|/p|/div|/tr)\b[^>]*>", "\n", text, flags=re.I)
        text = html.unescape(re.sub(r"<[^>]+>", "", text))
    return text.strip()


_MESSAGE_ID_KEYS = ("internetMessageId", "InternetMessageId", "internet_message_id", "messageId", "message_id")
_ITEM_ID_KEYS = ("itemId", "ItemId", "item_id", "id", "Id")
_FROM_KEYS = ("from", "From", "sender", "Sender")
_DATE_KEYS = (
    "receivedDateTime", "dateTimeReceived", "DateTimeReceived", "received", "receivedAt",
    "recievedAt",  # sic: the aws-outlook-mcp family spells it this way
    "sentDateTime", "dateTimeSent", "DateTimeSent", "date", "Date",
)


def _looks_like_message(d: dict) -> bool:
    return any(k in d for k in _FROM_KEYS) and (any(k in d for k in _MESSAGE_ID_KEYS + _ITEM_ID_KEYS) or any(k in d for k in _DATE_KEYS))


def _record(d: dict, conversation: str) -> MailRecord:
    from_name, from_addr = parse_address(_get(d, *_FROM_KEYS))
    reply_to = [a for _, a in parse_addresses(_get(d, "replyTo", "ReplyTo", "reply_to"))]
    item_id = str(_get(d, *_ITEM_ID_KEYS) or "")
    headers: dict[str, str] = {}
    raw_headers = _get(d, "internetMessageHeaders", "headers", "Headers")
    if isinstance(raw_headers, list):
        for h in raw_headers:
            if isinstance(h, dict) and h.get("name"):
                headers[str(h["name"]).lower()] = str(h.get("value") or "")
    elif isinstance(raw_headers, dict):
        headers = {str(k).lower(): str(v) for k, v in raw_headers.items()}
    if _get(d, "listId", "list_id"):
        headers.setdefault("list-id", str(_get(d, "listId", "list_id")))
    item_class = str(_get(d, "itemClass", "ItemClass", "messageClass", "type") or "")
    invite = (
        item_class.startswith("IPM.Schedule.Meeting")
        or "meeting" in item_class.lower()
        or bool(_get(d, "isMeetingRequest", "meetingMessageType", "meetingRequest"))
    )
    return MailRecord(
        message_id=str(_get(d, *_MESSAGE_ID_KEYS) or item_id),
        conversation=str(_get(d, "conversationId", "ConversationId", "conversation_id") or conversation or ""),
        subject=str(_get(d, "subject", "Subject") or ""),
        from_name=from_name,
        from_addr=from_addr,
        reply_to=reply_to,
        to=[a for _, a in parse_addresses(_get(d, "toRecipients", "to", "To", "ToRecipients", "recipients"))],
        cc=[a for _, a in parse_addresses(_get(d, "ccRecipients", "cc", "Cc", "CcRecipients"))],
        bcc=[a for _, a in parse_addresses(_get(d, "bccRecipients", "bcc", "Bcc", "BccRecipients"))],
        received_at=parse_time(_get(d, *_DATE_KEYS)),
        body=_body_text(_get(d, "body", "Body", "bodyText", "textBody", "text", "content", "uniqueBody", "bodyPreview", "preview")),
        headers=headers,
        is_invite=invite,
        item_id=item_id,
        change_key=str(_get(d, "changeKey", "itemChangeKey", "ChangeKey", "change_key") or ""),
        url=str(_get(d, "webLink", "url", "link") or ""),
    )


def split_conversations(data: Any, conversation: str = "") -> list[MailRecord]:
    """Every message in an answer that holds whole conversations, one record per message.

    Walks any nesting of lists and dicts; a dict with a sender and an id or a date is a
    message, and the nearest enclosing ``conversationId`` is its conversation."""
    out: list[MailRecord] = []

    def walk(obj: Any, conv: str) -> None:
        if isinstance(obj, list):
            for item in obj:
                walk(item, conv)
        elif isinstance(obj, dict):
            here = str(_get(obj, "conversationId", "ConversationId", "conversation_id") or conv)
            if _looks_like_message(obj):
                out.append(_record(obj, here))
                return
            for value in obj.values():
                if isinstance(value, (list, dict)):
                    walk(value, here)

    walk(data, conversation)
    return out


def conversation_ids(data: Any) -> list[str]:
    """Conversation ids in a listing, in order, each once."""
    seen: list[str] = []

    def walk(obj: Any) -> None:
        if isinstance(obj, list):
            for item in obj:
                walk(item)
        elif isinstance(obj, dict):
            cid = _get(obj, "conversationId", "ConversationId", "conversation_id")
            if cid and str(cid) not in seen:
                seen.append(str(cid))
            for value in obj.values():
                if isinstance(value, (list, dict)):
                    walk(value)

    walk(data)
    return seen


class _MailSource:
    kind: ClassVar[str] = KIND
    category: ClassVar[SourceCategory] = SourceCategory.CONVERSATION
    READ_TOOLS: ClassVar[frozenset[str]] = frozenset()
    WRITE_TOOLS: ClassVar[frozenset[str]] = frozenset()
    ALLOWED_ARGS: ClassVar[dict[str, frozenset[str]]] = {}
    # Every tool must have an argument list unless a backend passes no argument dicts at all
    # (IMAP/SMTP opts out): a new mail family that forgets its lists fails at construction.
    ARGS_LISTED: ClassVar[bool] = True

    def __init__(self, config: SourceConfig) -> None:
        self.config = config
        opts = config.options or {}
        self._gate = ToolGate(
            f"mail:{config.id}", self.READ_TOOLS, self.WRITE_TOOLS, self.ALLOWED_ARGS, require_args=self.ARGS_LISTED
        )
        self._extra_addresses = [str(a).strip().lower() for a in opts.get("addresses") or () if "@" in str(a)]
        self._first_pull_s = float(opts.get("first_pull_hours", 24)) * 3600
        self._budget_s = float(opts.get("pull_budget_s", 120))
        self._deadline: Optional[Deadline] = None
        self._me: Optional[Identity] = None
        self.read_only_reason = ""

    async def _own_addresses(self) -> list[str]:
        raise NotImplementedError

    async def whoami(self) -> Identity:
        if self._me is None:
            addresses = [a for a in await self._own_addresses() if a]
            primary = addresses[0] if addresses else (self._extra_addresses[0] if self._extra_addresses else "")
            ids = frozenset(a.lower() for a in addresses + self._extra_addresses)
            if not ids:
                raise SourceUnavailable(f"{self.config.id}: cannot tell your mail address")
            self._me = Identity(ids=ids, display=primary)
        return self._me

    def _timeout(self, cap: float = _CALL_CAP_S) -> tuple[float, bool]:
        return call_timeout(self._deadline, cap)

    def _ref_for(self, rec: MailRecord) -> ThreadRef:
        raise NotImplementedError

    @staticmethod
    def _mine(me: Identity) -> set[str]:
        return {i.lower() for i in me.ids if "@" in i}

    def _to_message(self, rec: MailRecord, me: Identity) -> Message:
        mine = self._mine(me)
        in_to = any(a in mine for a in rec.to)
        listy = (
            "list-id" in rec.headers
            or "list-unsubscribe" in rec.headers
            or rec.headers.get("precedence", "").strip().lower() in ("bulk", "list", "junk")
        )
        auto = rec.headers.get("auto-submitted", "no").strip().lower() not in ("", "no")
        return Message(
            external_id=rec.message_id,
            thread=self._ref_for(rec),
            author_id=rec.from_addr,
            author_name=rec.from_name or rec.from_addr,
            received_at=rec.received_at or datetime.now(timezone.utc),
            text=strip_mail_quotes(rec.body) or rec.body,
            url=rec.url,
            title=rec.subject,
            is_direct=in_to,
            is_bot=bool(_AUTOMATED.match(rec.from_addr or "")) or auto,
            is_bulk=listy or not in_to,
            is_invite=rec.is_invite,
            extra={"to": list(rec.to), "cc": list(rec.cc), "reply_to": list(rec.reply_to), "conversation": rec.conversation},
        )

    def _target(self, rec: MailRecord, me: Identity) -> ReplyTarget:
        recipients = list(dict.fromkeys(rec.reply_to)) or [rec.from_addr]
        recipients = [r for r in recipients if r]
        flags: list[str] = []
        if rec.reply_to and set(rec.reply_to) != {rec.from_addr}:
            flags.append("reply_to_differs")
        own_domains = {domain_of(a) for a in self._mine(me)}
        if own_domains and any(domain_of(r) not in own_domains for r in recipients):
            flags.append("external_domain")
        name = rec.from_name if recipients == [rec.from_addr] and rec.from_name else ""
        label = f"{name} <{recipients[0]}>" if name else ", ".join(recipients)
        return ReplyTarget(label=label, recipients=tuple(recipients), flags=tuple(flags))

    @staticmethod
    def _dedup(records: list[MailRecord]) -> list[MailRecord]:
        out: dict[str, MailRecord] = {}
        for rec in records:
            if rec.message_id and rec.message_id not in out:
                out[rec.message_id] = rec
        return sorted(out.values(), key=lambda r: r.received_at or datetime.min.replace(tzinfo=timezone.utc))

    def _reply_record(self, ref: ThreadRef, records: list[MailRecord], me: Identity) -> Optional[MailRecord]:
        wanted = ref.data.get("message_id")
        if wanted:
            return next((r for r in records if r.message_id == wanted), None)
        mine = self._mine(me)
        others = [r for r in records if r.from_addr not in mine]
        return others[-1] if others else None

    async def _thread_records(self, ref: ThreadRef) -> list[MailRecord]:
        raise NotImplementedError

    async def thread(self, ref: ThreadRef) -> Thread:
        me = await self.whoami()
        records = self._dedup(await self._thread_records(ref))
        target_rec = self._reply_record(ref, records, me)
        return Thread(
            ref=ref,
            messages=tuple(self._to_message(r, me) for r in records),
            target=self._target(target_rec, me) if target_rec else None,
        )

    async def _sent_candidates(self, ref: ThreadRef, after: datetime, text: str) -> list[MailRecord]:
        raise NotImplementedError

    async def find_sent(self, ref: ThreadRef, after: datetime, text: str) -> Optional[SendReceipt]:
        me = await self.whoami()
        after = after if after.tzinfo else after.replace(tzinfo=timezone.utc)
        mine = self._mine(me)
        for rec in self._dedup(await self._sent_candidates(ref, after, text)):
            if rec.from_addr in mine and rec.received_at and rec.received_at > after and matches_sent(rec.body, text):
                return SendReceipt(external_msg_id=rec.message_id, url=rec.url, sent_at=rec.received_at)
        return None

    async def close(self) -> None:
        return None


# ---------------------------------------------------------------------------
# Backend: a mail MCP server of the aws-outlook-mcp family
# ---------------------------------------------------------------------------


class OutlookMcpSource(_MailSource):
    FAMILY: ClassVar[str] = "aws-outlook-mcp"
    WRITES_ENV: ClassVar[str] = "OUTLOOK_MCP_ENABLE_WRITES"
    READ_TOOLS: ClassVar[frozenset[str]] = frozenset({"email_folders", "email_read", "email_search"})
    WRITE_TOOLS: ClassVar[frozenset[str]] = frozenset({"email_reply"})
    # Only these arguments reach the server: never markAs (it changes the mailbox), never
    # extra recipients or a shared mailbox on a reply (the reply goes to the thread's own target).
    ALLOWED_ARGS: ClassVar[dict[str, frozenset[str]]] = {
        "email_folders": frozenset({"folder", "startDate", "limit", "offset"}),
        "email_read": frozenset({"conversationId", "format"}),
        "email_search": frozenset({"query", "folder", "startDate", "limit"}),
        "email_reply": frozenset({"itemId", "itemChangeKey", "body", "replyAll"}),
    }

    def __init__(self, config: SourceConfig, *, client: Any = None) -> None:
        super().__init__(config)
        self.server_id = config.backend.split(":", 1)[1] if config.backend.startswith("mcp:") else config.backend
        self._client = client
        opts = config.options or {}
        self._max_conversations = int(opts.get("max_conversations", 50))
        self._max_pages = max(1, int(opts.get("max_pages", 5)))

    async def _get_client(self) -> Any:
        if self._client is None:
            from core.mcp_stdio_client import McpStdioClient, McpUnavailable

            try:
                self._client = await asyncio.to_thread(McpStdioClient.from_server_id, self.server_id)
            except McpUnavailable as exc:
                raise SourceUnavailable(str(exc)) from exc
        return self._client

    def _auth_error(self, text: str) -> Optional[SourceAuthError]:
        if looks_like_auth_failure(text):
            return SourceAuthError(f"MCP server {self.server_id}: login expired", fix=auth_fix_for(text, self.server_id))
        return None

    async def _call(self, tool: str, args: dict) -> Any:
        from core.mcp_stdio_client import McpError, McpTimeout, McpToolError, parse_tool_result

        self._gate.check(tool, args)
        client = await self._get_client()
        timeout, by_budget = self._timeout()
        try:
            result = await client.call_tool(tool, args, timeout=timeout)
        except McpTimeout as exc:
            if by_budget:
                raise BudgetExhausted(f"{tool} cut short by the pull budget") from exc
            raise
        except McpToolError as exc:
            raise self._auth_error(str(exc)) or SourceUnavailable(f"{tool}: {exc}") from exc
        except McpError as exc:
            detail = f"{exc}\n{getattr(client, 'stderr_tail', '')}"
            raise self._auth_error(detail) or SourceUnavailable(f"{tool}: {exc}") from exc
        data = parse_tool_result(result)
        if isinstance(data, str):
            raise SourceUnavailable(f"{tool}: the answer is not in a shape the {self.FAMILY} mapping reads")
        return data

    def _ref_for(self, rec: MailRecord) -> ThreadRef:
        return ThreadRef(KIND, {"conversation_id": rec.conversation, "message_id": rec.message_id})

    async def _own_addresses(self) -> list[str]:
        """The person's address: the sender found in every one of their latest sent
        conversations. A folder listing names conversations, not senders' addresses, so
        each is read; a conversation also holds the other side's replies, which is why one
        is not enough."""
        listing = await self._call("email_folders", {"folder": "sentitems", "limit": 3})
        records = split_conversations(listing)          # a server that lists messages directly
        per_conv: list[set[str]] = []
        if records:
            per_conv.append({r.from_addr.lower() for r in records if r.from_addr})
        else:
            for cid in conversation_ids(listing)[:3]:
                read = split_conversations(await self._call("email_read", {"conversationId": cid}), cid)
                senders = {r.from_addr.lower() for r in read if r.from_addr}
                if senders:
                    per_conv.append(senders)
        if not per_conv:
            return []
        common = set.intersection(*per_conv)
        return sorted(common) if len(common) == 1 else []   # two candidates left: not sure who

    async def can_send(self) -> bool:
        client = await self._get_client()
        try:
            names = {t.get("name") for t in await client.list_tools()}
        except Exception as exc:  # noqa: BLE001 - an unreadable tool list means: do not offer sending
            self.read_only_reason = f"tools/list failed: {type(exc).__name__}"
            return False
        if "email_reply" not in names:
            self.read_only_reason = "the server does not offer email_reply"
            return False
        env = {**os.environ, **(getattr(client, "env", None) or {})}
        if str(env.get(self.WRITES_ENV, "")).strip().lower() not in _TRUE:
            self.read_only_reason = f"sending is off: the server's {self.WRITES_ENV} is not true"
            return False
        self.read_only_reason = ""
        return True

    async def pull(self, cursor: Optional[str]) -> PullResult:
        me = await self.whoami()
        since = parse_time(cursor) if cursor else None
        since = since or datetime.now(timezone.utc) - timedelta(seconds=self._first_pull_s)
        self._deadline = Deadline(self._budget_s)
        complete = True
        records: list[MailRecord] = []
        try:
            ids: list[str] = []
            listing: Any = None
            for page in range(self._max_pages):
                listing = await self._call("email_folders", {
                    "folder": "inbox", "startDate": since.date().isoformat(),
                    "limit": self._max_conversations, "offset": page * self._max_conversations,
                })
                page_ids = conversation_ids(listing)
                ids.extend(i for i in page_ids if i not in ids)
                if len(page_ids) < self._max_conversations:
                    break
            else:
                logger.warning("mail source %s: there may be more than %d conversations since the cursor; older ones are not read this pull",
                               self.config.id, self._max_pages * self._max_conversations)
            # One conversation per read: the messages in an email_read answer carry no
            # conversationId of their own, so a read of several could not tell which
            # message belongs to which thread (and a reply needs its thread).
            for cid in ids:
                found = split_conversations(await self._call("email_read", {"conversationId": cid, "format": "text"}), cid)
                if not found:
                    raise SourceUnavailable("email_read answered in a shape this mapping does not read")
                records.extend(found)
            if not ids:
                records.extend(split_conversations(listing))
        except BudgetExhausted:
            complete = False
        finally:
            self._deadline = None
        fresh = [r for r in self._dedup(records) if r.received_at and r.received_at > since]
        newest = max([since] + [r.received_at for r in fresh if r.received_at])
        return PullResult(tuple(self._to_message(r, me) for r in fresh), newest.isoformat() if complete else None)

    async def _thread_records(self, ref: ThreadRef) -> list[MailRecord]:
        cid = str(ref.data.get("conversation_id") or "")
        if not cid:
            return []
        return split_conversations(await self._call("email_read", {"conversationId": cid, "format": "text"}), cid)

    async def _sent_candidates(self, ref: ThreadRef, after: datetime, text: str) -> list[MailRecord]:
        records = await self._thread_records(ref)
        words = re.findall(r"\w+", normalize_text(text))[:6]
        if words:
            found = split_conversations(
                await self._call(
                    "email_search", {"query": " ".join(words), "folder": "sentitems", "startDate": after.date().isoformat()}
                )
            )
            cid = ref.data.get("conversation_id")
            records.extend(r for r in found if not cid or not r.conversation or r.conversation == cid)
        return records

    async def reply(self, ref: ThreadRef, text: str) -> SendReceipt:
        from core.mcp_stdio_client import (
            McpError, McpTimeout, McpToolError, McpToolRefused, McpUnavailable, parse_tool_result,
        )

        try:
            me = await self.whoami()
            rec = self._reply_record(ref, self._dedup(await self._thread_records(ref)), me)
        except (SourceUnavailable, SourceAuthError, McpError) as exc:
            raise SendFailed(f"could not read the thread before replying: {exc}") from exc
        if rec is None or not rec.item_id or not rec.change_key:
            raise SendFailed("the message to reply to is no longer in the mailbox")
        args = {"itemId": rec.item_id, "itemChangeKey": rec.change_key, "body": text, "replyAll": False}
        try:  # the thread was just read through this client; nothing is sent yet
            client = await self._get_client()
            await client.start()
        except (SourceUnavailable, McpError) as exc:
            raise SendFailed(f"MCP server {self.server_id} did not start: {exc}") from exc
        with self._gate.allow_writes("email_reply"):
            self._gate.check("email_reply", args, write=True)
            try:
                result = await client.call_tool("email_reply", args, timeout=_CALL_CAP_S)
            except McpTimeout:
                raise
            except (McpToolError, McpToolRefused, McpUnavailable) as exc:
                raise SendFailed(str(exc)) from exc
        data = parse_tool_result(result)
        msg_id = ""
        if isinstance(data, dict):
            msg_id = str(_get(data, "internetMessageId", "messageId", "itemId", "id") or "")
        return SendReceipt(external_msg_id=msg_id, sent_at=datetime.now(timezone.utc))

    async def close(self) -> None:
        if self._client is not None:
            await self._client.close()


# ---------------------------------------------------------------------------
# Backend: IMAP over TLS + SMTP
# ---------------------------------------------------------------------------

_PEEK_SPECS = frozenset({"(UID BODY.PEEK[])"})
_LOGIN_FIX = "在 Inbox 渠道设置里重新填邮箱密码（Gmail 等用应用专用密码）后点重试"


def imap_quote(value: str) -> str:
    """An IMAP quoted string; refuses CR, LF and NUL, which would end the command."""
    if any(c in value for c in "\r\n\0"):
        raise ValueError("refusing a value with a line break for an IMAP command")
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


class _Imap:
    """The IMAP commands a pull may send, each through the gate."""

    def __init__(self, gate: ToolGate, conn: Any) -> None:
        self._gate = gate
        self._conn = conn

    def login(self, user: str, password: str) -> None:
        self._gate.check("LOGIN")
        try:
            self._conn.login(user, password)
        except imaplib.IMAP4.error as exc:
            raise SourceAuthError("the mail server refused the login", fix=_LOGIN_FIX) from exc

    def examine(self, folder: str) -> tuple[str, Optional[int]]:
        self._gate.check("EXAMINE")
        typ, _ = self._conn.select(imap_quote(folder), readonly=True)
        if typ != "OK":
            raise SourceUnavailable(f"cannot open folder {folder!r}")
        validity = self._first_number("UIDVALIDITY")
        uidnext = self._first_number("UIDNEXT")
        return str(validity or ""), uidnext

    def _first_number(self, code: str) -> Optional[int]:
        try:
            _, data = self._conn.response(code)
        except Exception as exc:  # noqa: BLE001 - a server that does not send it
            logger.debug("IMAP server sent no %s: %s", code, type(exc).__name__)
            return None
        for item in data or ():
            if item is None:
                continue
            text = item.decode() if isinstance(item, bytes) else str(item)
            if text.strip().isdigit():
                return int(text.strip())
        return None

    def search(self, *criteria: str) -> list[int]:
        self._gate.check("UID SEARCH")
        typ, data = self._conn.uid("SEARCH", *criteria)
        if typ != "OK":
            raise SourceUnavailable("IMAP search failed")
        out: list[int] = []
        for chunk in data or ():
            if chunk:
                out.extend(int(x) for x in (chunk.decode() if isinstance(chunk, bytes) else str(chunk)).split() if x.isdigit())
        return out

    def fetch(self, uids: list[int], spec: str = "(UID BODY.PEEK[])") -> list[tuple[int, bytes]]:
        self._gate.check("UID FETCH")
        if spec not in _PEEK_SPECS:
            raise ReadToolRefused(f"IMAP fetch {spec!r} could mark mail read; only BODY.PEEK is allowed")
        if not uids:
            return []
        typ, data = self._conn.uid("FETCH", ",".join(str(u) for u in uids), spec)
        if typ != "OK":
            raise SourceUnavailable("IMAP fetch failed")
        out: list[tuple[int, bytes]] = []
        for item in data or ():
            if isinstance(item, tuple) and len(item) >= 2:
                head = item[0].decode() if isinstance(item[0], bytes) else str(item[0])
                match = re.search(r"UID (\d+)", head)
                if match:
                    out.append((int(match.group(1)), item[1]))
        return out

    def list_folders(self) -> list[tuple[str, str]]:
        """``(flags, name)`` for every folder."""
        self._gate.check("LIST")
        typ, data = self._conn.list()
        out: list[tuple[str, str]] = []
        for item in data or ():
            line = item.decode() if isinstance(item, bytes) else str(item or "")
            match = re.match(r'\((?P<flags>[^)]*)\)\s+(?:"[^"]*"|NIL)\s+(?P<name>.+)$', line)
            if match:
                name = match.group("name").strip()
                if name.startswith('"') and name.endswith('"'):
                    name = name[1:-1].replace('\\"', '"').replace("\\\\", "\\")
                out.append((match.group("flags"), name))
        return out

    def logout(self) -> None:
        self._gate.check("LOGOUT")
        try:
            self._conn.logout()
        except Exception as exc:  # noqa: BLE001 - the session is over either way
            logger.debug("IMAP logout failed: %s", type(exc).__name__)


def _default_imap(host: str, port: int) -> Any:
    return imaplib.IMAP4_SSL(host, port, ssl_context=ssl.create_default_context(), timeout=30)


def _default_smtp(host: str, port: int, security: str) -> Any:
    context = ssl.create_default_context()
    if security == "starttls":
        smtp = smtplib.SMTP(host, port, timeout=30)
        smtp.ehlo()
        smtp.starttls(context=context)
        smtp.ehlo()
        return smtp
    return smtplib.SMTP_SSL(host, port, context=context, timeout=30)


def _parse_rfc822(raw: bytes, fallback_id: str) -> MailRecord:
    msg = email.message_from_bytes(raw, policy=email.policy.default)
    message_id = str(msg.get("Message-ID") or "").strip() or fallback_id
    references = str(msg.get("References") or "").split()
    in_reply_to = str(msg.get("In-Reply-To") or "").strip()
    from_name, from_addr = parse_address(str(msg.get("From") or ""))
    body = ""
    try:
        part = msg.get_body(preferencelist=("plain", "html"))
        if part is not None:
            body = _body_text(part.get_content())
    except (KeyError, LookupError, ValueError):
        body = ""
    invite = any(p.get_content_type() in ("text/calendar", "application/ics") for p in msg.walk())
    headers = {
        name: str(msg.get(name) or "")
        for name in ("list-id", "list-unsubscribe", "precedence", "auto-submitted")
        if msg.get(name) is not None
    }
    return MailRecord(
        message_id=message_id,
        conversation=references[0] if references else (in_reply_to or message_id),
        subject=str(msg.get("Subject") or ""),
        from_name=from_name,
        from_addr=from_addr,
        reply_to=[a for _, a in parse_addresses(str(msg.get("Reply-To") or "")) if a] if msg.get("Reply-To") else [],
        to=[a for _, a in parse_addresses(str(msg.get("To") or ""))],
        cc=[a for _, a in parse_addresses(str(msg.get("Cc") or ""))],
        received_at=parse_time(str(msg.get("Date") or "")),
        body=body,
        headers=headers,
        is_invite=invite,
        references=references or ([in_reply_to] if in_reply_to else []),
    )


class ImapSmtpSource(_MailSource):
    ARGS_LISTED: ClassVar[bool] = False  # IMAP commands and SMTP take no argument dicts: names only
    READ_TOOLS: ClassVar[frozenset[str]] = frozenset({"LOGIN", "EXAMINE", "UID SEARCH", "UID FETCH", "LIST", "LOGOUT"})
    WRITE_TOOLS: ClassVar[frozenset[str]] = frozenset({"SMTP SEND"})

    def __init__(
        self,
        config: SourceConfig,
        *,
        imap_factory: Optional[Callable[[str, int], Any]] = None,
        smtp_factory: Optional[Callable[[str, int, str], Any]] = None,
        secret_reader: Optional[Callable[[str], Optional[str]]] = None,
    ) -> None:
        super().__init__(config)
        opts = config.options or {}
        self.host = str(opts.get("host") or "")
        self.port = int(opts.get("port", 993))
        self.username = str(opts.get("username") or "")
        self.folder = str(opts.get("folder") or "INBOX")
        self.sent_folder = str(opts.get("sent_folder") or "")
        self.smtp_host = str(opts.get("smtp_host") or "")
        self.smtp_port = int(opts.get("smtp_port", 465))
        self.smtp_security = str(opts.get("smtp_security") or "ssl")
        self.max_messages = int(opts.get("max_messages", 50))
        if not self.host or not self.username:
            raise ValueError("an imap mail source needs options.host and options.username")
        self._imap_factory = imap_factory or _default_imap
        self._smtp_factory = smtp_factory or _default_smtp
        self._secret_reader = secret_reader
        self._raw_cache: dict[str, bytes] = {}

    # -- plumbing ---------------------------------------------------------------

    def _secret(self, name: str) -> Optional[str]:
        if self._secret_reader is not None:
            return self._secret_reader(name)
        from core.inbox.sources.credentials import read_secret

        return read_secret(self.config.id, name)

    def _session(self) -> _Imap:
        password = self._secret("password")
        if not password:
            raise SourceAuthError("no mail password stored for this source", fix=_LOGIN_FIX)
        try:
            conn = self._imap_factory(self.host, self.port)
        except (OSError, imaplib.IMAP4.error) as exc:
            raise SourceUnavailable(f"cannot reach {self.host}:{self.port}: {type(exc).__name__}") from exc
        imap = _Imap(self._gate, conn)
        imap.login(self.username, password)
        return imap

    def _ref_for(self, rec: MailRecord) -> ThreadRef:
        return ThreadRef(KIND, {"root": rec.conversation, "message_id": rec.message_id})

    async def _own_addresses(self) -> list[str]:
        return [self.username.lower()] if "@" in self.username else []

    async def can_send(self) -> bool:
        if not self.smtp_host:
            self.read_only_reason = "no smtp_host configured"
            return False
        self.read_only_reason = ""
        return True

    # -- pull ---------------------------------------------------------------------

    async def pull(self, cursor: Optional[str]) -> PullResult:
        me = await self.whoami()
        records, new_cursor = await asyncio.wait_for(
            asyncio.to_thread(self._pull_sync, cursor), timeout=self._budget_s
        )
        return PullResult(tuple(self._to_message(r, me) for r in self._dedup(records)), new_cursor)

    def _pull_sync(self, cursor: Optional[str]) -> tuple[list[MailRecord], Optional[str]]:
        imap = self._session()
        try:
            validity, uidnext = imap.examine(self.folder)
            last_uid: Optional[int] = None
            if cursor and ":" in cursor:
                cur_validity, _, cur_uid = cursor.rpartition(":")
                if cur_validity == validity and cur_uid.isdigit():
                    last_uid = int(cur_uid)
            if last_uid is not None:
                uids = [u for u in imap.search("UID", f"{last_uid + 1}:*") if u > last_uid]
            else:
                since = datetime.now(timezone.utc) - timedelta(seconds=self._first_pull_s)
                uids = imap.search("SINCE", since.strftime("%d-%b-%Y"))
            # the oldest ones first: the cursor then stops where this pull stopped, and the
            # next pull continues from there (nothing is skipped over)
            uids = sorted(set(uids))[: self.max_messages]
            records = [_parse_rfc822(raw, f"imap:{validity}:{uid}") for uid, raw in imap.fetch(uids)]
            top = max(uids) if uids else max(last_uid or 0, (uidnext - 1) if uidnext else 0)
            return records, (f"{validity}:{top}" if validity and top else None)
        finally:
            imap.logout()

    # -- thread / find_sent ---------------------------------------------------------

    def _find_sent_folder(self, imap: _Imap) -> str:
        if self.sent_folder:
            return self.sent_folder
        for flags, name in imap.list_folders():
            if "\\sent" in flags.lower():
                return name
        return ""

    def _thread_sync(self, ref: ThreadRef) -> list[MailRecord]:
        ids = [str(ref.data.get(k) or "") for k in ("root", "message_id")]
        ids = [i for i in dict.fromkeys(ids) if i]
        if not ids:
            return []
        imap = self._session()
        try:
            records: list[MailRecord] = []
            folders = [self.folder]
            sent = self._find_sent_folder(imap)
            if sent and sent != self.folder:
                folders.append(sent)
            for folder in folders:
                validity, _ = imap.examine(folder)
                uids: set[int] = set()
                for mid in ids:
                    q = imap_quote(mid)
                    uids.update(imap.search("OR", "OR", "HEADER", "Message-ID", q, "HEADER", "References", q, "HEADER", "In-Reply-To", q))
                for uid, raw in imap.fetch(sorted(uids)[-200:]):
                    records.append(_parse_rfc822(raw, f"imap:{validity}:{uid}"))
            return records
        finally:
            imap.logout()

    async def _thread_records(self, ref: ThreadRef) -> list[MailRecord]:
        return await asyncio.wait_for(asyncio.to_thread(self._thread_sync, ref), timeout=_CALL_CAP_S)

    def _sent_sync(self, ref: ThreadRef, after: datetime) -> list[MailRecord]:
        mid = str(ref.data.get("message_id") or "")
        if not mid:
            return []
        imap = self._session()
        try:
            folder = self._find_sent_folder(imap)
            if not folder:
                return []
            validity, _ = imap.examine(folder)
            uids = imap.search("SINCE", after.strftime("%d-%b-%Y"), "HEADER", "In-Reply-To", imap_quote(mid))
            return [_parse_rfc822(raw, f"imap:{validity}:{uid}") for uid, raw in imap.fetch(sorted(uids)[-50:])]
        finally:
            imap.logout()

    async def _sent_candidates(self, ref: ThreadRef, after: datetime, text: str) -> list[MailRecord]:
        return await asyncio.wait_for(asyncio.to_thread(self._sent_sync, ref, after), timeout=_CALL_CAP_S)

    # -- reply ------------------------------------------------------------------------

    async def reply(self, ref: ThreadRef, text: str) -> SendReceipt:
        if not self.smtp_host:
            raise SendFailed("no smtp_host configured for this mail source")
        try:
            me = await self.whoami()
            rec = self._reply_record(ref, self._dedup(await self._thread_records(ref)), me)
        except (SourceUnavailable, SourceAuthError, OSError, asyncio.TimeoutError) as exc:
            raise SendFailed(f"could not read the thread before replying: {exc}") from exc
        if rec is None:
            raise SendFailed("the message to reply to is no longer in the mailbox")
        target = self._target(rec, me)
        sender = me.display or next(iter(sorted(self._mine(me))), "")
        msg = EmailMessage()
        msg["From"] = sender
        msg["To"] = ", ".join(target.recipients)
        subject = rec.subject or ""
        msg["Subject"] = subject if re.match(r"^\s*(re|回复|答复)\s*[:：]", subject, re.I) else f"Re: {subject}".strip()
        msg["In-Reply-To"] = rec.message_id
        refs = list(dict.fromkeys([*self._references_for(ref, rec), rec.message_id]))
        msg["References"] = " ".join(refs)
        msg["Message-ID"] = make_msgid(domain=domain_of(sender) or None)
        msg["Date"] = formatdate(localtime=True)
        msg.set_content(text)
        with self._gate.allow_writes("SMTP SEND"):
            await asyncio.to_thread(self._smtp_send, msg, sender, list(target.recipients))
        return SendReceipt(external_msg_id=str(msg["Message-ID"]), sent_at=datetime.now(timezone.utc))

    def _references_for(self, ref: ThreadRef, rec: MailRecord) -> list[str]:
        if rec.references:
            return list(rec.references)
        root = str(ref.data.get("root") or "")
        return [root] if root and root != rec.message_id else []

    def _smtp_send(self, msg: EmailMessage, sender: str, recipients: list[str]) -> None:
        self._gate.check("SMTP SEND", write=True)
        password = self._secret("smtp_password") or self._secret("password")
        if not password:
            raise SendFailed("no mail password stored for this source")
        try:
            smtp = self._smtp_factory(self.smtp_host, self.smtp_port, self.smtp_security)
            smtp.login(self.username, password)
        except Exception as exc:  # noqa: BLE001 - nothing is sent before DATA: every failure here is "not sent"
            raise SendFailed(f"the mail server did not accept the session: {type(exc).__name__}") from exc
        try:
            smtp.send_message(msg, from_addr=sender, to_addrs=recipients)
        except (smtplib.SMTPRecipientsRefused, smtplib.SMTPSenderRefused, smtplib.SMTPDataError,
                smtplib.SMTPHeloError, smtplib.SMTPNotSupportedError) as exc:
            raise SendFailed(f"the mail server refused the message: {type(exc).__name__}") from exc
        finally:
            try:
                smtp.quit()
            except Exception as exc:  # noqa: BLE001 - the message is handed over or refused already
                logger.debug("SMTP quit failed: %s", type(exc).__name__)


MCP_FAMILIES: dict[str, type[OutlookMcpSource]] = {OutlookMcpSource.FAMILY: OutlookMcpSource}


def create(config: SourceConfig) -> _MailSource:
    backend = config.backend or ""
    if backend.startswith("mcp:"):
        family = str((config.options or {}).get("tool_family") or backend.split(":", 1)[1])
        cls = MCP_FAMILIES.get(family)
        if cls is None:
            raise ValueError(
                f"no mail tool mapping for MCP server family {family!r}; set options.tool_family to one of {sorted(MCP_FAMILIES)}"
            )
        return cls(config)
    if backend == "imap":
        return ImapSmtpSource(config)
    raise ValueError(f"unknown mail backend {backend!r} (mcp:<server id> or imap)")


register_source(KIND, create)
