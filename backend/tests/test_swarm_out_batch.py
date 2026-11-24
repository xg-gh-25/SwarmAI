"""Several Swarm Out dispatches at once, and a spec error that says what to fix.

Questions:
1. does a batch of three dispatches on Elastic Workers run them at the same time (the
   wall time below the sum of three slow starts), and report a failing item on its own
   while the others go out?
2. within one batch, is each Brain packaged once and each repository bundled once, and
   are the shared bundle files removed when the batch ends?
3. does the Elastic start run on its own pool (not the event loop, not the default pool
   the chat path shares), and is it a step of the row's ``timings``?
4. does every item share the batch's one deadline, an item whose turn comes too late
   failing before anything is asked of a Hive?
5. does POST /dispatch/batch validate each item on its own (a task over the limit is
   that item's 400 with its field), and map each item's refusal to its own result?
6. does ``dispatch-many`` send one batch with this session, print every item's result
   and exit 1 when one failed; is it not taken for a literal ``dispatch`` call (its rows
   draw their cards from the dispatch log)?
7. does the CLI print the field and the limit of a spec whose task is too long?
"""
from __future__ import annotations

import asyncio
import io
import json
import threading
import time
import urllib.error

import pytest

from core import swarm_out as so
from tests.test_elastic_dispatch_e2e import world  # noqa: F401 — the elastic fixture
from tests.test_task_spec import ws  # noqa: F401 — the Brain fixture

SID = "11111111-2222-3333-4444-555555555555"


def _item(w, title, **kw):
    from core.project_agent import revision
    rev = revision(w.ws / "Projects" / "Lib")
    return {"hive_ref": None, "title": title, "brain": "Lib", "task": f"fix the signer ({title})",
            "acceptance": "tests pass", "execution": "native", "returns": "commits",
            "seen_revision": rev, **kw}


def _slow_starts(w, monkeypatch, seconds):
    """Every InvokeAgentRuntime(start) takes *seconds*; the threads it ran on are kept."""
    threads: list[str] = []
    fast = type(w.data).invoke_agent_runtime

    def slow(self, **kw):
        threads.append(threading.current_thread().name)
        time.sleep(seconds)
        return fast(self, **kw)
    monkeypatch.setattr(type(w.data), "invoke_agent_runtime", slow)
    return threads


# ── 1. three at once; one failure spares the rest ────────────────────────────

def test_three_items_run_at_once_and_one_failure_spares_the_rest(world, monkeypatch):
    _slow_starts(world, monkeypatch, 1.0)
    items = [_item(world, "one"), _item(world, "two", seen_revision="0" * 16), _item(world, "three")]
    started = time.monotonic()
    got = asyncio.run(so.dispatch_batch(items))
    wall = time.monotonic() - started
    assert len(got) == 3
    one, two, three = got
    assert isinstance(two, so.SwarmOutError) and "changed since spec" in str(two)
    assert one["title"] == "one" and three["title"] == "three"
    assert one["dispatch_id"] != three["dispatch_id"]
    assert len(world.data.invokes) == 2                   # the refused item opened nothing
    assert wall < 2 * 1.0 * 0.9, wall                     # below the sum of the two starts
    rows = [json.loads(x) for x in (world.state / "dispatches.jsonl").read_text().splitlines()]
    assert sorted(r["title"] for r in rows) == ["one", "three"]


# ── 2. one Brain package, one bundle per repository, removed at the end ──────

def test_a_batch_packages_each_brain_once_and_bundles_each_repo_once(world, monkeypatch):
    from core import brain_sync, repo_ship
    collects, packs, paths = [], [], []
    real_collect, real_pack = brain_sync.collect, repo_ship.pack

    def collect(root, general):
        collects.append(str(root))
        return real_collect(root, general)

    def pack(*a, **kw):
        packs.append(a[3])
        out = real_pack(*a, **kw)
        paths.append(out[0])
        return out
    monkeypatch.setattr(brain_sync, "collect", collect)
    monkeypatch.setattr(repo_ship, "pack", pack)
    got = asyncio.run(so.dispatch_batch([_item(world, f"t{i}") for i in range(3)]))
    assert all(isinstance(g, dict) for g in got), got
    assert len([c for c in collects if c.endswith("Lib")]) == 1, collects
    assert packs == [world.sha]
    assert [sha for _r, sha in world.bundles] == [world.sha] * 3      # each round got it
    assert paths and all(p is not None and not p.exists() for p in paths)


def test_a_single_dispatch_still_removes_its_own_bundle(world, monkeypatch):
    from core import repo_ship
    paths = []
    real_pack = repo_ship.pack

    def pack(*a, **kw):
        out = real_pack(*a, **kw)
        paths.append(out[0])
        return out
    monkeypatch.setattr(repo_ship, "pack", pack)
    kw = _item(world, "solo")
    asyncio.run(so.dispatch_spec(kw.pop("hive_ref"), kw.pop("title"), kw.pop("brain"), kw.pop("task"),
                                 kw.pop("acceptance"), **kw))
    assert paths and not paths[0].exists()


# ── 3. the start on its own pool, and timed ──────────────────────────────────

def test_the_elastic_start_runs_on_its_pool_and_is_timed(world, monkeypatch):
    threads = _slow_starts(world, monkeypatch, 0.3)
    [row] = asyncio.run(so.dispatch_batch([_item(world, "timed")]))
    assert threads and all(t.startswith("swarm-elastic_start") for t in threads), threads
    steps = {t["step"]: t["seconds"] for t in row["timings"]}
    [start] = [s for s in steps if s.startswith("opening a round on Elastic Workers")]
    assert steps[start] >= 0.3


def test_the_round_end_check_runs_on_its_own_pool(world, monkeypatch):
    from core.carriers import agentcore as ac
    seen = []
    real = ac._ended_in_s3

    def ended(*a):
        seen.append(threading.current_thread().name)
        return real(*a)
    monkeypatch.setattr(ac, "_ended_in_s3", ended)
    asyncio.run(so.dispatch_batch([_item(world, "s3")]))
    assert seen and all(t.startswith("swarm-elastic_s3") for t in seen), seen


# ── 4. one deadline for the batch ────────────────────────────────────────────

def test_an_item_whose_turn_comes_too_late_sends_nothing(monkeypatch):
    calls = []

    async def fake(hive_ref, title, *a, deadline_s=None, **kw):
        calls.append((title, deadline_s))
        await asyncio.sleep(0.6)
        return {"dispatch_id": "so_" + "0" * 12, "title": title}
    monkeypatch.setattr(so, "dispatch_spec", fake)
    monkeypatch.setattr(so, "BATCH_CONCURRENCY", 1)
    monkeypatch.setattr(so, "DISPATCH_DEADLINE_S", 1.0)
    monkeypatch.setattr(so, "SESSION_ID_WAIT_S", 0.5)
    got = asyncio.run(so.dispatch_batch([{"hive_ref": None, "title": "first"},
                                         {"hive_ref": None, "title": "late"}]))
    assert got[0]["title"] == "first"
    assert isinstance(got[1], so.SwarmOutError) and "nothing was sent" in str(got[1])
    assert [c[0] for c in calls] == ["first"]                 # the late one was never started
    assert calls[0][1] is not None and calls[0][1] <= 1.0     # its deadline is the batch's


# ── 5. the route: each item validated and answered on its own ────────────────

def _app():
    from fastapi import FastAPI

    from middleware.error_handler import setup_error_handlers
    from routers.swarm_out import router
    app = FastAPI()
    setup_error_handlers(app)
    app.include_router(router, prefix="/api/swarm-out")
    return app


def test_the_batch_route_reports_each_item_on_its_own(monkeypatch):
    from fastapi.testclient import TestClient
    sent = []

    async def fake(hive_ref, title, brain, task, *a, dispatched_from=None, **kw):
        sent.append((title, dispatched_from, kw.get("seen_revision")))
        if title == "refused":
            raise so.SwarmOutRefused("Lib cannot be sent to a Hive: private", private=True)
        return {"dispatch_id": "so_" + "a" * 12, "title": title}
    monkeypatch.setattr(so, "dispatch_spec", fake)
    monkeypatch.delenv("SWARMAI_MODE", raising=False)
    base = {"brain": "Lib", "acceptance": "ok", "execution": "native", "returns": "answer",
            "revision": "r1"}
    body = {"session": SID, "items": [
        {**base, "title": "too long", "task": "x" * (so.MAX_TASK_CHARS + 1)},
        {**base, "title": "refused", "task": "t"},
        {**base, "title": "fine", "task": "t"},
        {**base, "title": "no revision", "task": "t", "revision": None}]}
    r = TestClient(_app()).post("/api/swarm-out/dispatch/batch", json=body)
    assert r.status_code == 200, r.text
    res = r.json()["results"]
    assert [x["index"] for x in res] == [0, 1, 2, 3]
    assert res[0]["ok"] is False and res[0]["status"] == 400
    assert res[0]["fields"][0]["field"] == "task" and str(so.MAX_TASK_CHARS) in res[0]["fields"][0]["error"]
    assert res[1]["ok"] is False and res[1]["refused"] is True and res[1]["private"] is True
    assert res[2]["ok"] is True and res[2]["dispatch_id"] == "so_" + "a" * 12
    assert res[3]["ok"] is False and "revision" in res[3]["detail"]
    assert sent == [("refused", SID, "r1"), ("fine", SID, "r1")]       # each with the session


def test_the_batch_route_is_refused_on_a_hive(monkeypatch):
    from fastapi.testclient import TestClient
    monkeypatch.setenv("SWARMAI_MODE", "hive")
    r = TestClient(_app()).post("/api/swarm-out/dispatch/batch", json={"items": [{"title": "x"}]})
    assert r.status_code == 403


# ── 6. dispatch-many ─────────────────────────────────────────────────────────

class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_dispatch_many_sends_one_batch_and_exits_1_on_a_failed_item(tmp_path, monkeypatch, capsys):
    from scripts import swarm_out as cli
    (tmp_path / "long.md").write_text("the long task text")
    specs = [{"title": "a", "brief": "b", "brain": "Lib", "task": "do a", "acceptance": "ok",
              "execution": "native", "why": "small", "returns": "answer", "revision": "r1"},
             {"title": "b", "brain": "Lib", "task_file": str(tmp_path / "long.md"), "acceptance": "ok",
              "execution": "pipeline:full", "returns": "commits", "revision": "r2", "base": "main"},
             {"title": "c", "brain": "Lib", "task_file": str(tmp_path / "missing.md"), "acceptance": "ok",
              "execution": "native", "returns": "answer", "revision": "r3"}]
    f = tmp_path / "specs.json"
    f.write_text(json.dumps(specs))
    calls = []

    def urlopen(req, timeout=None):
        calls.append((req.full_url, json.loads(req.data), timeout))
        return _Resp(json.dumps({"results": [
            {"index": 0, "ok": True, "dispatch_id": "so_" + "1" * 12},
            {"index": 1, "ok": False, "status": 400, "detail": "Lib's Project Agent changed"}]}).encode())
    monkeypatch.setattr(cli.urllib.request, "urlopen", urlopen)
    monkeypatch.setenv("SWARM_APP_SESSION_ID", SID)
    rc = cli.main(["dispatch-many", "--file", str(f)])
    out = json.loads(capsys.readouterr().out)
    assert rc == 1
    [(url, body, timeout)] = calls
    assert url.endswith("/api/swarm-out/dispatch/batch") and timeout == cli.DISPATCH_TIMEOUT_S
    assert body["session"] == SID
    assert [i["title"] for i in body["items"]] == ["a", "b"]           # c could not be read
    assert body["items"][1]["task"] == "the long task text" and body["items"][1]["base"] == ["main"]
    assert body["items"][0]["revision"] == "r1"
    res = out["results"]
    assert [r["index"] for r in res] == [0, 1, 2] and [r["ok"] for r in res] == [True, False, False]
    assert res[2]["title"] == "c" and "missing.md" in res[2]["detail"]
    assert out["ok"] is False


def test_dispatch_many_is_not_taken_for_a_literal_dispatch_call():
    from core import swarm_out_card
    assert not swarm_out_card.watched('"$SWARM_BACKEND_BIN" py -m scripts.swarm_out dispatch-many --file s.json')
    assert swarm_out_card.watched('"$SWARM_BACKEND_BIN" py -m scripts.swarm_out dispatch --title t')


# ── 7. the field and the limit of a task that is too long ────────────────────

def test_the_cli_prints_the_field_and_limit_of_an_oversized_task(monkeypatch, capsys):
    from fastapi.testclient import TestClient

    from scripts import swarm_out as cli
    client = TestClient(_app())
    monkeypatch.delenv("SWARMAI_MODE", raising=False)

    def urlopen(req, timeout=None):
        path = req.full_url.split("/api/swarm-out", 1)[1]
        r = client.post("/api/swarm-out" + path, content=req.data, headers={"Content-Type": "application/json"})
        if r.status_code >= 400:
            raise urllib.error.HTTPError(req.full_url, r.status_code, "error", r.headers, io.BytesIO(r.content))
        return _Resp(r.content)
    monkeypatch.setattr(cli.urllib.request, "urlopen", urlopen)
    rc = cli.main(["spec", "--brain", "Lib", "--task", "x" * (so.MAX_TASK_CHARS + 1), "--acceptance", "ok",
                   "--execution", "native", "--returns", "answer"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 1 and out["status"] == 400
    assert out["fields"] == [{"field": "body.task", "error": f"String should have at most {so.MAX_TASK_CHARS} characters"}]
    assert "body.task" in out["detail"] and str(so.MAX_TASK_CHARS) in out["detail"]


def test_the_skill_says_dispatch_many_and_the_task_limit():
    from pathlib import Path
    text = (Path(so.__file__).resolve().parent.parent / "skills" / "s_swarm-out" / "SKILL.md").read_text()
    assert "dispatch-many" in text
    assert "20000" in text or "20,000" in text


def test_a_start_that_waited_for_a_thread_gets_only_what_is_left(world, monkeypatch):
    """Rounds of other dispatches hold every start thread: the start that waited behind
    them does not run its whole window on top of the dispatch's deadline; with too little
    left it is refused before anything is asked of the platform (another candidate may
    take it)."""
    from concurrent.futures import ThreadPoolExecutor

    from core import executors
    from core.carriers import agentcore as ac
    from core.carriers.base import LocationUnavailable
    pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="swarm-elastic_start")
    monkeypatch.setitem(executors._pools, "elastic_start", pool)
    monkeypatch.setattr(ac, "MIN_START_S", 0.3)
    busy = pool.submit(time.sleep, 0.8)                    # another round's start
    deadline = so._Deadline(1.0)
    with pytest.raises(LocationUnavailable, match="too little time left"):
        asyncio.run(ac.AgentCoreLocation("ac1").open_run("so_" + "c" * 12, 1, deadline))
    busy.result()
    pool.shutdown()
    assert world.data.invokes == []                          # never asked to start
    [step] = [t for t in deadline.timings if t["step"].startswith("opening a round")]
    assert step["seconds"] >= 0.5
