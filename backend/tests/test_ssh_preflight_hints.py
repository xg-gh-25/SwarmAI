"""What an SSH machine's install says when the machine does not meet what SwarmAI asks of it
(hive/ssh_provisioner: SSH without a password, and docker usable by the user).

Questions: (1) does the preflight tell docker missing, not usable by the user, and not running
apart, run as the real script with a stub docker; (2) does each such state, an SSH failure
of a known kind, a taken port and a full disk end the install in
error with the one thing to do on the machine and "then Retry"?
"""
from __future__ import annotations

import subprocess

import pytest

from hive import ssh_provisioner as sp
from tests import _ssh_machine_store as store
from tests.test_ssh_machine_autoconfig import Found, _add_host_only
from tests.test_ssh_provisioner import world  # noqa: F401 — the fixture


@pytest.mark.parametrize("stub, want", [
    (None, "missing"),
    ('echo "permission denied while trying to connect to the Docker daemon socket" >&2; exit 1', "denied"),
    ('echo "Cannot connect to the Docker daemon. Is the docker daemon running?" >&2; exit 1', "stopped"),
    ("exit 0", "ok"),
])
def test_preflight_tells_docker_states_apart(tmp_path, stub, want):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for tool in ("bash", "id", "uname", "awk", "df", "sed", "sort", "paste", "readlink", "find", "grep", "nproc", "cat"):
        real = subprocess.run(["/bin/sh", "-c", f"command -v {tool}"], capture_output=True, text=True).stdout.strip()
        if real:
            (bin_dir / tool).symlink_to(real)
    if stub is not None:
        (bin_dir / "docker").write_text(f"#!/bin/sh\n{stub}\n")
        (bin_dir / "docker").chmod(0o755)
    out = subprocess.run([str(bin_dir / "bash"), "-c", sp.PREFLIGHT_SH, "bash", "18999"], capture_output=True,
                         text=True, env={"PATH": str(bin_dir), "HOME": str(tmp_path)}, timeout=30).stdout
    assert sp._parse_kv(out)["docker"] == want


class Broken(Found):
    def __init__(self, preflight_error="", **facts):
        super().__init__()
        self.preflight_error, self.extra = preflight_error, facts

    async def run(self, host, remote, data=None, timeout=60, step="", tries=3):
        if step == "preflight" and self.preflight_error:
            raise sp.SshInstallError(f"preflight: {self.preflight_error}")
        out = await super().run(host, remote, data, timeout, step)
        if step == "preflight":
            out += "".join(f"{k}={v}\n" for k, v in self.extra.items())
        return out


@pytest.mark.parametrize("machine, says", [
    (Broken(docker="missing"), "install Docker Engine"),
    (Broken(docker="denied"), "usermod -aG docker"),
    (Broken(docker="stopped"), "systemctl start docker"),
    (Broken(port="taken"), "free it there"),
    (Broken(disk_free_gb="2"), "free some space"),
    (Broken("Permission denied (publickey,gssapi-keyex)."), "ssh-copy-id"),
    (Broken("Host key verification failed."), "accept it"),
    (Broken("ssh: Could not resolve hostname x: nodename nor servname provided"), "does not resolve"),
    (Broken("ssh: connect to host x port 22: Operation timed out"), "does not answer on SSH"),
])
def test_each_unmet_need_says_what_to_do(world, machine, says):  # noqa: F811
    mid = _add_host_only(world, machine)
    status, error, _, _ = store.status_of(world["db"], mid)
    assert status == "error" and says in error and error.endswith("then Retry"), error
