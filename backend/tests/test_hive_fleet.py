"""How many Hives (hive/fleet.py, routers aws_account /hive-fleet and hive /replace; design
2026-10-31-swarmai-aws-account §7.3).

Questions answered:
1. raising the desired count creates exactly the difference, also for repeated and
   concurrent requests; never above 8; lowering creates and deletes nothing; with more
   than 8 already only lowering is possible.
2. status: ready / short / over / cannot_build, and an unset count is max(1, counted):
   an upgrader with no Hive sees "1 short", nothing is created for them.
3. the last Hive can only be replaced; a replacement counts as one Hive, the old one is
   deleted once the new one runs, and kept when the new one fails.
4. reconcile: an instance EC2 no longer has stops counting (nothing created to make up
   for it); a failed lookup changes nothing.
5. provisioner: every resource is tagged swarmai:managed in its create call, and a role
   gets the SwarmAI boundary when the account has it.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import aiosqlite
import pytest
from httpx import ASGITransport, AsyncClient

from hive import accounts as ha
from hive import fleet

ACCT = "111111111111"
MODE = {"account_id": ACCT, "region": "us-east-1"}
UA = {"X-Swarm-User-Action": "test"}


@pytest.fixture(autouse=True)
async def _no_real_deploys(monkeypatch):
    from routers import hive as hive_router
    prov = MagicMock()
    prov.deploy = AsyncMock(return_value=None)
    prov.cleanup = AsyncMock(return_value=None)
    monkeypatch.setattr(hive_router, "_provisioner", prov)
    monkeypatch.setattr(ha, "swarmai_account", lambda: dict(MODE))
    fleet._save_config({})          # the config manager outlives a test: start clean
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
    return db.db_path


async def _seed(n, *, status="running", ec2="i-x", ref="swarmai", prefix="s"):
    await ha.ensure_swarmai_row(_db())
    ids = []
    async with aiosqlite.connect(str(_db())) as c:
        for i in range(n):
            iid = f"{prefix}-{status}-{i}"
            await c.execute(
                "INSERT INTO hive_instances (id, name, account_ref, region, instance_type, status, "
                "ec2_instance_id, created_at, updated_at) VALUES (?, ?, ?, 'us-east-1', "
                "'m7g.xlarge', ?, ?, 't', 't')", (iid, iid, ref, status, f"{ec2}{i}" if ec2 else None))
            ids.append(iid)
        await c.commit()
    return ids


async def _rows(where="1=1"):
    async with aiosqlite.connect(str(_db())) as c:
        return [r[0] for r in await (await c.execute(f"SELECT id FROM hive_instances WHERE {where}")).fetchall()]


@pytest.mark.asyncio
async def test_raising_creates_exactly_the_difference(client, _no_real_deploys):
    await _seed(1)
    r = await client.put("/api/aws-account/hive-fleet", json={"desired_count": 3}, headers=UA)
    assert r.status_code == 200 and r.json()["hive"]["state"] == "ready"
    assert len(await _rows("status = 'pending'")) == 2
    assert _no_real_deploys.deploy.await_count == 2
    await client.put("/api/aws-account/hive-fleet", json={"desired_count": 3}, headers=UA)
    assert len(await _rows("status = 'pending'")) == 2           # repeated: nothing more


@pytest.mark.asyncio
async def test_concurrent_raises_create_only_the_difference(client, monkeypatch):
    await _seed(1)
    real = ha.counted

    async def slow(conn, **kw):
        n = await real(conn, **kw)
        await asyncio.sleep(0.05)
        return n
    import hive.accounts as mod
    monkeypatch.setattr(mod, "counted", slow)
    await asyncio.gather(*(client.put("/api/aws-account/hive-fleet", json={"desired_count": 4},
                                      headers=UA) for _ in range(4)))
    assert len(await _rows("status = 'pending'")) == 3


@pytest.mark.asyncio
async def test_never_above_eight_and_lowering_creates_nothing(client):
    await _seed(2)
    r = await client.put("/api/aws-account/hive-fleet", json={"desired_count": 9}, headers=UA)
    assert r.status_code == 409
    assert (await client.put("/api/aws-account/hive-fleet", json={"desired_count": 1},
                             headers=UA)).json()["hive"] | {} and len(await _rows()) == 2
    st = await fleet.status(_db())
    assert st["state"] == "over" and st["over"] == 1
    await _seed(7, prefix="o", ref="swarmai")                    # 9 already (over the limit)
    r = await client.put("/api/aws-account/hive-fleet", json={"desired_count": 8}, headers=UA)
    assert r.status_code == 200 and len(await _rows("status = 'pending'")) == 0
    r = await client.put("/api/aws-account/hive-fleet", json={"desired_count": 8}, headers=UA)
    assert len(await _rows("status = 'pending'")) == 0


@pytest.mark.asyncio
async def test_hives_of_another_account_count_toward_eight(client):
    await _seed(6, ref="kept-old", prefix="k")                   # kept from the previous account
    await _seed(1)
    r = await client.put("/api/aws-account/hive-fleet", json={"desired_count": 3}, headers=UA)
    assert r.status_code == 409                                   # 7 + 2 would be 9
    assert len(await _rows("status = 'pending'")) == 0
    r = await client.put("/api/aws-account/hive-fleet", json={"desired_count": 2}, headers=UA)
    assert r.status_code == 200 and len(await _rows("status = 'pending'")) == 1


@pytest.mark.asyncio
async def test_the_route_needs_the_app_header(client):
    r = await client.put("/api/aws-account/hive-fleet", json={"desired_count": 2})
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_status_and_the_upgrade_default(client, monkeypatch):
    assert (await fleet.status(_db()))["state"] == "short"         # unset, no Hive: 1 short
    assert len(await _rows()) == 0                                  # nothing created for them
    await _seed(2)
    st = await fleet.status(_db())
    assert (st["state"], st["desired"], st["monthly_usd"]) == ("ready", 2, 238)
    await _seed(1, status="error", ec2=None, prefix="f")           # a failed create: not counted
    st = await fleet.status(_db())
    assert st["counted"] == 2
    # one line per counted Hive on the account page; the failed create is not one
    assert [(h["name"], h["status"], h["region"]) for h in st["hives"]] == [
        ("s-running-0", "running", "us-east-1"), ("s-running-1", "running", "us-east-1")]
    from core import aws_account
    await fleet.set_defaults()
    aws_account._cfg().update({"hive_fleet": {"desired_count": 4}})
    monkeypatch.setattr(aws_account, "get_record", lambda: {"capabilities": {
        "hive": {"status": "missing", "detail": "missing ec2:RunInstances"}}})
    st = await fleet.status(_db())
    assert st["state"] == "cannot_build" and st["short"] == 2
    monkeypatch.setattr(ha, "swarmai_account", lambda: None)
    assert (await fleet.status(_db()))["state"] == "unavailable"


@pytest.mark.asyncio
async def test_the_last_hive_is_replaced_never_deleted(client, _no_real_deploys, monkeypatch):
    (only,) = await _seed(1)
    r = await client.delete(f"/api/hive/instances/{only}")
    assert r.status_code == 409 and "replace" in r.json()["detail"]
    r = await client.post(f"/api/hive/instances/{only}/replace")
    assert r.status_code == 200
    new = r.json()["new"]
    assert (await fleet.status(_db()))["counted"] == 1             # a pair counts as one
    assert len(await _rows()) == 2
    # the new one runs: reconcile deletes the old one
    async with aiosqlite.connect(str(_db())) as c:
        await c.execute("UPDATE hive_instances SET status='running', ec2_instance_id='i-new' WHERE id=?", (new,))
        await c.commit()
    cleaned = []

    async def cleanup(iid):
        cleaned.append(iid)
    monkeypatch.setattr(fleet, "_ec2_states", lambda session, region, ids: {i: "running" for i in ids})
    out = await fleet.reconcile(_db(), cleanup=cleanup)
    assert out["replaced"] == [only] and cleaned == [only]
    assert fleet.get_config().get("replacing") == {}


@pytest.mark.asyncio
async def test_a_failed_replacement_keeps_the_old_hive(client, monkeypatch):
    (only,) = await _seed(1)
    new = (await client.post(f"/api/hive/instances/{only}/replace")).json()["new"]
    async with aiosqlite.connect(str(_db())) as c:
        await c.execute("UPDATE hive_instances SET status='error' WHERE id=?", (new,))
        await c.commit()
    monkeypatch.setattr(fleet, "_ec2_states", lambda session, region, ids: {i: "running" for i in ids})
    cleaned = []

    async def cleanup(iid):
        cleaned.append(iid)
    out = await fleet.reconcile(_db(), cleanup=cleanup)
    assert out["replaced"] == [] and cleaned == []
    assert fleet.get_config().get("replacing") == {}


@pytest.mark.asyncio
async def test_reconcile_follows_ec2_and_creates_nothing(client, monkeypatch):
    ids = await _seed(3)
    monkeypatch.setattr(ha, "session_for", lambda row, region, purpose: "S")
    monkeypatch.setattr(fleet, "_ec2_states", lambda session, region, eids: {
        eids[0]: "gone", eids[1]: "terminated", eids[2]: "running"})
    out = await fleet.reconcile(_db(), cleanup=AsyncMock())
    assert sorted(out["gone"]) == sorted(ids[:2])
    st = await fleet.status(_db())
    assert st["counted"] == 1 and len(await _rows("status = 'pending'")) == 0
    # a lookup that fails: everything stays as it is
    ids2 = await _seed(1, prefix="z")

    def boom(*a):
        raise RuntimeError("offline")
    monkeypatch.setattr(fleet, "_ec2_states", boom)
    assert (await fleet.reconcile(_db(), cleanup=AsyncMock()))["gone"] == []
    assert ids2[0] in await _rows("status = 'running'")


def _prov_session(captured, *, boundary):
    class C:
        def __getattr__(self, name):
            def call(**kw):
                captured.setdefault(name, []).append(kw)
                if name == "get_policy" and not boundary:
                    raise RuntimeError("NoSuchEntity")
                if name == "get_caller_identity":
                    return {"Account": ACCT}
                if name == "create_role":
                    return {"Role": {"Arn": "arn:aws:iam::1:role/SwarmAI-Hive-x"}}
                if name == "create_security_group":
                    return {"GroupId": "sg-1"}
                if name == "describe_vpcs":
                    return {"Vpcs": [{"VpcId": "vpc-1"}]}
                if name == "describe_managed_prefix_lists":
                    return {"PrefixLists": [{"PrefixListId": "pl-1"}]}
                if name == "allocate_address":
                    return {"AllocationId": "a", "PublicIp": "1.2.3.4"}
                return {}
            return call
    s = MagicMock()
    s.client = lambda *a, **k: C()
    return s


@pytest.mark.asyncio
@pytest.mark.parametrize("boundary", [True, False])
async def test_everything_is_tagged_while_created(tmp_path, boundary):
    from hive.provisioner import HiveProvisioner
    p = HiveProvisioner(tmp_path / "x.db")
    cap: dict = {}
    sess = _prov_session(cap, boundary=boundary)
    await p._create_iam_role(sess, "x")
    role = cap["create_role"][0]
    assert {"Key": "swarmai:managed", "Value": "true"} in role["Tags"]
    assert ("PermissionsBoundary" in role) is boundary
    await p._create_security_group(sess, "x", "us-east-1")
    sg = cap["create_security_group"][0]
    assert {"Key": "swarmai:managed", "Value": "true"} in sg["TagSpecifications"][0]["Tags"]
    assert "create_tags" not in cap                               # never tagged afterwards
    await p._allocate_elastic_ip(sess, "i-1", "x", "us-east-1")
    eip = cap["allocate_address"][0]
    assert {"Key": "swarmai:managed", "Value": "true"} in eip["TagSpecifications"][0]["Tags"]


@pytest.mark.asyncio
async def test_a_refused_raise_changes_no_default(client):
    await _seed(8, prefix="full")
    fleet._save_config({"instance_type": "m7g.xlarge", "region": "us-east-1"})
    r = await client.put("/api/aws-account/hive-fleet", json={
        "desired_count": 9, "instance_type": "t4g.large", "region": "eu-west-1"}, headers=UA)
    assert r.status_code == 409
    assert fleet.get_config() == {"instance_type": "m7g.xlarge", "region": "us-east-1"}


@pytest.mark.asyncio
async def test_a_replacement_written_meanwhile_is_never_lost(client, monkeypatch):
    await _seed(1)
    monkeypatch.setattr(ha, "fleet_lock", asyncio.Lock())         # this test's own event loop
    async with ha.fleet_lock:                                     # set_desired waits here...
        task = asyncio.create_task(fleet.set_desired(_db(), 1, start_deploy=lambda iid: None))
        await asyncio.sleep(0.05)
        fleet._save_config({**fleet.get_config(), "replacing": {"new": "old"}})   # ...replace() ran
    await task
    assert fleet.get_config()["replacing"] == {"new": "old"}


@pytest.mark.asyncio
async def test_reconcile_settles_only_its_pairs(client, monkeypatch):
    (a, b) = await _seed(2)
    (new_a,) = await _seed(1, status="running", ec2="i-na", prefix="na")
    (new_b,) = await _seed(1, status="pending", ec2=None, prefix="nb")
    fleet._save_config({"replacing": {new_a: a, new_b: b}})
    monkeypatch.setattr(fleet, "_ec2_states", lambda session, region, ids: {i: "running" for i in ids})
    out = await fleet.reconcile(_db(), cleanup=AsyncMock())
    assert out["replaced"] == [a]
    assert fleet.get_config()["replacing"] == {new_b: b}          # still building: kept


@pytest.mark.asyncio
async def test_a_hive_relaunched_meanwhile_is_not_marked_gone(client, monkeypatch):
    (iid,) = await _seed(1, status="error", ec2="i-old")
    monkeypatch.setattr(ha, "session_for", lambda row, region, purpose: "S")

    def states(session, region, ids):
        import sqlite3
        with sqlite3.connect(str(_db())) as c:                    # a retry launched a new one
            c.execute("UPDATE hive_instances SET ec2_instance_id='i-new', status='running' WHERE id=?", (iid,))
        return {i: "terminated" for i in ids}
    monkeypatch.setattr(fleet, "_ec2_states", states)
    assert (await fleet.reconcile(_db(), cleanup=AsyncMock()))["gone"] == []
    async with aiosqlite.connect(str(_db())) as c:
        row = await (await c.execute("SELECT status, ec2_instance_id FROM hive_instances WHERE id=?", (iid,))).fetchone()
    assert tuple(row) == ("running", "i-new")


@pytest.mark.asyncio
async def test_the_launch_tags_everything_it_creates(tmp_path):
    """The template allows RunInstances only when every created resource is tagged."""
    from hive.provisioner import HiveProvisioner
    p = HiveProvisioner(tmp_path / "x.db")
    sess, ec2 = MagicMock(), MagicMock()
    sess.client.return_value = ec2
    ec2.run_instances.return_value = {"Instances": [{"InstanceId": "i-new"}]}
    await p._launch_ec2(sess, "x", "arn:profile", "sg-1", "#!/bin/bash", "m7g.xlarge",
                        "us-east-1", "2.0.1", custom_ami_id="ami-0868a14e13f356561")
    specs = ec2.run_instances.call_args[1]["TagSpecifications"]
    assert {s["ResourceType"] for s in specs} == {"instance", "volume", "network-interface"}
    assert all({"Key": "swarmai:managed", "Value": "true"} in s["Tags"] for s in specs)
