"""Regression: run-report must survive string-shaped stage `decisions`.

DoD0a (run_e346b8ed). Pipeline stages write decisions as list[dict]
({classification, description, ...}), but session/DailyActivity-sourced
decisions are list[str]. Three sites in cmd_run_report consumed decisions
assuming dict shape (`**d`, `d.get(...)`), crashing with TypeError/AttributeError
on a bare string. This drove a real mid-pipeline crash (run_4261f1a3) that
blocked completion until the deliver decisions were hand-coerced to dicts.

The test builds a run.json with a STRING decision in BOTH a normal stage and
the reflect stage, then asserts run-report renders without raising.
Mutation check: revert any of the three coerces → this test raises.
"""

import json
from pathlib import Path


def _write_run(workspace: Path, run_id: str, stages: list, profile="full") -> Path:
    run_dir = workspace / "Projects" / "SwarmAI" / ".artifacts" / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    run_data = {
        "id": run_id,
        "project": "SwarmAI",
        "requirement": "coerce regression",
        "profile": profile,
        "status": "completed",
        "stages": stages,
        "created_at": "2026-07-07T00:00:00+00:00",
        "updated_at": "2026-07-07T00:00:00+00:00",
    }
    (run_dir / "run.json").write_text(json.dumps(run_data, indent=2))
    return run_dir


class _Args:
    def __init__(self, project, run_id, force=True):
        self.project = project
        self.run_id = run_id
        self.force = force


def test_string_decision_does_not_crash_report(tmp_path, monkeypatch):
    """String decisions at all 3 collection sites must not crash run-report."""
    monkeypatch.setenv("SWARM_WORKSPACE", str(tmp_path))
    from core.artifact_registry import ArtifactRegistry
    from scripts.artifact_cli import cmd_run_report

    stages = [
        {"stage": "evaluate", "status": "completed",
         "decisions": ["a bare string decision"]},
        {"stage": "build", "status": "completed",
         "decisions": [{"classification": "mechanical", "description": "dict decision"},
                       "mixed-in string decision"]},
        {"stage": "reflect", "status": "completed",
         "lessons": ["real lesson"],
         "decisions": ["reflect string decision"]},
    ]
    _write_run(tmp_path, "run_coerce01", stages)
    reg = ArtifactRegistry(tmp_path)

    # Must not raise — the coerce turns str → {"description": str} at all sites.
    cmd_run_report(_Args("SwarmAI", "run_coerce01"), reg)

    report = (tmp_path / "Projects" / "SwarmAI" / ".artifacts" / "runs"
              / "run_coerce01" / "REPORT.md")
    assert report.exists(), "report should be written despite string decisions"
    assert report.stat().st_size > 0


def test_dict_decisions_still_render(tmp_path, monkeypatch):
    """Coerce must not regress the normal dict path (classification counts)."""
    monkeypatch.setenv("SWARM_WORKSPACE", str(tmp_path))
    from core.artifact_registry import ArtifactRegistry
    from scripts.artifact_cli import cmd_run_report

    stages = [
        {"stage": "build", "status": "completed",
         "decisions": [
             {"classification": "taste", "description": "chose X"},
             {"classification": "mechanical", "description": "used pytest"},
         ]},
        {"stage": "reflect", "status": "completed", "lessons": ["l"], "decisions": []},
    ]
    _write_run(tmp_path, "run_coerce02", stages)
    reg = ArtifactRegistry(tmp_path)

    cmd_run_report(_Args("SwarmAI", "run_coerce02"), reg)
    report = (tmp_path / "Projects" / "SwarmAI" / ".artifacts" / "runs"
              / "run_coerce02" / "REPORT.md")
    assert report.exists() and report.stat().st_size > 0
