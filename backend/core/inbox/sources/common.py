"""Helpers every Inbox source shares: the read-only tool gate, text matching, addresses.

- ``ToolGate``: every call a source makes to its backend (an MCP tool, a Web API method,
  a ``gh api`` endpoint, an IMAP command) passes ``check`` first. A name outside the
  source's ``READ_TOOLS`` is refused; a write is refused unless it is in the source's
  write list **and** the caller is inside ``allow_writes`` (only ``reply`` enters it).
  A gate with argument lists (``allowed_args``) lets a tool receive only the argument
  names on its list, and a tool without a list none at all, so a side-effecting argument
  (``email_read(markAs=...)``, extra recipients on a reply) never reaches the server; a
  gate built with ``require_args=True`` refuses to exist while any of its tools lacks a
  list. A gate without lists (IMAP, ``gh``, feeds: no argument dicts) checks tool names
  only. So ``pull``/``thread``/``find_sent`` cannot write, whatever a mapping or a bug
  asks for.
- ``normalize_text`` / ``matches_sent``: the comparison ``find_sent`` uses. Formatting is
  removed first (Slack mrkdwn and its ``<url|label>`` links, Markdown, HTML entities, mail
  quoting, "On ... wrote:" history and the signature), then whitespace is collapsed and
  case folded; two texts match when their first 80 characters are equal.
- ``Deadline``: one monotonic budget for a whole pull, so a serial chain of calls cannot
  add up past it; each call gets ``min(its own cap, what is left)``.
- Address helpers for mail: ``parse_address``, ``domain_of``.

Key public symbols: ``ReadToolRefused``, ``ToolGate``, ``Deadline``, ``BudgetExhausted``,
``normalize_text``, ``matches_sent``, ``strip_mail_quotes``, ``parse_address``,
``domain_of``, ``parse_time``, ``SourceUnavailable``, ``auth_fix_for``.
"""
from __future__ import annotations

import html
import re
import time
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from email.utils import parseaddr, parsedate_to_datetime
from typing import Any, Iterator, Mapping, Optional

MATCH_CHARS = 80


class ReadToolRefused(PermissionError):
    """A source tried a call outside its read list, or a write outside ``reply``."""


class SourceUnavailable(RuntimeError):
    """The source cannot work as configured (no server, no mapping, unreadable output)."""


class BudgetExhausted(TimeoutError):
    """The pull's time budget ran out before every call was made."""


# (gate token, tool) pairs the current task may write with; set only inside ToolGate.allow_writes.
_WRITES: ContextVar[frozenset] = ContextVar("inbox_source_writes", default=frozenset())


class ToolGate:
    """The fixed lists of what one source may call."""

    def __init__(
        self,
        source: str,
        read_tools: frozenset[str],
        write_tools: frozenset[str] = frozenset(),
        allowed_args: Optional[Mapping[str, frozenset[str]]] = None,
        *,
        require_args: bool = False,
    ) -> None:
        self.source = source
        self.read_tools = frozenset(read_tools)
        self.write_tools = frozenset(write_tools)
        self.allowed_args = dict(allowed_args or {})
        if require_args:
            missing = sorted((self.read_tools | self.write_tools) - set(self.allowed_args))
            if missing:
                raise ValueError(f"{source}: no argument list for {missing}")
        self._token = object()  # identifies this gate's write permissions; never reused

    def check(self, tool: str, args: Optional[Mapping[str, Any]] = None, *, write: bool = False) -> None:
        if write:
            if tool not in self.write_tools or (self._token, tool) not in _WRITES.get():
                raise ReadToolRefused(f"{self.source}: write {tool!r} refused outside reply")
        elif tool not in self.read_tools:
            raise ReadToolRefused(f"{self.source}: {tool!r} is not in READ_TOOLS")
        # A gate with argument lists refuses every argument of a tool it has no list for.
        allowed = self.allowed_args.get(tool, frozenset()) if self.allowed_args else None
        if allowed is not None:
            extra = set(args or ()) - allowed
            if extra:
                raise ReadToolRefused(f"{self.source}: {tool!r} refused with argument(s) {sorted(extra)}")

    @contextmanager
    def allow_writes(self, *tools: str) -> Iterator[None]:
        """Let the current task (and threads it starts with ``asyncio.to_thread``) make
        these writes until the block ends."""
        unknown = set(tools) - self.write_tools
        if unknown:
            raise ReadToolRefused(f"{self.source}: {sorted(unknown)} are not write tools")
        token = _WRITES.set(_WRITES.get() | {(self._token, t) for t in tools})
        try:
            yield
        finally:
            _WRITES.reset(token)


class Deadline:
    """A monotonic time budget shared by every call of one operation."""

    def __init__(self, seconds: float) -> None:
        self._end = time.monotonic() + max(seconds, 0.0)

    def remaining(self) -> float:
        return self._end - time.monotonic()

    def timeout(self, cap: float) -> float:
        left = self.remaining()
        if left <= 0:
            raise BudgetExhausted("the pull's time budget is spent")
        return min(cap, left)


def call_timeout(deadline: Optional[Deadline], cap: float) -> tuple[float, bool]:
    """The timeout for one call, and whether the budget (not the call's own cap) set it.
    A call that times out on a budget-set timeout ends the pull as ``BudgetExhausted``
    (keep what was collected), not as a failed pull."""
    if deadline is None:
        return cap, False
    timeout = deadline.timeout(cap)
    return timeout, timeout < cap


# ---------------------------------------------------------------------------
# Text normalisation for find_sent
# ---------------------------------------------------------------------------

_SLACK_LINK = re.compile(r"<((?:https?|mailto):[^>|]+)\|([^>]+)>")
_SLACK_BARE = re.compile(r"<((?:https?|mailto):[^>|]+)>")
_SLACK_USER = re.compile(r"<@([A-Z0-9]+)(?:\|[^>]*)?>")
_SLACK_CHANNEL = re.compile(r"<#([A-Z0-9]+)\|([^>]*)>")
_SLACK_SPECIAL = re.compile(r"<!(here|channel|everyone)(?:\|[^>]*)?>")
_MD_LINK = re.compile(r"\[([^\]]+)\]\((?:[^)]+)\)")
_HTML_TAG = re.compile(r"</?[A-Za-z][^>\n]{0,200}>")
_FENCE = re.compile(r"```")
_EMPHASIS = re.compile(r"(?<![\w*_~`])[*_~`]+|[*_~`]+(?![\w*_~`])")
_WS = re.compile(r"\s+")

# Lines that start the quoted history of a reply.
_QUOTE_HEADERS = (
    re.compile(r"^\s*On .{3,200}wrote:\s*$", re.I),
    re.compile(r"^\s*-{2,}\s*Original Message\s*-{2,}\s*$", re.I),
    re.compile(r"^\s*_{8,}\s*$"),  # Outlook's separator line
    re.compile(r"^\s*(在|于).{2,200}(写道|寫道)[:：]\s*$"),
    re.compile(r"^\s*-----\s*原始邮件\s*-----\s*$"),
)
_SIGNATURE = re.compile(r"^-- ?$")
# the sender may be on the same line or, in a text rendering of Outlook's header, the next
_FROM_LINE = re.compile(r"^\s*(From|发件人|寄件者)\s*[:：]", re.I)
_SENT_LINE = re.compile(r"^\s*(Sent|Date|发送时间|日期|寄件日期)\s*[:：]", re.I)


def strip_mail_quotes(text: str) -> str:
    """The new part of a mail body: before the quoted history and the signature, without
    ``>``-quoted lines."""
    kept: list[str] = []
    lines = (text or "").replace("\r\n", "\n").split("\n")
    for i, line in enumerate(lines):
        if _SIGNATURE.match(line) or any(p.match(line) for p in _QUOTE_HEADERS):
            break
        if _FROM_LINE.match(line) and any(_SENT_LINE.match(n) for n in lines[i + 1:i + 7]):
            break  # Outlook's header block of the quoted mail: From: ... Sent:/Date: ...
        if line.lstrip().startswith(">"):
            continue
        kept.append(line)
    return "\n".join(kept).strip()


def normalize_text(text: str) -> str:
    """Text without formatting, quoting or signature, whitespace collapsed, case folded."""
    t = html.unescape(strip_mail_quotes(text or ""))
    t = _SLACK_LINK.sub(r"\2", t)
    t = _SLACK_BARE.sub(r"\1", t)
    t = _SLACK_USER.sub(r"@\1", t)
    t = _SLACK_CHANNEL.sub(r"#\2", t)
    t = _SLACK_SPECIAL.sub(r"@\1", t)
    t = _MD_LINK.sub(r"\1", t)
    t = _HTML_TAG.sub(" ", t)
    t = _FENCE.sub(" ", t)
    t = _EMPHASIS.sub("", t)
    return _WS.sub(" ", t).strip().casefold()


def matches_sent(candidate: str, sent: str, chars: int = MATCH_CHARS) -> bool:
    """True when the two texts' first ``chars`` characters agree once formatting is gone."""
    want = normalize_text(sent)[:chars]
    return bool(want) and normalize_text(candidate)[:chars] == want


# ---------------------------------------------------------------------------
# Addresses and times
# ---------------------------------------------------------------------------


def parse_address(value: Any) -> tuple[str, str]:
    """``(name, address)`` from ``"Name <a@b>"``, a bare address, or a dict in the Graph
    (``{"emailAddress": {"name", "address"}}``), EWS (``{"Name", "EmailAddress"}``) or plain
    (``{"name", "address"|"email"}``) shape. The address is lower-cased; ``("", "")`` when
    nothing usable is there."""
    if isinstance(value, Mapping):
        inner = value.get("emailAddress") if isinstance(value.get("emailAddress"), Mapping) else value
        name = inner.get("name") or inner.get("Name") or inner.get("displayName") or ""
        addr = (
            inner.get("address") or inner.get("email") or inner.get("EmailAddress")
            or inner.get("emailAddress") or inner.get("smtp") or ""
        )
        if isinstance(addr, str) and "<" in addr:
            n2, addr = parseaddr(addr)
            name = name or n2
        return str(name).strip(), str(addr).strip().lower() if isinstance(addr, str) else ""
    if isinstance(value, str):
        name, addr = parseaddr(value)
        return name.strip(), addr.strip().lower()
    return "", ""


def parse_addresses(value: Any) -> list[tuple[str, str]]:
    """Every address in a list, a comma-separated header, or a single value."""
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        out = [parse_address(v) for v in value]
    elif isinstance(value, str) and "," in value:
        from email.utils import getaddresses

        out = [(n.strip(), a.strip().lower()) for n, a in getaddresses([value])]
    else:
        out = [parse_address(value)]
    return [(n, a) for n, a in out if a]


def domain_of(address: str) -> str:
    return address.rsplit("@", 1)[-1].lower() if "@" in address else ""


def parse_time(value: Any) -> Optional[datetime]:
    """An aware datetime from ISO-8601, an RFC 2822 date, a Slack ts or epoch seconds."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value), tz=timezone.utc)
    text = str(value).strip()
    if re.fullmatch(r"\d{9,11}(\.\d+)?", text):
        return datetime.fromtimestamp(float(text), tz=timezone.utc)
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        pass
    try:
        dt = parsedate_to_datetime(text)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError, IndexError):
        return None


_HTTP_401 = re.compile(r"\b(?:http|status|code|error)\W{0,3}401\b", re.I)
_AUTH_MARKERS = (
    "unauthorized", "unauthenticated", "not authenticated", "authentication failed",
    "invalid_auth", "not_authed", "token_expired", "token_revoked", "token expired",
    "expired token", "credentials have expired", "session expired", "login required",
    "please log in", "please login", "mwinit", "midway", "bad credentials", "re-authenticate",
)


def looks_like_auth_failure(text: str) -> bool:
    low = (text or "").lower()
    return any(marker in low for marker in _AUTH_MARKERS) or bool(_HTTP_401.search(low))


def auth_fix_for(text: str, server_id: str) -> str:
    """The action to show for an expired login of an MCP server."""
    low = (text or "").lower()
    if "mwinit" in low or "midway" in low:
        return "运行 mwinit -f"
    return f"重新登录 MCP server「{server_id}」后点重试"
