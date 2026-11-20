"""The Inbox send path through its routes (design §8, §11 #1-4, #14).

The caller check is allowed here (``caller.check_caller`` patched) except in the tests
that pin its use by the routes; ``test_inbox_caller.py`` tests the check itself.

- The recipients come only from the thread fetched fresh; a target_sha that differs is
  409 target_changed and nothing is sent; a recipients field in the request is ignored.
- Two sends of one item (double click, or edited text) call reply once.
- unknown is never resent; the person's "not sent" makes it failed, and only then may it
  be sent again; SendFailed -> failed -> resend; a hang -> unknown.
- Redaction changing the text is 409 redacted with spans, without the secret; nothing sent.
- Not open -> 409 not_open. No X-Swarm-User-Action, or a caller in the backend's tree ->
  403 before the source is touched.
"""
from __future__ import annotations

import asyncio
import os

import pytest

from core.inbox import caller, send
from core.inbox.models import ReplyTarget
from core.inbox.sources.base import SendFailed
from tests import inbox_fakes as fx


@pytest.fixture
async def env(monkeypatch):
    await fx.clear()
    fake = fx.install(fx.FakeSource())
    await fx.add_source()
    item = await fx.add_item()
    monkeypatch.setattr(caller, "check_caller", lambda client, server: 4242)
    async with fx.client() as http:
        yield fake, item, http


async def _sha(http, item_id):
    r = await http.get(f"/api/inbox/items/{item_id}/target")
    assert r.status_code == 200, r.text
    return r.json()["target_sha"]


async def test_target_changed_is_409_and_never_replies(env):
    fake, item, http = env
    sha = await _sha(http, item["id"])
    fake.target = ReplyTarget(label="Priya", recipients=("billing@other.example",))
    r = await http.post(f"/api/inbox/items/{item['id']}/send", headers=fx.UA,
                        json={"text": "ok", "target_sha": sha})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "target_changed"
    assert r.json()["detail"]["target"]["recipients"] == ["billing@other.example"]
    assert fake.calls["reply"] == 0


async def test_recipients_in_the_request_are_ignored(env):
    from database import db
    fake, item, http = env
    sha = await _sha(http, item["id"])
    r = await http.post(f"/api/inbox/items/{item['id']}/send", headers=fx.UA,
                        json={"text": "ok", "target_sha": sha, "recipients": ["evil@x.example"],
                              "thread_ref": {"source_kind": "fake", "data": {"thread": "t9"}}})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "sent" and r.json()["to_label"] == "Priya"
    sent_ref, _text = fake.replied[0]
    assert sent_ref.data == {"thread": "t1"}, "the reply goes to the item's thread only"
    row = await db.inbox_get_send(r.json()["id"])
    assert "evil" not in row["target"]
    assert (await db.inbox_get_item(item["id"]))["state"] == "replied"


async def test_double_send_calls_reply_once(env):
    fake, item, http = env
    sha = await _sha(http, item["id"])
    gate = asyncio.Event()
    original = fake.reply

    async def slow_reply(ref, text):
        await gate.wait()
        return await original(ref, text)

    fake.reply = slow_reply
    url = f"/api/inbox/items/{item['id']}/send"
    first = asyncio.create_task(http.post(url, headers=fx.UA, json={"text": "ok", "target_sha": sha}))
    await asyncio.sleep(0.2)
    second = await http.post(url, headers=fx.UA, json={"text": "ok, edited", "target_sha": sha})
    assert second.status_code == 200 and second.json()["status"] == "reserved"
    gate.set()
    done = await first
    assert done.json()["status"] == "sent" and done.json()["id"] == second.json()["id"]
    third = await http.post(url, headers=fx.UA, json={"text": "ok", "target_sha": sha})
    assert third.status_code == 409 and third.json()["detail"]["code"] == "not_open"
    assert fake.calls["reply"] == 1


async def test_unknown_is_never_resent_and_find_sent_settles_it(env):
    fake, item, http = env
    sha = await _sha(http, item["id"])
    url = f"/api/inbox/items/{item['id']}/send"
    fake.reply_error = RuntimeError("connection reset")
    r = await http.post(url, headers=fx.UA, json={"text": "ok", "target_sha": sha})
    assert r.json()["status"] == "unknown"
    send_id = r.json()["id"]
    again = await http.post(url, headers=fx.UA, json={"text": "ok", "target_sha": sha})
    assert again.json()["id"] == send_id and again.json()["status"] == "unknown"
    assert fake.calls["reply"] == 1, "an unknown send is never sent again by itself"
    resend = await http.post(f"/api/inbox/sends/{send_id}/resend", headers=fx.UA)
    assert resend.status_code == 409 and resend.json()["detail"]["code"] == "not_failed"
    sends = (await http.get("/api/inbox/sends")).json()
    assert [s["id"] for s in sends["need_you"]] == [send_id]
    # the person confirms it did not go out: failed, and now it may be sent again
    nf = await http.post(f"/api/inbox/sends/{send_id}/not-sent", headers=fx.UA)
    assert nf.json()["status"] == "failed"
    fake.reply_error = None
    r2 = await http.post(url, headers=fx.UA, json={"text": "ok", "target_sha": sha})
    assert r2.json()["status"] == "sent" and fake.calls["reply"] == 2


async def test_send_failed_is_failed_and_resend_sends_once_more(env):
    fake, item, http = env
    sha = await _sha(http, item["id"])
    fake.reply_error = SendFailed("channel archived")
    r = await http.post(f"/api/inbox/items/{item['id']}/send", headers=fx.UA,
                        json={"text": "ok", "target_sha": sha})
    assert r.json()["status"] == "failed" and "channel archived" in r.json()["error"]
    assert r.json()["blocked_by_source"] is False
    fake.reply_error = None
    again = await http.post(f"/api/inbox/sends/{r.json()['id']}/resend", headers=fx.UA)
    assert again.json()["status"] == "sent" and fake.calls["reply"] == 2
    nf = await http.post(f"/api/inbox/sends/{r.json()['id']}/not-sent", headers=fx.UA)
    assert nf.status_code == 409 and nf.json()["detail"]["code"] == "not_unknown"


async def test_send_failed_with_a_fix_marks_the_source_auth_failed(env):
    """A definite not-sent auth failure keeps its fix and marks the source, like a pull."""
    from database import db
    fake, item, http = env
    sha = await _sha(http, item["id"])
    fake.reply_error = SendFailed("token revoked", fix="paste a new token, then retry")
    r = await http.post(f"/api/inbox/items/{item['id']}/send", headers=fx.UA,
                        json={"text": "ok", "target_sha": sha})
    assert r.json()["status"] == "failed" and "token revoked" in r.json()["error"]
    assert r.json()["blocked_by_source"] is True, "resend stays off until the source works"
    source = await db.inbox_get_source("s1")
    assert source["health"] == "auth_failed" and source["fix"] == "paste a new token, then retry"
    counts = (await http.get("/api/inbox/counts")).json()
    assert counts["unhealthy_sources"] == ["s1"]
    again = await http.post(f"/api/inbox/sends/{r.json()['id']}/resend", headers=fx.UA)
    assert again.status_code == 409 and again.json()["detail"]["code"] == "cannot_send"
    assert fake.calls["reply"] == 1


async def test_resend_of_a_source_removed_meanwhile_is_refused_not_a_crash(env, monkeypatch):
    from core.inbox import store
    from database import db
    fake, item, http = env
    sha = await _sha(http, item["id"])
    fake.reply_error = SendFailed("nope")
    r = await http.post(f"/api/inbox/items/{item['id']}/send", headers=fx.UA,
                        json={"text": "ok", "target_sha": sha})
    fake.reply_error = None
    real_can_send = fake.can_send

    async def removed_during_checks():
        ok = await real_can_send()
        await store.delete_source_rows("s1")
        return ok

    monkeypatch.setattr(fake, "can_send", removed_during_checks)
    again = await http.post(f"/api/inbox/sends/{r.json()['id']}/resend", headers=fx.UA)
    assert again.status_code == 409 and again.json()["detail"]["code"] == "source_removed"
    assert fake.calls["reply"] == 1 and await db.inbox_get_send(r.json()["id"]) is None


async def test_resend_refuses_a_source_that_cannot_send(env):
    fake, item, http = env
    sha = await _sha(http, item["id"])
    fake.reply_error = SendFailed("nope")
    r = await http.post(f"/api/inbox/items/{item['id']}/send", headers=fx.UA,
                        json={"text": "ok", "target_sha": sha})
    fake.sendable = False
    again = await http.post(f"/api/inbox/sends/{r.json()['id']}/resend", headers=fx.UA)
    assert again.status_code == 409 and again.json()["detail"]["code"] == "cannot_send"
    assert fake.calls["reply"] == 1


async def test_a_reply_that_hangs_is_unknown(env, monkeypatch):
    fake, item, http = env
    sha = await _sha(http, item["id"])
    monkeypatch.setattr(send, "REPLY_TIMEOUT_S", 0.1)

    async def hang(ref, text):
        await asyncio.sleep(30)

    fake.reply = hang
    r = await http.post(f"/api/inbox/items/{item['id']}/send", headers=fx.UA,
                        json={"text": "ok", "target_sha": sha})
    assert r.json()["status"] == "unknown"


async def test_redaction_change_is_409_with_spans(env):
    fake, item, http = env
    sha = await _sha(http, item["id"])
    key_like = "AK" + "IA" + "IOSFODNN7EXAMPLE"   # built here: no literal in the source
    text = f"here is the key {key_like} thanks"
    r = await http.post(f"/api/inbox/items/{item['id']}/send", headers=fx.UA,
                        json={"text": text, "target_sha": sha})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "redacted"
    spans = r.json()["detail"]["spans"]
    assert [text[s["start"]:s["end"]] for s in spans] == [key_like], spans
    assert key_like not in r.text, "the credential is never echoed back"
    assert fake.calls["reply"] == 0


async def test_not_open_is_409(env):
    from database import db
    fake, item, http = env
    sha = await _sha(http, item["id"])
    await db.inbox_update_item(item["id"], "2026-11-20T00:00:00.000000+00:00", state="done")
    r = await http.post(f"/api/inbox/items/{item['id']}/send", headers=fx.UA,
                        json={"text": "ok", "target_sha": sha})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "not_open"
    assert fake.calls["reply"] == 0


async def test_send_needs_the_user_action_header(env):
    fake, item, http = env
    sha = await _sha(http, item["id"])
    for path in (f"/api/inbox/items/{item['id']}/send", "/api/inbox/sends/x/not-sent",
                 "/api/inbox/sends/x/resend"):
        r = await http.post(path, json={"text": "ok", "target_sha": sha})
        assert r.status_code == 403, path
    assert fake.calls["reply"] == 0 and fake.calls["thread"] == 1


@pytest.mark.parametrize("peer", ["self", "none"])
async def test_a_caller_in_the_backend_tree_is_403(env, monkeypatch, peer):
    """The routes use the real check: a peer that is this process, or none, is refused."""
    fake, item, http = env
    sha = await _sha(http, item["id"])
    monkeypatch.undo()
    fx.install(fake)
    if peer == "self":
        monkeypatch.setattr(caller, "find_peer_pid", lambda client, server: os.getpid())
    for path in (f"/api/inbox/items/{item['id']}/send", "/api/inbox/sends/x/not-sent",
                 "/api/inbox/sends/x/resend"):
        r = await http.post(path, headers=fx.UA, json={"text": "ok", "target_sha": sha})
        assert r.status_code == 403, (path, r.text)
    assert fake.calls["reply"] == 0 and fake.calls["thread"] == 1


async def test_a_failed_send_answered_later_no_longer_needs_the_person(env):
    fake, item, http = env
    sha = await _sha(http, item["id"])
    url = f"/api/inbox/items/{item['id']}/send"
    fake.reply_error = SendFailed("rate limited")
    failed = (await http.post(url, headers=fx.UA, json={"text": "ok", "target_sha": sha})).json()
    assert failed["status"] == "failed"
    assert (await http.get("/api/inbox/counts")).json()["sends_need_you"] == 1
    fake.reply_error = None
    sent = (await http.post(url, headers=fx.UA, json={"text": "ok", "target_sha": sha})).json()
    assert sent["status"] == "sent" and sent["id"] != failed["id"]
    assert (await http.get("/api/inbox/counts")).json()["sends_need_you"] == 0
    assert (await http.get("/api/inbox/sends")).json()["need_you"] == []
