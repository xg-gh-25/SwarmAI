"""Swarm Out, Host side (core/swarm_out.py) and the Hive IAM policy it relies on."""
import asyncio
import json
import time

import httpx

from tests import _hive_work_fake as _work
import pytest

from core import swarm_out
from hive import provisioner


@pytest.fixture(autouse=True)
def _no_running_hives_from_the_store(monkeypatch):
    """list_runs also reads the Hives' status from the Host's store: here the tests'
    Hive h1 is running (a test that needs another store sets it)."""
    async def h1_running():
        return {"h1": "running"}
    monkeypatch.setattr(swarm_out, "_hive_statuses", h1_running)

INST = {"id": "h1", "name": "worker-1", "status": "running", "cloudfront_domain": "abc123.cloudfront.net",
        "s3_bucket": "swarmai-hive-2361-us-east-1", "region": "us-east-1",
        "auth_user": "admin", "auth_password": "s3cret-pass", "account_ref": "a1"}


# ── instance validation (every outbound URL and S3 key comes from these fields) ──

@pytest.mark.parametrize("field,value", [
    ("cloudfront_domain", "evil.example.com"),
    ("cloudfront_domain", "abc.cloudfront.net/../x"),
    ("cloudfront_domain", "169.254.169.254"),
    ("name", "../other"),
    ("s3_bucket", "Bad_Bucket"),
    ("status", "stopped"),
])
def test_unexpected_instance_fields_are_refused(field, value):
    with pytest.raises(swarm_out.SwarmOutError):
        swarm_out._validate_instance({**INST, field: value}, {"id": "a1"})


def test_valid_instance_passes():
    assert swarm_out._validate_instance(INST, {"id": "a1"})["cloudfront_domain"] == "abc123.cloudfront.net"


# ── dispatch ──

def _fake_hive(saturated=False, events=None, seen=None):
    events = events if events is not None else [{"type": "session_start", "session_id": "sess-42"}]

    def handler(request: httpx.Request):
        if seen is not None:
            seen.append(request)
        if request.url.path == "/api/chat/sessions/admission-state":
            return httpx.Response(200, json={**_work.ADMISSION, "saturated": saturated})
        if _work.answer(request) is not None:
            return _work.answer(request)
        if request.url.path == "/api/chat/stream":
            body = "".join(f"data: {json.dumps(e)}\n\n" for e in events)
            return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})
        return httpx.Response(404)
    return handler


@pytest.fixture
def patched(monkeypatch, tmp_path):
    async def fake_instance(ref):
        return swarm_out._validate_instance(INST, {"id": "a1"})
    monkeypatch.setattr(swarm_out, "_instance", fake_instance)
    monkeypatch.setattr(swarm_out, "_state_dir", lambda: tmp_path)
    return tmp_path


def _use(monkeypatch, handler):
    def client(inst, timeout=None):
        return httpx.AsyncClient(base_url=swarm_out._base(inst), headers=swarm_out._auth(inst),
                                 transport=httpx.MockTransport(handler))
    monkeypatch.setattr(swarm_out, "_client", client)


def test_dispatch_returns_the_session_and_records_no_credentials(patched, monkeypatch):
    seen = []
    _use(monkeypatch, _fake_hive(seen=seen))
    row = asyncio.run(swarm_out.dispatch("worker-1", "Fix the thing", "do X"))
    assert row["session_id"] == "sess-42" and row["hive"] == "worker-1"
    log = (patched / "dispatches.jsonl").read_text()
    assert "s3cret-pass" not in json.dumps(row) and "s3cret-pass" not in log
    sent = json.loads(seen[-1].content)
    pre = swarm_out.UNATTENDED_PREAMBLE.removesuffix("Task:\n")
    assert sent["message"].startswith(pre) and sent["message"].endswith("Task:\ndo X")


def test_the_row_keeps_what_the_card_shows_never_over_its_own_fields(patched, monkeypatch):
    _use(monkeypatch, _fake_hive())
    extra = {"brief": "b", "hive_reason": "idle", "brain_sync": "synced",
             "repo_notes": [{"name": "r", "url": "u", "dirty": 2}], "session_id": "forged", "hive": "x"}
    row = asyncio.run(swarm_out.dispatch("worker-1", "t", "x", extra=extra))
    (logged,) = [json.loads(line) for line in (patched / "dispatches.jsonl").read_text().splitlines()]
    assert logged == row and (row["session_id"], row["hive"]) == ("sess-42", "worker-1")
    assert (row["brief"], row["hive_reason"], row["brain_sync"], row["repo_notes"][0]["dirty"]) == ("b", "idle", "synced", 2)


def test_dispatch_refuses_a_busy_hive(patched, monkeypatch):
    _use(monkeypatch, _fake_hive(saturated=True))
    with pytest.raises(swarm_out.SwarmOutError, match="busy"):
        asyncio.run(swarm_out.dispatch("worker-1", "t", "x"))
    assert not (patched / "dispatches.jsonl").exists()


def test_dispatch_without_a_session_id_fails_and_records_nothing(patched, monkeypatch):
    _use(monkeypatch, _fake_hive(events=[{"type": "assistant", "content": "hi"}]))
    with pytest.raises(swarm_out.SwarmOutError):
        asyncio.run(swarm_out.dispatch("worker-1", "t", "x"))
    assert not (patched / "dispatches.jsonl").exists()


# ── answers are checked against the question the Hive wrote ──

Q = {"kind": "question", "questions": [{"question": "Which DB?"}, {"question": "Which port?"}]}


def test_question_answer_must_cover_every_question_exactly():
    assert swarm_out._check_answer(Q, {"Which DB?": "sqlite", "Which port?": "80"}, None) == \
        {"answers": {"Which DB?": "sqlite", "Which port?": "80"}}
    for bad in ({"Which DB?": "sqlite"}, {"Which DB?": "a", "Which port?": "b", "Other?": "c"},
                {"Which DB?": "", "Which port?": "80"}, None):
        with pytest.raises(swarm_out.SwarmOutError):
            swarm_out._check_answer(Q, bad, None)


def test_approval_takes_only_approve_or_deny():
    assert swarm_out._check_answer({"kind": "approval"}, None, "deny") == {"decision": "deny"}
    with pytest.raises(swarm_out.SwarmOutError):
        swarm_out._check_answer({"kind": "approval"}, None, "allow_all")


class FakeS3:
    class exceptions:
        class NoSuchKey(Exception):
            pass

    def __init__(self, objects):
        self.objects = objects

    def get_object(self, Bucket, Key):
        if Key not in self.objects:
            raise self.exceptions.NoSuchKey()
        body = self.objects[Key]

        class B:
            def read(self, n=-1):
                return body
        return {"Body": B()}

    def put_object(self, Bucket, Key, Body, **kw):
        if kw.get("IfNoneMatch") == "*" and Key in self.objects:
            err = Exception("precondition")
            err.response = {"Error": {"Code": "PreconditionFailed"}}
            raise err
        self.objects[Key] = Body


def _answer_env(monkeypatch, objects):
    s3 = FakeS3(objects)

    async def fake_instance(ref):
        return swarm_out._validate_instance(INST, {"id": "a1"})
    monkeypatch.setattr(swarm_out, "_instance", fake_instance)
    monkeypatch.setattr(swarm_out, "_s3", lambda inst: s3)
    return s3


ASK = "bridge/worker-1/ask/sess-1/req-1.json"
ANS = "bridge/worker-1/answer/sess-1/req-1.json"
NOW = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
APPROVAL = json.dumps({"kind": "approval", "session_id": "sess-1", "request_id": "req-1",
                       "asked_at": NOW, "command": "rm -rf /tmp/x"}).encode()


SHOWN = swarm_out.approval_digest("", "rm -rf /tmp/x")   # what the card shows for APPROVAL


def test_answer_is_written_under_the_hives_answer_prefix(monkeypatch):
    s3 = _answer_env(monkeypatch, {ASK: APPROVAL})
    asyncio.run(swarm_out.answer("h1", "sess-1", "req-1", decision="approve", shown_sha256=SHOWN))
    body = json.loads(s3.objects[ANS])
    assert body["request_id"] == "req-1" and body["decision"] == "approve"
    assert body["shown_sha256"] == SHOWN


def test_an_approval_of_a_changed_ask_is_refused(monkeypatch):
    """The ask was rewritten after the card was shown (fixing spec B3)."""
    s3 = _answer_env(monkeypatch, {ASK: APPROVAL})
    with pytest.raises(swarm_out.SwarmOutError, match="changed"):
        asyncio.run(swarm_out.answer("h1", "sess-1", "req-1", decision="approve",
                                     shown_sha256=swarm_out.approval_digest("", "ls")))
    assert ANS not in s3.objects


def test_a_truncated_command_cannot_be_approved_but_can_be_denied(monkeypatch):
    long = json.dumps({"kind": "approval", "session_id": "sess-1", "request_id": "req-1",
                       "asked_at": NOW, "tool_name": "Bash", "truncated": True,
                       "command": "echo " + "x" * 3000}).encode()
    s3 = _answer_env(monkeypatch, {ASK: long})
    rec = swarm_out._clean_ask(json.loads(long), "worker-1")
    assert rec["truncated"] is True and len(rec["command"]) == swarm_out.MAX_COMMAND_CHARS
    with pytest.raises(swarm_out.SwarmOutError, match="too long"):
        asyncio.run(swarm_out.answer("h1", "sess-1", "req-1", decision="approve",
                                     shown_sha256=rec["shown_sha256"]))
    asyncio.run(swarm_out.answer("h1", "sess-1", "req-1", decision="deny",
                                 shown_sha256=rec["shown_sha256"]))
    assert json.loads(s3.objects[ANS])["decision"] == "deny"


def test_dropped_questions_and_options_are_counted():
    rec = {"kind": "question", "session_id": "s", "request_id": "r", "asked_at": NOW,
           "questions": [{"question": "", "options": []}]
           + [{"question": f"q{i}", "options": [{"label": "a"}, {"label": ""}] + [{"label": f"o{j}"} for j in range(12)]}
              for i in range(22)]}
    out = swarm_out._clean_ask(rec, "worker-1")
    # 23 asked: 20 kept slots, one of them empty → 19 shown. Each shown question has 14
    # options: the first ten minus the empty label leave 9, so 5 are not listed
    assert len(out["questions"]) == 19
    assert out["omitted"] == {"questions": 4, "options": 19 * 5}


def test_a_dispatch_that_timed_out_is_recorded_without_a_session(patched, monkeypatch):
    async def never(client, message, repos=None, project_agent=None):
        await asyncio.sleep(10)
    monkeypatch.setattr(swarm_out, "_start_session", never)
    monkeypatch.setattr(swarm_out, "SESSION_ID_WAIT_S", 0.05)
    _use(monkeypatch, _fake_hive())
    with pytest.raises(swarm_out.SwarmOutMaybeSent, match="may be running"):
        asyncio.run(swarm_out.dispatch("worker-1", "Fix it", "do X"))
    rows = [json.loads(ln) for ln in (patched / "dispatches.jsonl").read_text().splitlines()]
    assert len(rows) == 1 and rows[0]["session_id"] is None and rows[0]["title"] == "Fix it"


@pytest.mark.parametrize("extra", ["bridge/worker-1/closed/sess-1/req-1.json", ANS])
def test_answer_refused_when_closed_or_already_answered(monkeypatch, extra):
    _answer_env(monkeypatch, {ASK: APPROVAL, extra: b"{}"})
    with pytest.raises(swarm_out.SwarmOutError):
        asyncio.run(swarm_out.answer("h1", "sess-1", "req-1", decision="approve"))


@pytest.mark.parametrize("sid,rid", [("../x", "req-1"), ("sess-1", "a/b"), ("", "req-1")])
def test_answer_refuses_malformed_ids(monkeypatch, sid, rid):
    _answer_env(monkeypatch, {})
    with pytest.raises(swarm_out.SwarmOutError):
        asyncio.run(swarm_out.answer("h1", sid, rid, decision="approve"))


# ── IAM: no Hive reads the whole bucket; the bridge is scoped to the Hive's own name ──

def _statements():
    return {s["Sid"]: s for s in provisioner.HIVE_IAM_POLICY["Statement"]}


def test_no_statement_grants_the_whole_bucket():
    for s in provisioner.HIVE_IAM_POLICY["Statement"]:
        res = s["Resource"] if isinstance(s["Resource"], list) else [s["Resource"]]
        if any(a.startswith("s3:") for a in s["Action"]):
            assert "arn:aws:s3:::swarmai-hive-*/*" not in res
            if "s3:ListBucket" in s["Action"]:
                assert "s3:prefix" in json.dumps(s.get("Condition", {}))


def test_bridge_statements_are_scoped_to_the_principal_tag():
    st = _statements()
    for sid in ("SwarmOutBridgeWrite", "SwarmOutBridgeRead"):
        for r in st[sid]["Resource"]:
            assert "/bridge/${aws:PrincipalTag/HiveName}/" in r
    assert all("/answer/" not in r for r in st["SwarmOutBridgeWrite"]["Resource"])


def test_role_policy_is_written_whole():
    calls = []

    tags = []

    class IAM:
        def put_role_policy(self, **kw):
            calls.append(kw)

        def tag_role(self, **kw):
            tags.append(kw)
    provisioner._put_hive_role_policy(IAM(), "SwarmAI-Hive-worker-1")
    assert calls[0]["RoleName"] == "SwarmAI-Hive-worker-1"
    assert json.loads(calls[0]["PolicyDocument"]) == provisioner.HIVE_IAM_POLICY
    # the bridge statements resolve ${aws:PrincipalTag/HiveName}: the role must carry it
    assert tags == [{"RoleName": "SwarmAI-Hive-worker-1", "Tags": [{"Key": "HiveName", "Value": "worker-1"}]}]


def test_two_answers_at_once_the_second_is_refused(monkeypatch):
    s3 = _answer_env(monkeypatch, {ASK: APPROVAL})
    real_get = s3.get_object
    # both callers pass the "already answered?" check before either writes
    monkeypatch.setattr(s3, "get_object", lambda Bucket, Key: real_get(Bucket, Key) if "/answer/" not in Key
                        else (_ for _ in ()).throw(s3.exceptions.NoSuchKey()))
    asyncio.run(swarm_out.answer("h1", "sess-1", "req-1", decision="approve", shown_sha256=SHOWN))
    with pytest.raises(swarm_out.SwarmOutError, match="already answered"):
        asyncio.run(swarm_out.answer("h1", "sess-1", "req-1", decision="deny", shown_sha256=SHOWN))
    assert json.loads(s3.objects[ANS])["decision"] == "approve"


def test_an_ask_older_than_the_longest_wait_is_refused(monkeypatch):
    old = json.dumps({"kind": "approval", "session_id": "sess-1", "request_id": "req-1",
                      "asked_at": "2020-01-01T00:00:00Z"}).encode()
    _answer_env(monkeypatch, {ASK: old})
    with pytest.raises(swarm_out.SwarmOutError, match="no longer open"):
        asyncio.run(swarm_out.answer("h1", "sess-1", "req-1", decision="approve"))


# ── Hive-written asks are rebuilt from known fields before they reach the webview ──

def test_clean_ask_drops_unknown_fields_and_takes_the_hive_name_from_the_host():
    rec = {"kind": "question", "hive": "prod-hive", "session_id": "s1", "request_id": "r1", "asked_at": NOW,
           "questions": [{"question": "x?"}], "html": "<script>"}
    out = swarm_out._clean_ask(rec, "worker-1")
    assert out["hive"] == "worker-1" and "html" not in out
    assert out["questions"] == [{"question": "x?", "header": "", "multiSelect": False, "options": []}]


@pytest.mark.parametrize("rec", [
    {"kind": "other", "session_id": "s1", "request_id": "r1"},
    {"kind": "approval", "session_id": "../s", "request_id": "r1"},
    {"kind": "question", "session_id": "s1", "request_id": "r1", "questions": []},
    "not a dict",
])
def test_clean_ask_refuses_malformed_records(rec):
    assert swarm_out._clean_ask(rec, "worker-1") is None


# ── routes ──

def test_routes_refuse_on_a_hive(monkeypatch):
    from fastapi.testclient import TestClient
    from fastapi import FastAPI
    from routers.swarm_out import router
    app = FastAPI()
    app.include_router(router, prefix="/api/swarm-out")
    monkeypatch.setenv("SWARMAI_MODE", "hive")
    c = TestClient(app)
    assert c.get("/api/swarm-out/runs").status_code == 403
    assert c.get("/api/swarm-out/hives").status_code == 403
    assert c.post("/api/swarm-out/dispatch", json={"hive": "h", "title": "t", "task": "x"}).status_code == 403


def test_an_unexpected_error_is_a_generic_500(monkeypatch):
    from fastapi.testclient import TestClient
    from fastapi import FastAPI
    from routers.swarm_out import router

    async def boom(max_age_s=0):
        raise RuntimeError("internal detail /secret/path")
    monkeypatch.setattr(swarm_out, "list_runs", boom)
    monkeypatch.delenv("SWARMAI_MODE", raising=False)
    app = FastAPI()
    app.include_router(router, prefix="/api/swarm-out")
    r = TestClient(app).get("/api/swarm-out/runs")
    assert r.status_code == 500 and "secret" not in r.text


# ── hives (the panel's "is there anywhere to dispatch to" and the agent's choice) ──

def test_hives_lists_names_and_status_without_addresses_or_credentials(monkeypatch, tmp_path):
    import sqlite3
    db = tmp_path / "d.db"
    with sqlite3.connect(db) as c:
        c.execute("CREATE TABLE hive_instances (id, name, status, region, cloudfront_domain,"
                  " auth_user, auth_password, account_ref, s3_bucket)")
        c.execute("INSERT INTO hive_instances VALUES (?,?,?,?,?,?,?,?,?)",
                  tuple(INST[k] for k in ("id", "name", "status", "region", "cloudfront_domain",
                                          "auth_user", "auth_password", "account_ref", "s3_bucket")))
        c.execute("INSERT INTO hive_instances VALUES ('h2','alpha','stopped','us-west-2',"
                  "'x.cloudfront.net','u','p2','a1','b')")
    monkeypatch.setattr(swarm_out, "_db_path", lambda: str(db))
    out = asyncio.run(swarm_out.hives())
    assert out == {"hives": [{"name": "alpha", "status": "stopped", "region": "us-west-2"},
                             {"name": "worker-1", "status": "running", "region": "us-east-1"}],
                   "running": 1}
    text = json.dumps(out)
    assert "s3cret-pass" not in text and "cloudfront" not in text


def test_cli_lists_hives():
    from scripts import swarm_out as cli
    calls = []
    orig = cli._call
    cli._call = lambda method, path, body=None, timeout=120: calls.append((method, path)) or 0
    try:
        assert cli.main(["hives"]) == 0
    finally:
        cli._call = orig
    assert calls == [("GET", "/hives")]


# ── list_runs: is each dispatched session running on its Hive now ──

def _runs_env(monkeypatch, tmp_path, state_handler, asks=()):
    rows = [{"dispatch_id": f"so_{i}", "hive_id": "h1", "hive": "worker-1", "session_id": sid,
             "title": sid, "origin": "swarm-out", "sent_at": NOW} for i, sid in enumerate(["sess-1", "sess-2", "sess-3"])]
    (tmp_path / "dispatches.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    monkeypatch.setattr(swarm_out, "_state_dir", lambda: tmp_path)
    _answer_env(monkeypatch, {})
    monkeypatch.setattr(swarm_out, "_open_asks", lambda s3, bucket, hive: [dict(a) for a in asks])
    _use(monkeypatch, state_handler)


OPEN_ASK = {"kind": "approval", "session_id": "sess-1", "request_id": "req-1", "asked_at": NOW, "hive": "worker-1"}


def test_list_runs_marks_the_sessions_streaming_on_the_hive(monkeypatch, tmp_path):
    # Questions: does a row say running only for a session the Hive reports streaming
    # (a waiting or finished session is not running), and does a Hive that cannot be
    # asked leave running unknown (None) instead of False, without losing its questions?
    def hive(request):
        if request.url.path == "/api/chat/sessions/streaming-state":
            return httpx.Response(200, json={"sessions": {
                "sess-1": {"streaming": True}, "sess-2": {"streaming": False, "waiting_input": True}}})
        return httpx.Response(404)
    _runs_env(monkeypatch, tmp_path, hive, [OPEN_ASK])
    out = asyncio.run(swarm_out.list_runs())
    by = {r["session_id"]: r for r in out["runs"]}
    assert by["sess-1"]["running"] is True
    assert by["sess-2"]["running"] is False      # not streaming (it may be waiting on you)
    assert by["sess-3"]["running"] is False      # the Hive no longer holds it: ended
    assert [a["session_id"] for a in out["asks"]] == ["sess-1"]


def test_list_runs_leaves_running_unknown_when_the_hive_does_not_answer(monkeypatch, tmp_path):
    def down(request):
        raise httpx.ConnectError("unreachable")
    _runs_env(monkeypatch, tmp_path, down, [OPEN_ASK])
    out = asyncio.run(swarm_out.list_runs())
    by = {r["session_id"]: r for r in out["runs"]}
    # the Hive did not say: an open mirrored question still says waiting, the rest unknown
    assert by["sess-1"]["state"] == "waiting" and by["sess-1"]["running"] is False
    assert by["sess-2"]["running"] is None and by["sess-3"]["state"] == "unknown"
    assert len(out["asks"]) == 1                  # questions still listed

    def broken(request):
        return httpx.Response(502)
    _runs_env(monkeypatch, tmp_path, broken)
    assert all(r["running"] is None for r in asyncio.run(swarm_out.list_runs())["runs"])
