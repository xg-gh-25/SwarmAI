"""A run placed on the elastic Hive goes the whole way (design
docs/design/2026-11-20-swarm-out-multi-carrier.md §4.3-§4.5, §6; requirements R2.2,
R5.1-R5.5 of run_fd94fed6). No network: the boto3 clients are fakes and the Hive backend
behind the session endpoint is an httpx.MockTransport.

Questions:
1. does a dispatch with only the elastic offer go place -> open_run -> Brain sync -> repo
   shipping -> work order -> session, every step on the opened round's session endpoint,
   and write ``rounds`` with the agentcore handle (its runtime session in ``run_ref``, the
   chat session in ``session_id``) and no credential in the row?
2. is its result read through ``outputs`` from S3 (and its usage stored), its state read
   from its round, and nothing sent to the session for either?
3. does settling release the session (StopRuntimeSession), after which no client of the
   round sends anything?
4. does a failure after open_run (Brain sync, repo shipping, work order) release the round?
5. does a correction after the session ended open round 2 at the returned commits, go
   through its own session only, and append it to ``rounds`` as the current round?
6. is an open round's wait listed from its own bridge (``bridge/<runtime session>/``) and
   answered there, without a request to the session?
7. is a round kept once a row names it (a cancel after the session took the task, a
   failure after a new round was recorded), and a later round that did not name its chat
   session never addressed with an earlier round's?
8. does a relay of a round that ended stop, showing it idle, without a request?
"""
from __future__ import annotations

import asyncio
import io
import json
import sqlite3
import subprocess
from collections import namedtuple
from pathlib import Path

import httpx
import pytest
from botocore.credentials import ReadOnlyCredentials
from botocore.exceptions import ClientError

from core import hive_bundle, hive_records, hive_repo_inbox
from core import swarm_out as so
from core.carriers import agentcore as ac
from core.carriers import registry
from core.carriers.base import AGENTCORE, TIER_ELASTIC, RunHandle, current_handle, rounds_of
from tests import _hive_work_fake as _work
from tests.test_task_spec import BINDINGS, _brain, ws  # noqa: F401 — the Brain fixture

BUCKET = "swarmai-hive-123456789012-us-east-1"
ARN = "arn:aws:bedrock-agentcore:us-east-1:123456789012:runtime/swarm_hive-AbC123"
URL = "https://github.com/acme/lib.git"
Frozen = ReadOnlyCredentials   # what a session's get_frozen_credentials() returns


def _err(code, op="Op"):
    return ClientError({"Error": {"Code": code, "Message": code}}, op)


class _NoSuchKey(ClientError):
    def __init__(self, key):
        super().__init__({"Error": {"Code": "NoSuchKey", "Message": key}}, "GetObject")


class FakeS3:
    exceptions = namedtuple("Exceptions", "NoSuchKey")(_NoSuchKey)

    def __init__(self):
        self.objects: dict[str, bytes] = {}

    def put(self, key, data):
        self.objects[key] = data if isinstance(data, bytes) else json.dumps(data).encode()

    def get_object(self, Bucket, Key, **kw):
        assert Bucket == BUCKET
        if Key not in self.objects:
            raise _NoSuchKey(Key)
        data = self.objects[Key]
        return {"Body": io.BytesIO(data), "ContentLength": len(data)}

    def put_object(self, Bucket, Key, Body, IfNoneMatch=None, **kw):
        assert Bucket == BUCKET
        if IfNoneMatch == "*" and Key in self.objects:
            raise _err("PreconditionFailed", "PutObject")
        self.objects[Key] = Body

    def get_paginator(self, op):
        assert op == "list_objects_v2"
        s3 = self

        class Pages:
            def paginate(self, Bucket, Prefix, **kw):
                import datetime
                at = datetime.datetime.now(datetime.timezone.utc)
                yield {"Contents": [{"Key": k, "LastModified": at} for k in sorted(s3.objects)
                                    if k.startswith(Prefix)]}
        return Pages()

    def list_objects_v2(self, Bucket, Prefix, StartAfter=None, MaxKeys=1000, **kw):
        keys = sorted(k for k in self.objects if k.startswith(Prefix) and (StartAfter is None or k > StartAfter))
        return {"Contents": [{"Key": k} for k in keys[:MaxKeys]], "IsTruncated": len(keys) > MaxKeys}


class FakeAgentCore:
    def __init__(self):
        self.invokes: list[dict] = []
        self.stops: list[str] = []

    def invoke_agent_runtime(self, **kw):
        self.invokes.append(kw)
        rnd = int(kw["runtimeSessionId"].rsplit("-", 1)[1])
        url = f"https://sess-{rnd}.runtime.bedrock-agentcore.us-east-1.on.aws"
        return {"response": io.BytesIO(b'{"started": true}'), "statusCode": 200,
                "ResponseMetadata": {"HTTPHeaders": {ac.ENDPOINT_HEADER: url}}}

    def stop_runtime_session(self, **kw):
        self.stops.append(kw["runtimeSessionId"])
        return {"statusCode": 200}


class FakeControl:
    def get_agent_runtime(self, agentRuntimeId):
        return {"status": "READY", "agentRuntimeArtifact": {"containerConfiguration": {
            "containerUri": "123456789012.dkr.ecr.us-east-1.amazonaws.com/swarmai-hive:2.5.5"}}}


class FakeQuotas:
    def list_service_quotas(self, **kw):
        return {"Quotas": [{"QuotaName": "Active session workloads per account", "Value": 10.0}]}


class World:
    """The account (fake boto3) and the rounds' Hive backends (one MockTransport, told
    apart by the session endpoint's host: ``8081--sess-<round>...``)."""

    def __init__(self, tmp):
        self.tmp = tmp
        self.s3, self.data, self.control, self.quotas = FakeS3(), FakeAgentCore(), FakeControl(), FakeQuotas()
        self.seen: list[tuple[int, str, str]] = []           # (round, method, path)
        self.fail: dict[tuple[str, str], int] = {}           # (method, path prefix) -> status
        self.brains: dict[int, dict] = {}
        self.bundles: list[tuple[int, str]] = []
        self.orders: list[tuple[int, dict]] = []
        self.messages: list[tuple[int, dict]] = []
        self.made: dict[str, int] = {}

    def client(self, service, region_name=None, config=None):
        self.made[service] = self.made.get(service, 0) + 1
        return {"s3": self.s3, ac.SERVICE: self.data, ac.CONTROL_SERVICE: self.control,
                "service-quotas": self.quotas}[service]

    def get_credentials(self):
        return namedtuple("C", "get_frozen_credentials")(lambda: Frozen("AKIDEXAMPLE", "secretsecret", "tok"))

    def handle(self, request: httpx.Request) -> httpx.Response:
        host, path, method = request.url.host, request.url.path, request.method
        assert host.startswith("8081--sess-") and host.endswith(".bedrock-agentcore.us-east-1.on.aws")
        assert request.headers[ac.AUTH_HEADER].startswith("Basic ")         # the run credential
        assert request.headers["authorization"].startswith("AWS4-HMAC-SHA256 ")   # SigV4
        rnd = int(host.split(".")[0].rsplit("-", 1)[1])
        self.seen.append((rnd, method, path))
        for (m, prefix), status in self.fail.items():
            if m == method and path.startswith(prefix):
                return httpx.Response(status, json={"detail": "refused"})
        if path == "/api/chat/sessions/admission-state":
            return httpx.Response(200, json={**_work.ADMISSION, "streaming_count": 0, "task_checks": True,
                                             "pipeline_rerun": True})
        if path == "/api/hive/brains" and method == "GET":
            return httpx.Response(200, json={"brains": [
                {"brain": b, "digest": d, "current_digest": d} for b, d in self.brains.get(rnd, {}).items()]})
        if path.startswith("/api/hive/brains/") and method == "PUT":
            digest = request.headers["x-brain-digest"]
            self.brains.setdefault(rnd, {})[path.rsplit("/", 1)[1]] = digest
            return httpx.Response(200, json={"digest": digest, "installed_at": "2026-11-20T00:00:00Z"})
        if path == "/api/hive/repos" and method == "PUT":
            sha = request.url.params["sha"]
            self.bundles.append((rnd, sha))
            return httpx.Response(200, json={"bundle": hive_repo_inbox.bundle_rel(sha), "bytes": len(request.content)})
        if path == "/api/hive/work" and method == "POST":
            self.orders.append((rnd, json.loads(request.content)))
        if _work.answer(request) is not None:
            return _work.answer(request)
        if path == "/api/chat/stream":
            self.messages.append((rnd, json.loads(request.content)))
            body = f"data: {json.dumps({'type': 'session_start', 'session_id': f'chat-round-{rnd}'})}\n\n"
            return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})
        if path.startswith("/api/chat/stop/") and method == "POST":
            return httpx.Response(200, json={})
        if path.startswith("/api/hive/work/") and method == "GET":
            return httpx.Response(200, json={"exists": True, "claimed": True, "session_id": f"chat-round-{rnd}"})
        if path == "/api/chat/sessions/streaming-state":
            return httpx.Response(200, json={"sessions": {f"chat-round-{rnd}": {
                "streaming": True, "state": "streaming", "activity": "streaming", "activity_seq": 1}}})
        return httpx.Response(404, json={})


def _make_db(path: Path):
    from database.sqlite import SQLiteDatabase, close_all_pools

    async def go():
        await SQLiteDatabase(db_path=str(path)).initialize()
        await close_all_pools()
    asyncio.run(go())


def _git(cwd, *args):
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=cwd,
                          capture_output=True, text=True, check=True).stdout.strip()


@pytest.fixture
def world(tmp_path, monkeypatch, ws):  # noqa: F811 — the Brain fixture's workspace
    import database
    from core import swarm_out_model
    from hive import accounts
    w = World(tmp_path)
    db = tmp_path / "data.db"
    _make_db(db)
    now = "2026-11-20T00:00:00Z"
    with sqlite3.connect(db) as c:
        c.execute("INSERT INTO hive_accounts (id, account_id, label, auth_method, created_at) "
                  "VALUES ('acct1', '123456789012', '', 'access_keys', ?)", (now,))
        # the only location: the elastic Hive, as the provisioner leaves it (never an EC2 status)
        c.execute("INSERT INTO hive_instances (id, name, account_ref, region, s3_bucket, status, created_at, "
                  "updated_at, carrier, runtime_arn) VALUES ('ac1', 'elastic', 'acct1', 'us-east-1', ?, "
                  "'ready', ?, ?, 'agentcore', ?)", (BUCKET, now, now, ARN))
    w.db = db
    monkeypatch.setattr(database.db, "db_path", db)
    state = tmp_path / "state"
    state.mkdir()
    w.state = state
    monkeypatch.setattr(so, "_state_dir", lambda: state)
    monkeypatch.setattr(so, "_MIN_STEP_S", 0.01, raising=False)
    monkeypatch.setattr(so, "_refresh_soon", lambda: None)
    monkeypatch.setattr(accounts, "swarmai_account", lambda: None)
    monkeypatch.setattr(swarm_out_model, "host_model", lambda: None)
    monkeypatch.setattr(ac, "_host_version", lambda: "2.5.5")
    monkeypatch.setattr(ac, "_session_for", lambda acct, region, purpose: w)
    ac._quota_cache.clear()
    ac._manifest_seen.clear()
    monkeypatch.setitem(registry._made, AGENTCORE, ac.AgentCoreCarrier(transport=httpx.MockTransport(w.handle)))

    def boom(*a, **kw):
        raise AssertionError("an elastic run is never reached as a Hive (CloudFront + Basic)")
    monkeypatch.setattr(so, "_client", boom)
    # a checkout of the Brain's repository with a commit of this machine: it is shipped
    repo = tmp_path / "lib"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "work")
    (repo / "a.txt").write_text("a")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "a")
    w.repo, w.sha = repo, _git(repo, "rev-parse", "HEAD")
    b = _brain(ws)
    (b / "bindings.yaml").write_text(BINDINGS.replace("    kind: external\n", f"    kind: external\n    worktree: {repo}\n"))
    w.ws = ws
    return w


def _no_credential(text: str, cred: dict) -> None:
    """Neither the run credential nor the account's keys are in *text*."""
    for secret in (cred["password"], "hive_password", "Basic ", "AKIDEXAMPLE", "secretsecret"):
        assert secret not in text, secret


def _dispatch(w):
    from core.project_agent import revision
    rev = revision(w.ws / "Projects" / "Lib")
    return asyncio.run(so.dispatch_spec(None, "fix it", "Lib", "fix the signer", "tests pass",
                                        execution="native", returns="commits", seen_revision=rev))


def _log_lines(w) -> list[str]:
    p = w.state / "dispatches.jsonl"
    return p.read_text().splitlines() if p.exists() else []


def _close_round(w, sid: str, chat: str):
    """What the round's Hive wrote to S3 by its end: its run record (a bundle, a first
    turn, closed) and its usage."""
    ws = w.tmp / f"hive-ws-{sid[-4:]}"
    hive_records.append(ws, chat, "bundle", data=b"PACK" * 100, key=hive_bundle.key("lib"))
    hive_records.append(ws, chat, "turn", data=json.dumps({"rows": [], "first_turn": True}).encode())
    hive_records.append(ws, chat, "turn", data=json.dumps({"rows": [
        {"id": "r2", "role": "assistant", "content": [{"type": "text", "text": "Fixed the signer."}]}]}).encode())
    hive_records.append(ws, chat, "closed", data=b"{}")
    d = hive_records.session_dir(ws, chat)
    for e in hive_records._read_manifest(d):
        w.s3.put(f"bridge/{sid}/records/manifest/{e['seq']:06d}.json", e)
        w.s3.put(f"bridge/{sid}/records/{e['file']}", (d / e["file"]).read_bytes())
    w.s3.put(f"bridge/{sid}/usage.json", {"rows": [{
        "id": 1, "at_utc": "2026-11-20T09:00:00+00:00", "surface": "chat", "session_id": chat, "model": "opus",
        "input": 1, "output": 10, "cache_read": 100, "cache_write": 5, "cost_usd": 0.5, "cost_source": "cli",
        "kind": "spend", "imported": 0}]})


# ── 1. place -> open -> Brain -> repos -> work order -> session; the round kept ──

def test_a_dispatch_on_the_elastic_hive_goes_the_whole_way(world):
    out = _dispatch(world)
    assert out["hive_reason"] == "elastic" and out["placement"]["layer"] == TIER_ELASTIC
    sid = ac.session_id_for(out["dispatch_id"], 1)
    assert [i["runtimeSessionId"] for i in world.data.invokes] == [sid]          # opened once
    steps = [(m, p) for _r, m, p in world.seen]
    assert {r for r, _m, _p in world.seen} == {1}                                # all on round 1's session
    order = [steps.index(s) for s in (("GET", "/api/hive/brains"), ("PUT", "/api/hive/brains/Lib"),
                                      ("PUT", "/api/hive/repos"), ("POST", "/api/hive/work"),
                                      ("POST", "/api/chat/stream"))]
    assert order == sorted(order), steps
    assert world.bundles == [(1, world.sha)]
    [(_r, work)] = world.orders
    assert work["work_id"] == out["dispatch_id"] and [r["url"] for r in work["repos"]] == [URL]
    assert work["checks"]["validations"][0]["id"] == "tests"                    # checks by id
    # the row: its one round is the agentcore handle, the backend's chat session beside it
    [line] = _log_lines(world)
    row = json.loads(line)
    assert (row["hive_id"], row["hive"], row["session_id"]) == ("ac1", "elastic", "chat-round-1")
    [h] = rounds_of(row)
    assert (h.carrier, h.location_id, h.round, h.run_ref) == (AGENTCORE, "ac1", 1, sid)
    assert h.session == "chat-round-1" and h.auth_ref == f"run_cred:{out['dispatch_id']}:1"
    assert registry.handle_of(row) == h
    cred = ac.run_credential(out["dispatch_id"], 1)
    _no_credential(line, cred)
    assert "round_task" in row and "fix the signer" in row["round_task"]
    assert world.data.stops == []                                                # left running


# ── 2. result through outputs from S3, state from the round; 3. settle releases ──

def test_its_result_and_state_come_from_s3_and_settling_releases_it(world, monkeypatch):
    from core import swarm_out_results, swarm_out_settle, usage_accounting
    out = _dispatch(world)
    did = out["dispatch_id"]
    sid = ac.session_id_for(did, 1)
    _close_round(world, sid, "chat-round-1")
    world.seen.clear()
    rec = asyncio.run(swarm_out_results.fetch(so._log_row_of(did)))
    assert rec["fetch_error"] is None and rec["records"]["closed"] is True and rec["result"] is not None
    assert hive_records.bundle_path(did, 1, hive_bundle.key("lib")).read_bytes() == b"PACK" * 100
    usage_accounting.flush(5)
    with sqlite3.connect(world.db) as c:
        assert c.execute("SELECT dedup_key FROM usage_ledger").fetchall() == [(f"hive:{did}:1",)]
    # the runs list: its state is its round's, its location is never asked as a Hive
    started: list = []
    monkeypatch.setattr(swarm_out_results, "_start_check", lambda r: started.append(r["dispatch_id"]))
    monkeypatch.setattr(swarm_out_settle, "annotate", lambda rows, may_tell: [])

    async def no_hive(hive_id):
        raise AssertionError(f"{hive_id} was read as a Hive")
    monkeypatch.setattr(so, "_read_hive", no_hive)
    runs = asyncio.run(so._compute_runs())
    [r] = runs["runs"]
    assert (r["state"], r["state_reason"]) == ("stopped", "closed") and r["result_status"] == "fetched"
    assert world.seen == []                                                      # nothing sent to the session
    # settled: the session is stopped, and no client of the round sends anything after
    swarm_out_settle._update(did, lambda rec: rec.update(settled={"released": False, "at": 1}))
    asyncio.run(swarm_out_settle._tell(so._log_row_of(did)))
    assert world.data.stops == [sid]
    assert swarm_out_settle.load(did)["settled"]["released"] is True
    with pytest.raises(ac.RunEnded):
        registry.client_for(registry.handle_of(so._log_row_of(did)))
    # stopping it is done already: nothing runs there, and nothing is sent
    out = asyncio.run(so.stop_turn(did))
    assert out["stopped"] is True and out["ended"]
    assert world.seen == []


def test_a_running_rounds_state_and_its_stop_reach_its_own_chat_session(world):
    out = _dispatch(world)
    did = out["dispatch_id"]
    sid = ac.session_id_for(did, 1)
    world.s3.put(f"bridge/{sid}/state.json", {"state": "running"})
    h = registry.handle_of(so._log_row_of(did))
    assert asyncio.run(registry.carrier(AGENTCORE).state(h)) == ("running", "streaming")
    world.seen.clear()
    assert asyncio.run(so.stop_turn(did)) == {"stopped": True}
    # the chat session the backend named, never the runtime session id
    assert world.seen == [(1, "POST", "/api/chat/stop/chat-round-1")]


def test_an_open_rounds_wait_is_listed_and_answered_on_its_own_bridge(world, monkeypatch):
    import time
    from core import swarm_out_results, swarm_out_settle
    out = _dispatch(world)
    sid = ac.session_id_for(out["dispatch_id"], 1)
    world.s3.put(f"bridge/{sid}/state.json", {"state": "waiting"})
    world.s3.put(f"bridge/{sid}/ask/chat-round-1/q1.json", {
        "kind": "question", "session_id": "chat-round-1", "request_id": "q1",
        "asked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "questions": [{"question": "Which?", "options": [{"label": "A"}, {"label": "B"}]}]})
    monkeypatch.setattr(swarm_out_results, "_start_check", lambda r: None)
    monkeypatch.setattr(swarm_out_settle, "annotate", lambda rows, may_tell: [])
    world.seen.clear()
    runs = asyncio.run(so._compute_runs())
    [a] = runs["asks"]
    assert (a["hive_id"], a["hive"], a["session_id"], a["request_id"]) == ("ac1", "elastic", "chat-round-1", "q1")
    assert a["dispatch_title"] == "fix it" and runs["errors"] == []
    assert (runs["runs"][0]["state"], runs["runs"][0]["state_reason"]) == ("waiting", "waiting_input")
    # answered on the round's own bridge, nothing sent to its session
    assert asyncio.run(so.answer("ac1", "chat-round-1", "q1", answers={"Which?": "A"}))["ok"] is True
    # one S3 client per purpose (read, manage), made once and shared: never one per read
    assert world.made["s3"] <= 2, world.made
    got = json.loads(world.s3.objects[f"bridge/{sid}/answer/chat-round-1/q1.json"])
    assert got["answers"] == {"Which?": "A"} and got["answered_by"] == "person"
    with pytest.raises(so.SwarmOutError, match="already answered"):
        asyncio.run(so.answer("ac1", "chat-round-1", "q1", answers={"Which?": "B"}))
    assert world.seen == []


# ── 4. a failure after open_run releases the round ──

@pytest.mark.parametrize("fail", [("PUT", "/api/hive/brains/"), ("PUT", "/api/hive/repos"),
                                  ("POST", "/api/hive/work")], ids=["brain_sync", "repo_shipping", "work_order"])
def test_a_failure_after_open_releases_the_round(world, fail):
    world.fail[fail] = 500
    with pytest.raises(so.SwarmOutError):
        _dispatch(world)
    [inv] = world.data.invokes
    assert world.data.stops == [inv["runtimeSessionId"]]                          # no microVM left running
    assert _log_lines(world) == []                                               # nothing was dispatched
    assert not any(p == "/api/chat/stream" for _r, _m, p in world.seen)


def test_a_session_that_may_have_taken_the_task_is_kept_and_addressable(world, monkeypatch):
    real = world.handle

    def cut(request):
        if request.url.path == "/api/chat/stream":
            world.seen.append((1, "POST", "/api/chat/stream"))
            return httpx.Response(200, text="", headers={"content-type": "text/event-stream"})
        return real(request)
    world.handle = cut
    monkeypatch.setitem(registry._made, AGENTCORE, ac.AgentCoreCarrier(transport=httpx.MockTransport(cut)))
    with pytest.raises(so.SwarmOutMaybeSent):
        _dispatch(world)
    assert world.data.stops == []                                                # it may be running there
    [line] = _log_lines(world)
    row = json.loads(line)
    assert row["session_id"] is None and rounds_of(row)[0].carrier == AGENTCORE


# ── 5. a correction after the session ended opens round 2 ──

def test_a_correction_after_the_session_ended_opens_round_two(world):
    from core import swarm_out_settle
    out = _dispatch(world)
    did = out["dispatch_id"]
    sid1 = ac.session_id_for(did, 1)
    _close_round(world, sid1, "chat-round-1")
    # the round's commits taken back here (core/hive_return): refs/hive/<dispatch>/<key>
    (world.repo / "a.txt").write_text("fixed")
    _git(world.repo, "commit", "-qam", "the round's commit")
    returned = _git(world.repo, "rev-parse", "HEAD")
    key = hive_bundle.key("lib")
    _git(world.repo, "update-ref", f"refs/hive/{did}/{key}", returned)
    d = world.state / "returns" / did
    d.mkdir(parents=True)
    (d / f"{key}.json").write_text(json.dumps({"url": URL, "key": key, "repo": str(world.repo), "branch": "work",
                                               "ref": f"refs/hive/{did}/{key}", "tip": returned}))
    world.seen.clear()
    got = asyncio.run(so.rerun(did, "the signer still accepts an empty key"))
    assert got["sent"] is True and got["round"] == 2
    assert [i["runtimeSessionId"] for i in world.data.invokes] == [sid1, ac.session_id_for(did, 2)]
    assert {r for r, _m, _p in world.seen} == {2}                                 # nothing to round 1's session
    assert (2, world.sha) not in world.bundles and (2, returned) in world.bundles   # at the returned commit
    assert (2, "PUT", "/api/hive/brains/Lib") in world.seen                       # a fresh machine gets the Brain
    [(_r, work)] = [o for o in world.orders if o[0] == 2]
    assert work["work_id"] == did and work["repos"][0]["sha"] == returned and work["checks"]
    [(_r, msg)] = [m for m in world.messages if m[0] == 2]
    assert "session_id" not in msg and msg["work_id"] == did and msg["project_agent"]["brain"] == "Lib"
    assert "fix the signer" in msg["message"] and "the signer still accepts an empty key" in msg["message"]
    # the run's rounds: round 2 is the current one, with its own chat session
    row = so._log_row_of(did)
    assert [(h.round, h.run_ref, h.session) for h in rounds_of(row)] == [
        (1, sid1, "chat-round-1"), (2, ac.session_id_for(did, 2), "chat-round-2")]
    assert current_handle(row).round == 2 and row["session_id"] == "chat-round-2"
    assert len(swarm_out_settle.load(did)["rounds"]) == 1
    assert world.data.stops == []
    _no_credential(json.dumps(swarm_out_settle.load(did)), ac.run_credential(did, 2))


def test_a_correction_while_the_session_is_open_goes_into_it(world):
    out = _dispatch(world)
    did = out["dispatch_id"]
    world.seen.clear()
    assert asyncio.run(so.rerun(did, "also add a test"))["sent"] is True
    assert len(world.data.invokes) == 1                                            # no new round
    [(_r, msg)] = [m for m in world.messages if m[0] == 1][-1:]
    assert msg["session_id"] == "chat-round-1" and msg["message"] == "also add a test"
    assert len(rounds_of(so._log_row_of(did))) == 1


def test_a_failure_after_the_new_round_was_kept_does_not_release_it(world, monkeypatch):
    from core import swarm_out_results
    out = _dispatch(world)
    did = out["dispatch_id"]
    _close_round(world, ac.session_id_for(did, 1), "chat-round-1")

    def broken(did):
        raise OSError("disk full")
    monkeypatch.setattr(swarm_out_results, "turn_started", broken)
    with pytest.raises(OSError):
        asyncio.run(so.rerun(did, "again"))
    assert world.data.stops == []                                                # the run's current round
    assert current_handle(so._log_row_of(did)).round == 2


def test_a_later_round_without_its_session_is_never_given_an_earlier_one(world, monkeypatch):
    out = _dispatch(world)
    did = out["dispatch_id"]
    _close_round(world, ac.session_id_for(did, 1), "chat-round-1")
    real = world.handle

    def unnamed(request):
        if request.url.path == "/api/chat/stream" and "8081--sess-2." in request.url.host:
            world.seen.append((2, "POST", "/api/chat/stream"))
            return httpx.Response(200, text="", headers={"content-type": "text/event-stream"})
        return real(request)
    registry._made[AGENTCORE]._transport = httpx.MockTransport(unnamed)
    with pytest.raises(so.SwarmOutMaybeSent):
        asyncio.run(so.rerun(did, "again"))
    assert world.data.stops == []                                                # it may be running there
    row = so._log_row_of(did)
    assert current_handle(row).round == 2 and row["session_id"] is None
    assert registry.handle_of(row).session is None
    world.seen.clear()
    with pytest.raises(so.SwarmOutError, match="no session"):
        asyncio.run(so.stop_turn(did))
    assert world.seen == []                                                      # never round 1's session
    # the refresh asks round 2's own machine about the work order (counted from when the
    # round opened, not from the dispatch) and names it there
    asyncio.run(so._find_sessions([row], {"ac1": "running"}))
    assert world.seen == []                                                      # just opened: not yet
    monkeypatch.setattr(so, "FIND_SESSION_AFTER_S", 0)
    asyncio.run(so._find_sessions([row], {"ac1": "running"}))
    assert world.seen == [(2, "GET", f"/api/hive/work/{did}")]
    got = so._log_row_of(did)
    assert got["session_id"] == "chat-round-2" and rounds_of(got)[1].session == "chat-round-2"
    assert rounds_of(got)[0].session == "chat-round-1"


def test_a_cancel_after_the_session_took_the_task_keeps_the_round(world, monkeypatch):
    async def took_then_cancelled(client, message, work_id=None, project_agent=None, session_id=None,
                                  on_accepted=None, origin=None):
        on_accepted()
        raise asyncio.CancelledError()
    monkeypatch.setattr(so, "_start_session", took_then_cancelled)
    with pytest.raises(asyncio.CancelledError):
        _dispatch(world)
    assert world.data.stops == []                                                # it may be running there
    [line] = _log_lines(world)
    row = json.loads(line)
    assert row["session_id"] is None and rounds_of(row)[0].run_ref == world.data.invokes[0]["runtimeSessionId"]
    _no_credential(line, ac.run_credential(row["dispatch_id"], 1))


def test_a_relay_of_a_round_that_ended_stops_idle_without_a_request(world):
    from core import swarm_out_relay
    out = _dispatch(world)
    did = out["dispatch_id"]
    sid = ac.session_id_for(did, 1)

    async def go():
        m = swarm_out_relay.RelayManager()
        inst = await registry.carrier(AGENTCORE).connect(registry.handle_of(so._log_row_of(did)))
        entry = await m.acquire("chat-round-1", "c1", inst)
        assert entry["activity"] == "streaming"
        _close_round(world, sid, "chat-round-1")                                  # it ends meanwhile
        world.seen.clear()
        relay = m._relays["chat-round-1"]
        await asyncio.wait_for(relay.task, 5)                                    # it stops by itself
        state = m.state_entry("chat-round-1")
        await m.stop_all()
        return state
    state = asyncio.run(go())
    assert state["activity"] == "idle" and state["relay_connected"] is False
    assert world.seen == []


def test_a_wait_no_elastic_run_names_is_not_answered_on_any_bridge(world):
    out = _dispatch(world)
    sid = ac.session_id_for(out["dispatch_id"], 1)
    with pytest.raises(so.SwarmOutError):
        asyncio.run(so.answer("ac1", "chat-of-another-run", "q1", answers={"Which?": "A"}))
    assert not any(k.startswith(f"bridge/{sid}/answer/") for k in world.s3.objects)


def test_two_corrections_at_once_open_one_new_round(world):
    out = _dispatch(world)
    did = out["dispatch_id"]
    _close_round(world, ac.session_id_for(did, 1), "chat-round-1")

    async def both():
        return await asyncio.gather(so.rerun(did, "first"), so.rerun(did, "second"), return_exceptions=True)
    got = asyncio.run(both())
    sent = [g for g in got if isinstance(g, dict)]
    refused = [g for g in got if isinstance(g, Exception)]
    assert len(sent) == 1 and len(refused) == 1 and isinstance(refused[0], so.SwarmOutError), got
    assert "later round" in str(refused[0])
    assert [i["runtimeSessionId"] for i in world.data.invokes] == [ac.session_id_for(did, 1),
                                                                   ac.session_id_for(did, 2)]
    assert world.data.stops == []                                                # nothing stopped the kept round
    assert [h.round for h in rounds_of(so._log_row_of(did))] == [1, 2]
    assert so._ROUND_LOCKS == {}


def test_a_failed_new_round_is_released_and_not_kept(world):
    out = _dispatch(world)
    did = out["dispatch_id"]
    _close_round(world, ac.session_id_for(did, 1), "chat-round-1")
    world.fail[("POST", "/api/hive/work")] = 500
    with pytest.raises(so.SwarmOutError):
        asyncio.run(so.rerun(did, "again"))
    assert world.data.stops == [ac.session_id_for(did, 2)]
    assert len(rounds_of(so._log_row_of(did))) == 1


def test_a_correction_after_a_failed_new_round_starts_a_new_attempt(world):
    # the failed attempt's session was released (marked ended here) and its supervisor
    # holds ``started``: the next correction must not ask for that session again
    out = _dispatch(world)
    did = out["dispatch_id"]
    _close_round(world, ac.session_id_for(did, 1), "chat-round-1")
    world.fail[("POST", "/api/hive/work")] = 500
    with pytest.raises(so.SwarmOutError):
        asyncio.run(so.rerun(did, "again"))
    world.fail.pop(("POST", "/api/hive/work"))
    asyncio.run(so.rerun(did, "once more"))
    first, second = ac.session_id_for(did, 2), ac.session_id_for(did, 2, 2)
    assert [i["runtimeSessionId"] for i in world.data.invokes] == [ac.session_id_for(did, 1), first, second]
    assert [(h.round, h.run_ref) for h in rounds_of(so._log_row_of(did))] == [
        (1, ac.session_id_for(did, 1)), (2, second)]


def test_only_a_run_scoped_round_reads_the_settle_record_for_later_rounds(world, monkeypatch):
    out = _dispatch(world)
    elastic = so._log_row_of(out["dispatch_id"])
    seen = []
    monkeypatch.setattr(so, "_later_rounds", lambda did: seen.append(did) or [])
    so._with_rounds({"dispatch_id": "so_0123456789ab", "hive_id": "h1", "session_id": "s1"})   # an EC2 row
    assert seen == []
    so._with_rounds(elastic)                                                                    # an elastic row
    assert seen == [out["dispatch_id"]]


def test_the_client_reads_the_run_credential_off_the_event_loop_at_its_first_send(world, monkeypatch):
    out = _dispatch(world)
    h = registry.handle_of(so._log_row_of(out["dispatch_id"]))
    real, reads = ac.run_credential, []
    monkeypatch.setattr(ac, "run_credential", lambda *a, **k: reads.append(a) or real(*a, **k))

    async def go():
        c = registry.carrier(AGENTCORE).client(h, 5)
        built = list(reads)                                      # nothing read to build it
        async with c:
            r = await c.get("/api/chat/sessions/admission-state")
            await c.get("/api/chat/sessions/admission-state")
        return built, r.status_code
    built, status = asyncio.run(go())
    assert built == [] and status == 200
    assert len(reads) == 1                                       # once, at the first send
