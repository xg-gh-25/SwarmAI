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

    def _git(self, repo, *a):
        return subprocess.run(["git", "-C", str(repo), *a],
                              capture_output=True, text=True, timeout=15)

    def _setup_repo(self, tmp_path, name):
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
        assert build_surface_events("run_none", workspace_root=str(tmp_path)) == []
