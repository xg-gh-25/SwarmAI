"""The daemon's pipeline service (B-class B-a/B-b): admission and runner leases.

Methodology: the real service (``daemon_exec.start``) on a private socket in this
process, with real hook tokens; clients connect from this process or from a child
process. Invariants:
- a command with no / a used / another command's token runs nothing;
- the service admits at most ``_MAX_CONNECTIONS`` requests at once and answers the
  rest "busy" immediately (no thread pile-up);
- a runner lease works only from the process that opened it, and is dropped once
  that process has exited;
- a lease cannot append event kinds outside ``RUNNER_KINDS``.
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading

import pytest

from tests.pipeline_v1_rig import SESSION, Rig

pytestmark = pytest.mark.timeout(240)


@pytest.fixture
def rig(tmp_path, monkeypatch):
    r = Rig(tmp_path, monkeypatch)
    yield r
    r.close()


def test_a_used_or_foreign_token_runs_nothing(rig):
    from core import hook_token
    from core.run_protocol import daemon_exec
    rig.create()
    argv = ["abandon", "--project", "P", "--run-id", rig.run_id, "--reason", "x"]
    token = hook_token.issue(rig.ws, "s", rig.run_id, "cli:status")   # another command
    out, rc = daemon_exec.call(token, argv)
    assert rc == 2 and out.get("code") == "token_scope", out
    out, rc = daemon_exec.call(token, argv)                          # already burned
    assert rc == 2 and out.get("code") == "no_token", out
    assert rig.state()["status"] == "active"


def test_the_service_answers_busy_past_its_admission_limit(rig, monkeypatch):
    from core import hook_token
    from core.run_protocol import daemon_exec
    monkeypatch.setattr(daemon_exec, "_ADMISSION", threading.BoundedSemaphore(1))
    path = daemon_exec.socket_path(int(hook_token.issue(rig.ws, "s", None, "x").split(".")[0]))
    slow = socket.socket(socket.AF_UNIX)
    slow.connect(str(path))                     # holds the one slot, sends nothing yet
    try:
        fast = socket.socket(socket.AF_UNIX)
        fast.settimeout(10)
        fast.connect(str(path))
        reply = json.loads(fast.makefile().readline())
        assert reply["out"]["code"] == "busy", reply
    finally:
        slow.close()


def test_a_failed_worker_start_frees_its_admission_slot(rig, monkeypatch):
    """B round 2 MED: an exception between taking the slot and starting the worker
    leaked the slot, so repeated failures left the service answering busy for ever."""
    import socketserver
    from core.run_protocol import daemon_exec
    sem = threading.BoundedSemaphore(1)
    monkeypatch.setattr(daemon_exec, "_ADMISSION", sem)
    real = socketserver.ThreadingMixIn.process_request

    def boom(self, request, client_address):
        raise RuntimeError("cannot start thread")
    monkeypatch.setattr(socketserver.ThreadingMixIn, "process_request", boom)
    server = daemon_exec._server
    a, b = socket.socketpair(socket.AF_UNIX)
    with pytest.raises(RuntimeError):
        server.process_request(a, None)
    monkeypatch.setattr(socketserver.ThreadingMixIn, "process_request", real)
    assert sem.acquire(blocking=False), "the slot leaked"
    sem.release()
    b.close()


