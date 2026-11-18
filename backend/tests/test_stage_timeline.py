"""The run's stage timeline (core.run_protocol.timeline) and the summary fields the
Pipelines Line reads (routers.pipelines._summary).

Events are built with the lifecycle tests' Stream, whose event time is that module's
``AT``: each test moves it forward between steps so the intervals are known.
"""
from __future__ import annotations

import copy

import pytest

import tests.test_run_protocol_lifecycle as lt
from core.run_protocol import lifecycle as lc
from core.run_protocol.timeline import stage_timeline


@pytest.fixture
def clock(monkeypatch):
    """Set the time the next events are written at (minutes after a fixed start)."""
    def at(minute: int) -> None:
        monkeypatch.setattr(lt, "AT", f"2026-11-18T10:{minute:02d}:00+00:00")
    at(0)
    return at


def test_entered_at_per_opened_stage(clock):
    stream = lt.Stream("trivial")
    clock(10)
    stream.complete_stage()                 # evaluate -> plan at 10:10
    clock(25)
    stream.complete_stage()                 # plan -> build at 10:25
    out = stage_timeline(stream.rows)
    assert out["evaluate"]["entered_at"] == "2026-11-18T10:00:00+00:00"
    assert out["plan"]["entered_at"] == "2026-11-18T10:10:00+00:00"
    assert out["build"]["entered_at"] == "2026-11-18T10:25:00+00:00"
    assert out["evaluate"]["active_s"] == 600.0 and out["plan"]["active_s"] == 900.0
    # stages never entered have no entry, nothing is estimated for them
    assert set(out) == {"evaluate", "plan", "build"}
    assert out["build"]["active_s"] == 0.0


def test_decision_wait_counts_as_waiting(clock):
    stream = lt.Stream("full")
    clock(5)
    stream.add("decision", {"request_id": "q1", "decision_kind": "requirement_ambiguity",
                            "document": "decision-doc", "decision_json": "{}"})
    clock(35)
    stream.add("human_answer", {"request_id": "q1", "option_id": "A"})
    clock(40)
    stream.complete_stage()
    out = stage_timeline(stream.rows)
    assert out["evaluate"]["wait_s"] == 1800.0          # 10:05 -> 10:35 awaiting_human
    assert out["evaluate"]["active_s"] == 600.0         # 10:00 -> 10:05 and 10:35 -> 10:40
    assert out["think"]["entered_at"] == "2026-11-18T10:40:00+00:00"


def test_paused_time_is_neither_working_nor_waiting(clock):
    stream = lt.Stream()
    clock(3)
    stream.add("worktree_unavailable", {"reason": "cannot create"})
    clock(50)
    stream.add("resume", {"worktree_ready": True})
    out = stage_timeline(stream.rows)
    assert out["build"]["active_s"] == 180.0 and out["build"]["wait_s"] == 0.0


def test_an_event_without_a_time_leaves_its_intervals_out(clock):
    stream = lt.Stream("trivial")
    clock(10)
    stream.complete_stage()
    rows = copy.deepcopy(stream.rows)
    rows[1]["at"] = ""                        # the 10:00 -> 10:10 gap now has an unreadable end
    out = stage_timeline(rows)
    assert out["evaluate"]["active_s"] == 0.0  # neither interval touching it is measured
    rows = copy.deepcopy(stream.rows)
    for row in rows[1:]:
        row["at"] = ""
    assert stage_timeline(rows)["plan"]["entered_at"] is None


def test_fold_unchanged(clock):
    stream = lt.Stream("full")
    clock(10)
    stream.complete_stage()
    before = stream.projection()
    stage_timeline(stream.rows)
    assert stream.projection() == before
    assert "stage_timeline" not in before


def test_replay_error_omits_timeline(clock):
    stream = lt.Stream("full")
    stream.add("user_stop", {"reason": "stop"}, transition=False)   # no transition follows
    stream.add("turn", {"message_id": "m1"}, transition=False)
    with pytest.raises(lc.LifecycleError):
        stage_timeline(stream.rows)


def _engine(tmp_path, monkeypatch):
    import json  # noqa: F401
    from core import pipeline_human
    from core.run_protocol.engine import RunEngine
    from tests.test_pipeline_v1_human_loop import OWNER, _git_repo
    workspace, data = tmp_path / "workspace", tmp_path / "data"
    monkeypatch.setenv("SWARM_DATA_DIR", str(data))
    monkeypatch.setattr(pipeline_human, "_workspace", lambda: workspace)
    return RunEngine.create(data_dir=data, workspace=workspace, project="P", profile="trivial",
                            session_id=OWNER, repos={"repo": str(_git_repo(tmp_path / "repo"))},
                            run_id="run_tl1")


def test_projection_carries_the_stage_timeline(tmp_path, monkeypatch):
    import json
    engine = _engine(tmp_path, monkeypatch)
    value = json.loads(engine.write_projection().read_text())
    assert value["stage_timeline"]["evaluate"]["entered_at"]
    assert set(value["stage_timeline"]) == {"evaluate"}


@pytest.mark.parametrize("error", [lc.LifecycleError("refused"), TypeError("naive vs aware")])
def test_replay_error_still_writes_the_projection(tmp_path, monkeypatch, error):
    import json
    from core.run_protocol import timeline

    def refuse(_events):
        raise error
    engine = _engine(tmp_path, monkeypatch)
    monkeypatch.setattr(timeline, "stage_timeline", refuse)
    value = json.loads(engine.write_projection().read_text())
    assert "stage_timeline" not in value and value["stage"] == "evaluate"


def test_a_time_without_an_offset_is_unreadable(clock):
    stream = lt.Stream("trivial")
    clock(10)
    stream.complete_stage()
    rows = copy.deepcopy(stream.rows)
    rows[1]["at"] = "2026-11-18T10:10:00"          # no offset: cannot be compared
    out = stage_timeline(rows)                       # never raises
    assert out["evaluate"]["active_s"] == 0.0


def test_analytics_lists_every_run_of_the_window(monkeypatch):
    import asyncio
    from routers import pipelines
    raws = [dict(_raw(id=f"run_{i:02d}", updated_at=f"2026-11-18T10:{i:02d}:00+00:00"), _project="SwarmAI")
            for i in range(25)]
    monkeypatch.setattr(pipelines, "_load_pipeline_runs", lambda: raws)
    out = asyncio.run(pipelines.pipeline_analytics(window="30d"))
    group = next(g for g in out.by_project if g.project == "SwarmAI")
    assert group.run_count == 25 and len(group.runs) == 25


def _raw(**extra):
    raw = {"id": "run_t1", "requirement": "r", "status": "active", "profile": "trivial",
           "created_at": "2026-11-18T10:00:00+00:00", "updated_at": "2026-11-18T10:10:00+00:00",
           "stage": "plan", "stages": {"evaluate": "completed", "plan": "open", "build": "pending",
                                       "review": "pending", "test": "pending",
                                       "deliver": "pending", "reflect": "pending"},
           "stage_attempts": {"evaluate": 2}}
    raw.update(extra)
    return raw


def test_summary_carries_stage_fields():
    from routers.pipelines import _summary
    s = _summary(_raw(stage_timeline={"evaluate": {"entered_at": "2026-11-18T10:00:00+00:00",
                                                   "active_s": 600.0, "wait_s": 0.0},
                                      "plan": {"entered_at": 7, "active_s": "x"}}), "SwarmAI")
    assert s.stage == "plan"
    assert [x["name"] for x in s.stages] == list(lc.PROFILE_STAGES["trivial"])
    assert s.stages[0] == {"name": "evaluate", "status": "completed", "attempts": 2}
    assert s.stage_timeline["evaluate"]["active_s"] == 600.0
    # an unreadable value stays unknown, never 0
    assert s.stage_timeline["plan"] == {"entered_at": None, "active_s": None, "wait_s": None}


def test_summary_of_an_old_run_has_no_stage_record():
    from routers.pipelines import _summary
    raw = _raw()
    for key in ("stage", "stages", "stage_timeline"):
        raw.pop(key, None)
    s = _summary(raw, "SwarmAI")
    assert s.stage is None and s.stages is None and s.stage_timeline is None
