"""Tests for create_active_run_commit_gate — the structural gate that makes it
IMPOSSIBLE for a pipeline run's CODE to be committed before run-commit runs, so
code + REPORT.md always land in ONE commit (the review-purpose invariant).

Root cause it closes (git-reflog 29b3c1dd + a1ec9b0e, Gate-0 + Gate-1 confirmed):
REPORT.md is generated late (REFLECT). If the run's code is committed FIRST (manual
`git commit` in COMPLETE, or a per-cycle BUILD commit), run-commit's
`git commit -- *real_files` later carries only the new REPORT.md → a split.

The gate DENYs an agent `git commit` whose pending paths intersect the files_touched
of any ACTIVE pipeline run (status in {running, paused} — an ALLOWLIST, Gate-1 fix:
a status denylist rots). run-commit itself commits via a Python subprocess, NOT the
agent Bash tool, so it never reaches PreToolUse — no self-deadlock.

Invariants:
  • DENY a git commit of a path owned by a RUNNING run → route to run-commit.
  • DENY a git commit of a path owned by a PAUSED run (resumes + run-commits later).
  • APPROVE a commit of a path owned only by a TERMINAL run (completed/cancelled/
    abandoned/superseded/rejected/aborted/complete) — ownership expired.
  • APPROVE a commit of a path in NO active run (unrelated / parallel session).
  • APPROVE all non-commit / non-Bash commands (fail-safe).
  • FAIL-OPEN: uncomputable pending (None) / any infra error → approve (never false-block).
"""

import asyncio
import json
import os
import pytest

from core import security_hooks
from core.security_hooks import create_active_run_commit_gate, _active_run_owned_paths


@pytest.fixture
def runs_root(tmp_path, monkeypatch):
    """Point the gate's active-run scan at a temp Projects/*/.artifacts/runs root."""
    root = tmp_path / "Projects"
    monkeypatch.setattr(security_hooks, "_ACTIVE_RUN_GLOB_ROOT", str(tmp_path))
    return root


def _seed_run(runs_root, project, run_id, status, files_touched):
    d = runs_root / project / ".artifacts" / "runs" / run_id
    d.mkdir(parents=True, exist_ok=True)
    payload = {"status": status}
    if files_touched is not None:
        payload["files_touched"] = files_touched
    (d / "run.json").write_text(json.dumps(payload))


def _run(gate, command, tool_name="Bash", cwd="/repo"):
    return asyncio.run(
        gate({"tool_name": tool_name, "tool_input": {"command": command}, "cwd": cwd}, None, None)
    )


def _is_deny(result):
    return result.get("hookSpecificOutput", {}).get("permissionDecision") == "deny"


# ── _active_run_owned_paths helper ───────────────────────────────────────────

class TestActiveRunOwnedPaths:
    def test_running_run_contributes_its_files(self, runs_root):
        _seed_run(runs_root, "P", "run_a", "running", ["/abs/x.py"])
        assert "/abs/x.py" in _active_run_owned_paths()

    def test_paused_run_contributes(self, runs_root):
        _seed_run(runs_root, "P", "run_b", "paused", ["/abs/y.py"])
        assert "/abs/y.py" in _active_run_owned_paths()

    @pytest.mark.parametrize("terminal", [
        "completed", "abandoned", "cancelled", "superseded", "rejected", "aborted", "complete",
    ])
    def test_terminal_run_contributes_nothing(self, runs_root, terminal):
        _seed_run(runs_root, "P", "run_c", terminal, ["/abs/z.py"])
        assert "/abs/z.py" not in _active_run_owned_paths()

    def test_missing_files_touched_contributes_nothing(self, runs_root):
        _seed_run(runs_root, "P", "run_d", "running", None)
        assert _active_run_owned_paths() == set()

    def test_corrupt_run_json_is_skipped_not_raised(self, runs_root):
        d = runs_root / "P" / ".artifacts" / "runs" / "run_e"
        d.mkdir(parents=True, exist_ok=True)
        (d / "run.json").write_text("{ not json")
        _seed_run(runs_root, "P", "run_f", "running", ["/abs/ok.py"])
        # corrupt one skipped, valid one still collected
        assert _active_run_owned_paths() == {"/abs/ok.py"}


# ── gate behavior ────────────────────────────────────────────────────────────

class TestGateDeniesActiveRunCommit:
    def test_deny_commit_of_running_run_file(self, runs_root, monkeypatch):
        _seed_run(runs_root, "P", "run_a", "running", ["/repo/x.py"])
        monkeypatch.setattr(security_hooks, "_pending_commit_paths",
                            lambda cwd, cmd: {"/repo/x.py"})
        gate = create_active_run_commit_gate({"sdk_session_id": "s"})
        res = _run(gate, "git commit -- x.py")
        assert _is_deny(res) is True
        assert "pipeline_v1.py land" in res["hookSpecificOutput"]["permissionDecisionReason"]

    def test_deny_commit_of_paused_run_file(self, runs_root, monkeypatch):
        _seed_run(runs_root, "P", "run_b", "paused", ["/repo/y.py"])
        monkeypatch.setattr(security_hooks, "_pending_commit_paths",
                            lambda cwd, cmd: {"/repo/y.py"})
        gate = create_active_run_commit_gate({"sdk_session_id": "s"})
        assert _is_deny(_run(gate, "git commit -- y.py")) is True


class TestGateApproves:
    def test_approve_commit_of_terminal_run_file(self, runs_root, monkeypatch):
        _seed_run(runs_root, "P", "run_c", "completed", ["/repo/z.py"])
        monkeypatch.setattr(security_hooks, "_pending_commit_paths",
                            lambda cwd, cmd: {"/repo/z.py"})
        gate = create_active_run_commit_gate({"sdk_session_id": "s"})
        assert _run(gate, "git commit -- z.py") == {"decision": "approve"}

    def test_approve_commit_of_unowned_file(self, runs_root, monkeypatch):
        _seed_run(runs_root, "P", "run_a", "running", ["/repo/x.py"])
        monkeypatch.setattr(security_hooks, "_pending_commit_paths",
                            lambda cwd, cmd: {"/repo/other.py"})
        gate = create_active_run_commit_gate({"sdk_session_id": "s"})
        assert _run(gate, "git commit -- other.py") == {"decision": "approve"}

    def test_approve_non_commit_command(self, runs_root):
        gate = create_active_run_commit_gate({"sdk_session_id": "s"})
        assert _run(gate, "git status") == {"decision": "approve"}

    def test_approve_non_bash_tool(self, runs_root):
        gate = create_active_run_commit_gate({"sdk_session_id": "s"})
        assert _run(gate, "git commit -- x.py", tool_name="Write") == {"decision": "approve"}

    def test_approve_run_commit_cli_invocation(self, runs_root, monkeypatch):
        # The agent's Bash call that triggers run-commit is `python ... run-commit`,
        # which contains NO `git commit` token → _command_has_git_commit False → approve.
        # (The real git commit happens inside run-commit's subprocess, invisible here.)
        _seed_run(runs_root, "P", "run_a", "running", ["/repo/x.py"])
        gate = create_active_run_commit_gate({"sdk_session_id": "s"})
        assert _run(gate, "python backend/scripts/artifact_cli.py run-commit --run-id run_a") == {"decision": "approve"}


class TestGateFailOpen:
    def test_fail_open_when_pending_uncomputable(self, runs_root, monkeypatch):
        _seed_run(runs_root, "P", "run_a", "running", ["/repo/x.py"])
        monkeypatch.setattr(security_hooks, "_pending_commit_paths", lambda cwd, cmd: None)
        gate = create_active_run_commit_gate({"sdk_session_id": "s"})
        assert _run(gate, "git commit -- x.py") == {"decision": "approve"}

    def test_fail_open_when_pending_raises(self, runs_root, monkeypatch):
        _seed_run(runs_root, "P", "run_a", "running", ["/repo/x.py"])
        def _boom(cwd, cmd):
            raise RuntimeError("git exploded")
        monkeypatch.setattr(security_hooks, "_pending_commit_paths", _boom)
        gate = create_active_run_commit_gate({"sdk_session_id": "s"})
        assert _run(gate, "git commit -- x.py") == {"decision": "approve"}


# ── Layer-4 cross-boundary E2E (hook ↔ run.json ↔ git, NO mock of the seam) ──

class TestLayer4CrossBoundaryE2E:
    """Drive the REAL seam end-to-end: a REAL git repo + REAL _pending_commit_paths
    (not mocked) + a REAL on-disk run.json read by the REAL _active_run_owned_paths.
    The only thing not real is nothing — this is the full hook↔run.json↔git boundary.
    Mutation proof: flip the owning run's status running→completed → the gate must
    stop denying (the status-allowlist contract line is load-bearing)."""

    def _git(self, repo, *args):
        import subprocess
        subprocess.run(["git", "-C", str(repo), *args], check=True,
                       capture_output=True, text=True)

    def test_real_seam_denies_then_mutation_flips_to_approve(self, tmp_path, monkeypatch):
        import subprocess
        # 1. REAL git repo with a staged file (so _pending_commit_paths computes a real set)
        repo = tmp_path / "repo"
        repo.mkdir()
        self._git(repo, "init")
        self._git(repo, "config", "user.email", "t@t.t")
        self._git(repo, "config", "user.name", "t")
        f = repo / "code.py"
        f.write_text("x = 1\n")
        self._git(repo, "add", "code.py")  # staged → pending set = {abs path of code.py}
        abspath = str(f.resolve())

        # 2. REAL on-disk run.json owning that exact absolute path, status=running
        monkeypatch.setattr(security_hooks, "_ACTIVE_RUN_GLOB_ROOT", str(tmp_path))
        _seed_run(tmp_path / "Projects", "P", "run_live", "running", [abspath])

        # 3. Drive the REAL gate (NO mock of _pending_commit_paths or _active_run_owned_paths)
        gate = create_active_run_commit_gate({"sdk_session_id": "s"})
        res = asyncio.run(gate(
            {"tool_name": "Bash", "tool_input": {"command": "git commit -m x"}, "cwd": str(repo)},
            None, None))
        assert _is_deny(res) is True, "real seam must DENY a staged active-run file"
        assert "pipeline_v1.py land" in res["hookSpecificOutput"]["permissionDecisionReason"]

        # 4. MUTATION: flip the owning run running→completed (the status-allowlist line).
        #    The gate must now APPROVE — proving the allowlist is load-bearing, not vacuous.
        _seed_run(tmp_path / "Projects", "P", "run_live", "completed", [abspath])
        res2 = asyncio.run(gate(
            {"tool_name": "Bash", "tool_input": {"command": "git commit -m x"}, "cwd": str(repo)},
            None, None))
        assert res2 == {"decision": "approve"}, \
            "mutation (running→completed) must flip DENY→APPROVE — allowlist is load-bearing"


# ── Gate-2 HIGH regression: relative-form files_touched must still match ──

class TestPathFormCanonicalization:
    """Gate-2 HIGH (multi-specialist confirmed): _pending_commit_paths emits
    os.path.realpath'd absolute paths; a files_touched entry may be REPO-RELATIVE
    (build.md form — ~50% of live active runs). A raw-string owned set silently
    fails to match relatives → gate fails OPEN. _canonicalize_owned_path fixes it."""

    def test_relative_files_touched_is_canonicalized_to_realpath(self, runs_root, monkeypatch):
        # A relative entry resolves against a DETERMINISTIC base (_ACTIVE_RUN_GLOB_ROOT,
        # which the fixture sets to runs_root.parent) — NOT os.getcwd() (meta-review HIGH:
        # cwd is BACKEND_DIR in the daemon). The chdir below proves cwd is IRRELEVANT now.
        monkeypatch.chdir(str(runs_root.parent))  # cwd != source-repo base → proves cwd-independence
        _seed_run(runs_root, "P", "run_rel", "running", ["sub/code.py"])
        owned = _active_run_owned_paths()
        expected = os.path.realpath(os.path.join(str(runs_root.parent), "sub/code.py"))
        assert expected in owned, \
            "relative files_touched must canonicalize against the deterministic base, not cwd"

    def test_gate_denies_relative_owned_file_end_to_end(self, runs_root, monkeypatch):
        # The whole point: a relative-stored active-run file, committed via a path
        # whose _pending_commit_paths realpath matches, must DENY (not fail open).
        # Base is deterministic (_ACTIVE_RUN_GLOB_ROOT=runs_root.parent), cwd-independent.
        monkeypatch.chdir(str(runs_root.parent))  # cwd != source-repo base → proves independence
        rel = "sub/code.py"
        realp = os.path.realpath(os.path.join(str(runs_root.parent), rel))
        _seed_run(runs_root, "P", "run_rel", "running", [rel])
        monkeypatch.setattr(security_hooks, "_pending_commit_paths",
                            lambda cwd, cmd: {realp})
        gate = create_active_run_commit_gate({"sdk_session_id": "s"})
        res = _run(gate, "git commit -- sub/code.py")
        assert _is_deny(res) is True, \
            "relative files_touched must still be DENIED (the Gate-2 HIGH fix, daemon-safe base)"

    def test_absolute_entry_realpathed(self, runs_root, monkeypatch):
        import os as _os
        _seed_run(runs_root, "P", "run_abs", "running", ["/tmp/x/code.py"])
        owned = _active_run_owned_paths()
        assert _os.path.realpath("/tmp/x/code.py") in owned


# ── an active run's own REPORT.md (.aidlc/deliverables/<run_id>/) ──────────
class TestActiveRunDeliverable:
    """A hand-made commit of an ACTIVE run's review deliverable is the REPORT-only
    half of a split delivery: denied, even though B is never in files_touched."""

    def _git(self, repo, *args):
        import subprocess
        subprocess.run(["git", "-C", str(repo), *args], check=True,
                       capture_output=True, text=True)

    def _repo_with_staged_report(self, tmp_path, run_id):
        repo = tmp_path / "repo"
        repo.mkdir()
        for a in (("init", "-q"), ("config", "user.email", "t@t.t"),
                  ("config", "user.name", "t")):
            self._git(repo, *a)
        rep = repo / ".aidlc" / "deliverables" / run_id / "REPORT.md"
        rep.parent.mkdir(parents=True)
        rep.write_text("# Report\n")
        self._git(repo, "add", "-A")
        return repo

    def _commit(self, repo):
        gate = create_active_run_commit_gate({"sdk_session_id": "s"})
        return asyncio.run(gate(
            {"tool_name": "Bash", "tool_input": {"command": "git commit -m 'add report'"},
             "cwd": str(repo)}, None, None))

    def test_report_of_an_active_run_is_denied(self, tmp_path, monkeypatch):
        repo = self._repo_with_staged_report(tmp_path, "run_live")
        monkeypatch.setattr(security_hooks, "_ACTIVE_RUN_GLOB_ROOT", str(tmp_path / "ws"))
        _seed_run(tmp_path / "ws" / "Projects", "P", "run_live", "running", ["/elsewhere/x.py"])
        res = self._commit(repo)
        assert _is_deny(res)
        reason = res["hookSpecificOutput"]["permissionDecisionReason"]
        assert "REPORT.md" in reason and "pipeline_v1.py land" in reason and "run_live" in reason

    @pytest.mark.parametrize("status,run_id", [("completed", "run_live"),
                                               ("running", "run_other")])
    def test_finished_or_other_runs_report_is_not_this_gates_business(
            self, tmp_path, monkeypatch, status, run_id):
        repo = self._repo_with_staged_report(tmp_path, "run_live")
        monkeypatch.setattr(security_hooks, "_ACTIVE_RUN_GLOB_ROOT", str(tmp_path / "ws"))
        _seed_run(tmp_path / "ws" / "Projects", "P", run_id, status, [])
        assert self._commit(repo) == {"decision": "approve"}

    def test_deliverable_matcher(self):
        pending = {"/r/.aidlc/deliverables/run_a/REPORT.md", "/r/.aidlc/deliverables/run_b/x",
                   "/r/docs/.aidlc/deliverables.md", "/r/src/run_a/REPORT.md"}
        assert security_hooks._active_run_deliverables(pending, {"run_a"}) == \
            ["/r/.aidlc/deliverables/run_a/REPORT.md"]
        assert security_hooks._active_run_deliverables(pending, set()) == []

    def test_run_id_falls_back_to_the_run_dir_name(self, tmp_path, monkeypatch):
        monkeypatch.setattr(security_hooks, "_ACTIVE_RUN_GLOB_ROOT", str(tmp_path))
        _seed_run(tmp_path / "Projects", "P", "run_noid", "paused", ["/abs/a.py"])
        owned, ids = security_hooks._active_run_ownership()
        assert ids == {"run_noid"} and "/abs/a.py" in owned
