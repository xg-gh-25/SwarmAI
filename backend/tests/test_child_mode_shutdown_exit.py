"""In subprocess mode (the desktop app's child on Windows/Linux) POST /shutdown is
followed, after its response, by the lifespan's teardown, the kill of the backend's
own children and the process exit, in that order and bounded below the app's 3 s
exit ceiling. In daemon, hive and dev mode nothing exits.

The exit, the kills and the teardown are replaced by recorders: no test ends the
test process or touches a live backend. A bare ``TestClient(main.app)`` (no
``with``) serves the route without running the real lifespan.
"""
from __future__ import annotations

import asyncio
import subprocess
import sys
import time
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

import main

# what the desktop app sends with its /shutdown (desktop/src-tauri/src/lib.rs)
_APP = {"X-Swarm-User-Action": "shutdown"}


@pytest.fixture
def calls(monkeypatch):
    """Record the exit-path steps instead of running them."""
    seen: list[str] = []

    async def teardown():
        seen.append("teardown")

    async def kill_children():
        seen.append("kill")

    def exit_process(code=0):
        seen.append(f"exit:{code}")

    async def schedule():
        seen.append("schedule")

    monkeypatch.setattr(main, "_run_teardown_once", teardown)
    monkeypatch.setattr(main, "_kill_own_children", kill_children)
    monkeypatch.setattr(main, "_exit_process", exit_process)
    monkeypatch.setattr(main, "_schedule_child_exit", schedule)
    monkeypatch.setattr(main, "_child_exit_task", None)
    monkeypatch.setattr(main.session_registry, "disconnect_all", AsyncMock())
    return seen


def test_subprocess_shutdown_schedules_exit_after_response(monkeypatch, calls):
    """Subprocess mode: /shutdown answers, then (as its background task) starts the
    exit once. Fails before the fix: there was no exit at all."""
    monkeypatch.setenv("SWARMAI_MODE", "subprocess")
    resp = TestClient(main.app).post("/shutdown", headers=_APP)
    assert resp.status_code == 200
    assert resp.json() == {"status": "shutting_down"}
    assert calls == ["schedule"]
    main.session_registry.disconnect_all.assert_awaited_once()


def test_real_scheduler_starts_the_exit_from_the_http_response(monkeypatch):
    """The real scheduler, run as the response's background task over HTTP, starts the
    exit task on the server's event loop (a sync background task would run in a
    worker thread with no loop and fail there)."""
    started = []

    def exit_after_shutdown():
        started.append("exit")
        return asyncio.sleep(0)

    monkeypatch.setenv("SWARMAI_MODE", "subprocess")
    monkeypatch.setattr(main.session_registry, "disconnect_all", AsyncMock())
    monkeypatch.setattr(main, "_exit_after_shutdown", exit_after_shutdown)
    monkeypatch.setattr(main, "_child_exit_task", None)
    resp = TestClient(main.app, raise_server_exceptions=True).post("/shutdown", headers=_APP)
    assert resp.status_code == 200
    assert started == ["exit"]
    assert main._child_exit_task is not None


async def test_exit_is_the_responses_background_task(monkeypatch, calls):
    """The handler body never starts the exit itself: it is attached to the response,
    which Starlette runs only after the body has been sent."""
    monkeypatch.setenv("SWARMAI_MODE", "subprocess")
    resp = await main.shutdown(user_action="shutdown")
    assert calls == []
    assert resp.background is not None
    assert resp.background.func is main._schedule_child_exit


@pytest.mark.parametrize("mode", ["daemon", "hive", "dev"])
async def test_supervised_modes_never_exit(monkeypatch, calls, mode):
    """daemon / hive: still 403 with no teardown; dev: still only disconnects. None
    of them attaches or schedules an exit, tears down or exits. (The handler is
    called directly: in hive mode the auth middleware would answer first.)"""
    monkeypatch.setenv("SWARMAI_MODE", mode)
    resp = await main.shutdown(user_action="shutdown")
    if mode == "dev":
        assert resp == {"status": "shutting_down"}
        main.session_registry.disconnect_all.assert_awaited_once()
    else:
        assert resp.status_code == 403
        assert resp.background is None
        main.session_registry.disconnect_all.assert_not_awaited()
    assert calls == []


def test_daemon_mode_http_shutdown_is_refused(monkeypatch, calls):
    monkeypatch.setenv("SWARMAI_MODE", "daemon")
    resp = TestClient(main.app).post("/shutdown", headers=_APP)
    assert resp.status_code == 403
    assert calls == []


async def test_exit_sequence_teardown_then_kill_then_exit(calls):
    await main._exit_after_shutdown()
    assert calls == ["teardown", "kill", "exit:0"]


async def test_teardown_overrun_is_cancelled_and_exit_still_runs(monkeypatch, calls):
    cancelled = []

    async def hanging_teardown():
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            cancelled.append(True)
            raise

    monkeypatch.setattr(main, "_run_teardown_once", hanging_teardown)
    monkeypatch.setattr(main, "_CHILD_EXIT_TEARDOWN_BUDGET_S", 0.05)
    t0 = time.monotonic()
    await main._exit_after_shutdown()
    assert time.monotonic() - t0 < 2.0
    assert cancelled == [True]
    assert calls == ["kill", "exit:0"]


async def test_teardown_error_still_kills_and_exits(monkeypatch, calls):
    async def failing_teardown():
        raise RuntimeError("boom")

    monkeypatch.setattr(main, "_run_teardown_once", failing_teardown)
    await main._exit_after_shutdown()
    assert calls == ["kill", "exit:0"]


async def test_exit_and_teardown_run_once(monkeypatch):
    """A second /shutdown starts no second exit task, and the teardown body runs once
    whether the lifespan or the exit asks for it, even concurrently."""
    runs = []

    async def exit_after_shutdown():
        runs.append("exit")

    monkeypatch.setattr(main, "_exit_after_shutdown", exit_after_shutdown)
    monkeypatch.setattr(main, "_child_exit_task", None)
    await main._schedule_child_exit()
    first = main._child_exit_task
    await main._schedule_child_exit()
    assert main._child_exit_task is first
    await first
    assert runs == ["exit"]

    bodies = []

    async def body():
        bodies.append(1)
        await asyncio.sleep(0.01)

    monkeypatch.setitem(main._teardown_holder, "fn", body)
    monkeypatch.setitem(main._teardown_holder, "task", None)
    await asyncio.gather(main._run_teardown_once(), main._run_teardown_once())
    await main._run_teardown_once()
    assert bodies == [1]


async def test_teardown_without_lifespan_is_a_no_op(monkeypatch):
    monkeypatch.setitem(main._teardown_holder, "fn", None)
    monkeypatch.setitem(main._teardown_holder, "task", None)
    await main._run_teardown_once()
    assert main._teardown_holder["task"] is None


async def test_tracked_pids_killed_before_descendant_sweep(monkeypatch):
    order = []

    class FakeLifecycle:
        async def _kill_tracked_pids(self):
            order.append("tracked")

    monkeypatch.setattr(main.session_registry, "lifecycle_manager", FakeLifecycle())
    monkeypatch.setattr(main, "_kill_descendants", lambda pid: order.append(("tree", pid)) or 0)
    await main._kill_own_children()
    assert order == ["tracked", ("tree", main.os.getpid())]


@pytest.mark.skipif(sys.platform == "win32", reason="uses a POSIX shell to build the tree")
def test_kill_descendants_kills_whole_tree(monkeypatch):
    """A real tree: a shell with two sleeping children. Every descendant is killed."""
    import psutil
    from core import kill_audit

    monkeypatch.setattr(kill_audit, "record", lambda *a, **k: None)
    root = subprocess.Popen(["sh", "-c", "sleep 60 & sleep 60 & wait"])
    try:
        deadline = time.monotonic() + 5
        while len(psutil.Process(root.pid).children(recursive=True)) < 2:
            assert time.monotonic() < deadline, "the test tree never started"
            time.sleep(0.02)
        kids = psutil.Process(root.pid).children(recursive=True)
        assert main._kill_descendants(root.pid) == 2
        gone, alive = psutil.wait_procs(kids, timeout=2)
        assert alive == []
    finally:
        root.kill()
        root.wait(timeout=5)


@pytest.mark.parametrize("mode", ["subprocess", "dev"])
def test_shutdown_without_the_app_header_is_refused(monkeypatch, calls, mode):
    """A web page cannot send X-Swarm-User-Action cross-site: a /shutdown without it
    disconnects nothing and starts no exit."""
    monkeypatch.setenv("SWARMAI_MODE", mode)
    resp = TestClient(main.app).post("/shutdown")
    assert resp.status_code == 403
    main.session_registry.disconnect_all.assert_not_awaited()
    assert calls == []


def test_each_lifespan_in_a_process_runs_its_own_teardown(monkeypatch):
    """Two lifespans one after the other (as the tests' client fixture runs them):
    each one's exit runs its teardown, not only the first one's."""

    teardowns = []
    real_register = main._register_teardown

    def register(fn):
        if fn is None:
            return real_register(None)

        async def counted():
            teardowns.append(1)
            await fn()

        return real_register(counted)

    monkeypatch.setattr(main, "_register_teardown", register)
    for _ in range(2):
        with TestClient(main.app):
            pass
    assert teardowns == [1, 1]
    assert main._teardown_holder == {"fn": None, "task": None}
