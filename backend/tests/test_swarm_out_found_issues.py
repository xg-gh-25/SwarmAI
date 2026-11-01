"""Gaps found driving three Hive runs end to end (a code change through the pipeline, two
research tasks), each fixed where it starts.

Questions: (1) is a correction of a pipeline task whose run ended given a run of its own
(the Host names it, the Hive creates it on the same worktrees and records it on the
session), does a session whose run is still open keep it, and does the Host count that
run as the task's own; does an older Hive still get a plain correction; (2) does a
session whose turn ended while its sub-agents still run read as running, not finished;
(3) does the landed commit carry the reviewer's summary and the files, and never the
Hive's own commit messages; (4) does ``update-hive`` default to the version this Host
runs.
"""
from __future__ import annotations

import asyncio
import json

import pytest

from core import project_agent_session as pas, swarm_out, swarm_out_results
from tests.test_project_agent_session import PIN, _router, _run, _ws

DID = "so_0123456789ab"
SID = "sess-fi1"
FIRST = swarm_out.pipeline_run_id(DID)


# ── (1) a correction of a pipeline task gets a run of its own ─────────────────

def test_a_corrections_run_id_is_one_of_the_tasks_own_and_never_the_first():
    ids = [swarm_out.pipeline_run_id(DID, k) for k in range(4)]
    assert ids[0] == "run_01234567" and len(set(ids)) == 4
    assert all(pas._RUN_ID_RE.fullmatch(i) for i in ids)
    assert set(ids) <= swarm_out.pipeline_run_ids(DID)


def _row(execution="pipeline"):
    return {"dispatch_id": DID, "hive_id": "h1", "session_id": SID,
            "project_agent": {"brain": "Lib", "mode": "code-change", "revision": "r", "profile": "bugfix",
                              "execution": execution, "run_id": FIRST, "returns": "commits"}}


def test_the_host_counts_its_corrections_itself_not_from_the_hives_partial_list(tmp_path, monkeypatch):
    monkeypatch.setattr(swarm_out, "_state_dir", lambda: tmp_path)
    shown = {"result": {"pipeline_runs": [{"run_id": FIRST}]}}
    assert [swarm_out._next_rerun(DID, shown) for _ in range(3)] == [1, 2, 3]
    # the result lists only some runs (the first ten, the turns read): the count holds
    assert swarm_out._next_rerun(DID, {}) == 4
    # a run the result shows past the count (a record from before it) is skipped too
    later = {"result": {"pipeline_runs": [{"run_id": swarm_out.pipeline_run_id(DID, 9)}]}}
    assert swarm_out._next_rerun(DID, later) == 10
    pin = swarm_out._rerun_pin(_row(), 10, "fix the timing")
    assert pin["execution"] == {"how": "pipeline", "profile": "bugfix",
                                "run_id": swarm_out.pipeline_run_id(DID, 10), "requirement": "fix the timing"}
    from core import swarm_out_settle
    swarm_out_settle._update(DID, lambda rec: rec.update(pipeline_reruns=swarm_out.MAX_PIPELINE_RERUNS))
    with pytest.raises(swarm_out.SwarmOutError, match="corrections"):
        swarm_out._next_rerun(DID, {})
    assert not swarm_out._pipeline_task(_row("native"))


@pytest.fixture
def rerun_world(tmp_path, monkeypatch):
    monkeypatch.setattr(swarm_out, "_state_dir", lambda: tmp_path)
    row = _row()
    monkeypatch.setattr(swarm_out, "_log_row_of", lambda d: row)

    async def inst(ref):
        return {"id": ref, "name": "h1"}
    monkeypatch.setattr(swarm_out, "_instance", inst)

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False
    monkeypatch.setattr(swarm_out, "_client", lambda i, t=None: Client())
    monkeypatch.setattr(swarm_out_results, "load", lambda d: {"result": {"pipeline_runs": [{"run_id": FIRST}]}})
    monkeypatch.setattr(swarm_out_results, "turn_started", lambda d: None)
    sent = []

    async def start(client, message, work_id=None, project_agent=None, session_id=None):
        sent.append((message, project_agent, session_id))
        return session_id
    monkeypatch.setattr(swarm_out, "_start_session", start)
    return sent


def test_a_pipeline_correction_sends_its_run_and_says_how(rerun_world, monkeypatch):
    async def cap(inst, name):
        return name == "pipeline_rerun"
    monkeypatch.setattr(swarm_out, "_has_cap", cap)
    out = asyncio.run(swarm_out.rerun(DID, "make get fast"))
    new = swarm_out.pipeline_run_id(DID, 1)
    assert out == {"sent": True, "run_id": new}
    message, pin, sid = rerun_world[-1]
    assert sid == SID and pin["execution"]["run_id"] == new and pin["brain"] == "Lib"
    assert message.startswith("How:") and new in message and message.endswith("make get fast")


def test_a_hive_that_does_not_answer_the_probe_gets_nothing(rerun_world, monkeypatch):
    async def cap(inst, name):
        return None
    monkeypatch.setattr(swarm_out, "_has_cap", cap)
    with pytest.raises(swarm_out.SwarmOutError, match="did not answer"):
        asyncio.run(swarm_out.rerun(DID, "make get fast"))
    assert rerun_world == []


def test_a_refused_correction_run_is_reported_as_such(monkeypatch):
    import httpx

    def hive(request):
        return httpx.Response(409, json={"detail": "busy"})
    client = httpx.AsyncClient(base_url="http://h", transport=httpx.MockTransport(hive))
    with pytest.raises(swarm_out.SwarmOutError, match="did not start a pipeline run for the correction .HTTP 409"):
        asyncio.run(swarm_out._start_session(client, "x", project_agent={"execution": {}}, session_id=SID))


def test_an_older_hive_gets_the_plain_correction_and_the_host_says_so(rerun_world, monkeypatch):
    async def cap(inst, name):
        return False
    monkeypatch.setattr(swarm_out, "_has_cap", cap)
    out = asyncio.run(swarm_out.rerun(DID, "make get fast"))
    assert rerun_world[-1] == ("make get fast", None, SID)
    assert out["sent"] is True and "run_id" not in out and "pipeline" in out


def test_the_tasks_correction_run_is_its_own_and_another_run_is_not():
    agent = _row()["project_agent"]
    res = {"ended": "completed", "pipeline_runs": [{"run_id": FIRST, "profile": "bugfix"},
                                                   {"run_id": swarm_out.pipeline_run_id(DID, 1), "profile": "bugfix"}]}
    assert swarm_out_results.execution_check(res, agent, DID)["state"] == "matches"
    only_new = {"ended": "completed", "pipeline_runs": [{"run_id": swarm_out.pipeline_run_id(DID, 1)}]}
    assert swarm_out_results.execution_check(only_new, agent, DID)["state"] == "matches"
    other = {"ended": "completed", "pipeline_runs": [{"run_id": "run_deadbeef"}]}
    assert swarm_out_results.execution_check(other, agent, DID) == {"state": "differs", "why": "other_run"}


def _pipeline_pin(run_id, repos=None):
    return {**PIN, "execution": {"how": "pipeline", "profile": "bugfix", "run_id": run_id,
                                 "requirement": None, "repos": repos or {}}}


async def _pinned_session(monkeypatch, sid, pin):
    """A session whose first message was pinned to *pin* (its run's create faked)."""
    monkeypatch.setattr(pas, "create_pinned_run", lambda s, p: p["execution"]["run_id"])

    def send(**kw):
        async def gen():
            yield {"type": "result", "sessionId": sid}
        return gen()
    await _run(_router(), sid, pin, send)


@pytest.mark.asyncio
async def test_the_hive_makes_the_corrections_run_on_the_same_worktrees(tmp_path, monkeypatch):
    from database import db
    import core.run_protocol.engine as engine
    await _pinned_session(monkeypatch, "s-adv", _pipeline_pin("run_aaaaaaaa", {"lib": "/w/lib"}))
    made = []
    monkeypatch.setattr(pas, "create_pinned_run", lambda s, p: made.append((s, p)) or p["execution"]["run_id"])
    monkeypatch.setattr(engine, "logged_terminal", lambda d, r: r == "run_aaaaaaaa")
    ex = {"how": "pipeline", "profile": "bugfix", "run_id": "run_bbbbbbbb", "requirement": "make it fast"}
    new = await pas.advance_pipeline_pin("s-adv", ex)
    assert new["execution"] == {**ex, "repos": {"lib": "/w/lib"}}
    assert {k: new[k] for k in ("brain", "mode", "revision", "gates_digest")} == \
        {k: PIN[k] for k in ("brain", "mode", "revision", "gates_digest")}
    assert made == [("s-adv", new)]
    assert (await db.sessions.get("s-adv"))["metadata"]["project_agent"] == new
    # the new run is open now: a further correction continues in it
    assert await pas.advance_pipeline_pin("s-adv", {**ex, "run_id": "run_cccccccc"}) is None
    assert len(made) == 1


def test_the_result_follows_the_corrections_run():
    later = swarm_out.pipeline_run_id(DID, 1)

    def block(rid, status):
        return {"type": "pipeline_summary", "runId": rid, "profile": "bugfix", "status": status}
    msgs = [{"role": "user", "content": [{"type": "text", "text": "do it"}]},
            {"role": "assistant", "content": [block(FIRST, "completed"), block("run_deadbeef", "abandoned")]},
            {"role": "user", "content": [{"type": "text", "text": "fix"}]},
            {"role": "assistant", "content": [block(later, "completed")]}]
    agent = _row()["project_agent"]
    assert swarm_out_results.summarize(msgs, False, agent, DID)["pipeline"]["run_id"] == later
    assert swarm_out_results.summarize(msgs[:2], False, agent, DID)["pipeline"]["run_id"] == FIRST


@pytest.mark.asyncio
async def test_a_correction_run_is_refused_without_a_pipeline_session_or_a_new_id(monkeypatch):
    from database import db
    import core.run_protocol.engine as engine
    monkeypatch.setattr(engine, "logged_terminal", lambda d, r: True)
    await _pinned_session(monkeypatch, "s-native", {**PIN, "execution": {"how": "native"}})
    ex = {"how": "pipeline", "profile": "bugfix", "run_id": "run_bbbbbbbb", "requirement": None}
    with pytest.raises(pas.PinError, match="not a pipeline session"):
        await pas.advance_pipeline_pin("s-native", ex)
    await _pinned_session(monkeypatch, "s-same", _pipeline_pin("run_aaaaaaaa"))
    with pytest.raises(pas.PinError, match="no new pipeline run"):
        await pas.advance_pipeline_pin("s-same", {**ex, "run_id": "run_aaaaaaaa"})
    with pytest.raises(pas.PinError, match="no new pipeline run"):
        await pas.advance_pipeline_pin("s-same", {"how": "native"})        # never to native
    with pytest.raises(pas.PinError, match="own profile"):
        await pas.advance_pipeline_pin("s-same", {**ex, "profile": "direct"})
    stored = (await db.sessions.get("s-same"))["metadata"]["project_agent"]
    assert stored["execution"]["run_id"] == "run_aaaaaaaa"

    def fail(s, p):
        raise RuntimeError("no")
    monkeypatch.setattr(pas, "create_pinned_run", fail)
    with pytest.raises(pas.PinError, match="could not be created"):
        await pas.advance_pipeline_pin("s-same", ex)
    stored = (await db.sessions.get("s-same"))["metadata"]["project_agent"]
    assert stored["execution"]["run_id"] == "run_aaaaaaaa"          # the old pin is put back
    # a record that cannot be written: no run is made
    made = []
    monkeypatch.setattr(pas, "create_pinned_run", lambda s, p: made.append(p))

    async def no_record(sid, pin):
        return False
    monkeypatch.setattr(pas, "record_pin", no_record)
    with pytest.raises(pas.PinError, match="nothing was created"):
        await pas.advance_pipeline_pin("s-same", ex)
    assert made == []


@pytest.mark.asyncio
async def test_the_hives_chat_advances_the_pin_and_the_live_hooks_read_it(async_client, monkeypatch):
    from routers import chat as chat_router
    old = _pipeline_pin("run_aaaaaaaa")
    new = _pipeline_pin("run_bbbbbbbb")
    ctx = {"sdk_session_id": "s-live"}
    pas.set_pin(ctx, old)

    class _Unit:
        has_background_work = False
        _hook_session_context = ctx
        stop_event = None

    class _Router:
        busy = False

        def get_unit(self, sid):
            return _Unit()

        def is_busy(self, sid):
            return self.busy

        async def run_conversation(self, **kw):
            yield {"type": "result", "sessionId": "s-live"}
    router = _Router()
    monkeypatch.setattr(chat_router, "_get_router", lambda: router)
    got = []

    async def advance(sid, execution):
        got.append((sid, execution))
        return new
    monkeypatch.setattr(pas, "advance_pipeline_pin", advance)

    async def no_hive(sid):
        return False
    monkeypatch.setattr("core.swarm_out_remote.is_dispatched_hive_session", no_hive)
    body = {"agent_id": "default", "message": "fix", "session_id": "s-live",
            "project_agent": {"execution": new["execution"]}}
    r = await async_client.post("/api/chat/stream", json=body)
    assert r.status_code == 200
    assert got == [("s-live", new["execution"])] and ctx[pas.PIN_KEY] == new
    router.busy = True
    r = await async_client.post("/api/chat/stream", json=body)
    assert r.status_code == 409 and len(got) == 1
    router.busy = False
    _Unit.has_background_work = True                       # its sub-agents still run
    r = await async_client.post("/api/chat/stream", json=body)
    assert r.status_code == 409 and len(got) == 1


def test_a_current_hive_says_it_gives_a_correction_its_own_run():
    import inspect
    from routers import chat as chat_router
    assert '"pipeline_rerun": True' in inspect.getsource(chat_router)


# ── (2) background work still running is not a finished run ───────────────────

def test_an_idle_session_with_background_work_is_running():
    row = {"session_id": SID}
    e = {"streaming": False, "waiting_input": False, "activity": "idle"}
    assert swarm_out.run_state(row, "running", {SID: {**e, "background_work": False}}, False) == ("stopped", "idle")
    assert swarm_out.run_state(row, "running", {SID: {**e, "background_work": True}}, False) == \
        ("running", "background")
    assert swarm_out.run_state(row, "running", {SID: e}, False) == ("stopped", "idle")   # an older Hive


def test_the_host_keeps_only_a_true_background_flag(monkeypatch):
    class Resp:
        status_code = 200

        def json(self):
            return {"sessions": {"a": {"background_work": True}, "b": {"background_work": "yes"}, "c": {}}}

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url):
            return Resp()
    monkeypatch.setattr(swarm_out, "_client", lambda i, t=None: Client())
    out = asyncio.run(swarm_out._session_states({"id": "h"}))
    assert [out[s]["background_work"] for s in "abc"] == [True, False, False]


@pytest.mark.asyncio
async def test_the_hive_reports_background_work_in_its_session_state(monkeypatch):
    from core.session_state import session_state_entry
    from core.session_unit import SessionUnit
    unit = _router().get_or_create_unit("s-bg", "default")
    for flag in (True, False):
        monkeypatch.setattr(SessionUnit, "has_background_work", property(lambda self, f=flag: f))
        assert (await session_state_entry(unit))["background_work"] is flag


def test_watch_reports_a_run_waiting_on_background_work_once():
    from core import swarm_out_follow
    row = {"state": "running", "state_reason": "background", "stage": "running"}
    assert swarm_out_follow.fingerprint(row, {})["reason"] == "background"


# ── (3) the landed commit's message ───────────────────────────────────────────

def test_the_landing_message_has_the_summary_and_files_and_no_hive_text():
    from core import hive_return
    files = [{"path": "backend/core/cognition.py", "added": 271, "deleted": 16},
             {"path": "logo.png", "added": None, "deleted": None}]
    msg = hive_return.landing_message("fix get", "h1", DID, "refs/hive/x/k", files, [],
                                      "get reads a Library id from its recorded source.")
    assert msg.startswith("hive: fix get\n\nget reads a Library id from its recorded source.\n\nFiles:\n")
    assert "  backend/core/cognition.py (+271 -16)" in msg and "  logo.png (binary)" in msg
    assert "refs/hive/x/k" in msg
    assert "hive: fix get\n\nFiles:" in hive_return.landing_message("fix get", "h1", DID, "r", files, [], None)


def test_a_summary_too_long_or_with_control_characters_is_refused_not_cut():
    from core import hive_return
    assert hive_return._summary("  a  \n b ") == "a\n b"
    with pytest.raises(hive_return.ReturnError, match="at most"):
        hive_return._summary("x" * (hive_return.SUMMARY_MAX + 1))
    for bad in ("a\x1b[2Jb", "a\u202eb", "a\u200bb", "a\x7fb"):
        with pytest.raises(hive_return.ReturnError, match="control or invisible"):
            hive_return._summary(bad)
    assert hive_return._summary("a\tb\nc") == "a\tb\nc"


def test_the_agents_landing_needs_a_summary_and_sends_it(tmp_path, monkeypatch):
    from scripts import swarm_out as cli
    calls = []
    monkeypatch.setattr(cli, "_call", lambda method, path, body=None, **k: calls.append(body) or 0)
    monkeypatch.setenv("SWARM_APP_SESSION_ID", "s-abc123")
    with pytest.raises(SystemExit):
        cli.main(["return-land", DID, "--why", "w", "--key", "k", "--tip", "t"])
    f = tmp_path / "summary.md"
    f.write_text("what changed and why\n", encoding="utf-8")
    assert cli.main(["return-land", DID, "--why", "w", "--key", "k", "--tip", "t", "--summary-file", str(f)]) == 0
    assert calls[-1]["summary"] == "what changed and why\n"
    assert cli.main(["return-land", DID, "--why", "w", "--key", "k", "--tip", "t",
                     "--summary-file", str(tmp_path / "missing.md")]) == 1
    assert len(calls) == 1


# ── (4) update-hive defaults to this Host's version ───────────────────────────

def test_update_hive_asks_for_this_hosts_version_unless_told(monkeypatch, capsys):
    from scripts import swarm_out as cli
    monkeypatch.setattr(cli, "UPDATE_EVERY_S", 0)
    sent = []
    monkeypatch.setattr(cli, "_call", lambda method, path, body=None, **k: sent.append(body) or 0)
    monkeypatch.setattr(cli, "_fleet_row", lambda name: {"name": "h1", "open_runs": 0, "capable": True})
    monkeypatch.setattr(cli, "_hive_id", lambda name: "i1")
    monkeypatch.setattr(cli, "_status_of", lambda name: "running")
    monkeypatch.setattr(cli, "_host_version", lambda: "2.4.0")
    assert cli.main(["update-hive", "--hive", "h1"]) == 0
    assert cli.main(["update-hive", "--hive", "h1", "--version", "2.3.0"]) == 0
    assert sent == [{"version": "2.4.0"}, {"version": "2.3.0"}]
    monkeypatch.setattr(cli, "_host_version", lambda: None)
    assert cli.main(["update-hive", "--hive", "h1"]) == 1
    assert "pass --version" in json.loads(capsys.readouterr().out.strip().splitlines()[-1])["detail"]
    assert len(sent) == 2
