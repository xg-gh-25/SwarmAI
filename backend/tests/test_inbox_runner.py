"""Inbox runner (core/inbox/runner.py), with a fake source and fake triage.

- Runs in daemon and subprocess mode by the backend that owns backend.json; never on a
  Hive or in dev (design §11 #12), and main.py starts it through that predicate.
- The same message pulled twice is one item, and is triaged once (§11 #10).
- The person's own message is never stored and closes the open items of its thread
  received at or before it (§11 #10).
- Triage gets the item ids as candidate ids; its lane is applied but never below the rule
  floor and never PROPOSED; a rejected batch is offered again at the next pull.
- SourceAuthError: health auth_failed with the fix, no retry before the interval (§11 #11).
- Pulls only inside the window, only due sources.
- Unknown sends: find_sent on each pull; a hit records sent, a miss never resends (§11 #4);
  at startup reserved sends become unknown.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from core.inbox import runner, send, store, triage
from core.inbox.models import Lane, SendReceipt, TriageResult
from core.inbox.sources.base import SourceAuthError
from tests import inbox_fakes as fx


@pytest.fixture
async def env(monkeypatch):
    await fx.clear()
    fake = fx.install(fx.FakeSource())
    await fx.add_source()
    calls: list[list[str]] = []

    def rule_lane(msg, me, category):
        # no own-message check here: the runner's own Identity.is_me check is under test
        if msg.is_direct:
            return triage.RuleVerdict(Lane.REPLY, Lane.AWARE)
        return triage.RuleVerdict(Lane.AWARE, None)

    answers: dict = {}

    async def fake_triage(candidates, context):
        calls.append([c.id for c in candidates])
        if answers.get("raise"):
            raise triage.TriageRejected("bad batch")
        return [TriageResult(id=c.id, lane=answers.get("lane", c.rule_lane), priority=1,
                             reason="reply to priya", reply_expected=True) for c in candidates]

    monkeypatch.setattr(triage, "rule_lane", rule_lane)
    monkeypatch.setattr(triage, "triage", fake_triage)
    monkeypatch.setattr(runner, "pull_window", lambda: (runner.dtime(0, 0), runner.dtime(23, 59, 59)))
    yield fake, calls, answers


async def _source():
    from database import db
    return await db.inbox_get_source("s1")


@pytest.mark.parametrize("mode,owner,expected", [
    ("daemon", True, True), ("subprocess", True, True), ("hive", True, False),
    ("dev", True, False), ("daemon", False, False), ("subprocess", False, False)])
def test_runner_runs_in_subprocess_not_hive(mode, owner, expected):
    assert runner.should_run(mode, owner) is expected


def test_main_starts_the_runner_through_should_run():
    src = (Path(__file__).resolve().parents[1] / "main.py").read_text(encoding="utf-8")
    assert "_inbox_runner.should_run(backend_mode, _bjson_written)" in src
    start = src.index("_inbox_runner_on = _inbox_runner.should_run(")
    assert "if _inbox_runner_on:\n        _inbox_runner.start()" in src[start:start + 300]


async def test_same_message_pulled_twice_is_one_item(env):
    from database import db
    fake, calls, _ = env
    fake.messages = [fx.msg("e1")]
    await runner.pull_source(await _source())
    await runner.pull_source(await _source())
    items = await db.inbox_list_items("reply")
    assert len(items) == 1
    assert calls == [[items[0]["id"]]], "an item already triaged is not triaged again"
    assert items[0]["reason"] == "reply to priya" and items[0]["priority"] == 1
    assert (await _source())["cursor"] == "c1"


async def test_own_message_closes_thread_and_is_not_stored(env):
    from database import db
    fake, _, _ = env
    fake.messages = [fx.msg("e1", thread=1, minutes=0), fx.msg("e2", thread=2, minutes=1)]
    await runner.pull_source(await _source())
    # the person answered in thread 1 (minute 5); Priya wrote again in thread 1 at minute 10
    fake.messages = [fx.msg("mine", thread=1, author="me@example.com", minutes=5),
                     fx.msg("e3", thread=1, minutes=10)]
    await runner.pull_source(await _source())
    by_ext = {r["external_id"]: r for r in await db.inbox_list_items("reply")}
    assert set(by_ext) == {"e2", "e3"}, "thread 1's earlier item is replied, the later one stays"
    assert (await db.inbox_get_item_by_external("s1", "e1"))["state"] == "replied"
    assert await db.inbox_get_item_by_external("s1", "mine") is None, "own messages are not items"


async def test_triage_answer_never_goes_below_the_rule_floor(env):
    from database import db
    fake, calls, answers = env
    answers["lane"] = Lane.SKIP
    fake.messages = [fx.msg("dm", direct=True), fx.msg("bot", thread=2, direct=False)]
    await runner.pull_source(await _source())
    dm = await db.inbox_get_item_by_external("s1", "dm")
    bot = await db.inbox_get_item_by_external("s1", "bot")
    assert dm["lane"] == "aware", "a direct message may drop to AWARE, never to SKIP"
    assert bot["lane"] == "skip"
    assert sorted(calls[0]) == sorted([dm["id"], bot["id"]]), "candidate ids are the item ids"


async def test_triage_cannot_make_proposed(env):
    from database import db
    fake, _, answers = env
    answers["lane"] = Lane.PROPOSED
    fake.messages = [fx.msg("e1"), fx.msg("e2", thread=2, direct=False)]
    assert await runner.pull_source(await _source()) == "ok"
    assert (await db.inbox_get_item_by_external("s1", "e1"))["lane"] == "reply"
    assert (await db.inbox_get_item_by_external("s1", "e2"))["lane"] == "aware", \
        "without a floor the rule lane is kept: only inbox submit makes PROPOSED"


async def test_rejected_batch_is_offered_again_next_pull(env):
    from database import db
    fake, calls, answers = env
    answers["raise"] = True
    fake.messages = [fx.msg("e1")]
    await runner.pull_source(await _source())
    item = await db.inbox_get_item_by_external("s1", "e1")
    assert item["triaged_at"] is None and item["lane"] == "reply", "kept with its rule lane"
    answers["raise"] = False
    fake.messages = []
    await runner.pull_source(await _source())
    assert calls[-1] == [item["id"]]
    assert (await db.inbox_get_item(item["id"]))["triaged_at"] is not None


async def test_auth_failure_marks_source_and_waits_for_interval(env):
    fake, _, _ = env
    fake.pull_error = SourceAuthError("token expired", fix="gh auth login")
    assert await runner.run_cycle() == 1
    src = await _source()
    assert src["health"] == "auth_failed" and src["fix"] == "gh auth login"
    assert await runner.run_cycle() == 0 and fake.calls["pull"] == 1, "no retry within the interval"
    later = store.now() + timedelta(seconds=901)
    assert await runner.run_cycle(at=later) == 1 and fake.calls["pull"] == 2


async def test_other_error_marks_error_and_hides_secrets(env):
    fake, _, _ = env
    token_like = "gh" + "p_" + "abcdefghijklmnopqrstuvwxyz0123456789"   # built here: no literal in the source
    fake.pull_error = RuntimeError(f"failed with token {token_like}")
    await runner.pull_source(await _source())
    src = await _source()
    assert src["health"] == "error"
    assert token_like not in src["last_error"]


async def test_pulls_only_inside_the_window_and_only_due_sources(env, monkeypatch):
    fake, _, _ = env
    monkeypatch.setattr(runner, "pull_window", lambda: (runner.dtime(7, 0), runner.dtime(22, 0)))
    night = datetime.now().astimezone().replace(hour=23, minute=0)
    assert await runner.run_cycle(at=night) == 0 and fake.calls["pull"] == 0
    day = datetime.now().astimezone().replace(hour=10, minute=0)
    assert await runner.run_cycle(at=day) == 1
    assert await runner.run_cycle(at=day + timedelta(minutes=10)) == 0, "not due before 900 s"
    rows = [await _source()]
    delay = runner.next_delay(rows, day + timedelta(minutes=10), runner.pull_window())
    assert 0 < delay <= 900
    assert runner.next_delay(rows, night, runner.pull_window()) >= 3600, "sleeps until morning"


def test_window_setting_is_parsed_and_bad_values_fall_back():
    assert runner._parse_window("08:30-20:00") == (runner.dtime(8, 30), runner.dtime(20, 0))
    assert runner._parse_window("nonsense") == runner.DEFAULT_WINDOW
    assert runner._parse_window("22:00-07:00") == runner.DEFAULT_WINDOW


async def _unknown_send(item):
    from database import db
    stamp = store.ts(store.now())
    row = {"id": store.new_id("snd"), "item_id": item["id"], "source_id": "s1", "draft_id": None,
           "origin": "click", "target": "{}", "target_sha": "x", "text": "on it", "status": "unknown",
           "external_msg_id": None, "url": "", "error": "", "created_at": stamp, "sent_at": None,
           "updated_at": stamp}
    assert await db.inbox_insert_send(row)
    return row


async def test_unknown_send_is_settled_only_by_find_sent(env):
    from database import db
    fake, _, _ = env
    item = await fx.add_item()
    row = await _unknown_send(item)
    await runner.pull_source(await _source())
    assert fake.calls["find_sent"] == 1 and fake.calls["reply"] == 0, "never resent"
    assert (await db.inbox_get_send(row["id"]))["status"] == "unknown"
    fake.found = SendReceipt(external_msg_id="m9", url="https://x.example/m9")
    await runner.pull_source(await _source())
    assert (await db.inbox_get_send(row["id"]))["status"] == "sent"
    assert (await db.inbox_get_item(item["id"]))["state"] == "replied"
    assert fake.calls["reply"] == 0


async def test_startup_turns_reserved_into_unknown(env, monkeypatch):
    from database import db
    item = await fx.add_item()
    row = await _unknown_send(item)
    assert await db.inbox_update_send(row["id"], store.ts(store.now()), expect_status="unknown",
                                      status="failed")
    stamp = store.ts(store.now())
    reserved = {**row, "id": store.new_id("snd"), "status": "reserved", "created_at": stamp}
    assert await db.inbox_insert_send(reserved)
    cycled = asyncio.Event()

    async def one_cycle(at=None):
        cycled.set()
        return 0

    monkeypatch.setattr(runner, "run_cycle", one_cycle)
    runner.start()
    await asyncio.wait_for(cycled.wait(), 5)
    await runner.stop()
    assert (await db.inbox_get_send(reserved["id"]))["status"] == "unknown"
    assert (await db.inbox_get_send(row["id"]))["status"] == "failed", "only reserved rows move"
    assert await send.recover_reserved() == 0


async def test_item_raised_to_reply_is_closed_by_a_later_own_message(env):
    """An AWARE-by-rule item the answer raises to REPLY, answered by the person later in
    the same pull, ends replied."""
    from database import db
    fake, _, answers = env
    answers["lane"] = Lane.REPLY
    fake.messages = [fx.msg("q", direct=False, minutes=0),
                     fx.msg("mine", author="U_ME", minutes=5)]
    await runner.pull_source(await _source())
    item = await db.inbox_get_item_by_external("s1", "q")
    assert item["lane"] == "reply" and item["state"] == "replied"


async def test_untriaged_item_is_closed_by_an_own_message(env):
    """A batch was rejected; the person then answered in the thread: the item is replied
    and is not triaged into REPLY afterwards."""
    from database import db
    fake, calls, answers = env
    answers["raise"] = True
    fake.messages = [fx.msg("q", direct=False, minutes=0)]
    await runner.pull_source(await _source())
    answers["raise"] = False
    answers["lane"] = Lane.REPLY
    fake.messages = [fx.msg("mine", author="U_ME", minutes=5)]
    await runner.pull_source(await _source())
    assert (await db.inbox_get_item_by_external("s1", "q"))["state"] == "replied"
    assert await db.inbox_list_items("reply") == []


async def test_runner_keeps_running_when_recovery_fails(env, monkeypatch):
    cycled = asyncio.Event()

    async def broken():
        raise RuntimeError("database is locked")

    async def one_cycle(at=None):
        cycled.set()
        return 0

    monkeypatch.setattr(send, "recover_reserved", broken)
    monkeypatch.setattr(runner, "run_cycle", one_cycle)
    runner.start()
    await asyncio.wait_for(cycled.wait(), 5)
    await runner.stop()


async def test_one_trend_summary_per_day_and_unique_links(env, monkeypatch):
    from core.inbox import trends
    from core.inbox.models import Lane as L
    from database import db
    await fx.add_source("s2")
    for sid in ("s1", "s2"):
        row = store.message_to_item_row(sid, fx.msg("same", thread=5), L.TREND, None)
        row["received_at"] = store.ts(store.now())
        await db.inbox_insert_items([row])
    row = store.message_to_item_row("s1", fx.msg("only", thread=6), L.TREND, None)
    row["received_at"] = store.ts(store.now())
    await db.inbox_insert_items([row])
    calls = []

    async def summarize(items, profile):
        calls.append(len(items))
        await asyncio.sleep(0.2)
        return [trends.TrendPointData("A", "why a", "HN", external_id="same"),
                trends.TrendPointData("B", "why b", "HN", external_id="only")]

    monkeypatch.setattr(trends, "summarize", summarize)
    monkeypatch.setattr(runner, "TREND_SUMMARY_HOUR", 0)
    first = runner.kick_summary()
    assert runner.kick_summary() is None, "a second kick while one runs starts nothing"
    await first
    assert runner.kick_summary() is not None
    await runner._loop_state["summary"]
    assert len(calls) == 1, "one summary a day, even when a refresh races a cycle"
    import json as _json
    points = _json.loads((await db.inbox_latest_trend_summary())["points"])
    only = (await db.inbox_get_item_by_external("s1", "only"))["id"]
    assert [p["item_id"] for p in points] == [None, only], "an id two sources share links nowhere"


async def test_a_slow_trend_summary_never_holds_a_pull(env, monkeypatch):
    from core.inbox import trends
    from core.inbox.models import Lane as L
    from database import db
    row = store.message_to_item_row("s1", fx.msg("t", thread=9), L.TREND, None)
    row["received_at"] = store.ts(store.now())
    await db.inbox_insert_items([row])
    started = asyncio.Event()

    async def hang(items, profile):
        started.set()
        await asyncio.sleep(30)
        return []

    monkeypatch.setattr(trends, "summarize", hang)
    monkeypatch.setattr(runner, "TREND_SUMMARY_HOUR", 0)
    task = runner.kick_summary()
    await asyncio.wait_for(started.wait(), 5)
    await asyncio.wait_for(runner.pull_now(["s1"]), 5)   # not blocked by the summary
    await runner.stop()                                  # and stop() ends it
    assert task.cancelled()
    assert await db.inbox_latest_trend_summary() is None


async def test_a_summary_that_never_answers_is_bounded(env, monkeypatch):
    from core.inbox import trends
    from core.inbox.models import Lane as L
    from database import db
    row = store.message_to_item_row("s1", fx.msg("t", thread=9), L.TREND, None)
    row["received_at"] = store.ts(store.now())
    await db.inbox_insert_items([row])

    async def hang(items, profile):
        await asyncio.sleep(30)

    monkeypatch.setattr(trends, "summarize", hang)
    monkeypatch.setattr(runner, "TREND_SUMMARY_HOUR", 0)
    monkeypatch.setattr(runner, "SUMMARY_TIMEOUT_S", 0.1)
    await asyncio.wait_for(runner.kick_summary(), 5)
    assert runner.kick_summary() is not None, "a timed-out summary does not block the next"
    await runner.stop()


async def test_an_unanswered_item_triaged_to_reply_stays_open(env):
    from database import db
    fake, _, answers = env
    answers["lane"] = Lane.REPLY
    fake.messages = [fx.msg("q", direct=False, minutes=0)]
    await runner.pull_source(await _source())
    item = await db.inbox_get_item_by_external("s1", "q")
    assert (item["lane"], item["state"], item["answered_at"]) == ("reply", "open", None)


async def test_an_aware_item_stays_aware_after_an_own_message(env):
    """An item the answer keeps in AWARE is not labelled replied by the person writing
    later in its thread: it stays open in AWARE."""
    from database import db
    fake, _, answers = env
    answers["lane"] = Lane.AWARE
    fake.messages = [fx.msg("fyi", direct=False, minutes=0), fx.msg("mine", author="U_ME", minutes=5)]
    await runner.pull_source(await _source())
    item = await db.inbox_get_item_by_external("s1", "fyi")
    assert (item["lane"], item["state"]) == ("aware", "open")


async def test_item_answered_while_its_batch_was_rejected_stays_replied(env):
    """The pull with the person's answer has its triage batch rejected; the next pull's
    answer raises the item to REPLY: it stays replied."""
    from database import db
    fake, _, answers = env
    answers["raise"] = True
    fake.messages = [fx.msg("q", direct=False, minutes=0), fx.msg("mine", author="U_ME", minutes=5)]
    await runner.pull_source(await _source())
    answers["raise"] = False
    answers["lane"] = Lane.REPLY
    fake.messages = []
    await runner.pull_source(await _source())
    assert (await db.inbox_get_item_by_external("s1", "q"))["state"] == "replied"
    assert await db.inbox_list_items("reply") == []


async def test_a_new_loop_does_not_inherit_a_summary(env):
    """A summary task of an earlier event loop (a test's, or a restarted loop) never
    blocks the summary of the current one."""
    stale = asyncio.get_running_loop().create_future()
    runner._loop_state.update(loop=object(), summary=stale)
    task = runner.kick_summary()
    assert task is not None
    await task
    stale.cancel()
