"""The EC2 carrier (core/carriers/ec2.py; design 2026-11-20-swarm-out-multi-carrier §5, §4.2-§4.5).

Questions: (1) does ``offers`` give one offer per Hive of a manageable account, with each
blocked reason (saturated, out_of_date, unreachable, unknown_version) and none for another
account's Hive (R1.8); (2) is a stopped Hive offered as able only when its last probe
while running, at its present version, found it capable, and does a probe while running
keep that; (3) does ``open_run`` raise LocationUnavailable, before any write, when the Hive
is full, silent or out of date, and otherwise return the round's handle; (4) do handles
survive the dispatch row (to_row/from_row, a legacy row without rounds); (5) do client,
release, state and outputs reach today's code for the handle's Hive; (6) the registry.
"""
from __future__ import annotations

import asyncio
import json
import sqlite3

import httpx
import pytest

from core import swarm_out as so
from core.carriers import ec2, registry
from core.carriers.base import (EC2, TIER_COLD, TIER_WARM, Carrier, Location, LocationUnavailable,
                                Needs, RunHandle, current_handle, rounds_of)
from hive import accounts

MODE = {"account_id": "111111111111", "region": "us-east-1"}
CAPABLE = {"streaming": 1, "saturated": False, "memory_percent": 40.0, "capable": True}

# id, name, status, version, account
HIVES = [
    ("h-free", "w-free", "running", "1.2", "acc-s"),
    ("h-full", "w-full", "running", "1.2", "acc-s"),
    ("h-old", "w-old", "running", "1.0", "acc-s"),
    ("h-dark", "w-dark", "running", "1.2", "acc-s"),
    ("h-known", "c-known", "stopped", "1.2", "acc-s"),
    ("h-new", "c-new", "stopped", "1.2", "acc-s"),
    ("h-upd", "c-upd", "stopped", "1.2", "acc-s"),
    ("h-bad", "c-bad", "stopping", "1.0", "acc-s"),
    ("h-other", "x-other", "running", "1.2", "acc-o"),
    ("h-otherc", "x-otherc", "stopped", "1.2", "acc-o"),
    ("h-busy", "u-busy", "updating", "1.2", "acc-s"),
]
PROBES = {"h-free": CAPABLE, "h-full": {**CAPABLE, "saturated": True, "streaming": 4},
          "h-old": {**CAPABLE, "saturated": True, "capable": False}, "h-dark": None,
          "h-other": CAPABLE}


@pytest.fixture
def world(tmp_path, monkeypatch):
    db = tmp_path / "swarm.db"
    with sqlite3.connect(db) as c:
        c.execute("CREATE TABLE hive_accounts (id TEXT PRIMARY KEY, account_id TEXT, auth_method TEXT)")
        c.execute("CREATE TABLE hive_instances (id TEXT PRIMARY KEY, name TEXT, status TEXT, "
                  "version TEXT, account_ref TEXT)")
        c.execute("INSERT INTO hive_accounts VALUES ('acc-s', ?, 'swarmai')", (MODE["account_id"],))
        c.execute("INSERT INTO hive_accounts VALUES ('acc-o', '222222222222', 'access_keys')")
        c.executemany("INSERT INTO hive_instances VALUES (?, ?, ?, ?, ?)", HIVES)
    state = tmp_path / "state"
    state.mkdir()
    (state / ec2._PROBES_FILE).write_text(json.dumps({
        "h-known": {"version": "1.2", "capable": True, "at": 1.0},
        "h-upd": {"version": "1.1", "capable": True, "at": 1.0},
        "h-bad": {"version": "1.0", "capable": False, "at": 1.0}}))
    monkeypatch.setattr(so, "_db_path", lambda: str(db))
    monkeypatch.setattr(so, "_state_dir", lambda: state)
    monkeypatch.setattr(accounts, "swarmai_account", lambda: dict(MODE))
    asked: list[str] = []

    async def admission(hid):
        asked.append(hid)
        return PROBES[hid]
    monkeypatch.setattr(so, "_admission", admission)
    monkeypatch.setattr(so, "open_runs_by_hive", lambda strict=False: {"w-free": 2, "c-new": 1})
    so._IN_FLIGHT.clear()
    yield {"db": db, "state": state, "asked": asked}
    so._IN_FLIGHT.clear()


def _offers():
    return {o.name: o for o in asyncio.run(ec2.Ec2Carrier().offers(Needs()))}


def test_offers_map_each_hive_and_leave_other_accounts_out(world):
    so._IN_FLIGHT["w-free"] = 1
    got = _offers()
    assert set(got) == {"w-free", "w-full", "w-old", "w-dark", "c-known", "c-new", "c-upd", "c-bad"}
    assert "h-other" not in world["asked"]                     # another account's Hive is not even probed
    free = got["w-free"]
    assert (free.carrier, free.location_id, free.tier, free.blocked) == (EC2, "h-free", TIER_WARM, None)
    assert (free.open_runs, free.in_flight, free.streaming, free.memory_percent) == (2, 1, 1, 40.0)
    assert (free.reaches_private, free.brain_lock_scope, free.start_s) == (False, "machine", 0.0)
    assert got["w-full"].blocked == "saturated" and got["w-full"].streaming == 4
    assert got["w-old"].blocked == "out_of_date"               # full too, but out of date is not full (D5)
    assert got["w-dark"].blocked == "unreachable" and got["w-dark"].tier == TIER_WARM
    known = got["c-known"]
    assert (known.tier, known.blocked, known.start_s) == (TIER_COLD, None, float(so.START_TIMEOUT_S))
    assert got["c-new"].blocked == "unknown_version" and got["c-new"].open_runs == 1
    assert got["c-upd"].blocked == "unknown_version"           # capable at 1.1, now at 1.2
    assert got["c-bad"].blocked == "out_of_date"               # known incapable at its version


def test_legacy_mode_offers_legacy_rows_only(world, monkeypatch):
    monkeypatch.setattr(accounts, "swarmai_account", lambda: None)
    got = _offers()
    # no SwarmAI account connected: its row is "disconnected" and a legacy row is the Host's own
    assert set(got) == {"x-other", "x-otherc"}
    assert got["x-other"].blocked is None and got["x-otherc"].blocked == "unknown_version"


def test_a_probe_while_running_is_kept_for_when_it_stops(world):
    _offers()
    kept = json.loads((world["state"] / ec2._PROBES_FILE).read_text())
    assert kept["h-free"]["capable"] is True and kept["h-free"]["version"] == "1.2"
    assert kept["h-old"]["capable"] is False
    assert "h-dark" not in kept and "h-other" not in kept      # no answer, other account: nothing kept
    assert kept["h-known"]["version"] == "1.2"                 # earlier entries stay
    with sqlite3.connect(world["db"]) as c:
        c.execute("UPDATE hive_instances SET status = 'stopped' WHERE id IN ('h-free', 'h-old')")
    got = _offers()
    assert got["w-free"].tier == TIER_COLD and got["w-free"].blocked is None
    assert got["w-old"].tier == TIER_COLD and got["w-old"].blocked == "out_of_date"


def test_an_unreadable_probe_file_offers_no_stopped_hive_as_able(world):
    (world["state"] / ec2._PROBES_FILE).write_text("{not json")
    got = _offers()
    assert got["c-known"].blocked == "unknown_version"


# -- open_run ------------------------------------------------------------------------------

INST = {"id": "h-free", "name": "w-free", "version": "1.2", "cloudfront_domain": "d123.cloudfront.net"}


@pytest.fixture
def hive(world, monkeypatch):
    """A Hive answering admission-state with ``h["admission"]`` (or raising it)."""
    h = {"admission": {"streaming_count": 0, "saturated": False, "memory_percent": 12,
                       **{c: True for c in so.REQUIRED_CAPS}},
         "requests": [], "started": []}

    async def instance_up(ref, deadline, held=frozenset()):
        h["started"].append((ref, deadline))
        return dict(INST)
    monkeypatch.setattr(so, "_instance_up", instance_up)

    async def instance(ref):
        assert ref == "h-free"
        return dict(INST)
    monkeypatch.setattr(so, "_instance", instance)

    def handler(request):
        h["requests"].append((request.method, request.url.path))
        if isinstance(h["admission"], Exception):
            raise h["admission"]
        if request.url.path == "/api/chat/sessions/admission-state":
            return httpx.Response(200, json=h["admission"])
        return httpx.Response(h.get("status", 200), json={})

    def client(inst, timeout=so.HTTP_TIMEOUT_S):
        return httpx.AsyncClient(base_url=f"https://{inst['cloudfront_domain']}",
                                 transport=httpx.MockTransport(handler), timeout=timeout)
    monkeypatch.setattr(so, "_client", client)
    return h


def _open(round_=1, deadline=None):
    return asyncio.run(ec2.Ec2Carrier().location("h-free").open_run("so_0123456789ab", round_, deadline))


@pytest.mark.parametrize("answer, why", [
    ({"streaming_count": 3, "saturated": True, **{c: True for c in so.REQUIRED_CAPS}}, "saturated"),
    ({"streaming_count": 0, "saturated": False, "work_orders": True}, "out_of_date"),
    ({"saturated": False}, "out_of_date"),                      # an answer without the capabilities
    (["not", "an", "object"], "unreachable"),                   # not an answer
    (httpx.ConnectError("no route"), "unreachable"),
])
def test_open_run_unavailable_before_any_write(hive, answer, why):
    # the check is today's dispatch check (swarm_out._require_work_orders), its refusal a
    # LocationUnavailable whose ``why`` placement records and whose words the card shows
    hive["admission"] = answer
    with pytest.raises(LocationUnavailable) as e:
        _open()
    assert e.value.why == why and isinstance(e.value, so.SwarmOutError) and "w-free" in str(e.value)
    assert hive["requests"] == [("GET", "/api/chat/sessions/admission-state")]   # nothing written


def test_open_run_returns_the_rounds_handle(hive):
    d = so._Deadline(so.DISPATCH_DEADLINE_S)
    h = _open(2, d)
    assert hive["started"] == [("h-free", d)]                   # through _instance_up: it starts a stopped one
    assert (h.carrier, h.location_id, h.round, h.run_ref) == (EC2, "h-free", 2, None)
    assert h.address == "d123.cloudfront.net" and h.auth_ref == "hive_instances:h-free"
    assert "auth_password" not in json.dumps(h.to_row())
    assert [t["step"] for t in d.timings] == ["checking Hive w-free"]
    kept = json.loads((so._state_dir() / ec2._PROBES_FILE).read_text())
    assert kept["h-free"] == {**kept["h-free"], "version": "1.2", "capable": True}


OTHER = {"id": "h-other", "name": "x-other", "status": "running", "version": "1.2",
         "cloudfront_domain": "d999.cloudfront.net", "s3_bucket": "swarmai-hive-x", "region": "us-east-1",
         "auth_user": "u", "auth_password": "p", "account_ref": "acc-o"}


@pytest.mark.parametrize("status", ["running", "stopped"])
def test_open_run_refuses_another_accounts_hive_before_anything(hive, monkeypatch, status):
    # the refusal lives in the lookup every open goes through (ec2.instance_up), read from
    # the Hive's own account row: a running Hive is asked nothing, a stopped one not started
    from core import hive_idle
    monkeypatch.setattr(so, "_instance_up", ec2.instance_up)
    monkeypatch.setattr(so, "_instance", ec2.instance)

    async def row_and_account(ref):
        return {**OTHER, "status": status}, {"id": "acc-o", "auth_method": "access_keys"}
    monkeypatch.setattr(so, "_row_and_account", row_and_account)

    async def wake(hid):
        hive["started"].append(hid)
    monkeypatch.setattr(hive_idle, "wake", wake)
    with pytest.raises(so.SwarmOutError, match="an account this Host may not run tasks on") as e:
        asyncio.run(ec2.Ec2Carrier().location("h-other").open_run("so_0123456789ab", 1, None))
    assert not isinstance(e.value, LocationUnavailable)
    assert hive["started"] == [] and hive["requests"] == []
    # the SwarmAI account's own Hive is opened
    async def own(ref):
        return {**OTHER, "status": "running", "id": "h-free"}, {"id": "acc-s", "auth_method": "swarmai"}
    monkeypatch.setattr(so, "_row_and_account", own)
    assert asyncio.run(ec2.Ec2Carrier().location("h-free").open_run("so_0123456789ab", 1, None)).location_id == "h-free"


def test_a_start_that_fails_is_not_a_reason_to_move_on(hive, monkeypatch):
    async def instance_up(ref, deadline, held=frozenset()):
        raise so.SwarmOutError("starting Hive w-free did not finish; nothing was sent")
    monkeypatch.setattr(so, "_instance_up", instance_up)
    with pytest.raises(so.SwarmOutError) as e:
        _open()
    assert not isinstance(e.value, LocationUnavailable)


# -- handles in the dispatch row -----------------------------------------------------------

def test_handle_round_trips_through_the_row(hive):
    h = _open(1)
    row = json.loads(json.dumps({"dispatch_id": "so_0123456789ab", "rounds": [h.to_row()]}))
    assert RunHandle.from_row(row["rounds"][0]) == h
    assert RunHandle.from_row({**h.to_row(), "later_field": 1}) == h
    assert rounds_of(row) == [h] and current_handle(row) == h


def test_rounds_of_a_legacy_row():
    row = {"dispatch_id": "so_0123456789ab", "hive_id": "h-free", "hive": "w-free", "session_id": "s-1"}
    [h] = rounds_of(row)
    assert (h.carrier, h.location_id, h.round, h.run_ref, h.auth_ref) == (
        EC2, "h-free", 1, "s-1", "hive_instances:h-free")
    assert current_handle(row) == h
    assert rounds_of({**row, "rounds": []}) == [h]


# -- per-run operations --------------------------------------------------------------------

H = RunHandle(carrier=EC2, location_id="h-free", round=1, run_ref="s-1",
              address="d123.cloudfront.net", auth_ref="hive_instances:h-free")


def test_client_is_todays_client_for_the_hive(hive):
    async def go():
        async with ec2.Ec2Carrier().client(H) as c:
            return await c.get("/api/x")
    assert asyncio.run(go()).status_code == 200
    assert hive["requests"] == [("GET", "/api/x")]
    with pytest.raises(ValueError):
        ec2.Ec2Carrier().client(RunHandle(carrier="agentcore", location_id="rt", round=1, run_ref="r"))


def test_release(hive):
    car = ec2.Ec2Carrier()
    asyncio.run(car.release(H, "so_0123456789ab"))
    assert hive["requests"] == [("POST", "/api/hive/work/so_0123456789ab/release")]
    hive["status"] = 404
    asyncio.run(car.release(H, "so_0123456789ab"))              # no such order there: nothing to remove
    hive["status"] = 409
    with pytest.raises(so.SwarmOutError):
        asyncio.run(car.release(H, "so_0123456789ab"))
    hive["requests"].clear()
    with pytest.raises(ValueError):
        asyncio.run(car.release(H, "../etc"))
    assert hive["requests"] == []


def test_state(hive, monkeypatch):
    statuses = {"h-free": "running"}

    async def hive_statuses():
        return statuses

    async def session_states(inst):
        return {"s-1": {"streaming": True, "waiting_input": False, "activity": None, "background_work": False}}
    monkeypatch.setattr(so, "_hive_statuses", hive_statuses)
    monkeypatch.setattr(so, "_session_states", session_states)
    car = ec2.Ec2Carrier()
    assert asyncio.run(car.state(H)) == ("running", "streaming")
    statuses.clear()
    assert asyncio.run(car.state(H)) == ("stopped", "hive_gone")

    async def silent(inst):
        raise httpx.ConnectError("down")
    statuses["h-free"] = "running"
    monkeypatch.setattr(so, "_session_states", silent)
    assert asyncio.run(car.state(H)) == ("unknown", "hive_silent")
    unnamed = RunHandle(carrier=EC2, location_id="h-free", round=1, run_ref=None)
    assert asyncio.run(car.state(unnamed)) == ("unknown", "no_session_id")


def test_outputs_reads_todays_result(monkeypatch):
    from core import swarm_out_results
    seen = []
    monkeypatch.setattr(so, "_log_row_of", lambda did: {"dispatch_id": did, "hive_id": "h-free",
                                                        "session_id": None, "title": "t"})

    async def fetch(row):
        seen.append(row)
        return {"dispatch_id": row["dispatch_id"], "fetch_error": None}
    monkeypatch.setattr(swarm_out_results, "fetch", fetch)
    got = asyncio.run(ec2.Ec2Carrier().outputs(H, "so_0123456789ab"))
    assert got["dispatch_id"] == "so_0123456789ab"
    assert seen[0]["session_id"] == "s-1" and seen[0]["hive_id"] == "h-free" and seen[0]["title"] == "t"


# -- registry ------------------------------------------------------------------------------

def test_registry():
    car = registry.carrier(EC2)
    assert car is registry.carrier(EC2) and isinstance(car, ec2.Ec2Carrier)
    assert isinstance(car, Carrier) and isinstance(car.location("h-1"), Location)
    assert (car.kind, car.reaches_private, car.max_run_s, car.brain_lock_scope) == (EC2, False, None, "machine")
    assert car in registry.carriers()
    with pytest.raises(KeyError):
        registry.carrier("nope")
