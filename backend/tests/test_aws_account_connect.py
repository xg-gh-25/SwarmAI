"""Connecting and changing the SwarmAI account (core/aws_account.connect, POST
/api/aws-account/connect; design 2026-10-31-swarmai-aws-account §6.1 ④, §6.5, §7.5).

Questions answered:
1. a candidate becomes the account only after STS confirms it is the account asked for:
   a mismatch or a failed sign-in leaves the current record exactly as it was (never
   locked out); a success replaces the pin and the region.
2. what reaches a command is checked: the ada program is the one found here (never a
   path from the request), a provider is isengard / conduit, a profile must exist and
   cannot be SwarmAI's own, SSO needs its account and role, keys must be stored first.
3. changing to another account while Hives are in the current one asks first (409 with
   the list); keeping them moves them to a row of the old account that can only stop,
   delete and read, checked with STS against that account. Every sign-in method can be
   kept (each identity has its own slot); the account the SwarmAI row's Hives are in
   decides, so a record without that account cannot adopt them; a commit that fails
   puts the Hives back; after a commit only the sign-ins still used are kept; one
   connect runs at a time.
5. a kept record is data from the database: what reaches a command is checked again.
4. a connected record is never re-derived from the old settings; the remediation names
   the new sources.
"""
from __future__ import annotations

import json

import aiosqlite
import pytest
from httpx import ASGITransport, AsyncClient

from core import aws_account as aa
from core.app_config_manager import AppConfigManager
from hive import accounts as ha

A, B = "111111111111", "222222222222"
UA = {"X-Swarm-User-Action": "test"}


@pytest.fixture
def cfg(monkeypatch):
    mgr = AppConfigManager.instance()
    old = mgr.get(aa.RECORD_KEY)
    aa._reset_cache()
    monkeypatch.setattr(aa, "find_ada", lambda: "/opt/tools/ada")
    monkeypatch.setattr(aa, "write_profile_file", lambda region: None)
    monkeypatch.setattr(aa, "run_capability_checks", lambda c, r=None: {})
    yield mgr
    aa.save_record(old)
    aa._reset_cache()


def _creds(akid="ASIAONE"):
    return aa.Creds(akid, "s", "t", None)


@pytest.fixture
def sts(monkeypatch):
    answer = {"Account": A}
    monkeypatch.setattr(aa, "_sts_identity", lambda c, r: {"Account": answer["Account"],
                                                            "Arn": f"arn:aws:sts::{answer['Account']}:x"})
    monkeypatch.setitem(aa._RESOLVERS, "ada", lambda rec: _creds())
    return answer


ADA = {"source": "ada", "account_id": A, "region": "eu-west-1",
       "ada": {"role": "Admin", "provider": "isengard", "bin": "/tmp/evil/ada"}}


def test_connect_replaces_the_record_only_after_sts(cfg, sts):
    rec = aa.connect(ADA)
    assert rec["account_id"] == A and rec["state"] == aa.CONNECTED and rec["derived"] is False
    assert rec["ada"]["bin"] == "/opt/tools/ada"            # the one found here, not the request's
    assert cfg.get("aws_region") == "eu-west-1"
    sts["Account"] = B                                        # the sign-in reaches another account
    with pytest.raises(aa.AccountMismatch):
        aa.connect({**ADA, "account_id": "333333333333"})
    assert aa.get_record()["account_id"] == A                 # unchanged


def test_a_failed_sign_in_keeps_the_current_record(cfg, sts, monkeypatch):
    aa.connect(ADA)

    def fail(rec):
        raise aa.NeedsLogin("Midway has expired")
    monkeypatch.setitem(aa._RESOLVERS, "ada", fail)
    with pytest.raises(aa.NeedsLogin):
        aa.connect({**ADA, "account_id": B})
    assert aa.get_record()["account_id"] == A


@pytest.mark.parametrize("cand,kind", [
    ({"source": "nope"}, "bad_input"),
    ({**ADA, "region": "mars-1"}, "bad_input"),
    ({**ADA, "account_id": "12"}, "bad_input"),
    ({**ADA, "ada": {"role": "Admin", "provider": "elsewhere"}}, "bad_input"),
    ({**ADA, "ada": {"role": "Ad min;rm", "provider": "isengard"}}, "bad_input"),
    ({"source": "ada", "region": "us-east-1"}, "bad_input"),             # ADA needs the account
    ({"source": "profile", "profile": "swarmai"}, "profile_not_found"),
    ({"source": "profile", "profile": "../x"}, "profile_not_found"),
    ({"source": "sso", "account_id": A, "sso": {"start_url": "https://evil.example",
                                                "sso_region": "us-east-1", "role": "Admin"}}, "bad_input"),
    ({"source": "sso", "sso": {"start_url": "https://d-1.awsapps.com/start",
                               "sso_region": "us-east-1", "role": "Admin"}}, "bad_input"),
    ({"source": "keys"}, "no_credentials"),
])
def test_what_reaches_a_command_is_checked(cfg, sts, cand, kind):
    with pytest.raises(aa.AccountError) as exc:
        aa.connect(cand)
    assert exc.value.kind == kind


def test_ada_without_ada_installed(cfg, sts, monkeypatch):
    monkeypatch.setattr(aa, "find_ada", lambda: None)
    with pytest.raises(aa.AccountError) as exc:
        aa.connect(ADA)
    assert exc.value.kind == "ada_not_found"


def test_a_connected_record_is_never_re_derived(cfg, sts, monkeypatch):
    aa.connect(ADA)
    monkeypatch.setattr(aa, "derive_from_legacy", lambda c: (
        {"source": "profile", "profile": "corp", "account_id": B, "region": "us-east-1"}, "aws"))
    cfg.update({"auth_method": "sso"})
    assert aa.rederive(cfg)["account_id"] == A


@pytest.mark.parametrize("source,method", [("sso", "sso_app"), ("login", "aws_login"),
                                           ("keys", "access_keys")])
def test_remediation_names_the_new_sources(cfg, source, method):
    aa.save_record({"source": source, "account_id": A})
    cfg.update({"use_bedrock": True, "auth_method": "sso"})
    assert aa.remediation_method(cfg) == method
    from core.auth_remediation import remediation_for
    assert "SwarmAI Account" in remediation_for(method)["fix_text"]


# ── the route, and the Hives of the old account ───────────────────────────────

@pytest.fixture
async def client():
    from main import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


def _db():
    from database import db
    return db.db_path


async def _hive_in_swarmai_row():
    await ha.ensure_swarmai_row(_db())
    async with aiosqlite.connect(str(_db())) as c:
        await c.execute(
            "INSERT INTO hive_instances (id, name, account_ref, region, instance_type, status, "
            "ec2_instance_id, created_at, updated_at) VALUES ('h1', 'h1', 'swarmai', 'us-east-1', "
            "'m7g.xlarge', 'running', 'i-1', 't', 't')")
        await c.commit()


@pytest.mark.asyncio
async def test_the_route_needs_the_app_header(client, cfg, sts):
    assert (await client.post("/api/aws-account/connect", json={"candidate": ADA})).status_code == 403


@pytest.mark.asyncio
async def test_a_mismatch_answers_with_both_accounts(client, cfg, sts):
    sts["Account"] = B
    r = await client.post("/api/aws-account/connect", json={"candidate": ADA}, headers=UA)
    assert r.status_code == 409
    assert r.json()["detail"] | {} == {**r.json()["detail"], "expected": A, "actual": B}


@pytest.mark.asyncio
async def test_changing_with_hives_asks_then_keeps_them(client, cfg, sts, monkeypatch):
    aa.connect(ADA)                                              # the current account: A
    await _hive_in_swarmai_row()
    sts["Account"] = B
    r = await client.post("/api/aws-account/connect",
                          json={"candidate": {**ADA, "account_id": B}}, headers=UA)
    assert r.status_code == 409 and r.json()["detail"]["kind"] == "old_hives"
    assert [h["id"] for h in r.json()["detail"]["hives"]] == ["h1"] and r.json()["detail"]["can_keep"]
    assert aa.get_record()["account_id"] == A                    # nothing changed yet
    r = await client.post("/api/aws-account/connect", json={
        "candidate": {**ADA, "account_id": B}, "old_hives": "keep"}, headers=UA)
    assert r.status_code == 200 and aa.get_record()["account_id"] == B
    async with aiosqlite.connect(str(_db())) as c:
        ref = (await (await c.execute("SELECT account_ref FROM hive_instances WHERE id='h1'")).fetchone())[0]
        row = dict(zip(("id", "account_id", "auth_method", "auth_config"), await (await c.execute(
            "SELECT id, account_id, auth_method, auth_config FROM hive_accounts WHERE id=?", (ref,))).fetchone()))
        swarm = (await (await c.execute("SELECT account_id FROM hive_accounts WHERE id='swarmai'")).fetchone())[0]
    assert row["auth_method"] == ha.RECORD_METHOD and row["account_id"] == A and swarm == B
    kept = json.loads(row["auth_config"])
    assert kept["source"] == "ada" and "state" not in kept
    # the kept row: stop / delete / read only, checked against account A
    monkeypatch.setattr(ha, "swarmai_account", lambda: {"account_id": B, "region": "us-east-1"})
    with pytest.raises(ha.OtherAccountError):
        ha.session_for(row, "us-east-1", "manage")
    seen = []
    monkeypatch.setattr(aa, "creds_for_record", lambda rec: seen.append(rec["account_id"]) or _creds())
    ha.session_for(row, "us-east-1", "stop")
    assert seen == [A]


@pytest.mark.asyncio
async def test_every_sign_in_method_can_be_kept(client, cfg, sts, monkeypatch):
    aa.save_record({**ADA, "source": "keys", "keys_id": "AKIAOLD", "account_id": A, "ada": None})
    await _hive_in_swarmai_row()
    r = await client.post("/api/aws-account/connect", json={
        "candidate": {**ADA, "account_id": A}, "old_hives": "keep"}, headers=UA)
    assert r.status_code == 200                                   # same account: no question
    aa.save_record({**ADA, "source": "keys", "keys_id": "AKIAOLD", "account_id": A, "ada": None})
    sts["Account"] = B
    r = await client.post("/api/aws-account/connect", json={"candidate": {**ADA, "account_id": B}}, headers=UA)
    assert r.status_code == 409 and r.json()["detail"]["can_keep"] is True
    r = await client.post("/api/aws-account/connect", json={
        "candidate": {**ADA, "account_id": B}, "old_hives": "keep"}, headers=UA)
    assert r.status_code == 200 and aa.get_record()["account_id"] == B
    (kept,) = await ha.kept_records(_db())
    assert kept["keys_id"] == "AKIAOLD"                           # its own key pair, not the new one


@pytest.mark.asyncio
async def test_the_hive_rows_account_decides_not_only_the_record(client, cfg, sts):
    await _hive_in_swarmai_row()
    async with aiosqlite.connect(str(_db())) as c:              # the row: A's Hives
        await c.execute("DELETE FROM hive_accounts WHERE id = 'swarmai'")
        await c.execute(
            "INSERT INTO hive_accounts (id, account_id, label, auth_method, auth_config, "
            "default_region, created_at) VALUES ('swarmai', ?, 'SwarmAI', 'swarmai', '{}', "
            "'us-east-1', 't')", (A,))
        await c.commit()
    aa.save_record(None)                                          # no record for A
    sts["Account"] = B
    r = await client.post("/api/aws-account/connect", json={
        "candidate": {**ADA, "account_id": B}, "old_hives": "keep"}, headers=UA)
    assert r.status_code == 409 and r.json()["detail"]["can_keep"] is False
    assert aa.get_record() is None                                # nothing adopted A's Hives


@pytest.mark.asyncio
async def test_a_failed_commit_puts_the_hives_back(client, cfg, sts, monkeypatch):
    aa.connect(ADA)
    await _hive_in_swarmai_row()
    sts["Account"] = B

    def boom(rec, creds):
        raise RuntimeError("config.json not writable")
    monkeypatch.setattr(aa, "commit", boom)
    with pytest.raises(RuntimeError):
        await client.post("/api/aws-account/connect", json={
            "candidate": {**ADA, "account_id": B}, "old_hives": "keep"}, headers=UA)
    async with aiosqlite.connect(str(_db())) as c:
        ref = (await (await c.execute("SELECT account_ref FROM hive_instances WHERE id='h1'")).fetchone())[0]
        kept = (await (await c.execute("SELECT COUNT(*) FROM hive_accounts WHERE auth_method='record'")).fetchone())[0]
    assert ref == "swarmai" and kept == 0
    assert aa.get_record()["account_id"] == A


@pytest.mark.asyncio
async def test_only_the_sign_ins_still_used_are_kept_after_a_commit(client, cfg, sts, monkeypatch):
    from core import aws_signin
    seen = []
    monkeypatch.setattr(aws_signin, "prune", lambda records: seen.append(records))
    aa.connect(ADA)
    await _hive_in_swarmai_row()
    sts["Account"] = B
    r = await client.post("/api/aws-account/connect", json={
        "candidate": {**ADA, "account_id": B}, "old_hives": "keep"}, headers=UA)
    assert r.status_code == 200
    (records,) = seen
    assert [x["account_id"] for x in records] == [B, A]           # the new record, the kept one


@pytest.mark.asyncio
async def test_one_connect_at_a_time(client, cfg, sts, monkeypatch):
    import asyncio
    inside = {"n": 0, "max": 0}
    real = aa.verify_candidate

    def slow(cand):
        inside["n"] += 1
        inside["max"] = max(inside["max"], inside["n"])
        import time
        time.sleep(0.05)
        inside["n"] -= 1
        return real(cand)
    monkeypatch.setattr(aa, "verify_candidate", slow)
    await asyncio.gather(*(client.post("/api/aws-account/connect", json={"candidate": ADA}, headers=UA)
                           for _ in range(3)))
    assert inside["max"] == 1


def test_a_kept_record_is_checked_again_before_it_runs_anything(cfg, sts, monkeypatch):
    seen = []
    monkeypatch.setitem(aa._RESOLVERS, "ada", lambda rec: seen.append(rec["ada"]["bin"]) or _creds())
    aa.creds_for_record({"source": "ada", "account_id": A, "region": "us-east-1",
                         "ada": {"role": "Admin", "provider": "isengard", "bin": "/tmp/evil/ada"}})
    assert seen == ["/opt/tools/ada"]                             # the one found here
    with pytest.raises(aa.AccountError):
        aa.creds_for_record({"source": "profile", "account_id": A, "profile": "../x"})


@pytest.mark.asyncio
async def test_a_kept_record_row_cannot_be_created_through_the_hive_api(client, cfg, monkeypatch):
    monkeypatch.setattr(ha, "swarmai_account", lambda: None)
    r = await client.post("/api/hive/accounts", json={
        "account_id": A, "auth_method": "record",
        "auth_config": {"source": "ada", "account_id": A, "ada": {"bin": "/tmp/x/ada"}}})
    assert r.status_code == 422


def test_creds_for_record_is_pinned_to_its_account(cfg, sts):
    sts["Account"] = B
    with pytest.raises(aa.AccountMismatch):
        aa.creds_for_record({"source": "ada", "account_id": A})
