"""The SSH carrier's offers and where placement puts tasks (design
docs/design/2026-11-24-swarm-out-ssh-machine.md §4.2-§4.4, §13 invariants 5, 7, 8).

Questions: (1) does a running SSH machine get one warm offer that carries its private reach,
and the right blocked reason (no tunnel, expired certificate, no certificate, version, busy,
narrowed to private tasks); (2) does a machine that does not answer lose only its own offers,
so EC2 and Elastic Workers offers are still placed; (3) does placement keep a private task to
a location that reaches the private network, let a general task go to the machine by load,
and still start a stopped EC2 Hive when the SSH machine is blocked?
"""
from __future__ import annotations

import asyncio
import time

from core.carriers.base import EC2, SSH, TIER_COLD, TIER_WARM, Needs, Offer
from core.carriers.placement import place
from tests import _ssh_machine_store as store

GOOD = {"streaming": 0, "saturated": False, "memory_percent": 20.0, "capable": True, "cert_valid_until": None}


def test_offer_and_blocked_reasons(tmp_path, monkeypatch):
    from core.carriers import ssh, ssh_tunnel
    db = store.make(tmp_path, monkeypatch)
    store.add(db, "s1", "dev-dsk", carrier="ssh", carrier_config=store.ssh_config(28411))
    store.add(db, "s2", "lab-box", carrier="ssh", carrier_config=store.ssh_config(28412, reaches_private=False))
    store.add(db, "s3", "off-box", carrier="ssh", status="error", carrier_config=store.ssh_config(28413))
    monkeypatch.setattr("hive.accounts.swarmai_account", lambda: {"account_id": store.ACCOUNT, "region": "us-east-1"})
    probes: dict = {"s1": dict(GOOD), "s2": None}
    built: list = []

    async def admission(self, build=True):
        assert build is False                       # offers only look
        return probes[self.id]
    monkeypatch.setattr(ssh.SshLocation, "admission", admission)
    monkeypatch.setattr(ssh_tunnel, "ensure_soon", lambda lid, *a: built.append(lid))
    offers = {o.location_id: o for o in asyncio.run(ssh.SshCarrier().offers(Needs()))}
    assert set(offers) == {"s1", "s2"}                                          # a machine in error is not offered
    assert offers["s1"].tier == TIER_WARM and offers["s1"].reaches_private and offers["s1"].blocked is None
    assert offers["s2"].blocked == ssh.UNREACHABLE and not offers["s2"].reaches_private and built == ["s2"]

    now = time.time()
    cfg = ssh.config({"carrier_config": store.ssh_config()})
    assert ssh.blocked(dict(GOOD, cert_valid_until=now - 1), cfg, Needs()) == ssh.CERT_EXPIRED
    assert ssh.blocked(dict(GOOD, cert_valid_until=now + 600), cfg, Needs()) is None
    assert ssh.blocked(dict(GOOD, cert_valid_until=None), cfg, Needs()) is None      # no certificate: not blocked
    assert ssh.blocked(dict(GOOD, capable=False), cfg, Needs()) == ssh.OUT_OF_DATE
    assert ssh.blocked(dict(GOOD, saturated=True), cfg, Needs()) == ssh.SATURATED
    narrow = ssh.config({"carrier_config": store.ssh_config(general_tasks=False)})
    assert ssh.blocked(dict(GOOD), narrow, Needs()) == ssh.NOT_GENERAL
    assert ssh.blocked(dict(GOOD), narrow, Needs(private=("svc is an internal repository",))) is None
    assert ssh.blocked(dict(GOOD), narrow, Needs(named="dev-dsk")) is None
    # a named machine gets its one offer unprobed
    named = asyncio.run(ssh.SshCarrier().offers(Needs(named="dev-dsk")))
    assert [o.location_id for o in named] == ["s1"]


def test_slow_ssh_carrier_does_not_drop_other_offers(monkeypatch):
    from core import swarm_out
    from core.carriers import registry

    class Fast:
        kind = EC2

        async def offers(self, needs):
            return [Offer(EC2, "e1", "ec2-one", TIER_WARM)]

    class Slow:
        kind = SSH

        async def offers(self, needs):
            await asyncio.sleep(5)
            return [Offer(SSH, "s1", "dev-dsk", TIER_WARM)]
    monkeypatch.setattr(registry, "carriers", lambda: [Fast(), Slow()])
    monkeypatch.setattr(swarm_out, "OFFERS_TIMEOUT_S", 0.2)
    offers, unread = asyncio.run(swarm_out._offers(Needs()))
    assert [o.location_id for o in offers] == ["e1"]
    assert unread == ["ssh: offers unreadable (TimeoutError)"]


def _o(carrier, lid, tier=TIER_WARM, private=False, blocked=None, open_runs=0):
    return Offer(carrier, lid, lid, tier, open_runs=open_runs, reaches_private=private, blocked=blocked)


def test_placement_with_ssh_offers():
    ec2_busy = _o(EC2, "ec2-one", open_runs=2)
    machine = _o(SSH, "dev-dsk", private=True)
    # a private task goes only where the private network is reached
    p = place(Needs(private=("svc is an internal repository",)), [ec2_busy, machine])
    assert p.offer.location_id == "dev-dsk" and ("ec2-one", "private") in p.skipped
    # nothing reaches it: no offer, every location's reason kept
    p = place(Needs(private=("x",)), [ec2_busy, _o(SSH, "dev-dsk", private=True, blocked="cert_expired")])
    assert p.offer is None and ("dev-dsk", "cert_expired") in p.skipped
    # a general task: by load, the idle machine before a busy EC2 Hive
    assert place(Needs(), [ec2_busy, machine]).offer.location_id == "dev-dsk"
    # the SSH machine blocked does not keep a stopped EC2 Hive from starting (D5 compares a
    # stopped Hive with the running ones of its own carrier)
    stopped = _o(EC2, "ec2-cold", tier=TIER_COLD)
    for why in ("cert_expired", "not_general", "unreachable"):
        p = place(Needs(), [_o(SSH, "dev-dsk", private=True, blocked=why), stopped])
        assert p.offer is not None and p.offer.location_id == "ec2-cold", why
    # an EC2 Hive out of date still keeps the stopped one from being started, as before
    p = place(Needs(), [_o(EC2, "ec2-one", blocked="out_of_date"), stopped])
    assert p.offer is None


def test_open_run_is_unavailable_when_the_tunnel_or_certificate_fails(tmp_path, monkeypatch):
    """Opening a run is LocationUnavailable (the next candidate is tried) when the tunnel
    cannot be built or the machine's certificate has ended, before anything is written."""
    import pytest

    from core import swarm_out
    from core.carriers import ssh, ssh_tunnel
    from core.carriers.base import LocationUnavailable
    db = store.make(tmp_path, monkeypatch)
    store.add(db, "s1", "dev-dsk", carrier="ssh", carrier_config=store.ssh_config(28411))

    async def no_tunnel(*a, **k):
        raise ssh_tunnel.TunnelError("the SSH tunnel to dev-dsk did not start: Permission denied (publickey)")
    monkeypatch.setattr(ssh_tunnel, "ensure", no_tunnel)
    with pytest.raises(LocationUnavailable) as e:
        asyncio.run(ssh.SshLocation("s1").open_run("so_aaaaaaaaaaaa", 1))
    assert "Permission denied" in str(e.value)

    async def up(*a, **k):
        return "http://127.0.0.1:28411"
    monkeypatch.setattr(ssh_tunnel, "ensure", up)

    async def expired(inst, needs=None):
        return {"work_orders": True, "fixed_reserved": True, "zero_touch": True, "saturated": False,
                "ssh_cert_valid_until": time.time() - 60}
    monkeypatch.setattr(swarm_out, "_require_work_orders", expired)
    with pytest.raises(LocationUnavailable) as e:
        asyncio.run(ssh.SshLocation("s1").open_run("so_aaaaaaaaaaaa", 1))
    assert getattr(e.value, "why", None) == ssh.CERT_EXPIRED

    async def fine(inst, needs=None):
        return {"work_orders": True, "fixed_reserved": True, "zero_touch": True, "saturated": False,
                "ssh_cert_valid_until": time.time() + 3600}
    monkeypatch.setattr(swarm_out, "_require_work_orders", fine)
    h = asyncio.run(ssh.SshLocation("s1").open_run("so_aaaaaaaaaaaa", 1))
    assert h.carrier == SSH and h.location_id == "s1" and h.address is None and h.auth_ref == "hive_instances:s1"


def test_fleet_probe_never_waits_for_a_tunnel(tmp_path, monkeypatch):
    """The fleet's probe of an SSH machine whose tunnel is not up answers None at once and
    starts the tunnel in the background; it never waits for ssh (a dead machine would hold
    every fleet refresh for the tunnel's wait)."""
    from core import swarm_out
    from core.carriers import ssh_tunnel
    db = store.make(tmp_path, monkeypatch)
    store.add(db, "s1", "dev-dsk", carrier="ssh", carrier_config=store.ssh_config(28411))

    async def no_wait(*a, **k):
        raise AssertionError("the probe waited for the tunnel")
    soon: list = []
    monkeypatch.setattr(ssh_tunnel, "ensure", no_wait)
    monkeypatch.setattr(ssh_tunnel, "alive", lambda lid, port: False)
    monkeypatch.setattr(ssh_tunnel, "ensure_soon", lambda lid, *a: soon.append(lid))
    assert asyncio.run(swarm_out._admission("s1")) is None and soon == ["s1"]
