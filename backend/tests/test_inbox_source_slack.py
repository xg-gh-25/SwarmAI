"""The Slack Inbox source, both backends, against recorded-shape fakes.

slack-mcp answers are shaped like the Slack Web API objects its tools enrich (``ts``,
``user``, ``text``, ``thread_ts``, ``channel: {id, name}``, ``permalink``) and come back as
text wrapped in ``<untrusted_content>``; the Web API is faked with ``httpx.MockTransport``.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone

import httpx
import pytest

from core.inbox.models import ThreadRef
from core.inbox.sources.base import SendFailed, SourceAuthError, SourceConfig, create_source
from core.inbox.sources.slack import SlackMcpSource, SlackWebSource, create
from core.mcp_stdio_client import McpToolRefused
from tests.inbox_source_fakes import FakeMcp, mcp_error, mcp_timeout

ME = "U0ME"
NOW = time.time()
CURSOR = f"{NOW - 3600:.6f}"


def ts(offset_s: float) -> str:
    return f"{NOW - offset_s:.6f}"


def _config(**options):
    return SourceConfig(id="slack-1", kind="slack", backend="mcp:slack-mcp", options=options)


def _mcp_handlers(**overrides):
    dm_new = ts(600)
    handlers = {
        "search": lambda a: {"messages": {"matches": _search(a["query"])}},
        "list_dms": {"dms": [
            {"id": "D0BOB", "user": "U0BOB", "latest": {"ts": dm_new}},
            {"id": "D0OLD", "user": "U0OLD", "latest": {"ts": ts(86400)}},
        ]},
        "get_messages": lambda a: {"messages": _history(a["channel"])},
        "get_thread": lambda a: {"messages": _thread(a["channel"], a["threadTs"])},
        "post_message": {"ok": True, "ts": "1800000000.000100", "permalink": "https://x.slack.com/archives/C0TEAM/p1"},
    }
    handlers.update(overrides)
    return handlers


def _search(query):
    if query == "from:me":
        return [{"ts": ts(7200), "user": ME, "username": "me.person", "text": "earlier", "channel": {"id": "C0TEAM"}}]
    if query == f"<@{ME}>":
        return [
            {"ts": ts(300), "user": "U0ANA", "username": "ana", "text": f"<@{ME}> can you look?",
             "channel": {"id": "C0TEAM", "name": "team"}, "permalink": "https://x.slack.com/archives/C0TEAM/p300"},
            # the same message the DM leg also returns would be deduplicated; this one is too old
            {"ts": ts(7200 + 700), "user": "U0ANA", "text": f"<@{ME}> old", "channel": {"id": "C0TEAM", "name": "team"}},
        ]
    if query.startswith(f"from:<@{ME}> after:"):
        return [{"ts": ts(5000), "user": ME, "text": "my thread starter", "channel": {"id": "C0TEAM", "name": "team"},
                 "permalink": "https://x.slack.com/archives/C0TEAM/p5000?thread_ts=" + ts(5000)}]
    return []


def _history(channel):
    if channel == "D0BOB":
        return [{"ts": ts(600), "user": "U0BOB", "user_name": "Bob", "text": "ping"},
                {"ts": ts(600), "user": "U0BOB", "user_name": "Bob", "text": "ping"},  # repeated by the server
                {"ts": ts(7300), "user": "U0BOB", "text": "before the cursor"}]
    if channel in ("watched-ops", "C0OPS"):
        return [{"ts": ts(200), "user": "U0OPS", "text": "deploy at 5", "channel": {"id": "C0OPS", "name": "ops"}}]
    return []


def _thread(channel, thread_ts):
    return [
        {"ts": thread_ts, "user": ME, "text": "my thread starter", "channel": {"id": channel, "name": "team"}},
        {"ts": ts(100), "user": "U0CAT", "user_name": "Cat", "text": "a reply to you", "thread_ts": thread_ts,
         "channel": {"id": channel, "name": "team"}},
        {"ts": ts(50), "user": ME, "text": "Sounds *good*, see <https://ex.com|the doc> &amp; thanks", "thread_ts": thread_ts,
         "channel": {"id": channel, "name": "team"}},
    ]


@pytest.fixture
def mcp():
    return FakeMcp(_mcp_handlers())


async def test_whoami_comes_from_the_backend(mcp):
    src = SlackMcpSource(_config(), client=mcp)
    me = await src.whoami()
    assert me.ids == frozenset({ME, "me.person"})
    assert mcp.calls[0] == ("search", {"query": "from:me", "sort": "timestamp", "sort_dir": "desc", "count": 1, "scope": "messages"})


async def test_whoami_without_any_own_message_does_not_guess():
    src = SlackMcpSource(_config(), client=FakeMcp(_mcp_handlers(search={"messages": {"matches": []}})))
    with pytest.raises(Exception, match="cannot tell who you are"):
        await src.whoami()


async def test_pull_collects_dms_mentions_threads_and_watched(mcp):
    src = SlackMcpSource(_config(watched_channels=["watched-ops"]), client=mcp)
    result = await src.pull(CURSOR)
    by_text = {m.text: m for m in result.messages}
    assert set(by_text) == {"ping", f"<@{ME}> can you look?", "a reply to you", "deploy at 5",
                            "Sounds *good*, see <https://ex.com|the doc> &amp; thanks"}
    assert by_text["ping"].is_direct and by_text["ping"].thread.data == {"channel": "D0BOB", "thread_ts": None}
    assert by_text[f"<@{ME}> can you look?"].mentions_me
    assert by_text["deploy at 5"].watched and not by_text["deploy at 5"].is_direct
    reply = by_text["a reply to you"]
    assert reply.thread.data == {"channel": "C0TEAM", "thread_ts": ts(5000)}
    # the old DM was skipped without reading it
    assert ("get_messages", {"channel": "D0OLD"}) not in [(t, {"channel": a.get("channel")}) for t, a in mcp.calls]
    cursor = json.loads(result.cursor)
    assert cursor["ts"] == max(m.extra["ts"] for m in result.messages)
    assert cursor["dm"]["D0BOB"][0] == ts(600)  # the DM's own position
    assert cursor["dm"]["D0OLD"][0] == CURSOR  # not read: it keeps the position it had


async def test_pull_dedups_by_external_id(mcp):
    src = SlackMcpSource(_config(), client=mcp)
    result = await src.pull(CURSOR)
    ids = [m.external_id for m in result.messages]
    assert len(ids) == len(set(ids))
    assert ids.count(f"D0BOB:{ts(600)}") == 1


async def test_reply_goes_to_given_thread_only(mcp):
    src = SlackMcpSource(_config(), client=mcp)
    receipt = await src.reply(ThreadRef("slack", {"channel": "C0TEAM", "thread_ts": "1700000000.000001"}), "on it")
    posts = [a for t, a in mcp.calls if t == "post_message"]
    assert posts == [{"channel": "C0TEAM", "text": "on it", "asUser": True, "replyTo": "1700000000.000001", "reply_broadcast": False}]
    assert receipt.external_msg_id == "C0TEAM:1800000000.000100"


async def test_reply_in_a_dm_without_thread_posts_into_the_dm(mcp):
    src = SlackMcpSource(_config(), client=mcp)
    await src.reply(ThreadRef("slack", {"channel": "D0BOB", "thread_ts": None}), "hi")
    assert [a for t, a in mcp.calls if t == "post_message"] == [{"channel": "D0BOB", "text": "hi", "asUser": True}]


async def test_reply_failure_and_timeout(mcp):
    failing = FakeMcp(_mcp_handlers(post_message=lambda a: mcp_error("channel_not_found")))
    with pytest.raises(SendFailed):
        await SlackMcpSource(_config(), client=failing).reply(ThreadRef("slack", {"channel": "C1", "thread_ts": None}), "x")
    hanging = FakeMcp(_mcp_handlers(post_message=lambda a: mcp_timeout()))
    with pytest.raises(Exception) as info:
        await SlackMcpSource(_config(), client=hanging).reply(ThreadRef("slack", {"channel": "C1", "thread_ts": None}), "x")
    assert not isinstance(info.value, SendFailed)  # unknown outcome, never "surely not sent"


async def test_find_sent_matches_after_removing_formatting(mcp):
    src = SlackMcpSource(_config(), client=mcp)
    ref = ThreadRef("slack", {"channel": "C0TEAM", "thread_ts": ts(5000)})
    after = datetime.fromtimestamp(NOW - 3600, tz=timezone.utc)
    found = await src.find_sent(ref, after, "Sounds good, see the doc & thanks")
    assert found is not None and found.external_msg_id == f"C0TEAM:{ts(50)}"
    assert await src.find_sent(ref, after, "Sounds bad") is None
    later = datetime.fromtimestamp(NOW - 10, tz=timezone.utc)
    assert await src.find_sent(ref, later, "Sounds good, see the doc & thanks") is None


async def test_expired_midway_login_raises_source_auth_error():
    expired = FakeMcp(_mcp_handlers(search=lambda a: mcp_error("Midway session expired; run mwinit")))
    with pytest.raises(SourceAuthError) as info:
        await SlackMcpSource(_config(), client=expired).pull(None)
    assert info.value.fix == "Run mwinit -f, then click Retry"


async def test_thread_target_names_the_thread(mcp):
    src = SlackMcpSource(_config(), client=mcp)
    thread = await src.thread(ThreadRef("slack", {"channel": "C0TEAM", "thread_ts": ts(5000)}))
    assert thread.target.label == "#team thread"
    assert thread.target.recipients == (f"C0TEAM:{ts(5000)}",)
    assert [m.author_id for m in thread.messages] == [ME, "U0CAT", ME]


def test_factory_picks_backend_and_family():
    assert isinstance(create(_config()), SlackMcpSource)
    assert isinstance(create(SourceConfig(id="s", kind="slack", backend="mcp:my-slack", options={"tool_family": "slack-mcp"})), SlackMcpSource)
    assert isinstance(create(SourceConfig(id="s", kind="slack", backend="slack-web")), SlackWebSource)
    with pytest.raises(ValueError, match="tool mapping"):
        create(SourceConfig(id="s", kind="slack", backend="mcp:other-slack"))
    import core.inbox.sources.builtin  # noqa: F401

    assert isinstance(create_source(_config()), SlackMcpSource)


# ---------------------------------------------------------------------------
# slack-web
# ---------------------------------------------------------------------------


class SlackApi:
    def __init__(self, *, auth_ok=True):
        self.calls: list[tuple[str, dict, str]] = []
        self.auth_ok = auth_ok

    def __call__(self, request: httpx.Request) -> httpx.Response:
        method = request.url.path.rsplit("/", 1)[-1]
        form = dict(httpx.QueryParams(request.content.decode()))
        self.calls.append((method, form, request.headers.get("authorization", "")))
        if not self.auth_ok:
            return httpx.Response(200, json={"ok": False, "error": "invalid_auth"})
        data = {
            "auth.test": {"ok": True, "user_id": ME, "user": "me.person"},
            # ``updated`` is a metadata time (long ago here): it must not stop the DM from being read
            "conversations.list": {"ok": True, "channels": [{"id": "D0BOB", "is_im": True, "updated": 1600000000000}],
                                   "response_metadata": {"next_cursor": ""}},
            "conversations.history": {"ok": True, "messages": [{"ts": ts(600), "user": "U0BOB", "text": "ping"}]},
            "search.messages": {"ok": True, "messages": {"matches": []}},
            "conversations.replies": {"ok": True, "messages": []},
            "chat.postMessage": {"ok": True, "channel": form.get("channel"), "ts": "1800000000.000200"},
            "chat.getPermalink": {"ok": True, "permalink": "https://x.slack.com/archives/C/p2"},
        }.get(method, {"ok": False, "error": "unknown_method"})
        return httpx.Response(200, json=data)


def _web(api, token="xoxp-test-token"):  # pragma: allowlist secret
    cfg = SourceConfig(id="slack-web-1", kind="slack", backend="slack-web", options={"token": "xoxp-IGNORED"})
    return SlackWebSource(cfg, transport=httpx.MockTransport(api), token_reader=lambda: token)


async def test_web_whoami_reply_and_token_from_the_credential_store():
    api = SlackApi()
    src = _web(api)
    try:
        assert (await src.whoami()).ids == frozenset({ME, "me.person"})
        result = await src.pull(CURSOR)
        assert [m.text for m in result.messages] == ["ping"]
        receipt = await src.reply(ThreadRef("slack", {"channel": "C0TEAM", "thread_ts": "1.5"}), "ok")
        assert receipt.url == "https://x.slack.com/archives/C/p2"
    finally:
        await src.close()
    posts = [form for m, form, _ in api.calls if m == "chat.postMessage"]
    assert posts == [{"channel": "C0TEAM", "text": "ok", "thread_ts": "1.5"}]
    # the token used is the stored one, never options["token"]
    assert {auth for _, _, auth in api.calls} == {"Bearer xoxp-test-token"}  # pragma: allowlist secret
    assert {m for m, _, _ in api.calls} - {"chat.postMessage"} <= SlackWebSource.READ_TOOLS


async def test_web_expired_token_and_missing_token():
    src = _web(SlackApi(auth_ok=False))
    with pytest.raises(SourceAuthError) as info:
        await src.whoami()
    assert "token" in info.value.fix
    await src.close()
    with pytest.raises(SourceAuthError):
        await _web(SlackApi(), token=None).whoami()
    with pytest.raises(SourceAuthError, match="bot token"):
        await _web(SlackApi(), token="xoxb-bot").whoami()  # pragma: allowlist secret


async def test_web_reply_refused_by_slack_is_send_failed():
    def api(request: httpx.Request) -> httpx.Response:
        method = request.url.path.rsplit("/", 1)[-1]
        if method == "chat.postMessage":
            return httpx.Response(200, json={"ok": False, "error": "not_in_channel"})
        return httpx.Response(200, json={"ok": True, "user_id": ME, "user": "me"})

    src = SlackWebSource(SourceConfig(id="w", kind="slack", backend="slack-web"),
                         transport=httpx.MockTransport(api), token_reader=lambda: "xoxp-1")  # pragma: allowlist secret
    with pytest.raises(SendFailed, match="not_in_channel"):
        await src.reply(ThreadRef("slack", {"channel": "C1", "thread_ts": None}), "x")
    await src.close()


def test_recorded_shapes_round_trip():
    # the fake wraps every answer like the real server does; parsing must see through it
    from core.mcp_stdio_client import parse_tool_result

    wrapped = {"content": [{"type": "text", "text": '<untrusted_content>{"messages": [1]}</untrusted_content>'}]}
    assert parse_tool_result(wrapped) == {"messages": [1]}
    assert json.loads(json.dumps(_mcp_handlers()["list_dms"]))["dms"][0]["id"] == "D0BOB"


# ---------------------------------------------------------------------------
# review fixes: budget, paging, DM rotation, refused tools, unreadable answers
# ---------------------------------------------------------------------------


async def test_a_call_cut_short_by_the_budget_keeps_what_was_collected():
    timed_out = FakeMcp(_mcp_handlers(get_thread=lambda a: mcp_timeout()))
    src = SlackMcpSource(_config(pull_budget_s=5), client=timed_out)  # 5s < the 30s call cap
    result = await src.pull(CURSOR)
    texts = {m.text for m in result.messages}
    assert "ping" in texts and f"<@{ME}> can you look?" in texts
    cursor = json.loads(result.cursor)
    assert cursor["ts"] == CURSOR  # the overall position stays where it was
    assert cursor["dm"]["D0BOB"][0] == ts(600)  # the DM that was read keeps its progress


async def test_a_timeout_not_set_by_the_budget_still_fails_the_pull():
    timed_out = FakeMcp(_mcp_handlers(get_thread=lambda a: mcp_timeout()))
    with pytest.raises(Exception) as info:
        await SlackMcpSource(_config(pull_budget_s=600), client=timed_out).pull(CURSOR)
    assert type(info.value).__name__ == "McpTimeout"


async def test_history_is_read_page_by_page():
    stamps = [f"{NOW - 10 * i:.6f}" for i in range(1, 251)]  # 250 messages, newest first

    def page(args):
        until = args.get("until")
        limit = args["limit"]
        newer_than_until = [t for t in stamps if until is None or float(t) < datetime.fromisoformat(until.replace("Z", "+00:00")).timestamp()]
        return {"messages": [{"ts": t, "user": "U0OPS", "text": t} for t in newer_than_until[:limit]]}

    mcp = FakeMcp(_mcp_handlers(get_messages=page, list_dms={"dms": []}))
    result = await SlackMcpSource(_config(watched_channels=["C0OPS"]), client=mcp).pull(f"{NOW - 3000:.6f}")
    assert {m.extra["ts"] for m in result.messages if m.watched} == set(stamps)


async def test_dms_with_unknown_activity_rotate_and_keep_their_own_position():
    reads = []

    def history(args):
        reads.append(args["channel"])
        return {"messages": [{"ts": ts(4000), "user": "U0X", "text": f"in {args['channel']}"}]}

    dms = {"dms": [{"id": "D0A"}, {"id": "D0B"}, {"id": "D0C"}]}  # no activity time
    mcp = FakeMcp(_mcp_handlers(list_dms=dms, get_messages=history))
    src = SlackMcpSource(_config(max_dms=2), client=mcp)
    first = await src.pull(f"{NOW - 5000:.6f}")
    assert reads == ["D0A", "D0B"]
    reads.clear()
    second = await src.pull(first.cursor)
    assert reads[0] == "D0C"  # the one not checked yet goes first
    # its message is older than the first pull's overall position, but newer than its own
    assert "in D0C" in {m.text for m in second.messages}


async def test_refused_post_tool_is_send_failed():
    refused = FakeMcp(_mcp_handlers(post_message=lambda a: McpToolRefused("turned off")))
    with pytest.raises(SendFailed):
        await SlackMcpSource(_config(), client=refused).reply(ThreadRef("slack", {"channel": "C1", "thread_ts": None}), "x")


async def test_an_answer_that_is_not_json_fails_instead_of_reading_as_empty():
    odd = FakeMcp(_mcp_handlers(search=lambda a: "Found 3 messages: ..." if a["query"] != "from:me" else {"messages": [{"ts": ts(9), "user": ME}]}))
    with pytest.raises(Exception, match="not in a shape"):
        await SlackMcpSource(_config(), client=odd).pull(CURSOR)


async def test_web_dm_activity_is_never_taken_from_updated():
    api = SlackApi()
    src = _web(api)
    try:
        dms = await src._list_dms()
    finally:
        await src.close()
    assert dms == [("D0BOB", None)]


async def test_web_call_cut_short_by_the_budget_keeps_the_position():
    import asyncio as aio

    async def api(request: httpx.Request) -> httpx.Response:
        method = request.url.path.rsplit("/", 1)[-1]
        if method == "conversations.history":
            await aio.sleep(5)
        return SlackApi()(request)

    cfg = SourceConfig(id="w", kind="slack", backend="slack-web", options={"pull_budget_s": 0.5})
    src = SlackWebSource(cfg, transport=httpx.MockTransport(api), token_reader=lambda: "xoxp-1")  # pragma: allowlist secret
    try:
        result = await src.pull(CURSOR)
    finally:
        await src.close()
    assert json.loads(result.cursor)["ts"] == CURSOR


async def test_search_overflow_is_logged(caplog):
    full = [{"ts": ts(10 + i), "user": "U0ANA", "text": "x", "channel": {"id": "C0TEAM"}} for i in range(100)]
    mcp = FakeMcp(_mcp_handlers(search=lambda a: {"messages": {"matches": full if a["query"] != "from:me" else full[:1]}}))
    src = SlackMcpSource(_config(max_pages=2), client=mcp)
    with caplog.at_level("WARNING"):
        await src._search("<@U0ME>", NOW - 3600)
    assert "more than 200 results" in caplog.text  # "may have more than ..."


def test_history_rows_with_an_iso_timestamp_keep_the_permalink_ts():
    """slack-mcp's get_messages gives `timestamp` as an ISO time and no numeric `ts`; the
    message's real Slack ts (which a thread reply must name) is in its permalink."""
    from core.inbox.sources.slack import _slack_ts, _ts

    row = {"timestamp": "2026-11-20T01:02:03.456Z", "permalink": "https://x.slack.com/archives/D1/p1763600523456789",
           "user": {"id": "U2", "name": "ana"}, "text": "hi"}
    assert _slack_ts(row) == "1763600523.456789"
    assert _slack_ts({"ts": "1763600523.000100", "permalink": "https://x/p1111111111222222"}) == "1763600523.000100"
    no_link = _slack_ts({"timestamp": "2026-11-20T01:02:03Z"})
    assert no_link is not None and abs(float(no_link) - _ts("2026-11-20T01:02:03Z")) < 1e-6
    assert _ts("2026-11-20T01:02:03Z") > 1.7e9 and _ts("nonsense") == 0.0


def _wrapped(author: str, ts_: str, body: str) -> str:
    """Text the way slack-mcp returns it: one tagged block, spaces marked U+E000."""
    return f'<slack-user-content author="{author}" ts="{ts_}">\n{body.replace(" ", chr(0xE000))}\n</slack-user-content>'


async def test_marked_text_from_slack_mcp_reads_as_written_and_the_sent_reply_is_found():
    # Live get_thread shape: user as an object, ISO timestamp, the ts only in the permalink.
    root_ts, reply_ts = ts(60), ts(30)

    def iso(t):
        return datetime.fromtimestamp(float(t), tz=timezone.utc).isoformat()

    def link(t):
        return "https://x.slack.com/archives/D0SELF/p" + t.replace(".", "")

    thread = {"threadTs": root_ts, "messages": [
        {"text": _wrapped(ME, root_ts, "[Inbox test] root"), "user": {"id": ME, "name": "me"},
         "timestamp": iso(root_ts), "threadTs": root_ts, "permalink": link(root_ts), "isThreadParent": True},
        {"text": _wrapped(ME, reply_ts, "[Inbox test] thread reply, ignore"), "user": {"id": ME, "name": "me"},
         "timestamp": iso(reply_ts), "threadTs": root_ts, "permalink": link(reply_ts)},
    ]}
    src = SlackMcpSource(_config(), client=FakeMcp(_mcp_handlers(get_thread=thread)))
    ref = ThreadRef("slack", {"channel": "D0SELF", "thread_ts": root_ts})
    after = datetime.fromtimestamp(NOW - 45, tz=timezone.utc)
    found = await src.find_sent(ref, after, "[Inbox test] thread reply, ignore")
    assert found is not None and found.external_msg_id == f"D0SELF:{reply_ts}"
    msgs = await src._thread_messages(ref, 0)
    assert [m.text for m in msgs] == ["[Inbox test] root", "[Inbox test] thread reply, ignore"]


def test_text_that_is_not_one_marked_block_is_kept_as_is():
    from core.inbox.sources.slack import _plain_text

    assert _plain_text("plain <b>text</b>") == "plain <b>text</b>"
    two = _wrapped(ME, "1", "a b") + "\n" + _wrapped(ME, "2", "c")
    assert _plain_text(two) == two

# ---------------------------------------------------------------------------
# The person's own messages to one counterparty (drafting context)
# ---------------------------------------------------------------------------


def _thread_in(channel, thread_ts=None):
    from core.inbox.models import Thread
    return Thread(ref=ThreadRef("slack", {"channel": channel, "thread_ts": thread_ts}), messages=(), target=None)


async def test_own_messages_to_are_the_persons_messages_in_that_dm_only():
    def history(a):
        if a["channel"] == "D0BOB":
            return {"messages": [{"ts": ts(900), "user": "U0BOB", "text": "can you send the doc?"},
                                 {"ts": ts(800), "user": ME, "text": "sure, here it is"},
                                 {"ts": ts(700), "user": ME, "text": "anything else?"}]}
        return {"messages": [{"ts": ts(600), "user": ME, "text": "a message in a channel"}]}

    mcp = FakeMcp(_mcp_handlers(get_messages=history))
    src = SlackMcpSource(_config(), client=mcp)
    own = await src.own_messages_to(_thread_in("D0BOB", ts(900)), 10)
    assert [m.text for m in own] == ["sure, here it is", "anything else?"]
    assert all(m.author_id == ME for m in own)
    assert [a["channel"] for t, a in mcp.calls if t == "get_messages"] == ["D0BOB"]
    assert await src.own_messages_to(_thread_in("D0BOB"), 1) == own[-1:]
    # a reply in a channel thread or a group DM reaches others: no history from elsewhere
    assert await src.own_messages_to(_thread_in("C0TEAM", ts(900)), 10) == []
    assert await src.own_messages_to(_thread_in("G0GROUP"), 10) == []


async def test_web_own_messages_to_and_a_revoked_token_on_post():
    def api(request: httpx.Request) -> httpx.Response:
        method = request.url.path.rsplit("/", 1)[-1]
        data = {
            "auth.test": {"ok": True, "user_id": ME, "user": "me.person"},
            "conversations.list": {"ok": True, "channels": [{"id": "D0ANA", "is_im": True, "user": "U0ANA"}],
                                   "response_metadata": {"next_cursor": ""}},
            "conversations.history": {"ok": True, "messages": [{"ts": ts(500), "user": "U0ANA", "text": "hi"},
                                                               {"ts": ts(400), "user": ME, "text": "hello Ana"}]},
            "chat.postMessage": {"ok": False, "error": "token_revoked"},
        }.get(method, {"ok": False, "error": "unknown_method"})
        return httpx.Response(200, json=data)

    src = SlackWebSource(SourceConfig(id="w", kind="slack", backend="slack-web"),
                         transport=httpx.MockTransport(api), token_reader=lambda: "xoxp-1")  # pragma: allowlist secret
    try:
        assert [m.text for m in await src.own_messages_to(_thread_in("D0ANA"), 10)] == ["hello Ana"]
        with pytest.raises(SendFailed) as info:
            await src.reply(ThreadRef("slack", {"channel": "D0ANA", "thread_ts": None}), "x")
        assert "token" in info.value.fix, "a refused token keeps the fix, so the source is marked signed out"
    finally:
        await src.close()
    assert SlackWebSource.SECRET_NAMES == frozenset({"user_token"})


def test_an_app_users_name_comes_from_slack_mcps_realname():
    src = SlackMcpSource(_config(), client=FakeMcp(_mcp_handlers()))
    m = src._to_message({"ts": ts(10), "text": "Signal Digest", "user": {
        "id": "U0APP", "name": "", "realName": "Swarm", "isBot": True}}, "D0APP", is_dm=True)
    assert m.author_name == "Swarm" and m.author_id == "U0APP" and m.is_bot


async def test_a_dm_of_only_my_messages_names_its_member():
    """The person's own DM reads "DM · yourself"; a DM where only the person wrote names the
    member the backend lists, never the channel id."""
    dms = {"dms": [{"channelId": "D0SELF", "userId": ME, "name": "me.person"},
                   {"channelId": "D0DAN", "userId": "U0DAN", "name": "Dan"}]}
    mine = lambda a: {"messages": [{"ts": ts(60), "user": ME, "text": "note to self"}]}  # noqa: E731
    src = SlackMcpSource(_config(), client=FakeMcp(_mcp_handlers(list_dms=dms, get_messages=mine)))
    assert (await src.thread(ThreadRef("slack", {"channel": "D0SELF"}))).target.label == "DM · yourself"
    assert (await src.thread(ThreadRef("slack", {"channel": "D0DAN"}))).target.label == "DM · Dan"
    unknown = await src.thread(ThreadRef("slack", {"channel": "D0GONE"}))
    assert unknown.target.label == "DM · D0GONE"


async def test_web_dm_of_only_my_messages_names_its_member():
    def api(request: httpx.Request) -> httpx.Response:
        method = request.url.path.rsplit("/", 1)[-1]
        data = {
            "auth.test": {"ok": True, "user_id": ME, "user": "me.person"},
            "conversations.list": {"ok": True, "channels": [{"id": "D0SELF", "is_im": True, "user": ME}],
                                   "response_metadata": {"next_cursor": ""}},
            "conversations.history": {"ok": True, "messages": [{"ts": ts(60), "user": ME, "text": "note"}]},
        }.get(method, {"ok": False, "error": "unknown_method"})
        return httpx.Response(200, json=data)

    src = SlackWebSource(SourceConfig(id="w", kind="slack", backend="slack-web"),
                         transport=httpx.MockTransport(api), token_reader=lambda: "xoxp-1")  # pragma: allowlist secret
    try:
        assert (await src.thread(ThreadRef("slack", {"channel": "D0SELF"}))).target.label == "DM · yourself"
    finally:
        await src.close()
