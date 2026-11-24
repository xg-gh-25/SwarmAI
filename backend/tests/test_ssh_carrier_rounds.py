"""How the Host reaches an SSH machine and its runs (design
docs/design/2026-11-24-swarm-out-ssh-machine.md §4.5, §6, §13 invariants 2-4).

Questions: (1) does every per-machine caller (questions, answer bridge, admission probe, Brain
sync, usage sync) get the SSH machine's own location, an EC2 Hive's the EC2 one, and an
Elastic Workers row neither; (2) does a request's address come only from the location code,
never from a stored value, and is the SSH client's address found again at each use; (3) is a
round on an SSH machine written into the dispatch row and read back as an SSH round, while a
row without rounds still reads as an EC2 Hive's?
"""
from __future__ import annotations

import asyncio
import json

import pytest

from tests import _ssh_machine_store as store


@pytest.fixture
def machines(tmp_path, monkeypatch):
    db = store.make(tmp_path, monkeypatch)
    store.add(db, "e1", "ec2-one")
    store.add(db, "s1", "dev-dsk", carrier="ssh", carrier_config=store.ssh_config(28411))
    store.add(db, "a1", "elastic", carrier="agentcore", status="ready")
    from core.carriers import ssh_tunnel

    async def up(*a, **k):
        return "http://127.0.0.1:28411"
    monkeypatch.setattr(ssh_tunnel, "ensure", up)
    return db


def test_per_machine_callers_resolve_by_carrier(machines, monkeypatch):
    from core import brain_sync_host, swarm_out, usage_hive_sync
    from core.carriers import registry
    from core.carriers.base import BRAIN_SYNC, USAGE_SYNC
    from core.carriers.ec2 import Ec2Location
    from core.carriers.ssh import SshLocation
    assert isinstance(asyncio.run(swarm_out._machine("s1")), SshLocation)
    assert isinstance(asyncio.run(swarm_out._machine("dev-dsk")), SshLocation)        # by name too
    assert isinstance(asyncio.run(swarm_out._machine("e1")), Ec2Location)
    assert isinstance(asyncio.run(swarm_out._machine("a1")), Ec2Location)          # refused there, as before
    assert isinstance(asyncio.run(registry.location_of("s1", USAGE_SYNC)), SshLocation)
    with pytest.raises(swarm_out.SwarmOutError):
        asyncio.run(registry.location_of("a1", BRAIN_SYNC))                        # keeps no Brains
    # Brain sync reaches the SSH machine through its own record (its tunnel's address)
    rec = asyncio.run(brain_sync_host.hive_of("s1"))
    assert rec["_base_url"] == "http://127.0.0.1:28411" and rec["carrier"] == "ssh"
    # the questions list and the answer bridge read the machine's bucket under its name
    seen: list = []
    monkeypatch.setattr(swarm_out, "_s3", lambda inst, purpose="manage": seen.append((inst["name"], inst["s3_bucket"])) or object())
    s3, inst, name = asyncio.run(swarm_out._wait_bridge("s1", "sess-x"))
    assert name == "dev-dsk" and seen == [("dev-dsk", store.BUCKET)]
    # the admission probe asks the SSH machine, through its tunnel
    asked: list = []

    async def admission(self, build=True):
        asked.append(self.id)
        return {"streaming": 0, "saturated": False, "memory_percent": 10.0, "capable": True,
                "cert_valid_until": None}
    monkeypatch.setattr(SshLocation, "admission", admission)
    assert asyncio.run(swarm_out._admission("s1"))["capable"] is True and asked == ["s1"]
    # usage sync visits the SSH machine as a location of its carrier
    from core.carriers.ssh import SshCarrier
    assert [loc.id for loc in asyncio.run(SshCarrier().running())] == ["s1"]
    assert usage_hive_sync  # imported: its _pull goes through registry.location_of (above)


def test_base_url_only_from_location(machines, monkeypatch):
    import sqlite3

    from core import swarm_out
    from core.carriers.base import SSH, RunHandle
    from core.carriers.ssh import SshCarrier
    # a stored record has no address of its own: the CloudFront domain is the only one used
    row, acct = asyncio.run(swarm_out._row_and_account("e1"))
    assert "_base_url" not in row
    assert swarm_out._base(swarm_out._validate_instance(row, acct)) == "https://ec2-one.cloudfront.net"
    # an SSH machine's address is its tunnel's local end, put there by its location only
    rec = asyncio.run(SshCarrier().connect(RunHandle(carrier=SSH, location_id="s1", round=1, run_ref=None)))
    assert swarm_out._base(rec) == "http://127.0.0.1:28411"
    # the client of an SSH round finds the address when it is opened: a changed setting is used
    with sqlite3.connect(machines) as c:
        c.execute("UPDATE hive_instances SET carrier_config = ? WHERE id = 's1'", (json.dumps(store.ssh_config(28499)),))

    async def base():
        async with SshCarrier().client(RunHandle(carrier=SSH, location_id="s1", round=1, run_ref=None)) as c:
            return str(c.base_url)
    assert asyncio.run(base()).rstrip("/") == "http://127.0.0.1:28499"
    # a handle of another carrier is refused before any request
    with pytest.raises(ValueError):
        SshCarrier().client(RunHandle(carrier="ec2", location_id="e1", round=1, run_ref=None))


def test_ssh_round_written_and_read(machines, monkeypatch, tmp_path):
    from core import swarm_out
    from core.carriers import registry
    from core.carriers.base import EC2, SSH, RunHandle
    from core.carriers.ssh import SshLocation
    rec = asyncio.run(SshLocation("s1").hive())
    lines: list[str] = []
    monkeypatch.setattr(swarm_out, "_append", lines.append)
    opened = RunHandle(carrier=SSH, location_id="s1", round=1, run_ref=None, auth_ref="hive_instances:s1")
    swarm_out._record_maybe_sent(None, rec, "T", "swarm-out", [], None, "so_aaaaaaaaaaaa", opened)
    row = json.loads(lines[-1])
    assert row["rounds"][0]["carrier"] == SSH and registry.handle_of(row).carrier == SSH
    # a dispatch on its own to an SSH machine (no placement): its round is still an SSH one
    swarm_out._record_maybe_sent(None, rec, "T", "swarm-out", [], None, "so_bbbbbbbbbbbb", None)
    assert json.loads(lines[-1])["rounds"][0]["carrier"] == SSH
    assert swarm_out._first_round(None, rec, "sess-1").carrier == SSH
    # an EC2 Hive's maybe-sent row stays as before (no rounds: read as its Hive's round)
    row_e, acct = asyncio.run(swarm_out._row_and_account("e1"))
    ec2_rec = swarm_out._validate_instance(row_e, acct)
    swarm_out._record_maybe_sent(None, ec2_rec, "T", "swarm-out", [], None, "so_cccccccccccc",
                                 RunHandle(carrier=EC2, location_id="e1", round=1, run_ref=None))
    legacy = json.loads(lines[-1])
    assert "rounds" not in legacy and registry.handle_of(legacy).carrier == EC2
    assert swarm_out._first_round(None, ec2_rec, "sess-2").address == "ec2-one.cloudfront.net"
