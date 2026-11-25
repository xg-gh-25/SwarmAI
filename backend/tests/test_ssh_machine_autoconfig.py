"""An SSH machine added with only its host (hive/ssh_provisioner; design
docs/design/2026-11-24-swarm-out-ssh-machine.md §5.3, the person's rule that SwarmAI sets
every Hive's settings itself).

The machine and AWS are recorders (tests/test_ssh_provisioner.Machine). Questions: (1) does an
install of a machine added with only its host, on a machine with no AWS profile of its own,
find the machine's own top-level directories, record them and start the container with them,
and give the machine its own identity (no profile of the machine is ever asked); (3) does the
mount rule leave out system directories, /tmp, the home's
ancestors and names the mount check refuses, on any layout; (4) are mounts given at add kept,
and does a body with only the host reach the provisioner as "find them"; (5) does an update
find the directories again?
"""
from __future__ import annotations

import asyncio
import json
import sqlite3

from hive import ssh_provisioner as sp
from tests import _ssh_machine_store as store
from tests.test_ssh_provisioner import VERSION, Machine, world  # noqa: F401 — the fixture

TOPDIRS = "/apollo:ro,/bin:ro,/home:ro,/local:ro,/tmp:rw,/var:ro,/workplace:rw,/docker.sock:rw"


class Found(Machine):
    """A machine whose preflight reports its top-level directories (and no AWS setup at all)."""

    def __init__(self, topdirs: str = TOPDIRS, fail: str = ""):
        super().__init__(fail)
        self.topdirs = topdirs

    async def run(self, host, remote, data=None, timeout=60, step="", tries=3):
        if step == "preflight":
            self.calls.append((step, remote, data or b""))
            return ("uid=1001\ngid=100\nuser=alice\nhome=/home/alice\narch=x86_64\ncores=4\nmem_gb=15\n"
                    "disk_free_gb=42\ndocker=ok\nport=free\nhive_container=no\n"
                    f"topdirs={self.topdirs}\nhome_real=/local/home/alice\n")
        return await super().run(host, remote, data, timeout, step)


def _add_host_only(world, machine) -> str:  # noqa: F811
    world["monkeypatch"].setattr(sp, "_run", machine.run)

    async def go():
        row = await sp.add("dev-dsk-alice-1a-0123abcd.us-west-2.amazon.com")
        await asyncio.gather(*list(sp._TASKS))
        return row["id"]
    return asyncio.run(go())


def _config(db, mid) -> dict:
    with sqlite3.connect(db) as c:
        return json.loads(c.execute("SELECT carrier_config FROM hive_instances WHERE id = ?", (mid,)).fetchone()[0])


def test_host_only_install_finds_mounts_and_needs_no_aws_profile(world):  # noqa: F811
    m = Found()
    mid = _add_host_only(world, m)
    assert store.status_of(world["db"], mid)[:2] == ("running", None)
    cfg = _config(world["db"], mid)
    assert cfg["mounts_auto"] is True and cfg["mounts"] == ["/apollo:ro", "/workplace"]
    # its own identity; no profile of the machine's is ever asked for an identity
    assert cfg["identity"] == "rolesanywhere" and "source_profile" not in cfg
    assert "identity" not in m.steps() and all("profiles" not in c[1] for c in m.calls)
    start = [c for c in m.calls if c[0] == "start"][-1][1]
    assert start.endswith(f"bash {sp._tag(VERSION)} /apollo:ro /workplace")


def test_mount_rule():
    facts = {"home_real": "/local/home/alice",
             "topdirs": ",".join(["/apollo:ro", "/data:rw", "/scratch:ro", "/srv:rw", "/opt:ro", "/tmp:rw",
                                  "/var:ro", "/run:ro", "/local:ro", "/home:ro", "/docker.sock:rw",
                                  "/odd$name:rw", "/a/b:rw", "/x:maybe", "relative:rw", ""])}
    assert sp._found_mounts(facts, "/home/alice") == ["/apollo:ro", "/data", "/scratch:ro"]
    # where a directory really is decides too: a link into a system directory, to / or to the
    # home's parent is not mounted; a link to a directory of the machine's own is
    linked = {"home_real": "/local/home/alice",
              "topdirs": "/apollo:ro:/local/apollo,/sock:rw:/var/run,/all:rw:/,/homes:rw:/local,/u:rw:/usr/share,/w:rw:/data/w"}
    assert sp._found_mounts(linked, "/home/alice") == ["/apollo:ro", "/w"]
    # a home directly under /: that directory is the home's ancestor, so it is not mounted
    assert sp._found_mounts({"topdirs": "/users:rw,/work:rw"}, "/users/alice") == ["/work"]
    # nothing reported: nothing mounted besides the home
    assert sp._found_mounts({}, "/home/alice") == []


def test_given_values_are_kept(world, monkeypatch):  # noqa: F811
    m = Found()
    world["monkeypatch"].setattr(sp, "_run", m.run)

    async def go():
        row = await sp.add("dev-dsk-bob-1a-0123abcd.us-west-2.amazon.com", mounts=("/work",))
        await asyncio.gather(*list(sp._TASKS))
        return row["id"]
    mid = asyncio.run(go())
    cfg = _config(world["db"], mid)
    assert cfg["mounts"] == ["/work"] and cfg["mounts_auto"] is False
    # through the route: a body with only the host reaches the provisioner as "find them"
    from routers import hive as route
    seen: dict = {}

    async def add(host, name, mounts, *rest):
        seen.update(host=host, mounts=mounts)
        return {}
    monkeypatch.setattr(sp, "add", add)
    monkeypatch.delenv("SWARMAI_MODE", raising=False)
    asyncio.run(route.ssh_add(route.SshMachineAdd(ssh_host="dev-dsk.example.com")))
    assert seen == {"host": "dev-dsk.example.com", "mounts": None}


def test_update_finds_mounts_again(world):  # noqa: F811
    m = Found()
    mid = _add_host_only(world, m)
    m.topdirs = TOPDIRS + ",/data:rw"
    asyncio.run(sp.claim_update(mid))
    asyncio.run(sp.update(mid, "2.5.6"))
    assert store.status_of(world["db"], mid)[:3] == ("running", None, "2.5.6")
    assert _config(world["db"], mid)["mounts"] == ["/apollo:ro", "/data", "/workplace"]
    swap = [c for c in m.calls if c[0] == "update" and "swarmai-hive-old" in c[1] and "docker run" in c[1]][-1][1]
    import re
    assert re.search(rf"bash {re.escape(sp._tag('2.5.6'))} [0-9a-f]{{16}} /apollo:ro /data /workplace$", swap), swap


def test_a_switch_turned_while_installing_is_kept(world):  # noqa: F811
    """The card's private-network switch, turned while the image builds, is not written back
    over by the install's own settings."""
    class Turned(Found):
        async def run(self, host, remote, data=None, timeout=60, step="", tries=3):
            if step == "image":
                with sqlite3.connect(world["db"]) as c:
                    hid = c.execute("SELECT id FROM hive_instances WHERE carrier = 'ssh'").fetchone()[0]
                await sp.set_options(hid, reaches_private=False)
            return await super().run(host, remote, data, timeout, step)
    m = Turned()
    mid = _add_host_only(world, m)
    cfg = _config(world["db"], mid)
    assert cfg["reaches_private"] is False and cfg["identity"] == "rolesanywhere"
    assert cfg["mounts"] == ["/apollo:ro", "/workplace"]


def test_failed_update_keeps_the_running_mounts(world):  # noqa: F811
    """An update whose new container does not come up runs the old one again: the recorded
    mounts stay the ones that old container has."""
    m = Found()
    mid = _add_host_only(world, m)
    m.topdirs = TOPDIRS + ",/data:rw"
    world["healthy"]["ok"] = False
    asyncio.run(sp.claim_update(mid))
    asyncio.run(sp.update(mid, "2.5.6"))
    assert "started again" in store.status_of(world["db"], mid)[1]
    assert _config(world["db"], mid)["mounts"] == ["/apollo:ro", "/workplace"]
