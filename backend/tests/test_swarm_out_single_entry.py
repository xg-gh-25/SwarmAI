"""Swarm Out as the only place for Hives: the backend side (design
docs/design/2026-11-20-swarm-out-multi-carrier.md §6.8; mock
Knowledge/Mockups/2026-11-23-swarm-out-single-entry.html v3).

Questions answered:
1. a delete is one fewer Hive: the wanted count goes down by the Hives that stopped
   counting, so nothing is short; deleting a row that never counted (a failed create, one
   half of a replacement) leaves it as it is, also while a wanted Hive is not built yet;
   deletes at the same time lower it exactly as far as the Hives that went.
2. the SwarmAI account's last Hive may go while Elastic Workers is on standby, and is
   refused with the reason while it is off.
3. the person's Elastic Workers switch: off with no runtime only records it; off with one
   records it first and turns it off; on records it (and a chosen account) and starts a round.
4. only the person switches Elastic Workers or chooses its account: the gate refuses an
   agent's call, and PUT /api/settings writes neither key.
5. with several accounts in the old mode, the chosen one is the standby's target.
8. "off" holds: a runtime that a turn-on or update was changing when the person said off
   is turned off by the next standby round once that change ends.
9. a dispatch that finds a Hive running while a wake of it is under way (it may update the
   Hive) waits for that wake instead of sending.
6. no user-facing backend text, skill or locale says Elastic Hive or points to a Hive page.
7. the idle stop time is the person's to set through PUT /api/settings.
"""
from __future__ import annotations

import ast
import asyncio
import json
import re
import sqlite3
from pathlib import Path

import pytest

from hive import accounts as ha
from hive import agentcore_provisioner as ep
from hive import fleet
from tests.test_hive_fleet import MODE, _db, _no_real_deploys, _rows, _seed, client  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]


# ── 1, 2: deleting ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_delete_lowers_the_desired_count(client, monkeypatch):
    monkeypatch.setattr(ep, "standby_on", lambda: False)
    ids = await _seed(2)
    fleet._save_config({"desired_count": 2})
    r = await client.delete(f"/api/hive/instances/{ids[0]}")
    assert r.status_code == 200
    st = await fleet.status(_db())
    assert fleet.get_config()["desired_count"] == 1 and st["state"] == "ready" and st["counted"] == 1


@pytest.mark.asyncio
async def test_deleting_a_row_that_never_counted_keeps_the_desired_count(client, monkeypatch):
    monkeypatch.setattr(ep, "standby_on", lambda: False)
    await _seed(2)
    (failed,) = await _seed(1, status="error", ec2=None, prefix="f")
    fleet._save_config({"desired_count": 2})
    r = await client.delete(f"/api/hive/instances/{failed}")
    assert r.status_code == 200
    st = await fleet.status(_db())
    assert fleet.get_config()["desired_count"] == 2 and st["state"] == "ready" and st["counted"] == 2


@pytest.mark.asyncio
async def test_deleting_one_half_of_a_replacement_keeps_the_desired_count(client, monkeypatch):
    monkeypatch.setattr(ep, "standby_on", lambda: False)
    old, new = await _seed(2)
    fleet._save_config({"desired_count": 1, "replacing": {new: old}})
    r = await client.delete(f"/api/hive/instances/{old}")
    assert r.status_code == 200 and fleet.get_config()["desired_count"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("pair", [False, True])
async def test_deletes_at_the_same_time_lower_the_count_as_far_as_the_hives_that_went(
        client, monkeypatch, _no_real_deploys, pair):
    # both deletes are inside their AWS cleanup before either row goes
    monkeypatch.setattr(ep, "standby_on", lambda: True)
    ids = await _seed(3)
    fleet._save_config({"desired_count": 2 if pair else 3,
                        **({"replacing": {ids[1]: ids[0]}} if pair else {})})
    inside, go = [], asyncio.Event()

    async def cleanup(iid):
        inside.append(iid)
        if len(inside) == 2:
            go.set()
        await go.wait()
    _no_real_deploys.cleanup = cleanup
    r1, r2 = await asyncio.gather(client.delete(f"/api/hive/instances/{ids[0]}"),
                                  client.delete(f"/api/hive/instances/{ids[1]}"))
    assert r1.status_code == r2.status_code == 200 and len(inside) == 2
    # two Hives went (or the one a pair counted as): one is left, one is wanted
    assert fleet.get_config()["desired_count"] == 1


@pytest.mark.asyncio
async def test_deleting_a_hive_while_short_lowers_by_that_one(client, monkeypatch):
    # three wanted, two built (one could not be): deleting one leaves two wanted, not one
    monkeypatch.setattr(ep, "standby_on", lambda: True)
    ids = await _seed(2)
    fleet._save_config({"desired_count": 3})
    assert (await client.delete(f"/api/hive/instances/{ids[0]}")).status_code == 200
    assert fleet.get_config()["desired_count"] == 2


@pytest.mark.asyncio
async def test_a_delete_counts_only_itself_when_another_row_changes_meanwhile(client, monkeypatch):
    # another writer (reconcile marking a Hive gone outside SwarmAI) changes a row while the
    # delete runs: a trigger does it inside the DELETE itself; only the deleted Hive counts
    import aiosqlite
    monkeypatch.setattr(ep, "standby_on", lambda: True)
    gone, other, _ = await _seed(3)
    fleet._save_config({"desired_count": 3})
    async with aiosqlite.connect(str(_db())) as c:
        await c.execute(f"CREATE TRIGGER t_meanwhile AFTER DELETE ON hive_instances WHEN OLD.id = '{gone}' "
                        f"BEGIN UPDATE hive_instances SET status = 'error', ec2_instance_id = NULL "
                        f"WHERE id = '{other}'; END")
        await c.commit()
    try:
        assert (await client.delete(f"/api/hive/instances/{gone}")).status_code == 200
    finally:
        async with aiosqlite.connect(str(_db())) as c:
            await c.execute("DROP TRIGGER IF EXISTS t_meanwhile")
            await c.commit()
    assert fleet.get_config()["desired_count"] == 2


@pytest.mark.asyncio
async def test_a_hive_still_wanted_stays_wanted_through_deletes(client, monkeypatch, _no_real_deploys):
    monkeypatch.setattr(ep, "standby_on", lambda: True)
    # short by one: one counted Hive and a failed create the person deletes to try again
    (kept,) = await _seed(1)
    (failed,) = await _seed(1, status="error", ec2=None, prefix="f")
    fleet._save_config({"desired_count": 2})
    assert (await client.delete(f"/api/hive/instances/{failed}")).status_code == 200
    assert fleet.get_config()["desired_count"] == 2
    # a failed create and a counted Hive deleted at the same time: one fewer
    more = await _seed(1, prefix="m")
    (failed2,) = await _seed(1, status="error", ec2=None, prefix="g")
    inside, go = [], asyncio.Event()

    async def cleanup(iid):
        inside.append(iid)
        if len(inside) == 2:
            go.set()
        await go.wait()
    _no_real_deploys.cleanup = cleanup
    r1, r2 = await asyncio.gather(client.delete(f"/api/hive/instances/{failed2}"),
                                  client.delete(f"/api/hive/instances/{more[0]}"))
    assert r1.status_code == r2.status_code == 200
    assert fleet.get_config()["desired_count"] == 1 and await _rows() == [kept]


@pytest.mark.asyncio
async def test_the_last_hive_needs_elastic_workers(client, monkeypatch):
    (only,) = await _seed(1)
    fleet._save_config({"desired_count": 1})
    monkeypatch.setattr(ep, "standby_on", lambda: False)
    r = await client.delete(f"/api/hive/instances/{only}")
    assert r.status_code == 409 and "nothing would be left to run tasks" in r.json()["detail"]
    assert await _rows() == [only]
    monkeypatch.setattr(ep, "standby_on", lambda: True)
    r = await client.delete(f"/api/hive/instances/{only}")
    assert r.status_code == 200 and await _rows() == []
    st = await fleet.status(_db())
    assert fleet.get_config()["desired_count"] == 0 and st["state"] == "ready"


# ── 3, 4, 5: the Elastic Workers switch ─────────────────────────────────────────

class _Elastic:
    def __init__(self, rows):
        self._rows, self.claimed, self.disabled = rows, [], []

    async def rows(self, account_ref=None):
        return list(self._rows)

    async def claim_disable(self, location_id):
        self.claimed.append(location_id)

    async def run_disable(self, location_id):
        self.disabled.append(location_id)

    async def target(self, account_ref, region):
        return account_ref or "acct1", "111111111111", region or "us-east-1"


@pytest.fixture
def switch(monkeypatch):
    from routers import hive as hive_router
    store = {}
    monkeypatch.setattr(ep, "set_standby", lambda on: store.__setitem__("standby", on))
    monkeypatch.setattr(ep, "set_chosen_account", lambda ref: store.__setitem__("account", ref))
    rounds = []

    async def round_(db_path):
        rounds.append(db_path)
        return "enabled"
    monkeypatch.setattr(ep, "run_standby_round", round_)

    def use(rows):
        e = _Elastic(rows)
        monkeypatch.setattr(hive_router, "_get_elastic", lambda: e)
        return e
    return hive_router, store, rounds, use


def test_the_standby_switch(switch):
    hive_router, store, rounds, use = switch

    async def go():
        e = use([])
        out = await hive_router.elastic_standby(hive_router.ElasticStandby(on=False))
        assert out == {"standby": False, "turning_off": [], "after_change": []} and store == {"standby": False}
        e = use([{"id": "e1"}])
        out = await hive_router.elastic_standby(hive_router.ElasticStandby(on=False))
        await asyncio.sleep(0)
        assert out["turning_off"] == ["e1"] and out["after_change"] == [] and e.claimed == ["e1"] and e.disabled == ["e1"]
        out = await hive_router.elastic_standby(hive_router.ElasticStandby(on=True, account_ref="acct2"))
        await asyncio.sleep(0)
        assert out == {"standby": True} and store == {"standby": True, "account": "acct2"} and len(rounds) == 1
    asyncio.run(go())


def test_off_holds_for_a_runtime_that_was_changing(tmp_path, monkeypatch):
    elastic = ep.ElasticHive(tmp_path / "d.db")
    rows = [{"id": "busy1", "status": "ready"}, {"id": "ready1", "status": "ready"},
            {"id": "fail1", "status": "failed"}, {"id": "up1", "status": "upgrading"}]
    claimed, disabled = [], []

    async def _rows(account_ref=None):
        return [dict(r) for r in rows]

    async def _claim(i):
        claimed.append(i)

    async def _disable(i):
        disabled.append(i)
    async def _live(row):
        return {"runtime_status": "READY"}

    async def _settle(row, live):          # a row no task holds is settled from AWS first
        return {**row, "status": "ready"}
    monkeypatch.setattr(elastic, "rows", _rows)
    monkeypatch.setattr(elastic, "_live", _live)
    monkeypatch.setattr(elastic, "_settle", _settle)
    monkeypatch.setattr(elastic, "claim_disable", _claim)
    monkeypatch.setattr(elastic, "run_disable", _disable)
    monkeypatch.setattr(ep, "_WORKING", {"busy1"})
    monkeypatch.setattr(ep, "standby_on", lambda: False)
    did = asyncio.run(elastic.reconcile())
    # the one a task is still changing waits for a later round; one left changing with
    # no task holding it (SwarmAI quit meanwhile) is settled and turned off now
    assert claimed == disabled == ["ready1", "fail1", "up1"] and did.startswith("turned off")
    rows[:] = []
    assert asyncio.run(elastic.reconcile()) == "off"


def test_off_while_changing_is_named(switch):
    hive_router, store, rounds, use = switch

    async def go():
        e = use([{"id": "e1"}])

        async def refuse(location_id):
            raise RuntimeError("enabling")
        e.claim_disable = refuse
        out = await hive_router.elastic_standby(hive_router.ElasticStandby(on=False))
        assert out == {"standby": False, "turning_off": [], "after_change": ["e1"]} and store == {"standby": False}
    asyncio.run(go())


def test_a_dispatch_joins_a_wake_under_way(monkeypatch):
    from core import hive_idle, swarm_out
    from core.carriers import ec2
    row = {"id": "h1", "name": "w1", "status": "running", "_account": None}
    woken = []

    async def instance(ref):
        return dict(row)

    async def wake(hive_id):
        woken.append(hive_id)
    monkeypatch.setattr(swarm_out, "_instance", instance)
    monkeypatch.setattr(hive_idle, "wake", wake)

    async def go():
        assert (await ec2.instance_up("w1", swarm_out._Deadline(swarm_out.DISPATCH_DEADLINE_S)))["id"] == "h1" and woken == []
        pending = asyncio.get_running_loop().create_future()
        monkeypatch.setitem(hive_idle._STARTS, "h1", pending)
        await ec2.instance_up("w1", swarm_out._Deadline(swarm_out.DISPATCH_DEADLINE_S))
        assert woken == ["h1"]
        pending.cancel()
    asyncio.run(go())


def test_the_status_names_the_chosen_account(switch, monkeypatch):
    hive_router, store, rounds, use = switch
    e = use([])

    async def status(target=None):
        return {"hives": [], "can_enable": True, "reason": None}

    async def target(account_ref, region):
        return "acct1", "111111111111", "us-east-1"
    e.status = status
    monkeypatch.setattr(hive_router, "_elastic_target", target)
    monkeypatch.setattr(ep, "standby_on", lambda: True)
    monkeypatch.setattr(ep, "chosen_account", lambda: "acct2")
    assert asyncio.run(hive_router.elastic_status())["chosen_account"] == "acct2"


def test_only_the_person_switches_elastic_workers():
    from core import swarm_out_ops
    from core.run_owner_gate import _user_only_deny
    from core.swarm_out_ops import PERSON
    from routers.settings import WRITABLE_KEYS
    by_name = {op.name: op for op in swarm_out_ops.ops()}
    assert by_name["hive.elastic.standby"].callers == {PERSON}
    assert _user_only_deny("curl -s -X PUT http://127.0.0.1:18321/api/hive/elastic/standby "
                           "-d '{\"on\": true}'") is not None
    assert ep.STANDBY_SETTING not in WRITABLE_KEYS and ep.ACCOUNT_SETTING not in WRITABLE_KEYS


def test_the_chosen_account_is_the_target(tmp_path, monkeypatch):
    from database.sqlite import SQLiteDatabase, close_all_pools

    async def make():
        await SQLiteDatabase(db_path=str(tmp_path / "d.db")).initialize()
        await close_all_pools()
    asyncio.run(make())
    with sqlite3.connect(tmp_path / "d.db") as c:
        for ref, acct in (("acct1", "111111111111"), ("acct2", "222222222222")):
            c.execute("INSERT INTO hive_accounts (id, account_id, label, auth_method, created_at, default_region) "
                      "VALUES (?, ?, '', 'access_keys', 't', 'us-east-1')", (ref, acct))
    monkeypatch.setattr(ha, "swarmai_account", lambda: None)
    elastic = ep.ElasticHive(tmp_path / "d.db")
    monkeypatch.setattr(ep, "chosen_account", lambda: None)
    with pytest.raises(ep.ElasticTargetError) as e:
        asyncio.run(elastic.target(None, None))
    assert e.value.status == 422 and "Swarm Out › settings" in str(e.value)
    monkeypatch.setattr(ep, "chosen_account", lambda: "acct2")
    assert asyncio.run(elastic.target(None, None))[:2] == ("acct2", "222222222222")
    monkeypatch.setattr(ep, "chosen_account", lambda: "gone")
    with pytest.raises(ep.ElasticTargetError):
        asyncio.run(elastic.target(None, None))


# ── 6: the name ─────────────────────────────────────────────────────────────────

_OLD = re.compile(r"elastic hive|弹性 ?hive|hive page|hive 页", re.I)


def _strings(tree):
    """Every string constant that is not a docstring."""
    docs = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                docs.add(id(first.value))
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docs:
            yield node.lineno, node.value


def _locale_values(obj, path=""):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from _locale_values(v, f"{path}.{k}")
    elif isinstance(obj, str):
        yield path, obj


def test_no_user_facing_text_says_elastic_hive_or_the_hive_page():
    found = []
    for f in sorted(ROOT.rglob("*.py")):
        rel = f.relative_to(ROOT).as_posix()
        if rel.startswith(("tests/", ".venv/", "build/", "dist/")) or "/node_modules/" in rel:
            continue
        for line, text in _strings(ast.parse(f.read_text(encoding="utf-8"))):
            if _OLD.search(text):
                found.append(f"{rel}:{line}: {text[:80]}")
    skill = (ROOT / "skills" / "s_swarm-out" / "SKILL.md").read_text(encoding="utf-8")
    found += [f"skill: {m.group(0)}" for m in _OLD.finditer(skill)]
    for lang in ("en", "zh"):
        data = json.loads((ROOT.parent / "desktop" / "src" / "i18n" / "locales" / f"{lang}.json").read_text("utf-8"))
        found += [f"{lang}{k}: {v[:80]}" for k, v in _locale_values(data) if _OLD.search(v)]
    assert found == [], found


# ── 7: the idle stop time ───────────────────────────────────────────────────────

def test_the_idle_stop_time_is_written_through_settings(monkeypatch):
    from core import hive_idle
    from routers.settings import WRITABLE_KEYS
    assert hive_idle.SETTING in WRITABLE_KEYS
    assert hive_idle.idle_after_s(45) == 45 * 60 and hive_idle.idle_after_s(0) is None
