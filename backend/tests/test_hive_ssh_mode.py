"""The Hive backend on an SSH machine (core/hive_ssh_mode; design
docs/design/2026-11-24-swarm-out-ssh-machine.md §5.2, §13 invariant 10).

Questions: (1) in ssh mode, does the bridge take its name, bucket and region from the
environment and never read IMDS; (2) does admission-state report the end of the latest SSH
certificate (a real certificate made with ssh-keygen), None without one; (3) outside the mode
(an EC2 or an AgentCore Hive) is neither changed?
"""
from __future__ import annotations

import asyncio
import shutil
import subprocess
import time

import pytest

from core import hive_ssh_mode as m


def _env(monkeypatch, carrier="ssh", **extra):
    monkeypatch.setenv("SWARMAI_MODE", "hive")
    monkeypatch.setenv("SWARMAI_CARRIER", carrier)
    for k, v in extra.items():
        monkeypatch.setenv(k, v)


@pytest.mark.skipif(shutil.which("ssh-keygen") is None, reason="no ssh-keygen here")
def test_ssh_mode_identity_and_admission(monkeypatch, tmp_path):
    from core import swarm_out_bridge as br
    from routers import chat
    # (1) the bridge: environment, never IMDS
    _env(monkeypatch, SWARMAI_HIVE_NAME="dev-dsk", SWARMAI_HIVE_BUCKET="swarmai-hive-9012-us-east-1",
         AWS_REGION="us-east-1")
    assert m.active() and m.identity() == ("dev-dsk", "swarmai-hive-9012-us-east-1", "us-east-1")

    def imds():
        raise AssertionError("IMDS was read on an SSH machine")
    monkeypatch.setattr(br, "resolve_identity", imds)
    made: list = []

    class Store:
        def __init__(self, bucket, region, lazy=False):
            made.append((bucket, region))

    class Stop(Exception):
        pass

    class Bridge:
        def __init__(self, hive, store):
            made.append(hive)
            raise Stop()
    monkeypatch.setattr(br, "S3Store", Store)
    monkeypatch.setattr(br, "Bridge", Bridge)
    with pytest.raises(Stop):
        asyncio.run(br.run_bridge_loop())
    assert made == [("swarmai-hive-9012-us-east-1", "us-east-1"), "dev-dsk"]
    monkeypatch.setenv("SWARMAI_HIVE_BUCKET", "Bad Bucket")
    assert m.identity() is None
    assert asyncio.run(br.run_bridge_loop()) is None                   # bridge off, not IMDS

    # (2) the certificate's end, from a real certificate
    home = tmp_path / "home"
    (home / ".ssh").mkdir(parents=True)
    for args in (["-t", "ed25519", "-f", str(tmp_path / "ca"), "-N", "", "-q"],
                 ["-t", "ed25519", "-f", str(home / ".ssh" / "id_ed25519"), "-N", "", "-q"]):
        subprocess.run(["ssh-keygen", *args], check=True)
    assert m.cert_valid_until(home) is None                            # no certificate yet
    subprocess.run(["ssh-keygen", "-q", "-s", str(tmp_path / "ca"), "-I", "t", "-n", "alice", "-V", "+1h",
                    str(home / ".ssh" / "id_ed25519.pub")], check=True)
    until = m.cert_valid_until(home)
    assert until is not None and abs(until - (time.time() + 3600)) < 180
    monkeypatch.setattr(m, "cert_valid_until", lambda home=None: 1234.0)
    assert asyncio.run(chat._ssh_machine_state()) == {"ssh_cert_valid_until": 1234.0}

    # (3) outside the mode: nothing added, and the bridge does not take the ssh branch
    _env(monkeypatch, carrier="agentcore")
    assert not m.active() and asyncio.run(chat._ssh_machine_state()) == {}
    monkeypatch.delenv("SWARMAI_CARRIER")
    assert not m.active() and asyncio.run(chat._ssh_machine_state()) == {}
    monkeypatch.setattr(br, "resolve_identity", lambda: None)
    assert asyncio.run(br.run_bridge_loop()) is None                   # the EC2 path: IMDS, as before
