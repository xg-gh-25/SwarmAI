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


# A delivered run: PUSH-READY deliver + seeded reflect lessons. run-commit refuses
# until the lessons exist, so code and REPORT.md land in ONE commit.
_DELIVERED = [
    {"stage": "deliver", "status": "completed", "push_ready": True},
    {"stage": "reflect", "status": "recorded",
     "lessons": ["[pitfall] a durable lesson long enough to count"]},
]


def _src(files):
    """A commit's files minus this run's review deliverable (which rides along)."""
    return [f for f in files if not f.startswith(".aidlc/deliverables/")]


def _run_update(workspace, run_id, **kw):
    from scripts.artifact_cli import cmd_run_update
    from unittest.mock import patch
    # This mock must carry every flag cmd_run_update BARE-READS on the non-status path
    # (`if args.status:` guards force/reason, which this helper never reaches since it
    # passes status=None). The bare reads on that path are taste_decision/profile/
    # ddd_checksums/files_touched/add_required_expert. `add_required_expert` (argparse
    # default=None) was added with R1.5a and its bare read `if args.add_required_expert:`
    # was not reflected here — omitting it is a mock-drift AttributeError, not a code bug.
    # Add a flag here whenever cmd_run_update gains a NEW bare-read on the non-status path.
    args = Namespace(project="TestProject", run_id=run_id, status=kw.get("status"),
                     stage_json=kw.get("stage_json"), taste_decision=None, profile=None,
                     ddd_checksums=None, files_touched=kw.get("files_touched"),
                     add_required_expert=None,
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

    def test_a_non_converged_review_does_not_block_the_commit(self, workspace, capsys):
        """Gate-2 did not converge in 3 rounds and the root-cause review said SOUND:
        autonomous, so run-commit goes on (here it stops at the next gate, the empty
        files_touched) instead of refusing on push_ready. Any other reason blocks."""
        sound = {"action": "sound", "root_cause": "SOUND"}
        reason = "Gate-2 not converged after 3 rounds; root-cause review: SOUND — x"
        _write_run(workspace, "run_s", files_touched=[], stages=[
            {"stage": "deliver", "status": "completed", "push_ready": False,
             "push_ready_reason": reason, "gate2_outcome": sound}])
        r = _run_commit(workspace, "run_s")
        err = capsys.readouterr().err
        assert r.get("exit") == 2 and "files_touched" in err and "review_not_converged" in err
        _write_run(workspace, "run_s2", files_touched=["a.py"], stages=[
            {"stage": "deliver", "status": "completed", "push_ready": False,
             "push_ready_reason": reason + "; regression-classified failure", "gate2_outcome": sound}])
        r = _run_commit(workspace, "run_s2")
        assert r.get("exit") == 2 and "PUSH-READY" in capsys.readouterr().err
        from scripts.artifact_cli import _only_review_not_converged as only
        assert only({"push_ready_reason": reason, "gate2_outcome": sound})
        assert not only({"push_ready_reason": reason, "gate2_outcome": {"action": "redesign"}})
        assert not only({"push_ready_reason": "", "gate2_outcome": sound})
        assert not only(None)

    def test_refuses_when_files_touched_empty(self, workspace, capsys):
        _write_run(workspace, "run_e",
                   stages=_DELIVERED,
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
                   stages=_DELIVERED,
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
        # this run's file + its review deliverable, in the SAME commit
        assert last.splitlines() == [".aidlc/deliverables/run_g/REPORT.md", "mine.py"], \
            f"commit should contain only mine.py and its REPORT.md, got: {last!r}"

    def test_commit_ignores_pre_staged_foreign_file(self, workspace, tmp_path, capsys):
        """#1d (run_dd8411f6): the FOREIGN file is pre-STAGED into the index
        (not just working-tree dirty). A bare `git commit -m` would sweep the
        whole index → commit the parallel session's staged file + record it into
        commits[].files. The fix pathspec-scopes BOTH the commit AND the
        diff --cached to this run's real_files. The prior test left the sibling
        UNSTAGED (which a bare commit skips anyway), so it never exercised this
        bug — this one does."""
        repo = tmp_path / "prestage_repo"
        repo.mkdir()
        self._git(repo, "init", "-q")
        self._git(repo, "config", "user.email", "t@t.co")
        self._git(repo, "config", "user.name", "t")
        (repo / "mine.py").write_text("v1\n")
        (repo / "parallel.py").write_text("v1\n")
        self._git(repo, "add", "-A")
        self._git(repo, "commit", "-qm", "base")

        # This run edits mine.py; a parallel session edits AND STAGES parallel.py.
        (repo / "mine.py").write_text("v2 — my run\n")
        (repo / "parallel.py").write_text("v2 — OTHER session, pre-staged\n")
        self._git(repo, "add", "--", "parallel.py")  # ← pre-staged into the index

        _write_run(workspace, "run_prestage",
                   stages=_DELIVERED,
                   files_touched=[str(repo / "mine.py")])
        _run_commit(workspace, "run_prestage")

        result = json.loads(capsys.readouterr().out)
        assert result["pushed"] is False
        # The HEAD commit must contain ONLY mine.py — NOT the pre-staged parallel.py
        last = self._git(repo, "show", "--name-only", "--format=", "HEAD").stdout.strip()
        # this run's file + its review deliverable, in the SAME commit
        assert last.splitlines() == [".aidlc/deliverables/run_prestage/REPORT.md", "mine.py"], \
            f"commit must contain only mine.py and its REPORT.md, got: {last!r}"
        # parallel.py must remain STAGED (still in the index, uncommitted)
        staged = self._git(repo, "diff", "--cached", "--name-only").stdout
        assert "parallel.py" in staged, "parallel session's pre-staged file must stay staged, not committed"
        # committed[].files must NOT record the foreign file
        committed = result.get("committed", [])
        all_files = [f for c in committed for f in c.get("files", [])]
        assert "parallel.py" not in all_files, f"commits[].files leaked foreign file: {all_files}"
        assert any("mine.py" in f for f in all_files), f"this run's file must be recorded: {all_files}"

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
                   stages=_DELIVERED,
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
                   stages=_DELIVERED,
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
                        stages=_DELIVERED,
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
        assert _src(data["commits"][0]["files"]) == ["f.py"]

    def test_commits_dedup_across_calls(self, workspace, tmp_path, capsys):
        repo = self._setup_repo(tmp_path, "prepo2")
        rf = _write_run(workspace, "run_q",
                        stages=_DELIVERED,
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
                        stages=_DELIVERED,
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
        self._git(repo, "commit", "-qam", "per-cycle commit of the run's file\n\nRun: run_ab")
        expected_sha = self._git(repo, "rev-parse", "--short", "HEAD").stdout.strip()

        rf = _write_run(workspace, "run_ab",
                        stages=_DELIVERED,
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
        self._git(repo, "commit", "-qm", "ancient\n\nRun: run_ab2", env=env_old)
        rf = _write_run(workspace, "run_ab2",
                        stages=_DELIVERED,
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
        self._git(repo, "commit", "--amend", "-qm", "c\n\nRun: run_x")
        assert _lookup_already_committed(str(repo), [str(repo / "f.py")], "", "run_x") == []
        assert _lookup_already_committed(str(repo), [], "2020-01-01T00:00:00+00:00",
                                         "run_x") == []
        # the same commit IS found once the window is given, so the [] above is the
        # empty-since / no-files guard, not the run-id bound
        assert _lookup_already_committed(str(repo), [str(repo / "f.py")],
                                         "2020-01-01T00:00:00+00:00", "run_x")


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
                        stages=_DELIVERED,
                        files_touched=[str(repo / "real.py"), str(repo / "ghost.py")])
        _run_commit(workspace, "run_ghost")
        result = json.loads(capsys.readouterr().out)
        assert len(result["committed"]) == 1, \
            f"real.py must commit despite the ghost path: {result}"
        data = json.loads(rf.read_text())
        assert data.get("commits"), "commits[] must be populated (the Canvas surface source)"
        assert _src(data["commits"][0]["files"]) == ["real.py"], \
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
                        stages=_DELIVERED,
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
    violations, and it has no automatic enforcer of its own:
      • .git/hooks/prepare-commit-msg never runs (core.hooksPath = corporate
        git-defender, which REPLACES .git/hooks);
      • no PreToolUse commit hook fires — this module shells git out itself rather
        than an agent typing `git commit` through the Bash tool.
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
                   stages=_DELIVERED,
                   files_touched=[str(repo / "f.py")])
        _run_commit(workspace, "run_tr")
        assert json.loads(capsys.readouterr().out)["committed"], "nothing was committed"

        body = self._git(repo, "log", "-1", "--format=%B").stdout
        assert "Co-Authored-By: Swarm <swarm@swarmai.dev>" in body, (
            "the auto local-commit message must carry the project identity trailer — "
            f"nothing else will add it. Got:\n{body}"
        )

    def test_auto_commit_satisfies_the_trailer_classifier(
        self, workspace, tmp_path, capsys
    ):
        """Cross-bind the generator to check_commit_trailers.classify(): a drift in
        EITHER (message builder or REQUIRED_TRAILER) turns this red. The classifier is
        retained for local use; for THIS auto-commit path the trailer is built into the
        message by the generator itself (the PreToolUse commit_trailer_gate was removed
        run_d613bb27, and the CI trailer step was removed 2026-08-16) — so this test is
        the sole guard that the generated message stays trailer-correct."""
        import importlib.util
        import pathlib

        repo = self._repo_with_change(tmp_path, "citrailerrepo")
        _write_run(workspace, "run_ci",
                   stages=_DELIVERED,
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


class TestReviewDeliverableCheckin:
    """§4.9 Phase 3 块A: cmd_run_commit generates derivative B (the review
    deliverable) into the real repo's .aidlc/deliverables/<run_id>/ and stages it
    in the SAME commit as the code — so a reviewer sees code + context same-screen.
    Dual-store: ledger A (workspace .artifacts/) is untouched (A→B one-way).
    Covers DoD2 happy path (AC2). Boundaries (AC3) + content-safety (AC4) are
    separate test classes.
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

    def _land_eval_artifact(self, workspace, with_type=True):
        """B's content契约 is the EVALUATE requirements[] — land a resolvable
        evaluate artifact + manifest so cmd_run_commit's _load_artifact_for_metrics
        resolves it (the gate for B generation under the new contract).

        with_type=False drops the manifest entry's `type` field — reproduces a
        PRE-SCHEMA manifest entry (written before `type` was mandatory). B must
        STILL generate (the gate calls _load_artifact_for_metrics with
        expected_type=None, so run+id ownership alone resolves the artifact)."""
        art_dir = workspace / "Projects" / "TestProject" / ".artifacts"
        art_file = art_dir / "evaluation-20260704-art_evalB.json"
        art_file.write_text(json.dumps({
            "schema_version": 1,
            "requirements": [{"id": "FR-1", "statement": "ship the thing",
                              "work_class": "task", "source": {"kind": "user-stated", "ref": "req"},
                              "status": "confirmed",
                              "acceptance": [{"given": "g", "when": "w", "then": "t"}]}],
            "clarifications": [],
        }), encoding="utf-8")
        mf = art_dir / "manifest.json"
        manifest = {"artifacts": []}
        if mf.exists():
            try:
                manifest = json.loads(mf.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                manifest = {"artifacts": []}
        entry = {"id": "art_evalB", "file": art_file.name}
        if with_type:
            entry["type"] = "evaluation"
        manifest.setdefault("artifacts", []).append(entry)
        mf.write_text(json.dumps(manifest), encoding="utf-8")

    def test_B_generates_from_terminal_report_regardless_of_manifest_type(self, workspace, tmp_path, capsys):
        """B is a verbatim copy of ledger A's terminal REPORT.md — its generation is
        gated ONLY on that report existing on disk, NOT on resolving the evaluate
        artifact (the orphan requirements.json sidecar was removed, so the manifest's
        `type` field no longer gates B at all). A type-less manifest entry — once a
        meta-review HIGH regression because requirements.json resolution rejected it —
        is now simply irrelevant to B: the single REPORT.md still generates."""
        repo = self._repo_with_change(tmp_path, "preschemarepo")
        run_id = "run_pre"
        self._land_eval_artifact(workspace, with_type=False)  # NO type field — must NOT matter now
        run_file = _write_run(
            workspace, run_id,
            stages=[{"stage": "evaluate", "status": "completed", "artifact_id": "art_evalB"},
                    {"stage": "deliver", "status": "completed", "push_ready": True}],
            files_touched=[str(repo / "f.py")],
        )
        # deliver.md generates REPORT.md before run-commit → land one for the copy-gate.
        # Must carry a SUBSTANTIVE §9 (a real cmd_run_report projection always does) or
        # the §9-degenerate B-copy gate (run_7c30c517) correctly skips B — orthogonal to
        # this test's point (type-less manifest irrelevant to B).
        (run_file.parent / "REPORT.md").write_text(
            "# Report\n## 需求溯源审计\nFR-1\n## 9. Lessons\n- [note] projected §9\n")
        _run_commit(workspace, run_id)
        capsys.readouterr()
        bdir = repo / ".aidlc" / "deliverables" / run_id
        # B copied the terminal REPORT.md; the single projected report is all B carries.
        assert (bdir / "REPORT.md").is_file(), "B must generate when A has a terminal REPORT.md"
        assert not (bdir / "requirements.json").exists(), \
            "orphan requirements.json removed — a type-less manifest entry is now irrelevant to B"
        assert {p.name for p in bdir.iterdir()} == {"REPORT.md"}, \
            "B carries ONLY the projected REPORT.md"

    def test_deliverable_B_created_and_staged_in_same_commit(self, workspace, tmp_path, capsys):
        repo = self._repo_with_change(tmp_path, "srcrepo")
        run_id = "run_delivB"
        self._land_eval_artifact(workspace)
        # B is a verbatim COPY of A's terminal REPORT.md — which carries the §4.6
        # 需求溯源审计 (Intent→Requirement) section. deliver.md generates REPORT.md
        # (Pipeline Report step) BEFORE run-commit, so land one here for the copy-gate.
        run_file = _write_run(
            workspace, run_id,
            stages=[{"stage": "evaluate", "status": "completed", "artifact_id": "art_evalB"},
                    {"stage": "deliver", "status": "completed", "push_ready": True}],
            files_touched=[str(repo / "f.py")],
        )
        run_dir = run_file.parent
        (run_dir / "REPORT.md").write_text(
            "# Report\n## 需求溯源审计\n### 原始意图\n> ship the thing\nFR-1\n## 9. Lessons\n- x\n")

        _run_commit(workspace, run_id)
        capsys.readouterr()

        # B exists in the REAL repo under .aidlc/deliverables/<run_id>/ — audit file is
        # REPORT.md (a verbatim copy of A's terminal report; the §4.6 provenance section
        # lives inside it), NOT a separate REQUIREMENTS.md.
        bdir = repo / ".aidlc" / "deliverables" / run_id
        assert (bdir / "REPORT.md").is_file(), "B REPORT.md must be checked into the real repo"
        rep_body = (bdir / "REPORT.md").read_text()
        assert "需求溯源审计" in rep_body and "FR-1" in rep_body, \
            "B REPORT.md must carry the §4.6 provenance section (copied from A's terminal report)"
        # B carries EXACTLY the projected REPORT.md — the orphan sidecars (a separate
        # REQUIREMENTS.md machine-SRS AND the run-ref.txt back-pointer) are gone: the
        # provenance lives inside REPORT.md §4.6, and a cross-machine reader could never
        # resolve run-ref's workspace ledger path anyway.
        assert {p.name for p in bdir.iterdir()} == {"REPORT.md"}, \
            "B must carry ONLY REPORT.md (no REQUIREMENTS.md, no run-ref.txt)"

        # B is in the SAME commit as the code (HEAD commit touched BOTH f.py and B).
        head_files = self._git(repo, "show", "--name-only", "--format=", "HEAD").stdout
        assert "f.py" in head_files, "code file must be in the commit"
        assert f".aidlc/deliverables/{run_id}/REPORT.md" in head_files, \
            "B must be in the SAME commit as the code (§4.9 same-screen review)"

        # Ledger A (workspace .artifacts/) is UNTOUCHED — A→B one-way, A durable.
        assert run_dir.is_dir(), "ledger A run dir must remain"

    def test_workspace_only_run_skips_B_no_404(self, workspace, tmp_path, capsys):
        """§4.9 边界②: if the only resolved root IS the daemon workspace (non-git /
        workspace-resident code), B is NOT written into the workspace (that is the
        404 bug). Deliver via ledger A instead."""
        # Make the workspace itself a git repo so files_touched resolves to it,
        # and point a touched file INTO the workspace (= the daemon workspace root).
        self._git(workspace, "init", "-q")
        self._git(workspace, "config", "user.email", "t@t.co")
        self._git(workspace, "config", "user.name", "t")
        wsfile = workspace / "ws_code.py"
        wsfile.write_text("x=1\n")
        self._git(workspace, "add", "-A")
        self._git(workspace, "commit", "-qm", "base")
        wsfile.write_text("x=2\n")

        run_id = "run_wsonly"
        run_file = _write_run(
            workspace, run_id,
            stages=_DELIVERED,
            files_touched=[str(wsfile)],
        )
        (run_file.parent / "REPORT.md").write_text("# Report\n")
        _run_commit(workspace, run_id)
        capsys.readouterr()

        # No B dir created anywhere under the workspace .aidlc (would be the 404 dir).
        assert not (workspace / ".aidlc" / "deliverables" / run_id).exists(), \
            "B must NOT be written into the daemon workspace (§4.9 边界④ 404 defense)"

    def test_faillloud_autogen_failure_BLOCKS_commit_no_code_committed(self, workspace, tmp_path, capsys):
        """FR-2 integration (R28: the fail-loud BLOCK path must be execution-tested):
        when A has NO terminal REPORT.md AND auto-generation itself raises (真异常 —
        corrupt/unreadable ledger), cmd_run_commit must EXIT 2 with a COMMIT-BLOCKED
        signal AND leave the code UNcommitted — never ship code without the audit
        projection. Mutation-sound: delete the caller's sentinel check → this goes
        from exit-2/uncommitted to exit-0/committed."""
        from unittest.mock import patch
        repo = self._repo_with_change(tmp_path, "faillloudrepo")
        self._land_eval_artifact(workspace)  # eval artifact in workspace ledger
        run_id = "run_faillloud"
        run_file = _write_run(
            workspace, run_id,
            stages=[{"stage": "evaluate", "status": "completed", "artifact_id": "art_evalB"},
                    {"stage": "deliver", "status": "completed", "push_ready": True}],
            files_touched=[str(repo / "f.py")],
        )
        # NO REPORT.md on disk → auto-gen path taken; force it to RAISE (true anomaly).
        assert not (run_file.parent / "REPORT.md").is_file(), "precondition: no REPORT.md"
        head_before = self._git(repo, "rev-parse", "HEAD").stdout.strip()
        with patch("scripts.artifact_cli.cmd_run_report", side_effect=RuntimeError("corrupt ledger")):
            r = _run_commit(workspace, run_id)
        err = capsys.readouterr().err
        assert r.get("exit") == 2, f"fail-loud auto-gen failure must BLOCK (exit 2); got {r}"
        assert "COMMIT BLOCKED" in err, f"must emit COMMIT-BLOCKED signal; stderr={err}"
        # The decisive assertion: NO code was committed (HEAD unchanged).
        head_after = self._git(repo, "rev-parse", "HEAD").stdout.strip()
        assert head_after == head_before, \
            "fail-loud BLOCK must leave code UNcommitted — HEAD moved, meaning code shipped without audit"
        assert not (repo / ".aidlc" / "deliverables" / run_id).exists(), \
            "no B dir on a blocked commit"


# ── Tracked deletions are commits, not ghosts ─────────────────────────────────
class TestRunCommitTrackedDeletion:
    """A run that DELETES a tracked file must be able to commit the deletion.

    The ghost filter dropped every recorded path absent on disk — which is right
    for a hallucinated path and wrong for a deliberate deletion: a real run deleted
    two superseded components and run-commit could not commit the removal. The
    discriminator is git itself: absent on disk AND tracked (index or HEAD) is a
    deletion; absent and untracked is still a ghost.
    """

    def _git(self, repo, *a):
        return subprocess.run(["git", "-C", str(repo), *a],
                              capture_output=True, text=True, timeout=15)

    def _setup(self, tmp_path):
        repo = tmp_path / "delrepo"
        (repo / "pkg" / "old").mkdir(parents=True)
        self._git(repo, "init", "-q")
        self._git(repo, "config", "user.email", "t@t.co")
        self._git(repo, "config", "user.name", "t")
        (repo / "keep.py").write_text("v1\n")
        (repo / "pkg" / "gone.py").write_text("old\n")
        (repo / "pkg" / "old" / "only.py").write_text("old\n")
        self._git(repo, "add", "-A")
        self._git(repo, "commit", "-qm", "base")
        (repo / "keep.py").write_text("v2\n")
        (repo / "pkg" / "gone.py").unlink()
        (repo / "pkg" / "old" / "only.py").unlink()
        (repo / "pkg" / "old").rmdir()  # its parent dir is gone too
        return repo

    def test_tracked_deletions_are_committed(self, workspace, tmp_path, capsys):
        repo = self._setup(tmp_path)
        rf = _write_run(workspace, "run_del",
                        stages=_DELIVERED,
                        files_touched=[str(repo / "keep.py"), str(repo / "pkg" / "gone.py"),
                                       str(repo / "pkg" / "old" / "only.py"),
                                       str(repo / "never_existed.py")])
        _run_commit(workspace, "run_del")
        result = json.loads(capsys.readouterr().out)
        assert len(result["committed"]) == 1, result
        names = self._git(repo, "show", "--name-status", "--format=", "HEAD").stdout.split("\n")
        assert "M\tkeep.py" in names
        assert "D\tpkg/gone.py" in names, f"deletion not committed: {names}"
        assert "D\tpkg/old/only.py" in names, f"deletion under a removed dir not committed: {names}"
        assert self._git(repo, "status", "--porcelain").stdout.strip() == ""
        files = json.loads(rf.read_text())["commits"][0]["files"]
        assert "pkg/gone.py" in files and "never_existed.py" not in files
        warnings = " ".join(result.get("warnings", []))
        assert "never_existed.py" in warnings, "an untracked missing path is still reported as a ghost"


    def test_pathspec_magic_entries_are_ghosts_not_tracked(self, workspace, tmp_path, capsys,
                                                           monkeypatch):
        """A recorded ':/' or glob must never count as "tracked" and reach git add.

        git reads a path argument as a pathspec, so `ls-files --error-unmatch -- ':/'`
        succeeds and `git add -- ':/'` stages the whole tree. The tracked check is
        literal; these entries name no tracked file and are dropped as ghosts.

        SAFETY: run-commit resolves a RELATIVE path against the process cwd's repo.
        cwd is moved INTO the temp repo first, so if the literal check ever regresses
        the damage lands in the throwaway repo — never in the real one the test
        suite runs from (an earlier version of this test, run against a regressed
        build, committed the real working tree).
        """
        repo = self._setup(tmp_path)
        monkeypatch.chdir(repo)
        monkeypatch.setenv("SWARM_DATA_DIR", str(tmp_path / "no_data_dir"))
        (repo / "unrelated_untracked.py").write_text("another session's work\n")
        rf = _write_run(workspace, "run_magic",
                        stages=_DELIVERED,
                        files_touched=[str(repo / "keep.py"), ":/", "pkg/*.py",
                                       str(repo / "*.py")])
        _run_commit(workspace, "run_magic")
        result = json.loads(capsys.readouterr().out)
        committed = self._git(repo, "show", "--name-only", "--format=", "HEAD").stdout.split()
        assert _src(committed) == ["keep.py"], f"only the real recorded file may commit: {committed}"
        status = self._git(repo, "status", "--porcelain").stdout
        assert "unrelated_untracked.py" in status, "an unrelated file must stay untouched"
        assert "pkg/gone.py" in status, "a deletion NOT recorded by this run must stay uncommitted"
        assert _src(json.loads(rf.read_text())["commits"][0]["files"]) == ["keep.py"]


    def test_recorded_directory_is_not_a_tracked_file(self, workspace, tmp_path, capsys,
                                                       monkeypatch):
        """A literal pathspec still matches everything under a directory; a recorded
        deleted DIRECTORY must not count as tracked and pull in every deletion below it."""
        repo = self._setup(tmp_path)
        monkeypatch.chdir(repo)
        monkeypatch.setenv("SWARM_DATA_DIR", str(tmp_path / "no_data_dir"))
        from scripts.artifact_cli import _path_is_tracked
        assert _path_is_tracked(str(repo / "pkg" / "gone.py"), str(repo)) is True
        assert _path_is_tracked(str(repo / "pkg"), str(repo)) is False
        assert _path_is_tracked("pkg/old", str(repo)) is False
        assert _path_is_tracked("../outside.py", str(repo)) is False
        assert _path_is_tracked("pkg/gone.py", str(repo)) is True, "repo-relative form"
        assert _path_is_tracked("./pkg//gone.py", str(repo)) is True, "unnormalized form"

    def test_recorded_existing_directory_is_never_staged(self, workspace, tmp_path, capsys,
                                                         monkeypatch):
        """'.' or an existing directory in files_touched must not reach `git add`."""
        repo = self._setup(tmp_path)
        monkeypatch.chdir(repo)
        monkeypatch.setenv("SWARM_DATA_DIR", str(tmp_path / "no_data_dir"))
        (repo / "unrelated_untracked.py").write_text("another session's work\n")
        _write_run(workspace, "run_dir",
                   stages=_DELIVERED,
                   files_touched=[str(repo / "keep.py"), str(repo), str(repo / "pkg")])
        _run_commit(workspace, "run_dir")
        committed = self._git(repo, "show", "--name-only", "--format=", "HEAD").stdout.split()
        assert _src(committed) == ["keep.py"], committed
        assert "unrelated_untracked.py" in self._git(repo, "status", "--porcelain").stdout


    def test_tracked_file_replaced_by_a_directory_is_not_staged(self, workspace, tmp_path,
                                                               capsys, monkeypatch):
        repo = self._setup(tmp_path)
        monkeypatch.chdir(repo)
        monkeypatch.setenv("SWARM_DATA_DIR", str(tmp_path / "no_data_dir"))
        (repo / "pkg" / "gone.py").mkdir()          # tracked path is now a directory
        (repo / "pkg" / "gone.py" / "inner.py").write_text("x\n")
        _write_run(workspace, "run_dirswap",
                   stages=_DELIVERED,
                   files_touched=[str(repo / "keep.py"), str(repo / "pkg" / "gone.py")])
        _run_commit(workspace, "run_dirswap")
        committed = self._git(repo, "show", "--name-only", "--format=", "HEAD").stdout.split()
        assert _src(committed) == ["keep.py"], committed


class TestForcedCommitIsRecorded:
    """run_39068a4a AC3: --force past the push_ready gate leaves a trace, so a forced
    commit is never indistinguishable from a qualified one."""

    def _git(self, repo, *a, env=None):
        return subprocess.run(["git", "-C", str(repo), *a],
                              capture_output=True, text=True, timeout=15, env=env)

    def _repo(self, tmp_path):
        import os
        repo = tmp_path / "frepo"
        repo.mkdir()
        self._git(repo, "init", "-q")
        self._git(repo, "config", "user.email", "t@t.co")
        self._git(repo, "config", "user.name", "t")
        (repo / "f.py").write_text("v1\n")
        self._git(repo, "add", "-A")
        env_old = {**os.environ, "GIT_AUTHOR_DATE": "2020-01-01T00:00:00",
                   "GIT_COMMITTER_DATE": "2020-01-01T00:00:00"}
        self._git(repo, "commit", "-qm", "base", env=env_old)
        (repo / "f.py").write_text("v2\n")
        return repo

    def test_forced_commit_is_marked_and_audited(self, workspace, tmp_path, capsys):
        repo = self._repo(tmp_path)
        rf = _write_run(workspace, "run_force1",
                        stages=[{"stage": "deliver", "status": "completed", "push_ready": False,
                                 "push_ready_reason": "regression not clean"}, _DELIVERED[1]],
                        files_touched=[str(repo / "f.py")])
        _run_commit(workspace, "run_force1", force=True)
        out = capsys.readouterr()
        assert "push_ready gate FORCE-BYPASSED" in out.err
        data = json.loads(rf.read_text())
        assert data["commits"][0]["forced_push_ready"] is True
        kinds = [i.get("kind") for i in data.get("interventions", [])]
        assert "push_ready_force_bypass" in kinds

    def test_qualified_commit_carries_no_force_mark(self, workspace, tmp_path, capsys):
        repo = self._repo(tmp_path)
        rf = _write_run(workspace, "run_force2",
                        stages=_DELIVERED,
                        files_touched=[str(repo / "f.py")])
        _run_commit(workspace, "run_force2", force=True)
        capsys.readouterr()
        data = json.loads(rf.read_text())
        assert "forced_push_ready" not in data["commits"][0]
        assert "push_ready_force_bypass" not in [i.get("kind") for i in data.get("interventions", [])]


def test_finalize_stage_json_keeps_the_push_ready_verdict(workspace):
    """The legacy COMPLETE call (`run-update --stage-json {"stage":"deliver",
    "status":"completed","outputs_surfaced":true}`) REPLACES the deliver record. It
    used to drop push_ready=false and its reason, so every finished run lost the
    verdict and its summary read as qualified (run_1907ca1d). The docs no longer use
    this call (the surface tool records its own evidence, run_a87bfe3f), but runs
    that still send it must keep the verdict."""
    rf = _write_run(workspace, "run_keep_pr", stages=[
        {"stage": "deliver", "status": "completed", "artifact_id": "art_d",
         "push_ready": False, "push_ready_reason": "no clean regression"}])
    _run_update(workspace, "run_keep_pr", stage_json=json.dumps(
        {"stage": "deliver", "status": "completed", "outputs_surfaced": True,
         "stage_doc_consumed": True}))
    rec = next(s for s in json.loads(rf.read_text())["stages"] if s["stage"] == "deliver")
    assert rec["outputs_surfaced"] is True
    assert rec["push_ready"] is False
    assert rec["push_ready_reason"] == "no clean regression"


def test_carried_push_ready_is_not_recapped_after_commit(workspace, monkeypatch):
    """Gate-2 red-team: a finalize that omits push_ready carries True forward; the
    MED-4 cap must not re-judge it (run-commit already relied on it)."""
    import scripts.artifact_cli as ac
    calls = []
    monkeypatch.setattr(ac, "_cap_legacy_push_ready", lambda *a, **k: calls.append(1))
    rf = _write_run(workspace, "run_keep_true", stages=[
        {"stage": "deliver", "status": "completed", "artifact_id": "art_d", "push_ready": True}])
    _run_update(workspace, "run_keep_true", stage_json=json.dumps(
        {"stage": "deliver", "status": "completed", "outputs_surfaced": True, "stage_doc_consumed": True}))
    assert calls == [], "a carried verdict was re-capped"
    rec = next(s for s in json.loads(rf.read_text())["stages"] if s["stage"] == "deliver")
    assert rec["push_ready"] is True
    _run_update(workspace, "run_keep_true", stage_json=json.dumps(
        {"stage": "deliver", "status": "completed", "push_ready": True, "stage_doc_consumed": True}))
    assert calls == [1], "an explicitly sent True must still be capped (MED-4)"


def test_explicit_push_ready_does_not_inherit_a_stale_reason(workspace, monkeypatch):
    import scripts.artifact_cli as ac
    monkeypatch.setattr(ac, "_cap_legacy_push_ready", lambda *a, **k: None)
    rf = _write_run(workspace, "run_unit", stages=[
        {"stage": "deliver", "status": "completed", "push_ready": False,
         "push_ready_reason": "old", "push_ready_capped": True}])
    _run_update(workspace, "run_unit", stage_json=json.dumps(
        {"stage": "deliver", "status": "completed", "push_ready": True, "stage_doc_consumed": True}))
    rec = next(s for s in json.loads(rf.read_text())["stages"] if s["stage"] == "deliver")
    assert rec["push_ready"] is True
    assert "push_ready_reason" not in rec and "push_ready_capped" not in rec


class TestOneDeliveryOneCommit:
    """A run's code and its review deliverable (REPORT.md) land in ONE commit.

    15 runs split into a code commit plus a REPORT-only commit with the same subject:
    run-commit at DELIVER skipped B because the reflect lessons were not seeded, then a
    later run-commit committed B alone. Now the first call refuses (nothing staged,
    exact fix in the error, --force does not bypass), and a repeat call never commits
    B on its own."""

    def _git(self, repo, *a):
        return subprocess.run(["git", "-C", str(repo), *a], capture_output=True, text=True)

    def _repo(self, tmp_path):
        repo = tmp_path / "onerepo"
        repo.mkdir()
        for args in (("init", "-q"), ("config", "user.email", "t@t.co"),
                     ("config", "user.name", "t")):
            self._git(repo, *args)
        (repo / "f.py").write_text("v1\n")
        self._git(repo, "add", "-A")
        self._git(repo, "commit", "-qm", "base")
        (repo / "f.py").write_text("v2\n")
        return repo

    @pytest.mark.parametrize("force", [False, True])
    def test_no_lessons_refuses_and_commits_nothing(self, workspace, tmp_path, capsys, force):
        repo = self._repo(tmp_path)
        _write_run(workspace, "run_nolessons",
                   stages=[{"stage": "deliver", "status": "completed", "push_ready": True}],
                   files_touched=[str(repo / "f.py")])
        r = _run_commit(workspace, "run_nolessons", force=force)
        err = capsys.readouterr().err
        assert r.get("exit") == 2
        assert "ONE commit" in err and "run-update" in err and "run-report" in err
        assert self._git(repo, "rev-list", "--count", "HEAD").stdout.strip() == "1"
        assert self._git(repo, "diff", "--cached", "--name-only").stdout == ""
        assert not (repo / ".aidlc" / "deliverables" / "run_nolessons" / "REPORT.md").exists()

    def test_code_and_report_share_one_sha_and_repeat_adds_none(self, workspace, tmp_path, capsys):
        repo = self._repo(tmp_path)
        rf = _write_run(workspace, "run_one", stages=_DELIVERED,
                        files_touched=[str(repo / "f.py")])
        _run_commit(workspace, "run_one")
        capsys.readouterr()
        files = self._git(repo, "show", "--name-only", "--format=", "HEAD").stdout.split()
        assert files == [".aidlc/deliverables/run_one/REPORT.md", "f.py"]
        head = self._git(repo, "rev-parse", "HEAD").stdout.strip()

        # REFLECT refreshes ledger A's report; a repeat run-commit must not commit B alone.
        report_a = rf.parent / "REPORT.md"
        report_a.write_text(report_a.read_text() + "\nrefreshed after REFLECT\n")
        _run_commit(workspace, "run_one")
        out = json.loads(capsys.readouterr().out)
        assert self._git(repo, "rev-parse", "HEAD").stdout.strip() == head, "no REPORT-only commit"
        assert self._git(repo, "status", "--porcelain").stdout == "", "B restored to its commit"
        assert any("not committed on its own" in w for w in out["warnings"]), out["warnings"]
