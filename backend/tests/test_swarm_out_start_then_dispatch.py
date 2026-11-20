"""A dispatch to a stopped Hive starts it first, and every dispatch row says how long
each step took (core/swarm_out ``_ensure_running``, ``_Deadline.timings``).

Questions: (1) does a dispatch to a stopped Hive start it (core/hive_idle.wake) before
anything is asked of it, then send the task; (2) does a start that ends in error, or
that takes longer than the dispatch has, fail naming the step and saying nothing was
sent, with a start still going left to finish; (3) is a Hive in another status refused
as before, without a start; (4) with no Hive named and none running, is a stopped one
started (placement: one last probed able at its version, fewest open runs, then name) and
the reason ``started``; (5) does the row of a
dispatch (sent, or may have been sent) carry each step and its seconds, in order;
(6) is the dispatch marked in flight while it runs.
"""
from __future__ import annotations

import asyncio
import json

import pytest

from core import hive_idle
from core import swarm_out as so
from tests.test_swarm_out_deadline import _deadline, hive  # noqa: F401 — the fake Hive
from tests.test_swarm_out_host import INST
from tests.test_task_spec import _brain, ws  # noqa: F401 — the Brain fixture

_REAL_INSTANCE = so._instance        # the fake Hive fixture replaces it; this suite reads rows

@pytest.fixture
def stopped(hive, monkeypatch):
    """The fake Hive, stopped: ``_instance`` refuses it until the start has run."""
    st = {"status": "stopped", "error": None, "start_s": 0.05, "events": [], "starts": 0,
          "row": {}, "next": []}
    hive["on_"] = lambda path: st["events"].append(path)

    async def row_and_account(ref):
        if st["next"]:
            st["status"] = st["next"].pop(0)    # what the row says at this read
        st["events"].append("instance:" + st["status"])
        return {**INST, **st["row"], "status": st["status"], "error_message": st["error"]}, {"id": "a1"}
    monkeypatch.setattr(so, "_row_and_account", row_and_account)
    monkeypatch.setattr(so, "_instance", _REAL_INSTANCE)

    async def row(ref):
        return {"id": INST["id"], "name": INST["name"], "status": st["status"], "error_message": st["error"]}
    monkeypatch.setattr(hive_idle, "hive_row", row)

    class P:
        async def start(self, hid):
            st["starts"] += 1
            st["events"].append("start")
            st["status"] = "starting"
            await asyncio.sleep(st["start_s"])
            st["status"] = "error" if st["error"] else "running"
    monkeypatch.setattr(hive_idle, "_provisioner", lambda: P())
    monkeypatch.setattr(hive_idle, "WAKE_POLL_S", 0.01)
    hive_idle._STARTS.clear()
    so._IN_FLIGHT.clear()
    return st


def _rows(hive):
    p = hive["dir"] / "dispatches.jsonl"
    return [json.loads(ln) for ln in p.read_text().splitlines()] if p.exists() else []


# ── (1) start, then dispatch ──

def test_a_dispatch_to_a_stopped_hive_starts_it_first(hive, stopped):
    row = asyncio.run(so.dispatch("worker-1", "Fix it", "do X"))
    assert row["session_id"] == "sess-42" and stopped["starts"] == 1
    first_request = next(i for i, e in enumerate(stopped["events"]) if e.startswith("/"))
    assert stopped["events"].index("start") < first_request
    assert "instance:running" in stopped["events"][stopped["events"].index("start"):]
    assert hive["seen"][0] == "/api/chat/sessions/admission-state"
    assert row["timings"][0]["step"] == "starting Hive worker-1"
    assert row["timings"][0]["seconds"] >= 0.05
    assert _rows(hive)[0]["timings"] == row["timings"]


def test_a_running_hive_is_not_started(hive, stopped):
    stopped["status"] = "running"
    asyncio.run(so.dispatch("worker-1", "Fix it", "do X"))
    assert stopped["starts"] == 0
    assert all(t["step"] != "starting Hive worker-1" for t in _rows(hive)[0]["timings"])


# ── (2) a start that fails or does not fit ──

def test_a_start_that_fails_sends_nothing(hive, stopped):
    stopped["error"] = "Start failed: InsufficientInstanceCapacity"
    with pytest.raises(so.SwarmOutError) as e:
        asyncio.run(so.dispatch("worker-1", "Fix it", "do X"))
    assert "InsufficientInstanceCapacity" in str(e.value) and "nothing was sent" in str(e.value)
    assert hive["seen"] == [] and _rows(hive) == []


def test_a_start_longer_than_the_deadline_fails_and_goes_on(hive, stopped, monkeypatch):
    _deadline(monkeypatch, 0.6, 0.1)
    stopped["start_s"] = 1.0

    async def go():
        with pytest.raises(so.SwarmOutError) as e:
            await so.dispatch("worker-1", "Fix it", "do X")
        assert stopped["status"] == "starting"           # not cut off
        await asyncio.sleep(1.0)
        return e.value
    err = asyncio.run(go())
    assert "starting Hive worker-1" in str(err) and "nothing was sent" in str(err)
    assert stopped["status"] == "running"
    assert hive["seen"] == [] and _rows(hive) == []


# ── (3) other statuses ──

@pytest.mark.parametrize("status", ["updating", "error", "deleting"])
def test_a_hive_in_another_status_is_refused_without_a_start(hive, stopped, status):
    stopped["status"] = status
    with pytest.raises(so.SwarmOutError, match=f"is {status}, not running"):
        asyncio.run(so.dispatch("worker-1", "Fix it", "do X"))
    assert stopped["starts"] == 0 and hive["seen"] == []


def test_a_hive_running_again_after_a_given_back_claim_is_dispatched_to(hive, stopped):
    # read 'stopping' (an idle stop's claim), then 'running' (the claim given back)
    stopped["next"] = ["stopping", "running"]
    row = asyncio.run(so.dispatch("worker-1", "Fix it", "do X"))
    assert row["session_id"] == "sess-42" and stopped["starts"] == 0


def test_an_unusable_stopped_hive_is_not_started(hive, stopped):
    stopped["row"] = {"cloudfront_domain": "evil.example.com"}
    with pytest.raises(so.SwarmOutError, match="unexpected address"):
        asyncio.run(so.dispatch("worker-1", "Fix it", "do X"))
    assert stopped["starts"] == 0 and hive["seen"] == []


def test_a_stopped_hive_held_by_another_swarm_run_is_not_started(hive, stopped):
    with pytest.raises(so.SwarmOutError, match="holds this Brain"):
        asyncio.run(so._instance_up("worker-1", so._Deadline(10), frozenset({INST["id"]})))
    assert stopped["starts"] == 0


# ── (4) no Hive named ──

def test_pick_stopped_starts_one_only_when_none_runs():
    hives = [{"name": "b", "status": "stopped"}, {"name": "a", "status": "stopped"},
             {"name": "c", "status": "error"}]
    assert so.pick_stopped(hives) == "a"
    assert so.pick_stopped(hives, {"a": 2, "b": 0}) == "b"
    assert so.pick_stopped(hives + [{"name": "d", "status": "running"}]) is None
    assert so.pick_stopped([{"name": "c", "status": "error"}]) is None
    assert so.pick_stopped(hives, exclude=frozenset({"a"})) == "b"
    assert so.pick_stopped(hives, exclude=frozenset({"a", "b"})) is None


def _offered(monkeypatch, rows, probes=None, capable=()):
    """The Hives placement is offered (core/carriers/ec2 ``offers``): *rows* as the store
    has them (id, name, status), each running one answering *probes* (by id), each id in
    *capable* last probed able at its version (a stopped Hive is offered only then, D5)."""
    from core.carriers import ec2
    from hive import accounts
    full = [{"version": "2.5.2", "auth_method": "access_keys", "account_id": None, **r} for r in rows]

    async def hive_rows():
        return [dict(r) for r in full]

    async def admission(hid):
        return (probes or {}).get(hid)
    monkeypatch.setattr(ec2, "_hive_rows", hive_rows)
    monkeypatch.setattr(ec2, "_read_probes", lambda: {h: {"version": "2.5.2", "capable": True} for h in capable})
    monkeypatch.setattr(accounts, "swarmai_account", lambda: None)      # legacy rows: the Host's own
    monkeypatch.setattr(so, "_admission", admission)


CAPABLE = {"streaming": 0, "saturated": False, "memory_percent": 10.0, "capable": True}


def test_a_spec_dispatch_with_no_hive_named_starts_a_stopped_one(hive, stopped, monkeypatch, ws):
    # placement (design 2026-11-20-swarm-out-multi-carrier §4.4): with none running, a
    # stopped Hive last probed able at its version is started (layer 3, ``started``);
    # one never probed is not offered as able, and the refusal says why (D5)
    from core import brain_sync_host as host
    from core.project_agent import revision
    rev = revision(_brain(ws))

    async def hive_brains(inst):
        return {"supported": True, "brains": {"Lib": {"digest": "0" * 64, "current_digest": "0" * 64}}}

    async def sync(inst, brain, ws=None):
        return None

    async def nothing(*a, **kw):
        return None
    monkeypatch.setattr(host, "hive_brains", hive_brains)
    monkeypatch.setattr(host, "sync", sync)
    monkeypatch.setattr(so, "_require_work_orders", nothing)
    monkeypatch.setattr(so, "_ship_repos", nothing)
    _offered(monkeypatch, [{"id": INST["id"], "name": "worker-1", "status": "stopped"}])
    with pytest.raises(so.SwarmOutError, match="worker-1: unknown_version"):
        asyncio.run(so.dispatch_spec(None, "t", "Lib", "fix it", "tests pass", execution="native",
                                     returns="commits", seen_revision=rev))
    assert stopped["starts"] == 0
    _offered(monkeypatch, [{"id": INST["id"], "name": "worker-1", "status": "stopped"}], capable={INST["id"]})
    out = asyncio.run(so.dispatch_spec(None, "t", "Lib", "fix it", "tests pass", execution="native",
                                       returns="commits", seen_revision=rev))
    assert stopped["starts"] == 1 and out["hive_reason"] == "started"
    assert out["placement"]["layer"] == 3 and out["placement"]["location"] == "worker-1"
    steps = [t["step"] for t in out["timings"]]
    assert steps[0] == "choosing a Hive" and steps[1] == "starting Hive worker-1"
    from core.swarm_out_card import hive_reason_text
    assert "started" in hive_reason_text("started")


@pytest.mark.parametrize("held", [{"h0"}, {"h0", "h1"}])
def test_no_hive_named_skips_a_stopped_hive_another_swarm_run_holds(hive, stopped, monkeypatch, ws, held):
    from core import brain_sync_host as host
    from core import swarm_run
    from core.project_agent import revision
    rev = revision(_brain(ws))
    refs = []
    real = so._row_and_account

    async def row_and_account(ref):
        refs.append(ref)
        return await real(ref)
    monkeypatch.setattr(so, "_row_and_account", row_and_account)
    _offered(monkeypatch, [{"id": "h0", "name": "worker-0", "status": "stopped"},
                           {"id": INST["id"], "name": "worker-1", "status": "stopped"}], capable={"h0", INST["id"]})

    async def hive_brains(inst):
        return {"supported": True, "brains": {"Lib": {"digest": "0" * 64, "current_digest": "0" * 64}}}

    async def nothing(*a, **kw):
        return None
    monkeypatch.setattr(swarm_run, "held_hives", lambda *a, **kw: held)
    monkeypatch.setattr(host, "hive_brains", hive_brains)
    monkeypatch.setattr(host, "sync", nothing)
    monkeypatch.setattr(so, "_require_work_orders", nothing)
    monkeypatch.setattr(so, "_ship_repos", nothing)
    go = so.dispatch_spec(None, "t", "Lib", "fix it", "tests pass", execution="native",
                          returns="commits", seen_revision=rev)
    if len(held) == 2:
        # every stopped Hive is held: refused for that (each one named), nothing started
        with pytest.raises(so.SwarmOutError, match="worker-0: held; worker-1: held"):
            asyncio.run(go)
        assert refs == [] and stopped["starts"] == 0
        return
    out = asyncio.run(go)
    assert refs[0] == INST["id"] and out["hive_reason"] == "started" and stopped["starts"] == 1


def test_a_running_hive_another_swarm_run_holds_is_skipped_by_name():
    # the fleet's rows carry no id: the held set reaches pick_hive as names too
    row = {"status": "running", "reachable": True, "saturated": False, "streaming": 0,
           "capable": True, "memory_percent": 10}
    rows = [{**row, "name": "worker-0"}, {**row, "name": "worker-1"}]
    assert so.pick_hive(rows, exclude=frozenset({"h0", "worker-0"}))[0] == "worker-1"
    with pytest.raises(so.SwarmOutError, match="every Hive that could take it holds"):
        so.pick_hive(rows, exclude=frozenset({"worker-0", "worker-1"}))


def test_held_names_are_read_from_the_store_and_tolerate_a_failure(monkeypatch):
    async def known():
        return {"hives": [{"id": "h0", "name": "worker-0"}, {"id": "h1", "name": "worker-1"}]}
    monkeypatch.setattr(so, "hives", known)
    assert asyncio.run(so._held_names(frozenset({"h0"}))) == frozenset({"worker-0"})
    assert asyncio.run(so._held_names(frozenset())) == frozenset()

    async def broken():
        raise RuntimeError("database is locked")
    monkeypatch.setattr(so, "hives", broken)
    assert asyncio.run(so._held_names(frozenset({"h0"}))) == frozenset()


def test_no_hive_named_skips_a_running_hive_another_swarm_run_holds(hive, stopped, monkeypatch, ws):
    # the held id leaves its Hive out of placement (by id: offers carry it)
    from core import brain_sync_host as host
    from core import swarm_run
    from core.project_agent import revision
    rev = revision(_brain(ws))
    stopped["status"] = "running"
    refs = []
    real = so._row_and_account

    async def row_and_account(ref):
        refs.append(ref)
        return await real(ref)
    monkeypatch.setattr(so, "_row_and_account", row_and_account)
    _offered(monkeypatch, [{"id": "h0", "name": "worker-0", "status": "running"},
                           {"id": INST["id"], "name": "worker-1", "status": "running"}],
             probes={"h0": CAPABLE, INST["id"]: CAPABLE})

    async def hive_brains(inst):
        return {"supported": True, "brains": {"Lib": {"digest": "0" * 64, "current_digest": "0" * 64}}}

    async def nothing(*a, **kw):
        return None
    monkeypatch.setattr(swarm_run, "held_hives", lambda *a, **kw: {"h0"})
    monkeypatch.setattr(host, "hive_brains", hive_brains)
    monkeypatch.setattr(host, "sync", nothing)
    monkeypatch.setattr(so, "_require_work_orders", nothing)
    monkeypatch.setattr(so, "_ship_repos", nothing)
    out = asyncio.run(so.dispatch_spec(None, "t", "Lib", "fix it", "tests pass", execution="native",
                                       returns="commits", seen_revision=rev))
    assert refs[0] == INST["id"] and out["hive_reason"] == "only_free" and stopped["starts"] == 0
    assert ["worker-0", "held"] in out["placement"]["skipped"]


# ── (5) timings on the row ──

def test_the_row_records_each_step_and_its_duration(hive, stopped):
    stopped["status"] = "running"
    hive["delay"]["/api/hive/work"] = 0.2
    row = asyncio.run(so.dispatch("worker-1", "Fix it", "do X"))
    steps = [t["step"] for t in row["timings"]]
    assert steps == ["checking Hive worker-1", "setting Hive worker-1's model",
                     "preparing the workspace on Hive worker-1", "starting the session on Hive worker-1"]
    assert all(isinstance(t["seconds"], float) and t["seconds"] >= 0 for t in row["timings"])
    assert row["timings"][2]["seconds"] >= 0.2
    assert _rows(hive)[0]["timings"] == row["timings"]


def test_a_row_that_may_have_been_sent_has_its_timings(hive, stopped, monkeypatch):
    stopped["status"] = "running"
    _deadline(monkeypatch, 2.0, 0.2)
    hive["names_session"] = False
    with pytest.raises(so.SwarmOutMaybeSent):
        asyncio.run(so.dispatch("worker-1", "Fix it", "do X"))
    rec = _rows(hive)[0]
    assert rec["session_id"] is None
    assert rec["timings"][-1]["step"] == "starting the session on Hive worker-1"
    assert rec["timings"][-1]["seconds"] >= 0.15


# ── (6) in flight ──

def test_the_dispatch_is_in_flight_while_it_runs(hive, stopped):
    stopped["status"] = "running"
    seen = []
    hive["on_prepare"] = lambda path: seen.append(so.dispatch_in_flight(INST["id"], INST["name"]))
    asyncio.run(so.dispatch("worker-1", "Fix it", "do X"))
    assert seen == [True]
    assert not so.dispatch_in_flight(INST["id"], INST["name"])
