"""Each EC2 Hive's own idle stop: the column, the route and the list (database/sqlite v22,
routers/hive ``set_idle_stop`` / ``_with_idle_stop``, hive/fleet ``replace``). The idle loop
reading it is tested in test_hive_idle_stop.py (7) and test_hive_auto_update.py.

Questions answered:
1. does migration v22 add a nullable ``idle_stop_minutes`` to an existing database, every
   existing row NULL and nothing else changed, and add nothing when the column is there?
2. does PUT /api/hive/instances/{id}/idle-stop write null, 0 and a whole number of minutes
   from 30 to 480 for an EC2 Hive, and does the list return each row's own value, the
   effective minutes (the default for NULL, 0 for always on) and whether it can be set?
3. does it refuse (400, the app's VALIDATION_FAILED; nothing written) a string, a boolean, a fraction, a number out of
   range, a missing value or an extra field; (409) Elastic Workers, a carrier without the
   idle_stop duty and EC2 while its carrier has no such duty; (404) an unknown id; (403) a
   Hive's own backend?
4. does a replacement Hive keep the old one's own value?
"""
from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import aiosqlite
import pytest
from httpx import ASGITransport, AsyncClient

from core import hive_idle
from hive import accounts as ha
from hive import fleet

MODE = {"account_id": "111111111111", "region": "us-east-1"}


# ── (1) the migration ──

def _init(path: Path) -> None:
    from database.sqlite import SQLiteDatabase, close_all_pools

    async def go():
        await SQLiteDatabase(db_path=str(path)).initialize()
        await close_all_pools()
    asyncio.run(go())


def test_the_migration_adds_a_null_column_to_existing_rows(tmp_path):
    from database.sqlite import CURRENT_SCHEMA_VERSION
    db = tmp_path / "m.db"
    _init(db)
    with sqlite3.connect(db) as c:
        c.execute("INSERT INTO hive_accounts (id, account_id, created_at) VALUES ('a', '1', 'now')")
        for iid, status in (("h1", "running"), ("h2", "stopped")):
            c.execute("INSERT INTO hive_instances (id, name, account_ref, status, version, created_at, "
                      "updated_at) VALUES (?, ?, 'a', ?, '2.5.5', 'c', 'u')", (iid, iid, status))
        # a database written before v22: no column, user_version 21
        c.execute("ALTER TABLE hive_instances DROP COLUMN idle_stop_minutes")
        c.execute("PRAGMA user_version = 21")
        before = c.execute("SELECT * FROM hive_instances ORDER BY id").fetchall()
    _init(db)
    with sqlite3.connect(db) as c:
        cols = {r[1]: r for r in c.execute("PRAGMA table_info(hive_instances)")}
        assert cols["idle_stop_minutes"][2] == "INTEGER" and cols["idle_stop_minutes"][3] == 0
        assert cols["idle_stop_minutes"][4] is None                       # no default: NULL
        assert c.execute("PRAGMA user_version").fetchone()[0] == CURRENT_SCHEMA_VERSION >= 22
        assert c.execute("SELECT id, idle_stop_minutes FROM hive_instances ORDER BY id").fetchall() == [
            ("h1", None), ("h2", None)]
        names = [r[1] for r in c.execute("PRAGMA table_info(hive_instances)")]
        after = [tuple(v for n, v in zip(names, r) if n != "idle_stop_minutes")
                 for r in c.execute("SELECT * FROM hive_instances ORDER BY id")]
        assert after == before                                            # nothing else changed
        # the column already there (a v21 marker left behind): added once, values kept
        c.execute("UPDATE hive_instances SET idle_stop_minutes = 0 WHERE id = 'h1'")
        c.execute("PRAGMA user_version = 21")
    _init(db)
    with sqlite3.connect(db) as c:
        assert c.execute("SELECT id, idle_stop_minutes FROM hive_instances ORDER BY id").fetchall() == [
            ("h1", 0), ("h2", None)]
        assert c.execute("PRAGMA user_version").fetchone()[0] == CURRENT_SCHEMA_VERSION


# ── (2)-(4) the route, the list, a replacement ──

@pytest.fixture(autouse=True)
async def _no_aws(monkeypatch):
    from routers import hive as hive_router
    prov = MagicMock()
    prov.deploy = AsyncMock(return_value=None)
    prov.release_stale_updates = AsyncMock(return_value=None)
    monkeypatch.setattr(hive_router, "_provisioner", prov)
    monkeypatch.setattr(ha, "swarmai_account", lambda: dict(MODE))
    monkeypatch.setattr(hive_idle, "idle_after_s", lambda value=...: 45 * 60.0)   # the default: 45 min
    monkeypatch.delenv("SWARMAI_MODE", raising=False)
    fleet._save_config({})
    yield prov
    fleet._save_config({})
    tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()
             and t.get_name().startswith("hive-deploy-")]
    if tasks:
        await asyncio.gather(*tasks)


@pytest.fixture
async def client():
    from main import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


def _db():
    from database import db
    return str(db.db_path)


async def _seed(iid: str, carrier: str = "ec2", own=None) -> str:
    await ha.ensure_swarmai_row(Path(_db()))
    async with aiosqlite.connect(_db()) as c:
        await c.execute(
            "INSERT INTO hive_instances (id, name, account_ref, region, instance_type, status, "
            "ec2_instance_id, carrier, idle_stop_minutes, created_at, updated_at) VALUES "
            "(?, ?, 'swarmai', 'us-east-1', 'm7g.xlarge', 'running', 'i-1', ?, ?, 't', 't')",
            (iid, iid, carrier, own))
        await c.commit()
    return iid


async def _own(iid: str):
    async with aiosqlite.connect(_db()) as c:
        row = await (await c.execute(
            "SELECT idle_stop_minutes FROM hive_instances WHERE id = ?", (iid,))).fetchone()
    return row[0]


async def _listed(client, iid: str) -> dict:
    r = await client.get("/api/hive/instances")
    assert r.status_code == 200
    (row,) = [x for x in r.json() if x["id"] == iid]
    return {k: row[k] for k in ("idle_stop_minutes", "idle_stop_effective", "idle_stop_settable")}


@pytest.mark.asyncio
async def test_the_route_sets_null_zero_and_minutes(client):
    hid = await _seed("ec2-a")
    # an existing row: NULL, the default, settable
    assert await _listed(client, hid) == {"idle_stop_minutes": None, "idle_stop_effective": 45.0,
                                          "idle_stop_settable": True}
    for minutes, effective in ((0, 0.0), (30, 30.0), (240, 240.0), (480, 480.0), (None, 45.0)):
        r = await client.put(f"/api/hive/instances/{hid}/idle-stop", json={"minutes": minutes})
        assert r.status_code == 200, r.text
        assert r.json() == {"id": hid, "idle_stop_minutes": minutes, "idle_stop_effective": effective,
                            "idle_stop_settable": True}
        assert await _own(hid) == minutes
        assert await _listed(client, hid) == {"idle_stop_minutes": minutes,
                                              "idle_stop_effective": effective,
                                              "idle_stop_settable": True}


@pytest.mark.asyncio
async def test_the_list_reads_a_stored_value_as_the_idle_loop_does(client):
    # a value no route writes (a REAL in the INTEGER column): the list shows the default,
    # which is also what core/hive_idle stops the Hive after
    hid = await _seed("ec2-real", own=45.5)
    assert await _own(hid) == 45.5
    assert await _listed(client, hid) == {"idle_stop_minutes": None, "idle_stop_effective": 45.0,
                                          "idle_stop_settable": True}
    assert hive_idle.own_idle_after_s(45.5, 45 * 60.0) == 45 * 60.0
    hid2 = await _seed("ec2-real-2", own=90.5)
    assert (await _listed(client, hid2))["idle_stop_effective"] == 45.0


@pytest.mark.asyncio
async def test_the_list_follows_a_changed_default(client, monkeypatch):
    hid = await _seed("ec2-b")
    monkeypatch.setattr(hive_idle, "idle_after_s", lambda value=...: None)        # default off
    assert (await _listed(client, hid))["idle_stop_effective"] == 0.0
    monkeypatch.setattr(hive_idle, "idle_after_s", lambda value=...: 120 * 60.0)
    assert (await _listed(client, hid))["idle_stop_effective"] == 120.0


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [
    {"minutes": "60"}, {"minutes": True}, {"minutes": 45.5}, {"minutes": 60.0}, {"minutes": -1},
    {"minutes": 1}, {"minutes": 29}, {"minutes": 481}, {"minutes": 600}, {},
    {"minutes": 60, "extra": 1}])
async def test_the_route_refuses_bad_minutes(client, body):
    hid = await _seed("ec2-c", own=120)
    r = await client.put(f"/api/hive/instances/{hid}/idle-stop", json=body)
    assert r.status_code == 400 and r.json()["code"] == "VALIDATION_FAILED", (body, r.text)
    assert await _own(hid) == 120


@pytest.mark.asyncio
async def test_the_route_refuses_rows_that_are_not_ec2(client, monkeypatch):
    from core.carriers import registry
    from routers import hive as hive_router
    elastic = await _seed("elastic-a", carrier="agentcore")
    ssh = await _seed("ssh-a", carrier="ssh", own=60)
    ec2 = await _seed("ec2-d")
    r = await client.put(f"/api/hive/instances/{elastic}/idle-stop", json={"minutes": 0})
    assert r.status_code == 409 and r.json()["detail"] == hive_router.ELASTIC_NOT_EC2
    r = await client.put(f"/api/hive/instances/{ssh}/idle-stop", json={"minutes": 0})
    assert r.status_code == 409 and r.json()["detail"] == hive_router.NOT_IDLE_STOPPED
    assert await _own(elastic) is None and await _own(ssh) == 60
    # a machine the Host never stops: no selector, never stopped, its stored value ignored
    assert await _listed(client, ssh) == {"idle_stop_minutes": None, "idle_stop_effective": 0.0,
                                          "idle_stop_settable": False}
    r = await client.put("/api/hive/instances/no-such-hive/idle-stop", json={"minutes": 0})
    assert r.status_code == 404
    # the EC2 carrier without the idle_stop duty: nothing to set there either
    monkeypatch.setattr(registry, "with_duty", lambda duty: [])
    r = await client.put(f"/api/hive/instances/{ec2}/idle-stop", json={"minutes": 0})
    assert r.status_code == 409 and await _own(ec2) is None
    assert (await _listed(client, ec2))["idle_stop_settable"] is False
    # a Hive's own backend never manages Hives (its auth refuses first, else the route)
    monkeypatch.setenv("SWARMAI_MODE", "hive")
    r = await client.put(f"/api/hive/instances/{ec2}/idle-stop", json={"minutes": 0})
    assert r.status_code in (401, 403) and await _own(ec2) is None
    with pytest.raises(hive_router.HTTPException) as refused:
        await hive_router.set_idle_stop(ec2, hive_router.HiveIdleStop(minutes=0))
    assert refused.value.status_code == 403 and await _own(ec2) is None


@pytest.mark.asyncio
async def test_a_replacement_keeps_the_own_value(client):
    for iid, own in (("ec2-on", 0), ("ec2-default", None)):
        await _seed(iid, own=own)
        r = await client.post(f"/api/hive/instances/{iid}/replace")
        assert r.status_code == 200, r.text
        assert await _own(r.json()["new"]) == own
        fleet._save_config({})                     # one replacement at a time in this test
