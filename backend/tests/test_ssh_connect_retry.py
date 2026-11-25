"""An SSH connection that fails while it is being set up is tried again (core/carriers/ssh_tunnel
.connect_failed, hive/ssh_provisioner._run, ssh_tunnel.ensure).

A machine reached through a proxy in front of ssh (a ProxyCommand) sometimes drops the
handshake ("Connection timed out during banner exchange", "Connection to UNKNOWN port 65535
timed out"). Then nothing ran on the machine, so the same command may run again. Questions:
(1) does a step whose first connection drops and second works succeed, with the same stdin
sent again; (2) does a machine that stays unreachable fail after three attempts with ssh's
last line; (3) is a command that ran and failed, a refused key or host, a command that itself
exited 255 with a "connection refused" of its own, and a step killed at its timeout run only
once; (4) is a tunnel whose ssh drops in its handshake started again, and one that fails
otherwise not?
"""
from __future__ import annotations

import asyncio

import pytest

from core.carriers import ssh_tunnel as t
from hive import ssh_provisioner as sp

BANNER = b"Connection timed out during banner exchange\nConnection to UNKNOWN port 65535 timed out\n"


class Ssh:
    """``asyncio.create_subprocess_exec`` for ssh: each call takes the next (exit code, stdout,
    stderr) of *outcomes*; ``hang`` makes a call never finish. Records each call's stdin."""

    def __init__(self, *outcomes, hang=False):
        self.outcomes = list(outcomes)
        self.stdin: list[bytes] = []
        self.hang = hang

    async def __call__(self, *argv, **kw):
        outer = self
        code, out, err = self.outcomes.pop(0) if self.outcomes else (0, b"", b"")

        class Proc:
            returncode = None

            async def communicate(self, data=b""):
                outer.stdin.append(data)
                if outer.hang:
                    await asyncio.sleep(10)
                self.returncode = code
                return out, err

            def kill(self):
                self.returncode = -9

            async def wait(self):
                return self.returncode
        return Proc()


@pytest.fixture
def ssh(monkeypatch):
    waits: list = []
    real_sleep = asyncio.sleep

    async def sleep(s, *a):
        if s == t.CONNECT_RETRY_S:
            waits.append(s)
            return None
        return await real_sleep(s, *a)
    monkeypatch.setattr(sp.asyncio, "sleep", sleep)

    def use(fake):
        monkeypatch.setattr(sp.asyncio, "create_subprocess_exec", fake)
        return fake
    use.waits = waits
    return use


def _run(**kw):
    return asyncio.run(sp._run("box.example.com", "bash -c x", b"the env file", step="start", **kw))


def test_a_flaky_connection_is_tried_again(ssh):
    fake = ssh(Ssh((255, b"", BANNER), (0, b"started\n", b"")))
    assert _run() == "started\n"
    assert fake.stdin == [b"the env file", b"the env file"] and ssh.waits == [t.CONNECT_RETRY_S]


def test_a_machine_that_stays_unreachable(ssh):
    fake = ssh(Ssh(*[(255, b"", BANNER)] * 5))
    with pytest.raises(sp.SshInstallError) as e:
        _run()
    assert str(e.value) == "start: Connection to UNKNOWN port 65535 timed out"
    assert len(fake.stdin) == t.CONNECT_TRIES == 3
    assert "does not answer on SSH" in sp._ssh_hint(str(e.value), "box.example.com")


@pytest.mark.parametrize("code, err", [
    (1, b"docker: Error response from daemon\n"),                       # the command ran and failed
    (255, b"gawan@box: Permission denied (publickey).\n"),              # the person must fix the key
    (255, b"Host key verification failed.\n"),
    (255, b"ssh: Could not resolve hostname box: nodename nor servname provided\n"),
    (255, b"curl: (7) Failed to connect to localhost port 18321: Connection refused\n"),   # a command's own
    (255, b"Connection to box closed by remote host.\n"),               # dropped mid-command: may have run
])
def test_what_is_not_tried_again(ssh, code, err):
    fake = ssh(Ssh((code, b"", err), (0, b"ok", b"")))
    with pytest.raises(sp.SshInstallError):
        _run()
    assert len(fake.stdin) == 1 and ssh.waits == []


def test_a_command_that_began_is_not_run_again(ssh):
    """The machine printed the BEGUN marker: the command started, so whatever ssh printed after
    (here the generic 'Connection to ... timed out' of a session that stalled) it is not run
    again; the marker is never part of a step's output."""
    began = sp.BEGUN.encode() + b"\n"
    fake = ssh(Ssh((255, began + b"swapped half\n", BANNER), (0, b"ok", b"")))
    with pytest.raises(sp.SshInstallError, match="timed out"):
        _run()
    assert len(fake.stdin) == 1 and ssh.waits == []
    # a login script that prints first does not hide the marker; the output keeps the rest
    fake = ssh(Ssh((0, b"welcome\n" + began + b"started\n", b"")))
    assert _run() == "welcome\nstarted\n"
    # and every command sent prints it first
    seen: list = []

    async def record(*argv, **kw):
        seen.append(argv[-1])
        return await Ssh((0, b"", b""))(*argv, **kw)
    ssh(record)
    _run()
    assert seen[0].startswith(f"printf '{sp.BEGUN}\\n'; ")


def test_a_step_killed_at_its_timeout_is_not_run_again(ssh):
    fake = ssh(Ssh((0, b"", b""), hang=True))
    with pytest.raises(sp.SshInstallError, match="did not finish within"):
        _run(timeout=0.05)
    assert len(fake.stdin) == 1


def test_the_profile_check_connects_once_per_try(ssh):
    """_check_identity loops by itself: each of its checks is one connection attempt."""
    fake = ssh(Ssh(*[(255, b"", BANNER)] * 5))
    assert asyncio.run(sp._retry_identity("box.example.com", "2.5.5-h1.8.6", "swarmai-hive-box")) == ""
    assert len(fake.stdin) == 1


def test_connect_failed_reads_only_ssh_s_own_forms():
    for line in ("Connection timed out during banner exchange", "Connection to UNKNOWN port 65535 timed out",
                 "kex_exchange_identification: read: Connection reset by peer",
                 "ssh: connect to host box.example.com port 22: Connection refused",
                 "ssh: connect to host 10.0.0.1 port 22: Operation timed out"):
        assert t.connect_failed(255, f"some banner\n{line}\n"), line
        assert not t.connect_failed(1, line), line
    for line in ("Permission denied (publickey).", "curl: (7) Failed to connect: Connection refused",
                 "timed out", "Connection closed by 10.0.0.1 port 22", ""):
        assert not t.connect_failed(255, line), line


# -- the tunnel ---------------------------------------------------------------------------------

@pytest.fixture
def tunnel(tmp_path, monkeypatch):
    from core import swarm_out
    monkeypatch.setattr(swarm_out, "_state_dir", lambda: tmp_path)
    t._LIVE.clear()
    t._LOCKS.clear()
    waits: list = []
    real_sleep = asyncio.sleep

    async def sleep(s, *a):
        if s == t.CONNECT_RETRY_S:
            waits.append(s)
            return None
        return await real_sleep(s, *a)
    monkeypatch.setattr(t.asyncio, "sleep", sleep)
    yield waits
    t._LIVE.clear()


class _Proc:
    def __init__(self, code):
        self.code, self.pid = code, 4242

    def poll(self):
        return self.code

    @property
    def returncode(self):
        return self.code

    def terminate(self):
        self.code = 0

    def wait(self, timeout=None):
        return 0

    def kill(self):
        self.code = -9


def _starts(monkeypatch, *logs):
    """``_start`` for each start in turn: a log text and an exit code (None: it runs and its
    forward answers)."""
    queue = list(logs)
    started: list = []

    def start(lid, cmd, port):
        text, code = queue.pop(0)
        t._log_path(lid).write_text(text)
        p = _Proc(code)
        started.append(p)
        t._LIVE[lid] = (p, cmd)
        return p
    monkeypatch.setattr(t, "_start", start)
    monkeypatch.setattr(t, "_ready", lambda proc, port, timeout: proc.code is None)
    return started


def test_a_tunnel_that_drops_in_its_handshake_is_started_again(tunnel, monkeypatch):
    started = _starts(monkeypatch, (BANNER.decode(), 255), ("", None))
    assert asyncio.run(t.ensure("m1", "box.example.com", 28411, 18321, wait_s=1)) == "http://127.0.0.1:28411"
    assert len(started) == 2 and tunnel == [t.CONNECT_RETRY_S]
    # one that drops at every start fails after the last, with ssh's last line
    t._LIVE.clear()
    started = _starts(monkeypatch, *[(BANNER.decode(), 255)] * 4)
    with pytest.raises(t.TunnelError, match="UNKNOWN port 65535 timed out"):
        asyncio.run(t.ensure("m1", "box.example.com", 28411, 18321, wait_s=1))
    assert len(started) == t.CONNECT_TRIES
    # one refused for another reason is not started again
    t._LIVE.clear()
    started = _starts(monkeypatch, ("gawan@box: Permission denied (publickey).\n", 255), ("", None))
    with pytest.raises(t.TunnelError, match="Permission denied"):
        asyncio.run(t.ensure("m1", "box.example.com", 28411, 18321, wait_s=1))
    assert len(started) == 1



@pytest.mark.parametrize("old, running, label, skips", [
    (0, "true", "a1b2", True),       # this attempt's swap already ran: nothing changes
    (0, "true", "ffff", False),      # another update() (another attempt): it swaps
    (0, "true", "", False),          # a container from an install (no attempt label)
    (1, "true", "a1b2", False),      # no container to go back to
    (0, "false", "a1b2", False),     # this attempt's container is not running
])
def test_a_swap_runs_twice_only_as_a_no_op(tmp_path, old, running, label, skips):
    """SWAP_SH sent again within one call (the start marker lost on a stalled link) keeps the
    container to go back to: no removal, rename or new container. Anything else swaps."""
    import os
    import subprocess
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "docker.log"
    (bin_dir / "docker").write_text(
        '#!/bin/bash\necho "$@" >> "$DOCKER_LOG"\n'
        'if [ "$1" = inspect ]; then\n'
        '  case "$*" in *Labels*) echo "$LABEL";; *State.Running*) echo "$RUNNING";;\n'
        '    *swarmai-hive-old*) exit "$OLD";; esac\n'
        '  exit 0\nfi\nexit 0\n')
    (bin_dir / "docker").chmod(0o755)
    (tmp_path / "home").mkdir()
    env = dict(os.environ, PATH=f"{bin_dir}:{os.environ['PATH']}", HOME=str(tmp_path / "home"), DOCKER_LOG=str(log),
               OLD=str(old), RUNNING=running, LABEL=label)
    out = subprocess.run(["bash", "-c", sp.SWAP_SH, "bash", "2.5.6-h1.8.6", "a1b2"], env=env,
                         input=b"SWARMAI_MODE=hive\n", capture_output=True, timeout=30, check=True).stdout.decode()
    assert out.strip() == "swapped"
    calls = log.read_text().splitlines()
    if skips:
        assert calls and all(c.startswith("inspect") for c in calls), calls
    else:
        assert [c.split()[0] for c in calls if not c.startswith("inspect")] == ["rm", "stop", "rename", "run"], calls
        assert any("--label swarmai.attempt=a1b2" in c for c in calls if c.startswith("run"))
