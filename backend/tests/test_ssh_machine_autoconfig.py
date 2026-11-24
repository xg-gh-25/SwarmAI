"""An SSH machine added with only its host (hive/ssh_provisioner; design
docs/design/2026-11-24-swarm-out-ssh-machine.md §5.3, the person's rule that SwarmAI sets
every Hive's settings itself).

The machine and AWS are recorders (tests/test_ssh_provisioner.Machine). Questions: (1) does an
install of a machine added with only its host find, on the machine, the AWS profile that is a
role of the SwarmAI account (asked in name order, skipping one SwarmAI added) and the machine's
own top-level directories, record both and start the container with them; (2) does a machine
with no such profile end in error naming the account and what was tried, before any role or
profile is made; (3) does the mount rule leave out system directories, /tmp, the home's
ancestors and names the mount check refuses, on any layout; (4) are values given at add kept,
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

OTHER = "999999999999"
TOPDIRS = "/apollo:ro,/bin:ro,/home:ro,/local:ro,/tmp:rw,/var:ro,/workplace:rw,/docker.sock:rw"


class Found(Machine):
    """A machine whose preflight reports profiles and top-level directories, and whose
    profiles each have an identity (an account id, or None: the profile cannot sign in)."""

    def __init__(self, identities: dict, topdirs: str = TOPDIRS, fail: str = ""):
        super().__init__(fail)
        # a profile SwarmAI added earlier: it is a role of the SwarmAI account too (the Hive's
        # own narrow role), and must never be taken as the source of a new one
        self.identities = {**identities, "swarmai-hive-old": store.ACCOUNT}
        self.topdirs = topdirs

    async def run(self, host, remote, data=None, timeout=60, step=""):
        if step == "preflight":
            self.calls.append((step, remote, data or b""))
            return ("uid=1001\ngid=100\nuser=alice\nhome=/home/alice\narch=x86_64\ncores=4\nmem_gb=15\n"
                    "disk_free_gb=42\ndocker=ok\nport=free\nhive_container=no\n"
                    f"profiles={','.join(self.identities)}\n"
                    f"topdirs={self.topdirs}\nhome_real=/local/home/alice\n")
        if step == "identity":
            self.calls.append((step, remote, data or b""))
            profile = remote.rsplit(" ", 1)[-1].strip("'")
            acct = self.identities.get(profile)
            if acct is None:
                raise sp.SshInstallError(f"identity: {profile} cannot sign in")
            role = "Admin"
            if isinstance(acct, tuple):                       # (account, role name)
                acct, role = acct
            return json.dumps(f"arn:aws:sts::{acct}:assumed-role/{role}/alice-Isengard") + "\n"
        return await super().run(host, remote, data, timeout, step)

    def asked(self) -> list[str]:
        return [c[1].rsplit(" ", 1)[-1].strip("'") for c in self.calls if c[0] == "identity"]


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


def test_host_only_install_finds_profile_and_mounts(world):  # noqa: F811
    m = Found({"default": OTHER, "legacy": None, "swarmai": store.ACCOUNT, "zeta": store.ACCOUNT})
    mid = _add_host_only(world, m)
    assert store.status_of(world["db"], mid)[:2] == ("running", None)
    # profiles in name order until one is a role of the SwarmAI account; SwarmAI's own skipped
    assert m.asked() == ["default", "legacy", "swarmai"]
    cfg = _config(world["db"], mid)
    assert cfg["source_profile"] == "swarmai" and cfg["mounts_auto"] is True
    assert cfg["mounts"] == ["/apollo:ro", "/workplace"]
    # the Hive's profile is sourced from it, the container starts with the found directories
    assert "swarmai" in next(c for c in m.calls if c[0] == "profile")[1]
    start = [c for c in m.calls if c[0] == "start"][-1][1]
    assert start.endswith(f"bash {VERSION} /apollo:ro /workplace")


def test_no_profile_reaches_the_account(world):  # noqa: F811
    m = Found({"default": OTHER, "legacy": None})
    mid = _add_host_only(world, m)
    status, error, _, _ = store.status_of(world["db"], mid)
    assert status == "error" and store.ACCOUNT in error and "default, legacy" in error
    assert m.asked() == ["default", "legacy"]                 # SwarmAI's own profile is never asked
    # nothing was made: no role, no profile written on the machine
    assert world["roles"] == [] and "profile" not in m.steps()


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
    m = Found({"default": store.ACCOUNT, "swarmai": store.ACCOUNT})
    world["monkeypatch"].setattr(sp, "_run", m.run)

    async def go():
        row = await sp.add("dev-dsk-bob-1a-0123abcd.us-west-2.amazon.com", "swarmai", mounts=("/work",))
        await asyncio.gather(*list(sp._TASKS))
        return row["id"]
    mid = asyncio.run(go())
    cfg = _config(world["db"], mid)
    assert cfg["source_profile"] == "swarmai" and cfg["mounts"] == ["/work"] and cfg["mounts_auto"] is False
    assert m.asked()[0] == "swarmai"
    # through the route: a body with only the host reaches the provisioner as "find them"
    from routers import hive as route
    seen: dict = {}

    async def add(host, profile, name, mounts, *rest):
        seen.update(host=host, profile=profile, mounts=mounts)
        return {}
    monkeypatch.setattr(sp, "add", add)
    monkeypatch.delenv("SWARMAI_MODE", raising=False)
    asyncio.run(route.ssh_add(route.SshMachineAdd(ssh_host="dev-dsk.example.com")))
    assert seen == {"host": "dev-dsk.example.com", "profile": "", "mounts": None}


def test_update_finds_mounts_again(world):  # noqa: F811
    m = Found({"swarmai": store.ACCOUNT})
    mid = _add_host_only(world, m)
    m.topdirs = TOPDIRS + ",/data:rw"
    asyncio.run(sp.claim_update(mid))
    asyncio.run(sp.update(mid, "2.5.6"))
    assert store.status_of(world["db"], mid)[:3] == ("running", None, "2.5.6")
    assert _config(world["db"], mid)["mounts"] == ["/apollo:ro", "/data", "/workplace"]
    swap = [c for c in m.calls if c[0] == "update" and "swarmai-hive-old" in c[1] and "docker run" in c[1]][-1][1]
    assert swap.endswith("bash 2.5.6 /apollo:ro /data /workplace")


def test_a_hives_own_role_is_never_the_source(world):  # noqa: F811
    """A profile under any name whose identity is a Hive's narrow role (SwarmAI-Hive-*) is
    skipped: the new Hive's role would trust it, and that Hive's tasks could assume this one."""
    m = Found({"a-copy": (store.ACCOUNT, "SwarmAI-Hive-other"), "swarmai": store.ACCOUNT})
    mid = _add_host_only(world, m)
    assert _config(world["db"], mid)["source_profile"] == "swarmai" and m.asked() == ["a-copy", "swarmai"]


def test_a_switch_turned_while_installing_is_kept(world):  # noqa: F811
    """The card's private-network switch, turned while the image builds, is not written back
    over by the install's own settings."""
    class Turned(Found):
        async def run(self, host, remote, data=None, timeout=60, step=""):
            if step == "image":
                with sqlite3.connect(world["db"]) as c:
                    hid = c.execute("SELECT id FROM hive_instances WHERE carrier = 'ssh'").fetchone()[0]
                await sp.set_options(hid, reaches_private=False)
            return await super().run(host, remote, data, timeout, step)
    m = Turned({"swarmai": store.ACCOUNT})
    mid = _add_host_only(world, m)
    cfg = _config(world["db"], mid)
    assert cfg["reaches_private"] is False and cfg["source_profile"] == "swarmai"
    assert cfg["mounts"] == ["/apollo:ro", "/workplace"]


def test_failed_update_keeps_the_running_mounts(world):  # noqa: F811
    """An update whose new container does not come up runs the old one again: the recorded
    mounts stay the ones that old container has."""
    m = Found({"swarmai": store.ACCOUNT})
    mid = _add_host_only(world, m)
    m.topdirs = TOPDIRS + ",/data:rw"
    world["healthy"]["ok"] = False
    asyncio.run(sp.claim_update(mid))
    asyncio.run(sp.update(mid, "2.5.6"))
    assert "started again" in store.status_of(world["db"], mid)[1]
    assert _config(world["db"], mid)["mounts"] == ["/apollo:ro", "/workplace"]
