"""Machine cost under Swarm Out (core/machine_cost; design: the history's cost section).

1. record_power keeps one hive_power row per change of what EC2 reports, an on row from
   the instance's LaunchTime, an off row from the stop time EC2 gives, and none when
   nothing changed (its last row's observed_at moves instead).
2. A region EC2 cannot answer writes nothing for its Hives; the others are recorded.
3. machines() gives each Hive's on periods, the start of its record and its hourly price;
   an instance type with no price has none (null, never zero).
4. An AgentCore run's billed seconds end at the earlier of its release and its result's
   end plus the idle timeout; a round with no start or no end gives no cost.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from core import machine_cost as mc
from core.carriers import agentcore as ac
from hive import accounts as ha


def _db(tmp_path: Path, hives: list[tuple]) -> Path:
    p = tmp_path / "data.db"
    c = sqlite3.connect(p)
    c.execute("CREATE TABLE hive_accounts (id TEXT PRIMARY KEY, auth_method TEXT)")
    c.execute("CREATE TABLE hive_instances (id TEXT PRIMARY KEY, name TEXT, account_ref TEXT, "
              "region TEXT, instance_type TEXT, ec2_instance_id TEXT)")
    c.execute("INSERT INTO hive_accounts VALUES ('acct', 'swarmai'), ('other', 'swarmai')")
    c.executemany("INSERT INTO hive_instances VALUES (?, ?, ?, ?, ?, ?)", hives)
    c.commit()
    c.close()
    return p


def _rows(p: Path) -> list[tuple]:
    c = sqlite3.connect(p)
    try:
        return c.execute("SELECT hive_id, state, since FROM hive_power ORDER BY id").fetchall()
    finally:
        c.close()


@pytest.fixture
def ec2(monkeypatch):
    """What EC2 says, per instance id, and which regions fail."""
    said: dict = {"by_id": {}, "fail": set(), "purposes": []}

    def session_for(row, region, purpose="manage"):
        said["purposes"].append(purpose)
        return region

    def power(session, region, ids):
        if region in said["fail"]:
            raise RuntimeError("no EC2")
        return {i: said["by_id"][i] for i in ids}
    monkeypatch.setattr(ha, "session_for", session_for)
    monkeypatch.setattr(mc, "ec2_power", power)
    return said


def _on(launch: str) -> dict:
    return {"state": "running", "launch": launch, "transition": None}


def _off(at) -> dict:
    return {"state": "stopped", "launch": "2026-11-23T01:00:00+00:00", "transition": at}


def test_record_power_appends_only_changes(tmp_path, ec2, monkeypatch):
    p = _db(tmp_path, [("h1", "demo", "acct", "us-east-1", "m7g.xlarge", "i-1")])
    ec2["by_id"]["i-1"] = _on("2026-11-23T01:00:00+00:00")
    looks = iter(["2026-11-23T02:00:00+00:00", "2026-11-23T02:10:00+00:00"])
    monkeypatch.setattr(mc, "_now_iso", lambda: next(looks, "2026-11-23T09:00:00+00:00"))
    assert mc._record_power_sync(p) == {"appended": 1, "skipped": 0}
    assert mc._record_power_sync(p)["appended"] == 0          # nothing changed
    c = sqlite3.connect(p)
    seen = c.execute("SELECT observed_at FROM hive_power").fetchall()
    c.close()
    assert seen == [("2026-11-23T02:10:00+00:00",)]           # the look moved the last row's time
    ec2["by_id"]["i-1"] = _off("2026-11-23T03:30:00+00:00")
    assert mc._record_power_sync(p)["appended"] == 1
    assert mc._record_power_sync(p)["appended"] == 0          # still stopped: no row
    ec2["by_id"]["i-1"] = _off(None)                          # EC2 gives no stop time
    assert mc._record_power_sync(p)["appended"] == 0          # off already: no row
    ec2["by_id"]["i-1"] = _on("2026-11-23T05:00:00+00:00")
    mc._record_power_sync(p)
    ec2["by_id"]["i-1"] = _on("2026-11-23T07:00:00+00:00")   # restarted between two looks
    mc._record_power_sync(p)
    assert _rows(p) == [("h1", "on", "2026-11-23T01:00:00+00:00"),
                        ("h1", "off", "2026-11-23T03:30:00+00:00"),
                        ("h1", "on", "2026-11-23T05:00:00+00:00"),
                        ("h1", "on", "2026-11-23T07:00:00+00:00")]
    assert set(ec2["purposes"]) == {"read"}                   # it only ever reads EC2


def test_an_off_without_a_stop_time_is_the_time_of_the_look(tmp_path, ec2):
    p = _db(tmp_path, [("h1", "demo", "acct", "us-east-1", "m7g.xlarge", "i-1")])
    ec2["by_id"]["i-1"] = _off(None)
    mc._record_power_sync(p)
    (hid, state, since), = _rows(p)
    assert state == "off" and abs(mc._ts(since) - datetime.now(timezone.utc).timestamp()) < 60


def test_record_power_skips_a_group_ec2_cannot_answer(tmp_path, ec2):
    p = _db(tmp_path, [("h1", "demo", "acct", "us-east-1", "m7g.xlarge", "i-1"),
                       ("h2", "west", "acct", "us-west-2", "m7g.xlarge", "i-2")])
    ec2["by_id"] = {"i-1": _on("2026-11-23T01:00:00+00:00"), "i-2": _on("2026-11-23T02:00:00+00:00")}
    ec2["fail"].add("us-west-2")
    assert mc._record_power_sync(p) == {"appended": 1, "skipped": 1}
    assert [r[0] for r in _rows(p)] == ["h1"]


def test_machines_periods_and_prices(tmp_path, ec2):
    p = _db(tmp_path, [("h1", "demo", "acct", "us-east-1", "m7g.xlarge", "i-1"),
                       ("h2", "odd", "acct", "us-east-1", "x9.weird", "i-2"),
                       ("h3", "gone", "acct", "us-east-1", "m7g.xlarge", "i-3")])
    ec2["by_id"] = {"i-1": _on("2026-11-23T01:00:00+00:00"), "i-2": _on("2026-11-23T02:00:00+00:00"),
                    "i-3": _on("2026-11-23T02:00:00+00:00")}
    mc._record_power_sync(p)
    ec2["by_id"]["i-1"] = _off("2026-11-23T03:00:00+00:00")
    mc._record_power_sync(p)
    ec2["by_id"]["i-1"] = _on("2026-11-23T04:00:00+00:00")
    mc._record_power_sync(p)
    c = sqlite3.connect(p)
    c.execute("DELETE FROM hive_instances WHERE id = 'h3'")
    c.execute("UPDATE hive_power SET observed_at = '2026-11-23T05:00:00+00:00' WHERE hive_id = 'h3'")
    c.commit()
    c.close()
    m = mc.machines(p, now="2026-11-23T06:00:00+00:00")
    by = {h["hive_id"]: h for h in m["ec2"]}
    assert by["h1"]["on"] == [["2026-11-23T01:00:00+00:00", "2026-11-23T03:00:00+00:00"],
                              ["2026-11-23T04:00:00+00:00", "2026-11-23T06:00:00+00:00"]]
    assert by["h1"]["recorded_since"] == "2026-11-23T01:00:00+00:00"
    assert by["h1"]["hourly_usd"] == pytest.approx(119 / 730, abs=1e-4)
    assert by["h1"]["name"] == "demo"
    assert by["h2"]["hourly_usd"] is None                    # unknown type: no price, not zero
    assert by["h3"]["on"] == [["2026-11-23T02:00:00+00:00", "2026-11-23T05:00:00+00:00"]]
    assert m["agentcore"]["vcpu"] == 2 and m["agentcore"]["memory_gib"] == 8


def test_an_empty_database_has_no_machines(tmp_path):
    p = _db(tmp_path, [])
    assert mc.machines(p)["ec2"] == []


SID = "e3204508-5fd8-4b86-a0e8-db8a56c84038-0000001"


def _run(opened: float, ended_iso, active=600, carrier="agentcore") -> dict:
    return {"dispatch_id": "so_1", "rounds": [{"carrier": carrier, "opened_at": opened, "run_ref": SID}],
            "result": {"ended_at": ended_iso, "active_s": active}}


def _marker(tmp_path, monkeypatch, why: str, at: float) -> None:
    d = tmp_path / "state"
    monkeypatch.setattr(ac, "_state_dir", lambda: d)
    p = ac._ended_path(SID)
    p.write_text(json.dumps({"why": why, "at": at}), encoding="utf-8")


T0 = datetime(2026, 11, 23, 1, 0, tzinfo=timezone.utc).timestamp()
END = "2026-11-23T01:20:00+00:00"           # 1200 s after T0


def test_agentcore_run_cost(tmp_path, monkeypatch):
    monkeypatch.setattr(ac, "_state_dir", lambda: tmp_path / "none")
    # not released: the result's end plus the 900 s idle timeout
    c = mc.agentcore_cost(_run(T0, END))
    assert c["seconds"] == 2100
    assert c["usd"] == pytest.approx(2100 / 3600 * 8 * 0.00945 + 600 / 3600 * 2 * 0.0895, abs=1e-4)
    # released before the idle end: the release
    _marker(tmp_path, monkeypatch, "released", T0 + 1300)
    assert mc.agentcore_cost(_run(T0, END))["seconds"] == 1300
    # released after the session had already idled out: the idle end
    _marker(tmp_path, monkeypatch, "released", T0 + 9000)
    assert mc.agentcore_cost(_run(T0, END))["seconds"] == 2100


def test_agentcore_cost_unknown_is_none(tmp_path, monkeypatch):
    monkeypatch.setattr(ac, "_state_dir", lambda: tmp_path / "none")
    assert mc.agentcore_cost(_run(T0, None)) is None           # no end: unknown
    assert mc.agentcore_cost(_run(None, END)) is None          # no start: unknown
    assert mc.agentcore_cost(_run(T0, END, carrier="ec2")) is None
    rows = [_run(T0, None), _run(T0, END), {"dispatch_id": "e", "rounds": []}]
    mc.attach(rows)
    assert rows[0]["machine"] is None and rows[1]["machine"]["seconds"] == 2100
    assert "machine" not in rows[2]                             # an EC2 run: shared in the view


def test_a_marker_that_is_not_a_release_is_no_end(tmp_path, monkeypatch):
    _marker(tmp_path, monkeypatch, "closed", T0 + 100)
    assert ac.released_at(SID) is None
    assert ac.released_at("bad id") is None


class _NotFound(Exception):
    response = {"Error": {"Code": "InvalidInstanceID.NotFound"}}


class _Session:
    def __init__(self, known: dict):
        self.known = known

    def client(self, name, region_name=None):
        assert name == "ec2"
        return self

    def describe_instances(self, InstanceIds):
        if any(i not in self.known for i in InstanceIds):
            raise _NotFound()
        return {"Reservations": [{"Instances": [self.known[i] for i in InstanceIds]}]}


def test_ec2_power_reads_launch_and_stop_times():
    s = _Session({
        "i-1": {"InstanceId": "i-1", "State": {"Name": "running"},
                "LaunchTime": datetime(2026, 11, 23, 6, 17, 55, tzinfo=timezone.utc), "StateTransitionReason": ""},
        "i-2": {"InstanceId": "i-2", "State": {"Name": "stopped"},
                "LaunchTime": datetime(2026, 11, 20, 1, 0, tzinfo=timezone.utc),
                "StateTransitionReason": "User initiated (2026-11-23 08:01:02 GMT)"},
    })
    out = mc.ec2_power(s, "us-east-1", ["i-1", "i-2", "i-gone"])
    assert out["i-1"] == {"state": "running", "launch": "2026-11-23T06:17:55+00:00", "transition": None}
    assert out["i-2"]["transition"] == "2026-11-23T08:01:02+00:00"
    assert out["i-gone"]["state"] == "gone"


def _power_rows(p: Path, rows: list[tuple]) -> None:
    c = sqlite3.connect(p)
    c.executemany("INSERT INTO hive_power (hive_id, ec2_instance_id, instance_type, state, since, observed_at) "
                  "VALUES (?, 'i-1', 'm7g.xlarge', ?, ?, ?)", rows)
    c.commit()
    c.close()


def test_hours_no_look_saw_are_not_counted(tmp_path):
    p = _db(tmp_path, [("h1", "demo", "acct", "us-east-1", "m7g.xlarge", "i-1")])
    mc.machines(p)                                            # creates the table
    # seen on until 03:00, then the Mac slept; at 09:00 a new LaunchTime: stopped and
    # started meanwhile, the stop not seen
    _power_rows(p, [("h1", "on", "2026-11-23T01:00:00+00:00", "2026-11-23T03:00:00+00:00"),
                    ("h1", "on", "2026-11-23T09:00:00+00:00", "2026-11-23T11:00:00+00:00")])
    (h,) = mc.machines(p, now="2026-11-23T11:05:00+00:00")["ec2"]
    assert h["on"] == [["2026-11-23T01:00:00+00:00", "2026-11-23T03:00:00+00:00"],
                       ["2026-11-23T09:00:00+00:00", "2026-11-23T11:05:00+00:00"]]


def test_an_open_period_ends_soon_after_the_last_look(tmp_path):
    p = _db(tmp_path, [("h1", "demo", "acct", "us-east-1", "m7g.xlarge", "i-1")])
    mc.machines(p)
    # EC2 not read since 05:00 (credentials expired): the hours after are unknown
    _power_rows(p, [("h1", "on", "2026-11-23T01:00:00+00:00", "2026-11-23T05:00:00+00:00")])
    (h,) = mc.machines(p, now="2026-11-23T12:00:00+00:00")["ec2"]
    assert h["on"] == [["2026-11-23T01:00:00+00:00", "2026-11-23T05:20:00+00:00"]]
