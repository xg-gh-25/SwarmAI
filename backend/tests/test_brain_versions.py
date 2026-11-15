"""A Hive installs a new version of a Brain while turns still use the old one
(core/brain_use, core/brain_versions, routers/hive_brains; design
docs/design/2026-10-28-project-agent-flow.md §6 "安装和 turn 共存").

Questions: (1) with a turn holding version A, does an install of B land (200, B on the
stable path); (2) does the holding session still read A through every Brain reader
(view, addressing / knowledge find, DDD recall, the Brain gates, a path:line ref) while
a new turn reads B; (3) is A removed once its last turn ends, and not before, and does
the session's next turn read B; (4) is a second install while one runs still refused;
(5) does GET /api/hive/brains count the turns of each version; (6) does a Hive's start
empty the store; (7) does a copy that cannot be made install nothing, does a planted
link redirect nothing; (8) is a new session's turn marked once its id arrives, and a
turn whose Brain cannot be told kept on its version; (9) does the host dispatch a
second task of a changed Brain to a busy Hive, and do overlapping host pushes of one
Brain wait for each other instead of getting 423.
"""
from __future__ import annotations

import asyncio
import threading

import pytest

from core import brain_sync as bs
from core import brain_use, brain_versions
from tests.test_brain_sync import _brain, _client_for, hive_app  # noqa: F401 — fixture


@pytest.fixture(autouse=True)
def _clean_registry():
    yield
    for d in (brain_use._live, brain_use._kept, brain_use._session, brain_use._order,
              brain_use._reserved, brain_use._pending, brain_use._installing):
        d.clear()
    brain_use._any.clear()
    brain_use._failed.clear()
    brain_use._workspace = None


@pytest.fixture
def hive(hive_app, monkeypatch, tmp_path):
    """The Hive's app and workspace, with the readers pointed at that workspace."""
    import core.project_registry as pr
    app, ws, refreshed, hb = hive_app
    monkeypatch.setenv("SWARMAI_MODE", "hive")
    monkeypatch.delenv("SWARM_APP_SESSION_ID", raising=False)
    monkeypatch.setattr(pr, "get_swarmws", lambda: ws)
    host = tmp_path / "host"
    _brain(host)
    return app, ws, host / "Projects" / "Lib"


def _files(host_brain, label: str, gate: str = "import sys\nsys.exit(0)\n") -> dict[str, bytes]:
    """The host Brain's package with AGENTS.md, PRODUCT.md and the gate set to *label*."""
    (host_brain / "AGENTS.md").write_text(f"# Lib {label}\n")
    (host_brain / "2-understanding" / "PRODUCT.md").write_text(
        f"# PRODUCT\n\n## Vision\n\n- [decision] **{label} marker{label}** the {label} version\n")
    (host_brain / "3-gates" / "no_push.py").write_text(gate)
    return bs.collect(host_brain, False)


async def _put(app, files: dict[str, bytes]):
    async with _client_for(app) as c:
        return await c.put("/api/hive/brains/Lib", content=bs.pack(files),
                           headers={"X-Brain-Digest": bs.content_digest(files)})


async def _settle():
    """Wait for every cleanup the registry scheduled."""
    while brain_use._pending:
        await asyncio.wait([f for _lp, f in list(brain_use._pending.values())])


def _agents(d):
    return (d / "AGENTS.md").read_text()


def _store(ws):
    return ws / "Hive" / "brains" / "Lib"


def test_an_install_lands_while_a_turn_holds_the_brain(hive):
    app, ws, host = hive
    live = ws / "Projects" / "Lib"

    async def go():
        assert (await _put(app, _files(host, "alpha"))).status_code == 200
        await brain_use.hold("Lib", "t1", "sess-1")
        b = _files(host, "beta")
        r = await _put(app, b)
        assert r.status_code == 200, r.text
        assert r.json()["digest"] == bs.content_digest(b)
        assert _agents(live) == "# Lib beta\n"
        assert brain_use.in_use("Lib") == 1
        brain_use.release("Lib", "t1")
        await _settle()
    asyncio.run(go())


def test_the_holding_session_reads_its_version_and_a_new_turn_the_newest(hive, monkeypatch):
    from core import addressing, cognition, recall_multi
    from core.project_agent_session import gates_snapshot, run_gates
    app, ws, host = hive
    live = ws / "Projects" / "Lib"
    refuse = "import sys\nsys.stderr.write('no')\nsys.exit(2)\n"

    async def go():
        await _put(app, _files(host, "alpha"))
        await brain_use.hold("Lib", "t1", "sess-1")
        pin = {"brain": "Lib", "gates": ["no_push"], "gates_digest": gates_snapshot(live)[0]}
        assert (await _put(app, _files(host, "beta", gate=refuse))).status_code == 200
        await brain_use.hold("Lib", "t2", "sess-2")            # a turn that starts now
        return pin
    pin = asyncio.run(go())
    mine = brain_versions.view(ws, "Lib", live, "sess-1")
    assert mine != live and _agents(mine) == "# Lib alpha\n"
    assert brain_versions.view(ws, "Lib", live, "sess-2") == live and _agents(live) == "# Lib beta\n"
    # the Brain gates of each session: the holding one still runs A's gates under its pin
    tool = {"tool_name": "Bash", "tool_input": {"command": "ls"}, "cwd": str(ws)}
    assert run_gates(ws, pin, tool, "sess-1") is None
    assert "were changed" in run_gates(ws, pin, tool, "sess-2")
    # the readers a session's own processes use (knowledge find, DDD recall, get by path)
    monkeypatch.setenv("SWARM_APP_SESSION_ID", "sess-1")
    assert addressing.brain_root("Lib") == mine
    texts = " ".join(u.text for u in addressing.ddd_units("Lib").units)
    assert "markeralpha" in texts and "markerbeta" not in texts
    assert addressing.ddd_units("Lib").project == "Lib"
    _base, docs = recall_multi._load_ddd_docs_text("Lib")
    assert "markeralpha" in docs["PRODUCT.md"]
    got = addressing.matches("Lib", f"{live}/2-understanding/PRODUCT.md:5")
    assert got and "markeralpha" in got[0].text
    assert cognition._brain_of((mine / "AGENTS.md").resolve()) == "Lib"
    monkeypatch.setenv("SWARM_APP_SESSION_ID", "sess-2")
    assert addressing.brain_root("Lib") == live
    assert "markerbeta" in recall_multi._load_ddd_docs_text("Lib")[1]["PRODUCT.md"]


def test_a_kept_version_is_removed_after_its_last_turn(hive):
    app, ws, host = hive
    live = ws / "Projects" / "Lib"

    async def go():
        await _put(app, _files(host, "alpha"))
        await brain_use.hold("Lib", "t1", "s1")
        await brain_use.hold("Lib", "t2", "s2")
        await _put(app, _files(host, "beta"))
        versions = list((_store(ws) / "versions").iterdir())
        assert len(versions) == 1 and sorted(p.name for p in (_store(ws) / "turns").iterdir()) == ["s1", "s2"]
        brain_use.release("Lib", "t1")
        # s1's next turn (waiting for the cleanup of t1's marker) reads the newest; s2
        # still reads alpha
        await brain_use.hold("Lib", "t3", "s1")
        assert brain_versions.view(ws, "Lib", live, "s1") == live
        assert _agents(brain_versions.view(ws, "Lib", live, "s2")) == "# Lib alpha\n"
        assert versions[0].is_dir()
        brain_use.release("Lib", "t2")
        await _settle()
        assert not versions[0].exists() and list((_store(ws) / "turns").iterdir()) == []
        assert brain_versions.view(ws, "Lib", live, "s2") == live
        brain_use.release("Lib", "t3")
        await _settle()
    asyncio.run(go())


def test_a_second_concurrent_install_is_refused(hive, monkeypatch):
    app, ws, host = hive
    live = ws / "Projects" / "Lib"
    started, go_on = threading.Event(), threading.Event()
    real = bs.install

    def slow(*a):
        started.set()
        go_on.wait(5)
        return real(*a)

    async def go():
        await _put(app, _files(host, "alpha"))
        await brain_use.hold("Lib", "t1", "s1")
        monkeypatch.setattr(bs, "install", slow)
        first = asyncio.create_task(_put(app, _files(host, "beta")))
        while not started.is_set():
            await asyncio.sleep(0.01)
        second = await _put(app, _files(host, "gamma"))
        go_on.set()
        assert (await first).status_code == 200
        assert second.status_code == 423 and "being installed" in second.json()["detail"]
        assert _agents(live) == "# Lib beta\n"
        brain_use.release("Lib", "t1")
        await _settle()
    asyncio.run(go())


def test_the_report_counts_the_turns_of_each_version(hive):
    app, ws, host = hive

    async def go():
        a, b = _files(host, "alpha"), None
        await _put(app, a)
        await brain_use.hold("Lib", "t1", "s1")
        b = _files(host, "beta")
        await _put(app, b)
        await brain_use.hold("Lib", "t2", "s2")
        async with _client_for(app) as c:
            row = (await c.get("/api/hive/brains")).json()["brains"]
        row = next(r for r in row if r["brain"] == "Lib")
        brain_use.release("Lib", "t1")
        brain_use.release("Lib", "t2")
        await _settle()
        return row, bs.content_digest(a), bs.content_digest(b)
    row, da, db = asyncio.run(go())
    assert row["digest"] == db and row["current_digest"] == db and row["running"] == 2
    assert row["versions"] == [{"digest": db, "current": True, "running": 1},
                               {"digest": da, "current": False, "running": 1}]


def test_startup_clears_kept_versions(tmp_path):
    ws = tmp_path / "ws"
    (ws / "Hive" / "brains" / "Lib" / "versions" / ("a" * 16)).mkdir(parents=True)
    (ws / "Hive" / "brains" / "Lib" / "turns").mkdir()
    (ws / "Hive" / "brains" / "Lib" / "turns" / "s1").write_text("a" * 16)
    (ws / "Hive" / "work").mkdir()
    brain_versions.clear(ws)
    assert not (ws / "Hive" / "brains").exists() and (ws / "Hive" / "work").is_dir()
    brain_versions.clear(tmp_path / "missing")          # nothing there: nothing done


def test_an_install_whose_copy_fails_writes_nothing(hive, monkeypatch):
    app, ws, host = hive
    live = ws / "Projects" / "Lib"

    def broken(*a):
        raise OSError(28, "No space left on device")

    async def go():
        await _put(app, _files(host, "alpha"))
        await brain_use.hold("Lib", "t1", "s1")
        monkeypatch.setattr(brain_versions, "keep", broken)
        r = await _put(app, _files(host, "beta"))
        assert r.status_code == 409 and "could not be kept" in r.json()["detail"]
        assert _agents(live) == "# Lib alpha\n" and brain_use.begin_install("Lib")
        brain_use.end_install("Lib")
        brain_use.release("Lib", "t1")
        await _settle()
    asyncio.run(go())


def test_a_planted_link_in_the_store_is_not_followed(hive, tmp_path):
    import os
    app, ws, host = hive
    live = ws / "Projects" / "Lib"
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()

    async def go():
        await _put(app, _files(host, "alpha"))
        os.symlink(elsewhere, ws / "Hive")                # an agent's link where the store goes
        await brain_use.hold("Lib", "t1", "s1")
        r = await _put(app, _files(host, "beta"))
        assert r.status_code == 409 and list(elsewhere.iterdir()) == []
        brain_use.release("Lib", "t1")
        await _settle()
    asyncio.run(go())
    # a marker naming a version reached through a link: the stable path is read
    os.unlink(ws / "Hive")
    vid = "b" * 16
    (_store(ws) / "versions").mkdir(parents=True)
    os.symlink(elsewhere, _store(ws) / "versions" / vid)
    (_store(ws) / "turns").mkdir()
    (_store(ws) / "turns" / "s1").write_text(vid)
    assert brain_versions.view(ws, "Lib", live, "s1") == live
    (_store(ws) / "turns" / "s1").write_text("../../x")
    assert brain_versions.view(ws, "Lib", live, "s1") == live
    assert brain_versions.view(ws, "Lib", live, "../s1") == live


def test_a_new_sessions_turn_is_marked_when_its_session_starts(hive):
    from contextlib import aclosing
    from core.session_router import SessionRouter
    app, ws, host = hive
    live = ws / "Projects" / "Lib"
    gate = asyncio.Event()

    class _Self:
        async def _published_events(self, agen, **_k):
            await gate.wait()
            yield {"type": "turn_start", "sessionId": "new-1"}
            yield {"type": "x", "sessionId": "new-1"}

    async def go():
        await _put(app, _files(host, "alpha"))
        turn = SessionRouter._published_turn(_Self(), None, session_id=None, turn_client_id=None,
                                              source="chat", start_on_session_start=True, writer=None,
                                              project_agent={"brain": "Lib"})
        async with aclosing(turn) as t:
            first = asyncio.ensure_future(t.__anext__())
            await asyncio.sleep(0.01)                 # the turn holds alpha, no session yet
            assert brain_use.in_use("Lib") == 1
            await _put(app, _files(host, "beta"))
            gate.set()
            await first
            await _settle()
            seen = brain_versions.view(ws, "Lib", live, "new-1")
            text = _agents(seen)
        await _settle()
        return seen, text
    seen, text = asyncio.run(go())
    assert seen != live and text == "# Lib alpha\n"
    assert not (_store(ws) / "versions").exists() or list((_store(ws) / "versions").iterdir()) == []


def test_a_turn_whose_brain_cannot_be_told_keeps_its_version(hive):
    app, ws, host = hive
    live = ws / "Projects" / "Lib"

    async def go():
        await _put(app, _files(host, "alpha"))
        await brain_use.hold(brain_use.ANY, "t", "s-any")
        assert (await _put(app, _files(host, "beta"))).status_code == 200
        kept = brain_versions.view(ws, "Lib", live, "s-any")
        assert kept != live and _agents(kept) == "# Lib alpha\n"
        assert (await _put(app, _files(host, "gamma"))).status_code == 200
        assert brain_versions.view(ws, "Lib", live, "s-any") == kept    # still its own version
        brain_use.release(brain_use.ANY, "t")
        await _settle()
        assert not kept.exists()
    asyncio.run(go())


def test_the_host_dispatches_a_changed_brain_to_a_busy_hive(hive_app, monkeypatch, tmp_path):
    """dispatch_spec against the real install endpoint while a turn on the Hive holds the
    Brain: the changed Brain is synced and the task sent, no 'busy'."""
    import core.project_registry as pr
    import core.swarm_out as so
    from core import brain_sync_host as host
    from core.project_agent import revision
    from tests.test_task_spec import _brain as _task_brain
    app, hive_ws, _refreshed, _hb = hive_app
    monkeypatch.setenv("SWARMAI_MODE", "hive")         # the Hive endpoints answer
    w = tmp_path / "hostws"
    (w / "Projects").mkdir(parents=True)
    (w / "Knowledge" / "Library").mkdir(parents=True)
    monkeypatch.setattr(pr, "get_swarmws", lambda: w)
    b = _task_brain(w)
    monkeypatch.setattr(so, "_client", lambda inst, timeout=30: _client_for(app))
    sent = []

    async def instance(ref):
        return {"id": "i1", "name": ref}

    async def dispatch(hive, title, task, origin, repos, **k):
        sent.append(task)
        return {"dispatch_id": "so_1"}

    async def nothing(*a, **k):
        return None

    async def no_cap(inst, cap):
        return False
    monkeypatch.setattr(so, "_instance", instance)
    monkeypatch.setattr(so, "dispatch", dispatch)
    monkeypatch.setattr(so, "_require_work_orders", nothing)
    monkeypatch.setattr(so, "_has_cap", no_cap)

    async def go():
        await host.sync({"id": "i1", "name": "hive3"}, "Lib", w)      # the first task's Brain
        await brain_use.hold("Lib", "turn-of-task-1", "sess-1")      # the first task runs
        (b / "2-understanding" / "PRODUCT.md").write_text("# PRODUCT\n\n- changed by CDF\n")
        out = await so.dispatch_spec("hive3", "t", "Lib", "fix it", "tests pass", execution="native",
                                     returns="commits", seen_revision=revision(b))
        brain_use.release("Lib", "turn-of-task-1")
        await _settle()
        return out
    out = asyncio.run(go())
    assert out["brain_sync"] == "synced" and len(sent) == 1
    assert (hive_ws / "Projects" / "Lib" / "2-understanding" / "PRODUCT.md").read_text() \
        == "# PRODUCT\n\n- changed by CDF\n"


def test_overlapping_host_pushes_wait_for_each_other(hive, monkeypatch):
    import core.swarm_out as so
    from core import brain_sync_host as host
    app, ws, host_brain = hive
    monkeypatch.setattr(so, "_client", lambda inst, timeout=30: _client_for(app))
    installs = []
    real = bs.install

    def slow(*a):
        installs.append(a[1])
        threading.Event().wait(0.3)
        return real(*a)
    monkeypatch.setattr(bs, "install", slow)
    inst = {"id": "i1", "name": "hive3"}
    _files(host_brain, "alpha")

    async def go():
        return await asyncio.gather(host.sync(inst, "Lib", host_brain.parent.parent),
                                    host.sync(inst, "Lib", host_brain.parent.parent))
    one, two = asyncio.run(go())
    assert one["digest"] == two["digest"] and installs == ["Lib"]
    assert _agents(ws / "Projects" / "Lib") == "# Lib alpha\n"


def test_a_ref_cited_from_a_kept_version_resolves_after_its_turn(hive, monkeypatch):
    """A path:line a turn got from its kept version (a citation) names the same file of
    the version the session's later turn reads, after the copy is gone."""
    from core import addressing
    app, ws, host = hive
    live = ws / "Projects" / "Lib"
    monkeypatch.setenv("SWARM_APP_SESSION_ID", "s1")

    async def go():
        await _put(app, _files(host, "alpha"))
        await brain_use.hold("Lib", "t1", "s1")
        await _put(app, _files(host, "beta"))
        cited = f"{addressing.brain_root('Lib')}/2-understanding/PRODUCT.md:5"
        assert "markeralpha" in addressing.matches("Lib", cited)[0].text
        brain_use.release("Lib", "t1")
        await brain_use.hold("Lib", "t2", "s1")
        got = addressing.matches("Lib", cited)
        assert addressing.brain_root("Lib") == live and got and "markerbeta" in got[0].text
        brain_use.release("Lib", "t2")
        await _settle()
    asyncio.run(go())


def test_markers_that_cannot_be_written_install_nothing(hive, monkeypatch):
    app, ws, host = hive
    live = ws / "Projects" / "Lib"
    real = brain_versions.reconcile

    def broken(*a):
        raise OSError(5, "I/O error")

    async def go():
        await _put(app, _files(host, "alpha"))
        await brain_use.hold("Lib", "t1", "s1")
        monkeypatch.setattr(brain_versions, "reconcile", broken)
        r = await _put(app, _files(host, "beta"))
        assert r.status_code == 409 and _agents(live) == "# Lib alpha\n"
        assert brain_use.needs_keep("Lib") and brain_use.versions("Lib") == []   # t1 reads live again
        monkeypatch.setattr(brain_versions, "reconcile", real)
        r = await _put(app, _files(host, "beta"))
        assert r.status_code == 200
        assert _agents(brain_versions.view(ws, "Lib", live, "s1")) == "# Lib alpha\n"
        brain_use.release("Lib", "t1")
        await _settle()
    asyncio.run(go())


def test_an_overlapping_turn_of_the_session_reads_the_newest_when_it_starts(hive):
    app, ws, host = hive
    live = ws / "Projects" / "Lib"

    async def go():
        await _put(app, _files(host, "alpha"))
        await brain_use.hold("Lib", "t1", "s1")
        await _put(app, _files(host, "beta"))
        assert brain_versions.view(ws, "Lib", live, "s1") != live
        await brain_use.hold("Lib", "t2", "s1")              # t1 has not ended
        assert brain_versions.view(ws, "Lib", live, "s1") == live
        brain_use.release("Lib", "t1")
        brain_use.release("Lib", "t2")
        await _settle()
    asyncio.run(go())


def test_a_failed_cleanup_is_made_again_before_the_sessions_next_turn(hive, monkeypatch):
    app, ws, host = hive
    live = ws / "Projects" / "Lib"
    real = brain_versions.reconcile

    def broken(*a):
        raise OSError(5, "I/O error")

    async def go():
        await _put(app, _files(host, "alpha"))
        await brain_use.hold("Lib", "t1", "s1")
        await _put(app, _files(host, "beta"))
        monkeypatch.setattr(brain_versions, "reconcile", broken)
        brain_use.release("Lib", "t1")
        await _settle()
        assert brain_versions.view(ws, "Lib", live, "s1") != live      # the marker stayed
        monkeypatch.setattr(brain_versions, "reconcile", real)
        await brain_use.hold("Lib", "t2", "s1")
        assert brain_versions.view(ws, "Lib", live, "s1") == live
        assert list((_store(ws) / "versions").iterdir()) == []
        brain_use.release("Lib", "t2")
        await _settle()
    asyncio.run(go())


def test_a_turn_of_one_brain_does_not_wait_for_another_brains_cleanup(hive, monkeypatch):
    """The brain_versions pool is shared: a turn's start waits only for a pending cleanup
    of its own Brain (a turn holding every Brain: of any), never for another Brain's."""
    import time
    app, ws, host = hive
    go_on = threading.Event()
    real = brain_versions.reconcile

    def blocking(workspace, brain, *a):
        if brain == "B":
            go_on.wait(5)
        return real(workspace, brain, *a)
    monkeypatch.setattr(brain_versions, "reconcile", blocking)

    async def go():
        brain_use._workspace = ws
        brain_use._schedule("B")                       # Brain B's cleanup holds the pool
        await asyncio.sleep(0.01)
        t0 = time.monotonic()
        await asyncio.wait_for(brain_use.hold("A", "t1", "s1"), 1)
        took = time.monotonic() - t0
        every = asyncio.ensure_future(brain_use.hold(brain_use.ANY, "t2", "s2"))
        await asyncio.sleep(0.1)
        waited = not every.done()
        go_on.set()
        await asyncio.wait_for(every, 2)
        brain_use.release("A", "t1")
        brain_use.release(brain_use.ANY, "t2")
        await _settle()
        return took, waited
    took, waited = asyncio.run(go())
    assert took < 0.1 and waited


def test_a_turn_does_not_start_on_a_version_that_cannot_be_unmarked(hive, monkeypatch):
    """A cleanup that keeps failing: the session's next turn is refused rather than run
    on the old version."""
    app, ws, host = hive
    live = ws / "Projects" / "Lib"

    def broken(*a):
        raise OSError(5, "I/O error")

    async def go():
        await _put(app, _files(host, "alpha"))
        await brain_use.hold("Lib", "t1", "s1")
        await _put(app, _files(host, "beta"))
        monkeypatch.setattr(brain_versions, "reconcile", broken)
        brain_use.release("Lib", "t1")
        await _settle()
        with pytest.raises(brain_use.BrainUseError, match="cannot be updated"):
            await brain_use.hold("Lib", "t2", "s1")
        assert brain_use.in_use("Lib") == 0                # the refused turn holds nothing
        await brain_use.hold("Lib", "t3", "s9")             # another session is not refused
        assert brain_versions.view(ws, "Lib", live, "s9") == live
        brain_use.release("Lib", "t3")
        await _settle()
    asyncio.run(go())


def test_a_cancelled_cleanup_does_not_leave_the_turn_held(hive, monkeypatch):
    app, ws, host = hive

    async def go():
        brain_use._workspace = ws
        brain_use._failed.add("Lib")
        loop = asyncio.get_running_loop()

        def cancelled(brain):
            f = loop.create_future()
            f.cancel()
            return f
        monkeypatch.setattr(brain_use, "_schedule", cancelled)
        await brain_use.hold("Lib", "t1", "s1")          # no marker: the turn starts
        held = brain_use.in_use("Lib")
        brain_use.release("Lib", "t1")
        return held, brain_use.in_use("Lib")
    assert asyncio.run(go()) == (1, 0)
