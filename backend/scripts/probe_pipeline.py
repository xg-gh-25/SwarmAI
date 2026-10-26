#!/usr/bin/env python3
"""probe_pipeline — product eval probes for the autonomous pipeline's own guards.

Contract: scripts/_probe_kit.py. Each probe builds a throwaway git repo, workspace
and data dir, and drives a real run through ``RunEngine``. Subcommands:
  stages     a direct run needs the user's request; BUILD does not complete without a
             validation report, a completion decided on an earlier stage is refused,
             and a failed test command keeps the run in TEST
  isolation  the run edits its own worktree; the user's checkout and HEAD are untouched
  budget     a stage that keeps failing its checks stops after its attempt budget and
             the run ends not-landed, with the user's HEAD unchanged
  no_revert  landing detects a branch that would put back an older version of a file
             someone else changed in the meantime
  owner      a session that does not own the run cannot drive it; the command changes
             nothing and says why
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
from pathlib import Path
from unittest import mock

from scripts._probe_kit import run, tempdir, verdict


def _git(repo, *args) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True).stdout.strip()


def _repo(root: Path) -> Path:
    repo = root / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "probe@swarmai.dev")
    _git(repo, "config", "user.name", "Probe")
    (repo / "a.py").write_text("value = 1\n")
    (repo / "tests").mkdir()
    (repo / "tests/test_x.py").write_text("def test_value(): pass\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "base")
    return repo


def _report(result="pass"):
    cmd = {"command": "python3 -m pytest tests/test_x.py", "cwd": ".", "result": result}
    if result != "pass":
        cmd["reason"] = "assertion failed"
    return {"reports": [{"repo": "repo", "commands": [cmd], "tests_changed": ["tests/test_x.py"],
                         "rationale": "test_value covers the change"}]}


@contextlib.contextmanager
def _env():
    """A throwaway repo / workspace / data dir, with worktrees kept out of the user's."""
    with tempdir() as d:
        data, ws = d / "data", d / "workspace"
        (ws / "Projects" / "P").mkdir(parents=True)
        env = {"SWARM_DATA_DIR": str(data), "SWARM_WORKSPACE": str(ws), "SWARMWS": str(ws),
               "SWARM_WORKTREE_ROOT": str(d / "worktrees"),
               "SWARM_WORKTREE_LEDGER": str(d / "ledger")}
        with mock.patch.dict(os.environ, env):
            yield d, data, ws, _repo(d)


def _create(data, ws, repo, run_id, requirement="probe change", session="s"):
    from core.run_protocol.engine import RunEngine

    return RunEngine.create(data_dir=data, workspace=ws, project="P", profile="direct",
                            requirement=requirement, session_id=session,
                            repos={"repo": str(repo)}, run_id=run_id)


def _change(engine) -> Path:
    wt = Path(engine.ensure_stage_worktrees()["repo"])
    (wt / "a.py").write_text("value = 2\n")
    return wt


# ── stages ────────────────────────────────────────────────────────────────────

def _stages_holds() -> tuple[bool, str]:
    with _env() as (_d, data, ws, repo):
        try:
            _create(data, ws, repo, "run_noreq", requirement="")
            return False, "a direct run was created without the user's request"
        except Exception:  # noqa: BLE001 — any refusal is the expected outcome
            pass
        engine = _create(data, ws, repo, "run_stages")
        _change(engine)
        if engine.stage_complete()["state"]["stage"] != "build":
            return False, "BUILD completed without a validation report"
        if engine.stage_complete(_report())["state"]["stage"] != "review":
            return False, "BUILD did not complete with a passing report"
        try:
            engine.stage_complete(_report(), expected_stage="build")
            return False, "a completion decided on BUILD completed REVIEW"
        except Exception:  # noqa: BLE001 — the stale completion is refused
            pass
        engine.review_start()
        if engine.stage_complete()["state"]["stage"] != "test":
            return False, "REVIEW with no reviewers did not reach TEST"
        failed = engine.stage_complete(_report("fail"))
        if failed["ok"] is not False or failed["state"]["stage"] != "test":
            return False, "a failed test command let the run leave TEST"
    return True, ""


def stages(negative: bool) -> int:
    from core.run_protocol import checks

    def broken():  # a stage with no checks: anything completes
        with mock.patch.object(checks, "stage_checks", return_value=()):
            return _stages_holds()
    return verdict("PIPE_STAGES", negative, _stages_holds, broken)


# ── isolation ─────────────────────────────────────────────────────────────────

def _isolation_holds(shared: bool) -> tuple[bool, str]:
    with _env() as (_d, data, ws, repo):
        head = _git(repo, "rev-parse", "HEAD")
        engine = _create(data, ws, repo, "run_iso")
        if shared:  # broken: the run is handed the user's checkout itself
            engine.ensure_stage_worktrees = lambda: {"repo": str(repo)}
        wt = _change(engine)
        if wt.resolve() == repo.resolve():
            return False, "the run edits the user's checkout instead of its own worktree"
        if (repo / "a.py").read_text() != "value = 1\n" or _git(repo, "status", "--porcelain"):
            return False, "the user's checkout changed while the run worked"
        if _git(repo, "rev-parse", "HEAD") != head:
            return False, "the user's HEAD moved"
    return True, ""


def isolation(negative: bool) -> int:
    return verdict("PIPE_ISOLATION", negative, lambda: _isolation_holds(False),
                   lambda: _isolation_holds(True))


# ── budget ────────────────────────────────────────────────────────────────────

def _budget_holds() -> tuple[bool, str]:
    with _env() as (_d, data, ws, repo):
        head = _git(repo, "rev-parse", "HEAD")
        engine = _create(data, ws, repo, "run_budget")
        _change(engine)
        for _ in range(3):  # three BUILD attempts with no validation report
            state = engine.stage_complete()["state"]
        if state["stage"] == "build":
            return False, "BUILD kept accepting attempts past its budget"
        final = engine.stage_complete()["state"]
        # the outcome is fixed here; only the background reflection remains
        if final.get("status") not in ("reflecting", "completed") or final.get("landing") != "not_landed":
            return False, f"the run ended {final.get('status')}/{final.get('landing')}, not not-landed"
        if _git(repo, "rev-parse", "HEAD") != head or (repo / "a.py").read_text() != "value = 1\n":
            return False, "a not-landed run changed the user's repo"
    return True, ""


def budget(negative: bool) -> int:
    from core.run_protocol import lifecycle

    def no_counter(run, event, failed):  # a failed attempt closes but is never counted
        run["open_attempt"] = None

    def broken():
        with mock.patch.object(lifecycle, "_failed_stage", no_counter):
            return _budget_holds()
    return verdict("PIPE_BUDGET", negative, _budget_holds, broken)


# ── no_revert ─────────────────────────────────────────────────────────────────

def _no_revert_holds() -> tuple[bool, str]:
    from scripts.check_no_revert import reverted_paths

    with tempdir() as d:
        repo = _repo(d)
        _git(repo, "branch", "work")
        (repo / "a.py").write_text("value = 3  # someone else's change\n")
        _git(repo, "commit", "-qam", "other session")
        _git(repo, "checkout", "-q", "work")
        (repo / "b.py").write_text("x = 1\n")
        _git(repo, "add", "b.py")
        _git(repo, "commit", "-qm", "my change")
        _git(repo, "rebase", "-q", "main")          # landing rebases before it checks
        _git(repo, "checkout", "-q", "main")
        clean = reverted_paths(str(repo), "work", "main")
        if clean:
            return False, f"a rebased branch that only adds a file was flagged: {clean}"
        _git(repo, "checkout", "-q", "work")
        (repo / "a.py").write_text("value = 1\n")    # puts the old version back
        _git(repo, "commit", "-qam", "revert by accident")
        _git(repo, "checkout", "-q", "main")
        reverted = reverted_paths(str(repo), "work", "main")
        if reverted != ["a.py"]:
            return False, f"a branch putting back an older a.py was not detected ({reverted})"
    return True, ""


def no_revert(negative: bool) -> int:
    import scripts.check_no_revert as cnr

    def broken():  # the branch's starting point is never found
        with mock.patch.object(cnr, "branch_start", return_value=None):
            return _no_revert_holds()
    return verdict("PIPE_NO_REVERT", negative, _no_revert_holds, broken)


# ── owner ─────────────────────────────────────────────────────────────────────

def _cli(argv, session) -> dict:
    from core.run_protocol.cli_main import main

    out = io.StringIO()
    with mock.patch.dict(os.environ, {"SWARM_APP_SESSION_ID": session}), \
            contextlib.redirect_stdout(out):
        main(argv)
    return json.loads(out.getvalue().strip().splitlines()[-1])


def _owner_holds(check_owner: bool) -> tuple[bool, str]:
    from core.run_protocol import cli_commands

    with _env() as (_d, data, ws, repo):
        engine = _create(data, ws, repo, "run_owner", session="owner-session")
        _change(engine)
        before = engine.projection()["stage"]
        events = len(engine.store.read_events())
        argv = ["stage-complete", "--project", "P", "--run-id", "run_owner",
                "--artifact", json.dumps(_report())]
        patch = (contextlib.nullcontext() if check_owner
                 else mock.patch.object(cli_commands, "_not_owner", return_value=None))
        with patch:
            got = _cli(argv, "intruder-session")
        after = engine.projection()["stage"]
        if got.get("code") != "not_owner":
            return False, f"another session drove the run (got {got.get('code') or got.get('ok')})"
        if after != before or len(engine.store.read_events()) != events:
            return False, "a refused command still changed the run"
    return True, ""


def owner(negative: bool) -> int:
    return verdict("PIPE_OWNER", negative, lambda: _owner_holds(True),
                   lambda: _owner_holds(False))


if __name__ == "__main__":
    raise SystemExit(run({"stages": stages, "isolation": isolation, "budget": budget,
                          "no_revert": no_revert, "owner": owner}))
