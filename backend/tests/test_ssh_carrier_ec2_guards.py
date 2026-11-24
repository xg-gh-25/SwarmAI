"""An SSH machine's row is never taken for an EC2 Hive (design
docs/design/2026-11-24-swarm-out-ssh-machine.md §6, §13 invariant 1).

Question: with an SSH machine's row ``running`` next to an EC2 Hive's, does every EC2-only
path (the EC2 carrier's offers and timer list, the idle stop and update, the Hive count, the
stale-update release) act on the EC2 Hive alone and leave the SSH row as it is?
"""
from __future__ import annotations

import asyncio

from tests import _ssh_machine_store as store


def test_ec2_paths_leave_ssh_row_alone(tmp_path, monkeypatch):
    from core import hive_idle
    from core.carriers import ec2
    from hive import accounts
    from hive.provisioner import HiveProvisioner
    db = store.make(tmp_path, monkeypatch)
    store.add(db, "e1", "ec2-one")
    store.add(db, "s1", "dev-dsk", carrier="ssh", carrier_config=store.ssh_config())
    store.add(db, "a1", "elastic", carrier="agentcore", status="ready")

    # the EC2 carrier lists and visits only its own Hives
    assert [r["id"] for r in asyncio.run(ec2._hive_rows())] == ["e1"]
    assert [loc.id for loc in asyncio.run(ec2.Ec2Carrier().running())] == ["e1"]
    # the idle stop and the follow-host update of EC2 Hives look only at EC2 rows
    assert [h["id"] for h in asyncio.run(hive_idle._running_hives())] == ["e1"]

    # a full idle round never claims, stops or updates the SSH row through the EC2 provisioner
    touched: list[str] = []

    class Prov:
        async def claim_update(self, hid):
            touched.append(f"claim {hid}")

        async def claim_stop(self, hid):
            touched.append(f"stop {hid}")

        async def update(self, hid, *a, **k):
            touched.append(f"update {hid}")
    monkeypatch.setattr(hive_idle, "_provisioner", lambda: Prov())
    monkeypatch.setattr(hive_idle, "_claim", lambda prov, h: _record(touched, f"claim-stop {h['id']}"))
    monkeypatch.setattr(hive_idle, "_sessions", lambda hid: _none())
    monkeypatch.setattr(hive_idle, "_host_version", lambda: "9.9.9")
    monkeypatch.setattr(hive_idle, "_follow_other_carriers", lambda: _empty())
    asyncio.run(hive_idle.check_once())
    assert all("s1" not in t for t in touched), touched

    # the Hive count (MAX_HIVES) is of EC2 Hives
    import sqlite3
    with sqlite3.connect(db) as c:
        n = c.execute(f"SELECT COUNT(*) FROM hive_instances WHERE {accounts.COUNTED_SQL}").fetchone()[0]
    assert n == 1

    # an SSH machine's update in progress keeps its heartbeat fresh: the stale-update
    # release (written for EC2 updates SwarmAI stopped in the middle) leaves it alone
    with sqlite3.connect(db) as c:
        c.execute("UPDATE hive_instances SET status = 'updating', updated_at = datetime('now') WHERE id = 's1'")
    asyncio.run(HiveProvisioner(db).release_stale_updates())
    assert store.status_of(db, "s1")[0] == "updating"


async def _record(touched, what):
    touched.append(what)
    return False


async def _none():
    return None


async def _empty():
    return []
