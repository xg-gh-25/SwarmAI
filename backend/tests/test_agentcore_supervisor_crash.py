"""The AgentCore supervisor keeps what a dying backend produced (hive/agentcore/supervisor.py
``watch_once``; design docs/design/2026-11-20-swarm-out-multi-carrier.md §6.5).

Each test answers one question:
- when the backend exits on its own mid-turn, does the supervisor run the rescue (upload the
  pending record entries, seal the open turn with its commits: ``core.hive_rescue`` in the
  backend venv, with the backend's environment) before it writes ``state.json`` crashed and
  starts the backend again, once, while /ping says busy?
- when the restarted backend dies too, is it rescued again, ``crashed`` written as final,
  and no third backend started?
- before the platform's 8-hour lifetime (with its margin), is a busy backend stopped, the
  same rescue run and ``state.json`` expired written; and is an idle one left alone?
- does a backend the supervisor itself stopped never count as a crash?

Every subprocess and HTTP call is faked; the supervisor is imported by path, as in
tests/test_agentcore_supervisor.py.
"""
from __future__ import annotations

import json
import subprocess

import pytest

from tests.test_agentcore_supervisor import SID, START, FakeProc, Popens, _healthy, sup  # noqa: F401


class Helpers:
    """subprocess.run for the venv helpers: an S3 put, bcrypt and the rescue, in call order."""

    def __init__(self, rescue_out=None, rescue_code=0):
        self.objects: dict[str, bytes] = {}
        self.order: list[str] = []
        self.rescues: list[dict] = []
        self.rescue_out = rescue_out or {"ok": True, "uploaded": 2, "sealed": 1, "pending": False}
        self.rescue_code = rescue_code
        self.on_rescue = None

    def __call__(self, argv, input=None, **kw):
        if argv[1:3] == ["-m", "core.hive_rescue"]:
            self.order.append("rescue")
            self.rescues.append(kw)
            if self.on_rescue:
                self.on_rescue()
            return subprocess.CompletedProcess(argv, self.rescue_code,
                                               (json.dumps(self.rescue_out) + "\n").encode(), b"")
        script = argv[2]
        if "bcrypt" in script and "put_object" not in script:
            return subprocess.CompletedProcess(argv, 0, b"$2b$14$fakehashfakehashfakehash\n", b"")
        bucket, key, conditional = argv[3], argv[4], argv[5] == "1"
        if conditional and key in self.objects:
            return subprocess.CompletedProcess(argv, 3, b"", b"")
        self.order.append(f"put {key.rsplit('/', 1)[1]}")
        self.objects[key] = input
        return subprocess.CompletedProcess(argv, 0, b"", b"")

    def state(self) -> dict:
        return json.loads(self.objects[f"bridge/{SID}/state.json"])


@pytest.fixture
def started(sup, monkeypatch, tmp_path):  # noqa: F811 — the imported fixture
    """A supervisor whose backend started and became healthy; the watcher is driven by hand."""
    helpers = Helpers()
    popens = Popens()
    monkeypatch.setattr(sup.subprocess, "run", helpers)
    monkeypatch.setattr(sup.subprocess, "Popen", popens)
    monkeypatch.setattr(sup, "http_get_json", _healthy)
    monkeypatch.setattr(sup, "start_watch", lambda: None)      # the test drives watch_once
    out = sup.start(dict(START), platform_sid=SID)
    assert out["started"] is True and len(popens) == 1
    (tmp_path / "logs").mkdir(exist_ok=True)
    (tmp_path / "logs" / "backend.log").write_text("line 1\nKilled\n")
    helpers.order.clear()
    return sup, helpers, popens


def test_backend_death_mid_turn_is_rescued_then_restarted_once(started):
    sup, helpers, popens = started
    seen_busy = []
    helpers.on_rescue = lambda: seen_busy.append(sup.ping()["status"])
    assert sup.watch_once() is True                    # running: nothing to do
    assert helpers.order == [] and len(popens) == 1

    popens[0].code = -9                                # the kernel killed it mid-turn
    assert sup.watch_once() is True                    # rescued and restarted: still watched
    # the rescue (upload, then seal with its commits) runs before crashed is written and
    # before the backend starts again
    assert helpers.order == ["rescue", "put state.json"]
    rescue = helpers.rescues[0]
    assert rescue["cwd"] == sup.BACKEND_DIR and rescue["timeout"] == sup.RESCUE_TIMEOUT_S
    env = rescue["env"]
    assert env["SWARMAI_CARRIER"] == "agentcore" and env["SWARMAI_MODE"] == "hive"
    assert env["SWARMAI_AC_SID"] == SID and env["SWARMAI_AC_BUCKET"] == START["bucket"]
    assert START["hive_password"] not in json.dumps(env)
    st = helpers.state()
    assert st["state"] == "crashed" and st["exit"] == -9 and st["restarting"] is True
    assert "Killed" in st["log_tail"] and isinstance(st["at"], int)
    assert st["rescue"] == {"ok": True, "uploaded": 2, "sealed": 1, "pending": False}
    assert seen_busy == ["HealthyBusy"]               # the platform never idles a rescue away
    assert len(popens) == 2 and popens[1].argv == sup.backend_command()
    assert popens[1].kw["env"] == popens[0].kw["env"]
    assert sup.STATE["proc"] is popens[1] and sup.STATE["restarts"] == 1


def test_a_second_death_is_rescued_and_not_restarted(started):
    sup, helpers, popens = started
    popens[0].code = 1
    assert sup.watch_once() is True
    popens[1].code = 1                                 # the restarted backend dies too
    helpers.order.clear()
    assert sup.watch_once() is False                   # nothing more to watch
    assert helpers.order == ["rescue", "put state.json"]
    st = helpers.state()
    assert st["state"] == "crashed" and st["restarting"] is False and st["exit"] == 1
    assert len(popens) == 2                            # no third backend
    assert sup.ping()["status"] == "Healthy"


def test_a_restart_that_never_becomes_healthy_is_final(started, monkeypatch):
    sup, helpers, popens = started

    def unhealthy(url, headers=None, timeout=3.0):
        raise ConnectionRefusedError()
    monkeypatch.setattr(sup, "http_get_json", unhealthy)
    monkeypatch.setattr(sup, "HEALTH_WAIT_S", 0.0)
    popens[0].code = 1
    assert sup.watch_once() is False
    assert len(popens) == 2 and popens[1].code is not None     # stopped, never left hanging
    assert [o for o in helpers.order if o == "rescue"] == ["rescue", "rescue"]
    assert helpers.state()["state"] == "crashed" and helpers.state()["restarting"] is False


def test_a_failed_rescue_still_writes_crashed_and_restarts(started):
    sup, helpers, popens = started
    helpers.rescue_code = 1
    helpers.rescue_out = {"ok": False, "pending": True}
    popens[0].code = -9
    assert sup.watch_once() is True
    st = helpers.state()
    assert st["state"] == "crashed" and st["rescue"]["ok"] is False and len(popens) == 2


def test_the_pre_limit_seal_stops_a_busy_backend_and_writes_expired(started, monkeypatch):
    sup, helpers, popens = started
    t0 = sup.STATE["started_at"]
    seal_at = t0 + sup.MAX_LIFETIME_S - sup.PRE_LIMIT_MARGIN_S
    assert sup.PRE_LIMIT_MARGIN_S > sup.STOP_WAIT_S + sup.RESCUE_TIMEOUT_S
    monkeypatch.setattr(sup, "busy", lambda: True)
    sup.time.time = lambda: seal_at - 1
    assert sup.watch_once() is True and helpers.order == []
    sup.time.time = lambda: seal_at
    assert sup.watch_once() is False
    assert popens[0].code is not None                  # stopped by the supervisor first
    assert helpers.order == ["rescue", "put state.json"]
    assert helpers.state()["state"] == "expired"
    assert len(popens) == 1                            # never restarted
    sup.STATE["rescuing"] = False
    assert sup.watch_once() is False                   # and never sealed twice
    assert helpers.order == ["rescue", "put state.json"]


def test_the_pre_limit_time_leaves_an_idle_backend_alone_until_it_works(started, monkeypatch):
    sup, helpers, popens = started
    busy = {"v": False}
    monkeypatch.setattr(sup, "busy", lambda: busy["v"])
    sup.time.time = lambda: sup.STATE["started_at"] + sup.MAX_LIFETIME_S - sup.PRE_LIMIT_MARGIN_S
    assert sup.watch_once() is True                    # nothing open: still watched
    assert helpers.order == [] and popens[0].code is None
    busy["v"] = True                                   # a late correction started a turn
    assert sup.watch_once() is False
    assert helpers.order == ["rescue", "put state.json"] and helpers.state()["state"] == "expired"


def test_a_death_past_the_pre_limit_time_is_rescued_and_not_restarted(started):
    sup, helpers, popens = started
    sup.time.time = lambda: sup.STATE["started_at"] + sup.MAX_LIFETIME_S - sup.PRE_LIMIT_MARGIN_S + 5
    popens[0].code = -9
    assert sup.watch_once() is False
    assert helpers.order == ["rescue", "put state.json"]
    assert helpers.state()["state"] == "crashed" and helpers.state()["restarting"] is False
    assert len(popens) == 1


def test_the_lifetime_is_the_runtimes():
    import importlib.util
    from pathlib import Path
    from hive import agentcore_provisioner as prov
    spec = importlib.util.spec_from_file_location(
        "sup_lifetime", Path(__file__).resolve().parents[2] / "hive" / "agentcore" / "supervisor.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.MAX_LIFETIME_S == prov.MAX_LIFETIME_S


class GroupProc(FakeProc):
    """A backend with a pid whose terminate is ignored (the kill branch)."""

    def __init__(self, argv, **kw):
        super().__init__(argv, **kw)
        self.pid = 4242
        self.waits: list = []

    def terminate(self):
        pass                                           # ignores SIGTERM

    def wait(self, timeout=None):
        self.waits.append(self.code)
        if self.code is None:
            raise subprocess.TimeoutExpired("backend", timeout)
        return self.code


def test_the_backends_group_is_gone_before_any_rescue(sup, monkeypatch, tmp_path):  # noqa: F811
    helpers = Helpers()
    procs: list = []

    def popen(argv, **kw):
        assert kw.get("start_new_session") is True     # a group of its own
        procs.append(GroupProc(argv, **kw))
        return procs[-1]
    monkeypatch.setattr(sup.subprocess, "run", helpers)
    monkeypatch.setattr(sup.subprocess, "Popen", popen)
    monkeypatch.setattr(sup, "http_get_json", _healthy)
    monkeypatch.setattr(sup, "start_watch", lambda: None)
    signals: list = []

    def killpg(pid, sig):
        signals.append((pid, sig))
        helpers.order.append(f"killpg {sig.name}")
        if sig == sup.signal.SIGKILL:
            procs[-1].code = -9
    monkeypatch.setattr(sup.os, "killpg", killpg)
    assert sup.start(dict(START), platform_sid=SID)["started"] is True
    helpers.order.clear()
    # an OOM kill took the backend alone: its group is killed and reaped before the rescue
    procs[0].code = -9
    assert sup.watch_once() is True
    assert helpers.order[:2] == ["killpg SIGKILL", "rescue"] and signals[0] == (4242, sup.signal.SIGKILL)
    # the pre-limit seal: SIGTERM ignored, SIGKILL after STOP_WAIT_S, reaped, then the rescue
    helpers.order.clear()
    monkeypatch.setattr(sup, "busy", lambda: True)
    sup.time.time = lambda: sup.STATE["started_at"] + sup.MAX_LIFETIME_S
    assert sup.watch_once() is False
    assert helpers.order == ["killpg SIGTERM", "killpg SIGKILL", "rescue", "put state.json"]
    assert procs[1].waits[-1] == -9                    # waited for after the kill
