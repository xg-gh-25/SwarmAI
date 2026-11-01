"""A stage attempt a stopped process left half recorded no longer blocks the run.

Before: a process killed between a landing guard's check and the end of the attempt left
the attempt open, and every later ``land`` failed with "previous stage_attempt is
incomplete" forever. Now the next operation that opens an attempt closes it first
(``documents.close_interrupted_attempt``: the missing checks get ``cause: interrupted``)
and the fold does not count it as one of the stage's attempts.

Through real runs (``tests/pipeline_v1_rig.py``); the dead process is played by writing
its events with the run's own store, exactly as it would have.
"""
from __future__ import annotations

import pytest

from tests.pipeline_v1_rig import SWARM, Rig

pytestmark = pytest.mark.timeout(300)
REFLECT = {"delivered": [], "decisions": [], "corrections": [], "practices": [], "gaps": []}


@pytest.fixture
def rig(tmp_path, monkeypatch):
    r = Rig(tmp_path / "case", monkeypatch, profile="direct")
    yield r
    if r.run_id:
        assert r.v1("status").get("ok") is True, "the event log no longer folds"
    r.close()


def _dead_attempt(r: Rig, stage: str, check_ids: list[str], recorded: dict[str, bool]) -> None:
    """What a process killed mid-attempt leaves: the attempt and some of its checks."""
    from core.run_protocol.documents import close_interrupted_attempt
    from core.run_protocol.engine import _now
    engine = r.engine()
    close_interrupted_attempt(engine.store, at=_now())   # as the dead process did first
    opened, _ = engine.store.append_new(
        writer=engine.authority.writer("cli"), kind="stage_attempt", at=_now(),
        stage=stage, attempt=engine._next_attempt(stage), payload={"check_ids": check_ids})
    for check_id, passed in recorded.items():
        engine.store.append_new(
            writer=engine.authority.writer("cli"), kind="check", at=_now(),
            stage=stage, attempt=opened["attempt"],
            payload={"stage_attempt_event_id": opened["event_id"], "check_id": check_id,
                     "passed": passed, "detail": "x"})


def test_a_landing_killed_after_its_first_guard_lands_on_the_next_land(rig):
    r = rig
    r.create()
    r.drive(until="deliver")
    r.v1("reflect-prepare", "--artifact", r.artifact("reflect", REFLECT))
    for _ in range(3):                          # three dead landings count for nothing
        _dead_attempt(r, "land", ["DV-1", "LG-1", "LG-3", "LG-4"], {"LG-1": True})
    out = r.v1("land", "--message", "fix: swap the operands" + SWARM)
    assert out.get("ok") is True, out
    state = r.state()
    assert state.get("landing") == "landed" or state["status"] in ("reflecting", "completed")
    causes = [e["payload"].get("cause") for e in r.events() if e["kind"] == "check"
              and e.get("stage") == "land"]
    assert causes.count("interrupted") == 9, causes


def test_a_stage_attempt_killed_before_any_check_is_not_one_of_its_three(rig):
    r = rig
    r.create()
    r.drive(until="test")
    for _ in range(2):
        _dead_attempt(r, "test", ["TS-1"], {})
    r.report_and_complete("fail")              # a real failure counts as the first
    assert r.state()["stage"] == "test" and r.state()["stages"]["test"] != "degraded"
    _dead_attempt(r, "test", ["TS-1"], {})
    out = r.report_and_complete()
    assert out.get("ok") is True and r.state()["stage"] == "deliver", out
