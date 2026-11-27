"""An SSH machine whose tunnel cannot be built, and this computer's own login certificate
(core/carriers/ssh_tunnel build_failed, core/carriers/ssh SshLocation._admission,
routers/hive ssh_machines and ssh_options, hive/ssh_provisioner set_options).

Questions: (1) once a tunnel build failed, is a probe of the machine an answer (unreachable,
with ssh's error in the list) rather than 'still starting' for good, while a first build and
a build after a success are still 'starting'; (2) does the list return the end of THIS
computer's SSH certificate; (3) is the command that renews it stored per machine, shown in
its config, and refused when it is not one plain short line?
"""
from __future__ import annotations

import asyncio

import pytest

from tests import _ssh_machine_store as store


@pytest.fixture
def machine(tmp_path, monkeypatch):
    from core.carriers import ssh, ssh_tunnel
    db = store.make(tmp_path, monkeypatch)
    store.add(db, "s1", "dev-dsk", carrier="ssh", carrier_config=store.ssh_config(28411))
    monkeypatch.setattr("hive.accounts.swarmai_account", lambda: {"account_id": store.ACCOUNT, "region": "us-east-1"})
    monkeypatch.setattr(ssh_tunnel, "alive", lambda lid, port: False)
    soon: list = []
    monkeypatch.setattr(ssh_tunnel, "ensure_soon", lambda lid, *a: soon.append(lid))
    ssh_tunnel._FAILED.clear()
    ssh._LAST.clear()
    yield soon
    ssh_tunnel._FAILED.clear()
    ssh._LAST.clear()


def _probe():
    from core.carriers import ssh
    got = asyncio.run(ssh.SshLocation("s1", "dev-dsk").admission(build=False))
    return got, ssh.last_admission("s1")


def test_a_first_build_is_still_starting(machine):
    got, last = _probe()
    assert got is None and last[2] is True, "no build failed yet: still starting"
    assert machine == ["s1"], "the tunnel is started in the background"


def test_a_failed_build_makes_the_probe_an_unreachable_answer(machine, monkeypatch):
    from core.carriers import ssh_tunnel
    from routers import hive as route

    async def refused(*a, **k):
        raise ssh_tunnel.TunnelError("the SSH tunnel to dev-dsk did not start: Permission denied (publickey).")
    monkeypatch.setattr(ssh_tunnel, "_ensure", refused)
    with pytest.raises(ssh_tunnel.TunnelError):
        asyncio.run(ssh_tunnel.ensure("s1", "dev-dsk.example.com", 28411, 18321))
    assert ssh_tunnel.build_failed("s1")
    got, last = _probe()
    assert got is None and last[2] is False, "a failed build makes the probe an answer"
    assert machine == ["s1"], "and it still retries in the background"
    # the list now says unreachable, with ssh's own line
    monkeypatch.setattr(route, "_require_desktop", lambda: None)
    monkeypatch.setattr(ssh_tunnel, "last_error", lambda lid: "Permission denied (publickey).")
    monkeypatch.setattr("core.carriers.ssh.probe_soon", lambda *a: None)
    item = asyncio.run(route.ssh_machines())["machines"][0]
    assert item["reachable"] is False and "Permission denied" in item["tunnel_error"]


def test_a_build_that_comes_up_clears_the_failure(machine, monkeypatch):
    from core.carriers import ssh_tunnel
    ssh_tunnel._FAILED["s1"] = 1.0

    async def up(*a, **k):
        return "http://127.0.0.1:28411"
    monkeypatch.setattr(ssh_tunnel, "_ensure", up)
    assert asyncio.run(ssh_tunnel.ensure("s1", "dev-dsk.example.com", 28411, 18321)).endswith(":28411")
    assert not ssh_tunnel.build_failed("s1")
    _, last = _probe()
    assert last[2] is True, "after a success, a down tunnel is first 'starting' again"


def test_the_list_returns_this_computers_certificate_end(machine, monkeypatch):
    from core import hive_ssh_mode
    from routers import hive as route
    monkeypatch.setattr(route, "_require_desktop", lambda: None)
    monkeypatch.setattr("core.carriers.ssh.probe_soon", lambda *a: None)
    monkeypatch.setattr(hive_ssh_mode, "cert_valid_until", lambda home=None: 1764207573.0)
    assert asyncio.run(route.ssh_machines())["host_cert_valid_until"] == 1764207573.0
    monkeypatch.setattr(hive_ssh_mode, "cert_valid_until", lambda home=None: None)
    assert asyncio.run(route.ssh_machines())["host_cert_valid_until"] is None


def test_login_renew_hint_is_stored_and_shown(machine, monkeypatch):
    from routers import hive as route
    monkeypatch.setattr(route, "_require_desktop", lambda: None)
    monkeypatch.setattr("core.carriers.ssh.probe_soon", lambda *a: None)
    before = asyncio.run(route.ssh_machines())["machines"][0]["config"]["cert_renew_hint"]
    got = asyncio.run(route.ssh_options("s1", route.SshMachineOptions(login_renew_hint="mwinit")))
    assert got["config"]["login_renew_hint"] == "mwinit"
    assert got["config"]["cert_renew_hint"] == before, "the machine's own renew command is untouched"
    item = asyncio.run(route.ssh_machines())["machines"][0]
    assert item["config"]["login_renew_hint"] == "mwinit"


@pytest.mark.parametrize("bad", ["mwinit\nrm -rf ~", "echo $(id)", "a`b`", "x\\y", "m" * 121])
def test_a_bad_login_renew_hint_is_refused(machine, monkeypatch, bad):
    from fastapi import HTTPException
    from hive import ssh_provisioner
    from routers import hive as route
    monkeypatch.setattr(route, "_require_desktop", lambda: None)
    with pytest.raises((ValueError, HTTPException)):
        if len(bad) > 120:
            route.SshMachineOptions(login_renew_hint=bad)
        else:
            asyncio.run(ssh_provisioner.set_options("s1", login_renew_hint=bad))
    item = asyncio.run(route.ssh_machines())["machines"][0]
    assert not item["config"].get("login_renew_hint")


def test_stopping_a_tunnel_forgets_its_failed_build(machine):
    from core.carriers import ssh_tunnel
    ssh_tunnel._FAILED["s1"] = 1.0
    ssh_tunnel.stop("s1")
    assert not ssh_tunnel.build_failed("s1")
    _, last = _probe()
    assert last[2] is True, "a reinstalled machine's first build is 'starting' again"
