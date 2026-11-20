"""The /api/inbox routes (routers/inbox.py) against the contract in schemas/inbox.py.

- Every route the contract lists exists with its method (read from the contract's own
  route list, so a route dropped from the router turns this red).
- Each read returns its model's shape; the draft is made on first read (once) and
  redraft adds one; later / dismiss / done move the item.
- Feed configuration under /api/inbox/feeds behaves exactly as /api/community/feeds:
  the same requests give the same status codes and the same config.yaml.
- The draft's history holds the person's own messages only: the thread's and, when the
  source offers them, those to the same counterparty; another person's never.
- The 403/409 of the sending routes are SendRefusalOut bodies.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from core.inbox import caller, drafts, runner
from core.inbox.models import Lane
from routers.inbox import router
from schemas import inbox as contract
from tests import inbox_fakes as fx


def _contract_routes() -> set[tuple[str, str]]:
    doc = contract.__doc__
    routes = set()
    for method, path in re.findall(r"``(GET|POST|PUT|PATCH|DELETE)\s+(/api/inbox/[^`?\s]+)", doc):
        routes.add((method, path))
    feeds = re.search(r"``GET/POST /api/inbox/feeds``, ``PUT/DELETE /api/inbox/feeds/\{id\}``,"
                      r"\s+``POST/DELETE /api/inbox/feeds/\{id\}/members``", doc)
    assert feeds, "the contract's feed route line changed: update this test"
    routes |= {("GET", "/api/inbox/feeds"), ("POST", "/api/inbox/feeds"),
               ("PUT", "/api/inbox/feeds/{id}"), ("DELETE", "/api/inbox/feeds/{id}"),
               ("POST", "/api/inbox/feeds/{id}/members"), ("DELETE", "/api/inbox/feeds/{id}/members")}
    return routes


def _norm(path: str) -> str:
    return re.sub(r"\{[^}]+\}", "{id}", path)


def test_every_contract_route_exists():
    served = {(m, _norm(r.path)) for r in router.routes for m in r.methods}
    expected = {(m, _norm(p)) for m, p in _contract_routes()}
    assert len(expected) >= 25
    assert {("PATCH", "/api/inbox/sources/{id}"), ("DELETE", "/api/inbox/sources/{id}"),
            ("POST", "/api/inbox/sources"), ("GET", "/api/inbox/sources/available"),
            ("POST", "/api/inbox/sources/{id}/secret")} <= expected
    assert expected - served == set()


def test_routes_return_the_contract_models():
    by_route = {(m, _norm(r.path)): r.response_model for r in router.routes for m in r.methods}
    assert by_route[("GET", "/api/inbox/counts")] is contract.InboxCounts
    assert by_route[("GET", "/api/inbox/items")] == list[contract.ItemOut]
    assert by_route[("GET", "/api/inbox/items/{id}/target")] is contract.TargetOut
    assert by_route[("GET", "/api/inbox/items/{id}/draft")] is contract.DraftOut
    assert by_route[("POST", "/api/inbox/items/{id}/redraft")] is contract.DraftOut
    assert by_route[("POST", "/api/inbox/items/{id}/send")] is contract.SendOut
    assert by_route[("POST", "/api/inbox/items/{id}/action")] is contract.ItemOut
    assert by_route[("GET", "/api/inbox/sends")] is contract.SendsOut
    assert by_route[("POST", "/api/inbox/sends/{id}/not-sent")] is contract.SendOut
    assert by_route[("POST", "/api/inbox/sends/{id}/resend")] is contract.SendOut
    assert by_route[("GET", "/api/inbox/trends/summary")] is contract.TrendSummaryOut
    assert by_route[("GET", "/api/inbox/sources")] == list[contract.SourceOut]
    assert by_route[("POST", "/api/inbox/sources/{id}/retry")] is contract.SourceOut
    assert by_route[("POST", "/api/inbox/refresh")] is contract.InboxCounts
    assert by_route[("GET", "/api/inbox/feeds")] is contract.FeedsOut
    assert by_route[("GET", "/api/inbox/sources/available")] == list[contract.AvailableSourceOut]
    assert by_route[("POST", "/api/inbox/sources")] is contract.SourceOut
    assert by_route[("PATCH", "/api/inbox/sources/{id}")] is contract.SourceOut
    assert by_route[("DELETE", "/api/inbox/sources/{id}")] is contract.SourceDeletedOut
    assert by_route[("POST", "/api/inbox/sources/{id}/secret")] is contract.SourceOut
    declared = {(m, _norm(r.path)): r.responses for r in router.routes for m in r.methods}
    for path in ("/api/inbox/items/{id}/send", "/api/inbox/sends/{id}/not-sent", "/api/inbox/sends/{id}/resend"):
        assert declared[("POST", path)][403]["model"] is contract.SendRefusalBody
        assert declared[("POST", path)][409]["model"] is contract.SendRefusalBody


@pytest.fixture
async def env(monkeypatch, tmp_path):
    await fx.clear()
    fake = fx.install(fx.FakeSource())
    await fx.add_source(config={"watched_repos": ["o/a", "o/b"]})
    cfg = tmp_path / "config.yaml"
    cfg.write_text("feeds: []\n")
    import jobs.config_io
    import jobs.paths
    monkeypatch.setattr(jobs.config_io, "CONFIG_FILE", cfg)
    monkeypatch.setattr(jobs.paths, "CONFIG_FILE", cfg)
    monkeypatch.setattr(caller, "check_caller", lambda client, server: 4242)
    made = []

    async def make_draft(thread, me, history):
        made.append((thread.ref, [m.external_id for m in history]))
        return drafts.DraftText(text=f"draft {len(made)}", lang="en", note="from the thread")

    monkeypatch.setattr(drafts, "make_draft", make_draft)
    async with fx.client() as http:
        yield fake, http, made, cfg


async def test_reads_return_their_shapes(env):
    fake, http, _made, _cfg = env
    item = await fx.add_item(lane="reply")
    await fx.add_item(external_id="e2", lane="aware", thread=2)
    counts = contract.InboxCounts(**(await http.get("/api/inbox/counts")).json())
    assert (counts.reply, counts.aware, counts.proposed) == (1, 1, 0)
    items = [contract.ItemOut(**x) for x in (await http.get("/api/inbox/items?lane=reply")).json()]
    assert [i.id for i in items] == [item["id"]]
    assert items[0].channel_label == "fake" and items[0].where == "fake 私信"
    assert (await http.get("/api/inbox/items?lane=skip")).status_code == 422
    target = contract.TargetOut(**(await http.get(f"/api/inbox/items/{item['id']}/target")).json())
    assert target.recipients == ["priya@example.com"] and target.can_send
    sends = contract.SendsOut(**(await http.get("/api/inbox/sends")).json())
    assert sends.need_you == [] and sends.sent == []
    sources = [contract.SourceOut(**s) for s in (await http.get("/api/inbox/sources")).json()]
    assert [(s.id, s.health, s.identity, s.detail) for s in sources] == [("s1", "ok", "@me", "关注 2 个仓库")]
    summary = contract.TrendSummaryOut(**(await http.get("/api/inbox/trends/summary")).json())
    assert summary.points == [] and summary.watched_repos == 2 and summary.feeds == 0
    assert (await http.get("/api/inbox/items/nope/target")).status_code == 404


async def test_draft_is_made_on_first_read_only(env):
    fake, http, made, _cfg = env
    item = await fx.add_item()
    fake.thread_messages = [fx.msg("e1"), fx.msg("mine", author="U_ME", minutes=3)]
    one = contract.DraftOut(**(await http.get(f"/api/inbox/items/{item['id']}/draft")).json())
    two = contract.DraftOut(**(await http.get(f"/api/inbox/items/{item['id']}/draft")).json())
    assert one.id == two.id and one.text == "draft 1" and len(made) == 1
    assert made[0][1] == ["mine"], "history is the person's own messages of this thread"
    assert (await http.post(f"/api/inbox/items/{item['id']}/redraft")).status_code == 403
    three = await http.post(f"/api/inbox/items/{item['id']}/redraft", headers=fx.UA)
    assert three.json()["text"] == "draft 2"
    latest = (await http.get(f"/api/inbox/items/{item['id']}/draft")).json()
    assert latest["id"] == three.json()["id"]


async def test_draft_history_is_own_messages_to_the_counterparty(env):
    fake, http, made, _cfg = env
    item = await fx.add_item()
    fake.thread_messages = [fx.msg("e1"), fx.msg("mine", author="U_ME", minutes=3),
                            fx.msg("e2", author="U_PRIYA", minutes=4)]
    asked = []

    async def own_messages_to(thread, limit):
        asked.append(([m.external_id for m in thread.messages], limit))
        # a source that also returns someone else's message must not leak it into the draft
        return [fx.msg("dm-mine", author="U_ME", thread=7, minutes=-60),
                fx.msg("dm-theirs", author="U_PRIYA", thread=7, minutes=-61),
                fx.msg("dm-third", author="U_EVE", thread=7, minutes=-62)]

    fake.own_messages_to = own_messages_to
    r = await http.get(f"/api/inbox/items/{item['id']}/draft")
    assert r.status_code == 200, r.text
    assert asked == [(["e1", "mine", "e2"], drafts.HISTORY_MESSAGES)], "the source is asked with the thread itself"
    assert sorted(made[0][1]) == ["dm-mine", "mine"], "only the person's own words reach the draft"


async def test_draft_without_counterparty_history_still_drafts_when_it_fails(env):
    fake, http, made, _cfg = env
    item = await fx.add_item()
    fake.thread_messages = [fx.msg("e1"), fx.msg("mine", author="U_ME", minutes=3)]

    async def broken(thread, limit):
        raise RuntimeError("search is down")

    fake.own_messages_to = broken
    assert (await http.get(f"/api/inbox/items/{item['id']}/draft")).status_code == 200
    assert made[0][1] == ["mine"]


async def test_send_refusals_are_send_refusal_out_bodies(env, monkeypatch):
    fake, http, _made, _cfg = env
    item = await fx.add_item()
    sha = (await http.get(f"/api/inbox/items/{item['id']}/target")).json()["target_sha"]
    no_header = await http.post(f"/api/inbox/items/{item['id']}/send", json={"text": "x", "target_sha": sha})
    assert no_header.status_code == 403
    assert contract.SendRefusalBody(**no_header.json()).detail.code == "forbidden"
    fake.target = fx.ReplyTarget(label="Other", recipients=("other@example.com",))
    changed = await http.post(f"/api/inbox/items/{item['id']}/send", headers=fx.UA,
                              json={"text": "x", "target_sha": sha})
    body = contract.SendRefusalBody(**changed.json()).detail
    assert changed.status_code == 409 and body.code == "target_changed"
    assert body.target is not None and body.target.recipients == ["other@example.com"]
    monkeypatch.setattr(caller, "check_caller", _refuse)
    refused = await http.post(f"/api/inbox/items/{item['id']}/send", headers=fx.UA,
                              json={"text": "x", "target_sha": sha})
    detail = contract.SendRefusalBody(**refused.json()).detail
    assert refused.status_code == 403 and detail.code == "forbidden" and detail.reason == "in the backend tree"


def _refuse(client, server):
    raise caller.CallerRefused("in the backend tree")


async def test_item_actions(env):
    from database import db
    fake, http, _made, _cfg = env
    a = await fx.add_item("s1", "a")
    b = await fx.add_item("s1", "b", thread=2)
    c = await fx.add_item("s1", "c", thread=3)
    assert (await http.post(f"/api/inbox/items/{a['id']}/action", json={"action": "dismiss"})).status_code == 403
    r = await http.post(f"/api/inbox/items/{a['id']}/action", json={"action": "later"}, headers=fx.UA)
    assert r.json()["state"] == "snoozed"
    assert (await http.post(f"/api/inbox/items/{b['id']}/action", json={"action": "dismiss"},
                            headers=fx.UA)).json()["state"] == "dismissed"
    assert (await http.post(f"/api/inbox/items/{c['id']}/action", json={"action": "done"},
                            headers=fx.UA)).json()["state"] == "done"
    assert (await http.post(f"/api/inbox/items/{c['id']}/action", json={"action": "done"},
                            headers=fx.UA)).status_code == 409
    fb = await db.inbox_recent_feedback("s1")
    assert [f["action"] for f in fb] == ["dismiss"]
    assert (await http.get("/api/inbox/counts")).json()["reply"] == 0
    # a snoozed item comes back once its time has passed
    await db.inbox_update_item(a["id"], "2026-01-01T00:00:00.000000+00:00",
                               snooze_until="2026-01-01T00:00:00.000000+00:00")
    assert (await http.get("/api/inbox/counts")).json()["reply"] == 1


async def test_refresh_and_retry_pull_now(env, monkeypatch):
    from core.inbox import triage
    fake, http, _made, _cfg = env
    monkeypatch.setattr(triage, "rule_lane", lambda m, me, c: triage.RuleVerdict(Lane.REPLY, Lane.AWARE))

    async def no_triage(candidates, context):
        raise triage.TriageRejected("later")

    monkeypatch.setattr(triage, "triage", no_triage)
    fake.messages = [fx.msg("e1")]
    assert (await http.post("/api/inbox/refresh")).status_code == 403
    counts = (await http.post("/api/inbox/refresh", headers=fx.UA)).json()
    assert counts["reply"] == 1 and counts["updated_at"]
    retry = (await http.post("/api/inbox/sources/s1/retry", headers=fx.UA)).json()
    assert retry["id"] == "s1" and fake.calls["pull"] == 2
    assert (await http.post("/api/inbox/sources/none/retry", headers=fx.UA)).status_code == 404


_FEED_STEPS = [
    ("post", "/feeds", {"id": "ai", "name": "AI", "type": "rss", "config": {"urls": ["https://a.example/feed"]}}),
    ("post", "/feeds", {"id": "ai", "name": "AI", "type": "rss"}),
    ("post", "/feeds", {"id": "x", "name": "X", "type": "nope"}),
    ("post", "/feeds", {"id": "y", "name": "Y", "type": "rss", "config": {"urls": ["http://10.0.0.1/x"]}}),
    ("put", "/feeds/ai", {"enabled": False}),
    ("put", "/feeds/missing", {"enabled": True}),
    ("put", "/feeds/ai", {"tier": "nope"}),
    ("post", "/feeds/ai/members", {"value": "https://b.example/feed"}),
    ("post", "/feeds/ai/members", {"value": "https://b.example/feed"}),
    ("post", "/feeds/ai/members", {"value": "http://insecure.example/feed"}),
    ("post", "/feeds/ai/members", {"value": "  "}),
    ("delete", "/feeds/ai/members", {"value": "https://a.example/feed"}),
    ("delete", "/feeds/ai/members", {"value": "https://a.example/feed"}),
    ("delete", "/feeds/missing", None),
    ("delete", "/feeds/ai", None),
]


async def _apply(http, prefix: str) -> list[int]:
    codes = []
    for method, path, body in _FEED_STEPS:
        if method == "delete" and body is not None:
            r = await http.request("DELETE", prefix + path, json=body)
        elif body is None:
            r = await getattr(http, method)(prefix + path)
        else:
            r = await getattr(http, method)(prefix + path, json=body)
        codes.append(r.status_code)
        if path == "/feeds/ai/members" and method == "post" and r.status_code == 200:
            listing = "/sources" if prefix.endswith("community") else "/feeds"
            codes.append((await http.get(prefix + listing)).json()["count"])
    return codes


async def test_feed_routes_behave_as_the_community_routes(env, tmp_path, monkeypatch):
    import jobs.config_io
    import jobs.paths
    fake, http, _made, inbox_cfg = env
    inbox_codes = await _apply(http, "/api/inbox")
    inbox_yaml = yaml.safe_load(inbox_cfg.read_text())
    community_cfg = tmp_path / "community.yaml"
    community_cfg.write_text("feeds: []\n")
    monkeypatch.setattr(jobs.config_io, "CONFIG_FILE", community_cfg)
    monkeypatch.setattr(jobs.paths, "CONFIG_FILE", community_cfg)
    community_codes = await _apply(http, "/api/community")
    assert inbox_codes == community_codes
    assert inbox_codes[:4] == [200, 409, 422, 422]
    assert inbox_yaml == yaml.safe_load(community_cfg.read_text())


async def test_feed_list_and_the_feed_source(env):
    from database import db
    fake, http, _made, cfg = env
    assert (await http.post("/api/inbox/feeds",
                            json={"id": "hn", "name": "HN", "type": "hacker-news"})).status_code == 200
    listed = (await http.get("/api/inbox/feeds")).json()
    assert listed["count"] == 1 and listed["sources"][0]["id"] == "hn"
    sources = {s["id"]: s for s in (await http.get("/api/inbox/sources")).json()}
    assert sources["feed"]["detail"] == "1 个 feed" and sources["feed"]["interval_s"] == 21600
    await http.put("/api/inbox/feeds/hn", json={"enabled": False})
    await http.get("/api/inbox/sources")
    assert (await db.inbox_get_source("feed"))["enabled"] == 0, "no enabled feed: the source is off"


def test_router_module_is_served_by_main():
    src = (Path(__file__).resolve().parents[1] / "main.py").read_text(encoding="utf-8")
    assert "from routers.inbox import router as inbox_router" in src
    assert "app.include_router(inbox_router" in src


def test_runner_is_importable_for_refresh():
    assert callable(runner.pull_now)
