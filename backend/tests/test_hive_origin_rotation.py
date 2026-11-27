"""Rotating a Hive's origin secret, and keeping it out of backups (HiveProvisioner
rotate_origin_secret, POST /api/hive/instances/{id}/origin-secret/rotate, the owner agent's
op, core/git_sync_engine's db snapshot).

Questions answered:
1. does a rotation take the guard off in the order that never locks the Hive out (Caddy rule,
   then header) and lock it again with a fresh value (header, Deployed, then Caddy), holding
   the update gate and ending 'running'; does the old value then get 403 and the new one reach
   basic auth?
2. does a failed step end 'running' with the reason and raise; does a Hive without a
   distribution, or one that is not running, change nothing?
3. does the route claim and start the rotation in the background, refuse (404 / 400 / 409)
   without starting anything, and may the owner's agent call it?
4. does a backup leave the origin secret out while the live store keeps it, and does a
   restored Host (no secret stored) lock the origin again with the value its distribution
   still sends, so the Hive is not locked out?
Everything runs against fakes: no AWS call is made.
"""
from __future__ import annotations

import gzip
import sqlite3
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import aiosqlite
import pytest
from httpx import ASGITransport, AsyncClient

from hive import accounts as ha
from hive.provisioner import _set_origin_header
from tests.test_hive_origin_secret import (
    FakeCloudFront, ScriptRunningSSM, _caddyfile_of, _db_conn, _existing_hive_provisioner,
    _first, _pre_secret_dist_config, _render, _stored_secrets,
)

OLD = "o" * 43
SECOND = "s" * 43
INSTANCE = {"id": "inst-1", "name": "demo", "account_ref": "acc-1", "region": "us-east-1",
            "ec2_instance_id": "i-1", "s3_bucket": "swarmai-hive-123456789012-us-east-1",
            "cloudfront_dist_id": "EDIST"}


def _answer(caddyfile: Path, sent: str | None) -> str:
    """What the Caddyfile on disk answers a request carrying *sent* (ScriptRunningSSM's rule)."""
    import re
    m = re.search(r"not header X-Swarm-Origin (\S+)", caddyfile.read_text())
    return "401" if m is None or sent == m.group(1) else "403"


async def _locked_hive(tmp_path, events):
    """A Hive locked with OLD: its distribution sends OLD and its Caddyfile checks OLD."""
    caddyfile = tmp_path / "Caddyfile"
    caddyfile.write_text(_caddyfile_of(_render(OLD)))
    config = await _pre_secret_dist_config()
    _set_origin_header(config, OLD)
    cf = FakeCloudFront(events, config)
    ssm = ScriptRunningSSM(events, tmp_path, caddyfile)
    return caddyfile, cf, ssm


def _stateful(p, instance: dict) -> dict:
    """_get_instance reads what _update_instance wrote (the stored secret, the status)."""
    row = dict(instance)
    writes = p._update_instance.side_effect

    async def update(_id, **kw):
        row.update(kw)
        writes(_id, **kw)
    p._update_instance = AsyncMock(side_effect=update)
    p._get_instance = AsyncMock(side_effect=lambda _id: dict(row))
    return row


async def test_rotation_removes_then_locks_with_a_new_value(tmp_path):
    events: list = []
    caddyfile, cf, ssm = await _locked_hive(tmp_path, events)
    p = _existing_hive_provisioner(events, cf, ssm, {**INSTANCE, "origin_secret": OLD})
    row = _stateful(p, {**INSTANCE, "origin_secret": OLD})
    with patch("hive.provisioner.aiosqlite.connect", return_value=_db_conn()) as connect, \
         patch("hive.provisioner.asyncio.sleep", new=AsyncMock()):
        await p.rotate_origin_secret("inst-1")
    assert connect.called, "the rotation claims the running → updating gate"
    new = row["origin_secret"]
    assert new and new != OLD
    assert cf.header_value() == new
    # the guard came off rule-first, then went back on header-first, Deployed, then rule
    removes, applies = _first(events, "ssm.origin_remove"), _first(events, "ssm.origin_apply")
    updates = [i for i, e in enumerate(events) if e == "cf.update_distribution"]
    assert removes < updates[0] < updates[1] < applies, events
    assert "cf.get_distribution:Deployed" in events[updates[1]:applies]
    assert _stored_secrets(p._update_instance) == [None, new]
    # the leaked value no longer opens the origin; the new one reaches basic auth
    assert _answer(caddyfile, OLD) == "403"
    assert _answer(caddyfile, new) == "401"
    assert caddyfile.read_text() == _caddyfile_of(_render(new))
    assert row["status"] == "running" and row["error_message"] is None


@pytest.mark.parametrize("stored", [OLD, None])          # None: a Host restored from a backup
async def test_a_failed_step_ends_running_with_the_reason(tmp_path, stored):
    events: list = []
    caddyfile, cf, ssm = await _locked_hive(tmp_path, events)
    p = _existing_hive_provisioner(events, cf, ssm, {**INSTANCE, "origin_secret": stored})
    row = _stateful(p, {**INSTANCE, "origin_secret": stored})
    ssm.fail_flag.write_text("1")        # caddy validate fails: the first Caddy edit is refused
    with patch("hive.provisioner.aiosqlite.connect", return_value=_db_conn()), \
         patch("hive.provisioner.asyncio.sleep", new=AsyncMock()):
        with pytest.raises(RuntimeError):
            await p.rotate_origin_secret("inst-1")
    assert row["status"] == "running"
    assert "Rotating the origin secret failed" in row["error_message"]
    assert "keeps its old secret; rotate again" in row["error_message"]
    # nothing was changed on the way: the Hive still checks and is sent the old value, and
    # a removal that put the Caddyfile back is not followed by a relock
    assert cf.header_value() == OLD and _answer(caddyfile, OLD) == "401"
    assert "cf.update_distribution" not in events
    assert events.count("ssm.origin_apply") == 0


async def test_rotation_replaces_the_value_the_distribution_sends_when_none_is_stored(tmp_path):
    # a Host restored from a backup (or a retry after a half-failed rotation) stores no
    # secret while the Hive still checks OLD and its distribution still sends OLD: the
    # rotation must still end on a new value, never re-lock with OLD and report success
    events: list = []
    caddyfile, cf, ssm = await _locked_hive(tmp_path, events)
    p = _existing_hive_provisioner(events, cf, ssm, dict(INSTANCE))
    row = _stateful(p, dict(INSTANCE))                                   # no origin_secret
    with patch("hive.provisioner.aiosqlite.connect", return_value=_db_conn()), \
         patch("hive.provisioner.asyncio.sleep", new=AsyncMock()):
        await p.rotate_origin_secret("inst-1")
    new = row["origin_secret"]
    assert new and new != OLD and cf.header_value() == new
    assert _answer(caddyfile, OLD) == "403" and _answer(caddyfile, new) == "401"
    assert row["error_message"] is None
    # and never locked out on the way: the guard came off before the header changed
    removes = _first(events, "ssm.origin_remove")
    updates = [i for i, e in enumerate(events) if e == "cf.update_distribution"]
    assert removes < updates[0], events


class _ApplyFails(ScriptRunningSSM):
    """The Caddy edit that adds the guard fails its first *n* times (the remove one runs)."""

    def __init__(self, *a, n: int):
        super().__init__(*a)
        self.left = n

    def send_command(self, InstanceIds, DocumentName, Parameters, TimeoutSeconds, Comment):
        if "origin guard: apply" in Comment and self.left:
            self.left -= 1
            self.events.append("ssm.origin_apply:failed")
            cid = f"cmd-{len(self.results) + 1}"
            self.results[cid] = ("Failed", "validate failed")
            return {"Command": {"CommandId": cid}}
        return super().send_command(InstanceIds, DocumentName, Parameters, TimeoutSeconds, Comment)


@pytest.mark.parametrize("fails, note", [
    (1, "locked again with the value its distribution sends now"),
    (2, "no secret check until the next update or a rotation succeeds"),
])
async def test_a_failure_after_the_guard_came_off_locks_it_again_once(tmp_path, fails, note):
    events: list = []
    caddyfile = tmp_path / "Caddyfile"
    caddyfile.write_text(_caddyfile_of(_render(OLD)))
    config = await _pre_secret_dist_config()
    _set_origin_header(config, OLD)
    cf = FakeCloudFront(events, config)
    ssm = _ApplyFails(events, tmp_path, caddyfile, n=fails)
    p = _existing_hive_provisioner(events, cf, ssm, {**INSTANCE, "origin_secret": OLD})
    row = _stateful(p, {**INSTANCE, "origin_secret": OLD})
    with patch("hive.provisioner.aiosqlite.connect", return_value=_db_conn()), \
         patch("hive.provisioner.asyncio.sleep", new=AsyncMock()):
        with pytest.raises(RuntimeError):
            await p.rotate_origin_secret("inst-1")
    assert row["status"] == "running" and note in row["error_message"]
    assert "rotate again" in row["error_message"]
    assert events.count("ssm.origin_apply:failed") == fails
    if fails == 1:                           # the one relock put the guard back on
        assert row["origin_secret"] == cf.header_value() and row["origin_secret"] != OLD
        assert _answer(caddyfile, OLD) == "403"
    else:                                    # both failed: the record says the guard is off
        assert row["origin_secret"] is None and _answer(caddyfile, None) == "401"


class _HeaderDropFails(FakeCloudFront):
    """The distribution refuses the update that drops the header (an ETag race, throttling)."""

    def update_distribution(self, Id, DistributionConfig, IfMatch):
        from hive.provisioner import _origin_header_value
        if _origin_header_value(DistributionConfig) is None and _origin_header_value(self.config):
            self.events.append("cf.update_distribution:refused")
            raise ConnectionError("PreconditionFailed")
        return super().update_distribution(Id, DistributionConfig, IfMatch)


@pytest.mark.parametrize("stored", [OLD, None])          # None: a Host restored from a backup
async def test_a_failed_header_drop_after_the_rule_came_off_locks_again_now(tmp_path, stored):
    # the Caddy rule is out and no script is left running: the origin is locked again at once
    events: list = []
    caddyfile = tmp_path / "Caddyfile"
    caddyfile.write_text(_caddyfile_of(_render(OLD)))
    config = await _pre_secret_dist_config()
    _set_origin_header(config, OLD)
    cf = _HeaderDropFails(events, config)
    ssm = ScriptRunningSSM(events, tmp_path, caddyfile)
    p = _existing_hive_provisioner(events, cf, ssm, {**INSTANCE, "origin_secret": stored})
    row = _stateful(p, {**INSTANCE, "origin_secret": stored})
    with patch("hive.provisioner.aiosqlite.connect", return_value=_db_conn()), \
         patch("hive.provisioner.asyncio.sleep", new=AsyncMock()):
        with pytest.raises(ConnectionError):
            await p.rotate_origin_secret("inst-1")
    assert events.count("ssm.origin_apply") == 1
    assert _answer(caddyfile, None) == "403", "the guard is back on"
    assert row["origin_secret"] == OLD and "locked again" in row["error_message"]


class _KeepsSending(FakeCloudFront):
    """A distribution that answers the header drop but goes on sending the old value."""

    def update_distribution(self, Id, DistributionConfig, IfMatch):
        from hive.provisioner import _origin_header_value
        kept = _origin_header_value(self.config)
        out = super().update_distribution(Id, DistributionConfig, IfMatch)
        if kept and _origin_header_value(self.config) is None:
            _set_origin_header(self.config, kept)
        return out


class _RemoveTimesOut(ScriptRunningSSM):
    """The remove script runs on the Hive, but the call reports TimedOut (a slow agent)."""

    def get_command_invocation(self, CommandId, InstanceId):
        out = super().get_command_invocation(CommandId, InstanceId)
        if self.events and "ssm.origin_remove" in self.events and not getattr(self, "_once", False):
            self._once = True
            return {**out, "Status": "TimedOut"}
        return out


class _RemovePollFails(ScriptRunningSSM):
    """The remove script runs, then polling for its result raises (throttling, network)."""

    def get_command_invocation(self, CommandId, InstanceId):
        if "ssm.origin_remove" in self.events and not getattr(self, "_once", False):
            self._once = True
            raise ConnectionError("endpoint connection error")
        return super().get_command_invocation(CommandId, InstanceId)


@pytest.mark.parametrize("stored", [OLD, None])          # None: a Host restored from a backup
@pytest.mark.parametrize("how", ["timed_out", "polling_error"])
async def test_a_removal_that_did_not_report_back_is_left_to_the_next_update(tmp_path, how, stored):
    # the Caddy rule came off on the Hive although the call did not report Success: the
    # stored secret is still OLD. No relock now (the script could still be running and the
    # two would overlap): the stored secret is cleared, so the next update locks again
    events: list = []
    caddyfile = tmp_path / "Caddyfile"
    caddyfile.write_text(_caddyfile_of(_render(OLD)))
    config = await _pre_secret_dist_config()
    _set_origin_header(config, OLD)
    cf = FakeCloudFront(events, config)
    ssm = _RemoveTimesOut(events, tmp_path, caddyfile) if how == "timed_out" \
        else _RemovePollFails(events, tmp_path, caddyfile)
    p = _existing_hive_provisioner(events, cf, ssm, {**INSTANCE, "origin_secret": stored})
    row = _stateful(p, {**INSTANCE, "origin_secret": stored})
    with patch("hive.provisioner.aiosqlite.connect", return_value=_db_conn()), \
         patch("hive.provisioner.asyncio.sleep", new=AsyncMock()):
        with pytest.raises(Exception):
            await p.rotate_origin_secret("inst-1")
    assert "ssm.origin_apply" not in events, "no relock that could overlap the removal"
    assert row["origin_secret"] is None, "the next update's origin step will lock again"
    assert "did not report back" in row["error_message"] and "rotate again" in row["error_message"]
    # and the next update does lock it again, with the value the distribution sends
    events.clear()
    p2 = _existing_hive_provisioner(events, cf, ScriptRunningSSM(events, tmp_path, caddyfile),
                                    {**INSTANCE, "origin_secret": None})
    with patch("hive.provisioner.aiosqlite.connect", return_value=_db_conn()), \
         patch("hive.provisioner.asyncio.sleep", new=AsyncMock()):
        await p2.update("inst-1", "2.6.0")
    assert _stored_secrets(p2._update_instance) == [OLD]
    assert _answer(caddyfile, None) == "403", "the guard is back on"


async def test_rotation_never_reports_success_on_the_old_value(tmp_path):
    events: list = []
    caddyfile = tmp_path / "Caddyfile"
    caddyfile.write_text(_caddyfile_of(_render(OLD)))
    config = await _pre_secret_dist_config()
    _set_origin_header(config, OLD)
    cf = _KeepsSending(events, config)
    ssm = ScriptRunningSSM(events, tmp_path, caddyfile)
    p = _existing_hive_provisioner(events, cf, ssm, {**INSTANCE, "origin_secret": OLD})
    row = _stateful(p, {**INSTANCE, "origin_secret": OLD})
    with patch("hive.provisioner.aiosqlite.connect", return_value=_db_conn()), \
         patch("hive.provisioner.asyncio.sleep", new=AsyncMock()):
        with pytest.raises(RuntimeError, match="did not change"):
            await p.rotate_origin_secret("inst-1")
    assert "Rotating the origin secret failed" in row["error_message"]
    assert row["error_message"].endswith("rotate again.")


async def test_rotation_refuses_a_hive_it_cannot_rotate(tmp_path):
    events: list = []
    caddyfile, cf, ssm = await _locked_hive(tmp_path, events)
    # no distribution: refused, nothing on the Hive touched, back to running with the reason
    p = _existing_hive_provisioner(events, cf, ssm, {})
    row = _stateful(p, {**INSTANCE, "cloudfront_dist_id": None, "origin_secret": OLD})
    with patch("hive.provisioner.aiosqlite.connect", return_value=_db_conn()):
        with pytest.raises(RuntimeError, match="no CloudFront distribution"):
            await p.rotate_origin_secret("inst-1")
    assert not any(str(e).startswith(("ssm.", "cf.update")) for e in events)
    assert row["origin_secret"] == OLD and row["status"] == "running"
    # not running (or another update holds it): the claim refuses before anything runs
    p2 = _existing_hive_provisioner(events, cf, ssm, {**INSTANCE, "origin_secret": OLD})
    conn = _db_conn()
    conn.execute.return_value.rowcount = 0
    with patch("hive.provisioner.aiosqlite.connect", return_value=conn):
        with pytest.raises(RuntimeError, match="not in 'running' state"):
            await p2.rotate_origin_secret("inst-1")
    p2._get_instance.assert_not_called()
    p2._update_instance.assert_not_called()


# ── the route and the owner agent ──

@pytest.fixture
async def client():
    from main import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


@pytest.fixture
def prov(monkeypatch):
    from routers import hive as hive_router
    p = MagicMock()
    p.claim_update = AsyncMock(return_value=None)
    p.rotate_origin_secret = AsyncMock(return_value=None)
    p.release_stale_updates = AsyncMock(return_value=None)
    monkeypatch.setattr(hive_router, "_provisioner", p)
    monkeypatch.delenv("SWARMAI_MODE", raising=False)
    return p


async def _seed(iid: str, status: str = "running", dist: str | None = "EDIST") -> str:
    from database import db
    await ha.ensure_swarmai_row(Path(str(db.db_path)))
    async with aiosqlite.connect(str(db.db_path)) as c:
        await c.execute(
            "INSERT INTO hive_instances (id, name, account_ref, region, instance_type, status, "
            "ec2_instance_id, carrier, cloudfront_dist_id, created_at, updated_at) VALUES "
            "(?, ?, 'swarmai', 'us-east-1', 'm7g.xlarge', ?, 'i-1', 'ec2', ?, 't', 't')",
            (iid, iid, status, dist))
        await c.commit()
    return iid


async def test_route_claims_and_runs_in_background(client, prov):
    import asyncio
    hid = await _seed("rot-1")
    r = await client.post(f"/api/hive/instances/{hid}/origin-secret/rotate")
    assert r.status_code == 200 and r.json() == {"status": "updating"}
    prov.claim_update.assert_awaited_once_with(hid)
    for _ in range(20):                       # the background task starts right after
        if prov.rotate_origin_secret.await_count:
            break
        await asyncio.sleep(0.01)
    prov.rotate_origin_secret.assert_awaited_once_with(hid, claimed=True)


async def test_route_refuses_and_starts_nothing(client, prov):
    r = await client.post("/api/hive/instances/nope/origin-secret/rotate")
    assert r.status_code == 404
    stopped = await _seed("rot-2", status="stopped")
    assert (await client.post(f"/api/hive/instances/{stopped}/origin-secret/rotate")).status_code == 400
    nodist = await _seed("rot-3", dist=None)
    assert (await client.post(f"/api/hive/instances/{nodist}/origin-secret/rotate")).status_code == 400
    prov.claim_update.assert_not_called()
    # a Hive another update holds is 'updating': 409, before any claim
    busy = await _seed("rot-4", status="updating")
    assert (await client.post(f"/api/hive/instances/{busy}/origin-secret/rotate")).status_code == 409
    prov.claim_update.assert_not_called()
    # and the claim itself losing a race answers 409 too
    racing = await _seed("rot-5")
    prov.claim_update.side_effect = RuntimeError("Instance rot-5 is not in 'running' state")
    assert (await client.post(f"/api/hive/instances/{racing}/origin-secret/rotate")).status_code == 409
    prov.rotate_origin_secret.assert_not_called()


def test_owner_agent_may_rotate():
    from core import swarm_out_ops
    from core.run_owner_gate import _user_only_deny
    from core.swarm_out_ops import OWNER
    op = next(o for o in swarm_out_ops._STATIC if o.name == "hive.origin_secret.rotate")
    assert op.method == "POST" and op.path.endswith("/instances/{instance_id}/origin-secret/rotate")
    assert OWNER in op.callers and op.weight == "change"
    cmd = "curl -s -X POST http://127.0.0.1:18321/api/hive/instances/i1/origin-secret/rotate"
    assert _user_only_deny(cmd) is None


# ── backups ──

async def test_backup_snapshot_carries_no_origin_secret(tmp_path):
    from core.git_sync_engine import GitSyncEngine
    db = tmp_path / "data.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE hive_instances (id TEXT PRIMARY KEY, name TEXT, "
                 "auth_password TEXT, origin_secret TEXT)")
    conn.execute(f"INSERT INTO hive_instances VALUES ('h1', 'demo', 'HOST-KEY', '{OLD}')")
    conn.execute("INSERT INTO hive_instances VALUES ('h2', 'other', NULL, '" + SECOND + "')")
    conn.commit()
    conn.close()
    out = tmp_path / "db-export"
    exported = await GitSyncEngine(workspace_dir=tmp_path / "ws").export_db_tables(
        db_path=db, export_dir=out, tables=["hive_instances"])
    assert exported == 1
    dump = gzip.open(out / "hive_instances.sql.gz", "rt").read()
    for secret in (OLD, SECOND, "HOST-KEY"):
        assert secret not in dump
    assert "'demo'" in dump and "'other'" in dump
    live = sqlite3.connect(db).execute("SELECT origin_secret FROM hive_instances ORDER BY id").fetchall()
    assert live == [(OLD,), (SECOND,)]


def test_every_secret_looking_hive_column_is_left_out_of_backups(tmp_path):
    # the backup clears named columns: a secret column added later must be named there too
    import asyncio
    import re
    from core.git_sync_engine import BACKUP_SECRET_COLUMNS
    from database.sqlite import SQLiteDatabase, close_all_pools

    db = tmp_path / "schema.db"

    async def init():
        await SQLiteDatabase(db_path=str(db)).initialize()
        await close_all_pools()
    asyncio.run(init())
    cols = [r[1] for r in sqlite3.connect(db).execute("PRAGMA table_info(hive_instances)")]
    # names that look like a secret; ssh_key_name is the name of a key pair, not a key
    not_secret = {"ssh_key_name"}
    secretish = {c for c in cols if re.search(r"pass|secret|token|cred|key", c)} - not_secret
    assert secretish, "the schema read no secret-looking column: the check would prove nothing"
    assert secretish <= set(BACKUP_SECRET_COLUMNS["hive_instances"]), secretish


async def test_restored_host_relocks_with_the_value_the_distribution_sends(tmp_path):
    # a Host restored from a backup has no secret stored; its Hive still checks OLD and its
    # distribution still sends OLD. The next update locks again with OLD: never locked out.
    events: list = []
    caddyfile, cf, ssm = await _locked_hive(tmp_path, events)
    p = _existing_hive_provisioner(events, cf, ssm, dict(INSTANCE))          # no origin_secret
    with patch("hive.provisioner.aiosqlite.connect", return_value=_db_conn()), \
         patch("hive.provisioner.asyncio.sleep", new=AsyncMock()):
        await p.update("inst-1", "2.6.0")
    assert _stored_secrets(p._update_instance) == [OLD]
    assert cf.header_value() == OLD
    assert _answer(caddyfile, OLD) == "401"
    assert "cf.update_distribution" not in events, "the distribution keeps the value it sends"
