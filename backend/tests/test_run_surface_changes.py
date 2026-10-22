"""Tests for run-surface-changes (run_608a6217) — the git-based COMPLETE sweep.

The pipeline-finish Canvas review panel stands on GIT, not on tracing the process
that wrote each file (SDK filters sub-agent sidechain messages — types.py:1599, so
process-tracing structurally can't see sub-agent/CLI/hook writes; git sees them all).

At COMPLETE the sweep runs `git status --porcelain` on BOTH trees (SwarmWS workspace
+ the bound source repo), classifies each changed path via needs_human_review, and
returns {content, knowledge, source, process} buckets:
  - content/knowledge → pop to Canvas (DDD/design/memory/knowledge — normal workflow)
  - source            → aggregate into the LOCAL_PR (code — the one special case)
  - process           → machine noise, dropped

F1 (the Gate-1 critical fix this test exists to lock): git status returns paths
RELATIVE to each repo root. needs_human_review joins a non-absolute path against
SwarmWS root, so a source-repo-relative path ("backend/foo.py") would resolve to
<SwarmWS-root>/backend/foo.py → owning-tree=SwarmWS → misclassified as CONTENT
(popped per-file) instead of SOURCE (aggregated). The sweep MUST absolutize each
porcelain path against ITS OWN repo root before classifying.

NOTE (do not reintroduce the app-state dir name here): ci.yml --ignore-s any test
file mentioning it, assuming real-home dependence. This file has none — it drives
everything through tmp_path + a patched _get_workspace, and passes with an empty
HOME — but while the name sat in this docstring the whole file was invisible to CI,
which is how two stale fixtures here stayed red unnoticed. Write <SwarmWS-root>.
"""
import subprocess
from pathlib import Path

import pytest


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True,
                   capture_output=True, text=True)


def _init_repo(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "t@t.t")
    _git(root, "config", "user.name", "t")


class TestClassifyGitPaths:
    """classify_git_status_paths(repo_root, porcelain_paths, swarmws_root) →
    a list of (relative_path, ReviewVerdict) with paths ABSOLUTIZED against
    repo_root before classification (the F1 contract)."""

    def test_source_repo_relative_path_classifies_as_source_not_content(self, tmp_path):
        # F1 REGRESSION LOCK: a source-repo file, given as a repo-RELATIVE path
        # (exactly what `git status --porcelain` emits), must classify as SOURCE.
        # If the sweep forgets to absolutize against the source repo root, it
        # misclassifies as CONTENT (the bug). This is the tracer bullet.
        from core.run_surface_changes import classify_git_status_paths

        swarmws = tmp_path / "SwarmWS"
        (swarmws / "Projects" / "SwarmAI").mkdir(parents=True)
        # Bind a source repo via bindings.yaml so needs_human_review sees it as a worktree.
        src_repo = tmp_path / "src_repo"
        _init_repo(src_repo)
        (swarmws / "Projects" / "SwarmAI" / "bindings.yaml").write_text(
            "bindings:\n"
            "  - repo: swarmai\n"
            "    kind: external\n"
            "    clone: https://example.com/swarmai.git\n"
            f"    worktree: {src_repo}\n"
            "    delivery_contract:\n"
            "      remote_kind: github-pr\n"
            "      branch: main\n"
            "      review_path: PR\n"
            "      auto_send: 'false'\n",
            encoding="utf-8",
        )
        # Clear the worktree cache so the fresh binding is seen.
        from core.needs_human_review import clear_worktree_cache
        clear_worktree_cache()

        results = classify_git_status_paths(
            repo_root=src_repo,
            porcelain_paths=["backend/core/foo.py"],
            swarmws_root=swarmws,
        )
        assert len(results) == 1
        rel, verdict = results[0]
        assert rel == "backend/core/foo.py"
        assert verdict.kind == "source", (
            f"source-repo file misclassified as {verdict.kind!r} — the F1 "
            "absolutize-against-repo-root fix is missing"
        )

    def test_swarmws_knowledge_path_classifies_as_content_or_knowledge(self, tmp_path):
        from core.run_surface_changes import classify_git_status_paths
        from core.needs_human_review import clear_worktree_cache

        swarmws = tmp_path / "SwarmWS"
        (swarmws / "Knowledge" / "Designs").mkdir(parents=True)
        _init_repo(swarmws)
        clear_worktree_cache()

        results = classify_git_status_paths(
            repo_root=swarmws,
            porcelain_paths=["Knowledge/Designs/2026-08-04-x.md"],
            swarmws_root=swarmws,
        )
        assert len(results) == 1
        _, verdict = results[0]
        assert verdict.kind in ("content", "knowledge"), verdict.kind

    def test_machine_noise_classifies_as_process(self, tmp_path):
        from core.run_surface_changes import classify_git_status_paths
        from core.needs_human_review import clear_worktree_cache

        swarmws = tmp_path / "SwarmWS"
        (swarmws / ".context").mkdir(parents=True)
        _init_repo(swarmws)
        clear_worktree_cache()

        results = classify_git_status_paths(
            repo_root=swarmws,
            porcelain_paths=[".context/.eval-canary.json"],
            swarmws_root=swarmws,
        )
        assert len(results) == 1
        _, verdict = results[0]
        assert verdict.kind == "process", verdict.kind


class TestSweepRunChanges:
    """sweep_run_changes(swarmws_root) → SurfaceBuckets from REAL git status on
    BOTH the SwarmWS tree AND every bound source worktree (E2E, author-agnostic)."""

    def _make_two_tree_workspace(self, tmp_path):
        swarmws = tmp_path / "SwarmWS"
        (swarmws / "Projects" / "SwarmAI").mkdir(parents=True)
        (swarmws / "Knowledge" / "Designs").mkdir(parents=True)
        (swarmws / ".context").mkdir(parents=True)
        _init_repo(swarmws)
        src_repo = tmp_path / "src_repo"
        _init_repo(src_repo)
        (src_repo / "backend" / "core").mkdir(parents=True)
        (swarmws / "Projects" / "SwarmAI" / "bindings.yaml").write_text(
            "bindings:\n"
            "  - repo: swarmai\n"
            "    kind: external\n"
            "    clone: https://example.com/swarmai.git\n"
            f"    worktree: {src_repo}\n"
            "    delivery_contract:\n"
            "      remote_kind: github-pr\n"
            "      branch: main\n"
            "      review_path: PR\n"
            "      auto_send: 'false'\n",
            encoding="utf-8",
        )
        from core.needs_human_review import clear_worktree_cache
        clear_worktree_cache()
        return swarmws, src_repo

    def test_e2e_sweep_buckets_both_trees_authoragnostic(self, tmp_path):
        # E2E (real git status): a design doc in SwarmWS, a .py in the source repo,
        # a machine json in .context — all dirty. The sweep must bucket them by kind
        # regardless of which tree they live in.
        from core.run_surface_changes import sweep_run_changes

        swarmws, src_repo = self._make_two_tree_workspace(tmp_path)
        # Dirty a KNOWLEDGE file in SwarmWS (content), a machine json (process):
        (swarmws / "Knowledge" / "Designs" / "2026-08-04-plan.md").write_text("# plan\n")
        (swarmws / ".context" / ".eval-canary.json").write_text("{}\n")
        # Dirty a SOURCE file in the bound repo (source):
        (src_repo / "backend" / "core" / "foo.py").write_text("x = 1\n")

        buckets = sweep_run_changes(swarmws_root=swarmws)

        assert any("Knowledge/Designs/2026-08-04-plan.md" in p for p in buckets.content), buckets.as_dict()
        assert any("backend/core/foo.py" in p for p in buckets.source), buckets.as_dict()
        # .context/.eval-canary.json is machine noise → process, never surfaced.
        assert not any("eval-canary" in p for p in (buckets.content + buckets.knowledge + buckets.source)), buckets.as_dict()

    def test_e2e_sweep_source_file_is_NOT_in_content(self, tmp_path):
        # The whole point: a source file must NOT leak into the content bucket (it
        # would pop per-file mid-run instead of aggregating into the PR).
        from core.run_surface_changes import sweep_run_changes

        swarmws, src_repo = self._make_two_tree_workspace(tmp_path)
        (src_repo / "backend" / "core" / "foo.py").write_text("x = 1\n")
        buckets = sweep_run_changes(swarmws_root=swarmws)
        assert not any("foo.py" in p for p in buckets.content), (
            f"source file leaked into content bucket: {buckets.as_dict()}"
        )
        assert any("foo.py" in p for p in buckets.source), buckets.as_dict()


class TestPorcelainParse:
    """_porcelain_paths must parse git status -z correctly, incl. the rename/copy
    branch (R./C. records carry BOTH new + old path across a NUL boundary)."""

    def test_rename_yields_both_new_and_old_path(self, tmp_path):
        from core.run_surface_changes import _porcelain_paths
        repo = tmp_path / "repo"
        _init_repo(repo)
        (repo / "sub").mkdir()
        (repo / "sub" / "old.py").write_text("content here\n")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-qm", "init")
        _git(repo, "mv", "sub/old.py", "sub/new.py")
        paths = _porcelain_paths(repo)
        # Real -z rename format is "R  sub/new.py\\0sub/old.py" → BOTH paths.
        assert "sub/new.py" in paths, paths
        assert "sub/old.py" in paths, paths

    def test_untracked_and_modified_paths(self, tmp_path):
        from core.run_surface_changes import _porcelain_paths
        repo = tmp_path / "repo"
        _init_repo(repo)
        (repo / "tracked.py").write_text("v1\n")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-qm", "init")
        (repo / "tracked.py").write_text("v2\n")   # modified
        (repo / "fresh.md").write_text("new\n")     # untracked
        paths = _porcelain_paths(repo)
        assert "tracked.py" in paths, paths
        assert "fresh.md" in paths, paths


class TestGitErrorObservability:
    """Gate-2 MEDIUM 2: a git-status failure on one tree must be a KNOWN skipped
    tree (buckets.errors), never a silent all-clear."""

    def test_git_error_on_a_tree_is_recorded_not_silent(self, tmp_path, monkeypatch):
        from core.run_surface_changes import sweep_run_changes, _GitSweepError
        import core.run_surface_changes as rsc
        from core.needs_human_review import clear_worktree_cache

        swarmws = tmp_path / "SwarmWS"
        (swarmws / "Knowledge").mkdir(parents=True)
        _init_repo(swarmws)
        clear_worktree_cache()

        # Force the porcelain call to fail for the SwarmWS tree.
        def _boom(repo_root):
            raise _GitSweepError(str(repo_root), 128, "fatal: not a git repository")
        monkeypatch.setattr(rsc, "_porcelain_paths", _boom)

        buckets = sweep_run_changes(swarmws_root=swarmws)
        # The failed tree is RECORDED, not silently dropped as "clean".
        assert buckets.errors, "git error produced a silent all-clear (MEDIUM 2 regression)"
        assert any("SwarmWS" in e for e in buckets.errors), buckets.errors
        # errors surfaces in the JSON so the COMPLETE step sees the gap.
        assert "errors" in buckets.as_dict()


class TestStageDocsWiring:
    """The v1 deliver card must not restore the stale open-canvas instruction."""

    def _deliver_doc(self):
        base = Path(__file__).resolve().parents[1] / "skills" / "s_autonomous-pipeline" / "stages"
        return (base / "deliver.md").read_text()

    def test_docs_drop_stale_open_canvas_instruction(self):
        deliver = self._deliver_doc()
        assert "no `open-file` action" not in deliver, (
            "deliver.md still has the stale 'no open-file action' line"
        )
        # the OLD instruction 'open the Canvas panel via ... ui_action open-canvas`'
        # (empty-panel) must be gone — open-canvas-file replaces it for the PR.
        assert "the Canvas panel via the existing\n`ui_action open-canvas`" not in deliver
        assert "via the existing\n`ui_action open-canvas`" not in deliver


# run_cce6f4b9: TestSweepTurnDelta DELETED — sweep_turn_delta/porcelain_snapshot (the
# per-turn LIVE emit) were removed as the Canvas tab-isolation/trigger/cost regression.
# Live WRITE surfacing is now the EVENT-DRIVEN per-tool emit
# (StreamingOrchestrator._build_file_write_events); its behavior is covered by
# tests/test_orchestrator_surface_wiring.py (write emits on tool-result success,
# failed/non-review-worthy dropped, source kind carried for frontend gating). The
# author-agnostic pipeline-FINISH fallback (sweep_run_changes) remains covered by
# TestSweepRunChanges above.
