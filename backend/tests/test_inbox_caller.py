"""Inbox caller check (core/inbox/caller.py) on real sockets and real processes.

Design §11 #2: a process in the backend's tree calling the send route is refused; a peer
that cannot be found is refused. The test process plays the backend: it listens on a
loopback port and asks who is on the other end of each accepted connection.
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import time

import pytest

from core.inbox import caller

_CLIENT = """
import socket, sys, time
s = socket.create_connection(("127.0.0.1", int(sys.argv[1])))
print("connected", flush=True)
time.sleep(30)
"""


@pytest.fixture
def server():
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(4)
    yield srv
    srv.close()


def _accept(srv) -> tuple[socket.socket, tuple]:
    srv.settimeout(10)
    conn, peer = srv.accept()
    return conn, peer


def test_child_process_peer_is_refused(server):
    port = server.getsockname()[1]
    child = subprocess.Popen([sys.executable, "-c", _CLIENT, str(port)], stdout=subprocess.PIPE)
    try:
        conn, peer = _accept(server)
        assert child.stdout.readline().strip() == b"connected"
        assert caller.find_peer_pid(peer, server.getsockname()) == child.pid
        with pytest.raises(caller.CallerRefused) as info:
            caller.check_caller(peer, server.getsockname())
        assert info.value.reason == "backend_descendant"
        conn.close()
    finally:
        child.kill()
        child.wait(10)


def test_backend_process_itself_is_refused(server):
    client = socket.create_connection(server.getsockname())
    try:
        conn, peer = _accept(server)
        with pytest.raises(caller.CallerRefused) as info:
            caller.check_caller(peer, server.getsockname())
        assert info.value.reason == "backend_process"
        conn.close()
    finally:
        client.close()


def test_unknown_peer_is_refused(server):
    # nothing is connected from this port
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    unused = probe.getsockname()
    probe.close()
    with pytest.raises(caller.CallerRefused) as info:
        caller.check_caller(unused, server.getsockname())
    assert info.value.reason == "no_peer"
    with pytest.raises(caller.CallerRefused):
        caller.check_caller(None, server.getsockname())
    with pytest.raises(caller.CallerRefused):
        caller.check_caller(("testclient", 50000), ("testserver", 80))


def test_process_outside_the_tree_is_allowed(monkeypatch):
    """The desktop UI is not below the backend: our own parent stands in for it."""
    outsider = os.getppid()
    monkeypatch.setattr(caller, "find_peer_pid", lambda client, srv: outsider)
    assert caller.check_caller(("127.0.0.1", 1), ("127.0.0.1", 2)) == outsider


def test_a_vanished_peer_is_refused(monkeypatch):
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    child.wait(10)
    time.sleep(0.05)
    monkeypatch.setattr(caller, "find_peer_pid", lambda client, srv: child.pid)
    with pytest.raises(caller.CallerRefused) as info:
        caller.check_caller(("127.0.0.1", 1), ("127.0.0.1", 2))
    assert info.value.reason == "no_peer"


def test_ipv4_mapped_addresses_match():
    assert caller._norm_ip("::ffff:127.0.0.1") == "127.0.0.1"
    assert caller._norm_ip("127.0.0.1") == "127.0.0.1"


def test_per_process_scan_when_the_system_wide_list_is_denied(server, monkeypatch):
    """macOS refuses psutil.net_connections() without root: each process is asked instead."""
    import psutil

    def denied(kind="tcp"):
        raise psutil.AccessDenied()

    monkeypatch.setattr(psutil, "net_connections", denied)
    port = server.getsockname()[1]
    child = subprocess.Popen([sys.executable, "-c", _CLIENT, str(port)], stdout=subprocess.PIPE)
    try:
        conn, peer = _accept(server)
        child.stdout.readline()
        assert caller.find_peer_pid(peer, server.getsockname()) == child.pid
        conn.close()
    finally:
        child.kill()
        child.wait(10)
