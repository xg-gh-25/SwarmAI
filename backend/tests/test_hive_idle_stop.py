"""The Host stops a Hive nobody uses (core/hive_idle).

Questions: (1) does the setting give 120 minutes by default, its own minutes, or off;
(2) does each condition (an open run of this Host, a session at work, a run waiting for
its lessons, a dispatch in flight) and each unreadable one keep the Hive; (3) is an idle
Hive stopped (claim_stop, then stop as claimed) only once the idle time has passed since
its last busy look, its last status change and the Host's own start; (4) does a run
waiting for its lessons keep its Hive until they are in; (5) does a dispatch that begins
while the claim is made keep the Hive; (6) does a dispatch's wake wait for a stop, start
the Hive once for every caller, and say why when it ends elsewhere.
"""
from __future__ import annotations

import asyncio
import time

import pytest

from core import hive_idle
from core import swarm_out as so

H = {"id": "h1", "name": "worker-1"}
NOW = 1_800_000_000.0


def _db(ts: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(ts))


class _Prov:
    def __init__(self, on_claim=None):
        self.calls: list = []
        self.on_claim = on_claim

    async def claim_stop(self, hid):
        self.calls.append(("claim", hid))
        if self.on_claim:
            self.on_claim()

    async def release_stop_claim(self, hid):
        self.calls.append(("release", hid))
        return True

    async def stop(self, hid, claimed=False):
        self.calls.append(("stop", hid, claimed))


@pytest.fixture
def world(monkeypatch):
    """One running Hive, idle on every count, last changed and Host up 3 h before NOW."""
    w = {"minutes": None, "updated_at": _db(NOW - 3 * 3600), "open_runs": {}, "sessions": {},
         "snapshot": {"runs": []}, "log": True, "prov": _Prov()}
    hive_idle._LAST_BUSY.clear()
    so._IN_FLIGHT.clear()
    monkeypatch.setitem(hive_idle._HOST_UP_AT, "at", NOW - 3 * 3600)

    class Cfg:
        def get(self, key, default=None):
            assert key == hive_idle.SETTING
            return w["minutes"]
    from core import app_config_manager
    monkeypatch.setattr(app_config_manager.AppConfigManager, "instance", classmethod(lambda cls: Cfg()))

    async def running():
        return [{**H, "updated_at": w["updated_at"]}]
    monkeypatch.setattr(hive_idle, "_running_hives", running)
    monkeypatch.setattr(so, "open_runs_by_hive", lambda strict=False: w["open_runs"])

    async def sessions(hid):
        return w["sessions"]
    monkeypatch.setattr(hive_idle, "_sessions", sessions)
    monkeypatch.setattr(so, "snapshot", lambda: w["snapshot"])

    class _P:
        def exists(self):
            return w["log"]
    monkeypatch.setattr(so, "_log_path", lambda: _P())
    monkeypatch.setattr(hive_idle, "_provisioner", lambda: w["prov"])
    yield w
    so._IN_FLIGHT.clear()
    hive_idle._LAST_BUSY.clear()


def _look(now: float = NOW) -> list[str]:
    async def go():
        stopped = await hive_idle.check_once(now)
        await asyncio.gather(*list(hive_idle._STOPS))
        return stopped
    return asyncio.run(go())


# ── (1) the setting ──

@pytest.mark.parametrize("value,expected", [
    (None, 7200.0), (30, 1800.0), (2.5, 150.0), (0, None), (False, None),
    ("soon", 7200.0), (-5, 7200.0), (True, 7200.0)])
def test_the_setting(value, expected):
    assert hive_idle.idle_after_s(value) == expected


def test_the_setting_is_read_from_the_app_config(world):
    world["minutes"] = 45
    assert hive_idle.idle_after_s() == 45 * 60.0
    world["minutes"] = 0
    assert hive_idle.idle_after_s() is None
    assert _look() == [] and world["prov"].calls == []


def test_the_default_setting_is_two_hours():
    from core.app_config_manager import DEFAULT_CONFIG
    assert DEFAULT_CONFIG[hive_idle.SETTING] == 120
    assert hive_idle.idle_after_s(DEFAULT_CONFIG[hive_idle.SETTING]) == 7200.0


# ── (2) each condition ──

@pytest.mark.parametrize("kw,reason", [
    ({"open_runs": 1}, "open run"),
    ({"open_runs": None}, "open runs unknown"),
    ({"sessions": {"s1": {"streaming": True}}}, "active"),
    ({"sessions": {"s1": {"waiting_input": True}}}, "active"),
    ({"sessions": {"s1": {"background_work": True}}}, "active"),
    ({"sessions": {"s1": {"activity": "preparing"}}}, "active"),
    ({"sessions": None}, "sessions unknown"),
    ({"lessons_waiting": 2}, "lessons"),
    ({"lessons_waiting": None}, "lessons unknown"),
    ({"in_flight": True}, "dispatch in flight"),
])
def test_each_condition_keeps_the_hive(kw, reason):
    args = {"open_runs": 0, "sessions": {"s1": {"streaming": False, "activity": "idle"}},
            "lessons_waiting": 0, "in_flight": False, **kw}
    assert any(reason in r for r in hive_idle.busy_reasons(**args))


def test_a_hive_with_every_condition_met_is_idle():
    assert hive_idle.busy_reasons(0, {"s1": {"streaming": False, "activity": "idle"}}, 0, False) == []
    assert hive_idle.busy_reasons(0, {}, 0, False) == []


@pytest.mark.parametrize("change", [
    lambda w: w.update(open_runs={"worker-1": 1}),
    lambda w: w.update(open_runs=None),
    lambda w: w.update(sessions={"s1": {"streaming": True}}),
    lambda w: w.update(sessions=None),
    lambda w: w.update(snapshot=None),                       # a log, no snapshot yet: unknown
    lambda w: so._IN_FLIGHT.update({"worker-1": 1}),
])
def test_a_busy_or_unreadable_hive_is_kept_and_its_idle_time_starts_again(world, change):
    change(world)
    assert _look() == [] and world["prov"].calls == []
    assert hive_idle._LAST_BUSY["h1"] == NOW
    # idle again from the next look: stopped only two hours after the busy one
    world.update(open_runs={}, sessions={}, snapshot={"runs": []})
    so._IN_FLIGHT.clear()
    assert _look(NOW + 3600) == []
    assert _look(NOW + 7200) == ["worker-1"]


def test_an_open_run_on_another_hive_does_not_keep_this_one(world):
    world["open_runs"] = {"worker-2": 3}
    assert _look() == ["worker-1"]


# ── (3) stopped after the idle time ──

def test_an_idle_hive_is_stopped_after_the_idle_time(world):
    assert _look() == ["worker-1"]
    assert world["prov"].calls == [("claim", "h1"), ("stop", "h1", True)]


@pytest.mark.parametrize("recent", ["updated_at", "host_up", "last_busy"])
def test_an_idle_hive_is_kept_until_the_idle_time_has_passed(world, monkeypatch, recent):
    if recent == "updated_at":
        world["updated_at"] = _db(NOW - 1800)          # started (or changed) 30 min ago
    elif recent == "host_up":
        monkeypatch.setitem(hive_idle._HOST_UP_AT, "at", NOW - 1800)
    else:
        hive_idle._LAST_BUSY["h1"] = NOW - 1800
    assert _look() == [] and world["prov"].calls == []
    assert _look(NOW + 5400) == ["worker-1"]


def test_a_stopped_hive_forgets_its_last_busy_look(world, monkeypatch):
    hive_idle._LAST_BUSY["h1"] = NOW

    async def none():
        return []
    monkeypatch.setattr(hive_idle, "_running_hives", none)
    _look()
    assert "h1" not in hive_idle._LAST_BUSY


def test_a_hive_no_longer_running_at_the_claim_is_left_alone(world):
    class Gone(_Prov):
        async def claim_stop(self, hid):
            raise RuntimeError("Instance h1 is not in 'running' state")
    world["prov"] = Gone()
    assert _look() == []


# ── (4) lessons ──

def test_a_run_waiting_for_its_lessons_keeps_the_hive(world):
    world["snapshot"] = {"runs": [{"hive_id": "h1", "settle": {"why_not": ["lessons"]}},
                                  {"hive_id": "h2", "settle": {"why_not": []}}]}
    assert _look() == [] and world["prov"].calls == []
    world["snapshot"] = {"runs": [{"hive_id": "h1", "settle": {"why_not": []}}]}   # lessons in
    assert _look(NOW + 3600) == []
    assert _look(NOW + 7200) == ["worker-1"]


def test_lessons_waiting_on_another_hive_do_not_keep_this_one(world):
    world["snapshot"] = {"runs": [{"hive_id": "h2", "settle": {"why_not": ["lessons", "code"]}}]}
    assert _look() == ["worker-1"]


def test_no_dispatch_log_means_no_lessons_to_wait_for(world):
    world.update(snapshot=None, log=False)
    assert _look() == ["worker-1"]


# ── (5) a dispatch during the claim ──

def test_a_dispatch_arriving_during_the_claim_keeps_the_hive(world):
    world["prov"] = _Prov(on_claim=lambda: so._IN_FLIGHT.update({"worker-1": 1}))
    assert _look() == []
    assert world["prov"].calls == [("claim", "h1"), ("release", "h1")]


def test_the_in_flight_marker_counts_nested_dispatches():
    so._IN_FLIGHT.clear()
    with so._Dispatching("worker-1"):
        with so._Dispatching("worker-1"):
            assert so.dispatch_in_flight("h1", "worker-1")
        assert so.dispatch_in_flight("h1", "worker-1")
    assert not so.dispatch_in_flight("h1", "worker-1") and so._IN_FLIGHT == {}


# ── (6) wake ──

class _Row:
    def __init__(self, statuses):
        self.statuses = list(statuses)
        self.status = self.statuses.pop(0)
        self.error = None
        self.starts = 0


@pytest.fixture
def waking(monkeypatch):
    monkeypatch.setattr(hive_idle, "WAKE_POLL_S", 0.01)
    hive_idle._STARTS.clear()
    r = _Row(["stopping", "stopping", "stopped"])

    async def row(ref):
        if r.statuses and r.status == "stopping":
            r.status = r.statuses.pop(0)        # the stop ends after a few looks
        return {"id": "h1", "name": "worker-1", "status": r.status, "error_message": r.error}
    monkeypatch.setattr(hive_idle, "hive_row", row)

    class P:
        async def start(self, hid):
            r.starts += 1
            r.status = "starting"
            await asyncio.sleep(0.05)
            if r.error:
                r.status = "error"
                return
            r.status = "running"
    monkeypatch.setattr(hive_idle, "_provisioner", lambda: P())
    return r


def test_wake_waits_for_the_stop_and_starts_the_hive_once_for_every_caller(waking):
    async def go():
        await asyncio.gather(hive_idle.wake("h1"), hive_idle.wake("h1"))
    asyncio.run(go())
    assert waking.status == "running" and waking.starts == 1


def test_wake_says_why_when_the_hive_ends_in_error(waking):
    waking.error = "Start failed: InsufficientInstanceCapacity"
    with pytest.raises(hive_idle.WakeError, match="InsufficientInstanceCapacity"):
        asyncio.run(hive_idle.wake("h1"))


# ── the restart route uses the same marker ──

def test_the_restart_route_refuses_while_a_dispatch_is_in_flight(monkeypatch, tmp_path):
    from tests import test_zero_touch as zt
    so._IN_FLIGHT.clear()
    so._IN_FLIGHT["h1"] = 1
    try:
        r, fake = zt._restart_route(monkeypatch, tmp_path, probe=zt._QUIET)
    finally:
        so._IN_FLIGHT.clear()
    assert r.status_code == 409 and "being sent" in r.json()["detail"]
    assert fake.claimed == [] and fake.restarted == []


def test_the_restart_route_gives_the_claim_back_when_a_dispatch_began_meanwhile(monkeypatch, tmp_path):
    from tests import test_zero_touch as zt

    class Racing(zt._RestartProvisioner):
        released: list = []

        async def claim_stop(self, instance_id):
            await super().claim_stop(instance_id)
            so._IN_FLIGHT["h1"] = 1                    # a dispatch to it began

        async def release_stop_claim(self, instance_id):
            self.released.append(instance_id)
            return True
    monkeypatch.setattr(zt, "_RestartProvisioner", Racing)
    so._IN_FLIGHT.clear()
    try:
        r, fake = zt._restart_route(monkeypatch, tmp_path, probe=zt._QUIET)
    finally:
        so._IN_FLIGHT.clear()
    assert r.status_code == 409 and fake.released == ["i1"] and fake.restarted == []
