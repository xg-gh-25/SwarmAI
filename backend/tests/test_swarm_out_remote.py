"""Swarm Out remote session proxy (routers/swarm_out_remote.py, core/swarm_out_remote.py).

Questions: does the proxy reach only a session this Host dispatched, from the app, in
desktop mode (R1); is what it sends the Hive rebuilt from the route's keys, with the
Hive's credentials and none of the client's headers (R2); is what comes back treated
as untrusted — no redirect, a few headers, no Hive error text, no oversized line, no
event outside the whitelist or of another session, no Hive eventSeq on a POST stream
(R3)? Every case checks the Hive is not called when the request is refused.
"""
import base64
import json

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core import swarm_out, swarm_out_remote
from routers import swarm_out_remote as remote_router

SID = "sess-1"
INST = {"id": "h1", "name": "worker-1", "status": "running", "cloudfront_domain": "abc123.cloudfront.net",
        "s3_bucket": "swarmai-hive-2361-us-east-1", "region": "us-east-1",
        "auth_user": "admin", "auth_password": "s3cret-pass", "account_ref": "a1"}
APP_HEADERS = {"Content-Type": "application/json", "X-Swarm-User-Action": "1"}


class FakeHive:
    """Records every request; answers from ``routes`` (path -> callable(request) -> Response)."""

    def __init__(self):
        self.seen: list[httpx.Request] = []
        self.routes = {}

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.seen.append(request)
        fn = self.routes.get(request.url.path)
        return fn(request) if fn else httpx.Response(404)


def _sse(*events, done=True) -> bytes:
    body = "".join(f"data: {json.dumps(e)}\n\n" for e in events)
    return (body + ("data: [DONE]\n\n" if done else "")).encode()


@pytest.fixture
def hive(monkeypatch):
    fake = FakeHive()
    monkeypatch.delenv("SWARMAI_MODE", raising=False)
    rows = [{"dispatch_id": "so_1", "hive_id": "h1", "hive": "worker-1", "session_id": SID}]
    monkeypatch.setattr(swarm_out, "_read_log", lambda: list(rows))

    async def instance(ref):
        assert ref == "h1"
        return swarm_out._validate_instance(INST, {"id": "a1"})
    monkeypatch.setattr(swarm_out, "_instance", instance)

    real_client = swarm_out._client

    def client(inst, timeout=swarm_out.HTTP_TIMEOUT_S):
        # The production client (its headers and event hooks), with only the
        # transport swapped for the fake Hive.
        c = real_client(inst, timeout)
        c._transport = httpx.MockTransport(fake.handler)
        return c
    monkeypatch.setattr(swarm_out, "_client", client)
    from core.session_manager import session_manager

    async def no_local_session(sid):
        return None
    monkeypatch.setattr(session_manager, "get_session", no_local_session)
    fake.rows = rows
    return fake


@pytest.fixture
def api():
    app = FastAPI()
    app.include_router(remote_router.router, prefix="/api/swarm-out/remote")
    return TestClient(app)


BASE = f"/api/swarm-out/remote/{SID}"


# ── R1: who and which session ──

def test_hive_mode_refuses_every_route(hive, api, monkeypatch):
    monkeypatch.setenv("SWARMAI_MODE", "hive")
    assert api.get(f"{BASE}/chat/sessions/{SID}").status_code == 403
    assert api.post(f"{BASE}/chat/stop/{SID}", headers=APP_HEADERS, json={}).status_code == 403
    assert api.post(f"{BASE}/relay", headers=APP_HEADERS, json={"client_id": "w1"}).status_code == 403
    assert hive.seen == []


@pytest.mark.parametrize("sid", ["other-sess", "a.b", "..", "x" * 129])
def test_a_session_not_dispatched_here_is_not_reachable(hive, api, sid):
    hive.routes[f"/api/chat/sessions/{sid}"] = lambda r: httpx.Response(200, json={})
    assert api.get(f"/api/swarm-out/remote/{sid}/chat/sessions/{sid}").status_code == 404
    assert hive.seen == []


def test_a_session_logged_under_two_hives_is_refused(hive, api):
    hive.rows.append({"dispatch_id": "so_2", "hive_id": "h2", "hive": "worker-2", "session_id": SID})
    assert api.get(f"{BASE}/chat/sessions/{SID}").status_code == 404
    assert hive.seen == []


def test_a_local_session_id_is_refused(hive, api, monkeypatch):
    class Router:
        def get_unit(self, sid):
            return object() if sid == SID else None
    from core import session_registry
    monkeypatch.setattr(session_registry, "session_router", Router())
    r = api.get(f"{BASE}/chat/sessions/{SID}")
    assert r.status_code == 409 and r.json()["detail"] == swarm_out_remote.COLLIDES   # said, never addressed
    assert hive.seen == []


def test_a_stored_local_session_without_a_unit_is_refused(hive, api, monkeypatch):
    # the sid in the dispatch log was named by the Hive: it must never address a session
    # of this daemon, loaded or not
    from core.session_manager import session_manager

    async def stored(sid):
        return object() if sid == SID else None
    monkeypatch.setattr(session_manager, "get_session", stored)
    assert api.get(f"{BASE}/chat/sessions/{SID}").status_code == 409
    assert api.post(f"{BASE}/relay", headers=APP_HEADERS, json={"client_id": "w1"}).status_code == 409
    assert hive.seen == []


@pytest.mark.parametrize("headers", [
    {"X-Swarm-User-Action": "1"},                                       # no JSON content type
    {"Content-Type": "text/plain", "X-Swarm-User-Action": "1"},
    {"Content-Type": "application/json"},                               # no user-action header
])
def test_a_write_not_from_the_app_is_refused(hive, api, headers):
    hive.routes[f"/api/chat/stop/{SID}"] = lambda r: httpx.Response(200, json={"status": "ok"})
    r = api.post(f"{BASE}/chat/stop/{SID}", headers=headers, content=b"{}")
    assert r.status_code == 415
    assert api.request("DELETE", f"{BASE}/relay", headers=headers, content=b'{"client_id":"w1"}').status_code == 415
    assert hive.seen == []


@pytest.mark.parametrize("task", ["%2E%2E", "a.b", "a%2Fb", "x" * 129])   # %2E%2E: a client would normalise a literal ..
def test_a_bad_path_parameter_is_refused(hive, api, task):
    r = api.post(f"{BASE}/chat/stop/{SID}/task/{task}", headers=APP_HEADERS, json={})
    assert r.status_code == 404
    assert hive.seen == []


# ── R2: what goes to the Hive ──

@pytest.mark.parametrize("method,path", [
    ("GET", "chat/sessions"),                              # every session of the Hive
    ("DELETE", f"chat/sessions/{SID}"),                    # delete the session
    ("GET", "chat/sessions/other/messages"),               # another session's messages
    ("GET", "chat/events"),                                # the Hive's whole event stream
    ("POST", "chat/transcribe"),
    ("GET", "settings/open-tabs"),
    ("GET", "chat/sessions/streaming-state"),
])
def test_an_unlisted_endpoint_is_not_forwarded(hive, api, method, path):
    r = api.request(method, f"{BASE}/{path}", headers=APP_HEADERS, content=b"{}")
    assert r.status_code == 404
    assert hive.seen == []


def _stream_ok(r):
    return httpx.Response(200, content=_sse({"type": "accepted", "sessionId": SID}),
                          headers={"content-type": "text/event-stream"})


def test_the_stream_body_is_rebuilt_from_its_keys(hive, api):
    hive.routes["/api/chat/stream"] = _stream_ok
    body = {"session_id": SID, "message": "hi", "client_id": "c1", "agent_id": "evil-agent",
            "terminal_context": {"buffer_tail": "secret"}, "editor_context": {"file": "/Users/x/.aws/credentials"},
            "project_agent": {"brain": "X"}, "task_repos": [{"url": "u"}], "enable_mcp": True}
    r = api.post(f"{BASE}/chat/stream", headers=APP_HEADERS, json=body)
    assert r.status_code == 200
    sent = json.loads(hive.seen[-1].content)
    assert sent == {"session_id": SID, "message": "hi", "client_id": "c1", "agent_id": "default"}


@pytest.mark.parametrize("session_id", [None, "", "other-sess"])
def test_a_missing_or_other_session_id_is_refused(hive, api, session_id):
    hive.routes["/api/chat/stream"] = _stream_ok
    body = {"message": "hi"} if session_id is None else {"message": "hi", "session_id": session_id}
    assert api.post(f"{BASE}/chat/stream", headers=APP_HEADERS, json=body).status_code == 400
    assert api.post(f"{BASE}/chat/stream", headers=APP_HEADERS,
                    json={"message": "hi", "session_id": None}).status_code == 400
    assert hive.seen == []


@pytest.mark.parametrize("block,ok", [
    ({"type": "text", "text": "hi"}, True),
    ({"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "aGk="}}, True),
    ({"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": "aGk="}}, True),
    ({"type": "image", "source": {"type": "url", "url": "http://169.254.169.254/"}}, False),
    ({"type": "path_hint", "text": "saved at /Users/x"}, False),
    ({"type": "tool_result", "content": "x"}, False),
])
def test_content_blocks_are_limited(hive, api, block, ok):
    hive.routes["/api/chat/stream"] = _stream_ok
    r = api.post(f"{BASE}/chat/stream", headers=APP_HEADERS, json={"session_id": SID, "content": [block]})
    assert (r.status_code == 200) is ok
    assert (len(hive.seen) == 1) is ok


def test_answer_and_permission_bodies_are_rebuilt(hive, api):
    hive.routes["/api/chat/answer-question"] = _stream_ok
    hive.routes["/api/chat/cmd-permission-continue"] = _stream_ok
    api.post(f"{BASE}/chat/answer-question", headers=APP_HEADERS,
             json={"session_id": SID, "tool_use_id": "t1", "answers": {"q": "a"}, "enable_mcp": True, "x": 1})
    api.post(f"{BASE}/chat/cmd-permission-continue", headers=APP_HEADERS,
             json={"session_id": SID, "request_id": "r1", "decision": "approve", "extra": "y"})
    assert json.loads(hive.seen[0].content) == {"session_id": SID, "tool_use_id": "t1",
                                                "answers": {"q": "a"}, "agent_id": "default"}
    assert json.loads(hive.seen[1].content) == {"session_id": SID, "request_id": "r1",
                                                "decision": "approve", "agent_id": "default"}


def test_no_client_header_goes_upstream(hive, api):
    hive.routes[f"/api/chat/sessions/{SID}"] = lambda r: httpx.Response(200, json={"id": SID})
    api.get(f"{BASE}/chat/sessions/{SID}", headers={
        "Cookie": "c=1", "Authorization": "Bearer local", "Origin": "http://evil.example",
        "X-Custom": "y", "If-None-Match": '"1"'})
    sent = hive.seen[-1].headers
    expected = "Basic " + base64.b64encode(b"admin:s3cret-pass").decode()
    assert sent["authorization"] == expected
    for h in ("cookie", "origin", "x-custom", "if-none-match"):
        assert h not in sent


def test_only_listed_query_keys_are_forwarded(hive, api):
    hive.routes[f"/api/chat/sessions/{SID}/messages"] = lambda r: httpx.Response(200, json=[])
    api.get(f"{BASE}/chat/sessions/{SID}/messages?turns=5&before_id=m1&evil=1")
    assert dict(hive.seen[-1].url.params) == {"turns": "5", "before_id": "m1"}


# ── R3: what comes back ──

def test_a_redirect_becomes_502_without_location(hive, api):
    hive.routes[f"/api/chat/sessions/{SID}"] = lambda r: httpx.Response(
        302, headers={"Location": "http://127.0.0.1:18321/api/settings/open-tabs"})
    r = api.get(f"{BASE}/chat/sessions/{SID}", follow_redirects=False)
    assert r.status_code == 502 and "location" not in r.headers


def test_only_allowed_response_headers_pass(hive, api):
    hive.routes[f"/api/chat/sessions/{SID}/messages"] = lambda r: httpx.Response(
        200, json=[], headers={"ETag": '"s:3"', "X-Has-More": "1", "Set-Cookie": "a=b",
                               "WWW-Authenticate": "Basic", "X-Hive-Secret": "z"})
    r = api.get(f"{BASE}/chat/sessions/{SID}/messages")
    assert r.headers["etag"] == '"s:3"' and r.headers["x-has-more"] == "1"
    for h in ("set-cookie", "www-authenticate", "x-hive-secret"):
        assert h not in r.headers


def test_a_hive_error_keeps_its_status_but_not_its_body(hive, api):
    hive.routes[f"/api/chat/sessions/{SID}"] = lambda r: httpx.Response(500, text="Traceback: /opt/hive/secret.py")
    r = api.get(f"{BASE}/chat/sessions/{SID}")
    assert r.status_code == 500 and "secret" not in r.text and "Traceback" not in r.text


def test_an_oversized_stream_line_closes_the_stream(hive, api, monkeypatch):
    monkeypatch.setattr(swarm_out_remote, "MAX_LINE_BYTES", 2048)
    big = {"type": "assistant", "content": [{"type": "text", "text": "x" * 4096}]}
    hive.routes["/api/chat/stream"] = lambda r: httpx.Response(
        200, content=_sse({"type": "accepted"}, big, {"type": "result"}),
        headers={"content-type": "text/event-stream"})
    r = api.post(f"{BASE}/chat/stream", headers=APP_HEADERS, json={"session_id": SID, "message": "hi"})
    assert '"accepted"' in r.text
    assert "x" * 100 not in r.text and '"result"' not in r.text


def test_an_oversized_body_is_refused(hive, api, monkeypatch):
    monkeypatch.setattr(remote_router, "MAX_BODY_BYTES", 1000)
    hive.routes[f"/api/chat/sessions/{SID}"] = lambda r: httpx.Response(200, content=b"x" * 5000)
    assert api.get(f"{BASE}/chat/sessions/{SID}").status_code == 502


def test_a_body_is_cut_off_at_the_cap_while_it_streams(monkeypatch):
    # read_capped stops reading past the cap: the rest of the body is never pulled
    import asyncio
    pulled = []

    class Resp:
        headers = {}

        async def aiter_bytes(self):
            for i in range(100):
                pulled.append(i)
                yield b"x" * 100
    with pytest.raises(swarm_out_remote.RemoteError):
        asyncio.run(swarm_out_remote.read_capped(Resp(), 250))
    assert len(pulled) == 3
    big = type("R", (), {"headers": {"content-length": "999999"}, "aiter_bytes": Resp.aiter_bytes})()
    with pytest.raises(swarm_out_remote.RemoteError):
        asyncio.run(swarm_out_remote.read_capped(big, 250))


KEPT = ["accepted", "turn_start", "assistant", "text_delta", "tool_use", "tool_result",
        "ask_user_question", "cmd_permission_request", "result", "error", "turn_end",
        "session_state", "transcript_row", "background_agents", "heartbeat"]
DROPPED = ["ui_command", "file_changed", "turn_deliverables", "session_cleared", "events_hello", "resync", "made_up"]


def test_stream_events_are_cleaned(hive, api):
    events = [{"type": t, "sessionId": SID, "turnId": "t1", "eventSeq": i} for i, t in enumerate(KEPT + DROPPED)]
    events += [{"type": "assistant", "sessionId": "other", "eventSeq": 99},
               {"type": "session_start", "session_id": "other"},
               {"type": "session_start", "session_id": SID},
               {"type": "session_cleared", "sessionId": SID, "newSessionId": "victim-local-sess"},
               {"type": "assistant", "sessionId": SID, "newSessionId": "victim-local-sess"},
               {"type": "assistant", "sessionId": SID, "new_session_id": "victim-local-sess"},
               {"type": "ui_command", "cmd": "open-canvas-file", "path": "/Users/x/.aws/credentials", "allowAbs": True}]
    hive.routes["/api/chat/stream"] = lambda r: httpx.Response(200, content=_sse(*events),
                                                               headers={"content-type": "text/event-stream"})
    r = api.post(f"{BASE}/chat/stream", headers=APP_HEADERS, json={"session_id": SID, "message": "hi"})
    got = [json.loads(line[5:]) for line in r.text.split("\n") if line.startswith("data: {")]
    assert [e["type"] for e in got] == KEPT + ["session_start"]
    assert all("eventSeq" not in e for e in got)
    assert all(e.get("sessionId", SID) == SID and e.get("session_id", SID) == SID for e in got)
    assert r.text.rstrip().endswith("data: [DONE]")
    assert "credentials" not in r.text and "victim" not in r.text


def test_a_line_nested_past_the_parser_depth_is_skipped(hive, api):
    hive.routes["/api/chat/stream"] = lambda r: httpx.Response(
        200, content=b"data: " + b"[" * 200000 + b"\n\n" + _sse({"type": "result", "sessionId": SID}),
        headers={"content-type": "text/event-stream"})
    r = api.post(f"{BASE}/chat/stream", headers=APP_HEADERS, json={"session_id": SID, "message": "hi"})
    assert '"result"' in r.text and r.text.rstrip().endswith("data: [DONE]")


@pytest.mark.parametrize("etype", KEPT)
def test_clean_event_keeps_whitelisted_types(etype):
    out = swarm_out_remote.clean_event({"type": etype, "sessionId": SID, "eventSeq": 4}, SID, post_stream=True)
    assert out == {"type": etype, "sessionId": SID}
    kept = swarm_out_remote.clean_event({"type": etype, "sessionId": SID, "eventSeq": 4}, SID, post_stream=False)
    assert kept["eventSeq"] == 4


@pytest.mark.parametrize("etype", DROPPED)
def test_clean_event_drops_the_rest(etype):
    assert swarm_out_remote.clean_event({"type": etype, "sessionId": SID}, SID, post_stream=False) is None


def test_a_tree_diff_is_forwarded_with_its_query_keys_only(hive, api):
    hive.routes["/api/canvas/changes/tree-diff"] = lambda r: httpx.Response(200, json={"diff": "@@"})
    r = api.get(f"{BASE}/canvas/changes/tree-diff?root=/hive/repo&start=a&end=b&path=x.py&evil=1")
    assert r.status_code == 200 and r.json() == {"diff": "@@"}
    assert dict(hive.seen[-1].url.params) == {"root": "/hive/repo", "start": "a", "end": "b", "path": "x.py"}


# ── A Hive's answer is never decoded (bugfix: compressed answers past the caps) ──

def test_the_hive_client_asks_for_identity_and_refuses_an_encoded_answer():
    import asyncio
    client = swarm_out._client(INST)
    assert client.headers["accept-encoding"] == "identity"
    assert swarm_out._refuse_encoded in client.event_hooks["response"]

    async def go():
        await client.aclose()
        req = httpx.Request("GET", "https://abc123.cloudfront.net/x")
        for enc in ("gzip", "br", "deflate", " GZIP "):
            with pytest.raises(httpx.DecodingError):
                await swarm_out._refuse_encoded(httpx.Response(200, headers={"content-encoding": enc}, request=req))
        for enc in (None, "identity", "Identity"):
            headers = {"content-encoding": enc} if enc else {}
            await swarm_out._refuse_encoded(httpx.Response(200, headers=headers, request=req))
    asyncio.run(go())


def _gzip(raw: bytes) -> bytes:
    import gzip
    return gzip.compress(raw)


def test_every_proxied_request_asks_for_identity(hive, api):
    hive.routes[f"/api/chat/sessions/{SID}"] = lambda r: httpx.Response(200, json={"id": SID})
    assert api.get(f"{BASE}/chat/sessions/{SID}").status_code == 200
    assert hive.seen[-1].headers["accept-encoding"] == "identity"


def test_a_compressed_answer_is_refused_without_decoding(hive, api):
    hive.routes[f"/api/chat/sessions/{SID}"] = lambda r: httpx.Response(
        200, content=_gzip(json.dumps({"id": SID}).encode()), headers={"content-encoding": "gzip"})
    r = api.get(f"{BASE}/chat/sessions/{SID}")
    assert r.status_code == 502
    assert SID not in r.text


def test_a_compressed_stream_is_refused(hive, api):
    hive.routes["/api/chat/stream"] = lambda r: httpx.Response(
        200, content=_gzip(_sse({"type": "result", "sessionId": SID})),
        headers={"content-type": "text/event-stream", "content-encoding": "gzip"})
    r = api.post(f"{BASE}/chat/stream", headers=APP_HEADERS, json={"session_id": SID, "message": "hi"})
    assert r.status_code == 502
    assert '"result"' not in r.text


def test_an_uncompressed_answer_within_the_cap_is_forwarded_as_before(hive, api):
    hive.routes[f"/api/chat/sessions/{SID}"] = lambda r: httpx.Response(200, json={"id": SID, "n": 1})
    r = api.get(f"{BASE}/chat/sessions/{SID}")
    assert r.status_code == 200 and r.json() == {"id": SID, "n": 1}


# ── a Hive session's id never becomes a session of this machine ──

@pytest.mark.asyncio
async def test_a_local_send_under_a_hive_sessions_id_is_refused(hive, async_client, monkeypatch):
    """A tab that kept a Hive session's id must not make a local session of it: the run
    could never be opened again (resolve_session refuses a local id)."""
    from routers import chat as chat_router
    seen = []

    class _Router:
        def get_unit(self, sid):
            return None

        async def run_conversation(self, **kw):
            seen.append(kw["session_id"])
            yield {"type": "result", "sessionId": kw["session_id"]}
    monkeypatch.setattr(chat_router, "_get_router", lambda: _Router())
    r = await async_client.post("/api/chat/stream", json={"agent_id": "default", "message": "hi", "session_id": SID})
    assert r.status_code == 409 and "Hive session" in r.json()["detail"] and seen == []
    # any other id this Host did not dispatch goes through as before
    r = await async_client.post("/api/chat/stream", json={"agent_id": "default", "message": "hi", "session_id": "local-1"})
    assert r.status_code == 200 and seen == ["local-1"]
    # and a session of this machine that already holds the id is not cut off
    from core.session_manager import session_manager

    async def stored(sid):
        return object()
    monkeypatch.setattr(session_manager, "get_session", stored)
    r = await async_client.post("/api/chat/stream", json={"agent_id": "default", "message": "hi", "session_id": SID})
    assert r.status_code == 200 and seen[-1] == SID
