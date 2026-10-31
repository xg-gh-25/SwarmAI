"""Hives use the SwarmAI AWS account (design 2026-10-31-swarmai-aws-account §5.4, §5.5, §7.3).

Questions answered:
1. session_for: does a Hive in the SwarmAI account get the pinned session, does another
   account's row get its own credentials only to stop / clean up / read, and does nothing
   fall through to some other account (mismatched SwarmAI row, account not connected)?
   With no SwarmAI account (legacy mode) does every row work as before?
2. migrate: is a row whose credentials reach the SwarmAI account merged (its Hives moved,
   the change backed up 0600 first), another account's row left alone, an unreachable one
   left for next time, and is it idempotent? Does the SwarmAI row keep its account id
   while it holds Hives?
3. routes: in SwarmAI mode are adding accounts and deleting the SwarmAI row refused, do new
   Hives go to the SwarmAI row only, do the rows carry their role?
4. the limit of 8: refused at 8 counted Hives on create and retry, a failed create without
   EC2 not counted, and concurrent creates at 7 let exactly one through.
5. review round 1: nothing moves onto a SwarmAI row naming another account; a damaged
   backup is kept aside; a repeated merge writes no second entry; a delete whose account
   cannot be reached keeps the row (409); stop/start never leave 'stopping'/'starting'
   behind (they end in 'error' saying why); Verify names another account; the disconnected SwarmAI row takes no Hive;
   Swarm Out only reads another account's Hive.
6. another account's credentials must still reach the account the row records (a
   default-chain row can since point elsewhere): checked with STS once per access key.
"""
import asyncio
import json
import os
import stat
from unittest.mock import AsyncMock, MagicMock

import aiosqlite
import pytest
from httpx import ASGITransport, AsyncClient

from core import aws_account
from hive import accounts as ha

ACCT = "111111111111"
MODE = {"account_id": ACCT, "region": "us-east-1"}


def _row(**kw):
    base = {"id": "r1", "account_id": "222222222222", "auth_method": "access_keys",
            "auth_config": json.dumps({"access_key_id": "AKIAOLD", "secret_access_key": "s"}),
            "default_region": "us-east-1", "label": "old"}
    return {**base, **kw}


# ── 1. session_for ──────────────────────────────────────────────────────────────

@pytest.fixture
def pinned(monkeypatch):
    calls = []
    monkeypatch.setattr(ha, "swarmai_account", lambda: dict(MODE))
    monkeypatch.setattr(aws_account, "boto_session",
                        lambda region=None: calls.append(region) or "PINNED")
    return calls


def test_swarmai_row_gets_the_pinned_session(pinned):
    row = _row(id=ha.SWARMAI_ROW_ID, account_id=ACCT, auth_method=ha.SWARMAI_METHOD)
    assert ha.session_for(row, "us-west-2", "manage") == "PINNED"
    assert pinned == ["us-west-2"]


def test_swarmai_row_in_another_account_is_refused(pinned):
    row = _row(id=ha.SWARMAI_ROW_ID, account_id="999999999999", auth_method=ha.SWARMAI_METHOD)
    for purpose in ("manage", "stop", "cleanup", "read"):
        with pytest.raises(ha.OtherAccountError):
            ha.session_for(row, "us-east-1", purpose)
    assert pinned == []


def test_swarmai_row_without_the_account_connected_is_refused(monkeypatch):
    monkeypatch.setattr(ha, "swarmai_account", lambda: None)
    row = _row(id=ha.SWARMAI_ROW_ID, account_id=ACCT, auth_method=ha.SWARMAI_METHOD)
    with pytest.raises(aws_account.AccountError):
        ha.session_for(row, "us-east-1", "stop")


def test_another_account_only_stops_cleans_up_and_reads(pinned, monkeypatch):
    checked = []
    monkeypatch.setattr(ha, "_pin_other", lambda row, session: checked.append(row["id"]))
    row = _row()
    with pytest.raises(ha.OtherAccountError):
        ha.session_for(row, "us-east-1", "manage")
    for purpose in ("stop", "cleanup", "read"):
        s = ha.session_for(row, "us-east-1", purpose)
        assert s.get_credentials().access_key == "AKIAOLD"
    assert pinned == [] and checked == ["r1", "r1", "r1"]  # each use is pinned


def test_legacy_mode_rows_work_as_before(monkeypatch):
    monkeypatch.setattr(ha, "swarmai_account", lambda: None)
    s = ha.session_for(_row(), "eu-west-1", "manage")
    assert s.get_credentials().access_key == "AKIAOLD" and s.region_name == "eu-west-1"


def test_merged_rows_and_unknown_purposes_are_refused(pinned):
    with pytest.raises(ha.OtherAccountError):
        ha.session_for(_row(auth_method=ha.MERGED_METHOD), "us-east-1", "stop")
    with pytest.raises(ValueError):
        ha.session_for(_row(), "us-east-1", "deploy")


def test_swarmai_mode_needs_a_resolvable_record_with_an_account(monkeypatch):
    monkeypatch.setattr(aws_account, "active", lambda: True)
    monkeypatch.setattr(aws_account, "get_record", lambda: {"source": "ada", "account_id": None})
    assert ha.swarmai_account() is None
    monkeypatch.setattr(aws_account, "get_record",
                        lambda: {"source": "ada", "account_id": ACCT, "region": "us-west-2"})
    assert ha.swarmai_account() == {"account_id": ACCT, "region": "us-west-2"}
    monkeypatch.setattr(aws_account, "active", lambda: False)
    assert ha.swarmai_account() is None


# ── 2. migrate ─────────────────────────────────────────────────────────────────

async def _db(path):
    async with aiosqlite.connect(path) as c:
        await c.execute("CREATE TABLE hive_accounts (id TEXT PRIMARY KEY, account_id TEXT, label TEXT, "
                        "auth_method TEXT, auth_config TEXT, default_region TEXT, created_at TEXT, "
                        "verified_at TEXT)")
        await c.execute("CREATE TABLE hive_instances (id TEXT PRIMARY KEY, account_ref TEXT "
                        "REFERENCES hive_accounts(id) ON DELETE CASCADE, status TEXT, "
                        "ec2_instance_id TEXT)")
        for r in (_row(id="same", account_id=ACCT), _row(id="other"), _row(id="down")):
            await c.execute("INSERT INTO hive_accounts VALUES (?,?,?,?,?,?,'t',NULL)",
                            (r["id"], r["account_id"], r["label"], r["auth_method"],
                             r["auth_config"], r["default_region"]))
        for iid, ref in (("h1", "same"), ("h2", "same"), ("h3", "other")):
            await c.execute("INSERT INTO hive_instances VALUES (?,?,'running','i-x')", (iid, ref))
        await c.commit()


async def _rows(path, sql):
    async with aiosqlite.connect(path) as c:
        return [tuple(r) for r in await (await c.execute(sql)).fetchall()]


@pytest.fixture
def mig(tmp_path, monkeypatch):
    db = tmp_path / "h.db"
    asyncio.run(_db(db))
    monkeypatch.setattr(ha, "swarmai_account", lambda: dict(MODE))
    monkeypatch.setattr(aws_account, "aws_dir", lambda: tmp_path / "aws")

    def sts(row):
        if row["id"] == "down":
            raise TimeoutError("offline")
        return ACCT if row["id"] == "same" else "222222222222"
    monkeypatch.setattr(ha, "_sts_account", sts)
    return db


def test_migrate_merges_the_same_account_and_leaves_the_rest(mig, tmp_path):
    out = asyncio.run(ha.migrate(mig))
    assert out == {"same": "merged", "other": "other", "down": "unverified"}
    assert sorted(asyncio.run(_rows(mig, "SELECT id, account_ref FROM hive_instances"))) == [
        ("h1", "swarmai"), ("h2", "swarmai"), ("h3", "other")]
    methods = dict(asyncio.run(_rows(mig, "SELECT id, auth_method FROM hive_accounts")))
    assert methods == {"same": "merged", "other": "access_keys", "down": "access_keys",
                       "swarmai": "swarmai"}
    # credentials are never touched
    assert asyncio.run(_rows(mig, "SELECT auth_config FROM hive_accounts WHERE id='same'"))[0][0] \
        == _row()["auth_config"]
    backup = tmp_path / "aws" / "hive-accounts-migration.json"
    assert stat.S_IMODE(os.stat(backup).st_mode) == 0o600
    entry, = json.loads(backup.read_text())
    assert entry["row_id"] == "same" and entry["auth_method"] == "access_keys"
    assert sorted(entry["instances_moved"]) == ["h1", "h2"]
    assert "AKIAOLD" not in backup.read_text()
    # idempotent: the merged row is not checked again, nothing else moves
    again = asyncio.run(ha.migrate(mig))
    assert again == {"other": "other", "down": "unverified"}
    assert len(json.loads(backup.read_text())) == 1


def test_migrate_does_nothing_without_the_swarmai_account(mig, monkeypatch):
    monkeypatch.setattr(ha, "swarmai_account", lambda: None)
    assert asyncio.run(ha.migrate(mig)) == {}
    assert ("swarmai",) not in asyncio.run(_rows(mig, "SELECT id FROM hive_accounts"))


def test_the_swarmai_row_keeps_its_account_while_it_holds_hives(mig, monkeypatch):
    asyncio.run(ha.migrate(mig))
    monkeypatch.setattr(ha, "swarmai_account", lambda: {"account_id": "333333333333",
                                                        "region": "us-east-1"})
    row = asyncio.run(ha.ensure_swarmai_row(mig))
    assert row["account_id"] == ACCT  # h1/h2 are in ACCT: never re-pointed silently

    async def clear():
        async with aiosqlite.connect(mig) as c:
            await c.execute("DELETE FROM hive_instances WHERE account_ref='swarmai'")
            await c.commit()
    asyncio.run(clear())
    assert asyncio.run(ha.ensure_swarmai_row(mig))["account_id"] == "333333333333"


# ── 3 + 4. routes ──────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
async def _no_background_deploys(monkeypatch):
    from routers import hive as hive_router
    provisioner = MagicMock()
    provisioner.deploy = AsyncMock(return_value=None)
    provisioner.cleanup = AsyncMock(return_value=None)
    provisioner.instance_exists = AsyncMock(return_value=False)
    monkeypatch.setattr(hive_router, "_provisioner", provisioner)
    yield
    tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()
             and t.get_name().startswith(("hive-deploy-", "hive-retry-"))]
    if tasks:
        await asyncio.gather(*tasks)


@pytest.fixture
async def client():
    from main import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


async def _seed(n, *, status="running", ec2="i-1", ref="swarmai"):
    from database import db
    async with aiosqlite.connect(str(db.db_path)) as c:
        for i in range(n):
            await c.execute(
                "INSERT INTO hive_instances (id, name, account_ref, region, instance_type, status, "
                "ec2_instance_id, created_at, updated_at) VALUES (?, ?, ?, 'us-east-1', "
                "'m7g.xlarge', ?, ?, 't', 't')",
                (f"seed-{status}-{i}", f"seed-{status}-{i}", ref, status, ec2))
        await c.commit()


@pytest.mark.asyncio
async def test_swarmai_mode_routes(client, monkeypatch):
    monkeypatch.setattr(ha, "swarmai_account", lambda: dict(MODE))
    rows = (await client.get("/api/hive/accounts")).json()
    assert [(r["id"], r["role"], r["account_id"]) for r in rows] == [("swarmai", "swarmai", ACCT)]
    assert (await client.post("/api/hive/accounts", json={"account_id": "222222222222"})).status_code == 409
    assert (await client.delete("/api/hive/accounts/swarmai")).status_code == 409
    r = await client.post("/api/hive/instances", json={"name": "h-a"})
    assert r.status_code == 201 and r.json()["account_ref"] == "swarmai"
    r = await client.post("/api/hive/instances", json={"name": "h-b", "account_ref": "else"})
    assert r.status_code == 409


@pytest.mark.asyncio
async def test_legacy_mode_needs_an_account_and_cannot_fake_a_swarmai_row(client, monkeypatch):
    monkeypatch.setattr(ha, "swarmai_account", lambda: None)
    assert (await client.post("/api/hive/instances", json={"name": "h-a"})).status_code == 422
    r = await client.post("/api/hive/accounts", json={"account_id": "1", "auth_method": "swarmai"})
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_at_most_eight_and_a_failed_create_is_not_counted(client, monkeypatch):
    monkeypatch.setattr(ha, "swarmai_account", lambda: dict(MODE))
    await client.get("/api/hive/accounts")  # creates the SwarmAI row
    await _seed(7)
    await _seed(3, status="error", ec2=None)  # failed creates: no EC2, not counted
    r = await client.post("/api/hive/instances", json={"name": "h-eighth"})
    assert r.status_code == 201
    r = await client.post("/api/hive/instances", json={"name": "h-ninth"})
    assert r.status_code == 409 and "At most 8" in r.json()["detail"]
    # retrying a failed create would make a ninth
    r = await client.post("/api/hive/instances/seed-error-0/retry")
    assert r.status_code == 409


@pytest.mark.asyncio
async def test_a_retry_of_a_counted_hive_at_eight_is_allowed(client, monkeypatch):
    monkeypatch.setattr(ha, "swarmai_account", lambda: dict(MODE))
    await client.get("/api/hive/accounts")
    await _seed(7)
    await _seed(1, status="error", ec2="i-err")  # still has EC2: already counted
    r = await client.post("/api/hive/instances/seed-error-0/retry")
    assert r.status_code == 200, r.text


@pytest.mark.asyncio
async def test_concurrent_creates_at_seven_let_one_through(client, monkeypatch):
    monkeypatch.setattr(ha, "swarmai_account", lambda: dict(MODE))
    await client.get("/api/hive/accounts")
    await _seed(7)
    real = ha.counted

    async def slow_count(conn, **kw):  # widen the window between count and insert
        n = await real(conn, **kw)
        await asyncio.sleep(0.05)
        return n
    monkeypatch.setattr(ha, "counted", slow_count)
    rs = await asyncio.gather(*(client.post("/api/hive/instances", json={"name": f"h-c{i}"})
                                for i in range(5)))
    assert sorted(r.status_code for r in rs) == [201, 409, 409, 409, 409]


@pytest.mark.asyncio
async def test_retry_of_another_accounts_hive_is_refused(client, monkeypatch):
    monkeypatch.setattr(ha, "swarmai_account", lambda: None)
    await client.post("/api/hive/accounts", json={"account_id": "222222222222", "label": "o"})
    other = (await client.get("/api/hive/accounts")).json()[0]["id"]
    await _seed(1, status="error", ec2=None, ref=other)
    monkeypatch.setattr(ha, "swarmai_account", lambda: dict(MODE))
    r = await client.post("/api/hive/instances/seed-error-0/retry")
    assert r.status_code == 409 and "another AWS account" in r.json()["detail"]


# ── 5. review round 1 ───────────────────────────────────────────────────────────

def test_nothing_moves_onto_a_swarmai_row_of_another_account(mig, monkeypatch, tmp_path):
    asyncio.run(ha.migrate(mig))  # the swarmai row now holds h1/h2 in ACCT
    b = "222222222222"
    monkeypatch.setattr(ha, "swarmai_account", lambda: {"account_id": b, "region": "us-east-1"})
    monkeypatch.setattr(ha, "_sts_account", lambda row: b)  # 'other' reaches the new account
    assert asyncio.run(ha.migrate(mig))["other"] == "unverified"
    assert ("h3", "other") in asyncio.run(_rows(mig, "SELECT id, account_ref FROM hive_instances"))
    assert dict(asyncio.run(_rows(mig, "SELECT id, auth_method FROM hive_accounts")))["other"] \
        == "access_keys"


def test_a_damaged_backup_is_kept_aside_and_a_repeat_merge_adds_nothing(mig, tmp_path):
    aws = tmp_path / "aws"
    aws.mkdir()
    (aws / "hive-accounts-migration.json").write_text("{not json")
    asyncio.run(ha.migrate(mig))
    aside = [p.name for p in aws.iterdir() if ".corrupt-" in p.name]
    assert len(aside) == 1 and (aws / aside[0]).read_text() == "{not json"
    row = dict(zip(("id", "account_id", "auth_method", "auth_config", "default_region", "label"),
                   asyncio.run(_rows(mig, "SELECT id, account_id, 'access_keys', auth_config, "
                                          "default_region, label FROM hive_accounts WHERE id='same'"))[0]))
    assert asyncio.run(ha.merge_row(mig, row)) == "merged"  # a stale caller copy: re-checked
    assert len(json.loads((aws / "hive-accounts-migration.json").read_text())) == 1


def test_the_swarmai_row_is_disconnected_without_the_account():
    row = {"auth_method": ha.SWARMAI_METHOD}
    assert ha.row_role(row, None) == "disconnected"
    assert ha.row_role(row, dict(MODE)) == "swarmai"


@pytest.mark.asyncio
async def test_a_delete_whose_account_cannot_be_reached_keeps_the_row(client, monkeypatch):
    from routers import hive as hive_router
    monkeypatch.setattr(ha, "swarmai_account", lambda: dict(MODE))
    await client.get("/api/hive/accounts")
    await _seed(2)  # not the last one (that one can only be replaced)
    hive_router._provisioner.cleanup = AsyncMock(side_effect=ha.OtherAccountError("elsewhere"))
    r = await client.delete("/api/hive/instances/seed-running-0")
    assert r.status_code == 409 and "Nothing was deleted" in r.json()["detail"]
    ids = [i["id"] for i in (await client.get("/api/hive/instances")).json()]
    assert "seed-running-0" in ids


@pytest.mark.asyncio
async def test_deleting_an_account_that_cannot_be_reached_deletes_nothing(client, monkeypatch):
    from routers import hive as hive_router
    monkeypatch.setattr(ha, "swarmai_account", lambda: None)
    await client.post("/api/hive/accounts", json={"account_id": "222222222222"})
    acct = (await client.get("/api/hive/accounts")).json()[0]["id"]
    await _seed(1, ref=acct)
    hive_router._provisioner._session = AsyncMock(side_effect=aws_account.AccountError("x", "expired"))
    assert (await client.delete(f"/api/hive/accounts/{acct}")).status_code == 409
    assert len((await client.get("/api/hive/instances")).json()) == 1
    hive_router._provisioner.cleanup.assert_not_called()


@pytest.mark.asyncio
async def test_a_merged_row_cannot_be_deleted(client, monkeypatch):
    monkeypatch.setattr(ha, "swarmai_account", lambda: None)
    await client.post("/api/hive/accounts", json={"account_id": "222222222222"})
    acct = (await client.get("/api/hive/accounts")).json()[0]["id"]
    from database import db
    async with aiosqlite.connect(str(db.db_path)) as c:
        await c.execute("UPDATE hive_accounts SET auth_method='merged' WHERE id=?", (acct,))
        await c.commit()
    assert (await client.delete(f"/api/hive/accounts/{acct}")).status_code == 409


@pytest.mark.asyncio
async def test_the_disconnected_swarmai_row_takes_no_new_hive(client, monkeypatch):
    monkeypatch.setattr(ha, "swarmai_account", lambda: dict(MODE))
    await client.get("/api/hive/accounts")
    monkeypatch.setattr(ha, "swarmai_account", lambda: None)
    r = await client.post("/api/hive/instances", json={"name": "h-x", "account_ref": "swarmai"})
    assert r.status_code == 409 and "not connected" in r.json()["detail"]
    roles = {a["id"]: a["role"] for a in (await client.get("/api/hive/accounts")).json()}
    assert roles["swarmai"] == "disconnected"


@pytest.mark.asyncio
async def test_verify_names_another_account(client, monkeypatch):
    monkeypatch.setattr(ha, "swarmai_account", lambda: None)
    await client.post("/api/hive/accounts", json={"account_id": "222222222222"})
    acct = (await client.get("/api/hive/accounts")).json()[0]["id"]
    monkeypatch.setattr(ha, "swarmai_account", lambda: dict(MODE))
    monkeypatch.setattr(ha, "_sts_account", lambda row: "222222222222")
    r = (await client.post(f"/api/hive/accounts/{acct}/verify")).json()
    assert r["success"] is False and "not the SwarmAI account" in r["error"]


@pytest.mark.parametrize("method", ["stop", "start"])
def test_stop_and_start_never_leave_a_transitional_status(tmp_path, monkeypatch, method):
    from hive.provisioner import HiveProvisioner
    db = tmp_path / "p.db"

    async def make():
        async with aiosqlite.connect(db) as c:
            await c.execute("CREATE TABLE hive_accounts (id TEXT PRIMARY KEY, account_id TEXT, "
                            "auth_method TEXT, auth_config TEXT, default_region TEXT)")
            await c.execute("CREATE TABLE hive_instances (id TEXT PRIMARY KEY, account_ref TEXT, "
                            "region TEXT, status TEXT, ec2_instance_id TEXT, error_message TEXT, "
                            "name TEXT, updated_at TEXT)")
            await c.execute("INSERT INTO hive_accounts VALUES ('o', '222222222222', 'access_keys', '{}', 'us-east-1')")
            await c.execute("INSERT INTO hive_instances VALUES ('h', 'o', 'us-east-1', ?, 'i-1', NULL, 'h', 't')",
                            ("running" if method == "stop" else "stopped",))
            await c.commit()
    asyncio.run(make())
    monkeypatch.setattr(ha, "swarmai_account", lambda: dict(MODE))
    p = HiveProvisioner(db)
    if method == "stop":  # another account may stop: make its session unreachable instead
        monkeypatch.setattr(ha, "_legacy_session", lambda row, region: (_ for _ in ()).throw(
            aws_account.AccountError("expired", "sign in again")))
    with pytest.raises(ha.ACCOUNT_ERRORS):
        asyncio.run(getattr(p, method)("h"))
    status, msg = asyncio.run(_rows(db, "SELECT status, error_message FROM hive_instances"))[0]
    assert status == "error" and msg  # says why; Start / Retry / Delete stay available


def test_swarm_out_only_reads_another_accounts_hive(monkeypatch):
    from core import swarm_out
    seen = []
    from hive import provisioner
    monkeypatch.setattr(provisioner.HiveProvisioner, "_get_session",
                        lambda self, row, region, purpose: seen.append(purpose) or MagicMock())
    swarm_out._s3({"_account": {}, "region": "us-east-1"}, "read")
    swarm_out._s3({"_account": {}, "region": "us-east-1"})
    assert seen == ["read", "manage"]


# ── 6. another account's row is pinned to its recorded account ────────────────────

class _FakeSession:
    def __init__(self, account, key="AKIAONE"):
        self.account, self.key, self.sts_calls = account, key, 0

    def get_credentials(self):
        outer = self

        class C:
            def get_frozen_credentials(self):
                return type("F", (), {"access_key": outer.key})()
        return C()

    def client(self, name, **_kw):
        outer = self

        class Sts:
            def get_caller_identity(self):
                outer.sts_calls += 1
                return {"Account": outer.account}
        assert name == "sts"
        return Sts()


def test_other_row_credentials_that_reach_another_account_do_nothing(monkeypatch):
    ha._pinned_other.clear()
    row = _row(id="pin-1")
    good = _FakeSession("222222222222")
    ha._pin_other(row, good)
    ha._pin_other(row, good)
    assert good.sts_calls == 1  # once per access key
    moved = _FakeSession("999999999999", key="AKIATWO")  # the default chain changed
    with pytest.raises(ha.OtherAccountError, match="nothing was done"):
        ha._pin_other(row, moved)


def test_other_row_without_credentials_is_refused():
    class NoCreds:
        def get_credentials(self):
            return None
    with pytest.raises(aws_account.AccountError):
        ha._pin_other(_row(id="pin-2"), NoCreds())


def test_session_for_pins_other_rows_only_in_swarmai_mode(monkeypatch):
    seen = []
    monkeypatch.setattr(ha, "_pin_other", lambda row, session: seen.append(row["id"]))
    monkeypatch.setattr(ha, "swarmai_account", lambda: None)
    ha.session_for(_row(id="legacy"), "us-east-1", "stop")
    monkeypatch.setattr(ha, "swarmai_account", lambda: dict(MODE))
    ha.session_for(_row(id="other"), "us-east-1", "stop")
    assert seen == ["other"]


def test_a_failed_sts_check_is_an_account_error(monkeypatch):
    ha._pinned_other.clear()

    class Broken(_FakeSession):
        def client(self, name, **_kw):
            class Sts:
                def get_caller_identity(self):
                    raise RuntimeError("ExpiredToken")
            return Sts()
    with pytest.raises(aws_account.AccountError, match="nothing was done"):
        ha._pin_other(_row(id="pin-3"), Broken("x"))
