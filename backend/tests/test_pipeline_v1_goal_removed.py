"""The goal profile is removed (design docs/design/2026-10-26-pipeline-two-ends.md §6).

What is tested:
- create --profile goal is refused, and the refusal says what to do instead;
- an old goal run's log reads as that same message, not a crash or "unknown profile";
- no stage card, stage set or profile table still names goal / goal_cycle.
"""
import pytest

from core.run_protocol import lifecycle
from core.run_protocol.engine import EngineError, RunEngine


def test_create_goal_is_refused_with_what_to_do(tmp_path):
    (tmp_path / "ws" / "Projects" / "P").mkdir(parents=True)
    with pytest.raises(EngineError, match="sequence of runs"):
        RunEngine.create(data_dir=tmp_path / "data", workspace=tmp_path / "ws", project="P",
                         profile="goal", session_id="s", repos={"r": str(tmp_path)})


def test_an_old_goal_run_reads_as_removed():
    event = {"run_id": "run_old", "payload": {"run_id": "run_old", "profile": "goal",
                                              "decision_cap": 2}}
    with pytest.raises(lifecycle.LifecycleError, match="goal profile was removed"):
        lifecycle._initial(event)


def test_goal_is_gone_from_the_profile_and_stage_tables():
    from core.run_protocol import checks, next_step, validation_report
    from core.run_protocol.engine import RunEngine as E
    assert "goal" not in lifecycle.PROFILE_STAGES and "goal" in lifecycle.REMOVED_PROFILES
    assert all("goal_cycle" not in stages for stages in lifecycle.PROFILE_STAGES.values())
    assert "goal_cycle" not in validation_report.STAGES
    assert "goal_cycle" not in E.WORKTREE_STAGES
    assert "goal_cycle" not in next_step._CODE_STAGES
    assert all("goal" not in spec.profiles for spec in checks.CHECKS.values())
