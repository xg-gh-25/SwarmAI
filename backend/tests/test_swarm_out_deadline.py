"""One deadline for a whole Swarm Out dispatch (core/swarm_out.DISPATCH_DEADLINE_S).

The dispatch CLI stops waiting after ``scripts/swarm_out.DISPATCH_TIMEOUT_S`` and then
can only say the task "may be running". Questions: (1) does a step that runs past the
time left fail within the deadline, naming the step and saying nothing was sent, and
release a work order it had asked for; (2) is the deadline one budget across the
steps of ``dispatch_spec``, not a cap per step; (3) is a step that cannot fit never
started; (4) does a release the Hive never answers still leave the answer in time;
(5) does the session keep its window and a session not named in time stay "may be
running", with the work order kept; (6) does the backend answer before the CLI stops
waiting. Small deadlines stand in for the real ones; the fake Hive sleeps.
"""
from __future__ import annotations

import asyncio
import json
import time

import httpx
import pytest

from core import swarm_out as so
from tests import _hive_work_fake as _work
from tests.test_swarm_out_host import INST
from tests.test_task_spec import _brain, ws  # noqa: F401 — the Brain fixture



@pytest.fixture
def hive(monkeypatch, tmp_path):
    """A fake Hive whose answers can be slowed per path; ``seen`` lists the paths asked."""
    state = {"delay": {}, "status": {}, "seen": [], "names_session": True}

    async def fake_instance(ref):
        return so._validate_instance(INST, {"id": "a1"})
    monkeypatch.setattr(so, "_instance", fake_instance)
    monkeypatch.setattr(so, "_state_dir", lambda: tmp_path)

    async def handler(request: httpx.Request):
        path = request.url.path
        state["seen"].append(path)
        key = "release" if path.endswith("/release") else path
        hook = state.get("on_" + ("release" if key == "release" else "prepare" if path == "/api/hive/work" else ""))
        if hook:
            hook(path)
        await asyncio.sleep(state["delay"].get(key, 0))
        if key in state["status"]:
            return httpx.Response(state["status"][key], json={"detail": "no"})
        if path == "/api/chat/sessions/admission-state":
            return httpx.Response(200, json=_work.ADMISSION)
        if _work.answer(request) is not None:
            return _work.answer(request)
        if path == "/api/chat/stream":
            if not state["names_session"]:
                await asyncio.sleep(30)
            body = f"data: {json.dumps({'type': 'session_start', 'session_id': 'sess-42'})}\n\n"
            return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})
        return httpx.Response(404)

    def client(inst, timeout=None):
        return httpx.AsyncClient(base_url=so._base(inst), headers=so._auth(inst),
                                 transport=httpx.MockTransport(handler))
    monkeypatch.setattr(so, "_client", client)
    # tests use deadlines of a second; the smallest step worth starting scales with them
    monkeypatch.setattr(so, "_MIN_STEP_S", 0.01, raising=False)
    state["dir"] = tmp_path
    return state


def _deadline(monkeypatch, total, session):
    monkeypatch.setattr(so, "DISPATCH_DEADLINE_S", total, raising=False)
    monkeypatch.setattr(so, "SESSION_ID_WAIT_S", session)


def _timed(coro):
    t0 = time.monotonic()
    try:
        return asyncio.run(coro), time.monotonic() - t0
    except BaseException as e:            # the caller asserts on what was raised
        e.elapsed = time.monotonic() - t0
        raise


def test_a_slow_prepare_fails_within_the_deadline_and_releases_the_work_order(hive, monkeypatch):
    _deadline(monkeypatch, 1.0, 0.2)
    hive["delay"]["/api/hive/work"] = 3.0            # a first clone: longer than the deadline
    with pytest.raises(so.SwarmOutError) as e:
        _timed(so.dispatch("worker-1", "Fix it", "do X"))
    assert e.value.elapsed < 1.5
    assert not isinstance(e.value, so.SwarmOutMaybeSent)
    msg = str(e.value)
    assert "preparing the workspace" in msg and "nothing was sent" in msg and "worker-1" in msg
    assert "release" in hive["seen"][-1]              # the work order it asked for is released
    assert not (hive["dir"] / "dispatches.jsonl").exists()


def test_one_deadline_is_shared_by_every_step_of_dispatch_spec(hive, monkeypatch, ws):
    from core import brain_sync_host as host
    from core.project_agent import revision
    rev = revision(_brain(ws))
    _deadline(monkeypatch, 1.0, 0.1)

    async def hive_brains(inst):
        await asyncio.sleep(0.3)
        return {"supported": True, "brains": {"Lib": {"digest": "0" * 64, "current_digest": "0" * 64}}}

    async def sync(inst, brain, ws=None):
        await asyncio.sleep(0.3)

    async def can_prepare(inst):
        return None
    monkeypatch.setattr(host, "hive_brains", hive_brains)
    monkeypatch.setattr(host, "sync", sync)
    monkeypatch.setattr(so, "_require_work_orders", can_prepare)
    # each step is far below its own cap; together they are past the deadline
    hive["delay"]["/api/hive/work"] = 0.6
    with pytest.raises(so.SwarmOutError) as e:
        _timed(so.dispatch_spec("worker-1", "t", "Lib", "fix it", "tests pass", execution="native",
                                returns="commits", seen_revision=rev))
    assert e.value.elapsed < 1.5
    assert "preparing the workspace" in str(e.value) and "nothing was sent" in str(e.value)
    assert not (hive["dir"] / "dispatches.jsonl").exists()


def test_a_slow_bundle_upload_fails_within_the_deadline(hive, monkeypatch, tmp_path):
    from core import repo_ship
    _deadline(monkeypatch, 0.5, 0.1)
    bundle = tmp_path / "x.bundle"

    def pack(checkout, url, branch, sha, full=False, timeout=None):
        bundle.write_bytes(b"b")
        return bundle, None, 1

    async def put(inst, r, path, size):
        await asyncio.sleep(3.0)
        return 200, ""
    monkeypatch.setattr(repo_ship, "pack", pack)
    monkeypatch.setattr(so, "_put_repo", put)
    repos = [{"name": "lib", "url": "https://github.com/acme/lib.git", "ref": "main", "sha": "a" * 40,
              "checkout": str(tmp_path)}]
    with pytest.raises(so.SwarmOutError) as e:
        _timed(so._ship_repos({"name": "worker-1"}, repos))
    assert e.value.elapsed < 1.0
    assert "lib" in str(e.value) and "nothing was sent" in str(e.value)
    assert not bundle.exists()                        # the bundle is removed all the same


def test_the_bundle_git_is_bounded_by_the_time_left(hive, monkeypatch, tmp_path):
    from core import repo_ship
    _deadline(monkeypatch, 5.0, 0.1)
    got = {}

    def pack(checkout, url, branch, sha, full=False, timeout=None):
        got["timeout"] = timeout
        return None, sha, 0                           # the remote has it: nothing to send
    monkeypatch.setattr(repo_ship, "pack", pack)
    repos = [{"name": "lib", "url": "u", "ref": "main", "sha": "a" * 40, "checkout": str(tmp_path)}]
    asyncio.run(so._ship_repos({"name": "worker-1"}, repos))
    assert got["timeout"] is not None and got["timeout"] <= 5.0 - 0.1


def test_every_git_call_of_a_pack_is_bounded_by_its_timeout(monkeypatch, tmp_path):
    from core import repo_ship
    sha = "a" * 40
    seen = []

    def git(cwd, *args, timeout=60):
        seen.append((args[0], timeout))
        if args[:2] == ("config", "--get-regexp"):
            return "remote.origin.url https://github.com/acme/lib.git\n"
        if args[0] == "merge-base":
            return "b" * 40 + "\n"
        if args[:2] == ("bundle", "list-heads"):
            return f"{sha} HEAD\n"
        return ""
    monkeypatch.setattr(repo_ship, "_git", git)
    path, base, _size = repo_ship.pack(tmp_path, "https://github.com/acme/lib.git", "main", sha, timeout=5.0)
    path.unlink()
    assert base == "b" * 40 and {a for a, _ in seen} >= {"config", "rev-parse", "merge-base", "bundle"}
    assert all(t <= 5.0 for _, t in seen), seen     # none keeps its own 60 s past the time left


def test_a_step_that_does_not_fit_is_not_started(hive, monkeypatch):
    _deadline(monkeypatch, 0.5, 60)                  # less than the session's own window
    with pytest.raises(so.SwarmOutError) as e:
        asyncio.run(so.dispatch("worker-1", "Fix it", "do X"))
    assert "ran out of time before" in str(e.value) and "nothing was sent" in str(e.value)
    assert hive["seen"] == []                         # nothing was asked of the Hive


def test_a_hanging_release_does_not_hold_the_answer(hive, monkeypatch):
    _deadline(monkeypatch, 2.0, 0.2)
    monkeypatch.setattr(so, "RELEASE_WAIT_S", 0.2, raising=False)
    hive["status"]["/api/hive/work"] = 500            # the prepare fails at once ...
    hive["delay"]["release"] = 5.0                    # ... and the release is never answered
    with pytest.raises(so.SwarmOutError, match="could not prepare the workspace") as e:
        _timed(so.dispatch("worker-1", "Fix it", "do X"))
    assert e.value.elapsed < 1.5
    assert "release" in hive["seen"][-1]


def test_session_naming_keeps_its_window_and_still_reports_maybe_sent(hive, monkeypatch):
    _deadline(monkeypatch, 1.2, 0.3)
    hive["delay"]["/api/hive/work"] = 0.6            # fits: the session's window is held back
    hive["names_session"] = False
    with pytest.raises(so.SwarmOutMaybeSent, match="may be running") as e:
        _timed(so.dispatch("worker-1", "Fix it", "do X"))
    assert e.value.elapsed < 1.5
    assert not any(p.endswith("/release") for p in hive["seen"])   # the session may have started
    rows = [json.loads(x) for x in (hive["dir"] / "dispatches.jsonl").read_text().splitlines()]
    assert len(rows) == 1 and rows[0]["session_id"] is None


def test_the_backend_answers_before_the_cli_stops_waiting(monkeypatch):
    import scripts.swarm_out as cli
    # the backend's deadline, plus a release after a failure and the route's own work,
    # stays inside the CLI's wait, which stays inside the agent's 10-minute Bash ceiling
    assert so.DISPATCH_DEADLINE_S + so.RELEASE_WAIT_S + 30 <= cli.DISPATCH_TIMEOUT_S < 600
    # every step cap is larger than nothing; the session's window fits inside the deadline
    assert 0 < so.SESSION_ID_WAIT_S < so.DISPATCH_DEADLINE_S


def test_a_dispatch_that_ran_out_of_time_is_a_plain_failure_end_to_end(hive, monkeypatch, capsys):
    """The route answers the deadline's failure as 400 with its reason, and the CLI prints
    that reason as a failure, never maybe_sent."""
    import io
    import urllib.error
    from fastapi import HTTPException
    import scripts.swarm_out as cli
    from routers import swarm_out as route
    _deadline(monkeypatch, 0.5, 60)
    with pytest.raises(HTTPException) as h:
        asyncio.run(route._call(so.dispatch("worker-1", "Fix it", "do X")))
    assert h.value.status_code == 400 and "ran out of time before" in h.value.detail
    body = json.dumps({"detail": h.value.detail}).encode()

    def fail(*a, **k):
        raise urllib.error.HTTPError("u", h.value.status_code, "x", {}, io.BytesIO(body))
    monkeypatch.setattr(cli.urllib.request, "urlopen", fail)
    assert cli._call("POST", "/dispatch", {}) == 1
    out = json.loads(capsys.readouterr().out)
    assert out == {"ok": False, "status": 400, "detail": h.value.detail}


def test_a_prepare_given_up_on_is_released_again_once_it_can_have_finished(hive, monkeypatch):
    """The Hive's prepare goes on after the host gives up (its thread is not stopped) and
    makes the workspace again after the first release: the second release removes it.
    The fake Hive keeps the orders it holds; its prepare ends after the host gave up and
    after the first release, and before LATE_RELEASE_S."""
    _deadline(monkeypatch, 1.0, 0.2)
    monkeypatch.setattr(so, "LATE_RELEASE_S", 0.8, raising=False)
    hive["delay"]["/api/hive/work"] = 3.0             # the host's request hangs ...
    orders, history = set(), []

    def on_prepare(path):                             # ... while the Hive's thread goes on
        asyncio.get_running_loop().call_later(1.1, lambda: (orders.add("w"), history.append("made")))

    def on_release(path):
        history.append("released" if "w" in orders else "released-nothing")
        orders.discard("w")
    hive["on_prepare"], hive["on_release"] = on_prepare, on_release

    async def run():
        with pytest.raises(so.SwarmOutError, match="preparing the workspace"):
            await so.dispatch("worker-1", "Fix it", "do X")
        await asyncio.sleep(1.4)
    asyncio.run(run())
    assert history == ["released-nothing", "made", "released"] and not orders


def test_a_refused_session_step_releases_the_work_order(hive, monkeypatch):
    """Too little time left to start the session: nothing was sent, so the prepared work
    order is released and no row is recorded."""
    class SessionDoesNotFit(so._Deadline):
        def budget(self, step, cap, keep=0.0):
            if step.startswith("starting the session"):
                raise so.SwarmOutError(f"the dispatch ran out of time before {step}; nothing was sent")
            return super().budget(step, cap, keep)
    monkeypatch.setattr(so, "SESSION_ID_WAIT_S", 0.5)
    with pytest.raises(so.SwarmOutError, match="before starting the session") as e:
        asyncio.run(so.dispatch("worker-1", "Fix it", "do X", deadline=SessionDoesNotFit(30)))
    assert not isinstance(e.value, so.SwarmOutMaybeSent)
    assert "/api/chat/stream" not in hive["seen"] and hive["seen"][-1].endswith("/release")
    assert not (hive["dir"] / "dispatches.jsonl").exists()
