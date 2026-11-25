"""What keeps 等你回复 to the messages a person is waiting on (design
docs/design/2026-11-20-inbox.md §6-§7, revision 2026-11-25).

- An automated mail sender is recognised by a token anywhere in its address
  (``m365-notifications@``, ``anomalydetection@costalerts.…``), never only at the start.
- The triage prompt defines reply as "a person waits for a written answer" and puts
  things to do without replying in aware, with ``needs_action`` / ``due`` in the answer.
- A sender in the source's ``quiet_senders`` never lands in REPLY: by rule and whatever
  the model answers; the answer's ``needs_action`` / ``due`` reach ``ItemOut``.
- Lists are one row per thread, newest first, with ``thread_count``; counts count threads.
- 不用回 teaches: ``to_aware`` / ``mute_sender`` write a relane that reaches the next
  triage's examples; ``dismiss`` (handled elsewhere) teaches nothing. ``mute_sender`` also
  moves the sender's other open REPLY items and remembers the sender (never for an item
  with no sender). Every action covers the whole thread; PROPOSED drafts are one row each.
- A mail thread is its conversation (``ThreadRef.key`` leaves out ``message_id``); rows
  stored with the old key are regrouped by ``store.rekey_threads``.
- 稍后 comes back at the time chosen; 这一天的都知道了 marks many rows at once.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest

from core.inbox import caller, runner, store, triage
from core.inbox.models import Candidate, ItemState, Lane, SourceCategory, TriageResult
from core.inbox.prompts import TRIAGE_SYSTEM
from core.inbox.sources.mail import is_automated_address
from routers.inbox import snooze_until
from tests import inbox_fakes as fx


@pytest.mark.parametrize("addr,expected", [
    ("m365-notifications@amazon.com", True),
    ("iliad-diana-google-notifications@amazon.com", True),
    ("anomalydetection@costalerts.amazonaws.com", True),
    ("no-reply-aws@amazon.com", True),
    ("noreply@github.com", True),
    ("alerts.team@example.com", True),
    ("priya@example.com", False),
    ("walter@example.com", False),
    ("valerie@alerts.com", False),        # the registrable domain alone is not a label we read
    ("not-an-address", False),
])
def test_automated_mail_senders(addr, expected):
    assert is_automated_address(addr) is expected


def test_prompt_reply_means_a_written_answer():
    assert "an answer or an action" not in TRIAGE_SYSTEM
    assert "write an answer" in TRIAGE_SYSTEM
    assert "never reply" in TRIAGE_SYSTEM and "needs_action" in TRIAGE_SYSTEM
    props = triage._schema(["a"])["properties"]["items"]["items"]["properties"]
    assert {"needs_action", "due"} <= set(props)


def test_rule_lane_quiet_sender_is_aware_without_floor():
    m = fx.msg("e1", author="Health@AWS.com", direct=True)
    v = triage.rule_lane(m, fx.ME, SourceCategory.CONVERSATION, frozenset({"health@aws.com"}))
    assert v == triage.RuleVerdict(Lane.AWARE, None)
    assert triage.rule_lane(m, fx.ME, SourceCategory.CONVERSATION).lane is Lane.REPLY


def test_result_keeps_the_models_action_for_the_runner_to_judge_by_final_lane():
    c = Candidate(id="a", source_kind="fake", message=fx.msg("e1"), rule_lane=Lane.REPLY, floor=Lane.AWARE)
    r = triage._result(c, {"id": "a", "lane": "reply", "priority": 1, "reason": "r",
                           "needs_action": True, "due": "2026-12-24"})
    assert r.lane is Lane.REPLY and r.needs_action is True and r.due == "2026-12-24"


def test_a_mail_thread_is_its_conversation_not_one_message():
    from core.inbox.models import ThreadRef
    a = ThreadRef("mail", {"conversation_id": "c1", "message_id": "m1"})
    b = ThreadRef("mail", {"conversation_id": "c1", "message_id": "m2"})
    assert a.key() == b.key() and a.data["message_id"] == "m1", "the reply still knows its message"
    assert a.key() != ThreadRef("mail", {"conversation_id": "c2", "message_id": "m1"}).key()


@pytest.fixture
async def env(monkeypatch):
    await fx.clear()
    fake = fx.install(fx.FakeSource())
    await fx.add_source()
    monkeypatch.setattr(caller, "check_caller", lambda client, server: 4242)
    answers: dict = {"lane": Lane.REPLY}

    async def fake_triage(candidates, context):
        answers["examples"] = context.examples
        return [TriageResult(id=c.id, lane=answers["lane"], priority=1, reason="r",
                             reply_expected=True, needs_action=answers.get("needs_action", False),
                             due=answers.get("due", "")) for c in candidates]

    monkeypatch.setattr(triage, "triage", fake_triage)
    monkeypatch.setattr(runner, "pull_window", lambda: (runner.dtime(0, 0), runner.dtime(23, 59, 59)))
    async with fx.client() as http:
        yield fake, http, answers


async def _source():
    from database import db
    return await db.inbox_get_source("s1")


async def _put(external_id: str, *, thread: int = 1, minutes: int = 0, lane: str = "reply",
               author: str = "U_PRIYA", state: str = "open") -> dict:
    from database import db
    row = store.message_to_item_row("s1", fx.msg(external_id, thread=thread, minutes=minutes, author=author),
                                    Lane(lane), None, state=ItemState(state))
    stored = await db.inbox_insert_items([row])
    return await db.inbox_get_item(stored[external_id][0])


async def test_quiet_sender_never_reaches_reply_whatever_the_model_says(env):
    from database import db
    fake, _http, answers = env
    await db.inbox_update_source("s1", store.ts(store.now()), config=json.dumps({"quiet_senders": ["u_bot"]}))
    answers.update(lane=Lane.REPLY, needs_action=True, due="2026-12-24")
    fake.messages = [fx.msg("q", author="U_BOT", direct=True), fx.msg("p", thread=2, direct=True)]
    await runner.pull_source(await _source())
    q = await db.inbox_get_item_by_external("s1", "q")
    p = await db.inbox_get_item_by_external("s1", "p")
    assert q["lane"] == "aware" and p["lane"] == "reply"
    out = store.item_out(q)
    assert out["needs_action"] is True and out["due"] == "2026-12-24"
    assert store.item_out(p)["needs_action"] is False, "a reply item is never needs_action"


async def test_list_is_one_row_per_thread_newest_first(env):
    from database import db
    _fake, http, _ = env
    await _put("old", thread=1, minutes=0)
    await _put("mid", thread=2, minutes=5)
    await _put("new1", thread=1, minutes=10)
    await _put("x", thread=3, minutes=20, lane="aware")
    rows = (await http.get("/api/inbox/items?lane=reply")).json()
    assert [r["thread_count"] for r in rows] == [2, 1]
    assert [r["excerpt"] for r in rows] == ["can you review this?"] * 2
    ids = [r["id"] for r in rows]
    assert ids[0] == (await db.inbox_get_item_by_external("s1", "new1"))["id"]
    assert ids[1] == (await db.inbox_get_item_by_external("s1", "mid"))["id"]
    counts = (await http.get("/api/inbox/counts")).json()
    assert counts["reply"] == 2 and counts["aware"] == 1, "counts count threads"


async def test_to_aware_moves_the_thread_and_teaches_triage(env):
    from database import db
    fake, http, answers = env
    a = await _put("a", thread=1, minutes=0)
    b = await _put("b", thread=1, minutes=5)
    r = await http.post(f"/api/inbox/items/{b['id']}/action", json={"action": "to_aware"}, headers=fx.UA)
    assert r.status_code == 200 and r.json()["lane"] == "aware"
    assert (await db.inbox_get_item(a["id"]))["lane"] == "aware", "the whole thread moves"
    fb = await db.inbox_recent_feedback("s1")
    assert [(f["action"], f["from_lane"], f["to_lane"]) for f in fb] == [("relane", "reply", "aware")]
    again = await http.post(f"/api/inbox/items/{b['id']}/action", json={"action": "to_aware"}, headers=fx.UA)
    assert again.status_code == 409, "only an item waiting on a reply can move"
    fake.messages = [fx.msg("n", thread=9)]
    await runner.pull_source(await _source())
    assert any(e.startswith("reply -> aware: from U_PRIYA") for e in answers["examples"])


async def test_handled_elsewhere_teaches_triage_nothing(env):
    """A dismiss means the person answered outside the Inbox: it is no lane correction."""
    fake, http, answers = env
    a = await _put("a")
    await http.post(f"/api/inbox/items/{a['id']}/action", json={"action": "dismiss"}, headers=fx.UA)
    fake.messages = [fx.msg("n", thread=9)]
    await runner.pull_source(await _source())
    assert answers["examples"] == ()


async def test_quiet_sender_keeps_needs_action_when_the_model_said_reply(env):
    from database import db
    fake, _http, answers = env
    await db.inbox_update_source("s1", store.ts(store.now()), config=json.dumps({"quiet_senders": ["u_bot"]}))
    # the real _result on a model answer of "reply": the runner, not _result, decides
    # needs_action by the final lane (a quiet sender's reply becomes aware and keeps it)
    monkey = triage.triage

    async def real_result(candidates, context):
        return [triage._result(c, {"id": c.id, "lane": "reply", "priority": 1, "reason": "r",
                                   "needs_action": True, "due": "2026-12-01"}) for c in candidates]

    triage.triage = real_result
    try:
        fake.messages = [fx.msg("q", author="U_BOT", direct=True), fx.msg("p", thread=2, direct=True)]
        await runner.pull_source(await _source())
    finally:
        triage.triage = monkey
    q = store.item_out(await db.inbox_get_item_by_external("s1", "q"))
    p = store.item_out(await db.inbox_get_item_by_external("s1", "p"))
    assert q["lane"] == "aware" and q["needs_action"] is True and q["due"] == "2026-12-01"
    assert p["lane"] == "reply" and p["needs_action"] is False


async def test_mute_without_a_sender_moves_no_other_item(env):
    from database import db
    _fake, http, _ = env
    a = await _put("a", thread=1, author="")
    b = await _put("b", thread=2, author="")
    r = await http.post(f"/api/inbox/items/{a['id']}/action", json={"action": "mute_sender"}, headers=fx.UA)
    assert r.status_code == 200 and r.json()["lane"] == "aware"
    assert (await db.inbox_get_item(b["id"]))["lane"] == "reply", "an authorless row is nobody's"
    assert store.quiet_senders(await _source()) == frozenset()


async def test_proposed_drafts_are_one_row_each(env):
    from database import db
    _fake, http, _ = env
    a = await _put("a", thread=1, lane="proposed")
    b = await _put("b", thread=1, lane="proposed", minutes=3)
    rows = (await http.get("/api/inbox/items?lane=proposed")).json()
    assert [r["id"] for r in rows] == [b["id"], a["id"]] and [r["thread_count"] for r in rows] == [1, 1]
    assert (await http.get("/api/inbox/counts")).json()["proposed"] == 2
    await http.post(f"/api/inbox/items/{b['id']}/action", json={"action": "dismiss"}, headers=fx.UA)
    assert (await db.inbox_get_item(a["id"]))["state"] == "open", "the unseen draft is not swept"


async def test_a_reply_points_at_the_row_the_list_shows(env):
    from database import db
    _fake, _http, _ = env
    first = await _put("r1", thread=1, minutes=10)
    second = await _put("r2", thread=1, minutes=20)
    got = await db.inbox_later_item("s1", first["thread_key"], store.ts(fx.T0))
    assert got == second["id"]


async def test_rekey_threads_regroups_old_mail_rows(env):
    from database import db
    _fake, _http, _ = env
    a = await _put("a", thread=1)
    b = await _put("b", thread=1, minutes=1)
    ref = {"source_kind": "mail", "data": {"conversation_id": "c1", "message_id": "x"}}
    for item, mid in ((a, "m1"), (b, "m2")):
        ref["data"]["message_id"] = mid
        old_key = 'mail:' + json.dumps(ref["data"], sort_keys=True)
        async with __import__("database.sqlite", fromlist=["_get_pool"])._get_pool(str(db.db_path)).borrow(readonly=False) as conn:
            await conn.execute("UPDATE inbox_items SET thread_ref = ?, thread_key = ? WHERE id = ?",
                               (json.dumps(ref, sort_keys=True), old_key, item["id"]))
            await conn.commit()
    assert await store.rekey_threads() == 2
    ka, kb = [(await db.inbox_get_item(x["id"]))["thread_key"] for x in (a, b)]
    assert ka == kb == 'mail:{"conversation_id": "c1"}'
    assert json.loads((await db.inbox_get_item(a["id"]))["thread_ref"])["data"]["message_id"] == "m1"
    assert await store.rekey_threads() == 0


async def test_mute_sender_moves_the_sender_and_is_remembered(env):
    from database import db
    _fake, http, _ = env
    a = await _put("a", thread=1, author="U_BOT")
    b = await _put("b", thread=2, author="u_bot", minutes=3)
    c = await _put("c", thread=3, author="U_PRIYA")
    await db.inbox_update_item(b["id"], store.ts(store.now()), state="snoozed",
                               snooze_until="2999-01-01T00:00:00.000000+00:00")
    r = await http.post(f"/api/inbox/items/{a['id']}/action", json={"action": "mute_sender"}, headers=fx.UA)
    assert r.status_code == 200
    assert (await db.inbox_get_item(a["id"]))["lane"] == "aware"
    moved = await db.inbox_get_item(b["id"])
    assert moved["lane"] == "aware" and moved["state"] == "open"
    assert (await db.inbox_get_item(c["id"]))["lane"] == "reply", "another sender stays"
    assert store.quiet_senders(await _source()) == frozenset({"u_bot"})
    sources = (await http.get("/api/inbox/sources")).json()
    assert [s["options"].get("quiet_senders") for s in sources if s["id"] == "s1"] == [["u_bot"]]
    undo = await http.patch("/api/inbox/sources/s1", json={"quiet_senders": []}, headers=fx.UA)
    assert undo.status_code == 200 and store.quiet_senders(await _source()) == frozenset()


async def test_later_covers_the_thread_until_the_chosen_time(env):
    from database import db
    _fake, http, _ = env
    a = await _put("a", thread=1)
    b = await _put("b", thread=1, minutes=2)
    r = await http.post(f"/api/inbox/items/{b['id']}/action",
                        json={"action": "later", "until": "tomorrow"}, headers=fx.UA)
    assert r.json()["state"] == "snoozed"
    for item in (a, b):
        row = await db.inbox_get_item(item["id"])
        back = store.parse_ts(row["snooze_until"]).astimezone()
        assert row["state"] == "snoozed" and (back.hour, back.minute) == (9, 0)


def test_snooze_until_times():
    # local time of this machine, whatever its zone: 9:00 is the person's own 9:00
    wed = datetime(2026, 11, 25, 15, 0).astimezone()      # a Wednesday
    assert snooze_until("3h", wed) == wed + timedelta(hours=3)
    t = snooze_until("tomorrow", wed).astimezone()
    assert (t.date().isoformat(), t.hour) == ("2026-11-26", 9)
    n = snooze_until("next_week", wed).astimezone()
    assert (n.date().isoformat(), n.weekday(), n.hour) == ("2026-11-30", 0, 9)
    mon = datetime(2026, 11, 30, 8, 0).astimezone()
    assert snooze_until("next_week", mon).astimezone().date().isoformat() == "2026-12-07"


async def test_done_many_marks_rows_and_their_threads(env):
    from database import db
    _fake, http, _ = env
    a = await _put("a", thread=1, lane="aware")
    a2 = await _put("a2", thread=1, lane="aware", minutes=1)
    b = await _put("b", thread=2, lane="aware")
    c = await _put("c", thread=3, lane="aware")
    assert (await http.post("/api/inbox/items/actions", json={"ids": [a2["id"]], "action": "done"})).status_code == 403
    r = await http.post("/api/inbox/items/actions", json={"ids": [a2["id"], b["id"], "nope"], "action": "done"},
                        headers=fx.UA)
    assert r.status_code == 200 and r.json() == {"handled": 3}
    states = {x["id"]: (await db.inbox_get_item(x["id"]))["state"] for x in (a, a2, b, c)}
    assert states == {a["id"]: "done", a2["id"]: "done", b["id"]: "done", c["id"]: "open"}


async def test_sending_from_a_thread_row_answers_the_whole_conversation(env):
    from database import db
    _fake, http, _ = env
    older = await _put("o", thread=1, minutes=0)
    newer = await _put("n", thread=1, minutes=5)
    other = await _put("x", thread=2, minutes=1)
    sha = (await http.get(f"/api/inbox/items/{newer['id']}/target")).json()["target_sha"]
    r = await http.post(f"/api/inbox/items/{newer['id']}/send", headers=fx.UA,
                        json={"text": "done", "target_sha": sha})
    assert r.status_code == 200 and r.json()["status"] == "sent"
    assert (await db.inbox_get_item(newer["id"]))["state"] == "replied"
    assert (await db.inbox_get_item(older["id"]))["state"] == "replied", "the row was the thread"
    assert (await db.inbox_get_item(other["id"]))["state"] == "open"


async def test_two_proposals_for_two_messages_of_one_mail_conversation_are_two_items(env):
    from core.inbox import submit
    from core.inbox.models import ThreadRef
    fake, _http, _ = env
    fake.thread_messages = [fx.msg("q1", direct=False), fx.msg("mine", author="U_ME", direct=False)]

    def req(mid: str) -> submit.SubmitRequest:
        return submit.SubmitRequest(source_id="s1", producer="p", draft="d", title="t", text="x", ask="a",
                                    thread_ref=ThreadRef("fake", {"conversation_id": "c1", "message_id": mid}))

    a = await submit.submit(req("m1"))
    b = await submit.submit(req("m2"))
    assert a["created"] and b["created"] and a["item_id"] != b["item_id"]
