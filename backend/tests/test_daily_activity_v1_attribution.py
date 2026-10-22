"""Contract test: DailyActivity commit attribution consumes Pipeline v1 output.

Producer (real code, not a hand-written fixture): ``RunEngine.create`` makes a
v1 run, ``landing.commit_repos`` lands it exactly as both v1 landing paths do
(progress at ``<run_dir>/artifacts/landing-progress-*.json``), and
``RunEngine.write_projection`` publishes ``run.json``.

Consumer: ``hooks.daily_activity_hook._session_run_evidence`` +
``_get_session_git_commits``. Properties:
- the owning session gets the landed commit, found on the repo's main line;
- another session gets nothing, even with a git-writing call at that second
  absent and touched files present;
- the run's own landing is attributed with no call windows at all (run agents,
  not the session's Bash calls, made it).
If v1 renames run.json fields or moves the progress files, this fails.
"""

from __future__ import annotations

import subprocess
from datetime import datetime, timedelta

from core.run_protocol import landing
from core.run_protocol.engine import RunEngine


def _git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True,
                          stdout=subprocess.PIPE, text=True).stdout.strip()


def test_v1_landed_commit_is_attributed_to_its_session_only(tmp_path, monkeypatch):
    from hooks.daily_activity_hook import _get_session_git_commits, _session_run_evidence

    data, workspace = tmp_path / "data", tmp_path / "workspace"
    (workspace / "Projects" / "P").mkdir(parents=True)
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "a@b.c")
    _git(repo, "config", "user.name", "T")
    (repo / "a.py").write_text("old\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "base")
    monkeypatch.setenv("SWARM_DATA_DIR", str(data))
    monkeypatch.setenv("SWARM_WORKTREE_ROOT", str(tmp_path / "worktrees"))
    monkeypatch.setenv("SWARM_WORKTREE_LEDGER", str(tmp_path / "ledger"))
    engine = RunEngine.create(data_dir=data, workspace=workspace, project="P",
                              profile="direct", session_id="session_mine",
                              repos={"repo": str(repo)}, run_id="run_v1contract")

    # Land a change the way v1 does: commit-tree on base + merge --ff-only.
    base = _git(repo, "rev-parse", "HEAD")
    (repo / "a.py").write_text("new\n")
    _git(repo, "add", "a.py")
    tree = _git(repo, "write-tree")
    _git(repo, "reset", "-q", "--hard", base)
    commits = landing.commit_repos(
        run_id=engine.run_id, integration_roots={"repo": str(repo)},
        bases={"repo": base}, bindings={"repo": {"delivery_tree": tree}},
        message="feat: the v1 landing",
        progress_path=engine.run_dir / "artifacts" / "landing-progress-normal.json")
    engine.write_projection()
    landed = commits["repo"]
    assert _git(repo, "rev-parse", "HEAD") == landed

    shas, _files, ids = _session_run_evidence("session_mine", workspace)
    assert landed in shas and "run_v1contract" in ids

    since = datetime.now() - timedelta(hours=1)
    mine = _get_session_git_commits(repo, since, session_shas=shas,
                                    session_run_ids=ids, call_windows=[])
    assert len(mine) == 1 and mine[0].startswith(landed[:7]) and "v1 landing" in mine[0]

    o_shas, o_files, o_ids = _session_run_evidence("session_other", workspace)
    assert not o_shas and not o_ids
    assert _get_session_git_commits(
        repo, since, session_files=[str(repo / "a.py")], session_shas=o_shas,
        session_run_ids=o_ids, call_windows=[]) == []
