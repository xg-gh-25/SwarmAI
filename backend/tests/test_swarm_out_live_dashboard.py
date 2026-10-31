"""Swarm Out panel on real data (design 2026-10-28-swarm-out-live-dashboard).

Questions each test answers:
- does every run state come from the Hive's own report of the session, with the S3
  question deciding only when the Hive did not say, and a stopped Hive's runs stopped?
- are the Hives read concurrently, so one slow Hive does not add up?
- does the fleet carry only names, status and validated load numbers, for every Hive,
  with one that does not answer (or answers badly) marked unreachable?
"""
from __future__ import annotations

import asyncio
import sqlite3
import time

import httpx
import pytest

from core import swarm_out
from core.swarm_out import run_state

ROW = {"hive_id": "h1", "session_id": "s1"}
E = lambda **kw: {"streaming": False, "waiting_input": False, "activity": "idle", **kw}  # noqa: E731


@pytest.mark.parametrize("row,hive_status,sessions,ask,expect", [
    (ROW, "stopped", None, False, ("stopped", "hive_stopped")),
    (ROW, "gone", None, True, ("stopped", "hive_gone")),
    (ROW, "stopped", {"s1": E(streaming=True)}, True, ("stopped", "hive_stopped")),
    ({"hive_id": "h1", "session_id": None}, "running", {}, False, ("unknown", "no_session_id")),
    (ROW, "running", None, True, ("waiting", "question")),
    (ROW, "running", None, False, ("unknown", "hive_silent")),
    (ROW, "running", {}, True, ("stopped", "not_held")),          # a stale mirror is not waiting
    (ROW, "running", {"s1": E(waiting_input=True, activity="waiting_input")}, False, ("waiting", "waiting_input")),
    (ROW, "running", {"s1": E(activity="preparing")}, False, ("running", "preparing")),   # queued / starting
    (ROW, "running", {"s1": E(activity="stopping")}, False, ("running", "stopping")),
    (ROW, "running", {"s1": E(streaming=True, activity="streaming")}, True, ("running", "streaming")),
    (ROW, "running", {"s1": E(streaming=True, activity=None)}, False, ("running", "streaming")),
    (ROW, "running", {"s1": E()}, True, ("stopped", "idle")),
    (ROW, None, {"s1": E(streaming=True)}, False, ("running", "streaming")),   # store unreadable: the Hive's word
])
def test_run_state(row, hive_status, sessions, ask, expect):
    assert run_state(row, hive_status, sessions, ask) == expect


INST = {"name": "w", "status": "running", "cloudfront_domain": "abc123.cloudfront.net",
        "s3_bucket": "swarmai-hive-2361-us-east-1", "region": "us-east-1",
        "auth_user": "admin", "auth_password": "s3cret-pass", "account_ref": "a1"}


def _inst(hid: str) -> dict:
    return swarm_out._validate_instance({**INST, "id": hid, "name": f"w-{hid}",
                                         "cloudfront_domain": f"{hid}.cloudfront.net"}, {"id": "a1"})


def test_a_stopped_hives_runs_are_stopped_and_it_is_not_asked(monkeypatch):
    rows = [{"dispatch_id": "d1", "hive_id": "h1", "hive": "w", "session_id": "s1", "title": "A"}]
    monkeypatch.setattr(swarm_out, "_read_log", lambda: list(rows))

    async def statuses():
        return {"h1": "stopped"}
    asked = []

    async def instance(ref):
        asked.append(ref)
        raise AssertionError("a stopped Hive is not read")
    monkeypatch.setattr(swarm_out, "_hive_statuses", statuses)
    monkeypatch.setattr(swarm_out, "_instance", instance)
    out = asyncio.run(swarm_out.list_runs())
    r = out["runs"][0]
    assert (r["state"], r["state_reason"], r["running"], r["waiting"]) == ("stopped", "hive_stopped", False, False)
    assert asked == [] and out["errors"] == []


def test_the_hives_are_read_concurrently(monkeypatch):
    rows = [{"dispatch_id": f"d{i}", "hive_id": f"h{i}", "hive": "w", "session_id": "s", "title": "A"}
            for i in range(3)]
    monkeypatch.setattr(swarm_out, "_read_log", lambda: list(rows))

    async def statuses():
        return {f"h{i}": "running" for i in range(3)}

    async def read(hive_id):
        await asyncio.sleep(0.3)
        return [], {}, []
    monkeypatch.setattr(swarm_out, "_hive_statuses", statuses)
    monkeypatch.setattr(swarm_out, "_read_hive", read)
    t = time.monotonic()
    out = asyncio.run(swarm_out.list_runs())
    assert time.monotonic() - t < 0.7
    assert {r["state"] for r in out["runs"]} == {"stopped"}


def test_session_states_keeps_only_typed_fields(monkeypatch):
    def hive(request):
        return httpx.Response(200, json={"sessions": {
            "s1": {"streaming": "yes", "waiting_input": True, "activity": 7, "pid": 12, "pending": ["x"]},
            "s2": "not a dict",
            "s3": {"streaming": True, "activity": "<b>pwned</b> " * 20}}})
    monkeypatch.setattr(swarm_out, "_client", lambda inst, timeout=None: httpx.AsyncClient(
        base_url=swarm_out._base(inst), transport=httpx.MockTransport(hive)))
    got = asyncio.run(swarm_out._session_states(_inst("h1")))
    assert got == {"s1": {"streaming": False, "waiting_input": True, "activity": None},
                   "s3": {"streaming": True, "waiting_input": False, "activity": None}}


# ── fleet ─────────────────────────────────────────────────────────────────────

@pytest.fixture
def fleet_env(monkeypatch, tmp_path):
    db = tmp_path / "data.db"
    c = sqlite3.connect(db)
    c.execute("CREATE TABLE hive_instances (id TEXT, name TEXT, status TEXT, version TEXT, region TEXT, "
              "instance_type TEXT, cloudfront_domain TEXT, auth_password TEXT)")
    for hid, status in (("ok", "running"), ("down", "running"), ("bad", "running"),
                        ("over", "running"), ("off", "stopped")):
        c.execute("INSERT INTO hive_instances VALUES (?,?,?,?,?,?,?,?)",
                  (hid, f"w-{hid}", status, "2.3.0-hive2", "us-east-1", "m7g.xlarge", f"{hid}.cloudfront.net", "pw"))
    c.commit()
    c.close()
    monkeypatch.setattr(swarm_out, "_db_path", lambda: str(db))

    async def instance(ref):
        return _inst(ref)

    def hive(request):
        host = request.url.host.split(".")[0]
        if host == "down":
            raise httpx.ConnectTimeout("slow")
        if host == "bad":
            return httpx.Response(200, json={"streaming_count": "3", "saturated": False, "memory_percent": 50})
        if host == "over":
            return httpx.Response(200, json={"streaming_count": 6, "saturated": True, "memory_percent": 93.04})
        return httpx.Response(200, json={"streaming_count": 1, "saturated": False, "memory_percent": 61.2})
    monkeypatch.setattr(swarm_out, "_instance", instance)
    monkeypatch.setattr(swarm_out, "_client", lambda inst, timeout=None: httpx.AsyncClient(
        base_url=swarm_out._base(inst), transport=httpx.MockTransport(hive)))


def test_fleet_lists_every_hive_with_validated_load(fleet_env):
    out = asyncio.run(swarm_out.fleet())
    by = {h["name"]: h for h in out["hives"]}
    assert out["running"] == 4
    assert {k: by["w-ok"][k] for k in ("reachable", "streaming", "saturated", "memory_percent")} == \
        {"reachable": True, "streaming": 1, "saturated": False, "memory_percent": 61.2}
    for name in ("w-down", "w-bad"):
        assert by[name]["reachable"] is False and by[name]["streaming"] is None
    assert by["w-over"]["streaming"] == 6 and by["w-over"]["saturated"] is True
    assert by["w-over"]["memory_percent"] == 93.0
    assert by["w-off"]["reachable"] is None and by["w-off"]["status"] == "stopped"


def test_fleet_rows_carry_no_address_or_credential(fleet_env):
    out = asyncio.run(swarm_out.fleet())
    for h in out["hives"]:
        assert tuple(h) == swarm_out.FLEET_KEYS
    assert "cloudfront" not in str(out) and "pw" not in [v for h in out["hives"] for v in h.values()]


def test_fleet_route_exists():
    from routers.swarm_out import router
    assert any(getattr(r, "path", "").endswith("/fleet") for r in router.routes)


def test_a_log_hive_the_store_no_longer_has_is_gone_and_not_asked(monkeypatch):
    rows = [{"dispatch_id": "d1", "hive_id": "deleted", "hive": "w", "session_id": "s1", "title": "A"}]
    monkeypatch.setattr(swarm_out, "_read_log", lambda: list(rows))

    async def statuses():
        return {}

    async def instance(ref):
        raise AssertionError("a deleted Hive is not read")
    monkeypatch.setattr(swarm_out, "_hive_statuses", statuses)
    monkeypatch.setattr(swarm_out, "_instance", instance)
    out = asyncio.run(swarm_out.list_runs())
    assert (out["runs"][0]["state"], out["runs"][0]["state_reason"]) == ("stopped", "hive_gone")
    assert out["errors"] == []


def test_a_slow_question_read_does_not_hold_up_the_poll(monkeypatch):
    rows = [{"dispatch_id": "d1", "hive_id": "h1", "hive": "w", "session_id": "s1", "title": "A"}]
    monkeypatch.setattr(swarm_out, "_read_log", lambda: list(rows))
    monkeypatch.setattr(swarm_out, "QUESTIONS_TIMEOUT_S", 0.2)

    async def statuses():
        return {"h1": "running"}

    async def instance(ref):
        return _inst("h1")

    async def states(inst):
        return {"s1": {"streaming": True, "waiting_input": False, "activity": "streaming"}}

    def slow(s3, bucket, hive):
        time.sleep(1.0)
        return []
    monkeypatch.setattr(swarm_out, "_hive_statuses", statuses)
    monkeypatch.setattr(swarm_out, "_instance", instance)
    monkeypatch.setattr(swarm_out, "_session_states", states)
    monkeypatch.setattr(swarm_out, "_s3", lambda inst, purpose="manage": None)
    monkeypatch.setattr(swarm_out, "_read_decisions", lambda s3, b, h: [])   # no pipeline decisions
    monkeypatch.setattr(swarm_out, "_open_asks", slow)
    async def timed():
        t = time.monotonic()
        result = await swarm_out.list_runs()
        return result, time.monotonic() - t
    out, took = asyncio.run(timed())   # (asyncio.run itself then waits for the worker thread)
    assert took < 0.9
    assert out["runs"][0]["state"] == "running"
    assert out["errors"] == ["could not read the questions of Hive w-h1"]


@pytest.mark.parametrize("value,low,ok", [(3, 0, True), (0, 0, True), (0, 1, False), (True, 0, False),
                                          (swarm_out.MAX_REPORTED_SESSIONS + 1, 0, False), ("3", 0, False), (-1, 0, False)])
def test_a_reported_count_is_an_int_in_range(value, low, ok):
    assert (swarm_out._count(value, low) == value) is ok
