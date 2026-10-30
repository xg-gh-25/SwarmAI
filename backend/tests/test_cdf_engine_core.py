"""CDF engine storage, ownership, scheduling, cleanup, wake, and CLI tests.

Tests use per-case ``SWARM_DATA_DIR`` stores, fake clocks, and direct API calls.
Safety properties cover epoch fencing, additive cleanup, credential checks, and a
CLI that only imports enqueue/read APIs.
"""
from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import stat
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


def _remember(text: str = "Always verify the invariant before changing code.") -> dict:
    return {"kind": "remember", "text": text}


def _envelope() -> dict:
    return {"source_event_ids": ["evt-1"], "evidence": "user_explicit", "tainted": False}


class TestStore:
    def test_create_pragmas_and_read_only(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SWARM_DATA_DIR", str(tmp_path))
        from core.ingestion.store import connect_ro, connect_rw, ingestion_db_path

        with pytest.raises(FileNotFoundError):
            connect_ro()
        assert not ingestion_db_path().exists()
        conn = connect_rw()
        assert conn.execute("PRAGMA auto_vacuum").fetchone()[0] == 2
        assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        assert conn.execute("PRAGMA synchronous").fetchone()[0] == 1
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 9
        conn.close()
        ro = connect_ro()
        assert ro.execute("PRAGMA query_only").fetchone()[0] == 1
        with pytest.raises(sqlite3.OperationalError):
            ro.execute("CREATE TABLE forbidden(x)")
        ro.close()

    def test_failed_creation_removes_half_built_file(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SWARM_DATA_DIR", str(tmp_path))
        import core.ingestion.store as store

        monkeypatch.setattr(store, "SCHEMA_DDL", "CREATE TABLE ok(x); INVALID SQL;")
        with pytest.raises(sqlite3.DatabaseError):
            store.connect_rw()
        assert not store.ingestion_db_path().exists()

    def test_corrupt_database_is_isolated_and_event_recorded(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SWARM_DATA_DIR", str(tmp_path))
        from core.ingestion.store import connect_rw, ingestion_db_path

        ingestion_db_path().write_bytes(b"not sqlite")
        conn = connect_rw()
        event = conn.execute("SELECT kind FROM ingestion_events").fetchone()
        conn.close()
        assert event[0] == "restored_orphan"
        assert list(tmp_path.glob("ingestion.db.corrupt-*"))


class TestEnqueue:
    def test_idempotency_collision_and_validation(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SWARM_DATA_DIR", str(tmp_path))
        monkeypatch.setattr("core.ingestion.wake.wake", lambda _lane=None: None)
        from core.ingestion.inbox import enqueue

        first = enqueue("remember", "message-1", "lesson", _remember(), envelope=_envelope())
        duplicate = enqueue("remember", "message-1", "lesson", _remember(), envelope=_envelope())
        collision = enqueue("remember", "message-1", "lesson", _remember("Different"), envelope=_envelope())
        wrong_kind = enqueue("remember", "message-2", "lesson", {"kind": "backflow"}, envelope=_envelope())
        assert first.status == "queued"
        assert duplicate.status == "duplicate" and duplicate.candidate_id == first.candidate_id
        assert collision.status == "id_collision" and collision.candidate_id == first.candidate_id
        assert wrong_kind.status == "rejected"

    def test_project_candidates_are_resolved(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SWARM_DATA_DIR", str(tmp_path))
        monkeypatch.setattr("core.ingestion.wake.wake", lambda _lane=None: None)
        from core.ingestion.inbox import enqueue
        from core.ingestion.store import connect_ro

        result = enqueue(
            "remember", "message-2", "lesson", _remember(), envelope=_envelope(),
            project_snapshot={"explicit_project": "Alpha"},
        )
        conn = connect_ro()
        row = conn.execute("SELECT project_candidates FROM ingestion_inbox WHERE id=?", (result.candidate_id,)).fetchone()
        conn.close()
        assert json.loads(row[0]) == ["Alpha"]

    def test_busy_returns_within_bound(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SWARM_DATA_DIR", str(tmp_path))
        from core.ingestion.inbox import enqueue
        from core.ingestion.store import connect_rw

        locker = connect_rw()
        locker.execute("BEGIN EXCLUSIVE")
        started = time.monotonic()
        try:
            result = enqueue("remember", "busy", "slot", _remember(), envelope=_envelope())
        finally:
            locker.execute("ROLLBACK")
            locker.close()
        assert result.status == "busy"
        assert time.monotonic() - started < 1.7


class TestOwner:
    def test_single_acquirer_epoch_fence_and_adopt(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SWARM_DATA_DIR", str(tmp_path))
        from core.ingestion.owner import acquire
        from core.ingestion.store import connect_rw

        first = acquire()
        assert first is not None
        assert acquire() is None
        conn = connect_rw()
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            """INSERT INTO ingestion_inbox(id,idem_key,source,source_ref,lane,payload,payload_schema,
               content_hash,envelope,project_snapshot,project_candidates,created_at)
               VALUES('c','k','remember','r','immediate','{}','v','h','{}','{}','[]',?)""", (now,),
        )
        conn.execute(
            "INSERT INTO ingestion_units(unit_id,candidate_id,claim_group,owner_epoch,updated_at) VALUES('u','c','{}',0,?)",
            (now,),
        )
        first.adopt(conn)
        assert conn.execute("SELECT owner_epoch FROM ingestion_units").fetchone()[0] == first.epoch
        assert not conn.execute(
            "UPDATE ingestion_units SET state='judged' WHERE unit_id='u' AND state='queued' AND owner_epoch=0"
        ).rowcount
        stale = type(first)(first._lock_file, first.epoch - 1)
        assert not stale.claim_unit_step(conn, "u", "queued", "judged")
        assert first.claim_unit_step(conn, "u", "queued", "judged")
        conn.close()
        first.release()

    def test_repeated_error_fingerprint_poison_and_aggregation(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SWARM_DATA_DIR", str(tmp_path))
        from core.ingestion.owner import acquire
        from core.ingestion.store import connect_rw
        from core.ingestion.worker import _defer_or_poison, aggregate_candidate_state

        owner = acquire()
        assert owner is not None
        conn = connect_rw()
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            """INSERT INTO ingestion_inbox(id,idem_key,source,source_ref,lane,payload,payload_schema,
               content_hash,envelope,project_snapshot,project_candidates,created_at,owner_epoch)
               VALUES('p','p','remember','r','immediate','{}','v','h','{}','{}','[]',?,?)""",
            (now, owner.epoch),
        )
        conn.execute(
            "INSERT INTO ingestion_units(unit_id,candidate_id,claim_group,owner_epoch,updated_at) VALUES('p.u','p','{}',?,?)",
            (owner.epoch, now),
        )
        for attempt in range(3):
            unit = conn.execute("SELECT * FROM ingestion_units WHERE unit_id='p.u'").fetchone()
            if unit["state"] == "deferred":
                assert owner.claim_unit_step(conn, "p.u", "deferred", "queued")
                unit = conn.execute("SELECT * FROM ingestion_units WHERE unit_id='p.u'").fetchone()
            _defer_or_poison(conn, owner, unit, "boom", fingerprint="same")
        assert conn.execute("SELECT state FROM ingestion_units").fetchone()[0] == "failed_poison"
        assert aggregate_candidate_state(["written", "rejected"])[0] == "done"
        assert aggregate_candidate_state(["written", "deferred"])[0] == "partial_pending"
        assert aggregate_candidate_state(["judged", "deferred"])[0] == "in_progress"
        conn.close()
        owner.release()

    def test_worker_handle_joins_before_owner_release(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SWARM_DATA_DIR", str(tmp_path / "data"))
        from core.ingestion.owner import acquire
        from core.ingestion.worker import WorkerHandle

        owner = acquire()
        assert owner is not None
        handle = WorkerHandle(owner, tmp_path / "workspace")
        handle.start()
        time.sleep(0.05)
        assert acquire() is None
        handle.stop_and_join()
        replacement = acquire()
        assert replacement is not None
        replacement.release()


class TestScheduling:
    def test_fairness_burst_and_overdue(self):
        from core.ingestion.worker import WeightedLaneScheduler

        scheduler = WeightedLaneScheduler()
        now = datetime.now(timezone.utc)
        choices = [scheduler.choose({"immediate", "session"}, {}, now) for _ in range(15)]
        assert "session" in choices
        assert max(len(list(group)) for value, group in __import__("itertools").groupby(choices) if value == "immediate") <= 10
        assert scheduler.choose(
            {"immediate", "maintenance"}, {"maintenance": now - timedelta(hours=37)}, now
        ) == "maintenance"

    @pytest.mark.parametrize("pressure", ["warning", "critical"])
    def test_admission_pressure(self, monkeypatch, pressure):
        from core.ingestion.worker import ingestion_admission
        from core.resource_monitor import resource_monitor

        memory = type("Memory", (), {"pressure_level": pressure})()
        monkeypatch.setattr(resource_monitor, "system_memory", lambda: memory)
        monkeypatch.setattr("core.session_unit.is_any_chat_streaming", lambda: False)
        assert ingestion_admission("immediate").run
        assert not ingestion_admission("session").run
        floor = ingestion_admission("maintenance", maintenance_floor=True)
        assert floor.run and not floor.llm

    def test_streaming_holds_no_lane(self, monkeypatch):
        """A streaming chat holds no lane (2026-10-29-cdf-chat-coexistence.md)."""
        from core.ingestion.worker import ingestion_admission
        from core.resource_monitor import resource_monitor

        memory = type("Memory", (), {"pressure_level": "ok"})()
        monkeypatch.setattr(resource_monitor, "system_memory", lambda: memory)
        monkeypatch.setattr("core.session_unit.is_any_chat_streaming", lambda: True)
        assert ingestion_admission("immediate").llm
        assert ingestion_admission("run").run
        assert ingestion_admission("session").run


class TestCleanup:
    def _insert(self, conn, candidate: str, created: datetime):
        value = created.isoformat()
        conn.execute(
            """INSERT INTO ingestion_inbox(id,idem_key,source,source_ref,lane,payload,payload_schema,
               content_hash,envelope,project_snapshot,project_candidates,created_at)
               VALUES(?,?,?,?,?,'{}','v','h','{}','{}','[]',?)""",
            (candidate, candidate, "remember", candidate, "immediate", value),
        )
        conn.execute(
            "INSERT INTO ingestion_units(unit_id,candidate_id,claim_group,owner_epoch,updated_at) VALUES(?,?, '{}',1,?)",
            (candidate + ".u", candidate, value),
        )

    def test_two_midnight_sweeps_count_once(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SWARM_DATA_DIR", str(tmp_path))
        from core.ingestion.store import connect_rw
        from core.ingestion.worker import cleanup_expired

        conn = connect_rw()
        now = datetime(2026, 10, 27, 0, 1, tzinfo=timezone.utc)
        self._insert(conn, "old", now - timedelta(days=31))
        self._insert(conn, "kept", now - timedelta(days=29))
        assert cleanup_expired(conn, now=now, vacuum=False) == 1
        assert cleanup_expired(conn, now=now + timedelta(days=1), vacuum=False) == 0
        stats = conn.execute("SELECT SUM(candidates), SUM(units) FROM ingestion_daily_stats").fetchone()
        assert tuple(stats) == (1, 1)
        conn.close()

    def test_cleanup_upsert_adds_across_chunks(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SWARM_DATA_DIR", str(tmp_path))
        from core.ingestion.store import connect_rw
        from core.ingestion.worker import cleanup_expired

        conn = connect_rw()
        now = datetime(2026, 10, 27, tzinfo=timezone.utc)
        created = now - timedelta(days=31)
        self._insert(conn, "old-a", created)
        self._insert(conn, "old-b", created)
        assert cleanup_expired(conn, now=now, chunk_size=1, vacuum=False) == 2
        stats = conn.execute("SELECT candidates, units FROM ingestion_daily_stats").fetchone()
        assert tuple(stats) == (2, 2)
        conn.close()

    def test_rollback_does_not_count(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SWARM_DATA_DIR", str(tmp_path))
        import core.ingestion.worker as worker
        from core.ingestion.store import connect_rw

        conn = connect_rw()
        now = datetime(2026, 10, 27, tzinfo=timezone.utc)
        self._insert(conn, "old", now - timedelta(days=31))
        conn.execute("CREATE TRIGGER stop_delete BEFORE DELETE ON ingestion_inbox BEGIN SELECT RAISE(ABORT,'stop'); END")
        with pytest.raises(sqlite3.DatabaseError):
            worker.cleanup_expired(conn, now=now, vacuum=False)
        assert conn.execute("SELECT COUNT(*) FROM ingestion_daily_stats").fetchone()[0] == 0
        conn.close()



class TestBackupAndSafety:
    def test_ingestion_store_online_backup_and_restore(self, tmp_path):
        from core.backup_manager import _sqlite_backup_store, _sqlite_restore_store

        source = tmp_path / "ingestion.db"
        conn = sqlite3.connect(source)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("CREATE TABLE audit(value TEXT)")
        conn.execute("INSERT INTO audit VALUES ('kept')")
        conn.commit()
        snapshot = tmp_path / "backup" / "ingestion.db"
        assert _sqlite_backup_store(source, snapshot)
        restored = tmp_path / "restored" / "ingestion.db"
        assert _sqlite_restore_store(snapshot, restored)
        assert sqlite3.connect(restored).execute("SELECT value FROM audit").fetchone()[0] == "kept"
        conn.close()

    def test_ingestion_store_is_irreplaceable(self):
        from core.data_safety import StoreClass, classify_store

        assert classify_store(Path("/any/data/ingestion.db")) is StoreClass.IRREPLACEABLE

class TestWakeAndCli:
    def test_wake_credential_and_mode(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SWARM_DATA_DIR", str(tmp_path))
        from core.ingestion.wake import authenticate, install_owner_event, uninstall_owner_event

        loop = asyncio.new_event_loop()
        event = asyncio.Event()
        credential = install_owner_event(event, loop)
        try:
            assert authenticate("127.0.0.1", credential)
            assert authenticate("::1", credential)
            assert not authenticate("127.0.0.1", "wrong")
            assert not authenticate("10.0.0.1", credential)
            assert stat.S_IMODE(os.stat(tmp_path / "state" / "ingestion_wake.key").st_mode) == 0o600
        finally:
            uninstall_owner_event()
            loop.close()

    def test_cli_wait_never_imports_worker(self, tmp_path, monkeypatch, capsys):
        """The ingest CLI only enqueues (S1) and polls; it never runs the worker."""
        monkeypatch.setenv("SWARM_DATA_DIR", str(tmp_path))
        monkeypatch.setenv("SWARM_APP_SESSION_ID", "s1")
        monkeypatch.setenv("SWARM_LAST_USER_EVENT_ID", "input:e1")
        from core.ingestion.api import EnqueueResult
        import core.ingestion.sources.s1_user as s1
        import desktop_main

        monkeypatch.setattr(s1, "submit_units", lambda *_a, **_k: [EnqueueResult("queued", "cand-1")])
        unit = {"kind": "cognition", "type": "guideline", "scope": "agent", "title": "t",
                "what": "Verify the invariant before changing code.", "evidence": ["event:input:e1"]}
        assert desktop_main.run_ingest([
            "submit", "--source", "S1", "--ref", "m", "--evidence", "input:e1",
            "--payload-json", json.dumps(unit),
        ]) == 0
        import inspect
        source = inspect.getsource(desktop_main.run_ingest)
        assert "core.ingestion.worker" not in source
        assert "process_candidate" not in source
        assert '"status": "queued"' in capsys.readouterr().out

    def test_cli_wake_signals_and_reports_without_processing(self, tmp_path, monkeypatch, capsys):
        """`ingest wake` (s_memory-distill's trigger) nudges the owner worker once and
        prints the Flow overview; with no store yet it says so instead of failing."""
        monkeypatch.setenv("SWARM_DATA_DIR", str(tmp_path))
        import core.ingestion.queries as queries
        import core.ingestion.wake as wake_mod
        import desktop_main

        woken = []
        monkeypatch.setattr(wake_mod, "wake", lambda lane=None: woken.append(lane))
        monkeypatch.setattr(queries, "flow_overview", lambda window: {"window": window, "total_units": 3})
        assert desktop_main.run_ingest(["wake"]) == 0
        out = json.loads(capsys.readouterr().out)
        assert woken == [None] and out == {"status": "woken", "flow": {"window": "24h", "total_units": 3}}

        def missing(window):
            raise FileNotFoundError(window)
        monkeypatch.setattr(queries, "flow_overview", missing)
        assert desktop_main.run_ingest(["wake"]) == 0
        assert json.loads(capsys.readouterr().out)["flow"] is None

def test_in_flight_write_is_never_rejudged(tmp_path, monkeypatch):
    """A unit left judged/writing by a crash belongs to writer.recover(); the worker skips it."""
    import json as _json
    monkeypatch.setenv("SWARM_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SWARM_WORKSPACE", str(tmp_path / "ws"))
    from core.ingestion import api, worker
    from core.ingestion.owner import acquire
    res = api.enqueue("remember", "m1", "0", {"kind": "remember", "text": "Use Chinese."},
                      envelope={"source_event_ids": ["e"], "evidence": "user_explicit", "tainted": False})
    owner = acquire()
    try:
        conn = api.connect_rw()
        owner.adopt(conn)
        row = conn.execute("SELECT * FROM ingestion_inbox WHERE id=?", (res.candidate_id,)).fetchone()
        calls = []
        monkeypatch.setattr(worker.pipeline, "run_unit", lambda *a, **k: calls.append(1))
        # seed the unit as a crashed in-flight write, then process
        cand = worker._candidate_from_row(row)
        conn.execute("BEGIN IMMEDIATE")
        worker._upsert_units(conn, cand, owner.epoch)
        conn.execute("UPDATE ingestion_units SET state='writing' WHERE candidate_id=?", (res.candidate_id,))
        conn.execute("COMMIT")
        worker.process_candidate(conn, owner, row, tmp_path / "ws", judge=None)
        assert calls == []
        assert {r[0] for r in conn.execute("SELECT state FROM ingestion_units")} == {"writing"}
        conn.close()
    finally:
        owner.release()


def test_cleanup_skips_candidate_with_partially_applied_write(tmp_path, monkeypatch):
    monkeypatch.setenv("SWARM_DATA_DIR", str(tmp_path))
    from core.ingestion.store import connect_rw
    from core.ingestion.worker import cleanup_expired

    conn = connect_rw()
    now = datetime(2026, 10, 27, tzinfo=timezone.utc)
    created = (now - timedelta(days=31)).isoformat()
    conn.execute(
        """INSERT INTO ingestion_inbox(id,idem_key,source,source_ref,lane,payload,payload_schema,
           content_hash,envelope,project_snapshot,project_candidates,created_at)
           VALUES('old-write','old-write','remember','r','immediate','{}','v','h','{}','{}','[]',?)""",
        (created,),
    )
    conn.execute(
        "INSERT INTO ingestion_units(unit_id,candidate_id,claim_group,owner_epoch,updated_at) "
        "VALUES('old-write.u','old-write','{}',1,?)", (created,),
    )
    conn.execute(
        """INSERT INTO ingestion_writes(op_id,op_kind,unit_id,target_path,anchors_present,
           anchors_absent,relations,step,created_at,updated_at)
           VALUES('op','ingestion_unit','old-write.u','.context/MEMORY.md','[]','[]','[]',
                  'target_applied',?,?)""", (created, created),
    )
    assert cleanup_expired(conn, now=now, vacuum=False) == 0
    assert conn.execute("SELECT COUNT(*) FROM ingestion_inbox").fetchone()[0] == 1
    conn.execute("UPDATE ingestion_writes SET step='done' WHERE op_id='op'")
    assert cleanup_expired(conn, now=now, vacuum=False) == 1
    conn.close()


def test_unknown_domain_parking_does_not_consume_retry(tmp_path, monkeypatch):
    monkeypatch.setenv("SWARM_DATA_DIR", str(tmp_path))
    from core.ingestion.owner import acquire
    from core.ingestion.store import connect_rw
    from core.ingestion.worker import _defer_or_poison

    owner = acquire()
    assert owner is not None
    conn = connect_rw()
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """INSERT INTO ingestion_inbox(id,idem_key,source,source_ref,lane,payload,payload_schema,
           content_hash,envelope,project_snapshot,project_candidates,created_at,owner_epoch)
           VALUES('park','park','remember','r','immediate','{}','v','h','{}','{}','[]',?,?)""",
        (now, owner.epoch),
    )
    conn.execute(
        "INSERT INTO ingestion_units(unit_id,candidate_id,claim_group,owner_epoch,updated_at) "
        "VALUES('park.u','park','{}',?,?)", (owner.epoch, now),
    )
    unit = conn.execute("SELECT * FROM ingestion_units WHERE unit_id='park.u'").fetchone()
    _defer_or_poison(conn, owner, unit, "unknown_domain")
    state = conn.execute("SELECT state,attempts,next_attempt_at FROM ingestion_units").fetchone()
    assert tuple(state) == ("deferred", 0, None)
    conn.close(); owner.release()
