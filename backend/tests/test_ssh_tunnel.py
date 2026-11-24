"""The SSH tunnel to a machine's Hive (core/carriers/ssh_tunnel; design
docs/design/2026-11-24-swarm-out-ssh-machine.md §4.3, §13 invariant 9).

Questions: (1) do ten callers at once start one ssh; (2) is the command shaped so a person's
SSH config cannot turn it into a mux client, host-key checking stays on, the host comes after
``--`` and only 127.0.0.1 is bound; (3) is a host that looks like an option refused before any
process starts; (4) does a tunnel a previous Host left get ended before a new one is built,
and does a tunnel that never answers end in an error, not a hang?
"""
from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import time

import pytest

from core.carriers import ssh_tunnel as t


@pytest.fixture(autouse=True)
def state(tmp_path, monkeypatch):
    from core import swarm_out
    monkeypatch.setattr(swarm_out, "_state_dir", lambda: tmp_path)
    t._LIVE.clear()
    t._LOCKS.clear()
    yield tmp_path
    t.stop_all()


class _Proc:
    pid = 4242
    returncode = None

    def poll(self):
        return None

    def terminate(self):
        self.returncode = 0

    def wait(self, timeout=None):
        return 0

    def kill(self):
        self.returncode = -9


def test_concurrent_ensure_starts_one_process(monkeypatch):
    started: list = []
    up = {"on": False}

    def start(lid, cmd, port):
        started.append(cmd)
        time.sleep(0.05)          # a real ssh takes a while: other callers arrive meanwhile
        up["on"] = True
        p = _Proc()
        t._LIVE[lid] = (p, cmd)
        return p
    monkeypatch.setattr(t, "_start", start)
    monkeypatch.setattr(t, "_port_answers", lambda port, timeout=0.5: up["on"])
    monkeypatch.setattr(t, "_listens", lambda pid, port: up["on"])

    async def ten():
        return await asyncio.gather(*(t.ensure("m1", "dev-dsk.example.com", 28411, 18321) for _ in range(10)))
    got = asyncio.run(ten())
    assert set(got) == {"http://127.0.0.1:28411"} and len(started) == 1


def test_command_shape():
    cmd = t.command("dev-dsk.example.com", 28411, 18321)
    assert cmd[0] == "ssh" and cmd[-2:] == ["--", "dev-dsk.example.com"]
    joined = " ".join(cmd)
    for opt in ("BatchMode=yes", "ExitOnForwardFailure=yes", "ControlMaster=no", "ControlPath=none"):
        assert opt in joined
    assert "-L 127.0.0.1:28411:127.0.0.1:18321" in joined
    assert "StrictHostKeyChecking" not in joined and "UserKnownHostsFile" not in joined
    assert t.command("alice@dev-dsk.example.com", 28411, 18321)[-1] == "alice@dev-dsk.example.com"


def test_rejects_option_like_host(monkeypatch):
    def no_process(*a, **k):
        raise AssertionError("a process was started")
    monkeypatch.setattr(t, "_start", no_process)
    monkeypatch.setattr(subprocess, "Popen", no_process)
    for bad in ("-oProxyCommand=touch /tmp/x", "-x@dev-dsk.example.com", "host;rm -rf ~", "a b", "", "host\n-o", "$(id)"):
        with pytest.raises(t.TunnelError):
            asyncio.run(t.ensure("m1", bad, 28411, 18321))
    for bad_port in (80, 70000, "28411", True):
        with pytest.raises(t.TunnelError):
            t.command("dev-dsk.example.com", bad_port, 18321)


def test_reclaims_stale_pid_and_readiness_guard(monkeypatch, state):
    # a process a previous Host left: its command line is the one recorded, so it is ended
    cmd = [sys.executable, "-c", "import time; time.sleep(60)", "ssh-tunnel-test"]
    stale = subprocess.Popen(cmd)
    try:
        (state / "ssh-tunnels").mkdir(exist_ok=True)
        (state / "ssh-tunnels" / "m1.json").write_text(json.dumps({"pid": stale.pid, "cmd": cmd, "port": 28411}))
        assert t.reclaim("m1") is True
        stale.wait(timeout=5)
        assert stale.poll() is not None
    finally:
        if stale.poll() is None:
            stale.kill()
    # a recorded pid whose command line is something else is not ended
    other = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        (state / "ssh-tunnels" / "m1.json").write_text(json.dumps({"pid": other.pid, "cmd": cmd, "port": 28411}))
        assert t.reclaim("m1") is False and other.poll() is None
    finally:
        other.kill()
    # a tunnel whose forward never answers ends in TunnelError within the wait, never a hang
    monkeypatch.setattr(t, "_start", lambda lid, c, port: t._LIVE.setdefault(lid, (_Proc(), c))[0])
    monkeypatch.setattr(t, "_port_answers", lambda port, timeout=0.5: False)
    monkeypatch.setattr(t, "_listens", lambda pid, port: True)
    t0 = time.monotonic()
    with pytest.raises(t.TunnelError, match="did not answer"):
        asyncio.run(t.ensure("m2", "dev-dsk.example.com", 28412, 18321, wait_s=0.6))
    assert time.monotonic() - t0 < 5
    # and open_run turns that into LocationUnavailable (the next candidate is tried)
    from core.carriers.base import LocationUnavailable
    assert issubclass(__import__("core.swarm_out", fromlist=["HiveUnavailable"]).HiveUnavailable, LocationUnavailable)


def test_forward_must_be_ssh_s_own(monkeypatch):
    """Another program answering on the tunnel's port (it took the port while ssh was still
    logging in) is never taken for the tunnel: the readiness wait ends in TunnelError and
    no credential-bearing address is returned."""
    monkeypatch.setattr(t, "_start", lambda lid, c, port: t._LIVE.setdefault(lid, (_Proc(), c))[0])
    monkeypatch.setattr(t, "_port_answers", lambda port, timeout=0.5: True)     # someone answers
    with pytest.raises(t.TunnelError, match="did not answer"):
        asyncio.run(t.ensure("m3", "dev-dsk.example.com", 28413, 18321, wait_s=0.6))
    assert t.alive("m3", 28413) is False
    # the real check: a process that itself listens on the port is seen as listening
    listener = subprocess.Popen([sys.executable, "-c", "import socket, time; s = socket.socket(); "
                                 "s.bind(('127.0.0.1', 28978)); s.listen(); time.sleep(30)"])
    try:
        end = time.monotonic() + 10
        while not t._listens(listener.pid, 28978) and time.monotonic() < end:
            time.sleep(0.1)
        assert t._listens(listener.pid, 28978) is True
        assert t._listens(listener.pid, 28979) is False
    finally:
        listener.kill()
