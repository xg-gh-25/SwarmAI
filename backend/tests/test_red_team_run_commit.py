"""run-commit re-derives the Gate-2 Red Team HIGH trigger from the deliver artifact.

What is tested: a post-cutover run whose deliver artifact carries a specialist HIGH
must have the red-team EXPERT-ID marker before run-commit commits, even when
run.json never declared `red-team` in required_experts (a path that skipped the
persisted declaration). Method: cmd_run_commit in-process on a tmp workspace with a
tmp marker store; the refusal fires before any git call. Pre-cutover runs and runs
without the trigger are unaffected (pure helper checks).
"""
import json
from argparse import Namespace
from unittest.mock import patch

import pytest

from core.artifact_registry import ArtifactRegistry
from scripts import artifact_cli as cli

POST, PRE = "2026-10-19T00:00:00+00:00", "2026-10-13T00:00:00+00:00"


def _art(dispatched=True):
    return {"adversarial_review": {"profile_tier": "full", "spawned": True,
                                   "red_team_dispatched": dispatched,
                                   "findings": [{"severity": "HIGH", "resolved": True,
                                                 "specialist": "security",
                                                 "finding": "a.py x: y. Fixed: z."}]}}


@pytest.fixture
def run(tmp_path, monkeypatch):
    monkeypatch.setenv("SWARM_WORKSPACE", str(tmp_path))
    monkeypatch.setenv("SWARM_DATA_DIR", str(tmp_path / "data"))
    audit = tmp_path / "data" / "state" / "pipeline_agent_audit"
    audit.mkdir(parents=True)
    (audit / "session_s_adv_1.marker").write_text(json.dumps(
        {"ts": 1, "run_id": "run_rc", "expert_id": "security"}))
    (tmp_path / "Projects" / "P" / ".artifacts" / "runs" / "run_rc").mkdir(parents=True)
    reg = ArtifactRegistry(tmp_path)
    aid = reg.publish(project="P", artifact_type="delivery", data=_art(),
                      producer="s_autonomous-pipeline", summary="s", run_id="run_rc")
    state = {"id": "run_rc", "project": "P", "profile": "full", "status": "running",
             "created_at": POST, "files_touched": ["a.py"],
             "stages": [{"stage": "deliver", "status": "completed", "push_ready": True,
                         "artifact_id": aid}]}
    (tmp_path / "Projects/P/.artifacts/runs/run_rc/run.json").write_text(json.dumps(state))
    return tmp_path


def test_run_commit_refuses_without_red_team_marker(run, capsys):
    with patch("scripts.artifact_cli._get_workspace", return_value=run):
        with pytest.raises(SystemExit) as ex:
            cli.cmd_run_commit(Namespace(project="P", run_id="run_rc", force=False), reg=None)
    assert ex.value.code == 2
    err = json.loads(capsys.readouterr().err.strip().splitlines()[-1])
    assert err["blocked"] is True and err["missing_experts"] == ["red-team"], err


@pytest.mark.parametrize("state,art,added", [
    ({"created_at": POST, "profile": "full"}, _art(), True),
    ({"created_at": POST, "profile": "full", "required_experts": ["red-team"]}, _art(), False),
    ({"created_at": PRE, "profile": "full"}, _art(), False),            # grandfathered
    ({"created_at": POST, "profile": "trivial"}, _art(), False),        # relaxed profile
    ({"created_at": POST, "profile": "full", "required_experts": "x"}, _art(), False),
    ({"created_at": POST, "profile": "full"}, None, False),
    ({"created_at": POST, "profile": "full"},
     {"adversarial_review": {"findings": [{"severity": "LOW", "finding": "x"}]}}, False),
])
def test_require_red_team_expert(state, art, added):
    before = dict(state)
    assert cli._require_red_team_expert(state, art) is added
    if added:
        assert state["required_experts"] == ["red-team"]
    else:
        assert state == before
