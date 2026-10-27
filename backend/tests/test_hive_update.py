"""Updating a Hive from the desktop app (the Hive card's Update button).

Questions:
- does a failed update leave the Hive 'running' with the reason, never 'error' (an
  errored Hive is offered Retry, which deletes and recreates it), whether it fails before
  the SSM command (release not available, checksum) or with an unexpected exception;
- does a successful update clear an earlier error message;
- does the update route default to the latest published release, claim the Hive before
  returning (so the UI sees 'updating' at once), run the update in the background, and
  answer 409 / 502 / 400 without starting anything when it cannot;
- does /latest-version answer the release and refuse on a Hive.
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

INSTANCE = {"id": "inst-1", "name": "test-hive", "account_ref": "acc-1", "region": "us-east-1",
            "ec2_instance_id": "i-abc123", "s3_bucket": "swarmai-hive-test"}
ACCOUNT = {"auth_method": "access_keys", "auth_config": '{"access_key_id": "AK", "secret_access_key": "SK"}'}


def _run_update(sync_side_effect=None, ssm=("Success", "ok"), healthy=True, ssm_error=None):
    p = HiveProvisioner(Path("/tmp/test.db"))
    updates = AsyncMock()

    async def fake_to_thread(fn, *args, **kwargs):
        if getattr(fn, "__name__", "") == "_run_command" and ssm_error is not None:
            ssm_client = MagicMock()
            ssm_client.send_command.side_effect = ssm_error  # raised after the command left
            session.client.return_value = ssm_client
            return fn()
        return ssm

    session = MagicMock()

    with patch.object(p, "_get_instance", new_callable=AsyncMock, return_value=INSTANCE), \
         patch.object(p, "_get_account", new_callable=AsyncMock, return_value=ACCOUNT), \
         patch.object(p, "_update_instance", updates), \
         patch.object(p, "_sync_release_to_s3", new_callable=AsyncMock, side_effect=sync_side_effect), \
         patch.object(p, "_wait_healthy_via_ssm", new_callable=AsyncMock, return_value=healthy), \
         patch.object(p, "_get_session", return_value=session), \
         patch("hive.provisioner.asyncio.to_thread", side_effect=fake_to_thread):
        try:
            asyncio.run(p.update("inst-1", "9.9.9", claimed=True))
            raised = None
        except BaseException as e:  # noqa: BLE001 — the test inspects what escaped
            raised = e
    return updates, raised


@pytest.mark.parametrize("error", [RuntimeError("does not match its published checksum"),
                                   ValueError("something unexpected")])
def test_a_failed_update_leaves_the_hive_running_with_the_reason(error):
    updates, raised = _run_update(sync_side_effect=error)
    assert raised is error
    kwargs = updates.await_args.kwargs
    assert kwargs["status"] == "running"
    assert "was not changed and still runs its previous version" in kwargs["error_message"]
    assert str(error) in kwargs["error_message"]
    assert all(c.kwargs.get("status") != "error" for c in updates.await_args_list)


def test_a_failure_after_the_command_was_sent_does_not_claim_the_old_version():
    updates, raised = _run_update(ssm_error=RuntimeError("connection reset"))
    assert isinstance(raised, RuntimeError)
    msg = updates.await_args.kwargs["error_message"]
    assert updates.await_args.kwargs["status"] == "running"
    assert "may be on v9.9.9 or its previous version" in msg and "still runs" not in msg


def test_a_successful_update_clears_an_earlier_error():
    updates, raised = _run_update()
    assert raised is None
    assert updates.await_args.kwargs == {"version": "9.9.9", "status": "running", "error_message": None}


# ── routes ──

class FakeProvisioner:
    def __init__(self, latest="2.4.0", claim_error=None, resolve_error=None):
        self.latest, self.claim_error, self.resolve_error = latest, claim_error, resolve_error
        self.claimed, self.updated = [], []

    async def _resolve_version(self, version):
        if self.resolve_error:
            raise self.resolve_error
        return version or self.latest

    async def claim_update(self, instance_id):
        if self.claim_error:
            raise self.claim_error
        self.claimed.append(instance_id)

    async def update(self, instance_id, version, claimed=False):
        self.updated.append((instance_id, version, claimed))


@pytest.fixture
def client(monkeypatch, tmp_path):
    from routers import hive as hive_router

    db = tmp_path / "h.db"

    async def make():
        async with aiosqlite.connect(db) as c:
            await c.execute("CREATE TABLE hive_instances (id TEXT, status TEXT)")
            await c.execute("INSERT INTO hive_instances VALUES ('inst-1', 'running'), ('inst-2', 'stopped')")
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
    return TestClient(app), fake


def test_update_defaults_to_the_latest_release_and_claims_before_returning(client):
    c, fake = client
    r = c.post("/api/hive/instances/inst-1/update")
    assert r.status_code == 200 and r.json() == {"status": "updating", "version": "2.4.0"}
    assert fake.claimed == ["inst-1"]
    assert fake.updated == [("inst-1", "2.4.0", True)]


def test_update_takes_an_explicit_version(client):
    c, fake = client
    r = c.post("/api/hive/instances/inst-1/update", json={"version": "2.3.1"})
    assert r.json()["version"] == "2.3.1" and fake.updated == [("inst-1", "2.3.1", True)]


def test_update_refuses_without_starting_anything(client):
    c, fake = client
    assert c.post("/api/hive/instances/inst-2/update").status_code == 400  # not running
    fake.claim_error = RuntimeError("not in 'running' state")
    assert c.post("/api/hive/instances/inst-1/update").status_code == 409
    fake.claim_error, fake.resolve_error = None, RuntimeError("github down")
    r = c.post("/api/hive/instances/inst-1/update")
    assert r.status_code == 502 and "github" not in r.text
    fake.resolve_error, fake.latest = None, "v1;rm -rf"
    assert c.post("/api/hive/instances/inst-1/update").status_code == 502
    assert fake.updated == [] and fake.claimed == []


def test_a_version_with_a_trailing_newline_is_refused(client):
    c, fake = client
    assert c.post("/api/hive/instances/inst-1/update", json={"version": "2.4.0\n"}).status_code == 422
    fake.latest = "2.4.0\n"
    assert c.post("/api/hive/instances/inst-1/update").status_code == 502
    assert fake.updated == []


def test_listing_ends_an_update_swarmai_stopped_in_the_middle_of(tmp_path):
    async def go():
        db = tmp_path / "s.db"
        async with aiosqlite.connect(db) as c:
            await c.execute("CREATE TABLE hive_instances (id TEXT, status TEXT, error_message TEXT, updated_at TEXT)")
            await c.execute("INSERT INTO hive_instances VALUES "
                            "('old', 'updating', NULL, datetime('now', '-20 minutes')), "
                            "('new', 'updating', NULL, datetime('now', '-2 minutes')), "
                            "('run', 'running', NULL, datetime('now', '-1 day'))")
            await c.commit()
        n = await HiveProvisioner(db).release_stale_updates()
        async with aiosqlite.connect(db) as c:
            rows = {r[0]: (r[1], r[2]) for r in await (await c.execute(
                "SELECT id, status, error_message FROM hive_instances")).fetchall()}
        return n, rows
    n, rows = asyncio.run(go())
    assert n == 1
    assert rows["old"][0] == "running" and "interrupted" in rows["old"][1]
    assert rows["new"] == ("updating", None)  # an update may still be working on it
    assert rows["run"] == ("running", None)


def test_latest_version_route(client, monkeypatch):
    c, fake = client
    assert c.get("/api/hive/latest-version").json() == {"version": "2.4.0"}
    fake.resolve_error = RuntimeError("down")
    assert c.get("/api/hive/latest-version").status_code == 502
    monkeypatch.setenv("SWARMAI_MODE", "hive")
    assert c.get("/api/hive/latest-version").status_code == 403
    assert c.post("/api/hive/instances/inst-1/update").status_code == 403
