"""How often a person stepped into a local run (core.run_interventions; design
2026-11-01-brain-eval-and-runs §3): answers, corrections (messages into the run's session
while it ran) and rescues (a person's landing) count; the continue prompt, messages
outside the run, and the dispatching agent's answers do not."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

from core import run_interventions as RI

T0 = datetime(2026, 10, 31, 9, 0, tzinfo=timezone.utc)
T1 = datetime(2026, 10, 31, 10, 0, tzinfo=timezone.utc)
T2 = datetime(2026, 10, 31, 11, 0, tzinfo=timezone.utc)


def _db(tmp_path, rows):
    path = tmp_path / "data.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE messages (id TEXT, session_id TEXT, role TEXT, content TEXT, created_at TEXT)")
    for i, (sid, role, text, at) in enumerate(rows):
        conn.execute("INSERT INTO messages VALUES (?,?,?,?,?)",
                     (str(i), sid, role, json.dumps([{"type": "text", "text": text}]),
                      at.astimezone().replace(tzinfo=None).isoformat()))
    conn.commit()
    conn.close()
    return path


def _min(dt, m):
    return dt.replace(minute=m)


RAW = {"created_at": T0.isoformat(), "finished_at": T2.isoformat(), "session_id": "s-b",
       "session_history": [{"session_id": "s-a", "until": T1.timestamp(), "reason": "takeover"}],
       "human_decisions": 1}


def test_messages_in_each_owner_window_count_and_the_rest_do_not(tmp_path):
    db = _db(tmp_path, [
        ("s-a", "user", "start the run", _min(T0, 0).replace(hour=8)),          # before: the request
        ("s-a", "user", "use the other table", _min(T0, 20)),                     # s-a while it owned
        ("s-a", "user", "unrelated later", _min(T1, 30)),                         # s-a after takeover
        ("s-b", "user", "where are you?", _min(T1, 15)),                          # s-b after takeover
        ("s-b", "user", RI.CONTINUE_PREFIX + "run_x (project P): run ...", _min(T1, 40)),
        ("s-b", "assistant", "ok", _min(T1, 41)),
        ("s-b", "user", "after it finished", _min(T2, 30)),
    ])
    assert RI.messages(RAW, db_path=db) == 2
    got = RI.for_run(RAW, db_path=db)
    assert got == {"answers": 1, "corrections": 2, "rescues": 0, "total": 3}


def test_a_person_landing_is_a_rescue_not_an_answer():
    raw = {**RAW, "landing": "human_override", "human_decisions": 2}     # one answer + the run-land
    assert RI.count(raw, corrections=0) == {"answers": 1, "corrections": 0, "rescues": 1, "total": 2}
    host = {**raw, "human_decisions": 1, "landed_by": "host"}            # the Hive's agent landed it
    assert RI.count(host, corrections=0) == {"answers": 1, "corrections": 0, "rescues": 0, "total": 1}
    # the fold says who landed it (run.json has no auth grants): a host grant for another
    # action does not make the person's landing the host's
    person = {**raw, "landed_by": "person", "auth_grants": {"other": {"authorized_by": "host"}}}
    assert RI.count(person, corrections=0)["rescues"] == 1


def test_a_confirmed_landed_as_is_a_rescue():
    raw = {**RAW, "status": "abandoned", "human_decisions": 0, "landed_as": {"repo": "a" * 40},
           "landed_as_request": "r1", "land_requests": {"r1": {"action": "confirm_landed"}}}
    assert RI.count(raw, corrections=0)["rescues"] == 1
    # the engine showed it landed (no request bound), though an unapproved request exists
    agent = {**raw, "landed_as_request": None}
    assert RI.count(agent, corrections=0)["rescues"] == 0
    events = [{"kind": "landed_as", "at": "t", "payload": {"commits": {"repo": "a" * 40}}}]
    assert RI.details(agent, events, db_path=None)["rescues"] == []
    # an older projection without the field: a consumed confirm_landed authorization
    old = {k: v for k, v in raw.items() if k != "landed_as_request"}
    assert RI.count({**old, "auth_consumed": {"c": {"action": "confirm_landed"}}}, corrections=0)["rescues"] == 1
    assert RI.count(old, corrections=0)["rescues"] == 0


def test_the_dashboards_notes_are_not_corrections(tmp_path):
    db = _db(tmp_path, [
        ("s-b", "user", RI.ANSWER_PREFIX + "q1 on the dashboard: a — files. Continue pipeline run run_x", _min(T1, 10)),
        ("s-b", "user", "and keep it small", _min(T1, 20)),
    ])
    assert RI.messages(RAW, db_path=db) == 1


def test_an_unreadable_chat_store_is_unknown_never_zero(tmp_path):
    assert RI.messages(RAW, db_path=tmp_path / "missing.db") is None
    assert RI.for_run(RAW, db_path=tmp_path / "missing.db")["total"] is None


def test_a_running_run_reads_until_now(tmp_path):
    raw = {"created_at": T0.isoformat(), "session_id": "s-a", "human_decisions": 0}
    db = _db(tmp_path, [("s-a", "user", "steer", _min(T0, 30))])
    assert RI.messages(raw, db_path=db, now=T1) == 1


def test_a_run_without_a_start_time_is_unknown():
    assert RI.messages({"session_id": "s"}) is None
    assert RI.count({"human_decisions": 1}, corrections=None)["total"] is None


def test_the_card_counts_a_persons_messages_from_the_chat_store(tmp_path, monkeypatch):
    """routers.pipelines._card_facts reads the same function (the app data dir's data.db)."""
    monkeypatch.setenv("SWARM_DATA_DIR", str(tmp_path))
    _db(tmp_path, [("s-b", "user", "steer it", _min(T1, 20))])
    from routers.pipelines import _card_facts
    facts = _card_facts({**RAW, "human_decisions": 0}, "completed")
    assert facts["human_decisions"] == 1
    assert facts["interventions"] == {"answers": 0, "corrections": 1, "rescues": 0, "total": 1}
