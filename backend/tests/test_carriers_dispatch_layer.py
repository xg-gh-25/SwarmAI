"""Every Swarm Out caller goes through the dispatch layer (design
docs/design/2026-11-20-swarm-out-multi-carrier.md §4.3-§4.5; R2.2-R2.4, R1.4).

Questions: (1) does a Brain dispatch place the task over the carriers' offers, open it on
the chosen location, and write the row's ``rounds`` (one handle with the session, no
credential) and the placement record next to ``hive_reason``; (2) when the chosen Hive is
found full before anything is written to it, does the dispatch go to the next one, the
first named in ``tried``; (3) when nothing can take the task, is the refusal recorded with
every candidate's reason and said in the error; (4) is one dispatch counted in flight
exactly once, by the Hive's id, and does a second placement see it in the load; (5) does
a row written before ``rounds`` still fetch its result, settle (release), stop and answer
its waits; (6) when a carrier opens a new round for a correction, is it appended to the
run's rounds and read as the current one; (7) do the timers skip a carrier without the
duty.
"""
from __future__ import annotations

import asyncio
import io
import json

import httpx
import pytest

from core import swarm_out as so
from core.carriers import ec2, registry
from core.carriers.base import EC2, TIER_WARM, Needs, Offer, RunHandle, current_handle
from tests import _hive_work_fake as _work
from tests.test_swarm_out_host import INST as _HOST
from tests.test_task_spec import _brain, ws  # noqa: F401 — the Brain fixture

ACCT = {"id": "a1", "auth_method": "access_keys"}        # a legacy row: the Host's own
HIVES = {
    "h0": {"id": "h0", "name": "worker-0", "cloudfront_domain": "aaa000.cloudfront.net"},
    "h1": {"id": "h1", "name": "worker-1", "cloudfront_domain": "bbb111.cloudfront.net"},
}
for _h in HIVES.values():
    _h.update(status="running", version="2.5.2", s3_bucket="swarmai-hive-2361-us-east-1",
              region="us-east-1", account_ref="a1",
              **{k: _HOST[k] for k in ("auth_user", "auth_password")})   # the Host suite's fake credentials
CAPABLE = {"streaming": 0, "saturated": False, "memory_percent": 10.0, "capable": True}
DID = "so_0123456789ab"


@pytest.fixture
def world(tmp_path, monkeypatch):
    """Two running Hives of this Host behind one fake transport (by CloudFront host)."""
    from hive import accounts
    w = {"seen": [], "admission": {}, "sid": {}, "in_flight_at_prepare": []}
    monkeypatch.setattr(so, "_state_dir", lambda: tmp_path)
    monkeypatch.setattr(so, "_MIN_STEP_S", 0.01, raising=False)
    monkeypatch.setattr(accounts, "swarmai_account", lambda: None)
    monkeypatch.setattr(so, "_instance", ec2.instance)
    monkeypatch.setattr(so, "_instance_up", ec2.instance_up)

    async def row_and_account(ref):
        for h in HIVES.values():
            if ref in (h["id"], h["name"]):
                return dict(h), dict(ACCT)
        raise so.SwarmOutError(f"no single Hive named {ref!r}")
    monkeypatch.setattr(so, "_row_and_account", row_and_account)

    async def hive_rows():
        return [{"id": h["id"], "name": h["name"], "status": h["status"], "version": h["version"],
                 "auth_method": ACCT["auth_method"], "account_id": None} for h in HIVES.values()]

    async def admission_probe(hid):
        return dict(CAPABLE)
    monkeypatch.setattr(ec2, "_hive_rows", hive_rows)
    monkeypatch.setattr(so, "_admission", admission_probe)

    def handler(request: httpx.Request):
        host, path = request.url.host, request.url.path
        w["seen"].append((host.split(".")[0], request.method, path))
        if path == "/api/chat/sessions/admission-state":
            return httpx.Response(200, json={**_work.ADMISSION, "streaming_count": 0,
                                             **w["admission"].get(host.split(".")[0], {})})
        if path == "/api/hive/work":
            w["in_flight_at_prepare"].append(dict(so._IN_FLIGHT))
        if _work.answer(request) is not None:
            return _work.answer(request)
        if path == "/api/chat/stream":
            sid = "sess-" + host.split(".")[0]
            body = f"data: {json.dumps({'type': 'session_start', 'session_id': sid})}\n\n"
            return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})
        if path.startswith("/api/chat/sessions/") and path.endswith("/messages"):
            return httpx.Response(200, json=[])
        if path.startswith("/api/chat/stop/"):
            return httpx.Response(200, json={})
        return httpx.Response(404, json={})

    def client(inst, timeout=None):
        return httpx.AsyncClient(base_url=so._base(inst), headers=so._auth(inst),
                                 transport=httpx.MockTransport(handler))
    monkeypatch.setattr(so, "_client", client)
    so._IN_FLIGHT.clear()
    yield w
    so._IN_FLIGHT.clear()


@pytest.fixture
def brain_ok(monkeypatch):
    from core import brain_sync_host as host

    async def hive_brains(inst):
        return {"supported": True, "brains": {"Lib": {"digest": "0" * 64, "current_digest": "0" * 64}}}

    async def nothing(*a, **kw):
        return None
    monkeypatch.setattr(host, "hive_brains", hive_brains)
    monkeypatch.setattr(host, "sync", nothing)
    monkeypatch.setattr(so, "_ship_repos", nothing)


def _dispatch(ws, hive_ref=None):
    from core.project_agent import revision
    rev = revision(_brain(ws))
    return asyncio.run(so.dispatch_spec(hive_ref, "t", "Lib", "fix it", "tests pass", execution="native",
                                        returns="commits", seen_revision=rev))


def _log(tmp):
    return [json.loads(x) for x in (so._state_dir() / "dispatches.jsonl").read_text().splitlines()]


# ── (1) placed, opened, its round kept ──

def test_a_dispatch_writes_its_round_and_placement_without_credentials(world, brain_ok, ws):
    out = _dispatch(ws)
    line = (so._state_dir() / "dispatches.jsonl").read_text()
    row = json.loads(line)
    assert row["hive"] == "worker-0" and row["session_id"] == "sess-aaa000"
    [r] = row["rounds"]
    assert (r["carrier"], r["location_id"], r["round"], r["run_ref"]) == (EC2, "h0", 1, "sess-aaa000")
    assert r["address"] == "aaa000.cloudfront.net" and r["auth_ref"] == "hive_instances:h0"
    assert current_handle(row) == RunHandle.from_row(r)
    # no credential anywhere in the row: neither the password nor the header made of it
    assert _HOST["auth_password"] not in line and "Basic" not in line and "auth_password" not in line
    assert row["hive_reason"] == out["hive_reason"] == "idle"
    assert row["placement"] == {"location": "worker-0", "layer": TIER_WARM, "reason": "idle",
                                "tried": [], "skipped": []}
    assert [t["step"] for t in row["timings"]][:2] == ["choosing a Hive", "checking Hive worker-0"]


def test_a_named_hive_is_placed_without_probing_the_others(world, brain_ok, ws):
    out = _dispatch(ws, "worker-1")
    assert out["hive"] == "worker-1" and out["hive_reason"] == "named"
    assert out["rounds"][0]["location_id"] == "h1"            # the id, read when it was opened
    assert {h for h, _, _ in world["seen"]} == {"bbb111"}
    assert "choosing a Hive" not in [t["step"] for t in out["timings"]]


# ── (2) the next one, only when the first was unavailable before any write ──

def test_a_full_hive_found_at_open_passes_the_task_to_the_next(world, brain_ok, ws):
    world["admission"]["aaa000"] = {"saturated": True}
    out = _dispatch(ws)
    assert out["hive"] == "worker-1" and out["rounds"][0]["location_id"] == "h1"
    tried = out["placement"]["tried"]
    assert [t[0] for t in tried] == ["worker-0"] and "busy" in tried[0][1]
    # worker-0 was asked its admission state and nothing else
    assert [p for h, _, p in world["seen"] if h == "aaa000"] == ["/api/chat/sessions/admission-state"]


def test_a_failure_after_a_write_is_not_passed_on(world, brain_ok, ws, monkeypatch):
    from core import brain_sync_host as host

    async def broken(inst):
        raise host.SyncRefused("the package is too large")
    monkeypatch.setattr(host, "hive_brains", broken)
    with pytest.raises(so.SwarmOutError, match="could not be synced to worker-0"):
        _dispatch(ws)
    assert not any(h == "bbb111" for h, _, _ in world["seen"])


# ── (3) nothing can take it ──

def test_no_location_is_a_refusal_with_every_candidates_reason(world, brain_ok, ws, monkeypatch):
    async def probe(hid):
        return {**CAPABLE, "saturated": True} if hid == "h0" else None
    monkeypatch.setattr(so, "_admission", probe)
    with pytest.raises(so.SwarmOutError) as e:
        _dispatch(ws)
    assert "worker-0: saturated" in str(e.value) and "worker-1: unreachable" in str(e.value)
    assert "nothing was sent" in str(e.value)
    rec = json.loads((so._state_dir() / "refusals.jsonl").read_text().splitlines()[-1])
    assert rec["brain"] == "Lib" and rec["private"] is False
    assert rec["reasons"] == ["worker-0: saturated", "worker-1: unreachable"]
    assert rec["placement"]["location"] is None and rec["placement"]["skipped"] == [
        ["worker-0", "saturated"], ["worker-1", "unreachable"]]
    assert rec["kind"] == "placement"
    assert so.refusal_counts() == {"total": 0, "private": 0}   # not counted as an intake refusal
    assert not (so._state_dir() / "dispatches.jsonl").exists()
    assert world["seen"] == []                                  # nothing was asked of a Hive


def test_every_hive_unavailable_at_open_is_a_refusal_naming_each(world, brain_ok, ws):
    world["admission"]["aaa000"] = {"saturated": True}
    world["admission"]["bbb111"] = {"zero_touch": False}
    with pytest.raises(so.SwarmOutError) as e:
        _dispatch(ws)
    assert "worker-0: Hive worker-0 is busy" in str(e.value)
    assert "worker-1: Hive worker-1's version waits for a person" in str(e.value)
    rec = json.loads((so._state_dir() / "refusals.jsonl").read_text().splitlines()[-1])
    assert [t[0] for t in rec["placement"]["tried"]] == ["worker-0", "worker-1"]
    assert so._IN_FLIGHT == {}


def test_a_later_candidates_failure_keeps_what_the_earlier_ones_said(world, brain_ok, ws, monkeypatch):
    from hive import accounts
    # worker-0 is full at open; worker-1 was offered as the SwarmAI account's but its account
    # row says another's when it is opened (refused, not unavailable: no failover past it)
    monkeypatch.setattr(accounts, "swarmai_account", lambda: {"account_id": "1", "region": "us-east-1"})

    async def hive_rows():
        return [{"id": h["id"], "name": h["name"], "status": "running", "version": h["version"],
                 "auth_method": "swarmai", "account_id": "1"} for h in HIVES.values()]
    monkeypatch.setattr(ec2, "_hive_rows", hive_rows)
    world["admission"]["aaa000"] = {"saturated": True}

    async def row_and_account(ref):
        h = HIVES["h0"] if ref in ("h0", "worker-0") else HIVES["h1"]
        return dict(h), {"id": "a1", "auth_method": "swarmai" if h["id"] == "h0" else "access_keys"}
    monkeypatch.setattr(so, "_row_and_account", row_and_account)
    with pytest.raises(so.SwarmOutError) as e:
        _dispatch(ws)
    assert "Hive worker-1 is in an account this Host may not run tasks on" in str(e.value)
    assert "before it: worker-0: Hive worker-0 is busy" in str(e.value)
    assert so._IN_FLIGHT == {}


def test_the_next_candidate_must_fit_with_the_sessions_window(world, brain_ok, ws, monkeypatch):
    seen = {}
    real = so.open_with_failover

    async def spy(placement, open_fn, time_left):
        seen["left"], seen["deadline"] = time_left(), so._Deadline(so.DISPATCH_DEADLINE_S).left()
        return await real(placement, open_fn, time_left)
    monkeypatch.setattr(so, "open_with_failover", spy)
    _dispatch(ws)
    assert seen["left"] <= seen["deadline"] - so.SESSION_ID_WAIT_S


# ── (4) in flight once, and counted in the next placement's load ──

def test_a_dispatch_is_in_flight_once_by_its_hives_id(world, brain_ok, ws):
    _dispatch(ws)
    assert world["in_flight_at_prepare"] == [{"h0": 1}]          # once, by id (it was twice before)
    assert so._IN_FLIGHT == {}


def test_a_placement_sees_the_dispatches_in_flight(world):
    async def go():
        with so._Placed() as mark:
            mark.at("h0")                                       # another dispatch placed on worker-0
            first = await so._place(Needs(), so._Deadline(so.DISPATCH_DEADLINE_S))
        second = await so._place(Needs(), so._Deadline(so.DISPATCH_DEADLINE_S))
        return first, second
    first, second = asyncio.run(go())
    assert first.offer.name == "worker-1" and first.reason == "idle"
    assert {o.name: o.in_flight for o in (first.offer, *first.candidates)} == {"worker-1": 0, "worker-0": 1}
    assert second.offer.name == "worker-0" and so._IN_FLIGHT == {}


# ── (5) a row written before rounds ──

LEGACY = {"dispatch_id": DID, "hive_id": "h1", "hive": "worker-1", "session_id": "sess-legacy",
          "title": "t", "sent_at": "2026-11-01T00:00:00Z",
          "project_agent": {"brain": "Lib", "mode": "code-change", "revision": "r"}}


class _S3:
    """Just enough of an S3 client for a Hive's bridge."""

    class exceptions:
        class NoSuchKey(Exception):
            pass

    def __init__(self):
        self.objects: dict[str, bytes] = {}

    def get_object(self, Bucket, Key, **kw):
        if Key not in self.objects:
            raise self.exceptions.NoSuchKey(Key)
        return {"Body": io.BytesIO(self.objects[Key])}

    def put_object(self, Bucket, Key, Body, **kw):
        self.objects[Key] = Body


def test_a_row_without_rounds_fetches_settles_stops_and_answers(world, monkeypatch):
    from core import swarm_out_results, swarm_out_settle
    assert "rounds" not in LEGACY
    monkeypatch.setattr(so, "_read_log", lambda: [dict(LEGACY)])
    rec = asyncio.run(swarm_out_results.fetch(dict(LEGACY)))
    assert rec["fetch_error"] is None
    assert ("bbb111", "GET", "/api/chat/sessions/sess-legacy/messages") in world["seen"]
    # settled: its Hive is told to release the workspace, and that is recorded
    swarm_out_settle._update(DID, lambda r: r.update(settled={"released": False, "at": 1}))
    asyncio.run(swarm_out_settle._tell(dict(LEGACY)))
    assert ("bbb111", "POST", f"/api/hive/work/{DID}/release") in world["seen"]
    assert swarm_out_settle.load(DID)["settled"]["released"] is True
    asyncio.run(so.stop_turn(DID))
    assert ("bbb111", "POST", "/api/chat/stop/sess-legacy") in world["seen"]
    # a wait of its session is answered on its Hive's bridge
    s3 = _S3()
    ask = {"kind": "question", "session_id": "sess-legacy", "request_id": "q1", "asked_at":
           __import__("time").strftime("%Y-%m-%dT%H:%M:%SZ", __import__("time").gmtime()),
           "questions": [{"question": "Which?", "options": [{"label": "A"}, {"label": "B"}]}]}
    s3.objects["bridge/worker-1/ask/sess-legacy/q1.json"] = json.dumps(ask).encode()
    monkeypatch.setattr(so, "_s3", lambda inst, purpose="manage": s3)
    assert asyncio.run(so.answer("h1", "sess-legacy", "q1", answers={"Which?": "A"}))["ok"] is True
    assert json.loads(s3.objects["bridge/worker-1/answer/sess-legacy/q1.json"])["answers"] == {"Which?": "A"}


# ── (6) a new round ──

class _Reopening:
    """A carrier whose run ends with its round: a correction gets a new one."""
    kind = "fake"
    reaches_private = False
    max_run_s = None
    brain_lock_scope = "run"

    def __init__(self, world):
        self.world = world

    async def offers(self, needs):
        return []

    def location(self, location_id):
        raise AssertionError("not used")

    async def next_round(self, handle, dispatch_id):
        return RunHandle(carrier="fake", location_id="rt", round=handle.round + 1, run_ref=None)

    async def connect(self, handle):
        return dict(HIVES["h1"])          # its round answers like a Hive here

    def client(self, handle, timeout=None):
        raise AssertionError("connect's record is used")


def test_a_correction_on_a_new_round_appends_it(world, monkeypatch):
    from core import swarm_out_remote, swarm_out_results
    # a run without a Project Agent too: its later rounds are read like any other's
    row = {k: v for k, v in LEGACY.items() if k != "project_agent"}
    row = {**row, "session_id": "sess-old",
           "rounds": [RunHandle(carrier="fake", location_id="rt", round=1, run_ref="sess-old").to_row()]}
    monkeypatch.setattr(so, "_read_log", lambda: [dict(row)])
    monkeypatch.setitem(registry._MAKERS, "fake", lambda: _Reopening(world))
    monkeypatch.delitem(registry._made, "fake", raising=False)
    monkeypatch.setattr(swarm_out_results, "load", lambda did: {})
    monkeypatch.setattr(swarm_out_results, "turn_started", lambda did: None)
    assert asyncio.run(so.rerun(DID, "do it again"))["sent"] is True
    # the message went into a new session on the new round, not into the old one
    assert ("bbb111", "POST", "/api/chat/stream") in world["seen"]
    got = so._log_row_of(DID)
    assert [(r["round"], r["run_ref"]) for r in got["rounds"]] == [(1, "sess-old"), (2, "sess-bbb111")]
    assert current_handle(got).run_ref == "sess-bbb111"
    # every reader of the row's session reads the current round's (results, live, records)
    assert got["session_id"] == "sess-bbb111"

    async def not_local(sid):
        return False
    monkeypatch.setattr(swarm_out_remote, "_is_local_session", not_local)
    assert asyncio.run(swarm_out_remote.resolve_session("sess-bbb111"))["dispatch_id"] == DID
    assert asyncio.run(swarm_out_remote.resolve_session("sess-old"))["dispatch_id"] == DID


def test_a_round_of_an_unknown_carrier_is_a_run_out_of_reach(world, monkeypatch):
    row = {**LEGACY, "rounds": [RunHandle(carrier="gone", location_id="x", round=1, run_ref="s").to_row()]}
    with pytest.raises(so.SwarmOutError, match="names nothing this Host can reach"):
        registry.handle_of(row)
    monkeypatch.setattr(so, "_read_log", lambda: [dict(row)])
    out = asyncio.run(so.discard_run(DID))
    assert out == {"discarded": True, "stopped": False}
    # a rounds list with no round in it reads as the row's Hive and session
    assert registry.handle_of({**LEGACY, "rounds": ["junk"]}).location_id == "h1"


def test_a_dispatch_goes_to_the_opened_round_not_to_a_name(world):
    # placement opened worker-1's round; the name given is worker-0's: the round decides
    # (an elastic round has no name to look up at all: tests/test_elastic_dispatch_e2e)
    d = so._Deadline(so.DISPATCH_DEADLINE_S)
    d.opened = RunHandle(carrier=EC2, location_id="h1", round=1, run_ref=None,
                         address="bbb111.cloudfront.net", auth_ref="hive_instances:h1")
    row = asyncio.run(so.dispatch("worker-0", "t", "do it", deadline=d))
    assert (row["hive_id"], row["session_id"]) == ("h1", "sess-bbb111")
    assert {host for host, _m, _p in world["seen"]} == {"bbb111"}
    assert current_handle(row) == d.opened.with_session("sess-bbb111")


# ── (7) duties ──

def test_timers_skip_a_carrier_without_the_duty(world, monkeypatch):
    from core import hive_idle, usage_hive_sync
    from core.carriers.base import IDLE_STOP, USAGE_SYNC
    assert [c.kind for c in registry.with_duty(USAGE_SYNC)] == [EC2]
    monkeypatch.setitem(registry._MAKERS, "fake", lambda: _Reopening(world))
    monkeypatch.delitem(registry._made, "fake", raising=False)
    assert [c.kind for c in registry.with_duty(IDLE_STOP)] == [EC2]     # the fake has no duties
    monkeypatch.setattr(registry, "_MAKERS", {"fake": lambda: _Reopening(world)})
    monkeypatch.setattr(registry, "_made", {})

    async def boom():
        raise AssertionError("no Hive is listed for a carrier without the duty")
    monkeypatch.setattr(so, "_hive_statuses", boom)
    monkeypatch.setattr(hive_idle, "_running_hives", boom)
    monkeypatch.setattr(hive_idle, "idle_after_s", lambda *a: 60.0)
    assert asyncio.run(usage_hive_sync.sync_once()) == {}
    assert asyncio.run(hive_idle.check_once()) == []


def test_the_offer_of_a_named_location_reads_no_store(monkeypatch):
    async def boom():
        raise AssertionError("a named Hive is not looked for among the Hives")
    monkeypatch.setattr(ec2, "_hive_rows", boom)
    [o] = asyncio.run(ec2.Ec2Carrier().offers(Needs(named="worker-9")))
    assert (o.carrier, o.location_id, o.name, o.tier, o.blocked) == (EC2, "worker-9", "worker-9", TIER_WARM, None)
    assert isinstance(o, Offer)
