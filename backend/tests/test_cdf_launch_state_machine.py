"""CDF worker and writer state machine before the cutover (2026-10-29-cdf-launch §1 B).

Each test drives the real worker / writer on the real migrated stores of
``test_cdf_v2_e2e.cdf_env`` and pins one failure the state-machine review found:
- a parked unit is never "ready": the worker does not spin on it (B1);
- an exception in one unit is recorded on that unit, never raised out of the pass (B6);
- a retry after an abandoned plan writes the entry (B4), and a writer defer backs off (B7);
- one bad plan does not stop recovery; an invalid relation is skipped, not raised (B5);
- candidates left in_progress are recomputed (B3); judge outages back off for hours (B8);
- session jobs: an explicit save is not downgraded, a request during a failed run is
  kept, and the watermark is recorded before the optional main pass;
- a worker loop survives a pass that raises.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone

import pytest

from tests.test_cdf_v2_e2e import Extractor, _cognition, _drain, cdf_env  # noqa: F401


def _gap_to(section_present: bool, env):
    """One S5 gap unit bound for IMPROVEMENT.md ## What to Watch For."""
    from core.ingestion import api_v2
    from core.ingestion.sources import s5_metrics
    improvement = env["ddd"] / "IMPROVEMENT.md"
    if not section_present:
        improvement.write_text("# Improvement\n\n## What Worked\n", encoding="utf-8")
    root = api_v2.register_root("metric_window", "week:retry_rate", "0.4", "measured")
    gap = {"kind": "gap", "gap_kind": "process_issue", "scope": "project:P",
           "title": "Retry rate remains high", "what": "Four of ten runs required review retries.",
           "why": "The aggregate retry rate is 0.4.",
           "proposed_next": "Measure the validation gate over the next window.",
           "evidence": ["metric:week:retry_rate"]}
    s5_metrics.extract_metrics("week", {"retry_rate": 0.4}, run_count=10, root_ids=(root,),
                               llm=Extractor([gap]))


def _worker(env, owner):
    from core.ingestion.worker import IngestionWorker
    worker = IngestionWorker(owner, env["workspace"])
    worker._summary_due = datetime.now(timezone.utc) + timedelta(days=1)
    worker._maintenance_due = datetime.now(timezone.utc) + timedelta(days=1)
    return worker


def _owned(env):
    from core.ingestion import api
    from core.ingestion.owner import acquire
    owner = acquire()
    assert owner is not None
    conn = api.connect_rw()
    owner.adopt(conn)
    return owner, conn


def test_a_parked_unit_is_not_ready_and_the_worker_goes_idle(cdf_env):
    _gap_to(False, cdf_env)
    owner, conn = _owned(cdf_env)
    try:
        worker = _worker(cdf_env, owner)
        assert worker.run_once(conn) is True          # judged once: parked on missing_section
        unit = conn.execute("SELECT state,reason,next_attempt_at FROM ingestion_units").fetchone()
        assert (unit["state"], unit["reason"], unit["next_attempt_at"]) == ("deferred", "missing_section", None)
        assert [worker.run_once(conn) for _ in range(3)] == [False, False, False]
    finally:
        conn.close(); owner.release()


def test_an_error_in_one_unit_is_recorded_on_it(cdf_env, monkeypatch):
    _gap_to(True, cdf_env)
    import core.ingestion.pipeline_v2 as pv2
    monkeypatch.setattr(pv2, "run_unit_v2", lambda *a, **k: (_ for _ in ()).throw(KeyError("boom")))
    owner, conn = _owned(cdf_env)
    try:
        assert _worker(cdf_env, owner).run_once(conn) is True       # no exception escapes
        unit = conn.execute("SELECT state,reason,attempts,error_fingerprint FROM ingestion_units").fetchone()
        assert unit["state"] == "deferred" and unit["reason"] == "error:KeyError"
        assert unit["attempts"] == 1 and unit["error_fingerprint"]
        cand = conn.execute("SELECT state FROM ingestion_inbox").fetchone()
        assert cand["state"] == "partial_pending"
    finally:
        conn.close(); owner.release()


def test_a_writer_error_before_any_plan_defers_the_unit(cdf_env, monkeypatch):
    _gap_to(True, cdf_env)
    import core.ingestion.writer as writer
    monkeypatch.setattr(writer, "apply_unit", lambda *a, **k: (_ for _ in ()).throw(OSError("disk")))
    rows = _drain(cdf_env)
    assert rows[-1]["state"] == "deferred" and rows[-1]["reason"] == "error:OSError", rows[-1]


def test_a_retry_after_an_abandoned_plan_writes_the_entry(cdf_env, monkeypatch):
    _gap_to(True, cdf_env)
    import core.ingestion.writer as writer
    real = writer._execute_plan
    calls = []

    def race_once(conn, plan):
        calls.append(plan["op_id"])
        if len(calls) == 1:
            writer._defer(conn, plan, "user_race")
            return "deferred"
        return real(conn, plan)
    monkeypatch.setattr(writer, "_execute_plan", race_once)
    rows = _drain(cdf_env)
    assert rows[-1]["state"] == "deferred" and rows[-1]["next_attempt_at"], rows[-1]
    assert rows[-1]["attempts"] == 1
    from core.ingestion import api
    conn = api.connect_rw()
    conn.execute("UPDATE ingestion_units SET next_attempt_at=?",
                 ((datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(),))
    conn.commit(); conn.close()
    rows = _drain(cdf_env)
    assert rows[-1]["state"] == "written", rows[-1]
    assert "Retry rate remains high" in (cdf_env["ddd"] / "IMPROVEMENT.md").read_text(encoding="utf-8")


def test_one_bad_plan_does_not_stop_recovery(cdf_env, monkeypatch):
    import core.ingestion.writer as writer
    from core.ingestion import api
    conn = api.connect_rw()
    now = datetime.now(timezone.utc).isoformat()
    for op in ("ingestion:bad", "ingestion:good"):
        conn.execute(
            """INSERT INTO ingestion_writes(op_id,op_kind,unit_id,target_path,before_hash,intended_bytes,
               after_hash,anchors_present,anchors_absent,relations,step,owner_epoch,created_at,updated_at)
               VALUES(?, 'ingestion_unit', NULL, '.context/MEMORY.md', '', NULL, '', '[]', '[]', '[]',
               'planned', 0, ?, ?)""", (op, now, now))
    conn.commit()
    seen = []

    def plan_step(c, plan, counts):
        seen.append(plan["op_id"])
        if plan["op_id"] == "ingestion:bad":
            raise ValueError("deterministic plan error")
    monkeypatch.setattr(writer, "_recover_plan", plan_step)
    counts = writer.recover(conn)
    steps = dict(conn.execute("SELECT op_id,step FROM ingestion_writes").fetchall())
    conn.close()
    assert seen == ["ingestion:bad", "ingestion:good"]
    assert steps["ingestion:bad"] == "abandoned" and counts["abandoned"] >= 1


def test_an_invalid_relation_is_skipped_with_an_event(cdf_env):
    import core.ingestion.writer as writer
    from core.ingestion import api
    conn = api.connect_rw()
    plan = {"op_id": "ingestion:x", "relations": [
        {"subject_anchor": "nowhere.md##cand:x", "predicate": "supersedes", "object_anchor": "gone.md##cand:y"}]}
    writer._write_relations(conn, plan)
    conn.commit()
    events = [json.loads(r[0]) for r in conn.execute(
        "SELECT detail FROM ingestion_events WHERE kind='relation_skipped'")]
    conn.close()
    assert events and events[0]["reason"] == "anchor not found"


def test_a_candidate_left_in_progress_is_recomputed(cdf_env):
    _gap_to(True, cdf_env)
    _drain(cdf_env)
    from core.ingestion import api
    from core.ingestion.worker import reaggregate_candidates
    conn = api.connect_rw()
    conn.execute("UPDATE ingestion_inbox SET state='in_progress'")
    conn.commit()
    assert reaggregate_candidates(conn) == 1
    assert conn.execute("SELECT state FROM ingestion_inbox").fetchone()[0] == "done"
    conn.close()


def test_judge_outages_back_off_for_hours_and_do_not_poison_quickly(cdf_env):
    _gap_to(True, cdf_env)
    from core.ingestion import worker as w
    owner, conn = _owned(cdf_env)
    try:
        _worker(cdf_env, owner).run_once(conn)
        unit_id = conn.execute("SELECT unit_id FROM ingestion_units").fetchone()[0]

        def defer(reason, attempts):
            conn.execute("UPDATE ingestion_units SET state='queued',attempts=?,error_fingerprint=NULL "
                         "WHERE unit_id=?", (attempts, unit_id))
            row = conn.execute("SELECT * FROM ingestion_units WHERE unit_id=?", (unit_id,)).fetchone()
            w._defer_or_poison(conn, owner, row, reason, fingerprint="f")
            return conn.execute("SELECT state,next_attempt_at FROM ingestion_units WHERE unit_id=?",
                                (unit_id,)).fetchone()
        state, next_at = defer("judge_unavailable:RuntimeError", 5)
        assert state == "deferred"
        wait = datetime.fromisoformat(next_at) - datetime.now(timezone.utc)
        assert timedelta(minutes=30) < wait <= timedelta(hours=1, seconds=5)
        assert defer("judge_unavailable:RuntimeError", w.INFRA_ATTEMPTS - 1)[0] == "failed_poison"
        assert defer("error:KeyError", 4)[0] == "failed_poison"          # the unit's own failure
        assert defer("ambiguous_project", 4)[0] == "failed_poison"       # deterministic, not an outage
    finally:
        conn.close(); owner.release()


def test_a_request_during_a_failed_last_attempt_is_kept(cdf_env):
    from core.ingestion import api, s2_jobs
    s2_jobs.request("s-2", "session_end")
    conn = api.connect_rw()
    conn.execute("UPDATE ingestion_s2_jobs SET attempts=?", (s2_jobs.MAX_ATTEMPTS - 1,))
    conn.commit()
    job = s2_jobs.claim(conn)
    assert job["attempts"] == s2_jobs.MAX_ATTEMPTS
    s2_jobs.request("s-2", "session_end")                     # arrives while running
    assert s2_jobs.finish(conn, "s-2", error="boom") == "queued"
    assert conn.execute("SELECT attempts FROM ingestion_s2_jobs").fetchone()[0] == 0
    conn.close()


def test_a_review_moves_the_watermark(cdf_env, monkeypatch):
    from core.ingestion import api, s2_jobs, worker
    from core.ingestion.sources import s7_review
    s2_jobs.request("s-3", "session_end")
    monkeypatch.setattr(s7_review, "review_session",
                        lambda *a, **k: {"status": "reviewed", "reviewed_to": "m9", "units": 1})
    conn = api.connect_rw()
    job = s2_jobs.claim(conn)
    worker.run_session_job(conn, job, runner=object())
    assert conn.execute("SELECT reviewed_to FROM ingestion_s2_jobs").fetchone()[0] == "m9"
    conn.close()


def test_the_worker_loop_survives_a_pass_that_raises(cdf_env, monkeypatch):
    from core.ingestion import worker as w
    owner, conn = _owned(cdf_env)
    conn.close()
    try:
        worker = _worker(cdf_env, owner)
        monkeypatch.setattr(w, "LOOP_BACKOFF_SECONDS", 0)
        passes = []

        def run_once(c):
            passes.append(1)
            if len(passes) == 1:
                raise RuntimeError("database is locked")
            worker.stop()
            return False
        monkeypatch.setattr(worker, "run_once", run_once)
        monkeypatch.setattr(worker, "_maintain", lambda c: None)
        asyncio.run(worker.run())
        assert len(passes) == 2
    finally:
        owner.release()


def test_machine_errors_back_off_and_error_poisons_get_a_fresh_start_at_startup(cdf_env):
    """A read-only or locked store is the machine's failure, not the unit's: it backs
    off like an outage instead of poisoning after three identical errors. A unit an
    error did poison is queued again at the next startup, when the fix may be in."""
    import sqlite3
    _gap_to(True, cdf_env)
    from core.ingestion import worker as w
    owner, conn = _owned(cdf_env)
    try:
        _worker(cdf_env, owner).run_once(conn)
        unit_id = conn.execute("SELECT unit_id FROM ingestion_units").fetchone()[0]
        conn.execute("DELETE FROM ingestion_writes WHERE unit_id=?", (unit_id,))  # no live plan

        def fail(exc, attempts):
            conn.execute("UPDATE ingestion_units SET state='queued',attempts=?,error_fingerprint=? "
                         "WHERE unit_id=?", (attempts, w.error_fingerprint(exc), unit_id))
            w._unit_failed(conn, owner, unit_id, exc)
            return conn.execute("SELECT state FROM ingestion_units WHERE unit_id=?",
                                (unit_id,)).fetchone()[0]
        assert fail(OSError(30, "Read-only file system"), 4) == "deferred"
        assert fail(sqlite3.OperationalError("database is locked"), 4) == "deferred"
        assert fail(KeyError("x"), 4) == "failed_poison"      # the code's own error: quick

        _worker(cdf_env, owner)._maintain(conn)                 # the daily pass leaves it
        assert conn.execute("SELECT state FROM ingestion_units WHERE unit_id=?",
                            (unit_id,)).fetchone()[0] == "failed_poison"
        _worker(cdf_env, owner)._maintain(conn, startup=True)
        state, attempts = conn.execute("SELECT state,attempts FROM ingestion_units WHERE unit_id=?",
                                       (unit_id,)).fetchone()
        assert (state, attempts) == ("queued", 0)
        assert not conn.execute("SELECT 1 FROM ingestion_events WHERE kind='poison' AND resolved=0"
                                ).fetchone()
    finally:
        conn.close(); owner.release()
