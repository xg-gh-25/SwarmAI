"""A dispatched task's setup and checks run by id on the Hive (scripts/task_checks,
design docs/design/2026-11-02-swarm-out-e2e-round2-fixing-spec.md G2).

Real git and a real work order (core/hive_workspace ``prepare``, the fixtures of
test_hive_workspace), real bash. Questions: (1) does the order keep the checks the Host
sent, and refuse ones that point outside its repositories; (2) do a setup line's
``$GITHUB_PATH`` and ``$GITHUB_ENV`` reach the checks after it; (3) does ``run`` record
each check's exit code by id, and refuse an id the order does not have; (4) is a failed
setup line recorded with its exit; (5) does the task text for such a Hive tell the agent
to run the checks through task_checks.
"""
from __future__ import annotations

import json
import stat
import sys

import pytest

from core import hive_workspace as hw
from scripts import task_checks
from tests.test_hive_workspace import W1, _repo, _ship, world  # noqa: F401 — the fixture
from tests.test_task_spec import ws  # noqa: F401 — the fixture


def _checks(**over):
    c = {"setup": [], "validations": [
        {"id": "ok", "command": "true", "repo": "lib", "cwd": ""},
        {"id": "bad", "command": "exit 3", "repo": "lib", "cwd": ""},
    ]}
    c.update(over)
    return c


@pytest.fixture
def order(world, monkeypatch):  # noqa: F811 — the imported fixture
    monkeypatch.setattr(task_checks, "_workspace", lambda: world["ws"])
    sha = _ship(world)

    def make(checks):
        return hw.prepare(world["ws"], W1, [_repo(sha)], checks)
    return make


def _record(ws):
    return hw.checks_record(ws, W1)


def test_order_keeps_structured_checks(world, order):
    got = order(_checks(setup=[{"command": "echo hi", "repo": "lib", "cwd": ""}]))
    kept = hw.load(world["ws"], W1)
    assert got["checks"] == kept["checks"]
    assert kept["checks"]["validations"][0] == {"id": "ok", "command": "true", "repo": "lib", "cwd": ""}
    assert kept["checks"]["setup"] == [{"command": "echo hi", "repo": "lib", "cwd": ""}]


def test_order_refuses_checks_outside_its_repos(world, order):
    for bad in (
        _checks(validations=[{"id": "x", "command": "true", "repo": "other", "cwd": ""}]),   # not this order's
        _checks(validations=[{"id": "x", "command": "true", "repo": "lib", "cwd": "../.."}]),
        _checks(validations=[{"id": "x", "command": "true", "repo": "lib", "cwd": "/etc"}]),
        _checks(validations=[{"id": "x", "command": "true", "repo": "lib", "cwd": ""},
                             {"id": "x", "command": "true", "repo": "lib", "cwd": ""}]),     # a repeated id
        {"setup": [], "validations": [], "extra": []},
    ):
        with pytest.raises(hw.WorkError):
            order(bad)
        assert hw.load(world["ws"], W1) is None
        assert not hw.work_dir(world["ws"], W1).exists()


def test_github_path_and_env_reach_checks(world, order, capsys):
    tool = world["ws"].parent / "tools"
    tool.mkdir()
    probe = tool / "probe-tool"
    probe.write_text('#!/bin/sh\n[ "$FROM_SETUP" = "yes" ] && [ "$MULTI" = "a\nb" ]\n')
    probe.chmod(probe.stat().st_mode | stat.S_IEXEC)
    order(_checks(
        setup=[{"command": f'echo "{tool}" >> "$GITHUB_PATH"; echo "FROM_SETUP=yes" >> "$GITHUB_ENV"; '
                           'printf "MULTI<<EOF\\na\\nb\\nEOF\\n" >> "$GITHUB_ENV"', "repo": "lib", "cwd": ""}],
        validations=[{"id": "uses-setup", "command": "probe-tool", "repo": "lib", "cwd": ""}]))
    assert task_checks.main(["setup", W1]) == 0
    assert task_checks.main(["run", W1, "uses-setup"]) == 0
    assert [x.get("exit") for x in _record(world["ws"]) if "exit" in x] == [0]


def test_run_records_exit_codes(world, order):
    order(_checks())
    assert task_checks.main(["run", W1]) == 1               # one check failed
    lines = _record(world["ws"])
    ended = {x["id"]: x["exit"] for x in lines if "exit" in x}
    assert ended == {"ok": 0, "bad": 3}
    started = [x["id"] for x in lines if "exit" not in x]
    assert started == ["ok", "bad"]                          # each wrote a start line first
    log = world["ws"] / [x for x in lines if x.get("id") == "bad" and "log" in x][0]["log"]
    assert log.is_file()


def test_unknown_check_id_is_refused(world, order):
    order(_checks())
    assert task_checks.main(["run", W1, "ok", "not-a-check"]) == 2
    assert _record(world["ws"]) == []                        # nothing ran, nothing recorded


def test_failed_setup_is_recorded(world, order):
    order(_checks(setup=[{"command": "exit 7", "repo": "lib", "cwd": ""},
                         {"command": "touch never", "repo": "lib", "cwd": ""}]))
    assert task_checks.main(["setup", W1]) == 7
    cdir = hw.checks_dir(world["ws"], W1)
    rows = [json.loads(x) for x in (cdir / task_checks.SETUP_FILE).read_text().splitlines()]
    assert [(r["line"], r["exit"]) for r in rows] == [(1, 7)]        # stopped at the failing line
    clone = world["ws"] / hw.repo_rel(W1, "lib")
    assert not (clone / "never").exists()
    # the checks still run when asked, each recorded
    assert task_checks.main(["run", W1, "ok"]) == 0
    assert [x["exit"] for x in _record(world["ws"]) if "exit" in x] == [0]


def test_task_text_names_task_checks(ws):  # noqa: F811 — test_task_spec's workspace fixture
    from core import task_spec
    from tests.test_task_spec import _brain, _build
    _brain(ws)
    spec = _build(ws, "Lib", "x", "the signer test passes")
    assert spec["setup"] and spec["validations"]          # the definition declares both
    by_id = task_spec.segment(spec, None, work_id=W1)
    assert f"scripts.task_checks setup {W1}" in by_id
    assert f"scripts.task_checks run {W1} <id>" in by_id
    assert "Before you finish, run:" not in by_id
    assert all(v["id"] in by_id for v in spec["validations"])
    plain = task_spec.segment(spec, None)
    assert "task_checks" not in plain and "Before you finish, run:" in plain


def test_dispatch_sends_checks_by_id_only_to_a_capable_hive(ws, monkeypatch):  # noqa: F811
    """The Host sends the checks as data (and tells the agent to run them through
    task_checks) only to a Hive whose admission state says task_checks."""
    import asyncio

    import core.swarm_out as so
    from core.project_agent import revision
    from tests.test_task_spec import _brain, _fake_hive
    rev = revision(_brain(ws))
    calls = _fake_hive(monkeypatch, "0" * 64)
    asyncio.run(so.dispatch_spec("hive3", "t", "Lib", "fix it", "tests pass", execution="native",
                                 returns="commits", seen_revision=rev))
    old = calls["dispatch"][-1]
    assert old["checks"] is None and old["agent"]["checks_by_id"] is False
    assert "task_checks" not in old["segment"]
    calls["caps"]["task_checks"] = True
    asyncio.run(so.dispatch_spec("hive3", "t", "Lib", "fix it", "tests pass", execution="native",
                                 returns="commits", seen_revision=rev))
    new = calls["dispatch"][-1]
    assert new["agent"]["checks_by_id"] is True
    assert {v["id"] for v in new["checks"]["validations"]} == {v["id"] for v in new["agent"]["validations"]}
    assert all(set(v) == {"id", "command", "repo", "cwd"} for v in new["checks"]["validations"])
    assert f"scripts.task_checks run {new['dispatch_id']} <id>" in new["segment"]


def test_prepare_work_sends_checks_only_when_given():
    import asyncio
    import json as _json

    import httpx

    import core.swarm_out as so
    from tests import _hive_work_fake as fake
    bodies = []

    def handler(request):
        bodies.append(_json.loads(request.content))
        return fake.answer(request)
    inst = {"name": "h"}

    async def go(checks):
        async with httpx.AsyncClient(base_url="http://h", transport=httpx.MockTransport(handler)) as c:
            return await so._prepare_work(c, inst, W1, [], checks)
    asyncio.run(go(None))
    asyncio.run(go({"setup": [], "validations": [{"id": "a", "command": "true", "repo": "lib", "cwd": ""}]}))
    assert "checks" not in bodies[0]
    assert bodies[1]["checks"]["validations"][0]["id"] == "a"


def test_the_routes_take_checks_and_return_the_record(world, order, monkeypatch):  # noqa: F811
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routers import hive_brains
    app = FastAPI()
    app.include_router(hive_brains.router, prefix="/api/hive")
    client = TestClient(app)
    monkeypatch.setattr(hive_brains, "_workspace", lambda: world["ws"])
    monkeypatch.setenv("SWARMAI_MODE", "hive")
    work = "/api/hive/" + "work"
    checks = {"setup": [], "validations": [{"id": "ok", "command": "true", "repo": "x", "cwd": ""}]}
    # checks naming a repository the order does not have: the order is refused
    r = client.post(work, json={"work_id": W1, "repos": [], "checks": checks})
    assert (r.status_code, r.json()["detail"]) == (422, hw.ERR_BAD_ORDER)
    assert client.get(f"{work}/{W1}/checks").status_code == 404
    order({"setup": [], "validations": [{"id": "ok", "command": "true", "repo": "lib", "cwd": ""}]})
    assert client.get(f"{work}/{W1}/checks").json()["checks"] == []
    assert task_checks.main(["run", W1, "ok"]) == 0
    got = client.get(f"{work}/{W1}/checks").json()["checks"]
    assert [x.get("exit") for x in got] == [None, 0]


def test_a_signal_to_task_checks_ends_the_whole_check(world, order, tmp_path):  # noqa: F811
    """C4: a cap that signals task_checks (SIGTERM, as `perl -e 'alarm'` sends SIGALRM)
    ends the check and what the check started; the check reads as started, not ended."""
    import signal
    import subprocess
    import sys
    import time
    from pathlib import Path
    pidfile = tmp_path / "grandchild.pid"
    order({"setup": [], "validations": [
        {"id": "slow", "command": f"sleep 300 & echo $! > {pidfile}; wait", "repo": "lib", "cwd": ""}]})
    backend = Path(task_checks.__file__).resolve().parents[1]
    env = {**__import__("os").environ, "SWARM_WORKSPACE": str(world["ws"])}
    # its own session here, so the group it ends is not this test's
    p = subprocess.Popen([sys.executable, "-m", "scripts.task_checks", "run", W1, "slow"], cwd=str(backend),
                         env=env, start_new_session=True)
    for _ in range(100):
        if pidfile.exists() and pidfile.read_text().strip():
            break
        time.sleep(0.1)
    grandchild = int(pidfile.read_text().strip())
    p.send_signal(signal.SIGTERM)
    p.wait(timeout=20)
    gone = False
    for _ in range(50):
        try:
            __import__("os").kill(grandchild, 0)
        except ProcessLookupError:
            gone = True
            break
        time.sleep(0.1)
    assert gone, "the check's own child still runs after task_checks was stopped"
    lines = hw.checks_record(world["ws"], W1)
    assert [x for x in lines if x.get("id") == "slow" and "exit" not in x]       # started
    assert not [x for x in lines if x.get("id") == "slow" and "exit" in x]       # never ended


def test_a_signal_to_task_checks_leaves_its_callers_group_alone(world, order, tmp_path):  # noqa: F811
    """R2: task_checks shares a process group with its caller (as under an agent's Bash
    tool); a signal to it ends the check, and a sentinel process of that group lives on."""
    import os
    import signal
    import subprocess
    import sys
    import time
    from pathlib import Path
    pidfile = tmp_path / "check.pid"
    order({"setup": [], "validations": [
        {"id": "slow", "command": f"echo $$ > {pidfile}; sleep 300", "repo": "lib", "cwd": ""}]})
    backend = Path(task_checks.__file__).resolve().parents[1]
    env = {**os.environ, "SWARM_WORKSPACE": str(world["ws"])}
    # a caller in a fresh session: it starts a sentinel in its own group, then becomes
    # task_checks, so the sentinel and task_checks share one process group
    spid = tmp_path / "sentinel.pid"
    p = subprocess.Popen(["bash", "-c", f'sleep 300 & echo $! > {spid}; exec "$0" -m scripts.task_checks run {W1} slow',
                          sys.executable], cwd=str(backend), env=env, start_new_session=True)
    sentinel = None
    try:
        for _ in range(100):
            if pidfile.exists() and pidfile.read_text().strip() and spid.exists() and spid.read_text().strip():
                break
            time.sleep(0.1)
        sentinel = int(spid.read_text().strip())
        assert os.getpgid(sentinel) == os.getpgid(p.pid)          # one group, as under a Bash tool
        check = int(pidfile.read_text().strip())
        p.send_signal(signal.SIGTERM)
        p.wait(timeout=20)
        time.sleep(0.5)
        os.kill(sentinel, 0)                                      # raises if it was killed
        gone = False
        for _ in range(50):
            try:
                os.kill(check, 0)
            except ProcessLookupError:
                gone = True
                break
            time.sleep(0.1)
        assert gone, "the check still runs after task_checks was stopped"
    finally:
        if sentinel:
            try:
                os.kill(sentinel, signal.SIGKILL)
            except ProcessLookupError:
                pass



def _caller_with_check(world, order, tmp_path, command):
    """A caller in a fresh session that starts a sentinel, then becomes task_checks
    running one check: (process, sentinel pid, the check's pid file)."""
    import os
    import subprocess
    import sys
    import time
    from pathlib import Path
    pidfile = tmp_path / "check.pid"
    order({"setup": [], "validations": [{"id": "slow", "command": command.format(pidfile=pidfile),
                                         "repo": "lib", "cwd": ""}]})
    backend = Path(task_checks.__file__).resolve().parents[1]
    env = {**os.environ, "SWARM_WORKSPACE": str(world["ws"])}
    spid = tmp_path / "sentinel.pid"
    p = subprocess.Popen(["bash", "-c", f'sleep 300 & echo $! > {spid}; exec "$0" -m scripts.task_checks run {W1} slow',
                          sys.executable], cwd=str(backend), env=env, start_new_session=True)
    for _ in range(100):
        if pidfile.exists() and pidfile.read_text().strip() and spid.exists() and spid.read_text().strip():
            break
        time.sleep(0.1)
    return p, int(spid.read_text().strip()), pidfile


def _gone(pid):
    import os
    import time
    for _ in range(50):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        time.sleep(0.1)
    return False


@pytest.mark.parametrize("sig_name", ["SIGINT", "SIGTERM", "SIGQUIT"])
def test_a_signal_to_the_callers_group_ends_the_check(world, order, tmp_path, sig_name):  # noqa: F811
    """An interrupt or SIGTERM sent to the caller's whole group (as a tool that stops a
    call by its group) ends the check, which runs in a group of its own."""
    import os
    import signal
    p, sentinel, pidfile = _caller_with_check(world, order, tmp_path, "echo $$ > {pidfile}; sleep 300")
    try:
        check = int(pidfile.read_text().strip())
        os.killpg(os.getpgid(p.pid), getattr(signal, sig_name))
        p.wait(timeout=20)
        assert _gone(check), f"the check still runs after {sig_name} to the caller's group"
    finally:
        for pid in (sentinel, p.pid):
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


def test_a_finished_check_leaves_nothing_in_the_background(world, order, tmp_path):  # noqa: F811
    import os
    import signal
    p, sentinel, pidfile = _caller_with_check(world, order, tmp_path, "sleep 300 & echo $! > {pidfile}")
    try:
        p.wait(timeout=30)
        assert p.returncode == 0
        leftover = int(pidfile.read_text().strip())
        assert _gone(leftover), "a process the check left in the background outlived it"
    finally:
        try:
            os.kill(sentinel, signal.SIGKILL)
        except ProcessLookupError:
            pass


# A process that moves itself into a group of its own, as GNU `timeout` (without
# --foreground) does: it writes its pid, leaves the check's group, then sleeps.
_OWN_GROUP = ("perl -e 'open(F, \">\", shift) or die; print F $$; close F; setpgrp(0, 0); "
              "exec \"sleep\", \"300\"' {pidfile}")


def test_a_signal_ends_a_process_that_left_the_checks_group(world, order, tmp_path):  # noqa: F811
    """A check whose command runs a process in a group of its own (`timeout` does this):
    stopping task_checks ends that process too, and the caller's group lives on."""
    import os
    import signal
    # "; echo": bash forks it as a child (a lone command would be exec'd and stay the
    # session's leader, which cannot change group), as a script runs `timeout`
    p, sentinel, pidfile = _caller_with_check(world, order, tmp_path, _OWN_GROUP + "; echo ended")
    try:
        moved = int(pidfile.read_text().strip())
        for _ in range(50):                       # wait until it has left the check's group
            if os.getpgid(moved) == moved:
                break
            __import__("time").sleep(0.1)
        assert os.getpgid(moved) == moved
        p.send_signal(signal.SIGTERM)
        p.wait(timeout=20)
        assert _gone(moved), "a process in its own group outlived the stopped check"
        os.kill(sentinel, 0)                      # the caller's group is untouched
    finally:
        for pid in (sentinel, p.pid, int(pidfile.read_text().strip() or 0)):
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


@pytest.mark.skipif(not sys.platform.startswith("linux"),
                    reason="only Linux lets task_checks adopt a check's orphans (a child subreaper)")
def test_a_finished_check_ends_what_it_left_in_another_group(world, order, tmp_path):  # noqa: F811
    """A check that ends while a process it started in a group of its own still runs:
    that process ends with the check."""
    import os
    import signal
    p, sentinel, pidfile = _caller_with_check(world, order, tmp_path, _OWN_GROUP + " & sleep 1")
    try:
        p.wait(timeout=30)
        assert p.returncode == 0
        leftover = int(pidfile.read_text().strip())
        assert _gone(leftover), "a process the check left in another group outlived it"
    finally:
        for pid in (sentinel, int(pidfile.read_text().strip() or 0)):
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


def test_a_process_without_children_has_no_descendants():
    """The walk does not count its own process listing (ps is a child too): with no
    check running, nothing is found, so the end stops at once instead of looping."""
    import subprocess
    from pathlib import Path
    backend = Path(task_checks.__file__).resolve().parents[1]
    out = subprocess.run([sys.executable, "-c", "from scripts import task_checks; print(task_checks._descendants())"],
                         cwd=str(backend), capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "[]"


def test_a_check_run_in_process_leaves_the_callers_children_alone(world, order):  # noqa: F811
    """A caller that runs task_checks in its own process keeps the children it had
    before the check: the end takes only what the check started."""
    import signal
    import subprocess
    order({"setup": [], "validations": [{"id": "ok", "command": "true", "repo": "lib", "cwd": ""}]})
    mine = subprocess.Popen(["sleep", "60"])
    try:
        assert task_checks.main(["run", W1, "ok"]) == 0
        assert mine.poll() is None, "a child the caller had before the check was ended"
    finally:
        mine.kill()
        mine.wait()
        for sig in (signal.SIGTERM, signal.SIGALRM, signal.SIGHUP, signal.SIGQUIT):
            signal.signal(sig, signal.SIG_DFL)      # main() set them for its own process


def test_a_check_run_in_process_keeps_the_callers_exit_statuses(world, order):  # noqa: F811
    """The end collects only the check's ended processes: a child the caller had, which
    ended before the check, still reports its own exit code to the caller."""
    import signal
    import subprocess
    import time
    order({"setup": [], "validations": [{"id": "ok", "command": "true", "repo": "lib", "cwd": ""}]})
    mine = subprocess.Popen(["sh", "-c", "exit 3"])
    time.sleep(0.5)                                  # ended, not yet collected
    try:
        assert task_checks.main(["run", W1, "ok"]) == 0
        assert mine.wait(timeout=10) == 3
    finally:
        for sig in (signal.SIGTERM, signal.SIGALRM, signal.SIGHUP, signal.SIGQUIT):
            signal.signal(sig, signal.SIG_DFL)


def test_a_signal_during_the_end_acts_only_after_it(monkeypatch):
    """A second stop that arrives while a check is being ended waits until the end is
    done: it cannot leave the check half-killed."""
    import signal
    fired = []
    seen_inside = []

    def tree():
        signal.raise_signal(signal.SIGTERM)      # to this thread, which holds it
        seen_inside.append(bool(fired))          # a held signal has not acted yet
        return []

    old = signal.signal(signal.SIGTERM, lambda *_: fired.append(True))
    try:
        monkeypatch.setattr(task_checks, "_tree", tree)
        task_checks._end_group(None)
        assert seen_inside == [False]
        assert fired == [True]                   # and it acted once the end was done
    finally:
        signal.signal(signal.SIGTERM, old)


def test_without_a_process_table_the_checks_group_is_still_ended(monkeypatch):
    """When the processes cannot be listed (no ps), the check's own group is still
    killed, as before the walk existed."""
    import os
    import signal
    killed = []

    def no_table():
        raise FileNotFoundError("ps")

    monkeypatch.setattr(task_checks, "_tree", no_table)
    monkeypatch.setattr(os, "killpg", lambda pg, sig: killed.append((pg, sig)))
    task_checks._end_group(type("C", (), {"pid": 4242})())
    assert killed == [(4242, signal.SIGKILL)]


def test_main_in_process_does_not_adopt_the_callers_orphans(world, order, monkeypatch):  # noqa: F811
    """Only the command makes itself a child subreaper: a caller that runs main() in
    its own process keeps its role."""
    import signal
    order({"setup": [], "validations": [{"id": "ok", "command": "true", "repo": "lib", "cwd": ""}]})
    monkeypatch.setattr(task_checks, "_adopt_orphans", lambda: (_ for _ in ()).throw(AssertionError("adopted")))
    try:
        assert task_checks.main(["run", W1, "ok"]) == 0
    finally:
        for sig in (signal.SIGTERM, signal.SIGALRM, signal.SIGHUP, signal.SIGQUIT):
            signal.signal(sig, signal.SIG_DFL)


def test_a_handler_raising_while_signals_are_held_releases_them(monkeypatch):
    """A pending handler that raises inside the hold (CPython runs pending ones as the
    mask is set) leaves the signals as they were, not held for good."""
    import signal
    real = signal.pthread_sigmask

    def raising(how, sigs):
        got = real(how, sigs)
        if how == signal.SIG_BLOCK and signal.SIGTERM in set(sigs):
            raise KeyboardInterrupt
        return got

    before = real(signal.SIG_BLOCK, ())
    monkeypatch.setattr(signal, "pthread_sigmask", raising)
    with pytest.raises(KeyboardInterrupt):
        with task_checks._held():
            pass
    monkeypatch.setattr(signal, "pthread_sigmask", real)
    assert real(signal.SIG_BLOCK, ()) == before
