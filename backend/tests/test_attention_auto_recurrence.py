"""An automatic run is started once per problem, not once per occurrence
(core.attention_auto, core.attention_authority; release-readiness H1 and M1).

- A Cognitive Distillation item that keeps failing for the same cause writes a new
  event row each time. Its Needs You item is the same item every time, and its run
  record outlives the moment the item leaves the queue (the run's own requeue
  resolves the event), so the cause coming back is not a second run.
- A run record is released only when a read shows the cause fixed (the job ran
  well, the unit got through, CDF moves), never because one read missed the item.
"""
from __future__ import annotations

import asyncio
import json
import os
import sqlite3
from datetime import datetime, timezone

import pytest

from core import attention_auto as aa
from core import attention_authority as authority
from core.attention_authority import AttentionResult
from core.ingestion import api
from core.ingestion.api import SCHEMA_DDL


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@pytest.fixture
def auto(tmp_path, monkeypatch):
    """Records in a temp dir, the real job and CDF collectors on temp sources, and a
    fake session that counts what it was started for."""
    records = tmp_path / "attention_auto.json"
    monkeypatch.setattr(aa, "_records_path", lambda: records)
    monkeypatch.setattr(aa, "_is_worker", lambda: False)
    monkeypatch.setattr(aa, "_current", None)
    monkeypatch.setattr(aa, "_current_key", None)
    monkeypatch.setattr(aa, "_unsaved", {})

    db = tmp_path / "ingestion.db"
    conn = sqlite3.connect(db)
    conn.executescript(SCHEMA_DDL)
    # a live owner and nothing stuck: no "stalled" item in these tests
    conn.execute("INSERT INTO ingestion_owner(id,epoch,pid,started_at) VALUES(1,1,?,?)",
                 (os.getpid(), _now()))
    conn.commit()
    conn.close()

    def ro() -> sqlite3.Connection:
        value = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        value.execute("PRAGMA query_only=ON")
        return value
    monkeypatch.setattr(api, "connect_ro", ro)

    from jobs import scheduler
    state_file = tmp_path / "state.json"
    monkeypatch.setattr(scheduler, "STATE_FILE", state_file)
    monkeypatch.setattr(scheduler, "load_jobs", lambda: [])
    monkeypatch.setattr(authority, "_background_failures", lambda: [], raising=False)

    def collect(_root):
        items = authority._collect_jobs() + authority._collect_ingestion()
        for it in items:
            it.handler = authority.handler_of(it)
        aa.apply_records(items)
        return AttentionResult(items=items)
    monkeypatch.setattr(aa, "_collect", collect)

    env = {"db": db, "state": state_file, "records": records, "started": [],
           "outcome": (aa.FAILED, "the cause is still there"), "collect": collect}

    async def fake_session(item, on_session=None):
        env["started"].append(item.id)
        ctx = item.dispatch["context"]
        if ctx["kind"] == "poison":
            # what the item tells the run to do: `ingest requeue --reason <its reason>`,
            # which puts those units back and resolves their events: the item leaves
            with sqlite3.connect(db) as rw:
                ids = [r[0] for r in rw.execute(
                    "SELECT unit_id FROM ingestion_units WHERE state='failed_poison' "
                    "AND reason=?", (ctx.get("reason"),))]
                for unit_id in ids:
                    rw.execute("UPDATE ingestion_units SET state='queued' WHERE unit_id=?",
                               (unit_id,))
                    rw.execute("UPDATE ingestion_events SET resolved=1 WHERE kind='poison' "
                               "AND json_extract(detail,'$.unit_id')=?", (unit_id,))
        return env["outcome"]
    monkeypatch.setattr(aa, "run_session", fake_session)
    return env


def _poisoned(env, unit_id: str = "cand-1.u1", reason: str = "judge_rejected_shape") -> None:
    """The worker gives up on the unit again: failed_poison and a NEW event row."""
    with sqlite3.connect(env["db"]) as rw:
        rw.execute("INSERT OR IGNORE INTO ingestion_units(unit_id,candidate_id,claim_group,"
                   "state,updated_at) VALUES(?,?,?,?,?)",
                   (unit_id, "cand-1", "{}", "queued", _now()))
        rw.execute("UPDATE ingestion_units SET state='failed_poison',reason=? WHERE unit_id=?",
                   (reason, unit_id))
        rw.execute("INSERT INTO ingestion_events(at,kind,detail,resolved) VALUES(?,?,?,0)",
                   (_now(), "poison", json.dumps({"unit_id": unit_id, "reason": reason})))


LANE = "ingestion:poison:judge_rejected_shape"


def _unit_state(env, unit_id: str, state: str) -> None:
    with sqlite3.connect(env["db"]) as rw:
        rw.execute("UPDATE ingestion_units SET state=? WHERE unit_id=?", (state, unit_id))


def _job_state(env, *, fails: int, status: str = "failed") -> None:
    env["state"].write_text(json.dumps({"jobs": {"digest": {
        "last_run": _now(), "last_status": status, "consecutive_failures": fails,
        "last_error": "boom" if fails else None}}}))


async def _pass_and_settle(root):
    started = await aa.run_pass(root)
    if aa._current is not None:
        try:
            await aa._current
        except BaseException:
            pass
    return started


def _records(env) -> dict:
    return json.loads(env["records"].read_text()) if env["records"].exists() else {}


def _shown(env, kind: str):
    return next(it for it in env["collect"](None).items
                if (it.dispatch.get("context") or {}).get("kind") == kind)


@pytest.mark.parametrize("outcome", [(aa.FAILED, "the judge still rejects the shape"),
                                     (aa.DONE, "requeued")])
def test_five_events_of_one_cause_start_one_run(auto, tmp_path, outcome):
    """H1: each recurrence is a new event row; it must not be a new session."""
    auto["outcome"] = outcome

    async def scenario():
        for _ in range(5):
            _poisoned(auto)                       # the cause is still there
            await _pass_and_settle(tmp_path)      # a run may start; it requeues
            await _pass_and_settle(tmp_path)      # the item is out of the queue
    asyncio.run(scenario())

    assert len(auto["started"]) == 1
    # one item for the problem, whatever event row is the newest
    _poisoned(auto)
    asyncio.run(_pass_and_settle(tmp_path))
    assert auto["started"] == [LANE]
    shown = _shown(auto, "poison")
    assert shown.id == LANE
    assert shown.handler == authority.HANDLER_PERSON
    assert shown.auto["state"] == aa.FAILED
    assert shown.detail.startswith("Swarm tried this itself")


def test_a_unit_that_got_through_releases_the_record(auto, tmp_path):
    """Once the unit is processed the problem is gone: a later failure is a new one
    and Swarm handles it again, once (not one more item for the person)."""
    async def scenario():
        _poisoned(auto)
        await _pass_and_settle(tmp_path)
        _unit_state(auto, "cand-1.u1", "written")    # the fix worked
        await _pass_and_settle(tmp_path)
        assert _records(auto) == {}
        _poisoned(auto, unit_id="cand-2.u1", reason="error:KeyError")
        await _pass_and_settle(tmp_path)
    asyncio.run(scenario())
    assert auto["started"] == [LANE, "ingestion:poison:error:KeyError"]


def test_a_unit_that_expired_releases_the_record(auto, tmp_path):
    """A unit CDF gave up waiting for is never poisoned again: nothing is left to fix."""
    async def scenario():
        _poisoned(auto)
        await _pass_and_settle(tmp_path)
        _unit_state(auto, "cand-1.u1", "expired_unprocessed")
        await _pass_and_settle(tmp_path)
    asyncio.run(scenario())
    assert _records(auto) == {}


def test_two_causes_get_one_run_each_however_they_alternate(auto, tmp_path):
    """Each poison reason is its own item: a second cause is not reported as the first
    one coming back, and the two taking turns as newest start no more runs."""
    async def scenario():
        for _ in range(3):
            _poisoned(auto, unit_id="cand-1.u1", reason="reason_x")
            _poisoned(auto, unit_id="cand-1.u2", reason="reason_y")
            for _ in range(3):
                await _pass_and_settle(tmp_path)
    asyncio.run(scenario())
    assert sorted(auto["started"]) == ["ingestion:poison:reason_x", "ingestion:poison:reason_y"]


def _stalled(env, on: bool) -> None:
    """No live CDF owner is a stall; a live one (and nothing old) is none."""
    with sqlite3.connect(env["db"]) as rw:
        rw.execute("DELETE FROM ingestion_owner")
        if not on:
            rw.execute("INSERT INTO ingestion_owner(id,epoch,pid,started_at) VALUES(1,1,?,?)",
                       (os.getpid(), _now()))


def test_a_stall_that_flaps_with_its_cause_starts_one_run(auto, tmp_path):
    """A stall that stops showing for a pass without CDF moving anything through is
    the same stall: one run; once work gets through, a later stall is new, once."""
    async def scenario():
        for _ in range(4):
            _stalled(auto, True)
            await _pass_and_settle(tmp_path)
            _stalled(auto, False)                # the owner is back for a moment
            await _pass_and_settle(tmp_path)
        assert auto["started"] == ["ingestion:stalled"]
        assert _records(auto)["ingestion:stalled"]["state"] == aa.FAILED
        _stalled(auto, True)
        await _pass_and_settle(tmp_path)
        assert _shown(auto, "stalled").handler == authority.HANDLER_PERSON
        _stalled(auto, False)                    # fixed: a unit gets through
        with sqlite3.connect(auto["db"]) as rw:
            rw.execute("INSERT INTO ingestion_units(unit_id,candidate_id,claim_group,state,"
                       "updated_at) VALUES('cand-9.u1','cand-9','{}','written',?)", (_now(),))
        await _pass_and_settle(tmp_path)
        assert _records(auto) == {}
        _stalled(auto, True)
        await _pass_and_settle(tmp_path)
    asyncio.run(scenario())
    assert auto["started"] == ["ingestion:stalled", "ingestion:stalled"]


def test_an_outcome_that_cannot_be_saved_is_not_lost(auto, tmp_path, monkeypatch):
    """The records file unreadable just as a run ends: the outcome is saved on the next
    pass, not turned into "SwarmAI stopped while Swarm was working on this"."""
    _job_state(auto, fails=3)
    real_read = type(auto["records"]).read_text
    flaky = {"on": False}

    def read(self, *a, **k):
        if flaky["on"] and self == auto["records"]:
            raise PermissionError("busy")
        return real_read(self, *a, **k)
    monkeypatch.setattr(type(auto["records"]), "read_text", read)

    async def session(item, on_session=None):
        auto["started"].append(item.id)
        flaky["on"] = True
        return aa.FAILED, "the token expired; sign in again"
    monkeypatch.setattr(aa, "run_session", session)

    asyncio.run(_pass_and_settle(tmp_path))
    flaky["on"] = False
    asyncio.run(_pass_and_settle(tmp_path))
    rec = _records(auto)["job:digest"]
    assert rec["state"] == aa.FAILED and rec["note"] == "the token expired; sign in again"
    assert auto["started"] == ["job:digest"]


def test_a_read_failure_does_not_reset_a_failed_job_record(auto, tmp_path):
    """M1: a half-written state.json makes the job item vanish for one pass; its
    failed record stays, so the item does not come back as a new run."""
    _job_state(auto, fails=3)

    async def scenario():
        assert await _pass_and_settle(tmp_path) == "job:digest"
        assert _records(auto)["job:digest"]["state"] == aa.FAILED
        auto["state"].write_text('{"jobs": {"digest": {"last_run": ')   # a torn write
        await _pass_and_settle(tmp_path)
        assert _records(auto)["job:digest"]["state"] == aa.FAILED
        _job_state(auto, fails=3)                                      # read again
        await _pass_and_settle(tmp_path)
        _job_state(auto, fails=4)                                      # and a new state
        await _pass_and_settle(tmp_path)
    asyncio.run(scenario())
    assert auto["started"] == ["job:digest"]
    assert _shown(auto, "job").handler == authority.HANDLER_PERSON

    # the 24-hour breaker reset clears the streak without a good run: still not fixed
    _job_state(auto, fails=0)
    asyncio.run(_pass_and_settle(tmp_path))
    assert _records(auto)["job:digest"]["state"] == aa.FAILED
    # a good run is: the record goes, and a later streak is Swarm's again, once
    _job_state(auto, fails=0, status="success")
    asyncio.run(_pass_and_settle(tmp_path))
    assert _records(auto) == {}
    _job_state(auto, fails=3)
    asyncio.run(_pass_and_settle(tmp_path))
    assert auto["started"] == ["job:digest", "job:digest"]


def test_an_unreadable_records_file_does_not_reset_records(auto, tmp_path, monkeypatch):
    """A transient read error on the records file skips the pass: nothing starts and
    the failed records are not overwritten."""
    _job_state(auto, fails=3)
    asyncio.run(_pass_and_settle(tmp_path))
    before = auto["records"].read_text()
    assert json.loads(before)["job:digest"]["state"] == aa.FAILED
    _job_state(auto, fails=4)                                          # a new state

    real_read = type(auto["records"]).read_text

    def flaky(self, *a, **k):
        if self == auto["records"]:
            raise PermissionError("busy")
        return real_read(self, *a, **k)
    monkeypatch.setattr(type(auto["records"]), "read_text", flaky)
    assert asyncio.run(_pass_and_settle(tmp_path)) is None
    monkeypatch.setattr(type(auto["records"]), "read_text", real_read)
    assert auto["records"].read_text() == before
    asyncio.run(_pass_and_settle(tmp_path))
    assert auto["started"] == ["job:digest"]
    assert _records(auto)["job:digest"]["state"] == aa.FAILED
