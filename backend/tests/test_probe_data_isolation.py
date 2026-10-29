"""A product probe run by hand (outside eval_runner) never opens the user's real stores.

Earned: probes run straight from a shell during development wrote injection-selection
rows for their temporary workspaces into the live ~/.swarm-ai/ingestion.db (10 rows,
2026-10-27/28): ``tempdir()`` isolated the workspace only, and the selection writer,
ingestion.db and data.db all default to ~/.swarm-ai when SWARM_DATA_DIR is unset."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]


def test_a_probe_run_by_hand_writes_nothing_under_the_users_home(tmp_path):
    env = {k: v for k, v in os.environ.items() if k not in {"SWARM_DATA_DIR", "SWARM_WORKSPACE", "SWARMWS"}}
    env.update(HOME=str(tmp_path), PYTHONPATH=str(BACKEND))
    done = subprocess.run([sys.executable, "-m", "scripts.probe_memory", "inject"], cwd=str(BACKEND),
                          env=env, capture_output=True, text=True, timeout=90)
    assert "MEM_INJECT_OK" in done.stdout, done.stdout + done.stderr
    assert not (tmp_path / ".swarm-ai" / "ingestion.db").exists()
    assert not (tmp_path / ".swarm-ai" / "data.db").exists()
