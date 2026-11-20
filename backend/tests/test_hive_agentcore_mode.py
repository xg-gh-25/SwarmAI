"""The Hive backend inside an AgentCore Runtime microVM (core/hive_agentcore; design
docs/design/2026-11-20-swarm-out-multi-carrier.md §6.3-§6.7): the credential header, the
busy conditions, the S3 export of the run record, the bridge identity from the
environment and the review asked for when the turn ends. No network: S3 is a fake
client object.
"""
from __future__ import annotations

import asyncio
import base64
import json
import secrets
from datetime import timedelta
from types import SimpleNamespace

import bcrypt
import pytest
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from core.session_unit import SessionState

AC_SID = "so_0123456789ab-r1-" + "x" * 20          # >= 33 characters, like the Host derives
BUCKET = "swarmai-bridge-test"
HIVE_SID = "sess_agentcore_test_1"
WORK_ID = "so_" + "a" * 12
USER = "admin"
PASSWORD = secrets.token_urlsafe(12)            # a fresh test credential each run
HASH = bcrypt.hashpw(PASSWORD.encode(), bcrypt.gensalt(4)).decode()


class FakeS3:
    """put_object as S3 answers it: IfNoneMatch="*" on an existing key is refused."""

    def __init__(self):
        self.objects: dict[str, bytes] = {}
        self.calls: list[dict] = []
        self.fail = False

    def put_object(self, **kw):
        if self.fail:
            raise ConnectionError("s3 down")
        body = kw["Body"]
        body = body.read() if hasattr(body, "read") else body
        self.calls.append({**kw, "Body": body})
        if kw.get("IfNoneMatch") == "*" and kw["Key"] in self.objects:
            err = Exception("exists")
            err.response = {"Error": {"Code": "PreconditionFailed"}}
            raise err
        self.objects[kw["Key"]] = body


@pytest.fixture
def ac(tmp_path, monkeypatch):
    """An AgentCore Hive: the supervisor's environment, a fake S3 client, no usage rows."""
    from core import hive_agentcore
    monkeypatch.setenv("SWARMAI_MODE", "hive")
    monkeypatch.setenv("SWARMAI_CARRIER", "agentcore")
    monkeypatch.setenv("SWARMAI_AC_SID", AC_SID)
    monkeypatch.setenv("SWARMAI_AC_BUCKET", BUCKET)
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    monkeypatch.setenv("SWARM_DATA_DIR", str(tmp_path / "data"))
    ws = tmp_path / "ws"
    ws.mkdir()
    monkeypatch.setattr(hive_agentcore, "_workspace", lambda: ws)
    monkeypatch.setattr(hive_agentcore, "_usage_fingerprint", lambda: (0, 0))
    hive_agentcore._reset_for_tests()
    s3 = FakeS3()
    hive_agentcore._client_obj = s3
    yield SimpleNamespace(ws=ws, s3=s3, mod=hive_agentcore)
    hive_agentcore._reset_for_tests()


# ── 1. the credential header ─────────────────────────────────────────────────

def _app() -> Starlette:
    from middleware.hive_auth import HiveAuthMiddleware

    async def ok(request):  # noqa: ANN001
        return JSONResponse({"ok": True})
    app = Starlette(routes=[Route("/api/jobs/run", ok, methods=["POST"]),
                            Route("/health", ok, methods=["GET"])])
    app.add_middleware(HiveAuthMiddleware)
    return app


def _basic(user: str, password: str) -> str:
    return "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()


@pytest.fixture
def creds(monkeypatch):
    from config import settings
    monkeypatch.setattr(settings, "hive_user", USER, raising=False)
    monkeypatch.setattr(settings, "hive_pass_hash", HASH, raising=False)


def test_auth_header_required(ac, creds):
    client = TestClient(_app())
    assert client.post("/api/jobs/run").status_code == 401
    assert client.post("/api/jobs/run", headers={"X-Swarm-Hive-Auth": _basic(USER, "wrong")}).status_code == 401
    assert client.post("/api/jobs/run", headers={"X-Swarm-Hive-Auth": "Bearer abc"}).status_code == 401
    # Authorization is the platform's (SigV4) in this mode: a right credential there is ignored
    assert client.post("/api/jobs/run", headers={"Authorization": _basic(USER, PASSWORD)}).status_code == 401
    assert client.post("/api/jobs/run", headers={"X-Swarm-Hive-Auth": _basic(USER, PASSWORD)}).status_code == 200
    assert client.post("/api/jobs/run", headers={"Authorization": "AWS4-HMAC-SHA256 Credential=x",
                                                 "X-Swarm-Hive-Auth": _basic(USER, PASSWORD)}).status_code == 200
    assert client.get("/health").status_code == 200


def test_auth_outside_the_mode_is_unchanged(monkeypatch, creds):
    monkeypatch.setenv("SWARMAI_MODE", "hive")
    monkeypatch.delenv("SWARMAI_CARRIER", raising=False)
    client = TestClient(_app())
    assert client.post("/api/jobs/run", headers={"X-Swarm-Hive-Auth": _basic(USER, PASSWORD)}).status_code == 401
    assert client.post("/api/jobs/run", headers={"Authorization": _basic(USER, PASSWORD)}).status_code == 200


# ── 2. busy: exactly four conditions ─────────────────────────────────────────

class Unit:
    def __init__(self, sid, state=SessionState.IDLE, background=False):
        self.session_id = sid
        self.state = state
        self.has_background_work = background
        self._pending_tool_use_id = None


class Router:
    def __init__(self, *units):
        self._units = {u.session_id: u for u in units}

    def list_units(self):
        return list(self._units.values())


def _open_work_order(ac) -> None:
    from core import hive_workspace
    d = hive_workspace._state() / "orders"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{WORK_ID}.json").write_text(json.dumps({"work_id": WORK_ID, "session_id": HIVE_SID}))
    assert ac.mod._orders(ac.ws) == [WORK_ID]


async def _busy(ac, router, pm):
    return await ac.mod.busy(router=router, pm=pm, workspace=ac.ws)


@pytest.fixture
def pm(tmp_path):
    from core.permission_manager import PermissionManager
    return PermissionManager(persist_path=tmp_path / "pending.json")


async def test_busy_is_exactly_four_conditions(ac, pm, monkeypatch):
    from core import hive_records
    from core.ingestion import s2_jobs
    from core.ingestion.api import connect_rw
    monkeypatch.setattr(s2_jobs, "_WAKER", None)
    monkeypatch.setattr(s2_jobs, "_bound_project", lambda sid: None)
    idle = Router(Unit(HIVE_SID))
    # an open work order and an idle session, everything written: not busy
    _open_work_order(ac)
    hive_records.append(ac.ws, HIVE_SID, "turn", data=b'{"rows": []}')
    await ac.mod.sync_pass(router=idle, pm=pm, workspace=ac.ws)
    assert await _busy(ac, idle, pm) == {"busy": False, "reasons": []}

    # 1. a session streams, or has background work
    assert await _busy(ac, Router(Unit(HIVE_SID, SessionState.STREAMING)), pm) == \
        {"busy": True, "reasons": ["streaming"]}
    assert await _busy(ac, Router(Unit(HIVE_SID, background=True)), pm) == \
        {"busy": True, "reasons": ["background_work"]}
    # 2. a permission or a question waits (on the board, or the unit waits for input);
    # until the bridge's pass writes state.json "waiting", that write is pending too
    waiting = Router(Unit(HIVE_SID, SessionState.WAITING_INPUT))
    assert (await _busy(ac, waiting, pm))["reasons"] == ["waiting_input", "s3_pending"]
    await ac.mod.sync_pass(router=waiting, pm=pm, workspace=ac.ws)
    assert await _busy(ac, waiting, pm) == {"busy": True, "reasons": ["waiting_input"]}
    pm.publish_prompt(HIVE_SID, {"sessionId": HIVE_SID, "requestId": "req-1", "toolName": "Bash",
                                 "toolInput": {"command": "rm -rf /tmp/x"}, "options": ["approve", "deny"]})
    assert await _busy(ac, idle, pm) == {"busy": True, "reasons": ["waiting_input"]}
    pm._prompts.clear()
    await ac.mod.sync_pass(router=idle, pm=pm, workspace=ac.ws)     # state.json back to running
    assert await _busy(ac, idle, pm) == {"busy": False, "reasons": []}
    # 3. a session review job is queued (a not-before in the future still counts), or runs
    s2_jobs.request(HIVE_SID, "session_end", delay=timedelta(hours=1))
    assert await _busy(ac, idle, pm) == {"busy": True, "reasons": ["review_job"]}
    conn = connect_rw()
    try:
        conn.execute("UPDATE ingestion_s2_jobs SET state='running'")
        conn.commit()
        assert await _busy(ac, idle, pm) == {"busy": True, "reasons": ["review_job"]}
        conn.execute("UPDATE ingestion_s2_jobs SET state='done'")
        conn.commit()
    finally:
        conn.close()
    assert await _busy(ac, idle, pm) == {"busy": False, "reasons": []}
    # 4. an S3 write is pending: a record entry S3 refused
    ac.s3.fail = True
    hive_records.append(ac.ws, HIVE_SID, "turn", data=b'{"rows": [1]}')
    assert await _busy(ac, idle, pm) == {"busy": True, "reasons": ["s3_pending"]}   # queued or refused
    assert ac.mod.flush()
    assert await _busy(ac, idle, pm) == {"busy": True, "reasons": ["s3_pending"]}   # refused, pending
    ac.s3.fail = False
    await ac.mod.sync_pass(router=idle, pm=pm, workspace=ac.ws)      # the bridge's next pass
    assert await _busy(ac, idle, pm) == {"busy": False, "reasons": []}
    # ... or an object whose content changed (a check ran) and is not written yet
    from core import hive_workspace
    cdir = hive_workspace.checks_dir(ac.ws, WORK_ID)
    cdir.mkdir(parents=True)
    (cdir / hive_workspace.CHECKS_FILE).write_text(json.dumps({"id": "lint", "exit": 0}) + "\n")
    assert await _busy(ac, idle, pm) == {"busy": True, "reasons": ["s3_pending"]}
    await ac.mod.sync_pass(router=idle, pm=pm, workspace=ac.ws)
    assert await _busy(ac, idle, pm) == {"busy": False, "reasons": []}
    checks = json.loads(ac.s3.objects[f"bridge/{AC_SID}/checks.json"])
    assert checks["orders"] == [{"work_id": WORK_ID, "checks": [{"id": "lint", "exit": 0}]}]


async def test_busy_endpoint_only_in_the_mode(ac, monkeypatch):
    from fastapi import HTTPException
    from routers import hive_brains
    monkeypatch.setattr(ac.mod, "busy", lambda: asyncio.sleep(0, {"busy": False, "reasons": []}))
    assert await hive_brains.get_busy() == {"busy": False, "reasons": []}
    monkeypatch.delenv("SWARMAI_CARRIER")
    with pytest.raises(HTTPException) as e:
        await hive_brains.get_busy()
    assert e.value.status_code == 404


async def test_state_object_follows_the_session(ac, pm):
    from core import hive_records
    hive_records.append(ac.ws, HIVE_SID, "turn", data=b'{"rows": []}')
    state = f"bridge/{AC_SID}/state.json"
    await ac.mod.sync_pass(router=Router(Unit(HIVE_SID, SessionState.STREAMING)), pm=pm, workspace=ac.ws)
    assert json.loads(ac.s3.objects[state])["state"] == "running"
    await ac.mod.sync_pass(router=Router(Unit(HIVE_SID, SessionState.WAITING_INPUT)), pm=pm, workspace=ac.ws)
    assert json.loads(ac.s3.objects[state])["state"] == "waiting"
    hive_records.append(ac.ws, HIVE_SID, "closed", data=b'{"reason": "reviewed"}')
    assert ac.mod.flush()
    await ac.mod.sync_pass(router=Router(Unit(HIVE_SID)), pm=pm, workspace=ac.ws)
    assert json.loads(ac.s3.objects[state])["state"] == "ended"
    assert f"bridge/{AC_SID}/records/manifest/000002.json" in ac.s3.objects


async def test_usage_object_holds_the_ledger_rows(ac, pm, monkeypatch):
    from core import usage_accounting
    rows = [{"id": 1, "model": "m", "input": 3}, {"id": 2, "model": "m", "input": 4}]
    monkeypatch.setattr(ac.mod, "_usage_fingerprint", lambda: (2, 2))
    monkeypatch.setattr(usage_accounting, "rows_after", lambda after, limit: {
        "rows": [r for r in rows if r["id"] > after][:limit], "last_id": 2, "more": False})
    await ac.mod.sync_pass(router=Router(), pm=pm, workspace=ac.ws)
    got = json.loads(ac.s3.objects[f"bridge/{AC_SID}/usage.json"])
    assert got["rows"] == rows and got["last_id"] == 2
    n = len(ac.s3.calls)
    await ac.mod.sync_pass(router=Router(), pm=pm, workspace=ac.ws)
    assert len(ac.s3.calls) == n            # unchanged: not written again


# ── 3. the S3 export of an appended entry ────────────────────────────────────

def test_appended_entry_is_exported_once_in_order(ac):
    from core import hive_records
    e = hive_records.append(ac.ws, HIVE_SID, "turn", data=b'{"rows": ["a"]}')
    assert ac.mod.flush()
    rkey = f"bridge/{AC_SID}/records/{e['file']}"
    mkey = f"bridge/{AC_SID}/records/manifest/000001.json"
    assert ac.s3.objects[rkey] == b'{"rows": ["a"]}'
    assert json.loads(ac.s3.objects[mkey]) == e
    assert [c["Key"] for c in ac.s3.calls] == [rkey, mkey]          # file before its line
    assert all(c["IfNoneMatch"] == "*" and c["Bucket"] == BUCKET for c in ac.s3.calls)
    assert not ac.mod.export_pending(ac.ws)

    # S3 refuses: the record still seals, the entry stays pending
    ac.s3.fail = True
    tmp = hive_records.tmp_path(ac.ws, HIVE_SID, "0" * 16)
    tmp.write_bytes(b"bundle bytes")
    b = hive_records.append(ac.ws, HIVE_SID, "bundle", src=tmp, key="0" * 16)
    assert ac.mod.flush()
    assert b["seq"] == 2 and ac.mod.export_pending(ac.ws)
    # a restart loses nothing: the cursor is on disk
    ac.mod._reset_for_tests()
    ac.mod._client_obj = ac.s3
    assert ac.mod.export_pending(ac.ws)
    ac.s3.fail = False
    ac.mod._export(ac.ws, None)
    assert ac.s3.objects[f"bridge/{AC_SID}/records/{b['file']}"] == b"bundle bytes"
    assert json.loads(ac.s3.objects[f"bridge/{AC_SID}/records/manifest/000002.json"])["sha256"] == b["sha256"]
    assert not ac.mod.export_pending(ac.ws)

    # an object already there (a write whose answer was lost) is not an error
    ac.mod._save_cursor({"session": HIVE_SID, "exported": 0})
    assert ac.mod._export(ac.ws, None) == 2 and not ac.mod.export_pending(ac.ws)

    # another session's record is not this run's: never exported over it
    before = dict(ac.s3.objects)
    hive_records.append(ac.ws, "sess_other_session_9", "turn", data=b'{"rows": ["b"]}')
    assert ac.mod.flush()
    assert ac.s3.objects == before
    assert hive_records.KINDS == ("bundle", "turn", "lessons", "closed", "file")


def test_no_export_outside_the_mode(tmp_path, monkeypatch):
    from core import hive_agentcore, hive_records
    monkeypatch.setenv("SWARMAI_MODE", "hive")
    monkeypatch.delenv("SWARMAI_CARRIER", raising=False)
    monkeypatch.setenv("SWARM_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(hive_agentcore, "_client", lambda: pytest.fail("S3 used outside the mode"))
    ws = tmp_path / "ws"
    ws.mkdir()
    assert hive_records.append(ws, HIVE_SID, "turn", data=b"{}")["seq"] == 1
    assert not (tmp_path / "data" / "hive" / "agentcore-export.json").exists()


# ── 4. the bridge identity, from the environment only ────────────────────────

async def test_bridge_identity_from_env_never_imds(ac, monkeypatch):
    import urllib.request
    from core import swarm_out_bridge as sob
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: pytest.fail("IMDS called"))
    monkeypatch.setattr(sob, "resolve_identity", lambda: pytest.fail("IMDS identity read"))
    made: list = []
    monkeypatch.setattr(sob.S3Store, "_make", lambda self: made.append(self) or object())
    seen: dict = {}

    class FakeBridge:
        def __init__(self, hive, store):
            seen.update(hive=hive, store=store, made_before_start=list(made))

        async def reconcile(self):
            pass

        async def tick(self):
            raise asyncio.CancelledError

    passes: list = []

    async def fake_sync(*a, **k):
        passes.append(1)
    monkeypatch.setattr(sob, "Bridge", FakeBridge)
    monkeypatch.setattr(sob, "POLL_SECONDS", 0)
    monkeypatch.setattr(ac.mod, "sync_pass", fake_sync)
    with pytest.raises(asyncio.CancelledError):
        await sob.run_bridge_loop()
    assert seen["hive"] == AC_SID and seen["store"]._bucket == BUCKET
    assert seen["made_before_start"] == [] and made == []       # no client until first use
    seen["store"]._s3
    assert made == [seen["store"]]
    assert len(passes) == 2                                     # at start, then each pass

    # a missing or malformed session id: the bridge stays off, still without IMDS
    monkeypatch.setenv("SWARMAI_AC_SID", "short")
    seen.clear()
    await sob.run_bridge_loop()
    assert seen == {}


def test_ec2_store_still_makes_its_client_at_once(monkeypatch):
    from core import swarm_out_bridge as sob
    made: list = []
    monkeypatch.setattr(sob.S3Store, "_make", lambda self: made.append(1) or object())
    sob.S3Store("bucket-name", "us-east-1")
    assert made == [1]


# ── 5. the review, asked for when the turn ends ──────────────────────────────

@pytest.fixture
def requests(monkeypatch):
    from core.ingestion import s2_jobs
    got: list = []
    monkeypatch.setattr(s2_jobs, "request", lambda sid, reason, **kw: got.append((sid, reason, kw)) or "queued")
    return got


async def test_review_requested_immediately_at_turn_end(ac, pm, requests):
    ok = await ac.mod.request_review(Router(Unit(HIVE_SID)), HIVE_SID, pm=pm)
    assert ok and requests == [(HIVE_SID, "session_end", {"sdk_session_id": None, "delay": timedelta(0)})]
    requests.clear()
    for unit in (Unit(HIVE_SID, SessionState.WAITING_INPUT), Unit(HIVE_SID, background=True),
                 Unit(HIVE_SID, SessionState.STREAMING)):
        assert not await ac.mod.request_review(Router(unit), HIVE_SID, pm=pm)
    pm.publish_prompt(HIVE_SID, {"sessionId": HIVE_SID, "requestId": "req-2", "toolName": "Bash",
                                 "toolInput": {"command": "x"}, "options": ["approve", "deny"]})
    assert not await ac.mod.request_review(Router(Unit(HIVE_SID)), HIVE_SID, pm=pm)
    assert requests == []


async def test_turn_seal_then_review(ac, monkeypatch):
    """The router's turn seal asks for the review after the turn's entry is sealed."""
    from core import hive_records, project_registry
    from core.session_router import SessionRouter
    order: list = []
    monkeypatch.setattr(hive_records, "seal_turn", lambda *a, **k: order.append("sealed"))
    monkeypatch.setattr(project_registry, "get_swarmws", lambda: ac.ws)

    async def asked(router, sid, pm=None):
        order.append(("review", sid))
        return True
    monkeypatch.setattr(ac.mod, "request_review", asked)
    await SessionRouter._seal_turn_record(SimpleNamespace(), HIVE_SID, "2026-11-20T00:00:00")
    assert order == ["sealed", ("review", HIVE_SID)]
    order.clear()
    monkeypatch.delenv("SWARMAI_CARRIER")
    await SessionRouter._seal_turn_record(SimpleNamespace(), HIVE_SID, "2026-11-20T00:00:00")
    assert order == ["sealed"]


async def test_idle_hook_never_delays_the_review_in_the_mode(ac, requests, monkeypatch):
    from core.ingestion import s2_jobs
    from core.ingestion.sources.s2_session import SessionExtractionHook
    from core import hive_records
    ctx = SimpleNamespace(session_id=HIVE_SID, deleting=False, sdk_session_id="sdk-1")
    jobs = {"open": 1}
    monkeypatch.setattr(s2_jobs, "open_jobs", lambda sid=None, **kw: jobs["open"])
    await SessionExtractionHook().execute(ctx)
    assert requests == []                         # the turn's end asked: its job is open
    jobs["open"] = 0
    hive_records.append(ac.ws, HIVE_SID, "turn", data=b"{}")
    hive_records.append(ac.ws, HIVE_SID, "closed", data=b"{}")
    await SessionExtractionHook().execute(ctx)
    assert requests == []                         # reviewed and closed: nothing to ask
    # a later turn ended without a review asked (background work at its end): asked now
    hive_records.append(ac.ws, HIVE_SID, "turn", data=b"{}")
    await SessionExtractionHook().execute(ctx)
    assert requests == [(HIVE_SID, "session_end", {"sdk_session_id": "sdk-1"})]   # no delay
    requests.clear()
    monkeypatch.delenv("SWARMAI_CARRIER")
    await SessionExtractionHook().execute(ctx)
    assert requests == [(HIVE_SID, "session_end", {"sdk_session_id": "sdk-1", "delay": s2_jobs.REVIEW_DELAY})]


# ── review round: the busy windows, the entry files, the cursor ──────────────

async def test_an_open_turn_counts_as_streaming(ac, pm, monkeypatch):
    """The unit is IDLE once the result arrived, while the turn still writes its outcome,
    seals its bundles and spawns its record seal: busy until the turn closes."""
    from core import session_events
    hub = session_events.hub()
    monkeypatch.setattr(hub, "open_turn_id", lambda sid: "turn-1" if sid == HIVE_SID else None)
    got = await _busy(ac, Router(Unit(HIVE_SID)), pm)
    assert "streaming" in got["reasons"]


def test_review_job_end_and_its_seal_are_one_busy_step(ac, monkeypatch):
    """Between ``finish`` (the job is done) and ``seal_end`` (lessons, ``closed``) the
    worker holds the busy counter."""
    from core import hive_records
    from core.ingestion import s2_jobs, worker
    seen: list = []
    monkeypatch.setattr(s2_jobs, "_WAKER", None)
    monkeypatch.setattr(s2_jobs, "_bound_project", lambda sid: None)
    monkeypatch.setattr(worker, "ingestion_admission", lambda lane, **kw: SimpleNamespace(run=True, llm=True))
    monkeypatch.setattr(hive_records, "on_session_job_end",
                        lambda sid, state: seen.append((state, s2_jobs.open_jobs(), ac.mod._holds)))
    s2_jobs.request(HIVE_SID, "session_end")
    from core.ingestion.api import connect_rw
    conn = connect_rw()
    try:
        ex = worker.S2JobExecutor.__new__(worker.S2JobExecutor)
        ex._stop = __import__("threading").Event()
        ex._held_until = 0.0
        ex._extract = lambda sid: None
        assert ex.drain(conn) == 1
    finally:
        conn.close()
    assert seen == [("done", 0, 1)]     # the job is no longer open, the hold still is
    assert ac.mod._holds == 0


def test_open_jobs_by_session_and_age(ac, monkeypatch):
    from core.ingestion import s2_jobs
    from core.ingestion.api import connect_rw
    monkeypatch.setattr(s2_jobs, "_WAKER", None)
    monkeypatch.setattr(s2_jobs, "_bound_project", lambda sid: None)
    assert s2_jobs.open_jobs() == 0
    s2_jobs.request(HIVE_SID, "session_end")
    assert s2_jobs.open_jobs() == 1 and s2_jobs.open_jobs(HIVE_SID) == 1 and s2_jobs.open_jobs("other") == 0
    conn = connect_rw()
    try:
        conn.execute("UPDATE ingestion_s2_jobs SET requested_at='2026-01-01T00:00:00+00:00'")
        conn.commit()
    finally:
        conn.close()
    # postponed for hours (no model): no longer holds the microVM
    assert s2_jobs.open_jobs(younger_than=s2_jobs.MAX_WAIT) == 0 and s2_jobs.open_jobs() == 1
    assert not ac.mod._review_open()


def test_an_entry_file_that_is_a_link_or_fifo_is_not_uploaded(ac, tmp_path):
    import os
    from core import hive_records
    secret = tmp_path / "secret.txt"
    secret.write_text("not a record")
    e1 = hive_records.append(ac.ws, HIVE_SID, "turn", data=b"x" * 12)
    e2 = hive_records.append(ac.ws, HIVE_SID, "turn", data=b"y" * 12)
    assert ac.mod.flush()
    ac.s3.objects.clear()
    ac.mod._save_cursor({"session": HIVE_SID, "exported": 0})
    d = hive_records.session_dir(ac.ws, HIVE_SID)
    (d / e1["file"]).unlink()
    (d / e1["file"]).symlink_to(secret)
    (d / e2["file"]).unlink()
    os.mkfifo(d / e2["file"])
    assert ac.mod._export(ac.ws, None) == 2                       # returns: no read blocks
    assert f"bridge/{AC_SID}/records/{e1['file']}" not in ac.s3.objects
    assert f"bridge/{AC_SID}/records/{e2['file']}" not in ac.s3.objects
    assert b"not a record" not in b"".join(ac.s3.objects.values())
    assert f"bridge/{AC_SID}/records/manifest/000002.json" in ac.s3.objects   # the Host names the gap


def test_a_bad_or_lost_cursor_starts_again_and_skips_nothing(ac):
    from core import hive_records
    hive_records.append(ac.ws, HIVE_SID, "turn", data=b"{}")
    assert ac.mod.flush() and not ac.mod.export_pending(ac.ws)
    ac.s3.fail = True
    hive_records.append(ac.ws, HIVE_SID, "turn", data=b"{}")
    assert ac.mod.flush() and ac.mod.export_pending(ac.ws)
    ac.s3.fail = False
    for bad in ({"session": HIVE_SID, "exported": 999}, {"session": HIVE_SID, "exported": "2"}):
        ac.mod._save_cursor(bad)
        assert ac.mod.export_pending(ac.ws)                       # beyond the manifest / not a seq: 0
    ac.mod._export_path().unlink()                               # lost in a crash
    assert ac.mod.export_pending(ac.ws)                          # the only record is the run's
    assert ac.mod._export(ac.ws, None) == 2 and not ac.mod.export_pending(ac.ws)
    assert f"bridge/{AC_SID}/records/manifest/000002.json" in ac.s3.objects


def test_append_never_waits_for_s3(ac):
    """The upload runs on the export lane: an append returns while S3 hangs."""
    import threading
    import time as _t
    from core import hive_records
    gate = threading.Event()
    real = ac.s3.put_object
    ac.s3.put_object = lambda **kw: (gate.wait(5), real(**kw))[1]
    t0 = _t.monotonic()
    hive_records.append(ac.ws, HIVE_SID, "turn", data=b"{}")
    assert _t.monotonic() - t0 < 1.0 and ac.mod.export_pending(ac.ws)
    gate.set()
    assert ac.mod.flush() and not ac.mod.export_pending(ac.ws)


async def test_request_review_never_raises(ac, monkeypatch):
    class BadPM:
        def open_prompts(self, sid):
            raise RuntimeError("board gone")
    assert await ac.mod.request_review(Router(Unit(HIVE_SID)), HIVE_SID, pm=BadPM()) is False
