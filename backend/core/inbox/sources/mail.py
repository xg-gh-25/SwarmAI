"""The mail source (``kind="mail"``): the person's inbox, replies into the same thread.

Design: ``docs/design/2026-11-20-inbox.md`` §4. Two backends, picked by ``SourceConfig.backend``:

- ``mcp:<server id>``: a mail MCP server the person configured, with the tool mapping of
  its server family. The ``aws-outlook-mcp`` mapping ships here (``OutlookMcpSource``):
  ``email_folders`` lists the inbox since the cursor's day, ``email_read`` fetches whole
  conversations, and every conversation is **split into one message per message id**.
  The server reports an Outlook failure inside a normal answer (``success: false``):
  ``_call`` raises it with the server's text (an expired login is ``SourceAuthError``,
  anything else ``ServerError``). In a pull, one conversation that cannot be read is
  skipped: after a passing failure the cursor is held so the next pull reads it again; a
  deleted one (``ServerError.gone``) or one in an unknown shape is skipped for good. A
  failed ``email_reply`` is ``SendFailed`` only when it proves nothing went out (a refused
  login, a missing message); otherwise its outcome is unknown.
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
invites; ``is_bot`` for automated addresses (``is_automated_address``) and auto-submitted mail.

The reply target resolves ``Reply-To``. ``ReplyTarget.flags`` gets ``reply_to_differs``
when ``Reply-To`` is not the sender, and ``external_domain`` when a recipient is outside
the domains of the person's own addresses.

Identity: the From address of the person's own latest sent mail (Outlook) or the IMAP
login when it is an address, plus ``options["extra_addresses"]`` (the person's aliases).

``status_detail()`` says, after ``can_send``, why sending is off (no ``email_reply``, the
writes switch not set, no SMTP server). A reply refused for expired credentials raises
``SendFailed`` with the fix. ``own_messages_to(thread)``: when a reply to the thread goes to
exactly one address and that is the sender's own (no ``Reply-To`` elsewhere), the person's
own sent mail of the last ``history_days`` (60) with that address in To, quoted history
stripped, for drafting; none otherwise.

IMAP options: ``host``, ``port`` (993), ``username``, ``folder`` (INBOX), ``sent_folder``
(found by its ``\\Sent`` flag when not given), ``smtp_host``, ``smtp_port`` (465),
``smtp_security`` (``ssl`` | ``starttls``), ``max_messages`` (50). Both backends:
``extra_addresses``, ``first_pull_hours`` (24), ``pull_budget_s`` (120), ``history_days`` (60).

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
    source_text,
    strip_mail_quotes,
)

logger = logging.getLogger(__name__)

KIND = "mail"
_CALL_CAP_S = 60.0
# An automated sender, by its address: a token like "noreply" or "notifications" anywhere in
# the local part between separators (m365-notifications@, no-reply-aws@), or a subdomain label
# ending in one (anomalydetection@costalerts.example.com). A sender these miss is still
# caught by its headers (Auto-Submitted), by triage, or by the person's quiet_senders.
_AUTOMATED_LOCAL = re.compile(
    r"(^|[+.\-_])(no-?reply|do-?not-?reply|donotreply|mailer-daemon|postmaster|bounces?|"
    r"notifications?|notify|alerts?)([+.\-_]|$)", re.I)
_AUTOMATED_LABEL = re.compile(r"(notifications?|alerts?|notify|bounces?|mailer)$", re.I)


def is_automated_address(addr: str) -> bool:
    local, at, domain = (addr or "").strip().rpartition("@")
    if not at:
        return False
    if _AUTOMATED_LOCAL.search(local):
        return True
    return any(_AUTOMATED_LABEL.search(label) for label in domain.split(".")[:-2])
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
    sent_at: Optional[datetime] = None  # the sender's send time: the same on every copy of one mail
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


_SENT_TO_READS = 5  # sent conversations read for drafting context when the server lists no messages

_LATIN1_HIGH = re.compile(r"[\x80-\xff]")
_CJK = re.compile(r"[\u3400-\u9fff]")
_PUNCT = re.compile(r"[\u2000-\u206f\u3000-\u303f\uff00-\uffef]")
#: what a GBK body holds besides ideographs: its symbol rows (middle dot, times, Greek,
#: Cyrillic, arrows, box drawing, circled numbers, kana) and its user-defined areas, which
#: decode to private use (measured: a Chinese meeting-notes mail carried two)
_GBK_TEXT = re.compile(r"[\u00b7\u00d7\u00f7\u0391-\u03c9\u0401-\u0451\u2000-\u27bf\u3000-\u33ff"
                       r"\u3400-\u9fff\ue000-\uf8ff\ufe30-\ufe4f\uff00-\uffef]")


_BANNER_PARA = re.compile(r"\s*(.+?)(?:\n\s*\n|\Z)", re.S)
_BANNER_WORDS = re.compile(
    r"originated from outside|from an external sender|external (?:e-?mail|sender|message)|"
    r"外部邮件|来自(?:组织|公司)?外部|外部发件人", re.I)


def _without_banner(text: str) -> str:
    """The body without the external-sender banner many mail systems put first
    ("CAUTION: This email originated from outside of the organization..."): the first
    paragraph is dropped when it is short and says the mail came from outside. Measured:
    every external mail in the list began with it, so the excerpt showed no content."""
    found = _BANNER_PARA.match(text or "")
    if found and len(found.group(1)) <= 600 and _BANNER_WORDS.search(found.group(1)):
        rest = text[found.end():].strip()
        return rest or text
    return text


def _undo_latin1(text: str) -> str:
    """A body ``aws-outlook-mcp`` decoded as Latin-1 although its bytes were UTF-8 or GBK,
    decoded again. Measured on real mail: some Chinese bodies come back as ``²âÊÔ``
    (GBK ``测试``) or ``çŽ‹`` (UTF-8 ``王``) while the subject is right. Repaired only
    when every character fits in one byte, some are above ASCII, and the bytes decode
    strictly: as UTF-8, or as GB18030 into Chinese text (ideographs with GBK's own
    symbols and user-defined characters) or into English whose only non-ASCII is
    punctuation (so a Latin-1 body with accents is left alone). Anything else is returned unchanged."""
    if not _LATIN1_HIGH.search(text.replace("\xa0", "")):
        return text  # nothing above ASCII but no-break spaces, which HTML bodies are full of
    try:
        raw = text.encode("latin-1")
    except UnicodeEncodeError:
        return text  # already holds real characters above one byte
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        pass
    try:
        fixed = raw.decode("gb18030")
    except UnicodeDecodeError:
        return text
    wide = [c for c in fixed if ord(c) > 0x7F]
    if len(_CJK.findall(fixed)) >= 2 and all(_GBK_TEXT.match(c) for c in wide):
        return fixed                       # Chinese text
    if wide and not _CJK.search(fixed) and all(_PUNCT.match(c) for c in wide):
        return fixed                       # English whose only non-ASCII is punctuation (today’s)
    return text


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
        sent_at=parse_time(_get(d, "sentDateTime", "dateTimeSent", "DateTimeSent")),
        body=_undo_latin1(_body_text(_get(d, "body", "Body", "bodyText", "textBody", "text", "content", "uniqueBody", "bodyPreview", "preview"))),
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


# Server error codes that say the item is not there (deleted, or an id that never was):
# asking again gives the same answer, so nothing waits for them.
_GONE_CODES = frozenset({"ErrorItemNotFound", "ErrorInvalidIdMalformed", "ErrorInvalidIdEmpty",
                         "ErrorInvalidIdNotAnItemAttachmentId", "ErrorInvalidIdXml"})


class ServerError(SourceUnavailable):
    """The server answered ``success: false``. ``gone``: the item is not there, and asking
    again gives the same answer."""

    def __init__(self, message: str, code: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.gone = code in _GONE_CODES


def server_error(data: Any) -> Optional[tuple[str, str]]:
    """``(text, code)`` of the server's own failure when an answer says ``success: false``
    (the aws-outlook-mcp shape: ``{"success": false, "error": {"message": ..., "code": ...}}``),
    else None."""
    if not isinstance(data, dict) or data.get("success") is not False:
        return None
    err = data.get("error")
    if isinstance(err, dict):
        code = str(err.get("code") or "")
        text = str(err.get("message") or code)
    else:
        code, text = "", str(err or "")
    return (text or "the server reported a failure")[:500], code[:80]


def empty_conversation(data: Any) -> bool:
    """A successful answer that holds no message (a conversation whose mail is gone)."""
    if not isinstance(data, dict) or data.get("success") is not True:
        return False
    content = data.get("content")
    emails = content.get("emails") if isinstance(content, dict) else None
    return isinstance(emails, list) and not emails


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
        self._extra_addresses = [str(a).strip().lower() for a in opts.get("extra_addresses") or () if "@" in str(a)]
        self._first_pull_s = float(opts.get("first_pull_hours", 24)) * 3600
        self._history_s = float(opts.get("history_days", 60)) * 86400
        self._budget_s = float(opts.get("pull_budget_s", 120))
        self._deadline: Optional[Deadline] = None
        self._me: Optional[Identity] = None
        self.read_only_reason = ""

    async def _own_addresses(self) -> list[str]:
        raise NotImplementedError

    async def whoami(self) -> Identity:
        if self._me is None:
            try:
                addresses = [a for a in await self._own_addresses() if a]
            except SourceUnavailable:
                if not self._extra_addresses:
                    raise
                addresses = []    # the configured addresses still say who the person is
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
            text=strip_mail_quotes(_without_banner(rec.body)) or rec.body,
            url=rec.url,
            title=rec.subject,
            is_direct=in_to,
            is_bot=is_automated_address(rec.from_addr or "") or auto,
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
        copies: set[tuple] = set()
        for rec in records:
            if not rec.message_id or rec.message_id in out:
                continue
            # aws-outlook-mcp gives each copy of a mail its own item id and no Message-ID,
            # so a mail the person sent to a list they are on (or to themselves) comes back
            # twice: its sent copy and its received copy share sender, send time and subject.
            copy = (rec.from_addr, rec.sent_at, rec.subject) if rec.from_addr and rec.sent_at else None
            if copy is not None and copy in copies:
                continue
            if copy is not None:
                copies.add(copy)
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

    def status_detail(self) -> str:
        """Why sending is off, set by ``can_send`` ('' when it can send)."""
        return self.read_only_reason

    async def _sent_to(self, address: str, since: datetime) -> list[MailRecord]:
        raise NotImplementedError

    async def own_messages_to(self, thread: Thread, limit: int = 10) -> list[Message]:
        """The person's own latest sent mail to the one address a reply to ``thread`` goes
        to, oldest first: only when that is the sender's own address (one recipient, no
        Reply-To elsewhere); quoted history is stripped, mail from anyone else dropped."""
        me = await self.whoami()
        target = thread.target
        if target is None or target.public or "reply_to_differs" in target.flags or len(target.recipients) != 1:
            return []
        address = str(target.recipients[0] or "").strip().lower()
        if "@" not in address or any(c in address for c in "\r\n\0\"") or me.is_me(address):
            return []
        mine = self._mine(me)
        since = datetime.now(timezone.utc) - timedelta(seconds=self._history_s)
        records = [r for r in self._dedup(await self._sent_to(address, since))
                   if r.from_addr in mine and address in r.to]
        if not limit:
            return []
        return [self._to_message(r, me) for r in records[-int(limit):]]

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
        error = server_error(data if data is not None else result)
        if error is not None:
            # the server reports an Outlook failure inside a normal answer: its text is
            # the cause (an expired login, a busy server, a deleted item), never an empty mailbox
            text, code = error
            raise self._auth_error(text) or ServerError(f"{tool}: {text}", code)
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
                try:   # one unreadable sent conversation leaves the other two to decide
                    read = split_conversations(await self._call("email_read", {"conversationId": cid}), cid)
                except SourceUnavailable:
                    continue
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
            self.read_only_reason = source_text("mail_tools_unreadable", server=self.server_id,
                                                    error=type(exc).__name__)
            return False
        if "email_reply" not in names:
            self.read_only_reason = source_text("mail_no_reply_tool", server=self.server_id)
            return False
        env = {**os.environ, **(getattr(client, "env", None) or {})}
        if str(env.get(self.WRITES_ENV, "")).strip().lower() not in _TRUE:
            self.read_only_reason = source_text("mail_send_off", server=self.server_id,
                                                env=self.WRITES_ENV)
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
            # One conversation that cannot be read does not fail the pull; the others land.
            # - a passing server failure (busy, throttled, a network error): skipped, and the
            #   cursor is held so the next pull reads it again;
            # - a conversation that is gone, or an answer in a shape this mapping does not
            #   read: the same every time, so it is skipped without holding the cursor
            #   (holding would hold it forever);
            # the pull fails when the failures say the source itself is down: every
            # conversation it tried to read failed and none was read, or every one answered
            # in an unknown shape (the mapping no longer fits the server).
            from channels.egress_redactor import redact_text
            retry: list[str] = []
            gone = unknown_shape = 0
            for cid in ids:
                try:
                    answer = await self._call("email_read", {"conversationId": cid, "format": "text"})
                except ServerError as exc:
                    if exc.gone:
                        gone += 1
                    else:
                        retry.append(str(exc))
                    continue
                except SourceUnavailable as exc:
                    retry.append(str(exc))
                    continue
                found = split_conversations(answer, cid)
                if not found and not empty_conversation(answer):
                    unknown_shape += 1
                records.extend(found)
            if unknown_shape:
                logger.warning("mail source %s: %d conversations answered in a shape this mapping does not read; skipped",
                               self.config.id, unknown_shape)
            if retry:
                complete = False
                logger.warning("mail source %s: %d of %d conversations not read this pull, read again next pull (%s)",
                               self.config.id, len(retry), len(ids), redact_text(retry[-1])[:200])
            if retry and not records and len(retry) + gone + unknown_shape == len(ids):
                raise SourceUnavailable(retry[-1])
            if ids and unknown_shape == len(ids):
                raise SourceUnavailable("email_read answered in a shape this mapping does not read")
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
        try:
            answer = await self._call("email_read", {"conversationId": cid, "format": "text"})
        except ServerError as exc:
            if exc.gone:
                return []          # the thread is gone: nothing to reply to, not an outage
            raise
        return split_conversations(answer, cid)

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
        except SourceAuthError as exc:
            raise SendFailed(f"could not read the thread before replying: {exc}", fix=exc.fix) from exc
        except (SourceUnavailable, McpError) as exc:
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
                auth = self._auth_error(str(exc))
                raise SendFailed(str(exc), fix=auth.fix if auth else "") from exc
        data = parse_tool_result(result)
        error = server_error(data if data is not None else result)
        if error is not None:
            # The server said the reply failed: never recorded as sent. Only a refused login
            # or a missing message proves nothing went out (failed, may be sent again); any
            # other failure may have come after the mail left, so it is unknown and the next
            # pull's find_sent settles it.
            text, code = error
            auth = self._auth_error(text)
            if auth is not None or code in _GONE_CODES:
                raise SendFailed(f"email_reply: {text}", fix=auth.fix if auth else "")
            raise ServerError(f"email_reply: {text}", code)
        msg_id = ""
        if isinstance(data, dict):
            msg_id = str(_get(data, "internetMessageId", "messageId", "itemId", "id") or "")
        return SendReceipt(external_msg_id=msg_id, sent_at=datetime.now(timezone.utc))

    async def _sent_to(self, address: str, since: datetime) -> list[MailRecord]:
        # The sent folder of the window; the caller keeps only mail whose To holds the
        # address, so no search result is trusted. aws-outlook-mcp lists conversations,
        # not messages (measured: 50 listed, 0 messages): there a search for the address
        # only picks which few sent conversations to read.
        start = since.date().isoformat()
        data = await self._call("email_folders", {"folder": "sentitems", "startDate": start, "limit": 50})
        records = split_conversations(data)
        if not records:
            found = await self._call("email_search", {"query": address, "folder": "sentitems", "startDate": start})
            for cid in conversation_ids(found)[:_SENT_TO_READS]:
                records.extend(split_conversations(
                    await self._call("email_read", {"conversationId": cid, "format": "text"}), cid))
        return [r for r in records if r.received_at is None or r.received_at >= since]

    async def close(self) -> None:
        if self._client is not None:
            await self._client.close()


# ---------------------------------------------------------------------------
# Backend: IMAP over TLS + SMTP
# ---------------------------------------------------------------------------

_PEEK_SPECS = frozenset({"(UID BODY.PEEK[])"})
# a subject that already says it is a reply (English or Chinese mail clients)
_REPLY_PREFIX = re.compile(r"^\s*(re|回复|答复)\s*[:：]", re.I)


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
            raise SourceAuthError("the mail server refused the login", fix=source_text("mail_login")) from exc

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
    SECRET_NAMES: ClassVar[frozenset[str]] = frozenset({"password", "smtp_password"})
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
            raise SourceAuthError("no mail password stored for this source", fix=source_text("mail_login"))
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
            self.read_only_reason = source_text("mail_no_smtp")
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

    def _sent_to_sync(self, address: str, since: datetime) -> list[MailRecord]:
        imap = self._session()
        try:
            folder = self._find_sent_folder(imap)
            if not folder:
                return []
            validity, _ = imap.examine(folder)
            uids = imap.search("SINCE", since.strftime("%d-%b-%Y"), "TO", imap_quote(address))
            return [_parse_rfc822(raw, f"imap:{validity}:{uid}") for uid, raw in imap.fetch(sorted(uids)[-50:])]
        finally:
            imap.logout()

    async def _sent_to(self, address: str, since: datetime) -> list[MailRecord]:
        return await asyncio.wait_for(asyncio.to_thread(self._sent_to_sync, address, since), timeout=_CALL_CAP_S)

    # -- reply ------------------------------------------------------------------------

    async def reply(self, ref: ThreadRef, text: str) -> SendReceipt:
        if not self.smtp_host:
            raise SendFailed("no smtp_host configured for this mail source")
        try:
            me = await self.whoami()
            rec = self._reply_record(ref, self._dedup(await self._thread_records(ref)), me)
        except SourceAuthError as exc:
            raise SendFailed(f"could not read the thread before replying: {exc}", fix=exc.fix) from exc
        except (SourceUnavailable, OSError, asyncio.TimeoutError) as exc:
            raise SendFailed(f"could not read the thread before replying: {exc}") from exc
        if rec is None:
            raise SendFailed("the message to reply to is no longer in the mailbox")
        target = self._target(rec, me)
        sender = me.display or next(iter(sorted(self._mine(me))), "")
        msg = EmailMessage()
        msg["From"] = sender
        msg["To"] = ", ".join(target.recipients)
        subject = rec.subject or ""
        msg["Subject"] = subject if _REPLY_PREFIX.match(subject) else f"Re: {subject}".strip()
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
            raise SendFailed("no mail password stored for this source", fix=source_text("mail_login"))
        try:
            smtp = self._smtp_factory(self.smtp_host, self.smtp_port, self.smtp_security)
            smtp.login(self.username, password)
        except smtplib.SMTPAuthenticationError as exc:
            raise SendFailed("the mail server refused the login", fix=source_text("mail_login")) from exc
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
