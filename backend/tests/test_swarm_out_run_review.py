"""The Host as the one authority on a Swarm Out run (design
docs/design/2026-10-31-swarm-out-run-review.md §3, verification plan §5).

Each test answers one question:
- invariant 2: the runs are recomputed whether or not anyone looks (the refresh loop),
  one computation at a time, and GET /runs reuses a fresh snapshot instead of
  recomputing;
- invariant 3: discard stops a running run first, settles it even when stopping
  failed, and from then on the proxy forwards nothing of its session (410); an
  accepted run's session can be read but not written to (409);
- invariant 4: re-run sends the correction into the run's own session, and is refused
  for a closed run or one without a session;
- invariant 6: ``human.said`` counts every user message after the task itself;
- invariant 7: the Needs You source summarises the snapshot and never refreshes it.
"""
from __future__ import annotations

import asyncio
import json

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core import swarm_out, swarm_out_settle as st

DID = "so_bbbbbbbbbbbb"
SID = "sess-r1"


@pytest.fixture
def world(tmp_path, monkeypatch):
    """One dispatched run with a session; the Hive answers ``stop`` with ``code``."""
    monkeypatch.setattr(swarm_out, "_state_dir", lambda: tmp_path)
    row = {"dispatch_id": DID, "hive_id": "h1", "session_id": SID,
           "project_agent": {"returns": "answer"}}
    monkeypatch.setattr(swarm_out, "_log_row_of",
                        lambda d: row if d == DID else (_ for _ in ()).throw(swarm_out.SwarmOutError("no")))
    calls: list[str] = []
    code = {"v": 200}

    class Resp:
        def __init__(self, c):
            self.status_code = c

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, **kw):
            calls.append(url)
            if code["v"] == "raise":
                raise httpx.ConnectError("down")
            return Resp(code["v"])

    async def inst(ref):
        return {"id": ref}
    monkeypatch.setattr(swarm_out, "_instance", inst)
    monkeypatch.setattr(swarm_out, "_client", lambda i, t=None: Client())
    computed = {"n": 0}

    async def compute():
        computed["n"] += 1
        return {"runs": [], "asks": [], "errors": [], "refusals": []}
    monkeypatch.setattr(swarm_out, "_compute_runs", compute)
    swarm_out._SNAPSHOT.update(at=None, data=None, task=None, task_gen=0, gen=0)
    return {"row": row, "calls": calls, "code": code, "computed": computed}


async def _drain():
    for _ in range(5):
        await asyncio.sleep(0)


# ── invariant 2: refreshed whether or not anyone looks ───────────────────────

def test_a_fresh_snapshot_is_reused_and_a_stale_one_recomputed(world, monkeypatch):
    async def go():
        await swarm_out.list_runs(swarm_out.SNAPSHOT_FRESH_S)
        await swarm_out.list_runs(swarm_out.SNAPSHOT_FRESH_S)
        assert world["computed"]["n"] == 1                   # the second one read the snapshot
        swarm_out._SNAPSHOT["at"] -= swarm_out.SNAPSHOT_FRESH_S + 1
        await swarm_out.list_runs(swarm_out.SNAPSHOT_FRESH_S)
        assert world["computed"]["n"] == 2
        await swarm_out.list_runs()                          # no max age: always fresh
        assert world["computed"]["n"] == 3
    asyncio.run(go())


def test_concurrent_refreshes_share_one_computation(world, monkeypatch):
    gate = {"open": None}

    async def slow():
        world["computed"]["n"] += 1
        await gate["open"].wait()
        return {"runs": [{"n": world["computed"]["n"]}], "asks": []}
    monkeypatch.setattr(swarm_out, "_compute_runs", slow)

    async def go():
        gate["open"] = asyncio.Event()
        tasks = [asyncio.ensure_future(swarm_out.refresh_runs()) for _ in range(5)]
        await _drain()
        gate["open"].set()
        out = await asyncio.gather(*tasks)
        assert world["computed"]["n"] == 1 and all(o is out[0] for o in out)
        assert swarm_out.snapshot() is out[0]
    asyncio.run(go())


def test_a_command_during_a_computation_is_never_hidden_by_it(world, monkeypatch):
    """A computation that read the records before a command is not kept as the snapshot:
    the next one (started after it) is."""
    gates: list = []

    async def slow():
        n = world["computed"]["n"] = world["computed"]["n"] + 1
        gate = asyncio.Event()
        gates.append(gate)
        await gate.wait()
        return {"runs": [{"seen": n}], "asks": []}
    monkeypatch.setattr(swarm_out, "_compute_runs", slow)

    async def go():
        first = asyncio.ensure_future(swarm_out.refresh_runs())   # the loop's, before the command
        await _drain()
        swarm_out.invalidate_snapshot()                            # the person discards
        after = asyncio.ensure_future(swarm_out.refresh_runs())   # the command's refresh
        await _drain()
        gates[0].set()
        assert (await first)["runs"] == [{"seen": 1}]
        await _drain()
        assert swarm_out.snapshot() is None                        # the old answer is not kept
        assert world["computed"]["n"] == 2                         # a new one started after it
        gates[1].set()
        assert (await after)["runs"] == [{"seen": 2}]
        await _drain()
        assert swarm_out.snapshot()["runs"] == [{"seen": 2}]
        assert await swarm_out.list_runs(swarm_out.SNAPSHOT_FRESH_S) is swarm_out.snapshot()
    asyncio.run(go())


def test_a_computation_whose_caller_went_away_is_still_kept(world, monkeypatch):
    gate = {"e": None}

    async def slow():
        await gate["e"].wait()
        return {"runs": [{"kept": True}], "asks": []}
    monkeypatch.setattr(swarm_out, "_compute_runs", slow)

    async def go():
        gate["e"] = asyncio.Event()
        caller = asyncio.ensure_future(swarm_out.refresh_runs())
        await _drain()
        caller.cancel()                                            # GET /runs's client went away
        await _drain()
        gate["e"].set()
        await _drain()
        assert swarm_out.snapshot() == {"runs": [{"kept": True}], "asks": []}
    asyncio.run(go())


def test_the_loop_refreshes_without_anyone_looking(world, monkeypatch, tmp_path):
    rounds = {"n": 0}

    async def sleep(s):
        rounds["n"] += 1
        if rounds["n"] >= 2:
            raise asyncio.CancelledError
    monkeypatch.setattr(swarm_out.asyncio, "sleep", sleep)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(swarm_out.run_refresh_loop())
    assert world["computed"]["n"] == 0                       # nothing dispatched yet: nothing to do
    swarm_out._log_path().write_text("{}\n")
    rounds["n"] = 0
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(swarm_out.run_refresh_loop())
    assert world["computed"]["n"] == 2


def test_a_failing_refresh_does_not_end_the_loop(world, monkeypatch):
    swarm_out._log_path().write_text("{}\n")
    rounds = {"n": 0}

    async def boom():
        raise OSError("disk")
    monkeypatch.setattr(swarm_out, "_compute_runs", boom)

    async def sleep(s):
        rounds["n"] += 1
        if rounds["n"] >= 3:
            raise asyncio.CancelledError
    monkeypatch.setattr(swarm_out.asyncio, "sleep", sleep)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(swarm_out.run_refresh_loop())
    assert rounds["n"] == 3


# ── invariant 3: discard ─────────────────────────────────────────────────────

def test_discard_stops_first_then_closes(world):
    out = asyncio.run(swarm_out.discard_run(DID, "not needed"))
    assert out == {"discarded": True, "stopped": True}
    assert world["calls"] == [f"/api/chat/stop/{SID}"]
    rec = st.load(DID)
    assert rec["abandoned_at"] and rec["discard_note"] == "not needed"
    with pytest.raises(swarm_out.SwarmOutError, match="closed"):
        asyncio.run(swarm_out.discard_run(DID))


@pytest.mark.parametrize("hive", [500, "raise"])
def test_discard_closes_even_when_stopping_failed(world, hive):
    world["code"]["v"] = hive
    out = asyncio.run(swarm_out.discard_run(DID))
    assert out == {"discarded": True, "stopped": False} and st.load(DID)["abandoned_at"]


def test_discard_without_a_session_does_not_call_the_hive(world):
    world["row"]["session_id"] = None
    out = asyncio.run(swarm_out.discard_run(DID))
    assert out["stopped"] is None and world["calls"] == []


def test_a_discarded_or_expired_run_is_closed_to_every_request(world):
    from core.swarm_out_remote import closed_for
    row = {"dispatch_id": DID}
    assert closed_for(row, write=True) is None
    st.discard(DID)
    for write in (False, True):
        assert closed_for(row, write=write).status == 410
    st._update(DID, lambda rec: rec.update(abandoned_at=None, settled={"outcome": "expired"}))
    assert closed_for(row, write=False).status == 410


def test_an_accepted_run_is_read_only(world):
    from core.swarm_out_remote import closed_for
    row = {"dispatch_id": DID}
    st._update(DID, lambda rec: rec.update(settled={"outcome": "passed"}))
    assert closed_for(row, write=False) is None
    assert closed_for(row, write=True).status == 409


def test_a_pass_verdict_is_read_only_before_the_run_settles(world, monkeypatch):
    """The panel shows "accepted" as soon as the verdict is in (files or lessons may still
    be coming): the proxy and the commands follow the same rule."""
    from core import swarm_out_results
    from core.swarm_out_remote import closed_for

    async def start(*a, **k):
        raise AssertionError("nothing is sent")
    monkeypatch.setattr(swarm_out, "_start_session", start)
    # the verdict is on the result there is now (a verdict on an older one is void:
    # e2e-fixes #2, tests/test_swarm_out_e2e_fixes.py)
    monkeypatch.setattr(swarm_out_results, "load", lambda d: {
        "result": {"ended": "completed", "provisional": False, "ended_at": "x"}})
    st._update(DID, lambda rec: rec.update(verdict={"value": "pass", "ended_at": "x", "at": "y"}))
    assert closed_for({"dispatch_id": DID}, write=True).status == 409
    assert closed_for({"dispatch_id": DID}, write=False) is None
    for call in (swarm_out.rerun(DID, "x"), swarm_out.stop_run(DID), swarm_out.discard_run(DID)):
        with pytest.raises(swarm_out.SwarmOutError, match="accepted"):
            asyncio.run(call)
    assert world["calls"] == []


def test_rerun_refuses_a_session_gone_from_its_hive(world, monkeypatch):
    from core import swarm_out_results

    async def start(*a, **k):
        raise AssertionError("nothing is sent")
    monkeypatch.setattr(swarm_out, "_start_session", start)
    monkeypatch.setattr(swarm_out_results, "load", lambda d: {"fetch_error": "session_gone"})
    with pytest.raises(swarm_out.SwarmOutError, match="gone"):
        asyncio.run(swarm_out.rerun(DID, "x"))


def test_discard_refuses_a_run_sent_before_project_agents(world):
    world["row"].pop("project_agent")
    with pytest.raises(swarm_out.SwarmOutError, match="before Project Agents"):
        asyncio.run(swarm_out.discard_run(DID))
    assert world["calls"] == [] and not st.load(DID)


# ── the proxy itself refuses (no request reaches the Hive) ───────────────────

@pytest.fixture
def proxy(world, monkeypatch):
    from routers import swarm_out_remote as remote_router
    seen: list[str] = []
    monkeypatch.delenv("SWARMAI_MODE", raising=False)
    monkeypatch.setattr(swarm_out, "_read_log", lambda: [{"dispatch_id": DID, "hive_id": "h1", "session_id": SID}])

    async def inst(ref):
        seen.append(ref)
        raise swarm_out.SwarmOutError("the test stops here: the request was let through")
    monkeypatch.setattr(swarm_out, "_instance", inst)
    from core.session_manager import session_manager

    async def none(sid):
        return None
    monkeypatch.setattr(session_manager, "get_session", none)
    app = FastAPI()
    app.include_router(remote_router.router, prefix="/api/swarm-out/remote")
    return TestClient(app), seen


H = {"Content-Type": "application/json", "X-Swarm-User-Action": "1"}


def test_the_proxy_forwards_nothing_of_a_discarded_run(proxy):
    api, seen = proxy
    st.discard(DID)
    base = f"/api/swarm-out/remote/{SID}"
    assert api.get(f"{base}/chat/sessions/{SID}").status_code == 410
    assert api.post(f"{base}/chat/stream", headers=H,
                    content=json.dumps({"message": "x", "session_id": SID, "client_id": "c1"})).status_code == 410
    assert api.post(f"{base}/relay", headers=H, json={"client_id": "w1"}).status_code == 410
    assert seen == []


def test_the_proxy_lets_an_accepted_run_be_read_not_written(proxy):
    api, seen = proxy
    st._update(DID, lambda rec: rec.update(settled={"outcome": "passed"}))
    base = f"/api/swarm-out/remote/{SID}"
    assert api.post(f"{base}/chat/stop/{SID}", headers=H, json={}).status_code == 409
    assert seen == []
    assert api.get(f"{base}/chat/sessions/{SID}").status_code != 409
    assert seen == ["h1"]                                    # the read was let through


# ── invariant 4: re-run with a correction, stop ──────────────────────────────

def test_rerun_goes_into_the_runs_own_session(world, monkeypatch):
    sent = []

    async def start(client, message, work_id=None, project_agent=None, session_id=None):
        sent.append((message, session_id))
        return session_id
    monkeypatch.setattr(swarm_out, "_start_session", start)
    assert asyncio.run(swarm_out.rerun(DID, "  use the 2025 numbers ")) == {"sent": True}
    assert sent == [("use the 2025 numbers", SID)]


def test_rerun_refuses_when_the_hive_opened_another_session(world, monkeypatch):
    async def start(client, message, **kw):
        return "another"
    monkeypatch.setattr(swarm_out, "_start_session", start)
    with pytest.raises(swarm_out.SwarmOutError, match="did not take"):
        asyncio.run(swarm_out.rerun(DID, "x"))


def test_rerun_and_stop_refuse_a_closed_run_or_one_without_a_session(world, monkeypatch):
    async def start(*a, **k):
        raise AssertionError("nothing is sent")
    monkeypatch.setattr(swarm_out, "_start_session", start)
    with pytest.raises(swarm_out.SwarmOutError, match="say what"):
        asyncio.run(swarm_out.rerun(DID, "   "))
    st._update(DID, lambda rec: rec.update(settled={"outcome": "passed"}))
    for call in (swarm_out.rerun(DID, "x"), swarm_out.stop_run(DID)):
        with pytest.raises(swarm_out.SwarmOutError, match="accepted"):
            asyncio.run(call)
    st._update(DID, lambda rec: rec.update(settled={"outcome": "abandoned"}))
    for call in (swarm_out.rerun(DID, "x"), swarm_out.stop_run(DID)):
        with pytest.raises(swarm_out.SwarmOutError, match="closed"):
            asyncio.run(call)
    world["row"]["session_id"] = None
    for call in (swarm_out.rerun(DID, "x"), swarm_out.stop_run(DID)):
        with pytest.raises(swarm_out.SwarmOutError, match="no session"):
            asyncio.run(call)
    assert world["calls"] == []


def test_stop_asks_the_hive_to_stop_the_session(world):
    assert asyncio.run(swarm_out.stop_run(DID)) == {"stopped": True}
    assert world["calls"] == [f"/api/chat/stop/{SID}"]
    world["code"]["v"] = 409
    with pytest.raises(swarm_out.SwarmOutError, match="did not stop"):
        asyncio.run(swarm_out.stop_run(DID))


def test_the_command_routes_answer_and_validate(world, monkeypatch):
    from routers.swarm_out import router
    monkeypatch.delenv("SWARMAI_MODE", raising=False)
    app = FastAPI()
    app.include_router(router, prefix="/api/swarm-out")
    c = TestClient(app)
    base = f"/api/swarm-out/runs/{DID}"
    assert c.post(f"{base}/rerun", json={"text": ""}).status_code == 422
    # the panel's commands are the person's (core/swarm_out_actor)
    person = {"by": "person"}
    assert c.post(f"{base}/stop", json=person).json() == {"stopped": True}
    assert c.post(f"{base}/discard", json=person).json()["discarded"] is True
    r = c.post(f"{base}/stop", json=person)
    assert r.status_code == 400 and "closed" in r.json()["detail"]
    # what the old card called: no route any more
    assert c.post(f"{base}/abandon").status_code in (404, 405)
    monkeypatch.setenv("SWARMAI_MODE", "hive")
    assert c.post(f"{base}/discard", json=person).status_code == 403


# ── invariant 6: what the person said in the session ─────────────────────────

def test_said_counts_every_user_message_after_the_task():
    from core.swarm_out_results import summarize

    def U(text="go"):
        return {"role": "user", "content": [{"type": "text", "text": text}], "created_at": "2026-10-31T01:00:00"}

    def S(text):
        return {**U(text), "metadata": {"steered": "during"}}

    def A():
        return {"role": "assistant", "created_at": "2026-10-31T01:05:00",
                "content": [{"type": "turn_outcome", "ended": "completed", "at": "2026-10-31T01:05:00+00:00"}]}
    assert summarize([U(), A()])["work"]["human"]["said"] == 0
    assert summarize([U(), S("not that file"), A(), U("use 2025"), A()])["work"]["human"]["said"] == 2
    # older turns not read: the first row read is not the task, so it counts too
    assert summarize([U("again"), A()], has_more=True)["work"]["human"]["said"] == 1


def test_said_survives_the_public_record():
    from core import swarm_out_results as r
    rec = {"result": {"work": {"human": {"approved": 0, "denied": 0, "unanswered": 0,
                                         "answered_questions": 0, "said": 3}}}}
    assert r.public(rec)["work"]["human"]["said"] == 3


# ── invariant 7: Needs You reads the snapshot, never refreshes ───────────────

def test_needs_you_summarises_the_snapshot(monkeypatch):
    from core import attention_authority as aa

    async def never():
        raise AssertionError("Needs You must not refresh the runs")
    monkeypatch.setattr(swarm_out, "refresh_runs", never)
    monkeypatch.setattr(swarm_out, "_compute_runs", never)
    monkeypatch.setattr(swarm_out, "snapshot", lambda: None)
    assert aa._collect_swarm_out() == []
    snap = {"runs": [{"stage": "awaiting"}, {"stage": "stopped"}, {"stage": "running"},
                     {"stage": "accepted", "cleanup_soon": False}, {"stage": "awaiting", "cleanup_soon": True}],
            "asks": [{"id": "q1"}, {"id": "q2", "refused": True}]}
    monkeypatch.setattr(swarm_out, "snapshot", lambda: snap)
    items = {i.id: i for i in aa._collect_swarm_out()}
    assert set(items) == {"swarm_out:questions", "swarm_out:decide", "swarm_out:cleanup"}
    assert items["swarm_out:questions"].tier == aa.TIER_BLOCKING and items["swarm_out:questions"].title.startswith("1 ")
    assert items["swarm_out:decide"].tier == aa.TIER_REVIEW and items["swarm_out:decide"].title.startswith("3 ")
    for i in items.values():
        assert i.dispatch["context"]["kind"] == "swarm_out"


def test_needs_you_includes_the_swarm_out_source(monkeypatch, tmp_path):
    import re
    from core import attention_authority as aa
    import inspect
    for name in set(re.findall(r"(_collect_[a-z_]+)\(", inspect.getsource(aa.collect))) - {"_collect_swarm_out"}:
        monkeypatch.setattr(aa, name, lambda *a, **k: [])
    monkeypatch.setattr(swarm_out, "snapshot", lambda: {"runs": [{"stage": "awaiting"}], "asks": []})
    res = aa.collect(tmp_path)
    assert [i.id for i in res.items] == ["swarm_out:decide"]
    assert aa.collect(tmp_path, brain="SwarmAI").items == []        # OS-level, not a Brain's


def test_accepting_settles_now_not_at_the_next_look(world, monkeypatch):
    """verdict / manual-landed / landing: the run is recomputed (settled, its Hive told)
    right after, not when the panel or the loop next looks."""
    from routers.swarm_out import router
    monkeypatch.delenv("SWARMAI_MODE", raising=False)
    soon = []
    monkeypatch.setattr(swarm_out, "_refresh_soon", lambda: soon.append(1))
    monkeypatch.setattr(st, "set_verdict", lambda *a: {"ok": True})
    app = FastAPI()
    app.include_router(router, prefix="/api/swarm-out")
    r = TestClient(app).post(f"/api/swarm-out/runs/{DID}/verdict", json={"value": "pass", "note": "", "ended_at": "x",
                                                                                  "by": "person"})
    assert r.status_code == 200 and soon == [1]
