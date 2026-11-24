"""A dispatched session's review on any Hive (core/hive_review): asked for as soon as its
turn ends on EC2 as on AgentCore, never while a pipeline run of it waits on a decision,
and again after a re-run. Interactive and non-Hive sessions keep s2_jobs.REVIEW_DELAY.
"""
from __future__ import annotations

import json
from datetime import timedelta
from types import SimpleNamespace

import pytest

SID = "sess_dispatched_ec2_1"
OTHER = "sess_interactive_hive_1"
WORK_ID = "so_" + "b" * 12
RUN_ID = "run_0000abcd"
UNTIL = "2026-11-24T00:00:00"


@pytest.fixture
def hive(tmp_path, monkeypatch):
    """An EC2 Hive (SWARMAI_MODE=hive, no AgentCore carrier) with its own workspace."""
    from core import hive_records, pipeline_human, project_registry
    from core.ingestion import s2_jobs
    monkeypatch.setenv("SWARMAI_MODE", "hive")
    monkeypatch.delenv("SWARMAI_CARRIER", raising=False)
    ws = tmp_path / "ws"
    (ws / "Projects" / "SwarmAI").mkdir(parents=True)
    monkeypatch.setattr(hive_records, "_workspace", lambda: ws)
    monkeypatch.setattr(pipeline_human, "_workspace", lambda: ws)
    monkeypatch.setattr(project_registry, "get_swarmws", lambda: ws)
    monkeypatch.setattr(s2_jobs, "_WAKER", None)
    monkeypatch.setattr(s2_jobs, "_bound_project", lambda sid: None)
    contracts: set = set()

    def engine(run_id):
        """The engine of a run with a protected contract (``contracts``), read from its
        run.json; None for any other, as hive_review._engine answers for a run.json no
        contract backs (test_real_v1_run_waiting_holds_the_review runs the real one)."""
        if run_id not in contracts:
            return None
        data = json.loads(next((ws / "Projects").glob(f"*/.artifacts/runs/{run_id}/run.json")).read_text())
        return SimpleNamespace(owner_session=lambda: data["session_id"],
                               pending_decision=lambda: {"request_id": "q1"}
                               if data["status"] == "awaiting_human" else None)
    from core import hive_review
    monkeypatch.setattr(hive_review, "_engine", engine)
    return SimpleNamespace(ws=ws, contracts=contracts)


@pytest.fixture
def requests(monkeypatch):
    from core.ingestion import s2_jobs
    got: list = []
    monkeypatch.setattr(s2_jobs, "request", lambda sid, reason, **kw: got.append((sid, reason, kw)) or "queued")
    return got


def _bind(session_id: str, work_id: str = WORK_ID) -> None:
    """A work order prepared on this Hive and bound to the session (hive_workspace.bind)."""
    from core import hive_workspace
    d = hive_workspace._state() / "orders"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{work_id}.json").write_text(json.dumps({"work_id": work_id, "session_id": session_id}))


def _run(hive, session_id: str, status: str, run_id: str = RUN_ID, contract: bool = True) -> None:
    """A v1 pipeline run's run.json projection, owned by *session_id*; ``contract``: it
    has the protected contract (else only a run.json, as anything could write one)."""
    if contract:
        hive.contracts.add(run_id)
    d = hive.ws / "Projects" / "SwarmAI" / ".artifacts" / "runs" / run_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "run.json").write_text(json.dumps({"run_id": run_id, "status": status, "session_id": session_id,
                                            "decision_cap": 2, "stage": "plan"}))


async def _turn_end(monkeypatch, session_id: str, seal=None) -> None:
    """The router's turn seal for *session_id* (its entry stubbed unless *seal* is given)."""
    from core import hive_records
    from core.session_router import SessionRouter
    monkeypatch.setattr(hive_records, "seal_turn", seal or (lambda *a, **k: None))
    await SessionRouter._seal_turn_record(SimpleNamespace(), session_id, UNTIL)


async def _idle_hook(session_id: str) -> None:
    from core.ingestion.sources.s2_session import SessionExtractionHook
    await SessionExtractionHook().execute(SimpleNamespace(session_id=session_id, deleting=False,
                                                          sdk_session_id="sdk-1"))


def _kinds(ws, session_id: str) -> list[str]:
    from core import hive_records
    return [e["kind"] for e in hive_records._read_manifest(hive_records.session_dir(ws, session_id))]


# ── R1: the review at the turn's end, on EC2 too ─────────────────────────────

async def test_ec2_dispatched_turn_end_requests_review_now(hive, requests, monkeypatch):
    _bind(SID)
    await _turn_end(monkeypatch, SID)
    assert requests == [(SID, "session_end", {"sdk_session_id": None, "delay": timedelta(0)})]


async def test_interactive_and_non_hive_sessions_keep_review_delay(hive, requests, monkeypatch):
    from core.ingestion import s2_jobs
    _bind(SID)                                   # the order names another session
    await _turn_end(monkeypatch, OTHER)
    assert requests == []                        # an interactive Hive session: not at the turn end
    await _idle_hook(OTHER)
    assert requests == [(OTHER, "session_end", {"sdk_session_id": "sdk-1", "delay": s2_jobs.REVIEW_DELAY})]
    requests.clear()
    monkeypatch.delenv("SWARMAI_MODE")           # not a Hive: even a session an order names
    await _turn_end(monkeypatch, SID)
    assert requests == []
    await _idle_hook(SID)
    assert requests == [(SID, "session_end", {"sdk_session_id": "sdk-1", "delay": s2_jobs.REVIEW_DELAY})]


async def test_idle_hook_dispatched_ec2_asks_without_delay(hive, requests, monkeypatch):
    from core import hive_records
    from core.ingestion import s2_jobs
    _bind(SID)
    jobs = {"open": 1}
    monkeypatch.setattr(s2_jobs, "open_jobs", lambda sid=None, **kw: jobs["open"])
    await _idle_hook(SID)
    assert requests == []                        # the turn's end asked: its job is open
    jobs["open"] = 0
    hive_records.append(hive.ws, SID, "turn", data=b"{}")
    hive_records.append(hive.ws, SID, "closed", data=b"{}")
    await _idle_hook(SID)
    assert requests == []                        # reviewed and closed: nothing to ask
    hive_records.append(hive.ws, SID, "turn", data=b"{}")
    await _idle_hook(SID)
    assert requests == [(SID, "session_end", {"sdk_session_id": "sdk-1"})]     # no delay


# ── R2: a pipeline decision waiting holds the review and ``closed`` ──────────

@pytest.mark.parametrize("carrier", [None, "agentcore"])
async def test_pending_decision_blocks_review_until_answered(hive, requests, monkeypatch, carrier):
    from core import hive_review
    from core.ingestion import s2_jobs
    if carrier:
        monkeypatch.setenv("SWARMAI_CARRIER", carrier)
    monkeypatch.setattr(s2_jobs, "open_jobs", lambda sid=None, **kw: 0)
    _bind(SID)
    _run(hive, SID, "awaiting_human")
    await _turn_end(monkeypatch, SID)
    await _idle_hook(SID)
    assert not await hive_review.request_review(SimpleNamespace(), SID)
    assert requests == []
    # a run of another session waiting changes nothing for this one
    _run(hive, OTHER, "awaiting_human", run_id="run_0000ffff")
    _run(hive, SID, "active")                 # answered: the run goes on
    await _turn_end(monkeypatch, SID)
    assert requests == [(SID, "session_end", {"sdk_session_id": None, "delay": timedelta(0)})]


async def test_job_end_with_pending_decision_does_not_close(hive, monkeypatch):
    from core import hive_lessons, hive_records
    lessons = [{"id": "L1", "source": "S7", "unit": {"kind": "cognition", "text": "one"}}]
    monkeypatch.setattr(hive_lessons, "collect", lambda sid, exclude=None: {
        "lessons": [x for x in lessons if x["id"] not in (exclude or set())], "truncated": False})
    _bind(SID)
    _run(hive, SID, "awaiting_human")
    hive_records.on_session_job_end(SID, "done")
    assert _kinds(hive.ws, SID) == ["lessons"]   # sealed, but the run is not over
    hive_records.on_session_job_end(SID, "failed")
    assert _kinds(hive.ws, SID) == ["lessons"]
    _run(hive, SID, "completed")
    hive_records.on_session_job_end(SID, "done")
    assert _kinds(hive.ws, SID) == ["lessons", "closed"]
    # a deleted session is closed though its decision waits: nothing can answer it
    _run(hive, OTHER, "awaiting_human", run_id="run_0000ffff")
    hive_records.on_session_deleted(OTHER)
    assert _kinds(hive.ws, OTHER)[-1] == "closed"


async def test_unreadable_runs_count_as_waiting(hive, requests, monkeypatch):
    from core import hive_records, hive_review, pipeline_human

    def broken(*a, **k):
        raise OSError("runs unreadable")
    monkeypatch.setattr(pipeline_human, "owned_runs", broken)
    _bind(SID)
    assert hive_review.decision_waits(SID) is True
    await _turn_end(monkeypatch, SID)
    assert requests == []
    hive_records.on_session_job_end(SID, "done")
    assert "closed" not in _kinds(hive.ws, SID)


async def test_forged_run_json_holds_nothing(hive, requests, monkeypatch):
    """A run.json no protected contract backs (anything in the workspace can write one) or
    a directory no run could be created under never holds the review or ``closed``."""
    from core import hive_lessons, hive_records, hive_review
    monkeypatch.setattr(hive_lessons, "collect", lambda sid, exclude=None: {"lessons": [], "truncated": False})
    _bind(SID)
    _run(hive, SID, "awaiting_human", contract=False)
    _run(hive, SID, "awaiting_human", run_id="run_x.y")
    assert hive_review.decision_waits(SID) is False
    await _turn_end(monkeypatch, SID)
    assert requests == [(SID, "session_end", {"sdk_session_id": None, "delay": timedelta(0)})]
    hive_records.on_session_job_end(SID, "done")
    assert _kinds(hive.ws, SID) == ["closed"]
    # a run whose engine names another owner (taken over) does not hold this session either
    _run(hive, OTHER, "awaiting_human", run_id="run_0000eeee")
    assert hive_review.decision_waits(OTHER) is True
    hive.contracts.discard("run_0000eeee")
    assert hive_review.decision_waits(OTHER) is False


async def test_turn_started_during_the_hop_gets_no_review(hive, requests, monkeypatch):
    """The file reads run in a thread; a turn that starts meanwhile is seen after them."""
    from core import hive_review
    from core.session_unit import SessionState
    _bind(SID)
    unit = SimpleNamespace(session_id=SID, state=SessionState.IDLE, has_background_work=False)
    real = hive_review._due_now

    def due(sid):
        unit.state = SessionState.STREAMING          # the correction's turn began
        return real(sid)
    monkeypatch.setattr(hive_review, "_due_now", due)
    assert not await hive_review.request_review(SimpleNamespace(_units={SID: unit}), SID)
    assert requests == []


def test_real_v1_run_waiting_holds_the_review(tmp_path, monkeypatch):
    """A run created and asked a decision through the shipped pipeline-v1 CLI: its engine
    says it waits, so the session waits; a forged run.json beside it changes nothing."""
    from core import hive_review
    from tests.pipeline_v1_e2e_common import _decision
    from tests.pipeline_v1_rig import SESSION, Rig
    r = Rig(tmp_path, monkeypatch, profile="trivial")
    r.create()
    assert r.v1("decision-request", "--artifact", _decision(r), "--document", "REQUIREMENTS").get("ok") is True
    assert r.state()["status"] == "awaiting_human"
    assert hive_review.decision_waits(SESSION) is True
    assert hive_review.decision_waits("sess_someone_else_1") is False
    forged = r.ws / "Projects" / "Forged" / ".artifacts" / "runs" / "run_0000dddd"
    forged.mkdir(parents=True)
    (forged / "run.json").write_text(json.dumps({"status": "awaiting_human", "session_id": "sess_forger_0001",
                                                 "decision_cap": 1}))
    assert hive_review.decision_waits("sess_forger_0001") is False
    # a copy of the real run's directory under another project, claiming it for the
    # forger: the contract decides who owns it and what it waits on, and the copy makes
    # nothing ambiguous for the real owner either
    copy = r.ws / "Projects" / "Forged" / ".artifacts" / "runs" / r.run_id
    copy.mkdir(parents=True)
    (copy / "run.json").write_text(json.dumps({"status": "awaiting_human", "session_id": "sess_forger_0001",
                                               "decision_cap": 1}))
    assert hive_review.decision_waits("sess_forger_0001") is False
    assert hive_review.decision_waits(SESSION) is True


async def test_agentcore_busy_counts_a_waiting_decision(hive, monkeypatch):
    """On AgentCore a waiting decision is a wait: the microVM is busy and state.json says
    ``waiting``, so it neither idles out nor reads as ended."""
    from core import hive_agentcore
    from core.permission_manager import PermissionManager
    monkeypatch.setenv("SWARMAI_CARRIER", "agentcore")
    monkeypatch.setattr(hive_agentcore, "_workspace", lambda: hive.ws)
    monkeypatch.setattr(hive_agentcore, "_usage_fingerprint", lambda: (0, 0))
    monkeypatch.setattr(hive_agentcore, "_review_open", lambda: False)
    hive_agentcore._reset_for_tests()
    try:
        unit = SimpleNamespace(session_id=SID, state=None, has_background_work=False)
        router = SimpleNamespace(list_units=lambda: [unit])
        pm = PermissionManager(persist_path=hive.ws / "pending.json")
        _run(hive, SID, "awaiting_human")
        got = await hive_agentcore.busy(router=router, pm=pm, workspace=hive.ws)
        assert "waiting_input" in got["reasons"]
        assert hive_agentcore._fingerprints(hive_agentcore.live_conditions(router, pm), hive.ws)["state.json"] == "waiting"
        _run(hive, SID, "active")
        got = await hive_agentcore.busy(router=router, pm=pm, workspace=hive.ws)
        assert "waiting_input" not in got["reasons"]
        # deleted while its decision waits: the record is closed and nothing can answer
        # it, so it holds the microVM no longer and state.json reads ended
        from core import hive_lessons, hive_records
        monkeypatch.setattr(hive_lessons, "collect", lambda sid, exclude=None: {"lessons": [], "truncated": False})
        monkeypatch.setattr(hive_agentcore, "export_pending", lambda ws: False)
        _run(hive, SID, "awaiting_human")
        hive_records.on_session_deleted(SID)
        got = await hive_agentcore.busy(router=router, pm=pm, workspace=hive.ws)
        assert "waiting_input" not in got["reasons"]
        assert hive_agentcore._fingerprints(hive_agentcore.live_conditions(router, pm), hive.ws)["state.json"] == "ended"
    finally:
        hive_agentcore._reset_for_tests()


# ── R3: a re-run after the review seals its lessons too (executed, end to end) ─

async def test_rerun_after_review_seals_its_lessons(hive, monkeypatch):
    """Two rounds of one dispatched EC2 session: each turn end asks for the review, the
    worker runs the job, and its end seals that round's new lessons, then ``closed``."""
    import threading
    from core import hive_lessons, hive_records
    from core.ingestion import worker
    from core.ingestion.api import connect_rw
    lessons = [{"id": "L1", "source": "S7", "unit": {"kind": "cognition", "text": "first"}}]
    monkeypatch.setattr(hive_lessons, "collect", lambda sid, exclude=None: {
        "lessons": [x for x in lessons if x["id"] not in (exclude or set())], "truncated": False})
    monkeypatch.setattr(worker, "ingestion_admission", lambda lane, **kw: SimpleNamespace(run=True, llm=True))

    def seal(ws, sid, rows, until=None):
        hive_records.append(ws, sid, "turn", data=b"{}")

    def drain() -> int:
        conn = connect_rw()
        try:
            ex = worker.S2JobExecutor.__new__(worker.S2JobExecutor)
            ex._stop = threading.Event()
            ex._held_until = 0.0
            ex._extract = lambda sid: None
            return ex.drain(conn)
        finally:
            conn.close()

    _bind(SID)
    await _turn_end(monkeypatch, SID, seal)
    assert drain() == 1
    assert _kinds(hive.ws, SID) == ["turn", "lessons", "closed"]
    # the re-run: a correction into the same session, which learns one more lesson
    lessons.append({"id": "L2", "source": "S7", "unit": {"kind": "cognition", "text": "second"}})
    await _turn_end(monkeypatch, SID, seal)
    assert drain() == 1
    assert _kinds(hive.ws, SID) == ["turn", "lessons", "closed", "turn", "lessons", "closed"]
    entries = hive_records._read_manifest(hive_records.session_dir(hive.ws, SID))
    d =hive_records.session_dir(hive.ws, SID)
    body = json.loads((d / hive_records._entry_name(entries[4])).read_text(encoding="utf-8"))
    assert [x["unit"]["text"] for x in body["lessons"]] == ["second"]   # only the new one


# ── R4: the Host's idle stop lets the Hive go once the lessons are in ────────

def test_hive_idle_releases_once_lessons_sealed(tmp_path, monkeypatch):
    from datetime import datetime, timezone
    from core import hive_idle, hive_return, swarm_out, swarm_out_results
    from core import swarm_out_settle as st
    monkeypatch.setattr(swarm_out, "_state_dir", lambda: tmp_path)
    monkeypatch.setattr(hive_return, "load", lambda d, k: None)
    monkeypatch.setattr(swarm_out_results, "load", lambda d: None)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    row = {"dispatch_id": WORK_ID, "hive_id": "h1", "session_id": SID, "sent_at": now,
           "project_agent": {"returns": "answer"}, "state": "stopped", "state_reason": "idle",
           "result": {"ended": "completed", "provisional": False, "ended_at": now,
                      "lessons": None, "work": {"repos": []}}}

    def waiting(closed: bool) -> list[str]:
        settle = st.derive(row, {"records": {"closed": closed, "last_at": now}})
        monkeypatch.setattr(swarm_out, "snapshot", lambda: {"runs": [{"hive_id": "h1", "settle": settle}]})
        lessons = hive_idle._lessons_by_hive().get("h1", 0)
        return hive_idle.busy_reasons(0, {}, lessons, False)

    assert waiting(False) == ["1 run(s) waiting for lessons"]     # the record not closed yet
    assert waiting(True) == []                                    # lessons sealed, closed: idle
