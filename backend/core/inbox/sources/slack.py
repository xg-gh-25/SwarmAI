"""The Slack source (``kind="slack"``): the person's DMs, mentions, threads and watched channels.

Design: ``docs/design/2026-11-20-inbox.md`` §4. Two backends, picked by ``SourceConfig.backend``:

- ``mcp:<server id>``: a Slack MCP server the person configured, called through
  ``core.mcp_stdio_client`` with the tool mapping of its server family. The mapping for
  the ``slack-mcp`` family ships here (``SlackMcpSource``); another family is a subclass
  with its own ``READ_TOOLS`` and calls, registered in ``MCP_FAMILIES``. The family is
  ``options["tool_family"]``, or the server id when it names a known family.
- ``slack-web``: the Slack Web API with the person's user token (``xoxp-``), read from the
  OS credential store under ``<source id>:user_token`` (``credentials.py``).

Pull, since the cursor:

1. DMs with new messages (``list_dms`` / ``conversations.list``). Each DM keeps its own
   position in the cursor, so a DM is never skipped past: DMs whose backend says they
   were active since their position are read, and DMs whose activity is unknown are read
   ``max_dms`` per pull, least recently checked first;
2. messages that mention the person (search for ``<@me>``);
3. new replies in threads the person took part in (search for the person's own messages of
   the last ``thread_lookback_days``, then each thread's replies);
4. messages in ``options["watched_channels"]`` (names or ids), marked ``watched``.

Histories and searches are read page by page (``max_pages``), so a busy channel is not cut
at the first 100 messages; what is still beyond the last page is logged. Search results
lag behind posting, so the search legs look ``SEARCH_OVERLAP_S`` behind the cursor; repeats
are harmless (one item per external id ``<channel>:<ts>``). The whole pull shares one time
budget (``pull_budget_s``); when it runs out (a call cut short by it included) the pull
returns what it has, keeps its overall position and saves the DMs it finished.

The cursor is ``{"v": 2, "ts": <newest ts>, "dm": {<dm id>: [<ts read up to>, <checked
at>]}}`` as JSON; a bare ts from an older pull is read as ``ts``.

``reply`` posts into the thread (``thread_ts``); a DM message outside a thread is answered
in the DM. Identity comes from the backend: ``auth.test`` for the Web API; for slack-mcp,
which has no "who am I" tool, the author of the person's own latest message (search
``from:me``).

Options: ``watched_channels``, ``tool_family`` (mcp), ``max_dms`` (50), ``max_threads``
(20), ``max_pages`` (5), ``thread_lookback_days`` (7), ``first_pull_hours`` (24),
``pull_budget_s`` (120).

Key public symbols: ``SlackMcpSource``, ``SlackWebSource``, ``MCP_FAMILIES``, ``create``.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import Any, ClassVar, Optional
from urllib.parse import parse_qs, urlparse

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
    SourceUnavailable,
    ToolGate,
    auth_fix_for,
    call_timeout,
    looks_like_auth_failure,
    matches_sent,
)

logger = logging.getLogger(__name__)

KIND = "slack"
SEARCH_OVERLAP_S = 600.0
_CALL_CAP_S = 30.0
_PAGE = 100
_CHANNEL_ID = re.compile(r"^[CDG][A-Z0-9]{6,}$")


def _ts(value: Any) -> float:
    """A Slack timestamp as seconds: the numeric ``"1732080000.123456"`` form, or an ISO
    time (``get_messages`` of the slack-mcp family returns ``timestamp`` that way); 0 when
    it is neither."""
    try:
        return float(value)
    except (TypeError, ValueError):
        pass
    if isinstance(value, str) and value:
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return 0.0
        return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).timestamp()
    return 0.0


_LINK_TS_RE = re.compile(r"/p(\d{10})(\d{6})(?![0-9])")


def _slack_ts(raw: dict) -> Optional[str]:
    """The message's own Slack ts (``seconds.micros``), which a thread reply must name:
    the numeric ``ts`` when the answer has one, else the one in its permalink
    (``…/p1732080000123456``), else its ISO ``timestamp`` turned into seconds (good for
    order and the cursor, not for replying)."""
    value = _first(raw, "ts", "messageTs", "message_ts")
    if value is not None:
        try:
            float(value)
            return str(value)
        except (TypeError, ValueError):
            pass
    link = raw.get("permalink")
    if isinstance(link, str):
        found = _LINK_TS_RE.search(link)
        if found:
            return f"{found.group(1)}.{found.group(2)}"
    seconds = _ts(_first(raw, "ts", "timestamp", "messageTs", "message_ts"))
    return f"{seconds:.6f}" if seconds > 0 else None


def _first(d: dict, *names: str) -> Any:
    for name in names:
        value = d.get(name)
        if value not in (None, "", [], {}):
            return value
    return None


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _as_aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def messages_in(data: Any) -> list[dict]:
    """The message dicts in a Slack-shaped answer: a list, or a dict holding one under
    ``messages`` / ``matches`` / ``replies`` / ``results`` / ``items`` / ``data`` (nested,
    as in ``search.messages``), plus a thread's ``parent``/``root`` message."""
    if isinstance(data, list):
        return [d for d in data if isinstance(d, dict)]
    if not isinstance(data, dict):
        return []
    found: list[dict] = []
    for key in ("parent", "root", "parentMessage"):
        if isinstance(data.get(key), dict):
            found.append(data[key])
    for key in ("messages", "matches", "replies", "results", "items", "data"):
        value = data.get(key)
        if isinstance(value, list):
            return found + [d for d in value if isinstance(d, dict)]
        if isinstance(value, dict):
            inner = messages_in(value)
            if inner:
                return found + inner
    if not found and "ts" in data:
        return [data]
    return found


class _SlackSource:
    """Pull / thread / reply / find_sent over five backend operations the subclasses implement."""

    kind: ClassVar[str] = KIND
    category: ClassVar[SourceCategory] = SourceCategory.CONVERSATION
    READ_TOOLS: ClassVar[frozenset[str]] = frozenset()
    WRITE_TOOLS: ClassVar[frozenset[str]] = frozenset()
    ALLOWED_ARGS: ClassVar[dict[str, frozenset[str]]] = {}

    def __init__(self, config: SourceConfig) -> None:
        self.config = config
        opts = config.options or {}
        # every Slack backend passes argument dicts: each tool must have its argument list
        self._gate = ToolGate(f"slack:{config.id}", self.READ_TOOLS, self.WRITE_TOOLS, self.ALLOWED_ARGS, require_args=True)
        self._watched = [str(c).strip() for c in opts.get("watched_channels") or () if str(c).strip()]
        self._max_dms = int(opts.get("max_dms", 50))
        self._max_threads = int(opts.get("max_threads", 20))
        self._max_pages = max(1, int(opts.get("max_pages", 5)))
        self._thread_lookback_s = float(opts.get("thread_lookback_days", 7)) * 86400
        self._first_pull_s = float(opts.get("first_pull_hours", 24)) * 3600
        self._budget_s = float(opts.get("pull_budget_s", 120))
        self._deadline: Optional[Deadline] = None
        self._me: Optional[Identity] = None
        self._me_id = ""

    # -- backend operations (subclasses) ------------------------------------

    async def _auth(self) -> tuple[str, str]:
        raise NotImplementedError

    async def _list_dms(self) -> list[tuple[str, Optional[float]]]:
        raise NotImplementedError

    async def _history_page(
        self, channel: str, oldest: float, latest: Optional[str], limit: int, *, with_replies: bool = False
    ) -> list[dict]:
        """At most ``limit`` messages of ``channel`` after ``oldest`` (and before ``latest``,
        a ts, when given), newest first."""
        raise NotImplementedError

    async def _search_page(self, query: str, count: int, page: int) -> list[dict]:
        raise NotImplementedError

    async def _replies(self, channel: str, thread_ts: str) -> list[dict]:
        raise NotImplementedError

    async def _post(self, channel: str, text: str, thread_ts: Optional[str]) -> tuple[str, str]:
        raise NotImplementedError

    async def _resolve_channel(self, name: str) -> Optional[str]:
        return name

    def _timeout(self, cap: float = _CALL_CAP_S) -> tuple[float, bool]:
        return call_timeout(self._deadline, cap)

    async def _history(self, channel: str, oldest: float, *, with_replies: bool = False) -> list[dict]:
        """Every message of ``channel`` after ``oldest``, page by page (newest first)."""
        out: list[dict] = []
        seen: set[str] = set()
        latest: Optional[str] = None
        for _ in range(self._max_pages):
            batch = await self._history_page(channel, oldest, latest, _PAGE, with_replies=with_replies)
            fresh = [r for r in batch if str(_first(r, "ts", "timestamp") or "") not in seen]
            out.extend(fresh)
            seen.update(str(_first(r, "ts", "timestamp") or "") for r in fresh)
            stamps = [_ts(_first(r, "ts", "timestamp")) for r in batch if _ts(_first(r, "ts", "timestamp")) > 0]
            if len(batch) < _PAGE or not fresh or not stamps or min(stamps) <= oldest:
                return out
            latest = f"{min(stamps):.6f}"
        logger.warning(
            "slack source %s: there may be more than %d messages since the cursor in %s; older ones are not read this pull",
            self.config.id, self._max_pages * _PAGE, channel,
        )
        return out

    async def _search(self, query: str, after: float) -> list[dict]:
        """Search results newer than ``after``, page by page (sorted newest first)."""
        out: list[dict] = []
        for page in range(1, self._max_pages + 1):
            batch = await self._search_page(query, _PAGE, page)
            out.extend(batch)
            stamps = [_ts(_first(r, "ts", "timestamp")) for r in batch]
            if len(batch) < _PAGE or not stamps or min(stamps) <= after:
                return out
        logger.warning(
            "slack source %s: a search may have more than %d results since the cursor; older ones are not read",
            self.config.id, self._max_pages * _PAGE,
        )
        return out

    # -- Source protocol ------------------------------------------------------

    async def whoami(self) -> Identity:
        if self._me is None:
            user_id, name = await self._auth()
            if not user_id:
                raise SourceUnavailable(f"{self.config.id}: Slack did not say who you are")
            self._me_id = user_id
            self._me = Identity(ids=frozenset({user_id} | ({name} if name else set())), display=name or user_id)
        return self._me

    async def can_send(self) -> bool:
        await self.whoami()
        return True

    @staticmethod
    def _read_cursor(cursor: Optional[str]) -> tuple[Optional[float], dict[str, list]]:
        if not cursor:
            return None, {}
        if cursor.lstrip().startswith("{"):
            try:
                data = json.loads(cursor)
            except ValueError:
                return None, {}
            dms = {str(k): list(v) for k, v in (data.get("dm") or {}).items() if isinstance(v, (list, tuple)) and len(v) == 2}
            return (_ts(data.get("ts")) or None), dms
        return (_ts(cursor) or None), {}

    async def pull(self, cursor: Optional[str]) -> PullResult:
        await self.whoami()
        since_old, dm_state = self._read_cursor(cursor)
        since = since_old or time.time() - self._first_pull_s
        collected: dict[str, Message] = {}
        complete = True
        self._deadline = Deadline(self._budget_s)
        try:
            dms = await self._list_dms()
            listed = {d for d, _ in dms}
            dm_state = {d: v for d, v in dm_state.items() if d in listed}
            for d in listed:
                dm_state.setdefault(d, [f"{since:.6f}", 0.0])  # a DM first seen keeps this position
            known = sorted(((d, l) for d, l in dms if l is not None), key=lambda x: x[1], reverse=True)
            to_read = [d for d, latest in known if latest > _ts(dm_state[d][0])]
            unknown = [d for d, l in dms if l is None]
            unknown.sort(key=lambda d: float(dm_state[d][1] or 0.0))
            to_read += unknown[: self._max_dms]
            for channel in to_read:
                dm_since = _ts(dm_state[channel][0])
                newest = dm_since
                for raw in await self._history(channel, dm_since):
                    m = self._collect(collected, raw, channel, dm_since, is_dm=True)
                    if m is not None:
                        newest = max(newest, _ts(m.extra.get("ts")))
                dm_state[channel] = [f"{newest:.6f}", time.time()]

            search_since = since - SEARCH_OVERLAP_S
            for raw in await self._search(f"<@{self._me_id}>", search_since):
                self._collect(collected, raw, None, search_since, mention=True)

            after = (datetime.fromtimestamp(since - self._thread_lookback_s, tz=timezone.utc) - timedelta(days=1)).date()
            threads: list[tuple[str, str]] = []
            for raw in await self._search(f"from:<@{self._me_id}> after:{after.isoformat()}", since - self._thread_lookback_s):
                key = self._thread_of(raw)
                if key and key not in threads:
                    threads.append(key)
            for channel, thread_ts in threads[: self._max_threads]:
                for raw in await self._replies(channel, thread_ts):
                    self._collect(collected, raw, channel, since)

            for name in self._watched:
                channel = await self._resolve_channel(name)
                if not channel:
                    logger.warning("slack source %s: watched channel %r not found", self.config.id, name)
                    continue
                for raw in await self._history(channel, since, with_replies=True):
                    self._collect(collected, raw, channel, since, watched=True)
        except BudgetExhausted:
            complete = False
            logger.warning("slack source %s: pull budget of %.0fs spent; keeping the old position", self.config.id, self._budget_s)
        finally:
            self._deadline = None
        messages = tuple(sorted(collected.values(), key=lambda m: m.received_at))
        newest_all = max([since] + [_ts(m.extra.get("ts")) for m in messages])
        position = newest_all if complete else since_old
        out = {"v": 2, "ts": f"{position:.6f}" if position else None, "dm": dm_state}
        return PullResult(messages, json.dumps(out, sort_keys=True))

    async def thread(self, ref: ThreadRef) -> Thread:
        messages = await self._thread_messages(ref, time.time() - 7 * 86400)
        return Thread(ref=ref, messages=messages, target=await self._target(ref, messages))

    async def reply(self, ref: ThreadRef, text: str) -> SendReceipt:
        channel, thread_ts = self._ref_parts(ref)
        ts, url = await self._post(channel, text, thread_ts)
        return SendReceipt(
            external_msg_id=f"{channel}:{ts}" if ts else "",
            url=url,
            sent_at=datetime.fromtimestamp(_ts(ts), tz=timezone.utc) if ts else datetime.now(timezone.utc),
        )

    async def find_sent(self, ref: ThreadRef, after: datetime, text: str) -> Optional[SendReceipt]:
        me = await self.whoami()
        after = _as_aware(after)
        for m in await self._thread_messages(ref, after.timestamp()):
            if me.is_me(m.author_id) and m.received_at > after and matches_sent(m.text, text):
                return SendReceipt(external_msg_id=m.external_id, url=m.url, sent_at=m.received_at)
        return None

    async def close(self) -> None:
        return None

    # -- helpers ---------------------------------------------------------------

    @staticmethod
    def _ref_parts(ref: ThreadRef) -> tuple[str, Optional[str]]:
        channel = str(ref.data.get("channel") or "")
        if not channel:
            raise SendFailed("the thread reference names no channel")
        thread_ts = ref.data.get("thread_ts")
        return channel, (str(thread_ts) if thread_ts else None)

    async def _thread_messages(self, ref: ThreadRef, oldest: float) -> tuple[Message, ...]:
        await self.whoami()
        channel, thread_ts = self._ref_parts(ref)
        raws = await self._replies(channel, thread_ts) if thread_ts else await self._history(channel, oldest)
        out: dict[str, Message] = {}
        for raw in raws:
            m = self._to_message(raw, channel, is_dm=channel.startswith("D"))
            if m is not None:
                out.setdefault(m.external_id, m)
        return tuple(sorted(out.values(), key=lambda m: m.received_at))

    async def _target(self, ref: ThreadRef, messages: tuple[Message, ...]) -> ReplyTarget:
        me = await self.whoami()
        channel, thread_ts = self._ref_parts(ref)
        others = [m.author_name for m in messages if not me.is_me(m.author_id) and m.author_name]
        channel_name = next((m.extra.get("channel_name") for m in messages if m.extra.get("channel_name")), "")
        if channel.startswith("D"):
            label = f"私信 · {others[-1] if others else channel}"
        else:
            label = f"#{channel_name or channel} 的 thread"
        recipient = f"{channel}:{thread_ts}" if thread_ts else channel
        return ReplyTarget(label=label, recipients=(recipient,))

    def _thread_of(self, raw: dict) -> Optional[tuple[str, str]]:
        channel = raw.get("channel")
        channel_id = channel.get("id") if isinstance(channel, dict) else channel
        channel_id = channel_id or _first(raw, "channel_id", "channelId")
        thread_ts = _first(raw, "thread_ts", "threadTs") or self._thread_ts_from_link(raw) or _first(raw, "ts")
        if not channel_id or not thread_ts:
            return None
        return str(channel_id), str(thread_ts)

    @staticmethod
    def _thread_ts_from_link(raw: dict) -> Optional[str]:
        link = raw.get("permalink") or ""
        if not isinstance(link, str) or "thread_ts" not in link:
            return None
        values = parse_qs(urlparse(link).query).get("thread_ts")
        return values[0] if values else None

    def _to_message(self, raw: dict, channel_hint: Optional[str], *, is_dm: bool = False) -> Optional[Message]:
        ts = _slack_ts(raw)
        channel = raw.get("channel")
        channel_name = ""
        if isinstance(channel, dict):
            channel_name = str(channel.get("name") or "")
            is_dm = is_dm or bool(channel.get("is_im") or channel.get("is_mpim"))
            channel = channel.get("id")
        channel = channel or _first(raw, "channel_id", "channelId") or channel_hint
        channel_name = channel_name or str(_first(raw, "channel_name", "channelName") or "")
        if not ts or not channel or _ts(ts) <= 0:
            return None
        ts, channel = str(ts), str(channel)
        user = raw.get("user")
        user_name = ""
        is_bot = bool(raw.get("bot_id")) or raw.get("subtype") == "bot_message"
        if isinstance(user, dict):
            user_name = str(_first(user, "real_name", "display_name", "name") or "")
            is_bot = is_bot or bool(user.get("is_bot"))
            user = user.get("id")
        user_id = str(user or _first(raw, "user_id", "userId", "bot_id") or "")
        profile = raw.get("user_profile") if isinstance(raw.get("user_profile"), dict) else {}
        user_name = user_name or str(
            _first(raw, "user_name", "userName", "real_name", "realName", "username", "display_name", "author")
            or _first(profile, "real_name", "display_name", "name")
            or user_id
        )
        text = str(raw.get("text") or "")
        is_dm = is_dm or channel.startswith("D") or bool(raw.get("is_im"))
        thread_ts = _first(raw, "thread_ts", "threadTs") or self._thread_ts_from_link(raw)
        thread_ts = str(thread_ts) if thread_ts else None
        ref = ThreadRef(KIND, {"channel": channel, "thread_ts": thread_ts or (None if is_dm else ts)})
        return Message(
            external_id=f"{channel}:{ts}",
            thread=ref,
            author_id=user_id,
            author_name=user_name,
            received_at=datetime.fromtimestamp(_ts(ts), tz=timezone.utc),
            text=text,
            url=str(raw.get("permalink") or ""),
            is_direct=is_dm,
            mentions_me=bool(self._me_id) and f"<@{self._me_id}>" in text,
            is_bot=is_bot,
            extra={"ts": ts, "thread_ts": thread_ts, "channel_name": channel_name},
        )

    def _collect(
        self,
        collected: dict[str, Message],
        raw: dict,
        channel_hint: Optional[str],
        after: float,
        *,
        is_dm: bool = False,
        mention: bool = False,
        watched: bool = False,
    ) -> Optional[Message]:
        m = self._to_message(raw, channel_hint, is_dm=is_dm)
        if m is None or _ts(m.extra.get("ts")) <= after:
            return None
        if mention or watched:
            m = replace(m, mentions_me=m.mentions_me or mention, watched=m.watched or watched)
        old = collected.get(m.external_id)
        if old is not None:
            m = replace(
                old,
                is_direct=old.is_direct or m.is_direct,
                mentions_me=old.mentions_me or m.mentions_me,
                watched=old.watched or m.watched,
                url=old.url or m.url,
            )
        collected[m.external_id] = m
        return m


# ---------------------------------------------------------------------------
# Backend: a Slack MCP server of the slack-mcp family
# ---------------------------------------------------------------------------


class SlackMcpSource(_SlackSource):
    """The ``slack-mcp`` tool mapping: search, list_dms, get_messages, get_thread, post_message."""

    FAMILY: ClassVar[str] = "slack-mcp"
    READ_TOOLS: ClassVar[frozenset[str]] = frozenset({"search", "list_dms", "get_messages", "get_thread"})
    WRITE_TOOLS: ClassVar[frozenset[str]] = frozenset({"post_message"})
    ALLOWED_ARGS: ClassVar[dict[str, frozenset[str]]] = {
        "search": frozenset({"query", "sort", "sort_dir", "count", "scope", "page"}),
        "list_dms": frozenset({"limit"}),
        "get_messages": frozenset({"channel", "since", "until", "limit", "includeThreadReplies", "maxThreadsToFetch"}),
        "get_thread": frozenset({"channel", "threadTs", "limit"}),
        "post_message": frozenset({"channel", "text", "asUser", "replyTo", "reply_broadcast"}),
    }

    def __init__(self, config: SourceConfig, *, client: Any = None) -> None:
        super().__init__(config)
        self.server_id = config.backend.split(":", 1)[1] if config.backend.startswith("mcp:") else config.backend
        self._client = client

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

    async def _call(self, tool: str, args: dict, *, write: bool = False) -> Any:
        from core.mcp_stdio_client import McpError, McpTimeout, McpToolError, parse_tool_result

        self._gate.check(tool, args, write=write)
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
            # Not JSON: reading it as "no messages" would move the cursor past real ones.
            raise SourceUnavailable(f"{tool}: the answer is not in a shape the {self.FAMILY} mapping reads")
        return data

    async def _auth(self) -> tuple[str, str]:
        data = await self._call(
            "search", {"query": "from:me", "sort": "timestamp", "sort_dir": "desc", "count": 1, "scope": "messages"}
        )
        for raw in messages_in(data):
            user = raw.get("user")
            if isinstance(user, dict):
                return str(user.get("id") or ""), str(_first(user, "name", "display_name", "real_name") or "")
            if user or raw.get("user_id") or raw.get("userId"):
                return str(user or raw.get("user_id") or raw.get("userId")), str(
                    _first(raw, "username", "user_name", "userName", "real_name") or ""
                )
        raise SourceUnavailable(
            f"MCP server {self.server_id}: cannot tell who you are (no message of yours found); "
            "post one Slack message, then retry"
        )

    async def _list_dms(self) -> list[tuple[str, Optional[float]]]:
        data = await self._call("list_dms", {"limit": max(1, min(self._max_dms, 500))})
        items = data if isinstance(data, list) else next(
            (data.get(k) for k in ("dms", "conversations", "channels", "items", "results") if isinstance(data, dict) and isinstance(data.get(k), list)),
            [],
        )
        out: list[tuple[str, Optional[float]]] = []
        for item in items or ():
            if not isinstance(item, dict):
                continue
            channel = _first(item, "id", "channel", "channelId", "channel_id")
            if isinstance(channel, dict):
                channel = channel.get("id")
            if not channel:
                continue
            out.append((str(channel), _latest_of(item)))
        return out

    async def _history_page(
        self, channel: str, oldest: float, latest: Optional[str], limit: int, *, with_replies: bool = False
    ) -> list[dict]:
        args: dict[str, Any] = {"channel": channel, "since": _iso(oldest), "limit": limit, "includeThreadReplies": with_replies}
        if latest:
            args["until"] = _iso(_ts(latest) + 1)  # whole seconds: overlap by one, repeats are dropped
        if with_replies:
            args["maxThreadsToFetch"] = 10
        return messages_in(await self._call("get_messages", args))

    async def _search_page(self, query: str, count: int, page: int) -> list[dict]:
        args = {"query": query, "sort": "timestamp", "sort_dir": "desc", "count": count, "scope": "messages", "page": page}
        return messages_in(await self._call("search", args))

    async def _replies(self, channel: str, thread_ts: str) -> list[dict]:
        return messages_in(await self._call("get_thread", {"channel": channel, "threadTs": thread_ts, "limit": 200}))

    async def _post(self, channel: str, text: str, thread_ts: Optional[str]) -> tuple[str, str]:
        from core.mcp_stdio_client import (
            McpError, McpTimeout, McpToolError, McpToolRefused, McpUnavailable, parse_tool_result,
        )

        args: dict[str, Any] = {"channel": channel, "text": text, "asUser": True}
        if thread_ts:
            args["replyTo"] = thread_ts
            args["reply_broadcast"] = False
        try:  # start the server first: nothing is sent yet, so any failure is "not sent"
            client = await self._get_client()
            await client.start()
        except (SourceUnavailable, McpError) as exc:
            raise SendFailed(f"MCP server {self.server_id} did not start: {exc}") from exc
        with self._gate.allow_writes("post_message"):
            self._gate.check("post_message", args, write=True)
            try:
                result = await client.call_tool("post_message", args, timeout=_CALL_CAP_S)
            except McpTimeout:
                raise
            except (McpToolError, McpToolRefused, McpUnavailable) as exc:
                raise SendFailed(str(exc)) from exc
        data = parse_tool_result(result)
        ts, url = "", ""
        if isinstance(data, dict):
            message = data.get("message") if isinstance(data.get("message"), dict) else {}
            ts = str(_first(data, "ts", "messageTs", "message_ts") or _first(message, "ts") or "")
            url = str(_first(data, "permalink", "url") or "")
        return ts, url

    async def close(self) -> None:
        if self._client is not None:
            await self._client.close()


def _latest_of(item: dict) -> Optional[float]:
    latest = item.get("latest")
    if isinstance(latest, dict):
        latest = latest.get("ts")
    value = latest or _first(item, "last_activity", "lastActivity", "last_message_ts", "lastMessageTs", "latest_ts", "updated")
    if value in (None, ""):
        return None
    if isinstance(value, str) and not re.fullmatch(r"\d+(\.\d+)?", value):
        from core.inbox.sources.common import parse_time

        dt = parse_time(value)
        return dt.timestamp() if dt else None
    number = _ts(value)
    return number / 1000.0 if number > 1e11 else number  # ``updated`` is in milliseconds


# ---------------------------------------------------------------------------
# Backend: the Slack Web API with the person's user token
# ---------------------------------------------------------------------------

_AUTH_ERRORS = frozenset({
    "invalid_auth", "not_authed", "token_revoked", "token_expired", "account_inactive",
    "no_permission", "missing_scope", "not_allowed_token_type", "ekm_access_denied", "org_login_required",
})
_TOKEN_FIX = "在 Inbox 渠道设置里重新填 Slack user token（xoxp-…）后点重试"


class SlackApiError(RuntimeError):
    def __init__(self, method: str, code: str) -> None:
        super().__init__(f"{method}: {code}")
        self.code = code


class SlackWebSource(_SlackSource):
    """Slack Web API over httpx with a user token from the OS credential store."""

    API_BASE: ClassVar[str] = "https://slack.com/api/"
    READ_TOOLS: ClassVar[frozenset[str]] = frozenset({
        "auth.test", "conversations.list", "conversations.history", "conversations.replies",
        "search.messages", "chat.getPermalink",
    })
    WRITE_TOOLS: ClassVar[frozenset[str]] = frozenset({"chat.postMessage"})
    ALLOWED_ARGS: ClassVar[dict[str, frozenset[str]]] = {
        "auth.test": frozenset(),
        "conversations.list": frozenset({"types", "exclude_archived", "limit", "cursor"}),
        "conversations.history": frozenset({"channel", "oldest", "latest", "limit", "inclusive"}),
        "conversations.replies": frozenset({"channel", "ts", "limit"}),
        "search.messages": frozenset({"query", "sort", "sort_dir", "count", "page"}),
        "chat.getPermalink": frozenset({"channel", "message_ts"}),
        "chat.postMessage": frozenset({"channel", "text", "thread_ts"}),
    }

    def __init__(self, config: SourceConfig, *, transport: Any = None, token_reader: Any = None) -> None:
        super().__init__(config)
        self._transport = transport
        self._token_reader = token_reader
        self._http: Any = None
        self._channel_ids: Optional[dict[str, str]] = None

    async def _token(self) -> str:
        if self._token_reader is not None:
            token = self._token_reader()
        else:
            from core.inbox.sources.credentials import read_secret

            token = await asyncio.to_thread(read_secret, self.config.id, "user_token")
        if not token:
            raise SourceAuthError("no Slack user token stored for this source", fix=_TOKEN_FIX)
        if token.startswith("xoxb-"):
            raise SourceAuthError("a bot token cannot read your own messages; a user token is needed", fix=_TOKEN_FIX)
        return token

    async def _api(self, method: str, params: dict, *, write: bool = False) -> dict:
        import httpx

        self._gate.check(method, params, write=write)
        token = await self._token()
        if self._http is None:
            self._http = httpx.AsyncClient(base_url=self.API_BASE, transport=self._transport, timeout=httpx.Timeout(20.0))
        form = {k: (str(v).lower() if isinstance(v, bool) else str(v)) for k, v in params.items() if v is not None}
        timeout, by_budget = self._timeout() if not write else (_CALL_CAP_S, False)
        try:
            # wait_for bounds the whole request; httpx's own timeout bounds each read only
            response = await asyncio.wait_for(
                self._http.post(method, data=form, headers={"Authorization": f"Bearer {token}"}),
                timeout=timeout,
            )
        except asyncio.TimeoutError as exc:
            if by_budget:
                raise BudgetExhausted(f"{method} cut short by the pull budget") from exc
            raise
        except httpx.TransportError as exc:
            raise SourceUnavailable(f"{method}: {type(exc).__name__}") from exc
        if response.status_code in (401, 403):
            raise SourceAuthError(f"Slack refused the token ({response.status_code})", fix=_TOKEN_FIX)
        if response.status_code == 429:
            raise SourceUnavailable(f"{method}: rate limited by Slack")
        if response.status_code >= 400:
            raise SlackApiError(method, f"http_{response.status_code}")
        try:
            data = response.json()
        except ValueError as exc:
            raise SourceUnavailable(f"{method}: Slack answered with something that is not JSON") from exc
        if not data.get("ok"):
            code = str(data.get("error") or "unknown_error")
            if code in _AUTH_ERRORS:
                raise SourceAuthError(f"Slack: {code}", fix=_TOKEN_FIX)
            raise SlackApiError(method, code)
        return data

    async def _auth(self) -> tuple[str, str]:
        data = await self._api("auth.test", {})
        return str(data.get("user_id") or ""), str(data.get("user") or "")

    async def _paged_list(self, types: str, pages: int = 5) -> list[dict]:
        out: list[dict] = []
        cursor = None
        for _ in range(pages):
            data = await self._api(
                "conversations.list", {"types": types, "exclude_archived": True, "limit": 200, "cursor": cursor}
            )
            out.extend(c for c in data.get("channels") or () if isinstance(c, dict))
            cursor = (data.get("response_metadata") or {}).get("next_cursor")
            if not cursor:
                break
        return out

    async def _list_dms(self) -> list[tuple[str, Optional[float]]]:
        # conversations.list does not say when a DM last had a message (its ``updated`` is
        # a metadata change), so every DM's activity is "unknown": read in rotation.
        return [(str(c["id"]), None) for c in await self._paged_list("im,mpim") if c.get("id")]

    async def _history_page(
        self, channel: str, oldest: float, latest: Optional[str], limit: int, *, with_replies: bool = False
    ) -> list[dict]:
        params: dict[str, Any] = {"channel": channel, "oldest": f"{oldest:.6f}", "limit": limit}
        if latest:
            params["latest"] = latest
            params["inclusive"] = False
        return messages_in(await self._api("conversations.history", params))

    async def _search_page(self, query: str, count: int, page: int) -> list[dict]:
        data = await self._api(
            "search.messages", {"query": query, "sort": "timestamp", "sort_dir": "desc", "count": count, "page": page}
        )
        return messages_in(data)

    async def _replies(self, channel: str, thread_ts: str) -> list[dict]:
        return messages_in(await self._api("conversations.replies", {"channel": channel, "ts": thread_ts, "limit": 200}))

    async def _resolve_channel(self, name: str) -> Optional[str]:
        bare = name.lstrip("#")
        if _CHANNEL_ID.match(bare):
            return bare
        if self._channel_ids is None:
            self._channel_ids = {
                str(c.get("name")): str(c.get("id"))
                for c in await self._paged_list("public_channel,private_channel", pages=10)
                if c.get("name") and c.get("id")
            }
        return self._channel_ids.get(bare)

    async def _post(self, channel: str, text: str, thread_ts: Optional[str]) -> tuple[str, str]:
        params: dict[str, Any] = {"channel": channel, "text": text}
        if thread_ts:
            params["thread_ts"] = thread_ts
        with self._gate.allow_writes("chat.postMessage"):
            try:
                data = await self._api("chat.postMessage", params, write=True)
            except (SourceAuthError, SlackApiError) as exc:
                raise SendFailed(str(exc)) from exc  # Slack answered: nothing was posted
        ts = str(data.get("ts") or "")
        url = ""
        if ts:
            try:
                url = str((await self._api("chat.getPermalink", {"channel": channel, "message_ts": ts})).get("permalink") or "")
            except Exception as exc:  # noqa: BLE001 - the message is posted; a missing link is not a failure
                logger.info("slack source %s: posted, but no permalink: %s", self.config.id, type(exc).__name__)
                url = ""
        return ts, url

    async def close(self) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None


MCP_FAMILIES: dict[str, type[SlackMcpSource]] = {SlackMcpSource.FAMILY: SlackMcpSource}


def create(config: SourceConfig) -> _SlackSource:
    backend = config.backend or ""
    if backend.startswith("mcp:"):
        family = str((config.options or {}).get("tool_family") or backend.split(":", 1)[1])
        cls = MCP_FAMILIES.get(family)
        if cls is None:
            raise ValueError(
                f"no Slack tool mapping for MCP server family {family!r}; set options.tool_family to one of {sorted(MCP_FAMILIES)}"
            )
        return cls(config)
    if backend == "slack-web":
        return SlackWebSource(config)
    raise ValueError(f"unknown Slack backend {backend!r} (mcp:<server id> or slack-web)")


register_source(KIND, create)
