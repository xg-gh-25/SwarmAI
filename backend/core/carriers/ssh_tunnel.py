"""The SSH tunnel to an SSH machine's Hive (design docs/design/2026-11-24-swarm-out-ssh-machine.md
§4.3, decision D3).

One ``ssh -N -L`` child of the Host per machine, on the machine's fixed local port: every
caller (placement's probe, a dispatch, the runs refresh, a remote tab) reaches the machine's
Hive at ``http://127.0.0.1:<local_port>``, which ssh forwards to ``127.0.0.1:<remote_port>``
on the machine. Why it is shaped this way:

* **A fixed local port per machine** (chosen when the machine is added, kept in its
  ``carrier_config``): a record or a client held across a rebuild (the relay, a remote tab's
  stream) still reaches the same machine, and one machine's port is never given to another's
  tunnel, which would send the first machine's Basic credential to the second. A port taken
  by something else is an error, never a reason to pick another.
* **Built when used, never from a probe.** ``alive`` only looks; ``ensure`` builds, and waits
  at most READY_TIMEOUT_S for the forward to answer (the one hang guard: an ssh that never
  connects). Placement's offers use ``alive`` and start a build in the background
  (``ensure_soon``), so a machine that does not answer never spends the dispatch's
  "choosing a Hive" budget.
* **``ControlMaster=no``, ``ControlPath=none``.** A person's ``~/.ssh/config`` with connection
  sharing would make ``ssh -N -L`` a mux client: the forward would live in the master, and
  the pid kept here, ``ExitOnForwardFailure`` and ending the tunnel would all stop working.
  User name, jump host and certificate still come from the person's own SSH config.
* **The host is checked, then passed after ``--``,** in an argument list (no shell): a host
  that starts with ``-`` or holds anything but host-name characters is refused before any
  process starts. Host-key checking is never turned off.
* **No orphans.** The child's pid, its command line and its port are kept in a file under
  the Swarm Out state directory. Before a machine's tunnel is built, a pid in that file whose
  command line is still this tunnel's is ended (a Host that was stopped without ending its
  children); the Host ends every tunnel it started when it exits (``stop_all``, atexit).
* ssh's own output goes to a log file next to the pid file, truncated at each start; its
  last line is what an error says (``Permission denied (publickey)`` when the Host's own
  SSH certificate expired, for example).
"""
from __future__ import annotations

import asyncio
import atexit
import json
import logging
import os
import re
import socket
import subprocess
import time
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

#: the one hang guard: how long ``ensure`` waits for a new tunnel's forward to answer
READY_TIMEOUT_S = 15.0
#: how often the forward is tried while waiting
_POLL_S = 0.25
#: a host name (or address) ssh is given: letters, digits, dots, hyphens, an optional
#: ``user@`` in front; never starting with ``-`` (it would be read as an option)
HOST_RE = re.compile(r"^(?:[A-Za-z0-9._-]{1,64}@)?[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$")


class TunnelError(Exception):
    """The tunnel could not be built or is not usable; the message is safe to show."""


def valid_host(host: Any) -> bool:
    return isinstance(host, str) and not host.startswith("-") and bool(HOST_RE.fullmatch(host))


def valid_port(port: Any) -> bool:
    return isinstance(port, int) and not isinstance(port, bool) and 1024 <= port <= 65535


def command(host: str, local_port: int, remote_port: int) -> list[str]:
    """The tunnel's command line. TunnelError for a host or port that is not one."""
    if not valid_host(host):
        raise TunnelError(f"not an SSH host: {str(host)[:80]!r}")
    if not valid_port(local_port) or not valid_port(remote_port):
        raise TunnelError("the tunnel's ports must be between 1024 and 65535")
    return ["ssh", "-N",
            "-o", "BatchMode=yes",
            "-o", "ExitOnForwardFailure=yes",
            "-o", "ConnectTimeout=10",
            "-o", "ServerAliveInterval=15",
            "-o", "ServerAliveCountMax=3",
            "-o", "ControlMaster=no",
            "-o", "ControlPath=none",
            "-L", f"127.0.0.1:{local_port}:127.0.0.1:{remote_port}",
            "--", host]


# -- where the pid and the log are kept ------------------------------------------------------

def _dir() -> Path:
    from core import swarm_out
    d = swarm_out._state_dir() / "ssh-tunnels"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _safe_id(location_id: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", str(location_id)):
        raise TunnelError("not a machine id")
    return str(location_id)


def _pid_path(location_id: str) -> Path:
    return _dir() / f"{_safe_id(location_id)}.json"


def _log_path(location_id: str) -> Path:
    return _dir() / f"{_safe_id(location_id)}.log"


def _last_line(location_id: str) -> str:
    try:
        lines = [ln.strip() for ln in _log_path(location_id).read_text(errors="replace").splitlines()
                 if ln.strip()]
    except OSError:
        return ""
    return lines[-1][:300] if lines else ""


# -- the processes this Host started ----------------------------------------------------------

#: location id -> (the child, its command line)
_LIVE: dict[str, tuple[subprocess.Popen, list[str]]] = {}
_LOCKS: dict[str, asyncio.Lock] = {}
#: background builds started by ``ensure_soon``, kept so they are not collected mid-run
_SOON: set[asyncio.Task] = set()


def _lock(location_id: str) -> asyncio.Lock:
    lk = _LOCKS.get(location_id)
    if lk is None:
        lk = _LOCKS[location_id] = asyncio.Lock()
    return lk


def _port_answers(port: int, timeout: float = 0.5) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout):
            return True
    except OSError:
        return False


def _listens(pid: int, port: int) -> bool:
    """Whether process *pid* itself listens on 127.0.0.1:*port*. Something answering on the
    port is not enough: ssh binds it only after it has logged in, and another program that
    took the port in that time would be sent the Hive's credential."""
    try:
        import psutil
        conns = psutil.Process(pid).net_connections(kind="tcp")
    except Exception:  # noqa: BLE001 — gone, or not readable: not known to be ours
        return False
    return any(c.status == "LISTEN" and c.laddr and c.laddr.port == port and c.laddr.ip == "127.0.0.1"
               for c in conns)


def _ready(proc, port: int, timeout: float) -> bool:
    """The tunnel's forward is up: ssh itself listens on the port and the port answers."""
    return _listens(proc.pid, port) and _port_answers(port, timeout)


def alive(location_id: str, local_port: int) -> bool:
    """Whether this Host's tunnel of the machine runs and its local end (ssh's own) answers.
    Only looks."""
    got = _LIVE.get(str(location_id))
    if got is None or got[0].poll() is not None:
        return False
    return _ready(got[0], local_port, 0.3)


def _cmdline(pid: int) -> Optional[list[str]]:
    try:
        import psutil
        return psutil.Process(pid).cmdline()
    except Exception:  # noqa: BLE001 — gone, not ours to read: nothing to end
        return None


def _end(pid: int) -> None:
    try:
        import psutil
        p = psutil.Process(pid)
        p.terminate()
        try:
            p.wait(timeout=3)
        except psutil.TimeoutExpired:
            p.kill()
    except Exception as e:  # noqa: BLE001 — already gone
        logger.info("ssh_tunnel: ending pid %s: %s", pid, type(e).__name__)


def reclaim(location_id: str) -> bool:
    """End the tunnel a previous Host left for this machine: the pid in its file, only when
    that process's command line is still exactly the tunnel recorded there. True when one
    was ended."""
    path = _pid_path(location_id)
    try:
        kept = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    pid, cmd = kept.get("pid"), kept.get("cmd")
    ours = _LIVE.get(str(location_id))
    if ours is not None and ours[0].pid == pid:
        return False                                  # this Host's own, live: not stale
    ended = False
    if isinstance(pid, int) and isinstance(cmd, list) and _cmdline(pid) == cmd:
        logger.info("ssh_tunnel: ending the tunnel a previous Host left for %s (pid %s)", location_id, pid)
        _end(pid)
        ended = True
    path.unlink(missing_ok=True)
    return ended


def stop(location_id: str) -> None:
    """End this Host's tunnel of the machine, if any, and forget it."""
    got = _LIVE.pop(str(location_id), None)
    if got is not None and got[0].poll() is None:
        got[0].terminate()
        try:
            got[0].wait(timeout=3)
        except subprocess.TimeoutExpired:
            got[0].kill()
    try:
        _pid_path(location_id).unlink(missing_ok=True)
    except TunnelError:
        pass


def stop_all() -> None:
    """End every tunnel this Host started (atexit)."""
    for loc in list(_LIVE):
        try:
            stop(loc)
        except Exception as e:  # noqa: BLE001 — exiting: end what can be ended
            logger.info("ssh_tunnel: stopping %s at exit: %s", loc, type(e).__name__)


atexit.register(stop_all)


def _start(location_id: str, cmd: list[str], local_port: int) -> subprocess.Popen:
    reclaim(location_id)
    if _port_answers(local_port, 0.3):
        raise TunnelError(f"local port {local_port} is already taken by another program; "
                          "free it, or change this machine's local port")
    log = open(_log_path(location_id), "w", encoding="utf-8")  # noqa: SIM115 — the child keeps it
    try:
        proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=log, stderr=log,  # noqa: S603 — a fixed argument list
                                close_fds=True)
    finally:
        log.close()
    _pid_path(location_id).write_text(json.dumps({"pid": proc.pid, "cmd": cmd, "port": local_port,
                                                  "at": time.time()}), encoding="utf-8")
    _LIVE[str(location_id)] = (proc, cmd)
    return proc


async def ensure(location_id: str, host: str, local_port: int, remote_port: int,
                 wait_s: float = READY_TIMEOUT_S) -> str:
    """The machine's Hive base URL, after making sure its tunnel runs: the live one, or a
    new one whose forward answered within *wait_s*. One machine has at most one tunnel:
    concurrent callers wait for the same build. TunnelError, with ssh's last line, when it
    could not be built."""
    cmd = command(host, local_port, remote_port)
    base = f"http://127.0.0.1:{local_port}"
    async with _lock(str(location_id)):
        got = _LIVE.get(str(location_id))
        if got is not None and got[0].poll() is None and got[1] == cmd \
                and await asyncio.to_thread(_ready, got[0], local_port, 0.5):
            return base
        if got is not None:
            await asyncio.to_thread(stop, location_id)
        proc = await asyncio.to_thread(_start, str(location_id), cmd, local_port)
        end = time.monotonic() + wait_s
        while time.monotonic() < end:
            if proc.poll() is not None:
                why = _last_line(location_id) or f"ssh exited with code {proc.returncode}"
                _LIVE.pop(str(location_id), None)
                raise TunnelError(f"the SSH tunnel to {host} did not start: {why}")
            if await asyncio.to_thread(_ready, proc, local_port, 0.5):
                logger.info("ssh_tunnel: tunnel to %s up on 127.0.0.1:%s", host, local_port)
                return base
            await asyncio.sleep(_POLL_S)
        why = _last_line(location_id)
        await asyncio.to_thread(stop, location_id)
        raise TunnelError(f"the SSH tunnel to {host} did not answer within {wait_s:.0f}s"
                          + (f": {why}" if why else ""))


def ensure_soon(location_id: str, host: str, local_port: int, remote_port: int) -> None:
    """Start building the machine's tunnel in the background (placement's offers: the next
    dispatch finds it up). At most one build per machine runs; its failure is only logged."""
    if any(t.get_name() == f"ssh-tunnel-{location_id}" and not t.done() for t in _SOON):
        return

    async def build() -> None:
        try:
            await ensure(location_id, host, local_port, remote_port)
        except Exception as e:  # noqa: BLE001 — the next use reports it
            logger.info("ssh_tunnel: background build for %s failed (%s)", location_id, str(e)[:200])

    task = asyncio.get_running_loop().create_task(build(), name=f"ssh-tunnel-{location_id}")
    _SOON.add(task)
    task.add_done_callback(_SOON.discard)


def last_error(location_id: str) -> str:
    """ssh's last line for this machine (what the panel shows when it is unreachable)."""
    try:
        return _last_line(location_id)
    except TunnelError:
        return ""
