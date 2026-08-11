"""Tests for A1 auto local-commit (run_76932250).

Covers:
- files_touched dedup-append via `run-update --files-touched`
- run-commit gates: refuse when not push_ready / empty files_touched
- run-commit stages ONLY this run's files (git add -- <path>, never -A) so a
  parallel session's dirty file is NOT swept in (R29)
- run-commit NEVER pushes (no remote interaction; pushed:false always)
"""
import json
import subprocess
from argparse import Namespace

import pytest


@pytest.fixture
def workspace(tmp_path):
    (tmp_path / "Projects" / "TestProject" / ".artifacts" / "runs").mkdir(parents=True)
    return tmp_path


def _write_run(workspace, run_id, *, status="running", profile="bugfix",
               stages=None, files_touched=None):
    run_dir = workspace / "Projects" / "TestProject" / ".artifacts" / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    data = {
        "id": run_id, "project": "TestProject", "requirement": "Fix the thing",
        "profile": profile, "status": status, "stages": stages or [],
        "taste_decisions": [], "created_at": "2026-07-04T00:00:00+00:00",
        "updated_at": "2026-07-04T00:00:00+00:00",
    }
    if files_touched is not None:
        data["files_touched"] = files_touched
    (run_dir / "run.json").write_text(json.dumps(data), encoding="utf-8")
    return run_dir / "run.json"


def _run_update(workspace, run_id, **kw):
    from scripts.artifact_cli import cmd_run_update
    from unittest.mock import patch
    args = Namespace(project="TestProject", run_id=run_id, status=kw.get("status"),
                     stage_json=kw.get("stage_json"), taste_decision=None, profile=None,
                     ddd_checksums=None, files_touched=kw.get("files_touched"),
                     force_checkpoint=False)
    with patch("scripts.artifact_cli._get_workspace", return_value=workspace):
        cmd_run_update(args, reg=None)


def _run_commit(workspace, run_id, force=False):
    from scripts.artifact_cli import cmd_run_commit
    from unittest.mock import patch
    args = Namespace(project="TestProject", run_id=run_id, force=force)
    captured = {}
    with patch("scripts.artifact_cli._get_workspace", return_value=workspace):
        try:
            cmd_run_commit(args, reg=None)
        except SystemExit as e:
            captured["exit"] = e.code
    return captured


class TestFilesTouchedDedupAppend:
    def test_appends_and_dedups_across_calls(self, workspace):
        _write_run(workspace, "run_a", files_touched=None)
        _run_update(workspace, "run_a", files_touched='["a.py","b.py"]')
        _run_update(workspace, "run_a", files_touched='["b.py","c.py"]')  # b dup
        data = json.loads((workspace / "Projects/TestProject/.artifacts/runs/run_a/run.json").read_text())
        assert data["files_touched"] == ["a.py", "b.py", "c.py"], data["files_touched"]

    def test_drops_empty_and_nonstring(self, workspace):
        _write_run(workspace, "run_b", files_touched=[])
        _run_update(workspace, "run_b", files_touched='["x.py","", "  ", 123]')
        data = json.loads((workspace / "Projects/TestProject/.artifacts/runs/run_b/run.json").read_text())
        assert data["files_touched"] == ["x.py"]

    def test_rejects_non_array(self, workspace):
        _write_run(workspace, "run_c")
        with pytest.raises(SystemExit):
            _run_update(workspace, "run_c", files_touched='{"not":"array"}')


class TestRunCommitGates:
    def test_refuses_when_not_push_ready(self, workspace, capsys):
        # deliver stage exists but push_ready=false
        _write_run(workspace, "run_d",
                   stages=[{"stage": "deliver", "status": "completed", "push_ready": False}],
                   files_touched=["a.py"])
        r = _run_commit(workspace, "run_d")
        assert r.get("exit") == 2
        assert "PUSH-READY" in capsys.readouterr().err

    def test_refuses_when_files_touched_empty(self, workspace, capsys):
        _write_run(workspace, "run_e",
                   stages=[{"stage": "deliver", "status": "completed", "push_ready": True}],
                   files_touched=[])
        r = _run_commit(workspace, "run_e")
        assert r.get("exit") == 2
        assert "files_touched" in capsys.readouterr().err

    def test_force_overrides_push_ready_gate(self, workspace):
        # not push_ready, but --force + empty files still blocked on files (gate order)
        _write_run(workspace, "run_f",
                   stages=[{"stage": "deliver", "status": "completed", "push_ready": False}],
                   files_touched=[])
        r = _run_commit(workspace, "run_f", force=True)
        assert r.get("exit") == 2  # still blocked — files_touched empty


class TestRunCommitPathspecOnly:
    """The core R29 guarantee: only THIS run's files get committed, never -A."""

    def _git(self, repo, *a):
        return subprocess.run(["git", "-C", str(repo), *a],
                              capture_output=True, text=True, timeout=15)

    def test_commits_only_run_files_not_parallel_session_edit(self, workspace, tmp_path, capsys):
        # Build a real git repo with two files
        repo = tmp_path / "srcrepo"
        repo.mkdir()
        self._git(repo, "init", "-q")
        self._git(repo, "config", "user.email", "t@t.co")
        self._git(repo, "config", "user.name", "t")
        (repo / "mine.py").write_text("v1\n")
        (repo / "parallel.py").write_text("v1\n")
        self._git(repo, "add", "-A")
        self._git(repo, "commit", "-qm", "base")

        # Now: this run edits mine.py; a "parallel session" edits parallel.py
        (repo / "mine.py").write_text("v2 — my run\n")
        (repo / "parallel.py").write_text("v2 — OTHER session, must NOT be committed\n")

        _write_run(workspace, "run_g",
                   stages=[{"stage": "deliver", "status": "completed", "push_ready": True}],
                   files_touched=[str(repo / "mine.py")])
        _run_commit(workspace, "run_g")

        out = capsys.readouterr().out
        result = json.loads(out)
        # committed exactly one repo, and parallel.py is still dirty (uncommitted)
        assert result["pushed"] is False
        status = self._git(repo, "status", "--porcelain").stdout
        assert "parallel.py" in status, "parallel session's edit must remain uncommitted"
        assert "mine.py" not in status, "this run's file must be committed (clean)"
        # the last commit touched ONLY mine.py
        last = self._git(repo, "show", "--name-only", "--format=", "HEAD").stdout.strip()
        assert last == "mine.py", f"commit should contain only mine.py, got: {last!r}"

    def test_warns_about_untracked_working_tree_changes(self, workspace, tmp_path, capsys):
        # A "forgotten record" must surface as a warning (parallel.py dirty but not
        # in files_touched) — and the run's own file must NOT be falsely warned.
        repo = tmp_path / "warnrepo"
        repo.mkdir()
        self._git(repo, "init", "-q")
        self._git(repo, "config", "user.email", "t@t.co")
        self._git(repo, "config", "user.name", "t")
        (repo / "mine.py").write_text("v1\n")
        (repo / "parallel.py").write_text("v1\n")
        self._git(repo, "add", "-A")
        self._git(repo, "commit", "-qm", "base")
        (repo / "mine.py").write_text("v2\n")
        (repo / "parallel.py").write_text("v2 other\n")

        _write_run(workspace, "run_w",
                   stages=[{"stage": "deliver", "status": "completed", "push_ready": True}],
                   files_touched=[str(repo / "mine.py")])  # absolute path
        _run_commit(workspace, "run_w")
        result = json.loads(capsys.readouterr().out)
        warns = " ".join(result["warnings"])
        assert "parallel.py" in warns, f"forgotten parallel edit must be warned: {result['warnings']}"
        assert "mine.py" not in warns, f"the run's own file must NOT be flagged untracked: {result['warnings']}"

    def test_never_pushes(self, workspace, tmp_path, capsys):
        repo = tmp_path / "srcrepo2"
        repo.mkdir()
        self._git(repo, "init", "-q")
        self._git(repo, "config", "user.email", "t@t.co")
        self._git(repo, "config", "user.name", "t")
        (repo / "f.py").write_text("a\n")
        self._git(repo, "add", "-A")
        self._git(repo, "commit", "-qm", "base")
        (repo / "f.py").write_text("b\n")
        _write_run(workspace, "run_h",
                   stages=[{"stage": "deliver", "status": "completed", "push_ready": True}],
                   files_touched=[str(repo / "f.py")])
        _run_commit(workspace, "run_h")
        result = json.loads(capsys.readouterr().out)
        assert result["pushed"] is False
        assert "user-initiated" in result["note"].lower() or "push" in result["note"].lower()


class TestRunCommitPersistsCommits:
    """G1 (run_f8494370): run-commit writes committed shas to run.json.commits[]
    so the retro-analytics dashboard can trace which run made which commit.
    Gate-1 fix: the write RE-READS run.json fresh + merges ONLY `commits`, so a
    concurrent stage update is not clobbered by a stale write-back."""

    def _git(self, repo, *a, env=None):
        return subprocess.run(["git", "-C", str(repo), *a],
                              capture_output=True, text=True, timeout=15, env=env)

    def _setup_repo(self, tmp_path, name):
        import os
        repo = tmp_path / name
        repo.mkdir()
        self._git(repo, "init", "-q")
        self._git(repo, "config", "user.email", "t@t.co")
        self._git(repo, "config", "user.name", "t")
        (repo / "f.py").write_text("v1\n")
        self._git(repo, "add", "-A")
        # Backdate the setup 'base' commit BEFORE the run's created_at (2026-07-04,
        # set by _write_run) so the fixture matches production: pre-existing commits
        # predate the run start and are NOT credited by FIX B's --since window. The
        # run's OWN change (v2, committed at wall-clock-now) is the only in-window commit.
        env_old = {**os.environ, "GIT_AUTHOR_DATE": "2020-01-01T00:00:00",
                   "GIT_COMMITTER_DATE": "2020-01-01T00:00:00"}
        self._git(repo, "commit", "-qm", "base", env=env_old)
        (repo / "f.py").write_text("v2\n")
        return repo

    def test_commits_persisted_to_run_json(self, workspace, tmp_path, capsys):
        repo = self._setup_repo(tmp_path, "prepo")
        rf = _write_run(workspace, "run_p",
                        stages=[{"stage": "deliver", "status": "completed", "push_ready": True}],
                        files_touched=[str(repo / "f.py")])
        _run_commit(workspace, "run_p")
        result = json.loads(capsys.readouterr().out)
        assert len(result["committed"]) == 1
        expected_sha = result["committed"][0]["sha"]
        # THE G1 ASSERTION: the sha is now queryable in run.json
        data = json.loads(rf.read_text())
        assert "commits" in data, "run.json must persist commits[] after run-commit"
        assert len(data["commits"]) == 1
        assert data["commits"][0]["sha"] == expected_sha
        assert data["commits"][0]["files"] == ["f.py"]

    def test_commits_dedup_across_calls(self, workspace, tmp_path, capsys):
        repo = self._setup_repo(tmp_path, "prepo2")
        rf = _write_run(workspace, "run_q",
                        stages=[{"stage": "deliver", "status": "completed", "push_ready": True}],
                        files_touched=[str(repo / "f.py")])
        _run_commit(workspace, "run_q")
        capsys.readouterr()
        # 2nd run-commit with nothing new staged → no new commit → no dup row
        _run_commit(workspace, "run_q")
        capsys.readouterr()
        data = json.loads(rf.read_text())
        assert len(data.get("commits", [])) == 1, "no duplicate commit row on re-run"

    def test_write_does_not_clobber_concurrent_field(self, workspace, tmp_path, capsys):
        # Simulate a concurrent stage update landing AFTER cmd_run_commit read
        # run_state but BEFORE it writes back. The fresh re-read must preserve it.
        repo = self._setup_repo(tmp_path, "prepo3")
        rf = _write_run(workspace, "run_r",
                        stages=[{"stage": "deliver", "status": "completed", "push_ready": True}],
                        files_touched=[str(repo / "f.py")])
        from unittest.mock import patch
        from scripts import artifact_cli as ac
        orig_reader = ac.Path.read_text
        state = {"n": 0}

        def _inject(self, *a, **k):
            txt = orig_reader(self, *a, **k)
            # After cmd_run_commit's FIRST read of this run.json, a concurrent
            # writer adds a new field. The G1 fresh re-read must see it.
            if self == rf and state["n"] == 0:
                state["n"] = 1
                d = json.loads(txt)
                d["concurrent_marker"] = "from_parallel_stage"
                rf.write_text(json.dumps(d), encoding="utf-8")
            return txt

        with patch.object(ac.Path, "read_text", _inject):
            _run_commit(workspace, "run_r")
        capsys.readouterr()
        data = json.loads(rf.read_text())
        assert data.get("concurrent_marker") == "from_parallel_stage", \
            "G1 write must re-read fresh and NOT clobber a concurrent field update"
        assert len(data.get("commits", [])) == 1, "commits still persisted"


class TestRunCommitAlreadyCommitted:
    """FIX B (run_c526d393): when the agent per-cycle-commits (BUILD.md:428), the
    files are ALREADY on main by run-commit time → `git add` stages nothing.
    Previously run-commit only WARNed → commits[] stayed empty → the completion
    gate mis-read the run as uncommitted_source and BLOCKED it. Now run-commit
    finds the REAL run-scoped commit and records it, so completion is credited.
    R29 / no-mis-attribution: bounded by files_touched pathspec + reachable-from-HEAD
    + --since=created_at."""

    def _git(self, repo, *a, env=None):
        return subprocess.run(["git", "-C", str(repo), *a],
                              capture_output=True, text=True, timeout=15, env=env)

    def _init(self, tmp_path, name):
        import os
        repo = tmp_path / name
        repo.mkdir()
        self._git(repo, "init", "-q")
        self._git(repo, "config", "user.email", "t@t.co")
        self._git(repo, "config", "user.name", "t")
        return repo

    def test_already_committed_files_are_credited(self, workspace, tmp_path, capsys):
        """The bug repro: file committed per-cycle → commits[] populated (not empty)."""
        repo = self._init(tmp_path, "abrepo")
        (repo / "f.py").write_text("v1\n")
        self._git(repo, "add", "-A")
        self._git(repo, "commit", "-qm", "base")
        # run starts at created_at (default 2026-07-04 in _write_run); commit AFTER it
        (repo / "f.py").write_text("v2 — the run's real change\n")
        self._git(repo, "commit", "-qam", "per-cycle commit of the run's file")
        expected_sha = self._git(repo, "rev-parse", "--short", "HEAD").stdout.strip()

        rf = _write_run(workspace, "run_ab",
                        stages=[{"stage": "deliver", "status": "completed", "push_ready": True}],
                        files_touched=[str(repo / "f.py")])
        _run_commit(workspace, "run_ab")
        data = json.loads(rf.read_text())
        # THE FIX-B ASSERTION: the already-made commit is credited, commits[] non-empty
        assert data.get("commits"), "per-cycle-committed file must be credited (commits[] non-empty)"
        shas = {c.get("sha") for c in data["commits"]}
        assert expected_sha in shas, f"the real run commit {expected_sha} must be recorded, got {shas}"
        # and its files are run-scoped (⊆ files_touched, repo-relative)
        assert all("f.py" in c.get("files", []) for c in data["commits"])

    def test_commit_before_run_start_is_NOT_credited(self, workspace, tmp_path, capsys):
        """SECURITY (AC2): a commit OLDER than created_at must NOT be credited —
        prevents crediting pre-existing/sibling work outside this run's window."""
        repo = self._init(tmp_path, "abrepo2")
        (repo / "f.py").write_text("ancient\n")
        self._git(repo, "add", "-A")
        # backdate WELL before the run's created_at (2026-07-04)
        env_old = {**__import__("os").environ,
                   "GIT_AUTHOR_DATE": "2020-01-01T00:00:00",
                   "GIT_COMMITTER_DATE": "2020-01-01T00:00:00"}
        self._git(repo, "commit", "-qm", "ancient", env=env_old)
        rf = _write_run(workspace, "run_ab2",
                        stages=[{"stage": "deliver", "status": "completed", "push_ready": True}],
                        files_touched=[str(repo / "f.py")])
        _run_commit(workspace, "run_ab2")
        data = json.loads(rf.read_text())
        # tree clean + only a pre-run commit exists → nothing credited → gate will block
        assert not data.get("commits"), "a pre-run-start commit must NOT be credited (AC2)"

    def test_lookup_helper_fails_closed_without_since(self, tmp_path):
        """_lookup_already_committed with empty run_since → [] (never a HEAD guess)."""
        from scripts.artifact_cli import _lookup_already_committed
        repo = self._init(tmp_path, "abrepo3")
        (repo / "f.py").write_text("x\n")
        self._git(repo, "add", "-A")
        self._git(repo, "commit", "-qm", "c")
        assert _lookup_already_committed(str(repo), [str(repo / "f.py")], "") == []
        assert _lookup_already_committed(str(repo), [], "2020-01-01T00:00:00+00:00") == []


# ── PR-review surface (run_b8ea6d5c): LOCAL_PR.md REMOVED; finish batch = rail rows ──
class TestNoLocalPR:
    """LOCAL_PR.md generation was removed (run_b8ea6d5c): the aggregated doc had no
    review value. The finish deliverable is per-file PR-review ROWS in the Canvas
    OUTPUTS rail, emitted by the surface_run_outputs tool (build_surface_events →
    orchestrator observe-emit), NOT a doc written by the run-commit CLI subprocess."""

    def test_write_local_pr_is_gone(self):
        import scripts.artifact_cli as ac
        assert not hasattr(ac, "_write_local_pr"), (
            "LOCAL_PR.md generation must be removed; the finish deliverable is now "
            "Canvas OUTPUTS rows via surface_run_outputs"
        )

    def test_build_surface_events_emits_source_final_rows_from_commits(self, tmp_path):
        """The replacement: build_surface_events reads a run's committed files and
        yields one file_changed(kind=source-final) event per file (deduped)."""
        import json
        import sys
        sys.path.insert(0, "backend")
        from core.ui_actions import build_surface_events

        run_dir = tmp_path / "Projects" / "P" / ".artifacts" / "runs" / "run_z"
        run_dir.mkdir(parents=True)
        (run_dir / "run.json").write_text(json.dumps({
            "commits": [
                {"repo": "/repo", "files": ["desktop/src/a.tsx", "backend/b.py"]},
                {"repo": "/repo", "files": ["desktop/src/a.tsx", "c.md"]},  # a.tsx dup
            ]
        }))
        events = build_surface_events("run_z", workspace_root=str(tmp_path))
        assert len(events) == 3, "deduped across commits (a.tsx once)"
        assert all(e["type"] == "file_changed" for e in events)
        assert all(e["kind"] == "source-final" for e in events)
        assert all(e["operation"] == "written" for e in events)
        paths = {e["path"] for e in events}
        assert paths == {"desktop/src/a.tsx", "backend/b.py", "c.md"}

    def test_build_surface_events_empty_when_no_commits(self, tmp_path):
        import json
        import sys
        sys.path.insert(0, "backend")
        from core.ui_actions import build_surface_events

        run_dir = tmp_path / "Projects" / "P" / ".artifacts" / "runs" / "run_none"
        run_dir.mkdir(parents=True)
        (run_dir / "run.json").write_text(json.dumps({"commits": []}))
        # No commits AND no REPORT.md on disk → still empty (the REPORT append is
        # exists-guarded).
        assert build_surface_events("run_none", workspace_root=str(tmp_path)) == []


# ── Run-commit ghost-path robustness (run_14e560ed PART 1 — root fix) ──────────
class TestRunCommitGhostPathRobustness:
    """A hallucinated files_touched path (recorded by BUILD but not on disk) must
    NOT poison the whole batch. `git add -- <real> <ghost>` fails with exit 128 and
    stages NOTHING — the root cause of empty commits[] on source-changing runs. The
    fix stages ONLY git-resolvable paths, so the real files still commit and
    commits[] populates (which is what the Canvas finish-surface reads)."""

    def _git(self, repo, *a):
        return subprocess.run(["git", "-C", str(repo), *a],
                              capture_output=True, text=True, timeout=15)

    def _setup_repo(self, tmp_path, name):
        repo = tmp_path / name
        repo.mkdir()
        self._git(repo, "init", "-q")
        self._git(repo, "config", "user.email", "t@t.co")
        self._git(repo, "config", "user.name", "t")
        (repo / "real.py").write_text("v1\n")
        self._git(repo, "add", "-A")
        self._git(repo, "commit", "-qm", "base")
        (repo / "real.py").write_text("v2 — this run\n")
        return repo

    def test_mixed_real_and_ghost_commits_the_real_one(self, workspace, tmp_path, capsys):
        # files_touched has a REAL dirty file + a HALLUCINATED path that doesn't exist.
        # Pre-fix: `git add -- real.py ghost.py` → exit 128 → nothing staged → no
        # commit → commits[] empty. Post-fix: real.py commits, ghost dropped.
        repo = self._setup_repo(tmp_path, "ghostrepo")
        rf = _write_run(workspace, "run_ghost",
                        stages=[{"stage": "deliver", "status": "completed", "push_ready": True}],
                        files_touched=[str(repo / "real.py"), str(repo / "ghost.py")])
        _run_commit(workspace, "run_ghost")
        result = json.loads(capsys.readouterr().out)
        assert len(result["committed"]) == 1, \
            f"real.py must commit despite the ghost path: {result}"
        data = json.loads(rf.read_text())
        assert data.get("commits"), "commits[] must be populated (the Canvas surface source)"
        assert data["commits"][0]["files"] == ["real.py"], \
            f"only the real file committed, ghost dropped: {data['commits']}"
        # real.py is now clean (committed); the repo has no dangling ghost
        status = self._git(repo, "status", "--porcelain").stdout
        assert "real.py" not in status, "real.py must be committed (clean)"

    def test_exists_in_repo_is_cwd_safe_for_relative_paths(self, tmp_path, monkeypatch):
        # Gate-2 HIGH regression guard (mutation-sensitive): _path_exists_in_repo must
        # anchor a REPO-RELATIVE path to the resolved repo `root`, NOT the process cwd.
        # In a multi-repo run cwd equals only ONE root; a cwd-relative check
        # (the reverted `Path(f).exists()`) mis-drops a real file in another repo as a
        # ghost → empty commit → the exact regression this change fixes.
        from scripts.artifact_cli import _path_exists_in_repo
        repo = tmp_path / "somerepo"
        (repo / "sub").mkdir(parents=True)
        (repo / "sub" / "real.py").write_text("x\n")
        # Process cwd is deliberately ELSEWHERE (mimics a multi-repo run where cwd is
        # a DIFFERENT repo than this file's root).
        other = tmp_path / "elsewhere"
        other.mkdir()
        monkeypatch.chdir(other)
        # MUTATION PIN: a bare cwd-relative check would be False here (real.py is NOT
        # under cwd) — reverting _path_exists_in_repo to `Path(f).exists()` makes this
        # assertion RED. Anchored to root, it is correctly True.
        assert _path_exists_in_repo("sub/real.py", str(repo)) is True, \
            "repo-relative real file must be found via root, not cwd"
        assert (other / "sub" / "real.py").exists() is False, \
            "sanity: it is NOT under the process cwd (proves the cwd trap is real)"
        # A genuinely hallucinated relative path is still correctly a ghost.
        assert _path_exists_in_repo("sub/ghost.py", str(repo)) is False
        # Absolute paths are checked directly (unchanged behavior).
        assert _path_exists_in_repo(str(repo / "sub" / "real.py"), str(repo)) is True
        assert _path_exists_in_repo(str(repo / "nope.py"), str(repo)) is False

    def test_all_ghost_paths_commits_nothing_cleanly(self, workspace, tmp_path, capsys):
        # Every files_touched path is hallucinated → nothing to stage → no commit,
        # commits[] stays empty (honest), a WARN fires, and it does NOT crash.
        repo = self._setup_repo(tmp_path, "allghostrepo")
        rf = _write_run(workspace, "run_allghost",
                        stages=[{"stage": "deliver", "status": "completed", "push_ready": True}],
                        files_touched=[str(repo / "nope1.py"), str(repo / "nope2.py")])
        cap = _run_commit(workspace, "run_allghost")
        assert cap.get("exit") in (None, 0), "must exit cleanly, not crash"
        result = json.loads(capsys.readouterr().out)
        assert result["committed"] == [], "all-ghost → nothing committed"
        data = json.loads(rf.read_text())
        assert not data.get("commits"), "commits[] stays empty honestly (never a wrong file)"


# ── REPORT.md finish-surface append (run_14e560ed PART 2) ──────────────────────
class TestBuildSurfaceEventsReportAppend:
    """build_surface_events appends the run's REPORT.md LAST as a kind=knowledge
    event so the Canvas auto-opens ON the report (last-write-wins) and renders it as
    CONTENT (no baseRef → not a diff). Source files stay as rows before it."""

    def test_report_appended_last_when_present(self, tmp_path):
        import json
        import sys
        sys.path.insert(0, "backend")
        from core.ui_actions import build_surface_events

        run_dir = tmp_path / "Projects" / "P" / ".artifacts" / "runs" / "run_rep"
        run_dir.mkdir(parents=True)
        (run_dir / "run.json").write_text(json.dumps({
            "commits": [{"repo": "/repo", "files": ["backend/b.py", "desktop/a.tsx"]}],
        }))
        (run_dir / "REPORT.md").write_text("# Pipeline Report\n")
        events = build_surface_events("run_rep", workspace_root=str(tmp_path))
        # Source events + exactly one REPORT.md event, and it is LAST.
        report_events = [e for e in events if str(e.get("path", "")).endswith("REPORT.md")]
        assert len(report_events) == 1, f"exactly one REPORT.md event: {events}"
        last = events[-1]
        assert str(last["path"]).endswith("REPORT.md"), \
            f"REPORT.md must be the LAST event (last-write-wins auto-select): {events}"
        assert last["kind"] == "knowledge", "REPORT renders as knowledge (CONTENT, rail-kept)"
        assert last["relevance"] == "deliverable", "must pass the deliverable auto-pop gate"
        assert "baseRef" not in last, "no baseRef → renders CONTENT not diff"
        assert last["operation"] == "written"
        # Source events precede it, unchanged.
        assert events[0]["kind"] == "source-final"

    def test_no_report_event_when_absent(self, tmp_path, caplog):
        import json
        import logging
        import sys
        sys.path.insert(0, "backend")
        from core.ui_actions import build_surface_events

        run_dir = tmp_path / "Projects" / "P" / ".artifacts" / "runs" / "run_norep"
        run_dir.mkdir(parents=True)
        (run_dir / "run.json").write_text(json.dumps({
            "commits": [{"repo": "/repo", "files": ["backend/b.py"]}],
        }))
        # No REPORT.md written.
        with caplog.at_level(logging.WARNING, logger="core.ui_actions"):
            events = build_surface_events("run_norep", workspace_root=str(tmp_path))
        assert not any(str(e.get("path", "")).endswith("REPORT.md") for e in events), \
            "no REPORT event when the file is absent (exists-guard)"
        assert len(events) == 1 and events[0]["kind"] == "source-final", \
            "source events still returned"
        # LOUD-on-degradation: source rows present + REPORT absent → a WARNING fires
        # (surface ran before run-report) so the ordering hazard is observable, not silent.
        assert any("REPORT.md is absent" in r.message for r in caplog.records), \
            "must WARN when source rows exist but REPORT.md is missing (ordering hazard)"

    def test_no_warning_when_no_events_and_no_report(self, tmp_path, caplog):
        # A truly-empty run (no commits, no REPORT) must NOT warn — the loud signal is
        # ONLY for the degradation case (source emitted but report lost to ordering).
        import json
        import logging
        import sys
        sys.path.insert(0, "backend")
        from core.ui_actions import build_surface_events

        run_dir = tmp_path / "Projects" / "P" / ".artifacts" / "runs" / "run_empty2"
        run_dir.mkdir(parents=True)
        (run_dir / "run.json").write_text(json.dumps({"commits": []}))
        with caplog.at_level(logging.WARNING, logger="core.ui_actions"):
            events = build_surface_events("run_empty2", workspace_root=str(tmp_path))
        assert events == []
        assert not any("REPORT.md is absent" in r.message for r in caplog.records), \
            "no warning for a genuinely empty run (no source rows to lose a report against)"

    def test_report_appended_even_when_no_commits(self, tmp_path):
        # A knowledge/docs-only run (empty commits[]) still surfaces its REPORT.md.
        import json
        import sys
        sys.path.insert(0, "backend")
        from core.ui_actions import build_surface_events

        run_dir = tmp_path / "Projects" / "P" / ".artifacts" / "runs" / "run_reponly"
        run_dir.mkdir(parents=True)
        (run_dir / "run.json").write_text(json.dumps({"commits": []}))
        (run_dir / "REPORT.md").write_text("# Report\n")
        events = build_surface_events("run_reponly", workspace_root=str(tmp_path))
        assert len(events) == 1, f"just the REPORT event: {events}"
        assert str(events[0]["path"]).endswith("REPORT.md")
        assert events[0]["kind"] == "knowledge"


class TestAutoCommitCarriesProjectTrailer:
    """The auto local-commit is the code path that produced the 2026-08-11 trailer
    violations, and it is invisible to BOTH other enforcers:
      • .git/hooks/prepare-commit-msg never runs (core.hooksPath = corporate
        git-defender, which REPLACES .git/hooks);
      • security_hooks.create_commit_trailer_gate only sees a `git commit` an AGENT
        types through the Bash tool — this module shells git out itself.
    So the trailer must be built into the message, and this test reads the message
    off a REAL created commit (INV-5: a guard is not trusted until something proves
    it fires). 3 violations here cost an 18-commit rebase to repair.
    """

    def _git(self, repo, *a, env=None):
        return subprocess.run(["git", "-C", str(repo), *a],
                              capture_output=True, text=True, timeout=30, env=env)

    def _repo_with_change(self, tmp_path, name):
        repo = tmp_path / name
        repo.mkdir()
        self._git(repo, "init", "-q")
        self._git(repo, "config", "user.email", "t@t.co")
        self._git(repo, "config", "user.name", "t")
        (repo / "f.py").write_text("v1\n")
        self._git(repo, "add", "-A")
        self._git(repo, "commit", "-qm", "base")
        (repo / "f.py").write_text("v2\n")
        return repo

    def test_auto_commit_message_ends_with_the_project_trailer(
        self, workspace, tmp_path, capsys
    ):
        repo = self._repo_with_change(tmp_path, "trailerrepo")
        _write_run(workspace, "run_tr",
                   stages=[{"stage": "deliver", "status": "completed", "push_ready": True}],
                   files_touched=[str(repo / "f.py")])
        _run_commit(workspace, "run_tr")
        assert json.loads(capsys.readouterr().out)["committed"], "nothing was committed"

        body = self._git(repo, "log", "-1", "--format=%B").stdout
        assert "Co-Authored-By: Swarm <swarm@swarmai.dev>" in body, (
            "the auto local-commit message must carry the project identity trailer — "
            f"nothing else will add it. Got:\n{body}"
        )

    def test_auto_commit_satisfies_the_ci_trailer_gate(
        self, workspace, tmp_path, capsys
    ):
        """Cross-bind the generator to the CI checker: a drift in EITHER (message
        builder or REQUIRED_TRAILER) turns this red, instead of surfacing days later
        at push as a history-rewrite problem."""
        import importlib.util
        import pathlib

        repo = self._repo_with_change(tmp_path, "citrailerrepo")
        _write_run(workspace, "run_ci",
                   stages=[{"stage": "deliver", "status": "completed", "push_ready": True}],
                   files_touched=[str(repo / "f.py")])
        _run_commit(workspace, "run_ci")
        capsys.readouterr()

        script = pathlib.Path(__file__).resolve().parents[2] / "scripts" / "check_commit_trailers.py"
        spec = importlib.util.spec_from_file_location("cct_gen", script)
        cct = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cct)

        # classify() reads the BODY (git %b), matching how CI evaluates a commit.
        body = self._git(repo, "log", "-1", "--format=%b").stdout
        assert cct.classify(body) == "ok", (
            f"CI's own classifier rejects the message this code path produces: "
            f"{cct.classify(body)!r}\n{body}"
        )
