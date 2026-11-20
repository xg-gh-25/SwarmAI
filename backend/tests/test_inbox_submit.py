"""``inbox submit`` (core/inbox/submit.py) and its CLI (design §4, §8, §11 #9 and #15).

- A submitted item is PROPOSED only, never REPLY, and only after source.thread confirms
  the thread exists, can be replied to, and the person is in it or it is public.
- ``--sent`` records an item in state replied and one send origin auto:<producer>, status
  sent; it never calls reply (nor opens the source); the same message id twice is one row.
- The CLI refuses a database older than v20 instead of migrating it.
"""
from __future__ import annotations

import asyncio
import json

import pytest

from core.inbox import submit
from core.inbox.models import Message, ReplyTarget
from tests import inbox_fakes as fx


@pytest.fixture
async def env():
    await fx.clear()
    fake = fx.install(fx.FakeSource())
    await fx.add_source()
    return fake


def _req(**kw) -> submit.SubmitRequest:
    base = dict(source_id="s1", thread_ref=fx.ref(7), producer="github-outreach",
                draft="Thanks, a fix is in #12.", title="Issue 42", text="crash on start",
                ask="Answer the crash report")
    base.update(kw)
    return submit.SubmitRequest(**base)


async def test_submit_creates_proposed_after_thread_check(env):
    from database import db
    env.thread_messages = [fx.msg("q1", thread=7, direct=False),
                           fx.msg("mine", thread=7, author="U_ME", direct=False)]
    out = await submit.submit(_req())
    assert out["created"] is True
    item = await db.inbox_get_item(out["item_id"])
    assert item["lane"] == "proposed" and item["origin"] == "submit:github-outreach"
    assert json.loads(item["meta"])["producer"] == "github-outreach"
    assert (await db.inbox_latest_draft(item["id"]))["text"] == "Thanks, a fix is in #12."
    assert await db.inbox_list_items("reply") == [], "a submitted item never reaches REPLY"
    assert env.calls["thread"] == 1 and env.calls["reply"] == 0
    again = await submit.submit(_req())
    assert again == {"item_id": item["id"], "created": False}


async def test_submit_refuses_thread_without_person(env):
    from database import db
    env.thread_messages = [fx.msg("q1", thread=7, direct=False)]
    with pytest.raises(submit.SubmitRefused):
        await submit.submit(_req())
    assert await db.inbox_list_items("proposed") == []


async def test_submit_accepts_a_public_thread_the_person_is_not_in(env):
    env.thread_messages = [fx.msg("q1", thread=7, direct=False)]
    env.target = ReplyTarget(label="org/repo#42", recipients=("org/repo#42",), public=True)
    assert (await submit.submit(_req()))["created"] is True


@pytest.mark.parametrize("case", ["no_thread", "no_target", "wrong_kind", "no_source", "producer"])
async def test_submit_refusals(env, case):
    from database import db
    env.thread_messages = [fx.msg("q1", thread=7)]
    req = _req()
    if case == "no_thread":
        async def missing(ref):
            raise LookupError("404")
        env.thread = missing
    elif case == "no_target":
        env.target = None
    elif case == "wrong_kind":
        req = _req(thread_ref=fx.ref(7, kind="slack"))
    elif case == "no_source":
        req = _req(source_id="nope")
    else:
        req = _req(producer="bad producer!")
    with pytest.raises(submit.SubmitRefused):
        await submit.submit(req)
    assert await db.inbox_list_items("proposed") == []


async def test_submit_refuses_a_feed_source(env):
    await fx.add_source("feedish", category="feed")
    with pytest.raises(submit.SubmitRefused):
        await submit.submit(_req(source_id="feedish"))


def _rec(**kw) -> submit.SentRecord:
    base = dict(source_id="s1", thread_ref=fx.ref(7), producer="github-outreach",
                external_msg_id="IC_123", text="Thanks for the report!",
                url="https://github.example/o/r/issues/42#issuecomment-123", to_label="o/r#42")
    base.update(kw)
    return submit.SentRecord(**base)


async def test_sent_record_never_calls_reply_and_is_one_row(env):
    from database import db
    out = await submit.submit_sent(_rec())
    assert out["created"] is True
    row = await db.inbox_get_send(out["send_id"])
    assert row["origin"] == "auto:github-outreach" and row["status"] == "sent"
    item = await db.inbox_get_item(out["item_id"])
    assert item["state"] == "replied" and item["lane"] == "proposed"
    assert env.calls["reply"] == 0 and env.calls["thread"] == 0 and env.calls["whoami"] == 0
    again = await submit.submit_sent(_rec(text="different text, same comment"))
    assert again == {"item_id": out["item_id"], "send_id": out["send_id"], "created": False}
    assert len(await db.inbox_sends(("sent",))) == 1
    async with fx.client() as http:
        sends = (await http.get("/api/inbox/sends?filter=auto")).json()
    assert [s["id"] for s in sends["sent"]] == [out["send_id"]]
    assert sends["sent"][0]["origin"] == "auto" and sends["sent"][0]["producer"] == "github-outreach"
    assert sends["need_you"] == [] and sends["auto_7d"] == 1
    async with fx.client() as http:
        mine = (await http.get("/api/inbox/sends?filter=mine")).json()
    assert mine["sent"] == []


async def test_sent_record_needs_a_message_id(env):
    with pytest.raises(submit.SubmitRefused):
        await submit.submit_sent(_rec(external_msg_id=" "))


def test_cli_refuses_an_old_database(tmp_path, monkeypatch, capsys):
    import sqlite3
    old = tmp_path / "old.db"
    conn = sqlite3.connect(old)
    conn.execute("PRAGMA user_version = 19")
    conn.close()
    assert submit._schema_ready(str(old)) is False
    payload = tmp_path / "p.json"
    payload.write_text(json.dumps({"source_id": "s1", "producer": "p",
                                   "thread_ref": {"source_kind": "fake", "data": {}}}))
    monkeypatch.setattr(submit, "_schema_ready", lambda path: False)
    assert submit.main(["submit", "--json", str(payload)]) == 1
    assert "schema" in capsys.readouterr().err
    assert sqlite3.connect(old).execute("PRAGMA user_version").fetchone()[0] == 19


def test_cli_sent_record_end_to_end(tmp_path, monkeypatch, capsys):
    """The CLI as a job runs it: --sent with a JSON file, twice; one record."""
    from database.sqlite import close_all_pools

    async def setup():
        await fx.clear()
        await fx.add_source()
        await close_all_pools()

    asyncio.run(setup())
    fx.install(fx.FakeSource())
    payload = tmp_path / "rec.json"
    payload.write_text(json.dumps({
        "source_id": "s1", "producer": "github-outreach",
        "thread_ref": {"source_kind": "fake", "data": {"thread": "t7"}},
        "external_msg_id": "IC_9", "text": "Thanks!", "url": "https://github.example/x"}))
    assert submit.main(["submit", "--sent", "--json", str(payload)]) == 0
    first = json.loads(capsys.readouterr().out)
    assert first["ok"] and first["created"] is True
    assert submit.main(["submit", "--sent", "--json", str(payload)]) == 0
    second = json.loads(capsys.readouterr().out)
    assert second["created"] is False and second["send_id"] == first["send_id"]
    assert submit.main(["submit", "--json", str(tmp_path / "missing.json")]) == 2
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"source_id": "nope", "producer": "p", "external_msg_id": "1",
                               "text": "x", "thread_ref": {"source_kind": "fake", "data": {}}}))
    assert submit.main(["submit", "--sent", "--json", str(bad)]) == 1


def test_backend_program_has_the_inbox_subcommand():
    import desktop_main
    assert "inbox" in desktop_main._SUBCOMMANDS and "inbox submit" in desktop_main.USAGE


async def test_submit_cannot_attach_to_a_pulled_item(env):
    """A submission naming a pulled item's external id lands in the producer's own
    namespace: the pulled item and its draft are untouched."""
    from database import db
    real = await fx.add_item("s1", "e1")
    await db.inbox_insert_draft({"id": "dr_real", "item_id": real["id"], "text": "mine",
                                 "lang": "", "note": "", "model": "m",
                                 "created_at": "2026-11-20T00:00:00.000000+00:00"})
    env.thread_messages = [fx.msg("q", thread=7)]
    out = await submit.submit(_req(external_id="e1", draft="attacker text"))
    assert out["item_id"] != real["id"]
    assert (await db.inbox_latest_draft(real["id"]))["text"] == "mine"
    sent = await submit.submit_sent(_rec(external_id="e1"))
    assert sent["item_id"] != real["id"]
    assert (await db.inbox_get_item(real["id"]))["state"] == "open"


async def test_submit_refuses_an_id_of_another_thread_or_a_handled_proposal(env):
    from database import db
    env.thread_messages = [fx.msg("q", thread=7), fx.msg("q8", thread=8)]
    first = await submit.submit(_req(external_id="p1"))
    with pytest.raises(submit.SubmitRefused):
        await submit.submit(_req(external_id="p1", thread_ref=fx.ref(8)))
    await db.inbox_update_item(first["item_id"], "2026-11-20T00:00:00.000000+00:00", state="dismissed")
    with pytest.raises(submit.SubmitRefused):
        await submit.submit(_req(external_id="p1"))
    assert (await submit.submit(_req(external_id="p2")))["created"] is True


async def test_no_source_id_means_the_one_conversation_source_of_the_kind(env):
    from database import db
    env.thread_messages = [fx.msg("q1", thread=7, author="U_ME", direct=False)]
    await fx.add_source("feedish", category="feed")  # a feed of the same kind is not a candidate
    out = await submit.submit(_req(source_id=""))
    assert (await db.inbox_get_item(out["item_id"]))["source_id"] == "s1"
    sent = await submit.submit_sent(_rec(source_id=""))
    assert (await db.inbox_get_send(sent["send_id"]))["source_id"] == "s1"
    await fx.add_source("s2")
    with pytest.raises(submit.SubmitRefused, match="2 Inbox sources"):
        await submit.submit_sent(_rec(source_id="", external_msg_id="IC_999"))
