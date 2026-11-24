"""A Hive follows the Host's version without a person pressing Update (core/hive_idle).

Questions: (1) does the idle loop update an idle running Hive behind the Host's version to
that version (claim_update, then update as claimed), and leave alone a busy one or one
already at it; (2) does a dispatch's wake update a Hive it just started and return only once
it runs again, and say why when the update fails; (3) is a failed automatic update of a Hive
at a version left alone by both paths afterwards.
"""
from __future__ import annotations

import asyncio
import time

import pytest

import config
from core import hive_idle
from core import swarm_out as so

NOW = 1_800_000_000.0
HOST = "2.5.5"


def _db(ts: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(ts))


class _Prov:
    """Records every call; ``refuse`` claims raise, ``fail`` updates record why and raise."""

    def __init__(self, rows=None, refuse=(), fail=(), on_claim=None):
        self.calls: list = []
        self.rows = rows if rows is not None else {}
        self.refuse, self.fail = set(refuse), set(fail)
        self.on_claim = on_claim

    async def claim_stop(self, hid):
        self.calls.append(("claim_stop", hid))

    async def release_stop_claim(self, hid):
        return True

    async def stop(self, hid, claimed=False):
        self.calls.append(("stop", hid, claimed))

    async def claim_update(self, hid):
        self.calls.append(("claim_update", hid))
        if self.on_claim:
            self.on_claim(hid)
        if hid in self.refuse:
            raise RuntimeError(f"Instance {hid} is not in 'running' state (concurrent update?)")
        if hid in self.rows:
            self.rows[hid]["status"] = "updating"

    async def update(self, hid, version, claimed=False):
        self.calls.append(("update", hid, version, claimed))
        await asyncio.sleep(0.02)
        row = self.rows.get(hid)
        if hid in self.fail:
            if row is not None:
                row.update(status="running", error_message=(
                    f"Update to v{version} failed; the update command was sent. "
                    "RuntimeError: SSM update failed: TimedOut"))
            raise RuntimeError("SSM update failed: TimedOut")
        if row is not None:
            row.update(version=version, update_done=True)   # 'running' a few looks later

    def updates(self):
        return [c for c in self.calls if c[0] in ("claim_update", "update")]


@pytest.fixture
def host(monkeypatch):
    monkeypatch.setattr(config.settings, "app_version", HOST)
    hive_idle._TRIED.clear()
    hive_idle._LAST_BUSY.clear()
    so._IN_FLIGHT.clear()
    yield
    hive_idle._TRIED.clear()
    hive_idle._LAST_BUSY.clear()
    so._IN_FLIGHT.clear()


@pytest.fixture
def world(monkeypatch, host):
    """Running Hives, each idle on every count unless a test says otherwise, last changed
    and Host up 3 h before NOW (so an idle Hive at the Host's version is stopped)."""
    w = {"hives": [], "open_runs": {}, "sessions": {}, "prov": _Prov()}
    monkeypatch.setitem(hive_idle._HOST_UP_AT, "at", NOW - 3 * 3600)
    monkeypatch.setattr(hive_idle, "idle_after_s", lambda value=...: 7200.0)

    async def running():
        return [{"updated_at": _db(NOW - 3 * 3600), **h} for h in w["hives"]]
    monkeypatch.setattr(hive_idle, "_running_hives", running)
    monkeypatch.setattr(so, "open_runs_by_hive", lambda strict=False: w["open_runs"])

    async def sessions(hid):
        return w["sessions"].get(hid, {})
    monkeypatch.setattr(hive_idle, "_sessions", sessions)
    monkeypatch.setattr(so, "snapshot", lambda: {"runs": []})
    monkeypatch.setattr(hive_idle, "_provisioner", lambda: w["prov"])

    async def give_back(hid):
        w["prov"].calls.append(("release_update", hid))
    monkeypatch.setattr(hive_idle, "_release_update_claim", give_back)
    return w


def _look(now: float = NOW) -> list[str]:
    async def go():
        stopped = await hive_idle.check_once(now)
        await asyncio.gather(*list(hive_idle._STOPS), *list(hive_idle._UPDATES))
        return stopped
    return asyncio.run(go())


@pytest.fixture
def row(monkeypatch, host):
    """One Hive row as hive_row reads it; a row left 'updating' by a finished update turns
    'running' after two more looks (the update's own end of the status)."""
    monkeypatch.setattr(hive_idle, "WAKE_POLL_S", 0.01)
    hive_idle._STARTS.clear()
    r = {"id": "h1", "name": "worker-1", "status": "stopped", "error_message": None,
         "version": "2.5.4", "looks_after_update": 0}
    prov = _Prov(rows={"h1": r})

    async def read(ref):
        if r["status"] == "updating" and r.get("update_done"):
            r["looks_after_update"] += 1
            if r["looks_after_update"] >= 2:
                r["status"] = "running"
        return {k: r[k] for k in ("id", "name", "status", "error_message", "version")}
    monkeypatch.setattr(hive_idle, "hive_row", read)

    async def start(hid):
        prov.calls.append(("start", hid))
        r.update(status="starting", error_message=None)
        await asyncio.sleep(0.02)
        r["status"] = "running"
    prov.start = start
    monkeypatch.setattr(hive_idle, "_provisioner", lambda: prov)
    return r, prov


# ── (1) the idle loop ──

def test_a_hive_set_always_on_still_follows_the_host_version(world):
    world["hives"] = [{"id": "h1", "name": "worker-1", "version": "2.5.4", "idle_stop_minutes": 0},
                      {"id": "h2", "name": "worker-2", "version": HOST, "idle_stop_minutes": 0}]
    assert _look() == []                                # neither is stopped
    assert world["prov"].calls == [("claim_update", "h1"), ("update", "h1", HOST, True)]


def test_an_idle_hive_follows_the_host_version(world, monkeypatch):
    world["hives"] = [
        {"id": "h1", "name": "worker-1", "version": "2.5.4"},    # idle, behind: updated
        {"id": "h2", "name": "worker-2", "version": "2.5.4"},    # an open run
        {"id": "h3", "name": "worker-3", "version": "2.5.4"},    # a streaming session
        {"id": "h4", "name": "worker-4", "version": "2.5.4"},    # a dispatch in flight
        {"id": "h5", "name": "worker-5", "version": "2.5.5"},    # at the Host's version
        {"id": "h6", "name": "worker-6", "version": None},       # no version: differs
    ]
    world["open_runs"] = {"worker-2": 1}
    world["sessions"] = {"h3": {"s1": {"streaming": True}}}
    so._IN_FLIGHT["worker-4"] = 1
    stopped = _look()
    calls = world["prov"].calls
    assert sorted(world["prov"].updates()) == [("claim_update", "h1"), ("claim_update", "h6"),
                                               ("update", "h1", HOST, True),
                                               ("update", "h6", HOST, True)]
    # an updated Hive is not also stopped in that round; the one at the version is
    assert stopped == ["worker-5"]
    assert ("claim_stop", "h1") not in calls and ("claim_stop", "h6") not in calls

    # with the idle stop off, an idle Hive still follows the Host (and nothing is stopped)
    hive_idle._TRIED.clear()
    world["prov"].calls.clear()
    world["hives"] = world["hives"][:1]
    monkeypatch.setattr(hive_idle, "idle_after_s", lambda value=...: None)
    assert _look() == []
    assert world["prov"].calls == [("claim_update", "h1"), ("update", "h1", HOST, True)]

    # a dispatch that begins while the claim is made keeps the Hive: the claim is given
    # back, nothing runs, and the next round tries again
    hive_idle._TRIED.clear()
    world["prov"] = _Prov(on_claim=lambda hid: so._IN_FLIGHT.update({"worker-1": 1}))
    _look()
    assert world["prov"].calls == [("claim_update", "h1"), ("release_update", "h1")]
    so._IN_FLIGHT.clear()
    world["prov"] = _Prov()
    _look()
    assert world["prov"].updates() == [("claim_update", "h1"), ("update", "h1", HOST, True)]

    # a Host that does not know its version (no VERSION file) updates nothing
    hive_idle._TRIED.clear()
    world["prov"].calls.clear()
    config.settings.app_version = "0.0.0-dev"
    _look()
    assert world["prov"].updates() == []


# ── (2) wake ──

def test_a_stopped_hive_is_updated_when_woken(row):
    r, prov = row
    asyncio.run(hive_idle.wake("h1"))
    assert prov.calls == [("start", "h1"), ("claim_update", "h1"), ("update", "h1", HOST, True)]
    assert r["status"] == "running" and r["version"] == HOST
    assert r["looks_after_update"] >= 2              # returned only once it ran again

    # the update raises: the wake fails and names why
    hive_idle._TRIED.clear()
    r.update(status="stopped", version="2.5.4", looks_after_update=0, update_done=False)
    prov.calls.clear()
    prov.fail.add("h1")
    with pytest.raises(hive_idle.WakeError, match="SSM update failed: TimedOut"):
        asyncio.run(hive_idle.wake("h1"))
    assert prov.updates() == [("claim_update", "h1"), ("update", "h1", HOST, True)]

    # another update holds the Hive when the wake claims it: the wake waits for it to end
    hive_idle._TRIED.clear()
    prov.fail.clear()
    prov.calls.clear()
    r.update(status="stopped", version="2.5.4", looks_after_update=0, update_done=False)

    def person_updates(hid):
        r.update(status="updating", version=HOST, update_done=True)
        prov.refuse.add(hid)
    prov.on_claim = person_updates
    asyncio.run(hive_idle.wake("h1"))
    assert prov.updates() == [("claim_update", "h1")]
    assert r["status"] == "running" and r["looks_after_update"] >= 2


# ── (3) one try ──

def test_a_failed_update_is_not_tried_again(world, row, monkeypatch):
    world["hives"] = [{"id": "h1", "name": "worker-1", "version": "2.5.4"},
                      {"id": "h2", "name": "worker-2", "version": "2.5.4"}]
    world["prov"] = _Prov(refuse={"h2"}, fail={"h1"})
    monkeypatch.setattr(hive_idle, "_provisioner", lambda: world["prov"])
    _look()
    assert sorted(world["prov"].updates()) == [("claim_update", "h1"), ("claim_update", "h2"),
                                               ("update", "h1", HOST, True)]
    world["prov"].calls.clear()
    _look(NOW + 600)
    assert world["prov"].updates() == []
    # a wake of that Hive at that version does not try it either
    r, _ = row
    r.update(status="running", version="2.5.4")
    asyncio.run(hive_idle.wake("h1"))
    assert world["prov"].updates() == []
