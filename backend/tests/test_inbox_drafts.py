"""Inbox drafting (design 2026-11-20-inbox §6.3, §11 items 5 and 7): no tools, and a context
of this thread and the person's own messages only."""
from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone

import pytest

from core.inbox.drafts import make_draft
from core.inbox.models import Identity, Message, ReplyTarget, Thread, ThreadRef
from jobs.bedrock import StructuredOutputError
from tests.inbox_model_fake import FakeBedrock, text_reply

ME = Identity(ids=frozenset({"U_ME"}), display="Me")
REF = ThreadRef("slack", {"channel": "D1", "ts": "1"})
OTHER = ThreadRef("slack", {"channel": "D9", "ts": "9"})
T0 = datetime.now(timezone.utc) - timedelta(hours=3)


def m(eid, author, text, ref=REF, name="Priya", minutes=0) -> Message:
    return Message(external_id=eid, thread=ref, author_id=author, author_name=name,
                   received_at=T0 + timedelta(minutes=minutes), text=text)


def thread(*msgs) -> Thread:
    return Thread(ref=REF, messages=tuple(msgs), target=ReplyTarget(label="Priya", recipients=("U_PRIYA",)))


async def test_draft_call_has_no_tools(monkeypatch):
    fake = FakeBedrock(monkeypatch, text_reply("LANG: en\n\nSure, [date] works."))
    await make_draft(thread(m("a", "U_PRIYA", "When can we meet?")), ME, [])
    assert len(fake.calls) == 1
    assert fake.tool_config is None
    assert fake.calls[0]["feature"] == "inbox.draft"
    assert fake.threads[0] is not threading.main_thread()


async def test_draft_context_holds_only_this_thread_and_own_messages(monkeypatch):
    fake = FakeBedrock(monkeypatch, text_reply("LANG: en\n\nHere they are: [the numbers]."))
    t = thread(m("a", "U_PRIYA", "Can you send the Q3 numbers?"),
               m("b", "U_ME", "Looking now.", name="Me", minutes=1))
    history = [
        m("h1", "U_ME", "I shared the deck last week.", ref=OTHER, name="Me", minutes=-60),
        m("h2", "U_BOB", "BOB-SECRET launch plan", ref=OTHER, name="Bob", minutes=-50),
        m("h3", "U_PRIYA", "PRIYA-OTHER-THREAD salary details", ref=OTHER, minutes=-40),
        m("b", "U_ME", "Looking now.", name="Me", minutes=1),   # already in the thread
    ]
    draft = await make_draft(t, ME, history)
    sent = fake.system + fake.user_turn
    assert "BOB-SECRET" not in sent and "PRIYA-OTHER-THREAD" not in sent
    assert "I shared the deck last week." in fake.user_turn
    assert "Can you send the Q3 numbers?" in fake.user_turn
    assert fake.user_turn.count("Looking now.") == 1
    assert "Can you send" not in fake.system          # thread text never in the system prompt
    assert draft.note == "参考了你和 Priya 的 1 条往来"


async def test_draft_returns_lang_and_note(monkeypatch):
    FakeBedrock(monkeypatch, text_reply("LANG: en\n\nHi Priya, the total is [the number]."))
    d = await make_draft(thread(m("a", "U_PRIYA", "What is the total?")), ME, [])
    assert (d.text, d.lang, d.note) == ("Hi Priya, the total is [the number].", "en", "只参考了这个 thread")

    FakeBedrock(monkeypatch, text_reply("好的，周五前发你。"))     # no LANG line: script fallback
    d = await make_draft(thread(m("a", "U_ZL", "方案什么时候能给我看？", name="周琳")), ME,
                         [m("h", "U_ME", "我先整理一下", ref=OTHER, name="Me", minutes=-5)])
    assert (d.text, d.lang, d.note) == ("好的，周五前发你。", "zh", "参考了你和周琳的 1 条往来")


async def test_draft_with_only_a_lang_line_is_missing(monkeypatch):
    FakeBedrock(monkeypatch, text_reply("LANG: en\n\n"))
    with pytest.raises(StructuredOutputError):
        await make_draft(thread(m("a", "U_PRIYA", "Ping?")), ME, [])


@pytest.mark.parametrize("reply", ["LANG:\n\nOK\nthanks", "LANG: english\n\nOK\nthanks",
                                   "lang: en.\nOK\nthanks", "LANG: zh-CN, en\nOK\nthanks"])
async def test_draft_lang_line_never_swallows_the_reply(monkeypatch, reply):
    FakeBedrock(monkeypatch, text_reply(reply))
    d = await make_draft(thread(m("a", "U_PRIYA", "Can you confirm?")), ME, [])
    assert d.text == "OK\nthanks" and d.lang == "en"


async def test_long_thread_keeps_its_opening_message(monkeypatch):
    fake = FakeBedrock(monkeypatch, text_reply("LANG: en\n\nDone."))
    msgs = [m("first", "U_PRIYA", "OPENING-ASK: review the contract")]
    msgs += [m(f"x{i}", "U_PRIYA", f"MIDDLE-{i}", minutes=i + 1) for i in range(34)]
    await make_draft(thread(*msgs), ME, [])
    assert "OPENING-ASK" in fake.user_turn and "MIDDLE-33" in fake.user_turn
    assert "MIDDLE-0\n" not in fake.user_turn and "messages of this thread between these not shown" in fake.user_turn


@pytest.mark.parametrize("text,lang", [("参考：数据・报告・结论・附件", "zh"),
                                      ("请确认这份报告里的数据是否正确，品牌名叫ア", "zh"),
                                      ("サーバー・ログを確認してください。明日までにお願いします。", "ja")])
async def test_script_fallback_tells_chinese_from_japanese(monkeypatch, text, lang):
    FakeBedrock(monkeypatch, text_reply("好的"))
    d = await make_draft(thread(m("a", "U_X", text, name="X")), ME, [])
    assert d.lang == lang


async def test_draft_fence_nonce_avoids_the_subject(monkeypatch):
    from core.inbox import prompts
    draws = iter(["c" * 16, "d" * 16])
    monkeypatch.setattr(prompts.secrets, "token_hex", lambda n: next(draws))
    fake = FakeBedrock(monkeypatch, text_reply("LANG: en\n\nOk."))
    msgs = [m(f"x{i}", "U_PRIYA", f"text {i}", minutes=i) for i in range(35)]
    # the subject sits only on message 2, which a 35-message thread does not show
    msgs[1] = Message(external_id="x1", thread=REF, author_id="U_PRIYA", author_name="Priya",
                      received_at=T0, text="hi", title="Re: " + "c" * 16)
    await make_draft(thread(*msgs), ME, [])
    assert f"<external-data-{'d' * 16}>" in fake.user_turn
