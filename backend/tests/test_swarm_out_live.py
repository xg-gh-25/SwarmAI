"""The live stage of a running Hive pipeline task (core.swarm_out_live).

Questions: does it read the newest stage of the dispatch's run (the next stage after a
completed one), the run's planned stages, and nothing from another run, an unknown
stage or a malformed body; is it read only for a running pipeline task on a running
Hive, at most once per interval; does a failing Hive keep nothing and never raise; does
attach give it to running rows only?
"""
from __future__ import annotations

import asyncio
import json

import pytest

from core import swarm_out, swarm_out_live as live

RUN = "run_abc12345"


def _msgs(*blocks):
    return [{"role": "user", "content": [{"type": "text", "text": "go"}]},
            {"role": "assistant", "content": list(blocks)}]


def _stage(stage, status="in_progress", run=RUN, nxt=None, at="2026-11-18T10:00:00Z"):
    return {"type": "pipeline_stage", "runId": run, "stage": stage, "status": status, "next": nxt, "at": at}


def _run_block(stages, run=RUN):
    return {"type": "pipeline_run", "runId": run, "profile": "trivial", "stages": stages}


@pytest.fixture(autouse=True)
def _clean():
    live._live.clear(); live._checked.clear(); live._inflight.clear()
    yield
    live._live.clear(); live._checked.clear(); live._inflight.clear(); live._sems.clear()
    # asyncio.run closed its loop and left none set: later tests get a fresh one
    asyncio.set_event_loop(asyncio.new_event_loop())


def test_live_stage_of_reads_the_newest_stage():
    body = _msgs(_run_block(["evaluate", "plan", "build", "review", "test", "deliver", "reflect"]),
                 _stage("evaluate", "completed", nxt="plan"), _stage("plan"),
                 _stage("plan", "completed", nxt="build"))
    got = live.live_stage_of(body, {RUN})
    assert got == {"run_id": RUN, "stage": "build", "at": "2026-11-18T10:00:00Z",
                   "stages": ["evaluate", "plan", "build", "review", "test", "deliver", "reflect"]}
    assert live.live_stage_of(_msgs(_stage("build")), {RUN})["stages"] == []
    assert live.live_stage_of(_msgs(_stage("build", run="run_other")), {RUN}) is None      # another run
    assert live.live_stage_of(_msgs(_stage("rm -rf /")), {RUN}) is None                    # unknown stage
    assert live.live_stage_of(_msgs(_stage("build"), _stage("reflect", "completed")), {RUN}) is None  # ended
    assert live.live_stage_of({"not": "a list"}, {RUN}) is None
    assert live.live_stage_of(_msgs({"type": "pipeline_stage", "runId": RUN * 10, "stage": "build",
                                     "status": "in_progress"}), {RUN}) is None              # too long an id


def _row(**o):
    row = {"dispatch_id": "so_aaaaaaaaaaaa", "hive_id": "h1", "session_id": "sess1", "state": "running",
           "project_agent": {"execution": "pipeline", "run_id": RUN}}
    row.update(o)
    return row


def test_due_only_for_a_running_pipeline_task_on_a_running_hive():
    assert live.due(_row(), "running", now=1000.0)
    assert not live.due(_row(), "stopped", now=1000.0)
    assert not live.due(_row(state="stopped"), "running", now=1000.0)
    assert not live.due(_row(project_agent={"execution": "native"}), "running", now=1000.0)
    live._checked["so_aaaaaaaaaaaa"] = 1000.0
    assert not live.due(_row(), "running", now=1000.0 + live.LIVE_STAGE_S - 1)
    assert live.due(_row(), "running", now=1000.0 + live.LIVE_STAGE_S)


class _Resp:
    def __init__(self, status, body):
        self.status_code = status
        self._bytes = json.dumps(body).encode()
        self.headers = {}

    async def aiter_bytes(self):
        for i in range(0, len(self._bytes), 65536):
            yield self._bytes[i:i + 65536]

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class _Client:
    def __init__(self, resp):
        self.resp, self.calls = resp, []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    def stream(self, method, url, params=None):
        self.calls.append((method, url, params))
        if isinstance(self.resp, Exception):
            raise self.resp
        return self.resp


def _hive(monkeypatch, resp):
    client = _Client(resp)

    async def inst(_ref):
        return {"id": "h1"}
    monkeypatch.setattr(swarm_out, "_instance", inst)
    monkeypatch.setattr(swarm_out, "_client", lambda _inst, *a: client)
    monkeypatch.setattr(swarm_out, "pipeline_run_ids", lambda _d: {RUN})
    return client


def test_check_keeps_the_stage_and_reads_one_turn(monkeypatch):
    client = _hive(monkeypatch, _Resp(200, _msgs(_stage("review"))))
    got = asyncio.run(live.check(_row()))
    assert got["stage"] == "review"
    assert client.calls == [("GET", "/api/chat/sessions/sess1/messages", {"turns": 1})]
    rows = [_row(), _row(dispatch_id="so_bbbbbbbbbbbb", state="stopped")]
    live._live["so_bbbbbbbbbbbb"] = {"run_id": RUN, "stage": "test", "stages": [], "at": None}   # read while it ran
    live._checked.update({"so_aaaaaaaaaaaa": 1e12, "so_bbbbbbbbbbbb": 1e12})   # nothing due
    live.attach(rows, {"h1": "running"})
    assert rows[0]["live_stage"]["stage"] == "review"
    assert rows[1]["live_stage"] is None                                      # stopped: no live stage
    assert "so_bbbbbbbbbbbb" not in live._live


@pytest.mark.parametrize("resp", [_Resp(500, None), _Resp(503, _msgs(_stage("build"))), _Resp(200, {"bad": 1}),
                                  _Resp(200, _msgs(_stage("build"), {"type": "text", "text": "x" * (5 * 1024 * 1024)})),
                                  TimeoutError("slow")])
def test_a_failing_hive_keeps_nothing(monkeypatch, resp):
    _hive(monkeypatch, resp)
    assert asyncio.run(live.check(_row())) is None                            # never raises
    assert "so_aaaaaaaaaaaa" not in live._live
    assert "so_aaaaaaaaaaaa" in live._checked                                 # and is not retried at once


@pytest.mark.parametrize("sid", ["a/b", "x?y=1", "", None])
def test_an_unsafe_session_id_never_reaches_the_hive(monkeypatch, sid):
    client = _hive(monkeypatch, _Resp(200, _msgs(_stage("build"))))
    assert asyncio.run(live.check(_row(session_id=sid))) is None
    assert client.calls == []
