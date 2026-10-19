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


# ── D7 helper: declared ⊆ completed, keyed on run_id ───────────────────────────
def test_d7_missing_expert_detected(tmp_path, monkeypatch):
    """required=[correctness,security], only correctness ran this run → missing=[security]."""
    mod = _load_d7()
    monkeypatch.setenv("SWARM_DATA_DIR", str(tmp_path))
    audit = _audit_dir(tmp_path)
    rid = "run_abc"
    _write_marker(audit, "sessX", 1, "correctness", rid)
    missing = mod._d7_missing_required_experts(["correctness", "security"], rid)
    assert missing == ["security"], f"expected [security] missing, got {missing}"


def test_d7_all_present(tmp_path, monkeypatch):
    mod = _load_d7()
    monkeypatch.setenv("SWARM_DATA_DIR", str(tmp_path))
    audit = _audit_dir(tmp_path)
    rid = "run_abc"
    _write_marker(audit, "sessX", 1, "correctness", rid)
    _write_marker(audit, "sessX", 2, "security", rid)
    missing = mod._d7_missing_required_experts(["correctness", "security"], rid)
    assert missing == [], f"all declared ran → no missing, got {missing}"


def test_d7_cross_run_isolation(tmp_path, monkeypatch):
    """A marker from a SIBLING run_id must NOT satisfy this run's requirement."""
    mod = _load_d7()
    monkeypatch.setenv("SWARM_DATA_DIR", str(tmp_path))
    audit = _audit_dir(tmp_path)
    rid, sibling = "run_abc", "run_other"
    _write_marker(audit, "sessX", 1, "security", sibling)   # ran under a DIFFERENT run
    _write_marker(audit, "sessX", 2, "correctness", rid)    # this run ran correctness only
    missing = mod._d7_missing_required_experts(["security"], rid)
    assert missing == ["security"], "sibling-run marker must not satisfy this run"


def test_d7_old_marker_without_expert_id(tmp_path, monkeypatch):
    """A marker with no expert_id contributes nothing; a satisfied requirement stays satisfied."""
    mod = _load_d7()
    monkeypatch.setenv("SWARM_DATA_DIR", str(tmp_path))
    audit = _audit_dir(tmp_path)
    rid = "run_abc"
    _write_marker(audit, "sessX", 1, "correctness", rid)
    _write_marker(audit, "sessX", 2, None, rid)  # old marker, no expert_id
    missing = mod._d7_missing_required_experts(["correctness"], rid)
    assert missing == [], "expert_id-less marker must not break a satisfied requirement"


def test_d7_fail_open_unreadable_dir(tmp_path, monkeypatch):
    """Gate-2 correctness HIGH: marker STORE does not exist (unreadable) → FAIL-OPEN.
    An infra failure (missing/unreadable audit dir) must NEVER self-lock a commit, so
    the shortfall is EMPTY (allow), NOT the full declared set. This is the fix: an
    unreadable store is distinct from a readable-but-empty store."""
    mod = _load_d7()
    monkeypatch.setenv("SWARM_DATA_DIR", str(tmp_path))  # no audit dir created
    # the store is unreadable → _d7_completed_expert_ids returns None → fail-open
    assert mod._d7_completed_expert_ids("run_abc") is None, "missing store → None (fail-open signal)"
    missing = mod._d7_missing_required_experts(["correctness"], "run_abc")
    assert missing == [], "unreadable store → fail-open: no shortfall, never self-lock"


def test_d7_readable_but_empty_store_refuses(tmp_path, monkeypatch):
    """The OTHER half of the Gate-2 HIGH: a READABLE store that genuinely has no marker
    for this run → the declared expert IS uncovered → real refuse (NOT fail-open). This
    is distinguishable from the unreadable case above (set() vs None)."""
    mod = _load_d7()
    monkeypatch.setenv("SWARM_DATA_DIR", str(tmp_path))
    audit = _audit_dir(tmp_path)  # dir EXISTS but no markers written
    assert mod._d7_completed_expert_ids("run_abc") == set(), "readable+empty → set() (genuine zero)"
    missing = mod._d7_missing_required_experts(["correctness"], "run_abc")
    assert missing == ["correctness"], "readable store, no marker → correctness genuinely uncovered → refuse"


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


def test_run_commit_refuses_on_missing_expert(tmp_path, monkeypatch):
    """declared [correctness,security], only correctness ran → run-commit REFUSE exit 2."""
    import os
    rid = "run_d7int"
    ws, repo, rundir = _seed_run(tmp_path, rid, ["correctness", "security"])
    audit = _audit_dir(tmp_path)
    _write_marker(audit, "sessX", 1, "correctness", rid)  # security missing
    env = dict(os.environ, SWARM_DATA_DIR=str(tmp_path), SWARM_WORKSPACE=str(ws))
    r = _run_cli(["run-commit", "--project", "SwarmAI", "--run-id", rid], env)
    assert r.returncode == 2, f"expected refuse exit 2, got {r.returncode}: {r.stdout}{r.stderr}"
    assert "security" in (r.stdout + r.stderr), "refuse reason must name the missing expert"


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


# ── Gate-1 MED (normalization symmetry): the marker-writer forces a LOWERCASE registry
#    id, so a declared "Correctness"/"Security" (title-case) MUST still match — else a
#    false self-lock would push the run toward a bypass. ─────────────────────
def test_d7_declared_titlecase_matches_lowercase_marker(tmp_path, monkeypatch):
    mod = _load_d7()
    monkeypatch.setenv("SWARM_DATA_DIR", str(tmp_path))
    audit = _audit_dir(tmp_path)
    rid = "run_abc"
    _write_marker(audit, "sessX", 1, "correctness", rid)   # marker is lowercase (writer-forced)
    _write_marker(audit, "sessX", 2, "security", rid)
    # declaration is Title-case — must NOT false-self-lock
    missing = mod._d7_missing_required_experts(["Correctness", "Security"], rid)
    assert missing == [], f"title-case declaration must match lowercase marker, got {missing}"


def test_d7_declared_whitespace_and_case_normalized(tmp_path, monkeypatch):
    mod = _load_d7()
    monkeypatch.setenv("SWARM_DATA_DIR", str(tmp_path))
    audit = _audit_dir(tmp_path)
    rid = "run_abc"
    _write_marker(audit, "sessX", 1, "state-machine", rid)
    missing = mod._d7_missing_required_experts(["  State-Machine  "], rid)
    assert missing == [], f"whitespace+case must normalize to match, got {missing}"


def test_d7_uppercase_marker_also_normalized(tmp_path, monkeypatch):
    """Defense-in-depth: even if a marker ever carried an upper/mixed-case expert_id,
    the completed-set lowercasing keeps the match symmetric."""
    mod = _load_d7()
    monkeypatch.setenv("SWARM_DATA_DIR", str(tmp_path))
    audit = _audit_dir(tmp_path)
    rid = "run_abc"
    _write_marker(audit, "sessX", 1, "Correctness", rid)   # mixed-case marker (hypothetical)
    missing = mod._d7_missing_required_experts(["correctness"], rid)
    assert missing == [], f"mixed-case marker must normalize to match lowercase declared, got {missing}"

def test_run_commit_has_no_force_bypass_of_d7(tmp_path, monkeypatch):
    """--force is gone (2026-10-19): a missing declared expert refuses with no override;
    the remedy (spawn it) needs no human."""
    import os
    rid = "run_d7force"
    ws, repo, rundir = _seed_run(tmp_path, rid, ["correctness", "security"])
    audit = _audit_dir(tmp_path)
    _write_marker(audit, "sessX", 1, "correctness", rid)  # security missing
    env = dict(os.environ, SWARM_DATA_DIR=str(tmp_path), SWARM_WORKSPACE=str(ws))
    r = _run_cli(["run-commit", "--project", "SwarmAI", "--run-id", rid, "--force"], env)
    assert r.returncode == 2 and "unrecognized arguments: --force" in r.stderr, r.stderr
    r = _run_cli(["run-commit", "--project", "SwarmAI", "--run-id", rid], env)
    assert r.returncode == 2 and "security" in r.stderr and "--force" not in r.stderr, r.stderr


def test_a_degraded_deliver_commits_marked_despite_missing_experts(tmp_path, monkeypatch):
    """DELIVER already completed degraded (3 refused stage-completes): the run must not
    end uncommitted, so D7 marks the commit instead of refusing it."""
    import os
    rid = "run_d7deg"
    ws, repo, rundir = _seed_run(tmp_path, rid, ["correctness", "security"], push_ready=False)
    st = json.loads((rundir / "run.json").read_text())
    st["stages"][0].update(degraded={"attempts": 3, "errors": ["E_X: gate"]},
                           push_ready_reason="DELIVER completed degraded after 3 refused attempts")
    (rundir / "run.json").write_text(json.dumps(st))
    audit = _audit_dir(tmp_path)
    _write_marker(audit, "sessX", 1, "correctness", rid)  # security missing
    env = dict(os.environ, SWARM_DATA_DIR=str(tmp_path), SWARM_WORKSPACE=str(ws))
    r = _run_cli(["run-commit", "--project", "SwarmAI", "--run-id", rid], env)
    assert r.returncode == 0, r.stdout + r.stderr
    c = json.loads((rundir / "run.json").read_text())["commits"][0]
    assert c["push_ready"] is False and "security" in c["push_ready_reason"]
