"""Adding, configuring and removing Inbox sources (core/inbox/connect.py, /api/inbox/sources).

- The available list offers a configured MCP server only when its real tool list holds a
  shipped mapping's read tools, and gh only when its login works; already-added ones are
  marked; probes are cached.
- A source is stored only after its whoami succeeded; a failure returns the source's
  message and fix, stores nothing, and deletes the secret given with the request.
- A secret is never in a response, a log record or an error message.
- Every source write needs X-Swarm-User-Action and passes the caller check of the send
  routes; listing what can be connected needs the header (it starts processes).
- Concurrent listings share one probe per server; the real layer reading and the real
  stdio probe work against files and a stub server.
- Options never carry a secret, by key or by value, and only an allowlist of them is shown.
- PATCH changes enabled, interval and the editable options of the right kind only.
- DELETE removes the row with its items, drafts, sends, feedback and secrets, never
  another source's; it waits for no send and refuses while one is reserved.
"""
from __future__ import annotations

import logging
from typing import Optional

import pytest

from core.inbox import connect, store
from core.inbox.models import Identity, SourceCategory
from core.inbox.sources import base
from core.mcp_stdio_client import McpProtocolError, McpServerSpec, McpUnavailable
from tests import inbox_fakes as fx

PASSWORD = "hunter2-correct-horse"  # pragma: allowlist secret
TOKEN = "zz-plain-token-77"  # pragma: allowlist secret


class Vault:
    """The OS credential store, in memory."""

    def __init__(self) -> None:
        self.data: dict[str, str] = {}

    def store(self, source_id, name, value):
        self.data[f"{source_id}:{name}"] = value
        return "file"

    def read(self, source_id, name):
        return self.data.get(f"{source_id}:{name}")

    def delete(self, source_id, name):
        self.data.pop(f"{source_id}:{name}", None)


class ConnSource:
    """A conversation source whose whoami needs the stored token (like slack-web)."""

    kind = "conn"
    category = SourceCategory.CONVERSATION
    READ_TOOLS = frozenset({"read"})
    SECRET_NAMES = frozenset({"user_token"})
    behaviour: dict = {}

    def __init__(self, cfg: base.SourceConfig) -> None:
        self.cfg = cfg

    async def whoami(self) -> Identity:
        from core.inbox.sources import credentials
        token = credentials.read_secret(self.cfg.id, "user_token")
        error = self.behaviour.get("error")
        if error is not None:
            raise error(token) if callable(error) else error
        if token != TOKEN:
            raise base.SourceAuthError("the token was refused", fix="paste a new user token")
        return Identity(ids=frozenset({"U_ME"}), display="@me")

    async def can_send(self) -> bool:
        return self.behaviour.get("can_send", True)

    def status_detail(self) -> str:
        return "" if self.behaviour.get("can_send", True) else "sending is off here"

    async def close(self) -> None:
        return None


@pytest.fixture
async def env(monkeypatch):
    await fx.clear()
    fx.install(fx.FakeSource())
    await fx.add_source()
    vault = Vault()
    from core.inbox.sources import credentials
    monkeypatch.setattr(credentials, "store_secret", vault.store)
    monkeypatch.setattr(credentials, "read_secret", vault.read)
    monkeypatch.setattr(credentials, "delete_secret", vault.delete)
    ConnSource.behaviour = {}
    monkeypatch.setitem(base._REGISTRY, "conn", ConnSource)
    from core.inbox import caller
    monkeypatch.setattr(caller, "check_caller", lambda client, server: 4242)
    connect._cache.clear()
    connect._gh_cache.clear()
    async with fx.client() as http:
        yield vault, http
    connect._cache.clear()
    connect._gh_cache.clear()


def _add(secret: Optional[str] = TOKEN, **options):
    body = {"kind": "conn", "backend": "web", "options": options}
    if secret is not None:
        body["secrets"] = {"user_token": secret}
    return body


async def _rows():
    from database import db
    return [r["id"] for r in await db.inbox_list_sources()]


# ── available ────────────────────────────────────────────────────────────────

class FakeGh:
    kind = "github"
    category = SourceCategory.CONVERSATION
    READ_TOOLS = frozenset()
    logged_in = True

    def __init__(self, cfg):
        self.cfg = cfg
        self.login = ""

    async def whoami(self):
        if not FakeGh.logged_in:
            raise base.SourceAuthError("not logged in", fix="gh auth login")
        self.login = "octo"
        return Identity(ids=frozenset({"octo"}), display="Octo Cat")

    def status_detail(self):
        return ""

    async def close(self):
        return None


@pytest.fixture
def mcp_world(monkeypatch):
    """Four configured servers: a slack-mcp one under a person's own id, an Outlook one,
    one with unrelated tools and one that fails to start; listing is counted."""
    import core.mcp_stdio_client as mcp
    from core.inbox.sources import mail, slack  # noqa: F401 - their MCP_FAMILIES are what match
    tools = {
        "work-slack": ["search", "list_dms", "get_messages", "get_thread", "post_message", "extra"],
        "outlook": ["email_folders", "email_read", "email_search", "email_reply"],
        "notes": ["search", "get_messages", "write_note"],   # shares names, not a whole mapping
        "broken": None,
    }
    listed: list[str] = []

    def resolve(server_id, workspace=None):
        if server_id == "gone":
            raise McpUnavailable("no such server")
        return McpServerSpec(id=server_id, command="fake-server", env={"TOKEN": "x"})

    async def list_tools(spec):
        listed.append(spec.id)
        if tools[spec.id] is None:
            raise McpProtocolError("the server exited")
        return [{"name": n} for n in tools[spec.id]]

    monkeypatch.setattr(mcp, "resolve_server", resolve)
    monkeypatch.setattr(connect, "_list_tools", list_tools)
    monkeypatch.setattr(connect, "_configured_servers", lambda: ["work-slack", "outlook", "notes", "broken", "gone"])
    FakeGh.logged_in = True
    monkeypatch.setitem(base._REGISTRY, "github", FakeGh)
    return listed


async def test_available_detects_matching_mcp_and_logged_in_gh(env, mcp_world):
    _vault, http = env
    await fx.add_source("slack-old", kind="slack")
    from database import db
    stamp = store.ts(store.now())
    await db.inbox_put_source({**(await db.inbox_get_source("slack-old")), "backend": "mcp:work-slack",
                               "updated_at": stamp})
    r = await http.get("/api/inbox/sources/available", headers=fx.UA)
    assert r.status_code == 200, r.text
    got = {(a["kind"], a["backend"]): a for a in r.json()}
    assert set(got) == {("slack", "mcp:work-slack"), ("mail", "mcp:outlook"), ("github", "gh")}
    assert got[("slack", "mcp:work-slack")]["options"] == {"tool_family": "slack-mcp"}
    assert got[("slack", "mcp:work-slack")]["added"] and got[("slack", "mcp:work-slack")]["source_id"] == "slack-old"
    assert got[("mail", "mcp:outlook")]["options"] == {"tool_family": "aws-outlook-mcp"}
    assert not got[("mail", "mcp:outlook")]["added"]
    assert got[("github", "gh")]["identity"] == "@octo"
    # probes are cached per server configuration; refresh probes again
    assert sorted(mcp_world) == ["broken", "notes", "outlook", "work-slack"]
    await http.get("/api/inbox/sources/available", headers=fx.UA)
    assert len(mcp_world) == 4
    await http.get("/api/inbox/sources/available?refresh=true", headers=fx.UA)
    assert len(mcp_world) == 8


async def test_listing_needs_the_header_and_concurrent_listings_share_one_probe(env, mcp_world, monkeypatch):
    import asyncio
    _vault, http = env
    refused = await http.get("/api/inbox/sources/available?refresh=true")
    assert refused.status_code == 403 and mcp_world == [], "a web page cannot make it start servers"
    real = connect._list_tools
    started = asyncio.Event()

    async def slow(spec):
        started.set()
        await asyncio.sleep(0.2)
        return await real(spec)

    monkeypatch.setattr(connect, "_list_tools", slow)
    results = await asyncio.gather(*(http.get("/api/inbox/sources/available?refresh=true", headers=fx.UA)
                                     for _ in range(5)))
    assert all(r.status_code == 200 for r in results)
    assert sorted(mcp_world) == ["broken", "notes", "outlook", "work-slack"], "one probe per server"


async def test_the_real_layers_and_a_real_stdio_server_are_read(tmp_path, monkeypatch):
    """No fake between the MCP config files, the stdio client and the matching."""
    import json
    import sys
    from core.inbox.sources import slack  # noqa: F401 - its MCP_FAMILIES is what matches
    server = tmp_path / "server.py"
    server.write_text(
        "import json, sys\n"
        "tools = sys.argv[1].split(',')\n"
        "for line in sys.stdin:\n"
        "    m = json.loads(line)\n"
        "    if 'id' not in m: continue\n"
        "    r = {'protocolVersion': '2024-11-05', 'capabilities': {}} if m.get('method') == 'initialize' "
        "else {'tools': [{'name': t} for t in tools]}\n"
        "    sys.stdout.write(json.dumps({'jsonrpc': '2.0', 'id': m['id'], 'result': r}) + '\\n'); sys.stdout.flush()\n")
    mcps = tmp_path / ".claude" / "mcps"
    mcps.mkdir(parents=True)
    entry = lambda sid, tools: {"id": sid, "name": sid, "connection_type": "stdio", "enabled": True,
                                "config": {"command": sys.executable, "args": [str(server), tools]}}
    (mcps / "mcp-dev.json").write_text(json.dumps([
        entry("team-chat", "search,list_dms,get_messages,get_thread,post_message"),
        entry("notes", "search,write"),
        {"id": "off", "name": "off", "connection_type": "stdio", "enabled": False, "config": {"command": sys.executable}},
        {"id": "remote", "name": "remote", "connection_type": "http", "enabled": True, "config": {"url": "https://x.example"}},
    ]))
    catalog_entry = entry("catalog-default-off", "search")
    del catalog_entry["enabled"]   # a catalog entry is off unless the person turned it on
    (mcps / "mcp-catalog.json").write_text(json.dumps([catalog_entry]))
    import config
    monkeypatch.setattr(config, "get_swarmws", lambda: tmp_path)
    connect._cache.clear()
    assert connect._configured_servers() == ["team-chat", "notes"]
    assert await connect.server_tools("team-chat") == frozenset(
        {"search", "list_dms", "get_messages", "get_thread", "post_message"})
    assert await connect.server_tools("notes") == frozenset({"search", "write"})
    connect._cache.clear()


async def test_available_leaves_out_a_logged_out_gh_and_a_turned_off_read_tool(env, mcp_world, monkeypatch):
    _vault, http = env
    FakeGh.logged_in = False
    import core.mcp_stdio_client as mcp
    real = mcp.resolve_server

    def resolve(server_id, workspace=None):
        spec = real(server_id)
        if server_id == "outlook":   # the person turned email_read off: the mapping cannot work
            return McpServerSpec(id=spec.id, command=spec.command, env=spec.env,
                                 rejected_tools=frozenset({"email_read"}))
        return spec

    monkeypatch.setattr(mcp, "resolve_server", resolve)
    kinds = {(a["kind"], a["backend"]) for a in (await http.get("/api/inbox/sources/available", headers=fx.UA)).json()}
    assert kinds == {("slack", "mcp:work-slack")}


# ── add ──────────────────────────────────────────────────────────────────────

async def test_add_stores_a_row_only_after_whoami(env):
    vault, http = env
    before = await _rows()
    bad = await http.post("/api/inbox/sources", json=_add("wrong-token"), headers=fx.UA)
    assert bad.status_code == 400, bad.text
    detail = bad.json()["detail"]
    assert detail["code"] == "whoami_failed" and detail["fix"] == "paste a new user token"
    assert await _rows() == before, "nothing is stored when whoami fails"
    assert vault.data == {}, "the secret given with a failed add is deleted again"

    ok = await http.post("/api/inbox/sources", json=_add(), headers=fx.UA)
    assert ok.status_code == 200, ok.text
    out = ok.json()
    assert out["identity"] == "@me" and out["health"] == "ok" and out["can_send"]
    assert out["interval_s"] == 900 and out["enabled"]
    assert out["id"] in await _rows()
    assert vault.data == {f"{out['id']}:user_token": TOKEN}

    again = await http.post("/api/inbox/sources", json=_add(), headers=fx.UA)
    assert again.status_code == 409 and again.json()["detail"]["code"] == "already_added"
    assert vault.data == {f"{out['id']}:user_token": TOKEN}, "the duplicate's secret is not kept"


async def test_add_refuses_secrets_in_options_unknown_kinds_and_secret_names(env):
    vault, http = env
    before = await _rows()
    token_like = "gh" + "p_" + "abcdefghijklmnopqrstuvwxyz0123456789"   # built here: no literal in the source
    for body in (_add(password="x"), _add(nested={"api_key": "x"}), _add(note=token_like),
                 {"kind": "nope", "backend": "x"},
                 {"kind": "conn", "backend": "web", "secrets": {"password": PASSWORD}}):
        r = await http.post("/api/inbox/sources", json=body, headers=fx.UA)
        assert r.status_code == 422, (body, r.text)
    assert await _rows() == before and vault.data == {}


async def test_only_an_allowlist_of_options_is_shown(env):
    from database import db
    _vault, http = env
    stray = {"pwd": "left-by-hand"}  # pragma: allowlist secret - a fake value: an option no allowlist shows
    await fx.add_source("s3", kind="slack", config={"watched_channels": ["#a"], **stray})
    out = {s["id"]: s for s in (await http.get("/api/inbox/sources")).json()}["s3"]
    assert out["options"] == {"watched_channels": ["#a"]}
    assert "left-by-hand" not in (await http.get("/api/inbox/sources")).text
    assert json_of((await db.inbox_get_source("s3"))["config"]) == {"watched_channels": ["#a"], **stray}


def json_of(text):
    import json
    return json.loads(text)


async def test_a_source_that_cannot_send_is_added_read_only_with_its_reason(env):
    _vault, http = env
    ConnSource.behaviour = {"can_send": False}
    out = (await http.post("/api/inbox/sources", json=_add(), headers=fx.UA)).json()
    assert out["health"] == "read_only" and out["status_detail"] == "sending is off here"
    assert out["fix"] == ""


async def test_secrets_are_never_returned_or_logged(env, caplog):
    vault, http = env
    caplog.set_level(logging.DEBUG)
    # a source whose error text repeats the secret it was given
    ConnSource.behaviour = {"error": lambda token: RuntimeError(f"server said: bad password {token}")}
    bad = await http.post("/api/inbox/sources", json=_add(PASSWORD), headers=fx.UA)
    assert bad.status_code == 400 and PASSWORD not in bad.text
    ConnSource.behaviour = {}
    ok = await http.post("/api/inbox/sources", json=_add(), headers=fx.UA)
    sid = ok.json()["id"]
    put = await http.post(f"/api/inbox/sources/{sid}/secret", headers=fx.UA,
                          json={"name": "user_token", "value": PASSWORD})
    assert put.status_code == 200, put.text
    assert vault.data[f"{sid}:user_token"] == PASSWORD
    listing = await http.get("/api/inbox/sources")
    for text in (ok.text, put.text, listing.text):
        assert TOKEN not in text and PASSWORD not in text
    wrong = await http.post(f"/api/inbox/sources/{sid}/secret", headers=fx.UA,
                            json={"name": "password", "value": PASSWORD})
    assert wrong.status_code == 422 and PASSWORD not in wrong.text
    assert TOKEN not in caplog.text and PASSWORD not in caplog.text


async def test_source_writes_pass_the_caller_check(env, monkeypatch):
    from core.inbox import caller
    vault, http = env
    await fx.add_source("s2")

    def refuse(client, server):
        raise caller.CallerRefused("backend_descendant")

    monkeypatch.setattr(caller, "check_caller", refuse)
    for call in (http.post("/api/inbox/sources", json=_add(), headers=fx.UA),
                 http.patch("/api/inbox/sources/s2", json={"extra_addresses": ["evil@x.example"]}, headers=fx.UA),
                 http.delete("/api/inbox/sources/s2", headers=fx.UA),
                 http.post("/api/inbox/sources/s2/secret", json={"name": "user_token", "value": TOKEN}, headers=fx.UA)):
        r = await call
        assert r.status_code == 403 and r.json()["detail"]["code"] == "forbidden"
    from database import db
    assert (await db.inbox_get_source("s2"))["config"] == "{}"
    assert sorted(await _rows()) == ["s1", "s2"] and vault.data == {}


async def test_source_writes_need_the_user_action_header(env):
    vault, http = env
    await fx.add_source("s2")
    calls = [
        http.post("/api/inbox/sources", json=_add()),
        http.patch("/api/inbox/sources/s2", json={"enabled": False}),
        http.delete("/api/inbox/sources/s2"),
        http.post("/api/inbox/sources/s2/secret", json={"name": "user_token", "value": TOKEN}),
    ]
    for call in calls:
        r = await call
        assert r.status_code == 403 and r.json()["detail"]["code"] == "forbidden"
    from database import db
    assert (await db.inbox_get_source("s2"))["enabled"] == 1
    assert sorted(await _rows()) == ["s1", "s2"] and vault.data == {}


# ── patch ────────────────────────────────────────────────────────────────────

async def test_patch_changes_enabled_interval_and_the_options_of_its_kind(env):
    _vault, http = env
    await fx.add_source("sl", kind="slack", config={"tool_family": "slack-mcp"})
    await fx.add_source("ml", kind="mail")
    r = await http.patch("/api/inbox/sources/sl", headers=fx.UA,
                         json={"enabled": False, "interval_s": 1800, "watched_channels": [" #ops ", "#ops", "C0TEAM1"]})
    assert r.status_code == 200, r.text
    out = r.json()
    assert not out["enabled"] and out["interval_s"] == 1800
    assert out["options"] == {"tool_family": "slack-mcp", "watched_channels": ["#ops", "C0TEAM1"]}
    assert out["detail"] == "关注 2 个频道"
    r = await http.patch("/api/inbox/sources/ml", headers=fx.UA, json={"extra_addresses": ["Me@Alias.example"]})
    assert r.json()["options"] == {"extra_addresses": ["me@alias.example"]}
    for sid, body in (("ml", {"watched_channels": ["#x"]}), ("sl", {"extra_addresses": ["a@b.example"]}),
                      ("ml", {"extra_addresses": ["not-an-address"]}), ("feed", {"enabled": False})):
        if sid == "feed":
            await fx.add_source("feed", kind="feed", category="feed")
        r = await http.patch(f"/api/inbox/sources/{sid}", headers=fx.UA, json=body)
        assert r.status_code == 422, (sid, body, r.text)
    assert (await http.patch("/api/inbox/sources/sl", headers=fx.UA, json={"interval_s": 10})).status_code in (400, 422)
    assert (await http.patch("/api/inbox/sources/none", headers=fx.UA, json={"enabled": True})).status_code == 404


# ── delete ───────────────────────────────────────────────────────────────────

async def _send_row(item_id: str, source_id: str, status: str, n: int) -> None:
    from database import db
    stamp = store.ts(store.now())
    assert await db.inbox_insert_send({
        "id": f"snd{n}", "item_id": item_id, "source_id": source_id, "draft_id": None, "origin": "click",
        "target": "{}", "target_sha": "", "text": "hi", "status": status, "external_msg_id": None,
        "url": "", "error": "", "created_at": stamp, "sent_at": None, "updated_at": stamp})


async def test_delete_removes_items_drafts_sends_and_secrets(env):
    from database import db
    vault, http = env
    await fx.add_source("s2")
    mine = await fx.add_item("s1", "e1")
    other = await fx.add_item("s2", "e9", thread=9)
    stamp = store.ts(store.now())
    for it in (mine, other):
        await db.inbox_insert_draft({"id": f"dr-{it['id']}", "item_id": it["id"], "text": "d", "lang": "",
                                     "note": "", "model": "", "created_at": stamp})
        await db.inbox_insert_feedback({"id": f"fb-{it['id']}", "item_id": it["id"], "source_id": it["source_id"],
                                        "action": "dismiss", "from_lane": "reply", "to_lane": None,
                                        "created_at": stamp})
    await _send_row(mine["id"], "s1", "sent", 1)
    await _send_row(other["id"], "s2", "sent", 2)
    vault.data = {"s1:user_token": TOKEN, "s2:user_token": TOKEN}

    # a send of the source being sent right now: nothing is removed
    await _send_row(mine["id"], "s1", "reserved", 3)
    busy = await http.delete("/api/inbox/sources/s1", headers=fx.UA)
    assert busy.status_code == 409 and busy.json()["detail"]["code"] == "send_in_flight"
    assert await db.inbox_get_item(mine["id"]) is not None and "s1:user_token" in vault.data
    assert await db.inbox_update_send("snd3", stamp, expect_status="reserved", status="failed")

    r = await http.delete("/api/inbox/sources/s1", headers=fx.UA)
    assert r.status_code == 200, r.text
    assert r.json() == {"id": "s1", "items": 1, "drafts": 1, "sends": 2}
    assert await db.inbox_get_source("s1") is None and await db.inbox_get_item(mine["id"]) is None
    assert await db.inbox_latest_draft(mine["id"]) is None
    assert await db.inbox_get_send("snd1") is None and await db.inbox_get_send("snd3") is None
    assert await db.inbox_recent_feedback("s1") == []
    assert vault.data == {"s2:user_token": TOKEN}
    # the other source keeps everything
    assert await db.inbox_get_item(other["id"]) is not None
    assert await db.inbox_latest_draft(other["id"]) is not None and await db.inbox_get_send("snd2") is not None
    assert len(await db.inbox_recent_feedback("s2")) == 1
    assert (await http.delete("/api/inbox/sources/s1", headers=fx.UA)).status_code == 404


async def test_a_removal_during_a_long_pull_answers_busy_and_removes_nothing(env, monkeypatch):
    from core.inbox import runner
    from database import db
    _vault, http = env
    await fx.add_item("s1", "e1")
    monkeypatch.setattr(runner, "REMOVE_WAIT_S", 0.05)
    lock, _ = runner._primitives()
    async with lock:   # a pull is running
        r = await http.delete("/api/inbox/sources/s1", headers=fx.UA)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "pull_running"
    assert await db.inbox_get_source("s1") is not None
    assert (await http.delete("/api/inbox/sources/s1", headers=fx.UA)).status_code == 200


async def test_the_feed_source_is_not_removed_here(env):
    _vault, http = env
    await fx.add_source("feed", kind="feed", category="feed")
    r = await http.delete("/api/inbox/sources/feed", headers=fx.UA)
    assert r.status_code == 422 and r.json()["detail"]["code"] == "feed_source"


async def test_a_send_reserved_after_its_source_was_removed_is_dropped(env, monkeypatch):
    """The removal lands between the send's checks and its reservation."""
    from core.inbox import caller
    from database import db
    _vault, http = env
    fake = fx.install(fx.FakeSource())
    item = await fx.add_item("s1", "e1")
    monkeypatch.setattr(caller, "check_caller", lambda client, server: 4242)
    sha = (await http.get(f"/api/inbox/items/{item['id']}/target")).json()["target_sha"]
    real_can_send = fake.can_send

    async def can_send_then_removed():
        ok = await real_can_send()
        await store.delete_source_rows("s1")
        return ok

    monkeypatch.setattr(fake, "can_send", can_send_then_removed)
    r = await http.post(f"/api/inbox/items/{item['id']}/send", headers=fx.UA,
                        json={"text": "ok", "target_sha": sha})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "source_removed"
    assert fake.calls["reply"] == 0
    assert await db.inbox_sends(("reserved", "unknown", "failed", "sent")) == []
