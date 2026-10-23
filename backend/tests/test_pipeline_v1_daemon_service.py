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


def test_a_runner_lease_is_bound_to_the_process_that_opened_it(rig):
    from core import hook_token
    from core.run_protocol import daemon_exec, runner_service
    rig.create()
    token = hook_token.issue(rig.ws, SESSION, rig.run_id, "test")
    opened = daemon_exec.request({"op": "runner_open", "token": token, "project": "P",
                                  "run_id": rig.run_id}, token=token)
    assert opened.get("ok") is True, opened
    lease = opened["lease"]
    # this process opened it: a request from another process is refused
    code = ("import json, sys; sys.path.insert(0, %r)\n"
            "from core.run_protocol import daemon_exec\n"
            "print(json.dumps(daemon_exec.request({'op': 'runner', 'lease': %r, "
            "'call': 'events', 'args': {}}, token=%r)))\n"
            % (str(daemon_exec.__file__.rsplit('/core/', 1)[0]), lease, lease))
    proc = subprocess.run([sys.executable, "-c", code], env=rig.env, capture_output=True,
                          text=True, timeout=60)
    other = json.loads(proc.stdout.strip().splitlines()[-1])
    assert other.get("ok") is False, other
    mine = daemon_exec.request({"op": "runner", "lease": lease, "call": "events",
                                "args": {}}, token=lease)
    assert mine.get("ok") is True, mine
    bad = daemon_exec.request({"op": "runner", "lease": lease, "call": "append",
                               "args": {"kind": "land", "at": "2026-10-24T00:00:00Z",
                                        "payload": {}}}, token=lease)
    assert bad.get("ok") is False and "cannot record" in bad.get("error", ""), bad
    with runner_service._LOCK:
        runner_service._LEASES[lease]["start"] = "another process"   # pid reused
    runner_service.reap()
    assert lease not in runner_service._LEASES


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


def test_an_unidentifiable_runner_gets_no_lease(rig):
    from core import hook_token
    from core.run_protocol import runner_service
    rig.create()
    token = hook_token.issue(rig.ws, SESSION, rig.run_id, "test")
    out = runner_service.handle({"op": "runner_open", "token": token, "project": "P",
                                 "run_id": rig.run_id}, port=None, peer_pid=None)
    assert out.get("ok") is False, out
