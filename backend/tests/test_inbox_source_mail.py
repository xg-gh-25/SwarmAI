"""The mail Inbox source: the aws-outlook-mcp mapping and IMAP/SMTP, against recorded-shape fakes.

Outlook answers are shaped like the EWS/Graph objects the server returns (``itemId`` /
``changeKey`` / ``internetMessageId``, ``from: {name, address}``, recipient lists); a
whole conversation comes back from ``email_read`` and must be split per message.
"""
from __future__ import annotations

import smtplib
from datetime import datetime, timezone

import pytest

from core.inbox.models import ReplyTarget, Thread, ThreadRef
from core.inbox.sources.base import SendFailed, SourceAuthError, SourceConfig
from core.inbox.sources.common import ReadToolRefused
from core.inbox.sources.mail import ImapSmtpSource, OutlookMcpSource, create, split_conversations
from tests.inbox_source_fakes import FakeImap, FakeMcp, FakeSmtp, mcp_error, rfc822

ME = "me@corp.example"


def _addr(name, address):
    return {"name": name, "address": address}


def _conversation():
    return {
        "conversationId": "CONV-1",
        "messages": [
            {"itemId": "IT-1", "changeKey": "CK-1", "internetMessageId": "<m1@corp.example>", "subject": "Budget",
             "from": _addr("Priya", "priya@corp.example"), "toRecipients": [_addr("Me", ME)],
             "receivedDateTime": "2026-11-20T01:00:00Z", "body": "Can you approve?"},
            {"itemId": "IT-2", "changeKey": "CK-2", "internetMessageId": "<m2@corp.example>", "subject": "RE: Budget",
             "from": _addr("Me", ME), "toRecipients": [_addr("Priya", "priya@corp.example")],
             "receivedDateTime": "2026-11-20T01:30:00Z", "body": "Looking.\n\n-- \nMe"},
            {"itemId": "IT-3", "changeKey": "CK-3", "internetMessageId": "<m3@corp.example>", "subject": "RE: Budget",
             "from": _addr("Priya", "priya@corp.example"), "toRecipients": [_addr("Team", "team@corp.example")],
             "ccRecipients": [_addr("Me", ME)], "replyTo": [_addr("Billing", "billing@other.example")],
             "receivedDateTime": "2026-11-20T02:00:00Z",
             "body": "Thanks!\n\nOn Thu, Nov 20, 2026 Me wrote:\n> Looking."},
        ],
    }


def _outlook(**overrides):
    handlers = {
        "email_folders": lambda a: (
            {"emails": [{"conversationId": "CONV-0", "messages": [
                {"itemId": "S1", "from": _addr("Me", ME), "receivedDateTime": "2026-11-19T10:00:00Z"}]}]}
            if a.get("folder") == "sentitems"
            else {"emails": [{"conversationId": "CONV-1", "subject": "Budget"}, {"conversationId": "CONV-1"}]}
        ),
        "email_read": lambda a: _conversation(),
        "email_search": {"results": []},
        "email_reply": {"success": True, "itemId": "NEW-1"},
    }
    handlers.update(overrides)
    return FakeMcp(handlers, env={"OUTLOOK_MCP_ENABLE_WRITES": "true"})


def _cfg(**options):
    return SourceConfig(id="mail-1", kind="mail", backend="mcp:aws-outlook-mcp", options=options)


def test_conversation_is_split_into_one_record_per_message():
    records = split_conversations({"conversations": [_conversation()]})
    assert [r.message_id for r in records] == ["<m1@corp.example>", "<m2@corp.example>", "<m3@corp.example>"]
    assert {r.conversation for r in records} == {"CONV-1"}
    assert records[2].reply_to == ["billing@other.example"] and records[2].change_key == "CK-3"


async def test_whoami_from_the_sent_mail_plus_aliases():
    mcp = _outlook()
    me = await OutlookMcpSource(_cfg(extra_addresses=["Me.Alias@corp.example"]), client=mcp).whoami()
    assert me.ids == frozenset({ME, "me.alias@corp.example"})
    assert mcp.calls[0] == ("email_folders", {"folder": "sentitems", "limit": 3})


async def test_pull_splits_dedups_and_sets_the_rule_facts():
    mcp = _outlook()
    result = await OutlookMcpSource(_cfg(), client=mcp).pull("2026-11-20T00:00:00+00:00")
    assert [m.external_id for m in result.messages] == ["<m1@corp.example>", "<m2@corp.example>", "<m3@corp.example>"]
    first, mine, third = result.messages
    assert first.is_direct and not first.is_bulk
    assert third.is_bulk and not third.is_direct  # only in Cc
    assert third.text == "Thanks!"  # quoted history removed
    assert mine.author_id == ME
    assert first.thread.data == {"conversation_id": "CONV-1", "message_id": "<m1@corp.example>"}
    assert result.cursor == "2026-11-20T02:00:00+00:00"
    # the listing named CONV-1 twice; it was read once, never with markAs
    reads = [a for t, a in mcp.calls if t == "email_read"]
    assert reads == [{"conversationId": "CONV-1", "format": "text"}]  # one conversation per read
    listing = [a for t, a in mcp.calls if t == "email_folders" and a.get("folder") == "inbox"]
    assert listing == [{"folder": "inbox", "startDate": "2026-11-20", "limit": 50, "offset": 0}]


async def test_pull_drops_messages_not_newer_than_the_cursor():
    result = await OutlookMcpSource(_cfg(), client=_outlook()).pull("2026-11-20T01:30:00+00:00")
    assert [m.external_id for m in result.messages] == ["<m3@corp.example>"]


async def test_reply_to_and_external_domain_flags():
    src = OutlookMcpSource(_cfg(), client=_outlook())
    thread = await src.thread(ThreadRef("mail", {"conversation_id": "CONV-1", "message_id": "<m3@corp.example>"}))
    assert thread.target.recipients == ("billing@other.example",)
    assert set(thread.target.flags) == {"reply_to_differs", "external_domain"}
    plain = await src.thread(ThreadRef("mail", {"conversation_id": "CONV-1", "message_id": "<m1@corp.example>"}))
    assert plain.target.recipients == ("priya@corp.example",) and plain.target.flags == ()
    assert plain.target.label == "Priya <priya@corp.example>"


async def test_mcp_reply_answers_only_the_given_message():
    mcp = _outlook()
    await OutlookMcpSource(_cfg(), client=mcp).reply(
        ThreadRef("mail", {"conversation_id": "CONV-1", "message_id": "<m1@corp.example>"}), "Approved."
    )
    replies = [a for t, a in mcp.calls if t == "email_reply"]
    assert replies == [{"itemId": "IT-1", "itemChangeKey": "CK-1", "body": "Approved.", "replyAll": False}]


async def test_mcp_reply_refused_extra_recipients_never_reach_the_server():
    src = OutlookMcpSource(_cfg(), client=_outlook())
    with pytest.raises(ReadToolRefused):
        with src._gate.allow_writes("email_reply"):
            src._gate.check("email_reply", {"itemId": "x", "cc": ["a@b.c"]}, write=True)
    with pytest.raises(ReadToolRefused):
        src._gate.check("email_read", {"conversationId": "c", "markAs": "read"})
    with pytest.raises(ReadToolRefused):
        src._gate.check("email_folders", {"folder": "inbox", "moveTo": "archive"})  # anything not listed


async def test_can_send_needs_the_reply_tool_and_writes_enabled():
    assert await OutlookMcpSource(_cfg(), client=_outlook()).can_send()
    off = _outlook()
    off.env = {}
    src = OutlookMcpSource(_cfg(), client=off)
    assert not await src.can_send() and "OUTLOOK_MCP_ENABLE_WRITES" in src.read_only_reason
    no_tool = _outlook()
    no_tool.tools = ["email_read", "email_folders", "email_search"]
    assert not await OutlookMcpSource(_cfg(), client=no_tool).can_send()


async def test_find_sent_matches_without_quotes_and_signature():
    src = OutlookMcpSource(_cfg(), client=_outlook())
    ref = ThreadRef("mail", {"conversation_id": "CONV-1", "message_id": "<m1@corp.example>"})
    after = datetime(2026, 11, 20, 1, 0, tzinfo=timezone.utc)
    found = await src.find_sent(ref, after, "Looking.")
    assert found is not None and found.external_msg_id == "<m2@corp.example>"
    assert await src.find_sent(ref, after, "Something else") is None
    assert await src.find_sent(ref, datetime(2026, 11, 20, 1, 45, tzinfo=timezone.utc), "Looking.") is None


async def test_expired_midway_login_raises_source_auth_error():
    expired = _outlook(email_folders=lambda a: mcp_error("401 Unauthorized: Midway cookie expired"))
    with pytest.raises(SourceAuthError) as info:
        await OutlookMcpSource(_cfg(), client=expired).pull(None)
    assert info.value.fix == "Run mwinit -f, then click Retry"


def test_factory():
    assert isinstance(create(_cfg()), OutlookMcpSource)
    assert isinstance(create(SourceConfig(id="m", kind="mail", backend="imap", options={"host": "h", "username": "u@x.y"})), ImapSmtpSource)
    with pytest.raises(ValueError):
        create(SourceConfig(id="m", kind="mail", backend="mcp:some-mail"))


# ---------------------------------------------------------------------------
# IMAP + SMTP
# ---------------------------------------------------------------------------


def _inbox():
    return {
        "INBOX": {
            11: rfc822(message_id="<a1@x.example>", from_="Lee <lee@x.example>", to=ME, subject="Plan",
                       body="Draft attached", date="Thu, 20 Nov 2026 01:00:00 +0000"),
            12: rfc822(message_id="<a2@corp.example>", from_="News <news@corp.example>", to="all@corp.example", cc=ME,
                       subject="Weekly", extra_headers="List-Id: <weekly.corp.example>"),
            13: rfc822(message_id="<a3@corp.example>", from_="Cal <cal@corp.example>", to=ME, subject="Invite", calendar=True),
            14: rfc822(message_id="<a1@x.example>", from_="Lee <lee@x.example>", to=ME, subject="Plan", body="dup"),
        },
        "Sent": {
            5: rfc822(message_id="<r1@corp.example>", from_=ME, to="lee@x.example", subject="Re: Plan",
                      body="Thanks, will read.\n\nOn Thu, Lee wrote:\n> Draft attached",
                      date="Thu, 20 Nov 2026 03:00:00 +0000", in_reply_to="<a1@x.example>", references="<a1@x.example>"),
        },
    }


def _imap_source(imap, smtp=None, **options):
    cfg = SourceConfig(id="imap-1", kind="mail", backend="imap",
                       options={"host": "imap.corp.example", "username": ME, "smtp_host": "smtp.corp.example", **options})
    return ImapSmtpSource(cfg, imap_factory=lambda h, p: imap, smtp_factory=smtp or FakeSmtp(),
                          secret_reader=lambda name: "pw" if name == "password" else None)


async def test_imap_whoami_is_the_login():
    assert (await _imap_source(FakeImap(_inbox())).whoami()).ids == frozenset({ME})


async def test_imap_pull_never_marks_seen():
    imap = FakeImap(_inbox(), flags={"Sent": "\\Sent"})
    result = await _imap_source(imap).pull(None)
    assert not imap.seen_changed
    assert ("EXAMINE", "INBOX") in imap.commands and not any(c[0] == "SELECT" for c in imap.commands)
    assert all(c[2] == "(UID BODY.PEEK[])" for c in imap.commands if c[0] == "UID FETCH")
    by_id = {m.external_id: m for m in result.messages}
    assert list(by_id) == ["<a1@x.example>", "<a2@corp.example>", "<a3@corp.example>"]  # the repeat of a1 is one item
    assert by_id["<a1@x.example>"].is_direct
    assert by_id["<a2@corp.example>"].is_bulk
    assert by_id["<a3@corp.example>"].is_invite
    assert result.cursor == "7:14"


async def test_imap_cursor_reads_only_newer_uids():
    imap = FakeImap(_inbox())
    result = await _imap_source(imap).pull("7:13")
    assert [m.text for m in result.messages] == ["dup"]
    other_validity = await _imap_source(FakeImap(_inbox(), validity="8")).pull("7:13")
    assert len(other_validity.messages) == 3  # UIDVALIDITY changed: start over by date


async def test_imap_bad_password_is_source_auth_error():
    with pytest.raises(SourceAuthError) as info:
        await _imap_source(FakeImap(_inbox(), accept="other")).pull(None)
    assert "mail password" in info.value.fix


async def test_smtp_reply_threads_and_goes_only_to_the_sender():
    smtp = FakeSmtp()
    imap = FakeImap(_inbox(), flags={"Sent": "\\Sent"})
    receipt = await _imap_source(imap, smtp).reply(ThreadRef("mail", {"root": "<a1@x.example>", "message_id": "<a1@x.example>"}), "Got it")
    (msg, sender, rcpts), = smtp.sent
    assert rcpts == ["lee@x.example"] and sender == ME
    assert msg["In-Reply-To"] == "<a1@x.example>" and "<a1@x.example>" in msg["References"]
    assert msg["Subject"] == "Re: Plan" and msg["Cc"] is None
    assert receipt.external_msg_id == msg["Message-ID"]


async def test_smtp_reply_to_header_is_used_and_flagged():
    box = _inbox()
    box["INBOX"][20] = rfc822(message_id="<rt@corp.example>", from_="Ann <ann@corp.example>", to=ME, subject="Invoice",
                              reply_to="pay@vendor.example")
    smtp = FakeSmtp()
    src = _imap_source(FakeImap(box), smtp)
    ref = ThreadRef("mail", {"root": "<rt@corp.example>", "message_id": "<rt@corp.example>"})
    thread = await src.thread(ref)
    assert thread.target.recipients == ("pay@vendor.example",)
    assert set(thread.target.flags) == {"reply_to_differs", "external_domain"}
    await src.reply(ref, "Paid")
    assert smtp.sent[0][2] == ["pay@vendor.example"]


async def test_smtp_refusal_is_send_failed():
    smtp = FakeSmtp(refuse=smtplib.SMTPRecipientsRefused({"lee@x.example": (550, b"no")}))
    with pytest.raises(SendFailed):
        await _imap_source(FakeImap(_inbox()), smtp).reply(ThreadRef("mail", {"root": "<a1@x.example>", "message_id": "<a1@x.example>"}), "x")


async def test_imap_find_sent():
    src = _imap_source(FakeImap(_inbox(), flags={"Sent": "\\Sent"}))
    ref = ThreadRef("mail", {"root": "<a1@x.example>", "message_id": "<a1@x.example>"})
    after = datetime(2026, 11, 20, 2, 0, tzinfo=timezone.utc)
    found = await src.find_sent(ref, after, "Thanks, will read.")
    assert found is not None and found.external_msg_id == "<r1@corp.example>"
    assert await src.find_sent(ref, after, "Thanks, will not read.") is None


def test_imap_quote_refuses_line_breaks():
    from core.inbox.sources.mail import imap_quote

    assert imap_quote('a"b') == '"a\\"b"'
    with pytest.raises(ValueError):
        imap_quote("<x>\r\nA001 STORE 1 +FLAGS (\\Deleted)")


async def test_outlook_listing_is_paged():
    def folders(a):
        if a.get("folder") == "sentitems":
            return {"emails": [{"conversationId": "S", "messages": [{"itemId": "S1", "from": _addr("Me", ME)}]}]}
        pages = [[{"conversationId": "CONV-1"}], [{"conversationId": "CONV-2"}], []]
        return {"emails": pages[a["offset"]]}

    second = {"conversationId": "CONV-2", "messages": [
        {"itemId": "IT-9", "changeKey": "CK-9", "internetMessageId": "<m9@corp.example>", "from": _addr("Lee", "lee@corp.example"),
         "toRecipients": [_addr("Me", ME)], "receivedDateTime": "2026-11-20T03:00:00Z", "body": "page two"}]}
    by_id = {"CONV-1": _conversation(), "CONV-2": second}
    mcp = _outlook(email_folders=folders, email_read=lambda a: by_id[a["conversationId"]])
    result = await OutlookMcpSource(_cfg(max_conversations=1), client=mcp).pull("2026-11-20T00:00:00+00:00")
    assert "<m9@corp.example>" in {m.external_id for m in result.messages}


async def test_imap_reads_the_oldest_first_and_continues_from_there():
    imap = FakeImap(_inbox())
    first = await _imap_source(imap, max_messages=2).pull(None)
    assert [m.external_id for m in first.messages] == ["<a1@x.example>", "<a2@corp.example>"] and first.cursor == "7:12"
    second = await _imap_source(FakeImap(_inbox()), max_messages=2).pull(first.cursor)
    assert [m.external_id for m in second.messages] == ["<a3@corp.example>", "<a1@x.example>"]  # uid 13, then 14 (a repeat id)
    assert second.cursor == "7:14"


async def test_outlook_refused_reply_tool_is_send_failed():
    from core.mcp_stdio_client import McpToolRefused

    mcp = _outlook(email_reply=lambda a: McpToolRefused("turned off"))
    with pytest.raises(SendFailed):
        await OutlookMcpSource(_cfg(), client=mcp).reply(
            ThreadRef("mail", {"conversation_id": "CONV-1", "message_id": "<m1@corp.example>"}), "x")



async def test_outlook_call_cut_short_by_the_budget_keeps_the_position():
    from tests.inbox_source_fakes import mcp_timeout

    mcp = _outlook(email_read=lambda a: mcp_timeout())
    result = await OutlookMcpSource(_cfg(pull_budget_s=5), client=mcp).pull("2026-11-20T00:00:00+00:00")
    assert result.cursor is None and result.messages == ()


# The shapes the live aws-outlook-mcp server returns: a folder listing names conversations
# (display names only, no messages); email_read gives content.emails with sender/from
# {name, email}, recipients, ccRecipients and the field spelled "recievedAt"; the messages
# carry no conversationId of their own.

def _live_read(cid, senders):
    return {"success": True, "content": {"message": "Found", "emails": [
        {"itemId": f"{cid}-{i}", "itemChangeKey": "K", "subject": "Topic",
         "sender": {"name": n, "email": e}, "from": {"name": n, "email": e},
         "recievedAt": f"2026-11-20T0{i + 1}:00:00+08:00", "dateTimeSent": f"2026-11-20T0{i + 1}:00:00+09:00",
         "recipients": [{"name": "Me", "email": ME}], "ccRecipients": [], "bccRecipients": [], "replyTo": [],
         "body": "<html><body><p>hello</p></body></html>", "itemClass": "IPM.Note"}
        for i, (n, e) in enumerate(senders)]}}


def _live_outlook():
    sent = {"success": True, "content": {"emails": [
        {"conversationId": c, "topic": "t", "senders": ["Me"], "items": []} for c in ("S1", "S2", "S3")]}}
    inbox = {"success": True, "content": {"emails": [{"conversationId": "IN1", "topic": "t", "senders": ["Priya"], "items": []}]}}
    reads = {"S1": _live_read("S1", [("Me", ME), ("Priya", "priya@corp.example")]),
             "S2": _live_read("S2", [("Me", ME)]),
             "S3": _live_read("S3", [("Lee", "lee@corp.example"), ("Me", ME)]),
             "IN1": _live_read("IN1", [("Priya", "priya@corp.example")])}
    return _outlook(email_folders=lambda a: sent if a.get("folder") == "sentitems" else inbox,
                    email_read=lambda a: reads[a["conversationId"]])


async def test_live_shape_whoami_is_the_sender_of_every_sent_conversation():
    me = await OutlookMcpSource(_cfg(), client=_live_outlook()).whoami()
    assert ME in me.ids and "priya@corp.example" not in me.ids


async def test_live_shape_pull_keeps_the_conversation_and_reads_recipients():
    src = OutlookMcpSource(_cfg(), client=_live_outlook())
    result = await src.pull("2026-11-19T00:00:00+00:00")
    assert len(result.messages) == 1
    m = result.messages[0]
    assert m.thread.data["conversation_id"] == "IN1"      # a reply needs its thread
    assert m.is_direct and m.text == "hello"              # To: me, HTML body as words
    assert m.received_at.isoformat() == "2026-11-20T01:00:00+08:00"  # when it arrived, not when it was sent
    thread = await src.thread(m.thread)
    assert thread.target is not None and len(thread.messages) == 1


def _as_latin1(text: str, encoding: str) -> str:
    """Text the way aws-outlook-mcp sometimes returns a body: its bytes read as Latin-1."""
    return text.encode(encoding).decode("latin-1")


async def test_a_body_read_as_latin1_is_repaired_and_the_sent_reply_is_found():
    sent = "测试：邮件回复，可以忽略"
    conv = _conversation()
    conv["messages"].append(
        {"itemId": "IT-4", "changeKey": "CK-4", "internetMessageId": "<m4@corp.example>", "subject": "RE: Budget",
         "from": _addr("Me", ME), "toRecipients": [_addr("Priya", "priya@corp.example")],
         "receivedDateTime": "2026-11-20T03:00:00Z",
         "body": _as_latin1(f"{sent}\n\n发件人: Priya <priya@corp.example>\n\n发送时间: 2026年11月20日\n\nThanks!", "gbk")})
    src = OutlookMcpSource(_cfg(), client=_outlook(email_read=lambda a: conv))
    ref = ThreadRef("mail", {"conversation_id": "CONV-1"})
    found = await src.find_sent(ref, datetime(2026, 11, 20, 2, 30, tzinfo=timezone.utc), sent)
    assert found is not None and found.external_msg_id == "<m4@corp.example>"
    thread = await src.thread(ref)
    assert thread.messages[-1].text == sent


@pytest.mark.parametrize("body, expected", [
    (_as_latin1("王小刚 南京", "utf-8"), "王小刚 南京"),
    (_as_latin1("携手一载，感恩相伴！", "gbk"), "携手一载，感恩相伴！"),
    ("élève à Paris, café crème", "élève à Paris, café crème"),  # real Latin-1 text stays
    ("a\xa0\xa0b\xa0\xa0c\xa0\xa0d", "a\xa0\xa0b\xa0\xa0c\xa0\xa0d"),  # no-break spaces only
    ("真的中文 already", "真的中文 already"),
])
def test_only_a_body_whose_bytes_were_misread_is_decoded_again(body, expected):
    records = split_conversations({"conversationId": "C", "messages": [
        {"itemId": "I", "from": _addr("A", "a@corp.example"), "receivedDateTime": "2026-11-20T01:00:00Z", "body": body}]})
    assert records[0].body == expected


async def test_the_sent_and_received_copies_of_one_mail_show_once():
    def copy(item, received):
        return {"itemId": item, "itemChangeKey": "CK-" + item, "subject": "To the list", "from": _addr("Me", ME),
                "recipients": [_addr("List", "list@corp.example")], "dateTimeSent": "2026-11-20T03:00:00Z",
                "recievedAt": received, "body": "Hello list"}
    conv = {"conversationId": "CONV-L", "messages": [
        copy("SENT-COPY", "2026-11-20T03:00:00Z"), copy("INBOX-COPY", "2026-11-20T03:00:01Z"),
        {**copy("NEXT", "2026-11-20T04:00:01Z"), "dateTimeSent": "2026-11-20T04:00:00Z", "body": "Second mail"},
    ]}
    src = OutlookMcpSource(_cfg(), client=_outlook(email_read=lambda a: conv))
    thread = await src.thread(ThreadRef("mail", {"conversation_id": "CONV-L"}))
    assert [m.text for m in thread.messages] == ["Hello list", "Second mail"]

# ---------------------------------------------------------------------------
# The person's own mail to one counterparty, status_detail, fixes on refused sends
# ---------------------------------------------------------------------------


async def test_outlook_own_messages_to_is_sent_mail_to_that_address():
    sent = {"emails": [{"conversationId": "CONV-9", "messages": [
        {"itemId": "S1", "internetMessageId": "<s1@corp.example>", "from": _addr("Me", ME),
         "toRecipients": [_addr("Priya", "priya@corp.example")], "receivedDateTime": "2026-11-19T10:00:00Z",
         "subject": "Re: Budget", "body": "Approved.\n\nOn Wed, Priya wrote:\n> please approve the budget"},
        {"itemId": "S2", "internetMessageId": "<s2@corp.example>", "from": _addr("Me", ME),
         "toRecipients": [_addr("Sam", "sam@corp.example")], "receivedDateTime": "2026-11-19T11:00:00Z",
         "body": "To someone else"},
        {"itemId": "S3", "internetMessageId": "<s3@corp.example>", "from": _addr("Priya", "priya@corp.example"),
         "toRecipients": [_addr("Priya", "priya@corp.example")], "receivedDateTime": "2026-11-19T12:00:00Z",
         "body": "Not mine"},
    ]}]}
    whoami_listing = _outlook().handlers["email_folders"]  # the person's address comes from the default sent listing
    mcp = _outlook(email_folders=lambda a: sent if a.get("startDate") else whoami_listing(a))
    src = OutlookMcpSource(_cfg(history_days=36500), client=mcp)
    to_priya = Thread(ref=ThreadRef("mail", {"conversation_id": "C"}), messages=(),
                      target=ReplyTarget(label="Priya", recipients=("priya@corp.example",)))
    own = await src.own_messages_to(to_priya, 10)
    assert [m.external_id for m in own] == ["<s1@corp.example>"]
    assert own[0].text == "Approved.", "the quoted history of the other person is not kept"
    folders = [a for t, a in mcp.calls if t == "email_folders"]
    assert folders and all(a["folder"] == "sentitems" for a in folders)
    # a reply that goes elsewhere than the sender (Reply-To), or to several people: none
    redirected = Thread(ref=to_priya.ref, messages=(), target=ReplyTarget(
        label="billing", recipients=("priya@corp.example",), flags=("reply_to_differs",)))
    several = Thread(ref=to_priya.ref, messages=(), target=ReplyTarget(
        label="two", recipients=("priya@corp.example", "sam@corp.example")))
    assert await src.own_messages_to(redirected, 10) == []
    assert await src.own_messages_to(several, 10) == []
    assert await src.own_messages_to(Thread(ref=to_priya.ref, messages=(), target=None), 10) == []


async def test_status_detail_says_why_sending_is_off():
    off = _outlook()
    off.env = {}
    src = OutlookMcpSource(_cfg(), client=off)
    assert not await src.can_send()
    assert "OUTLOOK_MCP_ENABLE_WRITES" in src.status_detail()
    on = OutlookMcpSource(_cfg(), client=_outlook())
    assert await on.can_send() and on.status_detail() == ""
    no_smtp = _imap_source(FakeImap(_inbox()), smtp_host="")
    assert not await no_smtp.can_send() and "SMTP" in no_smtp.status_detail()


async def test_imap_own_messages_to_and_an_smtp_login_refusal_keeps_the_fix():
    imap = FakeImap(_inbox(), flags={"Sent": "\\Sent"})
    src = _imap_source(imap, history_days=36500)
    to_lee = Thread(ref=ThreadRef("mail", {"root": "<a1@x.example>"}), messages=(),
                    target=ReplyTarget(label="Lee", recipients=("lee@x.example",)))
    own = await src.own_messages_to(to_lee, 10)
    assert [m.text for m in own] == ["Thanks, will read."]
    assert ImapSmtpSource.SECRET_NAMES == frozenset({"password", "smtp_password"})

    class RefusingLogin(FakeSmtp):
        def login(self, user, password):
            raise smtplib.SMTPAuthenticationError(535, b"bad credentials")

    with pytest.raises(SendFailed) as info:
        await _imap_source(FakeImap(_inbox()), RefusingLogin()).reply(
            ThreadRef("mail", {"root": "<a1@x.example>", "message_id": "<a1@x.example>"}), "x")
    assert "mail password" in info.value.fix


async def test_outlook_own_messages_to_reads_sent_conversations_when_the_listing_has_no_messages():
    # aws-outlook-mcp lists conversations, not messages: the address search picks which to read
    summaries = {"emails": [{"conversationId": "CONV-S", "topic": "Re: Budget", "items": []}]}
    whoami_listing = _outlook().handlers["email_folders"]
    conv = {"conversationId": "CONV-S", "messages": [
        {"itemId": "S1", "from": _addr("Me", ME), "recipients": [_addr("Priya", "priya@corp.example")],
         "dateTimeSent": "2026-11-19T10:00:00Z", "recievedAt": "2026-11-19T10:00:00Z", "body": "Approved."}]}
    mcp = _outlook(email_folders=lambda a: summaries if a.get("startDate") else whoami_listing(a),
                   email_search=lambda a: summaries if a.get("query") == "priya@corp.example" else {"emails": []},
                   email_read=lambda a: conv if a["conversationId"] == "CONV-S" else _conversation())
    src = OutlookMcpSource(_cfg(history_days=36500), client=mcp)
    to_priya = Thread(ref=ThreadRef("mail", {"conversation_id": "C"}), messages=(),
                      target=ReplyTarget(label="Priya", recipients=("priya@corp.example",)))
    assert [m.text for m in await src.own_messages_to(to_priya, 10)] == ["Approved."]


def test_an_english_gbk_mail_whose_only_non_ascii_is_a_curly_quote_is_decoded():
    body = _as_latin1("Thanks for joining today’s meeting. Here are the notes.", "gbk")
    records = split_conversations({"conversationId": "C", "messages": [
        {"itemId": "I", "from": _addr("A", "a@corp.example"), "receivedDateTime": "2026-11-20T01:00:00Z", "body": body}]})
    assert records[0].body == "Thanks for joining today’s meeting. Here are the notes."


async def test_the_external_sender_banner_is_not_the_message_text():
    banner = ("CAUTION: This email originated from outside of the organization. Do not click links "
              "or open attachments unless you can confirm the sender and know the content is safe.")
    conv = {"conversationId": "CONV-X", "messages": [
        {"itemId": "X1", "changeKey": "CK", "internetMessageId": "<x1@ext.example>", "subject": "Hello",
         "from": _addr("Alan", "alan@ext.example"), "toRecipients": [_addr("Me", ME)],
         "receivedDateTime": "2026-11-20T01:00:00Z", "body": f"{banner}\n\nHi Xiaogang,\n\nCan we meet?"}]}
    src = OutlookMcpSource(_cfg(), client=_outlook(email_read=lambda a: conv))
    thread = await src.thread(ThreadRef("mail", {"conversation_id": "CONV-X"}))
    assert thread.messages[-1].text.startswith("Hi Xiaogang,")
    from core.inbox.sources.mail import _without_banner
    assert _without_banner("CAUTION: wet floor\n\nreal text") == "CAUTION: wet floor\n\nreal text"


def test_a_gbk_body_with_symbols_and_user_defined_characters_is_decoded():
    text = "会议纪要：① 上传知识 · 结果×2 ← 完成"
    raw = text.encode("gbk") + b"\xaa\xa1" + "。".encode("gbk")      # AAA1: GBK user-defined, decodes to U+E000
    records = split_conversations({"conversationId": "C", "messages": [
        {"itemId": "I", "from": _addr("A", "a@corp.example"), "receivedDateTime": "2026-11-20T01:00:00Z",
         "body": raw.decode("latin-1")}]})
    assert records[0].body.startswith(text)
