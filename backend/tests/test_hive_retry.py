"""Retry on an errored Hive never deletes its data unasked or without a backup.

Questions:
- when the Hive's EC2 instance still exists, is Retry refused (409, nothing changed)
  without confirm_data_loss;
- with it, are all its disks snapshotted (and the snapshots complete) before anything
  is deleted, and is the redeploy run afterwards;
- if the snapshot fails, is nothing deleted, and does the Hive end 'error' saying so;
- with no instance left (a failed fresh deploy), does Retry clean up and redeploy as
  before, with no confirmation and no snapshot;
- does instance_exists treat a failed lookup as present (protect the data), and a
  terminated or unknown instance as gone;
- does snapshot_volumes snapshot every volume and wait for completion.
"""
import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import aiosqlite
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hive.provisioner import HiveProvisioner


class FakeProvisioner:
    def __init__(self, exists=True, snapshot_error=None, terminate_fails=False):
        self.exists, self.snapshot_error, self.terminate_fails = exists, snapshot_error, terminate_fails
        self.calls = []

    async def instance_exists(self, instance_id):
        return self.exists

    async def snapshot_volumes(self, instance_id, purpose):
        self.calls.append(("snapshot", instance_id))
        if self.snapshot_error:
            raise self.snapshot_error
        return ["snap-1"]

    async def cleanup(self, instance_id):
        self.calls.append(("cleanup", instance_id))
        if not self.terminate_fails:
            self.exists = False

    async def deploy(self, instance_id):
        self.calls.append(("deploy", instance_id))


@pytest.fixture
def env(monkeypatch, tmp_path):
    from routers import hive as hive_router

    db = tmp_path / "h.db"

    async def make():
        async with aiosqlite.connect(db) as c:
            await c.execute("CREATE TABLE hive_instances (id TEXT, status TEXT, error_message TEXT, "
                            "version TEXT, ec2_instance_id TEXT, ec2_public_ip TEXT, elastic_ip_alloc_id TEXT, "
                            "security_group_id TEXT, iam_role_name TEXT, iam_instance_profile_arn TEXT, "
                            "cloudfront_dist_id TEXT, cloudfront_domain TEXT, s3_bucket TEXT, auth_user TEXT, "
                            "auth_password TEXT, updated_at TEXT)")
            await c.execute("INSERT INTO hive_instances (id, status, error_message, ec2_instance_id) "
                            "VALUES ('h1', 'error', 'Start failed', 'i-1')")
            await c.commit()
    asyncio.run(make())

    @asynccontextmanager
    async def conn():
        c = await aiosqlite.connect(db)
        c.row_factory = aiosqlite.Row
        try:
            yield c
        finally:
            await c.close()

    fake = FakeProvisioner()
    monkeypatch.setattr(hive_router, "_conn", conn)
    monkeypatch.setattr(hive_router, "_get_provisioner", lambda: fake)
    monkeypatch.delenv("SWARMAI_MODE", raising=False)
    app = FastAPI()
    app.include_router(hive_router.router, prefix="/api/hive")

    def row():
        async def get():
            async with aiosqlite.connect(db) as c:
                return await (await c.execute(
                    "SELECT status, error_message FROM hive_instances WHERE id='h1'")).fetchone()
        return asyncio.run(get())
    with TestClient(app) as client:  # one event loop for the test: background tasks keep running
        yield client, fake, row


def _settle():
    """Wait for the retry's background task (it runs on the TestClient's loop)."""
    import time
    from routers import hive as hive_router
    for _ in range(250):
        if not hive_router._BACKGROUND:
            return
        time.sleep(0.02)
    raise AssertionError("background retry did not finish")


def test_retry_with_a_live_instance_needs_confirmation(env):
    c, fake, row = env
    r = c.post("/api/hive/instances/h1/retry")
    assert r.status_code == 409 and "Start" in r.json()["detail"]
    assert c.post("/api/hive/instances/h1/retry", json={"version": "2.4.0"}).status_code == 409
    assert fake.calls == [] and row() == ("error", "Start failed")


def test_confirmed_retry_snapshots_before_deleting(env):
    c, fake, row = env
    r = c.post("/api/hive/instances/h1/retry", json={"confirm_data_loss": True})
    assert r.status_code == 200 and r.json() == {"status": "retrying"}
    _settle()
    assert fake.calls == [("snapshot", "h1"), ("cleanup", "h1"), ("deploy", "h1")]


def test_the_record_says_how_far_it_got(env):
    c, fake, row = env
    seen = []
    orig = fake.cleanup

    async def cleanup(instance_id):
        from routers import hive as hive_router
        async with hive_router._conn() as conn:  # the row as cleanup starts
            seen.append(tuple(await (await conn.execute(
                "SELECT status, error_message FROM hive_instances WHERE id='h1'")).fetchone()))
        await orig(instance_id)
    fake.cleanup = cleanup
    c.post("/api/hive/instances/h1/retry", json={"confirm_data_loss": True})
    _settle()
    assert seen[0][0] == "deleting" and "snap-1" in seen[0][1]


def test_an_instance_that_survives_cleanup_is_not_forgotten(env):
    c, fake, row = env
    fake.terminate_fails = True
    c.post("/api/hive/instances/h1/retry", json={"confirm_data_loss": True})
    _settle()
    assert fake.calls == [("snapshot", "h1"), ("cleanup", "h1")]  # no redeploy
    status, msg = row()
    assert status == "error" and "could not be deleted" in msg and "snap-1" in msg


def test_a_failed_snapshot_deletes_nothing(env):
    c, fake, row = env
    fake.snapshot_error = RuntimeError("UnauthorizedOperation: ec2:CreateSnapshot")
    r = c.post("/api/hive/instances/h1/retry", json={"confirm_data_loss": True})
    assert r.status_code == 200
    _settle()
    assert fake.calls == [("snapshot", "h1")]
    status, msg = row()
    assert status == "error" and "nothing was deleted" in msg and "CreateSnapshot" in msg


def test_retry_without_an_instance_left_redeploys_as_before(env):
    c, fake, row = env
    fake.exists = False
    r = c.post("/api/hive/instances/h1/retry")
    assert r.status_code == 200
    _settle()
    assert fake.calls == [("cleanup", "h1"), ("deploy", "h1")]


# ── provisioner ──

INSTANCE = {"id": "h1", "name": "hive-a", "account_ref": "a1", "region": "us-east-1", "ec2_instance_id": "i-1"}


def _with_ec2(ec2):
    p = HiveProvisioner(Path("/tmp/x.db"))
    session = MagicMock()
    session.client.return_value = ec2
    return p, [patch.object(p, "_get_instance", new_callable=AsyncMock, return_value=INSTANCE),
               patch.object(p, "_get_account", new_callable=AsyncMock, return_value={}),
               patch.object(p, "_get_session", return_value=session)]


def _run(p, patches, coro_fn):
    for x in patches:
        x.start()
    try:
        return asyncio.run(coro_fn(p))
    finally:
        for x in patches:
            x.stop()


@pytest.mark.parametrize("describe,expected", [
    ({"Reservations": [{"Instances": [{"State": {"Name": "stopped"}}]}]}, True),
    ({"Reservations": []}, True),  # an empty answer is not proof the instance is gone
    ({"Reservations": [{"Instances": [{"State": {"Name": "terminated"}}]}]}, False),
    (Exception("An error occurred (InvalidInstanceID.NotFound)"), False),
    (Exception("An error occurred (UnauthorizedOperation)"), True),
])
def test_instance_exists(describe, expected):
    ec2 = MagicMock()
    if isinstance(describe, Exception):
        ec2.describe_instances.side_effect = describe
    else:
        ec2.describe_instances.return_value = describe
    p, patches = _with_ec2(ec2)
    assert _run(p, patches, lambda p: p.instance_exists("h1")) is expected


def test_snapshot_volumes_snapshots_every_disk_and_waits():
    ec2 = MagicMock()
    ec2.describe_instances.return_value = {"Reservations": [{"Instances": [{"BlockDeviceMappings": [
        {"Ebs": {"VolumeId": "vol-a"}}, {"Ebs": {"VolumeId": "vol-b"}}]}]}]}
    ec2.create_snapshot.side_effect = [{"SnapshotId": "snap-a"}, {"SnapshotId": "snap-b"}]
    p, patches = _with_ec2(ec2)
    assert _run(p, patches, lambda p: p.snapshot_volumes("h1", "before retry")) == ["snap-a", "snap-b"]
    assert [c.kwargs["VolumeId"] for c in ec2.create_snapshot.call_args_list] == ["vol-a", "vol-b"]
    ec2.get_waiter.assert_called_once_with("snapshot_completed")
    assert ec2.get_waiter.return_value.wait.call_args.kwargs["SnapshotIds"] == ["snap-a", "snap-b"]


def test_snapshot_volumes_refuses_an_instance_without_volumes():
    ec2 = MagicMock()
    ec2.describe_instances.return_value = {"Reservations": [{"Instances": [{"BlockDeviceMappings": []}]}]}
    p, patches = _with_ec2(ec2)
    with pytest.raises(RuntimeError, match="no EBS volume"):
        _run(p, patches, lambda p: p.snapshot_volumes("h1", "before retry"))


def test_a_retry_swarmai_stopped_in_the_middle_of_ends_with_its_last_step(tmp_path):
    async def go():
        db = tmp_path / "s.db"
        async with aiosqlite.connect(db) as c:
            await c.execute("CREATE TABLE hive_instances (id TEXT, status TEXT, error_message TEXT, updated_at TEXT)")
            await c.execute("INSERT INTO hive_instances VALUES "
                            "('old', 'deleting', 'Disks saved as snapshots snap-9; deleting the old instance...', "
                            "datetime('now', '-2 hours')), "
                            "('new', 'deleting', 'Saving...', datetime('now', '-10 minutes'))")
            await c.commit()
        n = await HiveProvisioner(db).release_stale_updates()
        async with aiosqlite.connect(db) as c:
            rows = {r[0]: (r[1], r[2]) for r in await (await c.execute(
                "SELECT id, status, error_message FROM hive_instances")).fetchall()}
        return n, rows
    n, rows = asyncio.run(go())
    assert n == 1
    assert rows["old"][0] == "error"
    assert rows["old"][1].startswith("Interrupted") and "snap-9" in rows["old"][1]
    assert rows["new"] == ("deleting", "Saving...")


@pytest.mark.parametrize("present,expect_cleared", [(False, True), (True, False)])
def test_a_failed_deploy_forgets_an_instance_only_once_it_is_gone(present, expect_cleared):
    p = HiveProvisioner(Path("/tmp/x.db"))
    with patch.object(HiveProvisioner, "_ec2_present", staticmethod(lambda s, r, i: present)), \
         patch.object(p, "_update_instance", new_callable=AsyncMock) as upd:
        asyncio.run(p._forget_terminated_instance(MagicMock(), "us-east-1", "h1", "i-1"))
    if expect_cleared:
        upd.assert_awaited_once_with("h1", ec2_instance_id=None, ec2_public_ip=None)
    else:
        upd.assert_not_awaited()
