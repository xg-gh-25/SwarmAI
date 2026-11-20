"""Who is calling the Inbox send routes: the desktop UI, or something SwarmAI started.

Design: ``docs/design/2026-11-20-inbox.md`` §8 step 0. The local API does not know its
callers (``backend.json`` holds only pid / port / mode; ``X-Swarm-User-Action`` only stops a
cross-site web page; an agent can add any header with curl, and a job's agent runs with
``--bare``, past every hook). So the send routes look at who holds the connection: the
process on the other end of the request's TCP connection. Every agent CLI, job and script
SwarmAI runs is in the backend's process tree; the desktop UI is not (on macOS the app is
its own process; on Windows / Linux the backend is the app's child, so the app is its
parent, never its descendant).

- peer is the backend process itself, or any of its descendants -> refused
- peer process cannot be found (no matching connection, pid hidden, already gone) -> refused
- anything else -> allowed

Residual risk (design §8, accepted): a descendant that double-forks and is re-parented to
init leaves the tree. ``background_command_guard`` already refuses long-lived background
processes from an agent, so the path is narrow.

Blocking (psutil walks the machine's sockets): call through ``store.run_blocking``.

Public: ``CallerRefused``, ``check_caller``, ``find_peer_pid``, ``in_backend_tree``.
"""
from __future__ import annotations

import ipaddress
import logging
import os
from typing import Iterable, Optional

import psutil

logger = logging.getLogger(__name__)

Address = tuple[str, int]


class CallerRefused(Exception):
    """The caller is not the desktop UI. ``reason``: no_peer | backend_process |
    backend_descendant."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _norm_ip(host: str) -> str:
    """One spelling per address: ``::ffff:127.0.0.1`` and ``127.0.0.1`` are the same peer."""
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return host
    mapped = getattr(ip, "ipv4_mapped", None)
    return str(mapped or ip)


def _same(addr, want: Address) -> bool:
    return bool(addr) and addr.port == want[1] and _norm_ip(addr.ip) == want[0]


def _tcp_connections() -> Iterable[tuple[object, object, Optional[int]]]:
    """(laddr, raddr, pid) of every TCP connection psutil can see. The system-wide call
    needs no privilege on Linux and Windows; on macOS it needs root, so there each process
    of this user is asked for its own connections instead."""
    try:
        for c in psutil.net_connections(kind="tcp"):
            yield c.laddr, c.raddr, c.pid
        return
    except psutil.AccessDenied:
        pass
    for proc in psutil.process_iter(["pid"]):
        try:
            conns = proc.net_connections(kind="tcp")
        except (psutil.AccessDenied, psutil.NoSuchProcess, psutil.ZombieProcess):
            continue
        for c in conns:
            yield c.laddr, c.raddr, proc.pid


def find_peer_pid(client: Address, server: Address) -> Optional[int]:
    """The pid of the process whose socket is ``client`` connected to ``server`` (the
    request's two ends as the server sees them), or None."""
    want_local = (_norm_ip(client[0]), int(client[1]))
    want_remote = (_norm_ip(server[0]), int(server[1]))
    for laddr, raddr, pid in _tcp_connections():
        if _same(laddr, want_local) and _same(raddr, want_remote) and pid:
            return int(pid)
    return None


def in_backend_tree(pid: int, backend_pid: Optional[int] = None) -> Optional[str]:
    """``backend_process`` / ``backend_descendant`` when ``pid`` is the backend or below
    it, else None. A process that vanished counts as not found (``no_peer``)."""
    backend_pid = os.getpid() if backend_pid is None else backend_pid
    if pid == backend_pid:
        return "backend_process"
    try:
        parents = psutil.Process(pid).parents()
    except (psutil.NoSuchProcess, psutil.ZombieProcess):
        return "no_peer"
    except psutil.AccessDenied:
        # a process of another user: not one SwarmAI started, but its ancestry is hidden
        return "no_peer"
    if any(p.pid == backend_pid for p in parents):
        return "backend_descendant"
    return None


def check_caller(client: Optional[Address], server: Optional[Address]) -> int:
    """Raise ``CallerRefused`` unless the request's peer is a process outside the
    backend's tree; returns the peer pid."""
    if not client or not server or client[1] is None or server[1] is None:
        raise CallerRefused("no_peer")
    pid = find_peer_pid((str(client[0]), int(client[1])), (str(server[0]), int(server[1])))
    if pid is None:
        raise CallerRefused("no_peer")
    reason = in_backend_tree(pid)
    if reason:
        raise CallerRefused(reason)
    return pid
