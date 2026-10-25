"""Commit 3 (r6 §2.2): one read-decide-write operation at a time per run.

Methodology: the real operation lock, store and CLI (v1 rig); threads stand in for a
second process. Invariants:
- a second operation on the same run waits for the first and times out as RunBusy;
  another run is never blocked; the lock is re-entrant in one thread;
- ``ordinal=NEXT`` is counted under the store lock, so concurrent writers of the same
  kind never collide (the old ``len(read_events())+1`` raced);
- every mutating CLI call holds the lock; ``status`` does not;
- projections end with the source they were rendered from.
"""
from __future__ import annotations

import json
import threading
import time

import pytest

from core.run_protocol.oplock import RunBusy, operation
from tests.pipeline_v1_rig import Rig

pytestmark = pytest.mark.timeout(300)


@pytest.fixture
def rig(tmp_path, monkeypatch):
    made: list[Rig] = []

    def make(profile: str = "direct", **kwargs) -> Rig:
        r = Rig(tmp_path / f"case{len(made)}", monkeypatch, profile=profile, **kwargs)
        made.append(r)
        return r
    yield make
    for r in made:
        r.close()


def test_a_second_operation_waits_and_another_run_does_not(tmp_path):
    entered = threading.Event()
    release = threading.Event()

    def holder():
        with operation(tmp_path, "run_a"):
            entered.set()
            release.wait(5)
    t = threading.Thread(target=holder)
    t.start()
    entered.wait(5)
    started = time.monotonic()
    with pytest.raises(RunBusy):
        with operation(tmp_path, "run_a", timeout=0.3):
            pass
    assert time.monotonic() - started >= 0.3
    with operation(tmp_path, "run_b", timeout=0.3):        # another run: not blocked
        pass
    release.set()
    t.join(5)
    with operation(tmp_path, "run_a", timeout=1):
        with operation(tmp_path, "run_a", timeout=0.1):    # re-entrant in one thread
            pass


def test_next_ordinals_are_counted_under_the_lock(rig):
    from core.run_protocol.store import NEXT
    r = rig()
    r.create()
    store = r.engine().store
    writer = store.authority.writer("cli")
    errors: list[Exception] = []
    barrier = threading.Barrier(4)

    def write(i):
        barrier.wait()
        try:
            store.append_new(writer=writer, kind="integrity", at="2026-10-23T00:00:00Z",
                             ordinal=NEXT, payload={"conflict_id": f"c{i}",
                             "conflict_type": "probe_changed_tree", "first_hash": "a" * 64,
                             "new_hash": "b" * 64})
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)
    threads = [threading.Thread(target=write, args=(i,)) for i in range(4)]
    [t.start() for t in threads]
    [t.join(10) for t in threads]
    assert not errors
    ordinals = [e["ordinal"] for e in store.read_events() if e["kind"] == "integrity"]
    assert len(ordinals) == len(set(ordinals)) == 4


def test_a_mutating_cli_call_waits_for_the_run_and_status_does_not(rig):
    r = rig()
    r.create()
    audit = r.data / "state" / "pipeline_agent_audit"
    entered, release = threading.Event(), threading.Event()

    def holder():
        with operation(audit, r.run_id):
            entered.set()
            release.wait(10)
    t = threading.Thread(target=holder)
    t.start()
    entered.wait(5)
    started = time.monotonic()
    assert r.v1("status").get("ok") is True                 # read-only: not blocked
    assert time.monotonic() - started < 5
    started = time.monotonic()
    out = r.v1("push-status")                                # read-only too: not blocked
    assert time.monotonic() - started < 5, out
    threading.Timer(1.5, release.set).start()
    started = time.monotonic()
    out = r.v1("stage-complete")                             # mutating: waits its turn
    assert time.monotonic() - started >= 1.4, out
    assert "next" in out, out
    t.join(5)


def test_projections_name_the_source_they_were_rendered_from(rig):
    r = rig("full")
    r.create()
    r.drive(until="think")
    text = (r.engine().run_dir / "REQUIREMENTS.md").read_text()
    assert text.rstrip().splitlines()[-1].startswith("<!-- rendered from requirements.json sha256:")


def test_an_operation_refuses_a_run_id_that_is_not_one(tmp_path):
    from core.run_protocol.identifiers import IdentifierError
    with pytest.raises(IdentifierError):
        with operation(tmp_path / "audit", "../escape"):
            pass
    assert not (tmp_path / "escape").exists() and not (tmp_path / "audit").exists()


def test_an_answer_and_its_findings_are_one_write(rig, monkeypatch):
    from tests.test_pipeline_v1_conformance_k1 import _at_review_with_finding
    r = rig()
    _at_review_with_finding(r)
    events = r.engine().store.read_events()
    answer = next(e for e in events if e["kind"] == "answer" and e["payload"]["n_findings"])
    occ = [e for e in events if e["kind"] == "finding_occ"
           and e["payload"]["answer_event_id"] == answer["event_id"]]
    assert len(occ) == answer["payload"]["n_findings"]
    # the answer and its occurrences are contiguous: written by one append
    at = events.index(answer)
    assert events[at + 1: at + 1 + len(occ)] == occ


def test_a_parsed_answer_missing_a_finding_does_not_count(rig):
    from core.run_protocol import checks
    from tests.test_pipeline_v1_conformance_k1 import _at_review_with_finding
    r = rig()
    _at_review_with_finding(r)
    events = r.engine().store.read_events()
    answer = next(e for e in events if e["kind"] == "answer" and e["payload"]["n_findings"])
    ctx = type("C", (), {"events": [e for e in events if not (
        e["kind"] == "finding_occ" and e["payload"]["answer_event_id"] == answer["event_id"])]})
    assert answer not in checks._answers(ctx)
    assert answer in checks._answers(type("C", (), {"events": events}))


def test_a_trigger_and_its_transition_reach_the_log_in_one_write(tmp_path, monkeypatch):
    """Re-audit ca91762f HIGH: abandon (or stage_complete, user_stop, ...) was fsynced,
    then its due transition appended in a second write; a crash between them left a
    chain ending in an expected transition that a plain projection() rejects. Both
    are now one write + one fsync."""
    from core.run_protocol import store as store_mod
    from tests.pipeline_v1_rig import Rig
    r = Rig(tmp_path, monkeypatch)
    try:
        r.create()
        writes: list[list[str]] = []
        real = store_mod.AuditStore._write_events

        def spy(self, events):
            writes.append([e.get("kind") for e in events])
            return real(self, events)
        monkeypatch.setattr(store_mod.AuditStore, "_write_events", spy)
        engine = r.engine()
        engine.abandon(reason="test")
        assert ["abandon", "transition"] in writes, writes
        assert engine.projection()["status"] == "abandoned"
    finally:
        r.close()


def test_a_daemon_mutation_waits_for_the_runs_operation_lock(tmp_path, monkeypatch):
    """Re-audit ca91762f HIGH: dashboard Cancel (engine.abandon) ran beside a CLI
    landing without the per-run operation lock. Every public engine mutation now
    takes it (re-entrant for the CLI, which already holds it)."""
    import threading
    from core.run_protocol import engine as eng
    from core.run_protocol import oplock
    from tests.pipeline_v1_rig import Rig
    r = Rig(tmp_path, monkeypatch)
    try:
        r.create()
        engine = r.engine()
        monkeypatch.setattr(eng, "ENGINE_LOCK_TIMEOUT", 0.3)
        held, release = threading.Event(), threading.Event()

        def other_process_operation():
            with oplock.operation(engine.audit_root, engine.run_id):
                held.set()
                release.wait(10)
        t = threading.Thread(target=other_process_operation)
        t.start()
        held.wait(5)
        try:
            with pytest.raises(oplock.RunBusy):
                engine.abandon(reason="cancel while landing")
        finally:
            release.set()
            t.join(10)
        assert engine.projection()["status"] != "abandoned"
        with oplock.operation(engine.audit_root, engine.run_id):   # re-entrant
            engine.abandon(reason="now")
        assert engine.projection()["status"] == "abandoned"
    finally:
        r.close()



def test_a_stale_projection_is_rebuilt_from_the_signed_log(tmp_path, monkeypatch):
    """Re-audit ca91762f MED: a signed land event is durable, then the process died
    before run.json was rewritten; dashboard, Need You and the sweeper kept reading
    the old state. run.json now names the log tail it was built from, and the startup
    repair rebuilds every projection whose tail no longer matches."""
    from core.run_protocol import engine as eng
    from tests.pipeline_v1_rig import Rig
    r = Rig(tmp_path, monkeypatch)
    try:
        r.create()
        rj = r.engine().run_dir / "run.json"
        good = json.loads(rj.read_text())
        assert good["projection_of"]["event_count"] == len(r.events())
        rj.write_text(json.dumps({**good, "status": "completed",
                                  "projection_of": {"event_count": 1, "tail": "0" * 64}}))
        assert eng.repair_stale_projections(r.data, r.ws) == [r.run_id]
        fixed = json.loads(rj.read_text())
        assert fixed["status"] == "active" and fixed["projection_of"] == good["projection_of"]
        assert eng.repair_stale_projections(r.data, r.ws) == []
    finally:
        r.close()


def test_a_batch_torn_after_its_first_line_counts_as_never_written(tmp_path, monkeypatch):
    """Re-audit 84b72ec1 HIGH: one write can tear after the trigger's newline, leaving
    the trigger without its due transition. The batch's start is recorded first, so
    readers ignore everything past it and the next locked writer cuts it off."""
    from core.run_protocol import store as store_mod
    r = Rig(tmp_path, monkeypatch)
    try:
        r.create()
        engine = r.engine()
        store = engine.store
        before = store.paths.events.read_bytes()
        n_before = len(store.read_events())

        real = store_mod.AuditStore._write_events

        def torn(self, events):              # crash: intent + only the first line
            self._intent_path().write_text(f"{self.paths.events.stat().st_size}\n")
            with open(self.paths.events, "ab") as handle:
                handle.write(store_mod.ev.encode_event(events[0]))
            raise OSError("simulated crash mid-batch")
        monkeypatch.setattr(store_mod.AuditStore, "_write_events", torn)
        with pytest.raises(Exception):
            engine.abandon(reason="test")
        monkeypatch.setattr(store_mod.AuditStore, "_write_events", real)
        assert len(store.paths.events.read_bytes()) > len(before)   # the line IS on disk
        assert len(r.engine().store.read_events()) == n_before      # but does not count
        assert r.engine().projection()["status"] != "abandoned"
        r.engine().abandon(reason="again")                          # next writer cuts it
        assert r.engine().projection()["status"] == "abandoned"
        assert not r.engine().store._intent_path().exists()
    finally:
        r.close()


def test_every_public_engine_mutator_takes_the_operation_lock():
    """Re-audit 84b72ec1: mutators without the lock interleaved with the CLI, and the
    landing lock taken before the operation lock in some paths and after it in others
    could deadlock. Every public method that appends holds the operation lock first."""
    from core.run_protocol.engine import RunEngine
    mutators = ["gate0_start", "request_decision", "prepare_land_request",
                "execute_land_request", "normal_land", "prepare_reflect", "note_transcript",
                "measure_usage", "abandon", "restore", "resume", "stage_complete",
                "review_start", "review_address", "review_proof", "answer_decision",
                "reflect_complete", "finish_not_landed", "ensure_worktrees",
                "transfer_owner", "record_stop"]
    missing = [m for m in mutators if not hasattr(getattr(RunEngine, m), "__wrapped__")]
    assert not missing, missing
