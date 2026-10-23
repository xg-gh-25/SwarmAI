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
