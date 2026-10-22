"""Tests the one-shot Pipeline v1 cutover surface.

Methodology: inspect active skill/cards and invoke legacy CLI in a scratch data root.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SKILL = ROOT / "backend/skills/s_autonomous-pipeline"


def test_active_skill_has_one_v1_cli_and_no_legacy_review_path():
    paths = [SKILL / "INSTRUCTIONS.md"] + [
        SKILL / "stages" / f"{name}.md" for name in
        ("evaluate", "think", "plan", "build", "goal_cycle", "review", "test", "deliver", "reflect")]
    text = "\n".join(path.read_text(encoding="utf-8") for path in paths)
    assert "pipeline_v1.py" in text
    for old in ("artifact_cli.py stage-", "gate1-skeptic", "meta-review",
                "fixspec-skeptic", "root-cause", "gate2-next", "gate2-recheck",
                "--convergence", "run-update --stage-json", "advance --run-id"):
        assert old not in text


def test_legacy_mutation_cli_refuses_before_touching_workspace(tmp_path):
    env = {**os.environ, "SWARM_DATA_DIR": str(tmp_path / "data"),
           "SWARM_WORKSPACE": str(tmp_path / "workspace")}
    result = subprocess.run(
        [sys.executable, str(ROOT / "backend/scripts/artifact_cli.py"), "run-create",
         "--project", "P", "--requirement", "x"], env=env,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=20)
    assert result.returncode == 2
    assert "legacy pipeline mutation command removed" in result.stderr
    assert not (tmp_path / "workspace").exists()


def test_new_protocol_does_not_import_legacy_gate_modules():
    text = "\n".join(path.read_text(encoding="utf-8") for path in
                     (ROOT / "backend/core/run_protocol").glob("*.py"))
    for module in ("gate2_convergence", "gate2_fixspec", "finding_ledger",
                   "review_round_gate", "pipeline_validator"):
        assert module not in text
