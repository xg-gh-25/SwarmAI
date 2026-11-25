"""Swarm Out › Settings answers from local data (routers/hive.ssh_machines, core/carriers/ssh
last_admission / probe_soon).

Questions: (1) does the SSH machines list answer at once while a machine's probe hangs, with
that machine not known yet, and start one background probe of it (not two); (2) once the
probe answered, does the list show its result without probing again; (3) does a last probe
older than PROBE_FRESH_S start a new one in the background while the list still answers with
the last result, and does a probe that fails then read as unreachable with ssh's last error;
(4) is a tunnel still being started shown as not known yet and asked again after
PROBE_RETRY_S, never as unreachable?
"""
from __future__ import annotations

import asyncio
import time

from core.carriers import ssh
from tests import _ssh_machine_store as store


def test_the_ssh_list_answers_from_the_last_probe(tmp_path, monkeypatch):
    from routers import hive as route
    db = store.make(tmp_path, monkeypatch)
    store.add(db, "s1", "dev-dsk", carrier="ssh", carrier_config=store.ssh_config())
    monkeypatch.delenv("SWARMAI_MODE", raising=False)
    ssh._LAST.clear()
    ssh._PROBING.clear()
    monkeypatch.setattr("core.carriers.ssh_tunnel.last_error", lambda lid: "Connection to UNKNOWN port 65535 timed out")
    calls: list = []
    gate = {"answer": {"capable": True, "cert_valid_until": 1798172855.0}}

    async def admission(self, build):
        calls.append(self.id)
        await gate["release"].wait()
        return gate["answer"]
    monkeypatch.setattr(ssh.SshLocation, "_admission", admission)

    async def go():
        gate["release"] = asyncio.Event()
        # (1) the probe hangs: the list answers at once, the machine not known yet
        (m,) = (await asyncio.wait_for(route.ssh_machines(), 1))["machines"]
        assert m["reachable"] is None and m["cert_valid_until"] is None
        (m,) = (await asyncio.wait_for(route.ssh_machines(), 1))["machines"]
        await asyncio.sleep(0)
        assert calls == ["s1"]                                    # one background probe, not two
        # (2) it answers: the list shows it, and probes no more while it is fresh
        gate["release"].set()
        await ssh._PROBING["s1"]
        (m,) = (await route.ssh_machines())["machines"]
        assert m["reachable"] is True and m["cert_valid_until"] == 1798172855.0 and m["tunnel_error"] == ""
        assert calls == ["s1"]
        # (3) stale: the last result is shown while a new probe runs; a failed one reads unreachable
        at, probe, starting = ssh._LAST["s1"]
        ssh._LAST["s1"] = (time.time() - ssh.PROBE_FRESH_S - 1, probe, starting)
        gate["answer"] = None
        (m,) = (await route.ssh_machines())["machines"]
        assert m["reachable"] is True
        await ssh._PROBING["s1"]
        assert calls == ["s1", "s1"]
        (m,) = (await route.ssh_machines())["machines"]
        assert m["reachable"] is False and "timed out" in m["tunnel_error"]
        # (4) a tunnel still being started is not an answer: not known yet, asked again soon
        ssh._LAST["s1"] = (time.time() - ssh.PROBE_RETRY_S - 1, None, True)
        (m,) = (await route.ssh_machines())["machines"]
        assert m["reachable"] is None and m["tunnel_error"] == ""
        await ssh._PROBING["s1"]
        assert calls == ["s1", "s1", "s1"]
    asyncio.run(go())


def test_a_tunnel_being_started_is_recorded_as_no_answer_yet(tmp_path, monkeypatch):
    """The probe that finds the tunnel down starts it and records that it is not known yet."""
    db = store.make(tmp_path, monkeypatch)
    store.add(db, "s1", "dev-dsk", carrier="ssh", carrier_config=store.ssh_config())
    ssh._LAST.clear()
    started: list = []
    monkeypatch.setattr("core.carriers.ssh_tunnel.alive", lambda lid, port: False)
    monkeypatch.setattr("core.carriers.ssh_tunnel.ensure_soon", lambda *a: started.append(a[0]))
    assert asyncio.run(ssh.SshLocation("s1", "dev-dsk").admission()) is None
    assert started == ["s1"] and ssh._LAST["s1"][1:] == (None, True)
