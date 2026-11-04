"""A Hive's boot closes the turns its daemon's death left open (core/hive_boot; design
docs/design/2026-11-04-kirocrew-borrow.md §3.B1, §6 B1).

Each test answers one question:
- an open turn, and a trailing ``paused`` turn with no pipeline decision waiting, are
  each closed with a ``turn_outcome`` ``daemon_stopped`` whose ``repos`` come from the
  same function a normal turn end uses, then sealed; a second run changes nothing;
- a session with a waiting decision, one sealed through its newest row, and one whose
  last turn started after this process started are not touched;
- the readers take ``daemon_stopped`` as a final ending: ``summarize`` says so, not
  provisional; ``settle.derive`` derives what it derives for an interrupted turn;
- a session whose rows cannot be read is skipped with a warning, the others go on.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from core import hive_records as hr

OPEN, PAUSED, TRAILING, LONG, WAITING, SEALED, NEW, BAD, MOVED = (
    f"sess_boot_{n}_000" for n in ("open", "paused", "trailing", "long", "waiting", "sealed", "new", "bad", "moved"))
DECLARED = [{"url": "https://github.com/o/r.git", "ref": "main"}]
FACTS = [{"url": "https://github.com/o/r.git", "ref": "main", "dir": "repos/r", "present": True,
          "commits_total": 2, "files_total": 3}]
BEFORE = "2026-11-01T10:00:00"           # naive local, as the messages table writes it


def _u(i: str, at: str) -> dict:
    return {"id": f"u{i}", "role": "user", "created_at": at, "content": [{"type": "text", "text": "go"}]}


def _a(i: str, at: str, *blocks: dict) -> dict:
    return {"id": f"a{i}", "role": "assistant", "created_at": at,
            "content": [{"type": "text", "text": f"working {i}"}, *blocks]}


def _outcome(ended: str) -> dict:
    return {"type": "turn_outcome", "ended": ended, "at": "2026-10-31T00:00:00+00:00"}


class _Msgs:
    def __init__(self, rows: dict[str, list[dict]]):
        self.rows = rows
        self.bad: set[str] = set()
        self.moved: set[str] = set()

    async def list_by_session_paginated(self, sid, limit=None, **_kw):
        if sid in self.bad:
            raise RuntimeError("database is locked")
        if limit == 1 and sid in self.moved:          # a row written while the repos were read
            return [{"id": "later", "role": "user", "created_at": "2026-11-01T12:00:02"}]
        rows = list(self.rows.get(sid, []))
        return rows[-limit:] if limit else rows

    async def put(self, row):
        self.rows.setdefault(row["session_id"], []).append(dict(row))
        return row

    async def append_to_turn_row(self, sid, key, blocks):
        return False


class _Sessions:
    async def get(self, sid):
        return {"id": sid, "created_at": "2026-11-01T09:00:00", "metadata": {"task_repos": DECLARED}}


@pytest.fixture
def boot(tmp_path, monkeypatch):
    """A Hive with the sessions of the orders it has, its messages table and its router."""
    from core import hive_boot, hive_bundle, hive_workspace, pipeline_human, task_repos
    from core.session_router import SessionRouter
    import core.project_registry as pr
    import database
    ws = tmp_path / "ws"
    ws.mkdir()
    monkeypatch.setenv("SWARMAI_MODE", "hive")
    monkeypatch.setenv("SWARM_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(pr, "get_swarmws", lambda: ws)
    msgs = _Msgs({})
    monkeypatch.setattr(database, "db", SimpleNamespace(messages=msgs, sessions=_Sessions()))
    orders: dict[str, dict] = {}
    monkeypatch.setattr(hive_workspace, "_orders", lambda: sorted(orders))
    monkeypatch.setattr(hive_workspace, "load", lambda w, wid: orders.get(wid))
    calls: list = []

    def repo_facts(workspace, repos, since):
        calls.append(repos)
        return [dict(f) for f in FACTS]
    monkeypatch.setattr(task_repos, "repo_facts", repo_facts)
    monkeypatch.setattr(hive_bundle, "add_bundles", lambda w, s, facts: facts)
    monkeypatch.setattr(hive_bundle, "seal_bundles", lambda w, s, facts: facts)
    waiting: list[dict] = []
    monkeypatch.setattr(pipeline_human, "pending_decisions", lambda: list(waiting))
    # this process started after every row written so far
    monkeypatch.setattr(hive_boot, "STARTED_AT", datetime(2026, 11, 1, 12, 0, 0))

    router = SimpleNamespace(TASK_REPO_FACTS_TIMEOUT_S=SessionRouter.TASK_REPO_FACTS_TIMEOUT_S,
                             TASK_REPO_BUNDLES_TIMEOUT_S=SessionRouter.TASK_REPO_BUNDLES_TIMEOUT_S,
                             _persist_assistant_blocks=SessionRouter._persist_assistant_blocks)
    router._task_repo_facts = lambda sid: SessionRouter._task_repo_facts(router, sid)
    router._seal_turn_record = lambda sid, until: SessionRouter._seal_turn_record(router, sid, until)

    def add(sid: str, rows: list[dict], sealed_through: int | None):
        orders[f"so_{len(orders):012x}"] = {"work_id": f"so_{len(orders):012x}", "session_id": sid}
        msgs.rows[sid] = rows
        if sealed_through is not None:
            hr.seal_turn(ws, sid, rows[:sealed_through + 1])

    def run():
        return asyncio.run(hive_boot.close_open_turns(router))

    return SimpleNamespace(ws=ws, msgs=msgs, add=add, run=run, waiting=waiting, calls=calls,
                           orders=orders, router=router)


def _snapshot(b, sid):
    d = hr.session_dir(b.ws, sid)
    return (json.dumps(b.msgs.rows[sid], sort_keys=True, default=str),
            [e["seq"] for e in hr.entries(b.ws, sid)["entries"]] if d.exists() else None,
            hr._sealed_through(d))


def _closed_outcome(b, sid) -> dict:
    last = b.msgs.rows[sid][-1]
    blocks = [x for x in last["content"] if x.get("type") == "turn_outcome"]
    assert last["role"] == "assistant" and len(blocks) == 1
    return blocks[0]


def _long_turn(n: int) -> list[dict]:
    """A turn whose start is older than the rows read back (hive_records.TURN_ROWS)."""
    rows = [_u("0", "2026-11-01T08:00:00")]
    rows += [{"id": f"s{i}", "role": "assistant", "created_at": f"2026-11-01T09:{i // 60:02d}:{i % 60:02d}",
              "content": [{"type": "text", "text": f"step {i}"}]} for i in range(n)]
    return rows


def test_open_turn_closed_and_sealed_once(boot):
    # a first turn done and sealed, then a turn killed mid-way (no outcome)
    boot.add(OPEN, [_u("1", "2026-11-01T09:00:00"), _a("1", "2026-11-01T09:00:05", _outcome("completed")),
                    _u("2", BEFORE), _a("2", "2026-11-01T10:00:05")], sealed_through=1)
    # a turn that paused on a question, sealed; its continuation was killed mid-way
    boot.add(PAUSED, [_u("1", BEFORE), _a("1", "2026-11-01T10:00:05", _outcome("paused")),
                      {"id": "c1", "role": "assistant", "created_at": "2026-11-01T10:30:00",
                       "content": [{"type": "text", "text": "continuing"}]}], sealed_through=1)
    # a turn that paused on a question and was sealed at its end (its outcome the newest
    # row): its question card died with the process
    # (its outcome appended onto its row: the row's updated_at is when it last wrote)
    boot.add(TRAILING, [_u("1", BEFORE), {**_a("1", "2026-11-01T10:00:05", _outcome("paused")),
                                          "updated_at": "2026-11-01T10:20:00"}], sealed_through=1)
    # a turn longer than the rows read back, killed mid-way, never sealed
    boot.add(LONG, _long_turn(hr.TURN_ROWS + 5), sealed_through=None)
    newest_at = {sid: (boot.msgs.rows[sid][-1].get("updated_at") or boot.msgs.rows[sid][-1]["created_at"])
                 for sid in (OPEN, PAUSED, TRAILING, LONG)}
    closed = boot.run()
    assert closed == 4
    for sid in (OPEN, PAUSED, TRAILING, LONG):
        o = _closed_outcome(boot, sid)
        assert o["ended"] == "daemon_stopped"
        assert o["repos"] == FACTS                       # what a normal end computes
        # lost with the process, except for a turn that only sat on its question (its
        # paused outcome counted them)
        assert o["gate_refusals"] == {} and o["gate_refusals_partial"] is (sid != TRAILING)
        # when the turn last wrote, not when the Hive came back
        assert o["at"] == hr._at(newest_at[sid]).astimezone(timezone.utc).isoformat()
        d = hr.session_dir(boot.ws, sid)
        assert hr._sealed_through(d) == boot.msgs.rows[sid][-1]["id"]   # sealed to the outcome
        last = hr.entries(boot.ws, sid)["entries"][-1]
        body = json.loads((d / last["file"]).read_text())
        assert any(b.get("ended") == "daemon_stopped" for r in body["rows"] for b in r["content"])
    assert len(boot.calls) == 4 and boot.calls[0][0]["url"] == DECLARED[0]["url"]
    before = {sid: _snapshot(boot, sid) for sid in (OPEN, PAUSED, TRAILING, LONG)}
    assert boot.run() == 0                               # a second run changes nothing
    assert {sid: _snapshot(boot, sid) for sid in (OPEN, PAUSED, TRAILING, LONG)} == before


def test_skips_decision_sealed_and_new_turns(boot):
    # no outcome, but sealed through its newest row: nothing unsealed
    boot.add(SEALED, [_u("1", BEFORE), _a("1", "2026-11-01T10:00:05")], sealed_through=1)
    # its last turn started after this process started: not ours to close
    boot.add(NEW, [_u("1", BEFORE), _a("1", "2026-11-01T10:00:05", _outcome("completed")),
                   _u("2", "2026-11-01T12:00:01"), _a("2", "2026-11-01T12:00:05")], sealed_through=1)
    # a trailing paused turn, a pipeline decision of this session waiting: the decision
    # is still delivered
    boot.add(f"{WAITING}x", [_u("1", BEFORE), _a("1", "2026-11-01T10:00:05", _outcome("paused"))],
             sealed_through=1)
    boot.waiting.append({"run_id": "run_0000bbbb", "session_id": f"{WAITING}x"})
    # a row was written in the session while its repositories were read
    boot.add(MOVED, [_u("1", BEFORE), _a("1", "2026-11-01T10:00:05")], sealed_through=None)
    boot.msgs.moved.add(MOVED)
    sids = (f"{WAITING}x", SEALED, NEW, MOVED)
    before = {sid: _snapshot(boot, sid) for sid in sids}
    assert boot.run() == 0
    assert {sid: _snapshot(boot, sid) for sid in sids} == before
    assert len(boot.calls) == 1                          # MOVED's, read before it moved


def test_skips_paused_when_decisions_unreadable(boot, monkeypatch):
    """Whether a decision waits is not known: a turn sitting on its question is left
    alone; an open one, and a paused one whose continuation was cut off (its question was
    answered), are closed."""
    from core import pipeline_human

    def unreadable():
        raise LookupError("run_0000aaaa has no v1 engine")
    monkeypatch.setattr(pipeline_human, "pending_decisions", unreadable)
    boot.add(TRAILING, [_u("1", BEFORE), _a("1", "2026-11-01T10:00:05", _outcome("paused"))], sealed_through=1)
    # the continuation wrote into the paused row itself (same id): the marker cannot tell
    boot.add(PAUSED, [_u("1", BEFORE), _a("1", "2026-11-01T10:00:05", _outcome("paused"),
                                          {"type": "tool_result", "tool_use_id": "q1", "content": "A"})],
             sealed_through=1)
    boot.add(OPEN, [_u("1", BEFORE), _a("1", "2026-11-01T10:00:05")], sealed_through=None)
    before = _snapshot(boot, TRAILING)
    assert boot.run() == 2
    assert _snapshot(boot, TRAILING) == before
    assert _closed_outcome(boot, OPEN)["ended"] == "daemon_stopped"
    assert _closed_outcome(boot, PAUSED)["ended"] == "daemon_stopped"


def test_off_hive_does_nothing(boot, monkeypatch):
    monkeypatch.delenv("SWARMAI_MODE", raising=False)
    boot.add(OPEN, [_u("1", BEFORE), _a("1", "2026-11-01T10:00:05")], sealed_through=None)
    before = json.dumps(boot.msgs.rows[OPEN])
    assert boot.run() == 0
    assert json.dumps(boot.msgs.rows[OPEN]) == before


def test_unreadable_session_skipped(boot, caplog):
    boot.add(BAD, [_u("1", BEFORE), _a("1", "2026-11-01T10:00:05")], sealed_through=None)
    boot.add(OPEN, [_u("1", BEFORE), _a("1", "2026-11-01T10:00:05")], sealed_through=None)
    boot.msgs.bad.add(BAD)
    with caplog.at_level(logging.WARNING, logger="core.hive_boot"):
        assert boot.run() == 1
    assert any(BAD in r.getMessage() for r in caplog.records)
    assert len(boot.msgs.rows[BAD]) == 2
    assert not hr.session_dir(boot.ws, BAD).exists()
    assert _closed_outcome(boot, OPEN)["ended"] == "daemon_stopped"


# ── the readers ─────────────────────────────────────────────────────────────

def test_readers_treat_daemon_stopped_final(tmp_path, monkeypatch):
    from core import swarm_out, swarm_out_results, swarm_out_settle as st, turn_outcome
    assert "daemon_stopped" in turn_outcome.ENDED and "daemon_stopped" in swarm_out_results.ENDED
    block = {**_outcome("daemon_stopped"), "repos": FACTS, "gate_refusals": {},
             "gate_refusals_partial": True}
    rows = [_u("1", BEFORE), _a("1", "2026-11-01T10:00:05"),
            {"id": "o1", "role": "assistant", "created_at": "2026-11-01T12:00:01", "content": [block]}]
    res = swarm_out_results.summarize(rows)
    assert res["ended"] == "daemon_stopped" and res["provisional"] is False
    assert res["outcome_recorded"] is True and res["gate_refusals_partial"] is True
    assert swarm_out_results.public({"result": res})["ended"] == "daemon_stopped"
    # nothing more will be added to it: the ledger's rows are its spend
    spend = {"rows": 2, "tokens": {"input": 5, "output": 7, "cache_read": 0, "cache_write": 0},
             "cost_usd": 0.5, "models": ["m"]}
    assert swarm_out_results._usage(res, spend)["tokens_partial"] is False

    # settle: what it derives for a turn found interrupted (no_record for long enough)
    monkeypatch.setattr(swarm_out, "_state_dir", lambda: tmp_path)
    did = "so_bbbbbbbbbbbb"
    monkeypatch.setattr(swarm_out, "_log_row_of", lambda d: {"dispatch_id": did})
    monkeypatch.setattr(swarm_out_results, "load", lambda d: {"records": {"closed": True}, "result": {}})
    now = datetime.now(timezone.utc)

    def row(ended, provisional, **kw):
        r = {"dispatch_id": did, "hive_id": "h1", "session_id": "s1",
             "sent_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "project_agent": {"returns": "answer"},
             "state": "stopped", "state_reason": "idle",
             "result": {"ended": ended, "provisional": provisional, "ended_at": "2026-11-01T02:00:00Z",
                        "lessons": None, "work": {"repos": []}}}
        r.update(kw)
        return r
    old = (now - timedelta(seconds=st.INTERRUPTED_AFTER_S + 5)).strftime("%Y-%m-%dT%H:%M:%SZ")
    kept = {"records": {"closed": True, "last_at": now.strftime("%Y-%m-%dT%H:%M:%SZ")},
            "no_record_since": old}
    keys = ("stage", "actions", "verdict", "code", "settles", "why_not")
    for extra in ({}, {"state_reason": "hive_gone"}):
        cut = st.derive(row("no_record", True, **extra), kept)
        stopped = st.derive(row("daemon_stopped", False, **extra), {"records": kept["records"]})
        assert {k: stopped[k] for k in keys} == {k: cut[k] for k in keys}, extra
    cut = st.derive(row("no_record", True), kept)
    assert cut["stage"] == "stopped" and cut["why"] == "no_record"
    # commits that came back and landed: final, so its Hive gone does not close it
    from core import hive_bundle, hive_return
    monkeypatch.setattr(hive_return, "load", lambda d, k: {"status": "landed"}
                        if k == hive_bundle.key("Hive/work/x/repos/a") else None)

    def landed(ended):
        r = row(ended, False, state_reason="hive_gone", project_agent={"returns": "commits"})
        r["result"]["work"]["repos"] = [{"dir": "Hive/work/x/repos/a", "bundle": {"available": True}}]
        return st.derive(r, {"records": kept["records"]})
    assert {k: landed("daemon_stopped")[k] for k in keys} == {k: landed("crashed")[k] for k in keys}
    assert landed("daemon_stopped")["verdict"] == "pass"
    # commits kept here, not landed yet: they can still land, so not closed either
    monkeypatch.setattr(hive_return, "load", lambda d, k: {"status": "prepared"}
                        if k == hive_bundle.key("Hive/work/x/repos/a") else None)
    assert {k: landed("daemon_stopped")[k] for k in keys} == {k: landed("crashed")[k] for k in keys}
    assert landed("daemon_stopped")["stage"] != "closed"
    stopped = st.derive(row("daemon_stopped", False), {"records": kept["records"]})
    assert stopped["why"] == "daemon_stopped"     # the Hive's own fact, not "no ending recorded"


def test_lifespan_closes_turns_before_claims_and_bridge():
    """main.py's lifespan awaits it on a Hive before the claimed messages are reopened and
    before the Swarm Out bridge task is created, and imports the module first (its
    start time is the process's)."""
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / "main.py").read_text(encoding="utf-8")
    call = src.index("await hive_boot.close_open_turns(session_registry.session_router)")
    gate = src.rindex('if _detect_run_mode() == "hive":', 0, call)
    assert src[gate:call].count("\n") <= 2
    assert call < src.index("await reopen_dangling_claims()")
    assert call < src.index("asyncio.create_task(run_bridge_loop()")
    assert src.index("from core import hive_boot") < src.index("async def lifespan")


# ── a lost or failed seal ───────────────────────────────────────────────────

LOST, FAILED = "sess_boot_lost_000", "sess_boot_failed_000"


def test_lost_seal_is_sealed_without_a_new_outcome(boot):
    """The daemon died after the turn's own outcome was written but before its seal ran:
    the boot seals the turn as it is and writes no outcome of its own."""
    boot.add(LOST, [_u("1", "2026-11-01T09:00:00"), _a("1", "2026-11-01T09:00:05", _outcome("completed")),
                    _u("2", BEFORE), _a("2", "2026-11-01T10:00:05", _outcome("completed"))], sealed_through=1)
    rows_before = json.dumps(boot.msgs.rows[LOST], sort_keys=True)
    assert boot.run() == 1
    assert json.dumps(boot.msgs.rows[LOST], sort_keys=True) == rows_before   # no outcome added
    assert hr._sealed_through(hr.session_dir(boot.ws, LOST)) == "a2"
    before = _snapshot(boot, LOST)
    assert boot.run() == 0
    assert _snapshot(boot, LOST) == before


def test_failed_seal_is_not_counted_and_retried(boot):
    """A seal that fails during the boot (``_seal_turn_record`` swallows its errors) is not
    counted as closed, and the next boot seals the turn without a second outcome."""
    boot.add(FAILED, [_u("1", BEFORE), _a("1", "2026-11-01T10:00:05")], sealed_through=None)
    real_seal = boot.router._seal_turn_record

    async def broken(sid, until):
        return None
    boot.router._seal_turn_record = broken
    assert boot.run() == 0                               # outcome written, seal failed
    assert _closed_outcome(boot, FAILED)["ended"] == "daemon_stopped"
    assert hr._sealed_through(hr.session_dir(boot.ws, FAILED)) is None
    boot.router._seal_turn_record = real_seal
    assert boot.run() == 1                               # sealed now
    outcomes = [b for r in boot.msgs.rows[FAILED] for b in r.get("content") or []
                if isinstance(b, dict) and b.get("type") == "turn_outcome"]
    assert len(outcomes) == 1
    assert hr._sealed_through(hr.session_dir(boot.ws, FAILED)) == boot.msgs.rows[FAILED][-1]["id"]
