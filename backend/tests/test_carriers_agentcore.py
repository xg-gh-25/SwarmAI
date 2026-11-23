"""The AgentCore carrier (design docs/design/2026-11-20-swarm-out-multi-carrier.md §6;
requirements R5.1-R5.6 of run_fd94fed6). No network: boto3 clients are fakes, HTTP goes
to an httpx.MockTransport.

Questions:
- does a round's client refuse, with no request sent, once the round closed in S3, was
  replaced, was released or outlived the carrier's lifetime;
- is every request signed SigV4 for bedrock-agentcore with the empty-payload hash and
  carries the run credential in X-Swarm-Hive-Auth, the credential kept here only (0600);
- does a throttled or at-quota start raise LocationUnavailable, a refused one an ordinary
  error;
- does the offer say ready / not_ready / image_version / at_quota, and leave out an
  account this Host cannot manage;
- does a record read from S3 keep the same entries (seq, kind, sha256, size, key) as the
  same record read the HTTP way, and a later round set the earlier one aside;
- are two runs' usage rows both stored, once each;
- is release idempotent;
- does a correction after the round ended start a new session at the returned commits
  without one request to the old session;
- does the execution role reach only its own session's prefix, answer/ read-only;
- does migration v21 add the two columns.
"""
from __future__ import annotations

import base64
import io
import json
import os
import sqlite3
import subprocess
import time
from collections import namedtuple
from pathlib import Path

import httpx
import pytest
from botocore.exceptions import ClientError

from core import hive_bundle, hive_records, hive_repo_inbox, swarm_out
from core.carriers import agentcore as ac
from core.carriers.base import AGENTCORE, TIER_ELASTIC, LocationUnavailable, Needs, RunHandle

DID = "so_0123456789ab"
DID2 = "so_ba9876543210"
BUCKET = "swarmai-hive-123456789012-us-east-1"
ARN = "arn:aws:bedrock-agentcore:us-east-1:123456789012:runtime/swarm_hive-AbC123"
Frozen = namedtuple("Frozen", "access_key secret_key token")
CREDS = Frozen("AKIDEXAMPLE", "secretsecretsecret", "sessiontoken")


def _err(code, op="Op"):
    return ClientError({"Error": {"Code": code, "Message": code}}, op)


class FakeS3:
    def __init__(self):
        self.objects: dict[str, bytes] = {}
        self.calls: list[tuple] = []

    def put(self, key, data):
        self.objects[key] = data if isinstance(data, bytes) else json.dumps(data).encode()

    def get_object(self, Bucket, Key, **kw):
        self.calls.append(("get", Key))
        assert Bucket == BUCKET
        if Key not in self.objects:
            raise _err("NoSuchKey", "GetObject")
        data = self.objects[Key]
        return {"Body": io.BytesIO(data), "ContentLength": len(data)}

    def list_objects_v2(self, Bucket, Prefix, StartAfter=None, MaxKeys=1000, ContinuationToken=None, **kw):
        self.calls.append(("list", Prefix))
        keys = sorted(k for k in self.objects if k.startswith(Prefix) and (StartAfter is None or k > StartAfter))
        start = int(ContinuationToken or 0)
        page = keys[start:start + MaxKeys]
        more = start + MaxKeys < len(keys)
        return {"Contents": [{"Key": k} for k in page], "IsTruncated": more,
                **({"NextContinuationToken": str(start + MaxKeys)} if more else {})}


class FakeAgentCore:
    def __init__(self):
        self.invokes: list[dict] = []
        self.stops: list[dict] = []
        self.invoke_error = None
        self.answer = {"started": True}
        self.stop_errors: list = []
        self.url = None                        # the session endpoint to answer, when set

    def invoke_agent_runtime(self, **kw):
        self.invokes.append(kw)
        if self.invoke_error:
            raise _err(self.invoke_error, "InvokeAgentRuntime")
        rnd = int(kw["runtimeSessionId"].rsplit("-", 1)[1])
        url = self.url or f"https://sess-{rnd}.runtime.bedrock-agentcore.us-east-1.on.aws"
        return {"response": io.BytesIO(json.dumps(self.answer).encode()), "statusCode": 200,
                "ResponseMetadata": {"HTTPHeaders": {ac.ENDPOINT_HEADER: url}}}

    def stop_runtime_session(self, **kw):
        self.stops.append(kw)
        if self.stop_errors:
            raise _err(self.stop_errors.pop(0), "StopRuntimeSession")
        return {"statusCode": 200}


class FakeControl:
    def __init__(self):
        self.status = "READY"
        self.image = "123456789012.dkr.ecr.us-east-1.amazonaws.com/swarmai-hive:2.5.5"

    def get_agent_runtime(self, agentRuntimeId):
        assert agentRuntimeId == "swarm_hive-AbC123"
        return {"status": self.status, "agentRuntimeArtifact": {"containerConfiguration": {"containerUri": self.image}}}


class FakeQuotas:
    value = 10

    def list_service_quotas(self, **kw):
        return {"Quotas": [{"QuotaName": "Active session workloads per account", "Value": float(self.value)}]}


class FakeSession:
    def __init__(self, world):
        self.world = world

    def client(self, service, region_name=None, config=None):
        return {"s3": self.world.s3, ac.SERVICE: self.world.data, ac.CONTROL_SERVICE: self.world.control,
                "service-quotas": self.world.quotas}[service]

    def get_credentials(self):
        return namedtuple("C", "get_frozen_credentials")(lambda: CREDS)


class World:
    def __init__(self, tmp_path):
        self.tmp = tmp_path
        self.s3, self.data, self.control, self.quotas = FakeS3(), FakeAgentCore(), FakeControl(), FakeQuotas()
        self.purposes: list[tuple] = []
        self.requests: list[httpx.Request] = []
        self.bodies: list[bytes] = []
        self.handler = lambda request: httpx.Response(200, json={"ok": True})
        self.refused_accounts: set = set()

    def transport(self):
        def handle(request):
            self.requests.append(request)
            self.bodies.append(request.content)
            return self.handler(request)
        return httpx.MockTransport(handle)


def _make_db(path: Path):
    """A database at the current schema: every migration ran (v21 included)."""
    import asyncio
    from database.sqlite import SQLiteDatabase, close_all_pools

    async def go():
        await SQLiteDatabase(db_path=str(path)).initialize()
        await close_all_pools()
    asyncio.run(go())


def _insert_location(db: Path, lid="ac1", name="elastic", account="acct1", arn=ARN, carrier="agentcore"):
    now = "2026-11-20T00:00:00Z"
    with sqlite3.connect(db) as c:
        c.execute("INSERT OR IGNORE INTO hive_accounts (id, account_id, label, auth_method, created_at) "
                  "VALUES (?, '123456789012', '', 'access_keys', ?)", (account, now))
        c.execute("INSERT INTO hive_instances (id, name, account_ref, region, s3_bucket, status, created_at, "
                  "updated_at, carrier, runtime_arn) VALUES (?, ?, ?, 'us-east-1', ?, 'running', ?, ?, ?, ?)",
                  (lid, name, account, BUCKET, now, now, carrier, arn))


@pytest.fixture
def world(tmp_path, monkeypatch):
    import database
    w = World(tmp_path)
    db = tmp_path / "data.db"
    _make_db(db)
    _insert_location(db)
    w.db = db
    monkeypatch.setattr(database.db, "db_path", db)
    state = tmp_path / "state"
    state.mkdir()
    monkeypatch.setattr(swarm_out, "_state_dir", lambda: state)
    monkeypatch.setattr(swarm_out, "_read_log", lambda: [])
    monkeypatch.setattr(ac, "_host_version", lambda: "2.5.5")
    ac._quota_cache.clear()
    ac._manifest_seen.clear()

    def session_for(acct, region, purpose):
        w.purposes.append((acct["id"], region, purpose))
        if acct["id"] in w.refused_accounts:
            from hive.accounts import OtherAccountError
            raise OtherAccountError("another account")
        return FakeSession(w)
    monkeypatch.setattr(ac, "_session_for", session_for)
    w.state = state
    w.carrier = ac.AgentCoreCarrier(transport=w.transport())
    return w


async def _open(w, did=DID, rnd=1):
    return await w.carrier.location("ac1").open_run(did, rnd, 400.0)


def _close_in_s3(w, sid):
    w.s3.put(f"bridge/{sid}/records/manifest/000001.json", {"seq": 1, "kind": "closed"})
    w.s3.put(f"bridge/{sid}/records/000001-closed.json", b"{}")


# ── client ───────────────────────────────────────────────────────────────────────

async def test_client_refuses_after_closed_or_lifetime(world):
    h = await _open(world)
    # past the carrier's lifetime: refused at once, before a client exists
    old = RunHandle(**{**h.to_row(), "opened_at": time.time() - ac.MAX_RUN_S - 1})
    with pytest.raises(ac.RunEnded) as e:
        world.carrier.client(old, 5)
    assert e.value.why == "carrier_expired"
    near = RunHandle(**{**h.to_row(), "opened_at": time.time() - (ac.MAX_RUN_S - ac.END_MARGIN_S) - 1})
    with pytest.raises(ac.RunEnded):                       # the margin before the platform's end
        world.carrier.client(near, 5)

    # one request while open, then closed in S3: the very next send is refused
    c = world.carrier.client(h, 5)
    async with c:
        assert (await c.get("/api/chat/sessions/admission-state")).status_code == 200
        _close_in_s3(world, h.run_ref)
        with pytest.raises(ac.RunEnded) as e:
            await c.get("/api/chat/sessions/admission-state")
    assert e.value.why == "closed" and len(world.requests) == 1
    world.requests.clear()
    with pytest.raises(ac.RunEnded):                      # and from now on refused at once
        world.carrier.client(h, 5)

    # replaced (state.json): the same
    h2 = await _open(world, DID2)
    world.s3.put(f"bridge/{h2.run_ref}/state.json", {"state": "replaced"})
    c2 = world.carrier.client(h2, 5)
    with pytest.raises(ac.RunEnded) as e:
        async with c2:
            async with c2.stream("GET", "/api/hive/records/x"):
                pass
    assert e.value.why == "carrier_replaced"
    # state.json ended: the same
    h3 = await _open(world, DID, 3)
    world.s3.put(f"bridge/{h3.run_ref}/state.json", {"state": "ended"})
    with pytest.raises(ac.RunEnded):
        async with world.carrier.client(h3, 5) as c3:
            await c3.get("/x")
    assert world.requests == []                            # not one request was sent


async def test_an_unreadable_end_marker_is_an_end(world):
    h = await _open(world)
    ac._ended_path(h.run_ref).write_text("{torn")
    with pytest.raises(ac.RunEnded):
        world.carrier.client(h, 5)


async def test_s3_not_answering_sends_nothing(world, monkeypatch):
    h = await _open(world)

    def down(**kw):
        raise _err("SlowDown", "ListObjectsV2")
    monkeypatch.setattr(world.s3, "list_objects_v2", down)
    with pytest.raises(ClientError):
        async with world.carrier.client(h, 5) as c:
            await c.get("/x")
    assert world.requests == []


async def test_no_redirect_and_no_other_host(world):
    h = await _open(world)
    async with world.carrier.client(h, 5) as c:
        with pytest.raises(ac.AgentCoreError):
            await c.get("/x", follow_redirects=True)
        world.s3.calls.clear()
        with pytest.raises(ac.AgentCoreError):
            await c.get("https://evil.example.com/x")
        assert world.s3.calls == []                        # refused before anything is looked up
    assert world.requests == []


def test_the_run_credential_never_reaches_botocore_debug_logs(caplog):
    import logging
    mark = "never-" + "logged"
    with caplog.at_level(logging.DEBUG):
        logging.getLogger("botocore.endpoint").debug(
            "Making request for %s with params: %s", "InvokeAgentRuntime",
            {"body": json.dumps({"action": "start", "hive_" + "password": mark}).encode()})
        logging.getLogger("botocore.endpoint").debug("Response headers: %s", {"a": "b"})
    assert mark not in caplog.text and "Response headers" in caplog.text


async def test_a_credential_file_not_private_or_linked_is_refused(world):
    await _open(world)
    path = world.state / "run_creds" / f"{DID}-1.json"
    os.chmod(path, 0o640)
    with pytest.raises(ac.AgentCoreError):
        ac.run_credential(DID, 1)
    os.chmod(path, 0o600)
    real = world.tmp / "elsewhere.json"
    real.write_text(path.read_text())
    os.chmod(real, 0o600)
    path.unlink()
    path.symlink_to(real)
    with pytest.raises(OSError):
        ac.run_credential(DID, 1)


async def test_a_closed_file_without_its_manifest_line_is_not_an_end(world):
    h = await _open(world)
    world.s3.put(f"bridge/{h.run_ref}/records/000001-closed.json", b"{}")   # file, no manifest line yet
    async with world.carrier.client(h, 5) as c:
        assert (await c.get("/x")).status_code == 200
        world.s3.put(f"bridge/{h.run_ref}/records/manifest/000001.json", {"seq": 1, "kind": "closed"})
        with pytest.raises(ac.RunEnded):
            await c.get("/x")
    assert len(world.requests) == 1


async def test_a_closed_entry_after_many_others_is_found(world):
    h = await _open(world)
    for i in range(1, 1201):
        world.s3.put(f"bridge/{h.run_ref}/records/{i:06d}-turn.json", b"{}")
        world.s3.put(f"bridge/{h.run_ref}/records/manifest/{i:06d}.json", {"seq": i, "kind": "turn"})
    assert await world.carrier.ended(h) is None
    world.s3.put(f"bridge/{h.run_ref}/records/manifest/001201.json", {"seq": 1201, "kind": "closed"})
    world.s3.calls.clear()
    assert await world.carrier.ended(h) == "closed"
    assert len([c for c in world.s3.calls if c[0] == "get"]) == 2     # state.json and the one new line
    h2 = await _open(world, DID2)                                       # all there before the first check
    for i in range(1, 1201):
        world.s3.put(f"bridge/{h2.run_ref}/records/manifest/{i:06d}.json", {"seq": i, "kind": "turn"})
    world.s3.put(f"bridge/{h2.run_ref}/records/manifest/001201.json", {"seq": 1201, "kind": "closed"})
    assert await world.carrier.ended(h2) == "closed"


async def test_requests_are_signed_and_carry_the_run_credential(world):
    from botocore.auth import SigV4Auth
    from botocore.awsrequest import AWSRequest
    from botocore.credentials import Credentials
    h = await _open(world)
    assert "password" not in json.dumps(h.to_row()) and h.auth_ref == f"run_cred:{DID}:1"
    path = world.state / "run_creds" / f"{DID}-1.json"
    assert path.stat().st_mode & 0o777 == 0o600
    cred = json.loads(path.read_text())
    async with world.carrier.client(h, 5) as c:
        r = await c.put("/api/hive/repos", params={"url": "https://github.com/a/b"}, content=b"bundle bytes")
    assert r.status_code == 200
    req = world.requests[0]
    assert req.url.host == "8081--sess-1.runtime.bedrock-agentcore.us-east-1.on.aws"
    assert req.headers[ac.AUTH_HEADER] == "Basic " + base64.b64encode(
        f"{cred['user']}:{cred['password']}".encode()).decode()
    assert "x-amz-content-sha256" not in req.headers
    auth = req.headers["authorization"]
    assert auth.startswith("AWS4-HMAC-SHA256 Credential=AKIDEXAMPLE/") and "/us-east-1/bedrock-agentcore/aws4_request" in auth
    assert req.headers["x-amz-security-token"] == "sessiontoken"
    # the signature is the one botocore computes over the method, URL and host with the
    # empty payload (what the platform checks when no content hash header is sent)
    again = AWSRequest(method="PUT", url=str(req.url), headers={"host": req.url.host}, data=b"")
    # add_auth takes its own clock, not context["timestamp"]: sign at the request's own
    # time, or the two signatures differ whenever a second passes between them
    from unittest import mock
    from datetime import datetime
    signed_at = datetime.strptime(req.headers["x-amz-date"], "%Y%m%dT%H%M%SZ")
    with mock.patch("botocore.auth.get_current_datetime", return_value=signed_at):
        SigV4Auth(Credentials(*CREDS), "bedrock-agentcore", "us-east-1").add_auth(again)
    assert again.headers["Authorization"] == auth
    assert req.headers["accept-encoding"] == "identity"


# ── open ─────────────────────────────────────────────────────────────────────────

async def test_open_run_starts_the_derived_session_with_the_run_credential(world, monkeypatch):
    clock = iter([1000.0, 1100.0, 1200.0])
    monkeypatch.setattr(ac, "_now", lambda: next(clock))
    h = await _open(world)
    assert h.opened_at == 1000.0                           # taken before the start call
    monkeypatch.setattr(ac, "_now", time.time)
    kw = world.data.invokes[0]
    payload = json.loads(kw["payload"])
    cred = ac.run_credential(DID, 1)
    assert payload == {"action": "start", "sid": h.run_ref, "bucket": BUCKET,
                       "hive_user": cred["user"], "hive_password": cred["password"]}
    assert kw["runtimeSessionId"] == h.run_ref == ac.session_id_for(DID, 1) and len(h.run_ref) >= 33
    assert kw["agentRuntimeArn"] == ARN
    assert (h.carrier, h.location_id, h.round) == (AGENTCORE, "ac1", 1)
    assert h.address == "https://sess-1.runtime.bedrock-agentcore.us-east-1.on.aws"
    # a start sent again for the same round carries the same credential
    await _open(world)
    assert json.loads(world.data.invokes[1]["payload"])["hive_password"] == cred["password"]


@pytest.mark.parametrize("code", ["ThrottlingException", "ServiceQuotaExceededException"])
async def test_throttled_or_at_quota_start_is_unavailable(world, code):
    world.data.invoke_error = code
    with pytest.raises(LocationUnavailable):
        await _open(world)


async def test_a_refused_start_is_an_ordinary_error(world):
    world.data.answer = {"started": False, "why": "replaced"}
    with pytest.raises(ac.AgentCoreError) as e:
        await _open(world)
    assert not isinstance(e.value, LocationUnavailable) and "replaced" in str(e.value)
    world.data.answer, world.data.invoke_error = {"started": True}, "AccessDeniedException"
    with pytest.raises(ac.AgentCoreError):
        await _open(world)


async def test_an_endpoint_off_aws_is_refused(world, monkeypatch):
    monkeypatch.setattr(world.data, "invoke_agent_runtime", lambda **kw: {
        "response": io.BytesIO(b'{"started": true}'),
        "ResponseMetadata": {"HTTPHeaders": {ac.ENDPOINT_HEADER: "https://evil.example.com"}}})
    with pytest.raises(ac.AgentCoreError):
        await _open(world)


# ── offers ───────────────────────────────────────────────────────────────────────

async def test_offers_ready_and_blocked(world):
    [o] = await world.carrier.offers(Needs())
    assert (o.carrier, o.tier, o.start_s, o.blocked, o.brain_lock_scope, o.reaches_private) == \
        (AGENTCORE, TIER_ELASTIC, 60.0, None, "run", False)
    world.control.status = "UPDATING"
    assert (await world.carrier.offers(Needs()))[0].blocked == "not_ready"
    world.control.status, world.control.image = "READY", world.control.image.replace("2.5.5", "2.5.4")
    assert (await world.carrier.offers(Needs()))[0].blocked == "image_version"
    world.control.image = world.control.image.replace("2.5.4", "2.5.5")
    ac._quota_cache.clear()
    world.quotas.value = 1
    h = await _open(world)
    import core.swarm_out as so
    so._read_log = lambda: [{"dispatch_id": DID, "rounds": [h.to_row()]}]
    try:
        assert (await world.carrier.offers(Needs()))[0].blocked == "at_quota"
    finally:
        so._read_log = lambda: []
    world.refused_accounts.add("acct1")
    assert await world.carrier.offers(Needs()) == []        # another account: not offered


def test_carrier_capabilities():
    c = ac.AgentCoreCarrier()
    assert (c.kind, c.reaches_private, c.max_run_s, c.brain_lock_scope) == ("agentcore", False, 28800, "run")


# ── outputs ──────────────────────────────────────────────────────────────────────

def _hive_record(ws: Path, sid: str):
    key = hive_bundle.key("demo")
    hive_records.append(ws, sid, "bundle", data=b"PACK" * 1000, key=key)
    hive_records.append(ws, sid, "turn", data=json.dumps({"rows": [], "first_turn": True}).encode())
    hive_records.append(ws, sid, "lessons", data=json.dumps({"lessons": [{"text": "x"}]}).encode())
    hive_records.append(ws, sid, "turn", data=json.dumps({"rows": [{"id": "r2"}]}).encode())
    hive_records.append(ws, sid, "closed", data=b"{}")


def _to_s3(w, ws: Path, sid: str):
    d = hive_records.session_dir(ws, sid)
    for e in hive_records._read_manifest(d):
        w.s3.put(f"bridge/{sid}/records/manifest/{e['seq']:06d}.json", e)
        w.s3.put(f"bridge/{sid}/records/{e['file']}", (d / e["file"]).read_bytes())


def _kept(did):
    return [(e["seq"], e["kind"], e["sha256"], e["size"], e.get("key")) for e in hive_records.local_entries(did)]


async def test_s3_outputs_equal_the_http_read(world, monkeypatch):
    ws = world.tmp / "hive-ws"
    h = await _open(world, DID2)
    sid = h.run_ref
    _hive_record(ws, sid)

    def serve(request):
        parts = request.url.path.split("/")
        if len(parts) == 5:
            return httpx.Response(200, json=hive_records.entries(ws, sid, int(request.url.params["after"])))
        fd, _size = hive_records.open_entry(ws, sid, int(parts[5]))
        with os.fdopen(fd, "rb") as f:
            return httpx.Response(200, content=f.read())
    monkeypatch.setattr(swarm_out, "_client", lambda inst, timeout=20: httpx.AsyncClient(
        transport=httpx.MockTransport(serve), base_url="https://demo.cloudfront.net"))
    by_http = await hive_records.pull({"dispatch_id": DID, "session_id": sid}, {})
    _to_s3(world, ws, sid)
    world.s3.put(f"bridge/{sid}/checks.json", {"checks": [{"id": "backend-bvt", "exit": 0, "started_at": "a",
                                                           "ended_at": "b", "noise": "x"}, {"id": 5}]})
    by_s3 = await world.carrier.outputs(h, DID2, row={"dispatch_id": DID2})
    assert _kept(DID) == _kept(DID2) and len(_kept(DID)) == 5
    assert {k: by_http[k] for k in ("supported", "seq", "closed", "error")} == \
        {k: by_s3[k] for k in ("supported", "seq", "closed", "error")} == \
        {"supported": True, "seq": 5, "closed": True, "error": None}
    assert by_s3["check_records"] == [{"id": "backend-bvt", "exit": 0, "started_at": "a", "ended_at": "b"}]
    assert hive_records.bundle_path(DID2, 1, hive_bundle.key("demo")).read_bytes() == b"PACK" * 1000
    assert world.requests == []                                     # read from S3, never the session
    # nothing new: nothing kept twice
    await world.carrier.outputs(h, DID2, row={"dispatch_id": DID2})
    assert len(_kept(DID2)) == 5


async def test_s3_outputs_stop_at_a_bad_entry_and_keep_what_passed(world):
    ws = world.tmp / "hive-ws"
    h = await _open(world)
    _hive_record(ws, h.run_ref)
    _to_s3(world, ws, h.run_ref)
    key = f"bridge/{h.run_ref}/records/000003-lessons.json"
    world.s3.objects[key] = b"tampered"
    got = await world.carrier.outputs(h, DID, row={"dispatch_id": DID})
    assert got["error"] == "sha_mismatch" and got["seq"] == 2


async def test_a_later_round_sets_the_earlier_record_aside(world):
    ws = world.tmp / "hive-ws"
    h1 = await _open(world)
    _hive_record(ws, h1.run_ref)
    _to_s3(world, ws, h1.run_ref)
    await world.carrier.outputs(h1, DID, row={"dispatch_id": DID})
    first = _kept(DID)
    h2 = await _open(world, DID, 2)
    hive_records.append(ws, h2.run_ref, "turn", data=b'{"rows": []}')
    _to_s3(world, ws, h2.run_ref)
    got = await world.carrier.outputs(h2, DID, row={"dispatch_id": DID})
    assert got["seq"] == 1 and got["error"] is None and [k for _s, k, *_ in _kept(DID)] == ["turn"]
    aside = hive_records.local_dir(DID) / "rounds" / "1"
    assert [(e["seq"], e["kind"], e["sha256"], e["size"], e.get("key"))
            for e in hive_records._read_manifest(aside)] == first
    # the earlier round is not read over the later one
    assert (await world.carrier.outputs(h1, DID, row={"dispatch_id": DID}))["error"] == "older_round"


# ── state ────────────────────────────────────────────────────────────────────────

async def test_state_from_s3_and_age(world):
    h = await _open(world)
    assert await world.carrier.state(h) == ("unknown", "starting")
    world.s3.put(f"bridge/{h.run_ref}/state.json", {"state": "waiting"})
    assert await world.carrier.state(h) == ("waiting", "waiting_input")
    _close_in_s3(world, h.run_ref)
    assert await world.carrier.state(h) == ("stopped", "closed")
    h2 = await _open(world, DID2)
    old = RunHandle(**{**h2.to_row(), "opened_at": time.time() - ac.MAX_RUN_S - 1})
    assert await world.carrier.state(old) == ("stopped", "carrier_expired")


# ── usage ────────────────────────────────────────────────────────────────────────

def _usage_row(i):
    return {"id": i, "at_utc": "2026-11-20T09:00:00+00:00", "surface": "chat", "session_id": "s1",
            "model": "opus", "input": 1, "output": 10, "cache_read": 100, "cache_write": 5, "cost_usd": 0.5,
            "cost_source": "cli", "kind": "spend", "imported": 0}


async def test_two_runs_usage_rows_both_stored(world):
    h1, h2 = await _open(world, DID), await _open(world, DID2)
    for h in (h1, h2):
        world.s3.put(f"bridge/{h.run_ref}/usage.json", {"rows": [_usage_row(1), _usage_row(2)]})
    assert (await world.carrier.usage(h1, DID))["rows"] == 2
    assert (await world.carrier.usage(h2, DID2))["rows"] == 2
    assert (await world.carrier.usage(h1, DID))["stored"]            # read again: stored once
    h3 = await _open(world, DID, 2)                                    # a later round numbers from 1 again
    world.s3.put(f"bridge/{h3.run_ref}/usage.json", {"rows": [_usage_row(1)]})
    await world.carrier.usage(h3, DID)
    from core import usage_accounting
    usage_accounting.flush(5)
    with sqlite3.connect(world.db) as c:
        got = sorted(c.execute("SELECT dedup_key, dispatch_id FROM usage_ledger").fetchall())
    assert got == [(f"hive:{DID}.r2:1", DID), (f"hive:{DID}:1", DID), (f"hive:{DID}:2", DID),
                   (f"hive:{DID2}:1", DID2), (f"hive:{DID2}:2", DID2)]


# ── release ──────────────────────────────────────────────────────────────────────

async def test_release_is_idempotent(world):
    h = await _open(world)
    await world.carrier.release(h, DID)
    world.data.stop_errors = ["ResourceNotFoundException"]
    await world.carrier.release(h, DID)                                 # gone already: done
    assert [s["runtimeSessionId"] for s in world.data.stops] == [h.run_ref, h.run_ref]
    assert not (world.state / "run_creds" / f"{DID}-1.json").exists()
    with pytest.raises(ac.RunEnded):
        world.carrier.client(h, 5)
    h2 = await _open(world, DID2)
    world.data.stop_errors = ["AccessDeniedException"]
    with pytest.raises(ac.AgentCoreError):
        await world.carrier.release(h2, DID2)
    assert not (world.state / "run_creds" / f"{DID2}-1.json").exists()   # ended here: forgotten anyway
    assert ("acct1", "us-east-1", "stop") in world.purposes


# ── a correction after the round ended (R5.5) ────────────────────────────────────

def _git(cwd, *args):
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=cwd,
                          capture_output=True, text=True, check=True).stdout.strip()


def _returned_repo(w, did):
    repo = w.tmp / "checkout"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    (repo / "a.txt").write_text("1")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "one")
    (repo / "a.txt").write_text("2")
    _git(repo, "commit", "-qam", "the round's commit")
    returned = _git(repo, "rev-parse", "HEAD")
    url = "https://github.com/example/demo"
    key = hive_bundle.key("demo")
    _git(repo, "update-ref", f"refs/hive/{did}/{key}", returned)
    (repo / "a.txt").write_text("3")
    _git(repo, "commit", "-qam", "HEAD moved on")                     # pinned: HEAD is not what ships
    d = w.state / "returns" / did
    d.mkdir(parents=True)
    (d / f"{key}.json").write_text(json.dumps({"url": url, "key": key, "repo": str(repo), "branch": "main",
                                               "ref": f"refs/hive/{did}/{key}", "tip": returned}))
    return repo, url, returned


async def test_open_next_round_never_calls_the_old_session(world):
    prev = await _open(world)
    repo, url, returned = _returned_repo(world, DID)
    row = {"dispatch_id": DID, "repos": [{"url": url, "ref": "main", "name": "demo"}], "rounds": [prev.to_row()]}
    with pytest.raises(ac.AgentCoreError):                               # still open: into the same session
        await world.carrier.open_next_round(row, "fix the typo")
    assert len(world.data.invokes) == 1

    _close_in_s3(world, prev.run_ref)
    heads = []

    def keep(request):
        f = world.tmp / "got.bundle"
        f.write_bytes(request.content)
        heads.append(subprocess.run(["git", "bundle", "list-heads", str(f)], cwd=repo, capture_output=True,
                                    text=True, check=True).stdout.split()[0])
        return httpx.Response(200, json={"bundle": hive_repo_inbox.bundle_rel(request.url.params["sha"]),
                                         "bytes": len(request.content)})
    world.handler = keep
    h = await world.carrier.open_next_round(row, "fix the typo")
    assert (h.round, h.run_ref) == (2, ac.session_id_for(DID, 2))
    assert world.data.invokes[-1]["runtimeSessionId"] == h.run_ref
    assert heads == [returned]                                          # the new round starts at the returned commit
    assert h.extra["repos"] == [{"url": url, "ref": "main", "sha": returned, "name": "demo"}]
    hosts = {r.url.host for r in world.requests}
    assert hosts == {"8081--sess-2.runtime.bedrock-agentcore.us-east-1.on.aws"}   # never sess-1
    assert world.data.stops == []


# ── provisioning ─────────────────────────────────────────────────────────────────

def test_execution_role_policy_scopes_session_prefix():
    p = ac.execution_role_policy("123456789012", "us-east-1", BUCKET, "swarmai-hive")
    session = f"arn:aws:s3:::{BUCKET}/bridge/${{aws:AgentCoreRuntimeSourceSid}}/"
    allows = [s for s in p["Statement"] if s["Effect"] == "Allow"]
    s3 = [s for s in allows if any(a.startswith("s3:") for a in s["Action"])]
    for s in s3:
        for r in s["Resource"]:
            if s["Action"] == ["s3:ListBucket"]:
                assert r == f"arn:aws:s3:::{BUCKET}"
                assert s["Condition"] == {"StringLike": {"s3:prefix": ["bridge/${aws:AgentCoreRuntimeSourceSid}/*"]}}
            else:
                assert r.startswith(session)
    puts = [r for s in s3 if "s3:PutObject" in s["Action"] for r in s["Resource"]]
    assert puts and not any("/answer" in r for r in puts) and not any(r == session + "*" for r in puts)
    assert [r for s in s3 if "s3:GetObject" in s["Action"] for r in s["Resource"]] == [session + "*"]
    deny = [s for s in p["Statement"] if s["Effect"] == "Deny"]
    assert deny and deny[0]["Resource"] == [session + "answer/*"] and "s3:PutObject" in deny[0]["Action"]
    actions = {a for s in allows for a in s["Action"]}
    assert {"bedrock:InvokeModel", "ecr:BatchGetImage", "logs:PutLogEvents"} <= actions
    assert not any(a in ("s3:*", "*") or a.startswith("iam:") for a in actions)
    bedrock = next(s for s in allows if "bedrock:InvokeModel" in s["Action"])
    assert bedrock["Resource"] != "*" and all(":bedrock:" in r for r in bedrock["Resource"])
    pull = next(s for s in allows if "ecr:BatchGetImage" in s["Action"])
    assert pull["Resource"] == ["arn:aws:ecr:us-east-1:123456789012:repository/swarmai-hive"]
    with pytest.raises(ValueError):
        ac.execution_role_policy("123", "us-east-1", BUCKET, "swarmai-hive")
    with pytest.raises(ValueError):
        ac.execution_role_policy("123456789012", "us-east-1", "b/*", "swarmai-hive")


# ── pieces ───────────────────────────────────────────────────────────────────────

def test_session_ids_and_port_urls():
    a = ac.session_id_for(DID, 1)
    assert a == ac.session_id_for(DID, 1) != ac.session_id_for(DID, 2) and len(a) >= 33
    assert hive_records.SESSION_RE.fullmatch(a)
    with pytest.raises(ValueError):
        ac.session_id_for("not-a-dispatch", 1)
    dom = "runtime.bedrock-agentcore.us-east-1.on.aws"      # the form InvokeAgentRuntime returns
    assert ac.port_url(f"https://abc.{dom}/p/", "us-east-1") == f"https://8081--abc.{dom}/p"
    assert ac.port_url(f"https://8080--abc.{dom}", "us-east-1") == f"https://8081--abc.{dom}"
    for bad in (f"http://abc.{dom}", f"https://u:p@abc.{dom}", f"https://abc.{dom}:444", f"https://{dom}",
                "https://abc.evil.com", f"https://abc.{dom}.evil.com",
                "https://ec2-1-2-3-4.compute-1.amazonaws.com", "https://abcd.execute-api.us-east-1.amazonaws.com",
                "https://abcd.lambda-url.us-east-1.on.aws", f"https://abc.x.{dom}",
                "https://abc.bedrock-agentcore.us-east-1.amazonaws.com",
                "https://abc.runtime.bedrock-agentcore.us-west-2.on.aws"):
        with pytest.raises(ac.AgentCoreError):
            ac.port_url(bad, "us-east-1")


def test_migration_v21_adds_carrier_columns(tmp_path):
    from database.sqlite import CURRENT_SCHEMA_VERSION
    db = tmp_path / "m.db"
    _make_db(db)
    with sqlite3.connect(db) as c:
        cols = {r[1]: r for r in c.execute("PRAGMA table_info(hive_instances)")}
        assert cols["carrier"][2] == "TEXT" and cols["carrier"][3] == 1 and cols["carrier"][4] == "'ec2'"
        assert "runtime_arn" in cols
        assert c.execute("PRAGMA user_version").fetchone()[0] == CURRENT_SCHEMA_VERSION >= 21
        c.execute("INSERT INTO hive_accounts (id, account_id, created_at) VALUES ('a', '1', 'now')")
        c.execute("INSERT INTO hive_instances (id, name, account_ref, created_at, updated_at) "
                  "VALUES ('i', 'n', 'a', 'now', 'now')")
        assert c.execute("SELECT carrier, runtime_arn FROM hive_instances").fetchone() == ("ec2", None)
        c.execute("PRAGMA user_version = 20")          # an existing v20 database: added once, rows kept
    _make_db(db)
    with sqlite3.connect(db) as c:
        assert c.execute("SELECT carrier FROM hive_instances").fetchone() == ("ec2",)
        assert c.execute("PRAGMA user_version").fetchone()[0] == CURRENT_SCHEMA_VERSION


async def test_a_started_session_with_an_unusable_endpoint_is_stopped(world):
    # the start went through, so a microVM runs: refusing its endpoint must not leave it
    # running, unreachable, until its idle timeout
    world.data.url = "https://sess-1.bedrock-agentcore.us-east-1.amazonaws.com"
    with pytest.raises(ac.AgentCoreError, match="unexpected session endpoint"):
        await _open(world)
    sid = world.data.invokes[0]["runtimeSessionId"]
    assert [k["runtimeSessionId"] for k in world.data.stops] == [sid]


async def test_a_request_with_no_body_says_content_length_0(world):
    # measured on a live runtime: the session endpoint forwards a body-less request without
    # Content-Length in a form uvicorn refuses (400 "Invalid HTTP request received")
    h = await _open(world)
    async with world.carrier.client(h, 5) as c:
        await c.get("/health")
    assert world.requests[-1].headers["content-length"] == "0"
