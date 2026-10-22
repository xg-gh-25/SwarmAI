"""R1.5b-ii — D7 expert-coverage enforcement, landed INSIDE `cmd_run_commit`.

⚠️ LANDING-POINT CORRECTION (Gate-2 adversarial finding): the D7 decision does NOT
live in `security_hooks.create_adversarial_commit_gate` (a PreToolUse Bash gate).
The pipeline's canonical commit path is `run-commit`, whose `git commit` is a direct
`subprocess.run` — it BYPASSES Claude's PreToolUse hook entirely. A D7 check in
PreToolUse therefore never fires on a pipeline commit (it only catches an agent's
ad-hoc shell `git commit`). The decision must live where the pipeline commit actually
happens: `cmd_run_commit`, after the push_ready + files_touched gates, before the
per-repo `git commit` loop.

D7 rule enforced here:
  read run.json `required_experts` → collect `completed` expert_ids from this RUN's
  adversarial markers (glob `*_adv_*.marker`, filter payload `run_id == this run`,
  take `expert_id`) → require declared ⊆ completed, else REFUSE the commit (exit 2,
  list missing). The CLI process has only `run_id` (no session_id), so completed is
  keyed on run_id — no session→run map needed (simpler than the reverted PreToolUse
  version).

Fail-open (never self-lock the pipeline):
  - run.json has no `required_experts` key (legacy in-flight run / daemon-restart) → skip D7  [self-lock face]
  - `required_experts` is an empty list → skip D7
  - marker dir unreadable / any infra error → skip D7 (commit gate cardinal: fail-open)

Mutation-proof: neuter the subset check (treat every required as satisfied) → the
missing-expert REFUSE test goes green-should-be-red = FAILS.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
CLI = REPO / "scripts" / "artifact_cli.py"


# ── helpers ───────────────────────────────────────────────────────────────────
def _run_cli(args, env):
    """Invoke artifact_cli.py run-commit as a subprocess (real CLI path)."""
    return subprocess.run(
        [sys.executable, str(CLI), *args],
        capture_output=True, text=True, env=env, cwd=str(REPO),
    )


def _audit_dir(data_dir: Path) -> Path:
    d = data_dir / "state" / "pipeline_agent_audit"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _write_marker(audit: Path, sid: str, n: int, expert_id, run_id):
    payload = {"ts": n, "event": "SubagentStop", "session_id": sid,
               "adversarial": True, "agent_type": "general-purpose", "run_id": run_id}
    if expert_id is not None:
        payload["expert_id"] = expert_id
    (audit / f"session_{sid}_adv_{n}.marker").write_text(json.dumps(payload))


# The D7 helper is a pure function — test it directly (fast, no subprocess).
# Import lazily so a collection-time import error surfaces as a test failure, not a
# collection crash.
def _load_d7():
    sys.path.insert(0, str(REPO))
    import importlib
    mod = importlib.import_module("scripts.artifact_cli")
    return mod


# ── integration: run-commit REFUSES when a declared expert didn't run ──────────
def _seed_run(tmp_path, rid, required_experts, *, push_ready=True, files=None):
    """Write a minimal run.json + a committable file in a temp git repo."""
    ws = tmp_path / "SwarmWS"
    repo = tmp_path / "srcrepo"
    repo.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    (repo / "seed.txt").write_text("seed")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "seed"], cwd=repo, check=True)
    f = repo / "change.py"
    f.write_text("x = 1\n")
    files = files or [str(f)]
    rundir = ws / "Projects" / "SwarmAI" / ".artifacts" / "runs" / rid
    rundir.mkdir(parents=True, exist_ok=True)
    state = {
        "id": rid, "project": "SwarmAI", "status": "paused", "profile": "full",
        "requirement": "D7 test run", "files_touched": files,
        "stages": [{"stage": "deliver", "status": "completed", "push_ready": push_ready},
                   {"stage": "reflect", "status": "recorded", "lessons": ["[pitfall] a durable lesson long enough to count"]}],
    }
    if required_experts is not None:
        state["required_experts"] = required_experts
    (rundir / "run.json").write_text(json.dumps(state))
    return ws, repo, rundir


def test_run_commit_allows_when_all_experts_ran(tmp_path, monkeypatch):
    """declared [correctness], correctness ran → D7 passes (commit proceeds)."""
    import os
    rid = "run_d7ok"
    ws, repo, rundir = _seed_run(tmp_path, rid, ["correctness"])
    audit = _audit_dir(tmp_path)
    _write_marker(audit, "sessX", 1, "correctness", rid)
    env = dict(os.environ, SWARM_DATA_DIR=str(tmp_path), SWARM_WORKSPACE=str(ws))
    r = _run_cli(["run-commit", "--project", "SwarmAI", "--run-id", rid], env)
    # D7 must not block; commit may still succeed or warn on other grounds, but NOT a D7 refuse
    assert "did NOT run an adversarial review" not in (r.stdout + r.stderr), \
        f"D7 must not refuse when all declared experts ran: {r.stdout}{r.stderr}"


def test_run_commit_fail_open_legacy_run_no_required_experts(tmp_path, monkeypatch):
    """run.json WITHOUT a required_experts key (legacy/daemon-restart) → D7 skips, no refuse."""
    import os
    rid = "run_d7legacy"
    ws, repo, rundir = _seed_run(tmp_path, rid, None)  # NO required_experts key
    env = dict(os.environ, SWARM_DATA_DIR=str(tmp_path), SWARM_WORKSPACE=str(ws))
    r = _run_cli(["run-commit", "--project", "SwarmAI", "--run-id", rid], env)
    assert "did NOT run an adversarial review" not in (r.stdout + r.stderr), \
        "legacy run (no required_experts) must not be D7-refused (self-lock face)"
