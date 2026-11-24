"""Installing, updating and removing the Hive on an SSH machine (hive/ssh_provisioner; design
docs/design/2026-11-24-swarm-out-ssh-machine.md §5.3-§5.4, §13 invariant 11).

The machine and AWS are replaced by recorders: every ssh call's script, arguments and stdin, and
every role call, are kept. Questions: (1) does an install run its steps in order, send the
Dockerfile with a package checked against its checksums.txt, give the machine only the
password's hash (never the password, never on a command line), add one profile, check the
assumed role and end 'running'; (2) is a package that does not match refused; (3) does a
failed step end 'error' with the step's name, and a retry run again to 'running'; (4) does an
update whose new container does not come up start the old one again, keep a fresh heartbeat
while it runs, and does an idle machine behind the Host get updated by itself; (5) does a
removal take the container, images, profile and role away and keep the Hive's data?
"""
from __future__ import annotations

import asyncio
import hashlib
import io
import json
import tarfile

import pytest

from hive import ssh_provisioner as sp
from tests import _ssh_machine_store as store

PACKAGE = b"the hive package"
VERSION = "2.5.5"


class Machine:
    """The machine: answers each script by what it is, records each call."""

    def __init__(self, fail: str = ""):
        self.calls: list[tuple[str, str, bytes]] = []
        self.fail = fail

    async def run(self, host, remote, data=None, timeout=60, step=""):
        self.calls.append((step, remote, data or b""))
        if step == self.fail or (self.fail and step.startswith(self.fail)):
            raise sp.SshInstallError(f"{step}: it broke")
        if step == "preflight":
            return ("uid=1001\ngid=100\nuser=alice\nhome=/home/alice\narch=x86_64\ncores=4\nmem_gb=15\n"
                    "disk_free_gb=42\ndocker=ok\nport=free\nhive_container=no\n")
        if step in ("identity", "profile check"):
            profile = remote.rsplit(" ", 1)[-1].strip("'")
            if profile.startswith("swarmai-hive-"):
                hive = profile[len("swarmai-hive-"):]
                return json.dumps(f"arn:aws:sts::123456789012:assumed-role/SwarmAI-Hive-{hive}/botocore-session-1") + "\n"
            return json.dumps("arn:aws:sts::123456789012:assumed-role/Admin/alice-Isengard") + "\n"
        return "ok\n"

    def steps(self) -> list[str]:
        return [c[0] for c in self.calls]


@pytest.fixture
def world(tmp_path, monkeypatch):
    db = store.make(tmp_path, monkeypatch)
    monkeypatch.setattr("hive.accounts.swarmai_account", lambda: {"account_id": store.ACCOUNT, "region": "us-east-1"})

    async def ensure_row(path):
        return None
    monkeypatch.setattr("hive.accounts.ensure_swarmai_row", ensure_row)
    monkeypatch.setattr(sp, "_host_version", lambda: VERSION)

    # never AWS from a test: every session is refused, the bucket is the store's
    async def no_session(acct, region):
        raise AssertionError("a test reached AWS")
    monkeypatch.setattr(sp, "_session", no_session)

    async def bucket(acct, region):
        return store.BUCKET
    monkeypatch.setattr(sp, "_bucket", bucket)
    roles: list = []

    async def package(acct, region, bucket, version):
        return PACKAGE
    monkeypatch.setattr(sp, "_package", package)

    async def iam_role_arn(acct, region, assumed):
        roles.append(("trust", assumed))
        return "arn:aws:iam::123456789012:role/Admin"

    async def ensure_role(acct, region, name, trusted):
        roles.append(("role", name, trusted))
        return f"arn:aws:iam::123456789012:role/SwarmAI-Hive-{name}"

    async def delete_role(acct, region, name):
        roles.append(("delete", name))
    monkeypatch.setattr(sp, "_iam_role_arn", iam_role_arn)
    monkeypatch.setattr(sp, "_ensure_role", ensure_role)
    monkeypatch.setattr(sp, "_delete_role", delete_role)
    healthy = {"ok": True}

    async def health(mid, timeout=0, version=None):
        if not healthy["ok"]:
            raise sp.SshInstallError("start: the Hive did not answer /health")
    monkeypatch.setattr(sp, "_health", health)
    monkeypatch.setattr(sp, "_hash", lambda pw: "$2b$14$" + "h" * 53)

    async def no_sleep(*a):
        return None
    monkeypatch.setattr(sp.asyncio, "sleep", no_sleep)
    return {"db": db, "roles": roles, "healthy": healthy, "monkeypatch": monkeypatch}


def _install(world, machine: Machine) -> str:
    world["monkeypatch"].setattr(sp, "_run", machine.run)

    async def go():
        row = await sp.add("dev-dsk-alice-1a-0123abcd.us-west-2.amazon.com", "swarmai",
                           mounts=("/apollo:ro", "/workplace"), cert_renew_hint="mwinit -o")
        await asyncio.gather(*list(sp._TASKS))
        return row["id"]
    return asyncio.run(go())


def test_add_steps_and_secrets(world):
    m = Machine()
    mid = _install(world, m)
    status, error, version, _ = store.status_of(world["db"], mid)
    assert (status, error, version) == ("running", None, VERSION)
    assert m.steps() == ["preflight", "image", "identity", "profile", "profile check", "start"]
    # the image is built from the Dockerfile and the checked package
    image = next(c for c in m.calls if c[0] == "image")
    with tarfile.open(fileobj=io.BytesIO(image[2])) as tar:
        names = tar.getnames()
        assert names == ["Dockerfile", "swarmai-hive.tar.gz"]
        assert tar.extractfile("swarmai-hive.tar.gz").read() == PACKAGE
    # the Hive's role trusts the person's role, the profile names it
    assert world["roles"][:2] == [("trust", "arn:aws:sts::123456789012:assumed-role/Admin/alice-Isengard"),
                                  ("role", "dev-dsk-alice-1a-0123abcd", "arn:aws:iam::123456789012:role/Admin")]
    profile = next(c for c in m.calls if c[0] == "profile")[1]
    assert "SwarmAI-Hive-dev-dsk-alice-1a-0123abcd" in profile and "swarmai" in profile
    # the password never reaches the machine; its hash does, on stdin only
    import sqlite3
    with sqlite3.connect(world["db"]) as c:
        password = c.execute("SELECT auth_password FROM hive_instances WHERE id = ?", (mid,)).fetchone()[0]
    assert password
    for step, remote, data in m.calls:
        assert password not in remote and password.encode() not in data, step
        assert "HIVE_PASS_HASH" not in remote, step
    env = next(c for c in m.calls if c[0] == "start" and c[2])[2].decode()
    assert "HIVE_PASS_HASH=$2b$14$" in env and "SWARMAI_CARRIER=ssh" in env and "AWS_PROFILE=swarmai-hive-dev-dsk-alice-1a-0123abcd" in env  # pragma: allowlist secret  (a test value, not a credential)
    start = [c for c in m.calls if c[0] == "start"][-1][1]
    assert "/apollo:ro" in start and "/workplace" in start


def test_package_must_match_its_checksums(monkeypatch):
    class S3:
        def __init__(self, sums):
            self.sums = sums

        def get_object(self, Bucket, Key, **kw):
            body = PACKAGE if Key.endswith(".tar.gz") else self.sums.encode()
            return {"Body": io.BytesIO(body)}

    class Session:
        def __init__(self, sums):
            self._s3 = S3(sums)

        def client(self, name, region_name=None):
            return self._s3

    async def no_sync(self, *a, **k):
        return None
    monkeypatch.setattr("hive.provisioner.HiveProvisioner._sync_release_to_s3", no_sync)
    name = f"swarmai-hive-v{VERSION}-linux-arm64.tar.gz"
    good = f"{hashlib.sha256(PACKAGE).hexdigest()}  {name}\n"
    for sums, ok in ((good, True), (f"{'0' * 64}  {name}\n", False), ("", False)):
        async def session(acct, region, _s=sums):
            return Session(_s)
        monkeypatch.setattr(sp, "_session", session)
        if ok:
            assert asyncio.run(sp._package({"account_id": store.ACCOUNT}, "us-east-1", store.BUCKET, VERSION)) == PACKAGE
        else:
            with pytest.raises(sp.SshInstallError, match="does not match its checksums"):
                asyncio.run(sp._package({"account_id": store.ACCOUNT}, "us-east-1", store.BUCKET, VERSION))


def test_failed_step_marks_error_and_retry_resumes(world):
    m = Machine(fail="image")
    mid = _install(world, m)
    status, error, _, _ = store.status_of(world["db"], mid)
    assert status == "error" and error.startswith("image:")
    # a retry runs the install again, and reaches 'running'
    m.fail = ""

    async def again():
        await sp.retry(mid)
        await asyncio.gather(*list(sp._TASKS))
    asyncio.run(again())
    assert store.status_of(world["db"], mid)[0] == "running"
    # only a machine in error is retried
    with pytest.raises(RuntimeError):
        asyncio.run(sp.retry(mid))


def test_update_rolls_back_on_failed_health(world, monkeypatch):
    m = Machine()
    mid = _install(world, m)
    asyncio.run(sp.claim_update(mid))
    beats: list = []
    real_heartbeat = sp._heartbeat

    async def heartbeat(machine_id, stop):
        beats.append(machine_id)
        await real_heartbeat(machine_id, stop)
    monkeypatch.setattr(sp, "_heartbeat", heartbeat)
    world["healthy"]["ok"] = False
    asyncio.run(sp.update(mid, "2.5.6"))
    status, error, version, _ = store.status_of(world["db"], mid)
    assert status == "running" and version == VERSION and "started again" in error
    assert m.steps()[-4:] == ["preflight", "image", "update", "rollback"] and beats == [mid]
    # the swap got the environment from the Host, on stdin
    env = next(c for c in reversed(m.calls) if c[0] == "update" and c[2])[2].decode()
    assert "HIVE_PASS_HASH=$2b$14$" in env and "SWARM_DATA_DIR=/home/alice/.swarmai-hive/data" in env  # pragma: allowlist secret  (a test value, not a credential)
    # an update that works: the new version runs, the old container and images are dropped
    world["healthy"]["ok"] = True
    asyncio.run(sp.claim_update(mid))
    asyncio.run(sp.update(mid, "2.5.6"))
    assert store.status_of(world["db"], mid)[:3] == ("running", None, "2.5.6")
    assert m.steps()[-1] == "update" and "swarmai-hive-old" in m.calls[-1][1]

    # an idle machine behind the Host's version is updated by itself, once per version
    from core import hive_idle, swarm_out
    from core.carriers import ssh
    monkeypatch.setattr(hive_idle, "_host_version", lambda: "2.5.7")
    monkeypatch.setattr(swarm_out, "open_runs_by_hive", lambda strict=False: {})
    monkeypatch.setattr(hive_idle, "_lessons_by_hive", lambda: {})

    async def idle(self):
        return {}
    monkeypatch.setattr(ssh.SshLocation, "sessions", idle)
    started: list = []

    async def update(machine_id, version):
        started.append((machine_id, version))
        await sp.release_update_claim(machine_id)
    monkeypatch.setattr(sp, "update", update)

    async def follow_twice():
        first = await ssh.SshCarrier().follow_host()
        await asyncio.gather(*list(ssh._UPDATES))
        second = await ssh.SshCarrier().follow_host()
        return first, second
    first, second = asyncio.run(follow_twice())
    assert first == ["dev-dsk-alice-1a-0123abcd"] and second == [] and started == [(mid, "2.5.7")]


def test_remove_keeps_data(world):
    m = Machine()
    mid = _install(world, m)
    out = asyncio.run(sp.remove(mid))
    assert out["deleted"] and out["left"] == [] and ".swarmai-hive/data" in out["data_kept"]
    assert m.steps()[-2:] == ["remove", "remove profile"]
    script = m.calls[-2][1]
    assert "docker rm -f swarmai-hive" in script and "rm -rf" not in script and "/data" not in script
    assert ("delete", "dev-dsk-alice-1a-0123abcd") in world["roles"]
    import sqlite3
    with sqlite3.connect(world["db"]) as c:
        assert c.execute("SELECT COUNT(*) FROM hive_instances WHERE id = ?", (mid,)).fetchone()[0] == 0


def _docker_args(tmp_path, script: str, env: bytes, *args: str) -> tuple[list[str], str, bool]:
    """Run *script* (the text sent to the machine) with real bash, *env* on stdin, and a docker
    that records its arguments and the env file it was given. The arguments of its ``run``,
    that file's text, and whether the file is gone once the script ended."""
    import os
    import subprocess
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    log, seen = tmp_path / "docker.log", tmp_path / "env.seen"
    stub = bin_dir / "docker"
    stub.write_text('#!/bin/bash\nif [ "$1" = run ]; then printf "%s\\n" "$@" > "$DOCKER_LOG"\n'
                    '  while [ $# -gt 0 ]; do [ "$1" = --env-file ] && cp "$2" "$ENV_SEEN"; shift; done; fi\n'
                    '[ "$1" = inspect ] && exit 1\nexit 0\n')
    stub.chmod(0o755)
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    run_env = dict(os.environ, PATH=f"{bin_dir}:{os.environ['PATH']}", HOME=str(home), DOCKER_LOG=str(log),
                   ENV_SEEN=str(seen))
    subprocess.run(["bash", "-c", script, "bash", *args], env=run_env, input=env, check=True, timeout=30)
    argv = log.read_text().splitlines()
    used = argv[argv.index("--env-file") + 1]
    return argv, seen.read_text(), not os.path.exists(used)


def test_container_mounts_and_env_from_stdin(tmp_path):
    """A mount 'path:ro' is mounted read-only at the same path; the environment file is the
    Host's (stdin), kept outside the home only while docker reads it, and nothing of it
    stays on the machine: on an install and on an update alike."""
    home = str(tmp_path / "home")
    for script in (sp.START_SH, sp.SWAP_SH):
        argv, seen, gone = _docker_args(tmp_path, script, b"SWARMAI_MODE=hive\n", VERSION, "/apollo:ro", "/workplace")
        vols = [argv[i + 1] for i, a in enumerate(argv) if a == "-v"]
        assert vols == [f"{home}:{home}", "/apollo:/apollo:ro", "/workplace:/workplace"], vols
        used = argv[argv.index("--env-file") + 1]
        assert used.startswith("/tmp/swarmai-hive-env.") and not used.startswith(home)
        assert seen == "SWARMAI_MODE=hive\n" and gone
        assert not (tmp_path / "home" / ".swarmai-hive" / "hive.env").exists()
        assert (tmp_path / "home" / ".swarmai-hive" / "data").is_dir()


def test_machine_facts_and_region_are_checked(world):
    """What the machine reports about its user, and the account's region, are checked
    before they reach a script or a file: on an install and on an update."""
    import sqlite3

    class Odd(Machine):
        def __init__(self, facts):
            super().__init__()
            self.facts = facts

        async def run(self, host, remote, data=None, timeout=60, step=""):
            if step == "preflight" and self.facts is not None:
                self.calls.append((step, remote, data or b""))
                return self.facts
            return await super().run(host, remote, data, timeout, step)
    good = Odd(None)
    mid = _install(world, good)
    asyncio.run(sp.claim_update(mid))
    bad = Odd("uid=1001\ngid=100\nuser=alice\nhome=/home/alice\nx\nyy\nzz\ndocker=ok\n"
              .replace("home=/home/alice", "home=/home/alice;id"))
    world["monkeypatch"].setattr(sp, "_run", bad.run)
    asyncio.run(sp.update(mid, "2.5.6"))
    assert bad.steps() == ["preflight"] and "unexpected user name or home" in store.status_of(world["db"], mid)[1]
    # a region that is not of a region's shape never reaches the machine's AWS config
    with sqlite3.connect(world["db"]) as c:
        c.execute("UPDATE hive_accounts SET default_region = ? WHERE id = 'swarmai'", ("us-east-1\ncredential_process = x",))
    m = Machine()
    mid2 = _install_named(world, m, "other-box")
    assert "profile" not in m.steps() and "region" in store.status_of(world["db"], mid2)[1]


def _install_named(world, machine: Machine, name: str) -> str:
    world["monkeypatch"].setattr(sp, "_run", machine.run)

    async def go():
        row = await sp.add("other-box.example.com", "swarmai", name=name, cert_renew_hint="mwinit -o")
        await asyncio.gather(*list(sp._TASKS))
        return row["id"]
    return asyncio.run(go())


def test_interrupted_update_claim_is_released(world):
    """A claim the Host left (it stopped before the update's first heartbeat) is released by
    the stale-update release: the claim's time is in the form that release compares."""
    import time

    from hive.provisioner import HiveProvisioner
    mid = _install(world, Machine())
    asyncio.run(sp.claim_update(mid))
    time.sleep(1.1)
    asyncio.run(HiveProvisioner(world["db"]).release_stale_updates(max_age_s=0))
    assert store.status_of(world["db"], mid)[0] == "running"


def test_follow_host_tries_again_after_a_lost_claim(world, monkeypatch):
    """A claim refused because the machine's status changed is tried again next round."""
    from core import hive_idle, swarm_out
    from core.carriers import ssh
    mid = _install(world, Machine())
    monkeypatch.setattr(hive_idle, "_host_version", lambda: "2.5.7")
    monkeypatch.setattr(swarm_out, "open_runs_by_hive", lambda strict=False: {})
    monkeypatch.setattr(hive_idle, "_lessons_by_hive", lambda: {})

    async def idle(self):
        return {}
    monkeypatch.setattr(ssh.SshLocation, "sessions", idle)
    lost = {"once": True}
    real_claim = sp.claim_update

    async def claim(machine_id):
        if lost.pop("once", False):
            raise RuntimeError("the SSH machine is not running")
        await real_claim(machine_id)

    async def update(machine_id, version):
        await sp.release_update_claim(machine_id)
    monkeypatch.setattr(sp, "claim_update", claim)
    monkeypatch.setattr(sp, "update", update)
    ssh._TRIED.clear()

    async def twice():
        first = await ssh.SshCarrier().follow_host()
        second = await ssh.SshCarrier().follow_host()
        await asyncio.gather(*list(ssh._UPDATES))
        return first, second
    assert asyncio.run(twice()) == ([], ["dev-dsk-alice-1a-0123abcd"])
    assert mid


def test_settings_refuse_what_may_not_reach_the_machine():
    """The settings an SSH machine is added with: a mount is one normal absolute path
    (optionally ':ro'), never the root or a docker socket; the profile and the hint are of
    their fixed shapes."""
    ok = dict(ssh_host="dev-dsk.example.com", source_profile="swarmai", mounts=["/apollo:ro", "/workplace"],
              cert_renew_hint="mwinit -o")
    sp.check_settings(**ok)
    for fine in ("/var/lib/thing", "/runner", "/tmpfs", "/tmp/cache", "/home/alice/tmp"):
        sp.check_settings(**dict(ok, mounts=[fine]))
    for bad in ("/x -v /var/run/docker.sock:/var/run/docker.sock", "/a;id", "/a$HOME", "/a:/b", "/a:rw",
                "/a/../etc", "/.", "/", "//", "/a/", "/var/run/docker.sock", "/run/docker.sock:ro", "relative",
                "/var/run", "/run", "/var", "/tmp", "/tmp:ro"):
        with pytest.raises(ValueError):
            sp.check_settings(**dict(ok, mounts=[bad]))
    for bad in ("", "a b", "x;id", "$(id)", "-p"):
        with pytest.raises(ValueError):
            sp.check_settings(**dict(ok, source_profile=bad))
    for bad in ("two\nlines", "`id`", "$(id)", "a\\b", "x" * 121):
        with pytest.raises(ValueError):
            sp.check_settings(**dict(ok, cert_renew_hint=bad))
