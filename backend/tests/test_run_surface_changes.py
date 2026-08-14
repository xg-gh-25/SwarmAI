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


class TestCompletionGateSourceAck:
    """AC4 — cmd_run_update --status completed BLOCKS when this run wrote source but
    delivery is incomplete. Two distinct block_reasons, and the DISTINCTION is what
    most of these tests pin (completion_gate.completion_surface_verdict):

      - ``uncommitted_source`` — files_touched is non-empty but NO committed file
        is run-scoped to it (nothing committed, or only sibling-session commits).
      - ``unsurfaced_source`` — a run-scoped commit exists but deliver lacks
        outputs_surfaced/local_pr_surfaced=true. Self-attestation: the CLI cannot
        observe the frontend dispatch, so the flag is agent-set.

    Never blocks when this run wrote no source: ``files_touched: []`` (explicitly
    none) or the field absent (legacy/in-flight → UNKNOWN, warn-only, resume-safe).

    NOTE on run-scoping (the reason two tests below assert a BLOCK rather than
    completion): every non-empty entry in files_touched counts as source — there is
    no docs/source split — so a run that declares source it never committed is
    blocked by ``uncommitted_source`` BEFORE the surface gate is ever reached. Those
    tests were originally written against an earlier gate that let a sibling-only
    commit fall through to completion; the C045 hardening made that a deliberate
    block ("Also fires when commits exist but none intersect THIS run's source"),
    so the run-scoping property is now proven BY the block_reason."""

    def _completed(self, workspace, run_id, stage_json=None):
        """Attempt run-update --status completed; return (exit_code_or_None, printed_json)."""
        from scripts.artifact_cli import cmd_run_update
        from unittest.mock import patch
        from argparse import Namespace
        import io, contextlib
        args = Namespace(project="TP", run_id=run_id, status="completed",
                         stage_json=stage_json, taste_decision=None, profile=None,
                         ddd_checksums=None, files_touched=None, force_checkpoint=False)
        buf = io.StringIO()
        code = None
        with patch("scripts.artifact_cli._get_workspace", return_value=workspace), \
             contextlib.redirect_stdout(buf):
            try:
                cmd_run_update(args, reg=None)
            except SystemExit as e:
                code = e.code
        return code, buf.getvalue()

    def _write_run(self, workspace, run_id, *, commits=None, files_touched=None,
                   local_pr_surfaced=None):
        import json
        run_dir = workspace / "Projects" / "TP" / ".artifacts" / "runs" / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        # A minimal-but-complete run that would otherwise pass the completion gate:
        # all bugfix stages done, deliver push_ready, REPORT.md present + >500 bytes.
        deliver_stage = {"stage": "deliver", "status": "completed",
                         "stage_doc_consumed": True, "push_ready": True,
                         "artifact_id": "art_x",
                         "adversarial_review": {"spawned": True, "evidence": "Agent tool"}}
        if local_pr_surfaced is not None:
            deliver_stage["local_pr_surfaced"] = local_pr_surfaced
        stages = [{"stage": s, "status": "completed", "stage_doc_consumed": True}
                  for s in ("evaluate", "think", "plan", "build", "review", "test")]
        stages.append(deliver_stage)
        stages.append({"stage": "reflect", "status": "completed",
                       "stage_doc_consumed": True,
                       "lessons": ["a real substantive lesson learned this run about X"]})
        data = {"id": run_id, "project": "TP", "requirement": "do a thing",
                "profile": "bugfix", "status": "running", "stages": stages,
                "taste_decisions": [], "created_at": "2026-08-04T00:00:00+00:00",
                "updated_at": "2026-08-04T00:00:00+00:00"}
        if commits is not None:
            data["commits"] = commits
        if files_touched is not None:
            data["files_touched"] = files_touched
        (run_dir / "run.json").write_text(json.dumps(data), encoding="utf-8")
        (run_dir / "REPORT.md").write_text("# Report\n" + ("x" * 600), encoding="utf-8")
        return run_dir

    def test_source_committed_but_no_ack_BLOCKS(self, tmp_path):
        (tmp_path / "Projects" / "TP" / ".artifacts" / "runs").mkdir(parents=True)
        # This run committed backend/foo.py AND recorded it in files_touched, but
        # deliver has no local_pr_surfaced → the PR was never surfaced → BLOCK.
        self._write_run(tmp_path, "run_a",
                        commits=[{"repo": "swarmai", "sha": "abc", "files": ["backend/foo.py"]}],
                        files_touched=["backend/foo.py"],
                        local_pr_surfaced=None)
        code, out = self._completed(tmp_path, "run_a")
        assert "local_pr" in out.lower() or "surface" in out.lower(), out
        # gate blocks: run must NOT be marked completed
        import json
        data = json.loads((tmp_path / "Projects/TP/.artifacts/runs/run_a/run.json").read_text())
        assert data["status"] != "completed", "run was completed despite unsurfaced source PR"

    def test_source_committed_and_acked_COMPLETES(self, tmp_path):
        (tmp_path / "Projects" / "TP" / ".artifacts" / "runs").mkdir(parents=True)
        self._write_run(tmp_path, "run_b",
                        commits=[{"repo": "swarmai", "sha": "abc", "files": ["backend/foo.py"]}],
                        files_touched=["backend/foo.py"],
                        local_pr_surfaced=True)
        self._completed(tmp_path, "run_b")
        import json
        data = json.loads((tmp_path / "Projects/TP/.artifacts/runs/run_b/run.json").read_text())
        assert data["status"] == "completed", "acked source run should complete"

    def test_knowledge_only_run_COMPLETES_no_false_block(self, tmp_path):
        (tmp_path / "Projects" / "TP" / ".artifacts" / "runs").mkdir(parents=True)
        # No commits at all (docs/knowledge-only) → gate must NOT fire.
        self._write_run(tmp_path, "run_c", commits=None, files_touched=None,
                        local_pr_surfaced=None)
        self._completed(tmp_path, "run_c")
        import json
        data = json.loads((tmp_path / "Projects/TP/.artifacts/runs/run_c/run.json").read_text())
        assert data["status"] == "completed", "knowledge-only run false-blocked"

    def _assert_blocked_as_uncommitted(self, tmp_path, run_id, out, why):
        """Assert the run was blocked as `uncommitted_source` and NOT `unsurfaced_source`.

        This pair is what discriminates a run-scoping regression. Both fixtures below
        have deliver_surfaced=False, so IF the committed file were wrongly credited to
        files_touched the commit check would pass and the gate would fall through to
        `unsurfaced_source` instead. Asserting the reason — not merely "blocked" —
        is therefore what pins the anchored match; a bare `!= "completed"` would stay
        green through exactly the regression this exists to catch."""
        import json
        assert "uncommitted_source" in out, f"{why}: expected uncommitted_source, got {out}"
        assert "unsurfaced_source" not in out, (
            f"{why}: committed file was CREDITED to files_touched (run-scoping "
            f"regression — the gate reached the surface check): {out}")
        data = json.loads((tmp_path / f"Projects/TP/.artifacts/runs/{run_id}/run.json").read_text())
        assert data["status"] != "completed", f"{why}: run completed despite a block"

    def test_sibling_only_commits_do_NOT_satisfy_this_runs_commit_requirement(self, tmp_path):
        # F3 run-scoping: commits exist but belong to a SIBLING session — none is
        # run-scoped to this run's files_touched. So this run declared source
        # (backend/mine.py) it never committed → uncommitted_source, and the sibling
        # commit must not be credited to it.
        (tmp_path / "Projects" / "TP" / ".artifacts" / "runs").mkdir(parents=True)
        self._write_run(tmp_path, "run_d",
                        commits=[{"repo": "swarmai", "sha": "abc", "files": ["sibling/other.py"]}],
                        files_touched=["backend/mine.py"],
                        local_pr_surfaced=None)
        _, out = self._completed(tmp_path, "run_d")
        self._assert_blocked_as_uncommitted(tmp_path, "run_d", out, "sibling-only commit")

    def test_explicit_no_source_with_sibling_commits_COMPLETES(self, tmp_path):
        # The still-true half of the old F3 case: THIS run wrote no source at all
        # (files_touched: [] — explicitly none, distinct from the field being absent),
        # while a sibling session's commits are recorded on the run. Nothing to commit
        # or surface → must complete. Pins the `[]` branch, which no other test covers
        # (test_knowledge_only_run_COMPLETES_no_false_block exercises the absent/None
        # UNKNOWN branch instead).
        (tmp_path / "Projects" / "TP" / ".artifacts" / "runs").mkdir(parents=True)
        self._write_run(tmp_path, "run_d2",
                        commits=[{"repo": "swarmai", "sha": "abc", "files": ["sibling/other.py"]}],
                        files_touched=[],
                        local_pr_surfaced=None)
        self._completed(tmp_path, "run_d2")
        import json
        data = json.loads((tmp_path / "Projects/TP/.artifacts/runs/run_d2/run.json").read_text())
        assert data["status"] == "completed", "a no-source run was false-blocked by sibling commits"

    def test_bare_basename_does_NOT_falsematch_deep_path(self, tmp_path):
        # Gate-2 LOW 1: a committed deep path "sub/config.py" must NOT be considered
        # run-scoped just because files_touched has an UNRELATED bare "config.py"
        # (_tail_match is DIRECTORY-anchored, never a bare basename). The block itself
        # is the proof: a false match would reach unsurfaced_source instead.
        (tmp_path / "Projects" / "TP" / ".artifacts" / "runs").mkdir(parents=True)
        self._write_run(tmp_path, "run_e",
                        commits=[{"repo": "swarmai", "sha": "abc", "files": ["sub/config.py"]}],
                        files_touched=["config.py"],  # bare basename, different file
                        local_pr_surfaced=None)
        _, out = self._completed(tmp_path, "run_e")
        self._assert_blocked_as_uncommitted(tmp_path, "run_e", out, "bare basename vs deep path")

    def test_dir_anchored_suffix_DOES_match_a_repo_relative_commit(self, tmp_path):
        # The other side of _tail_match: a repo-relative committed "backend/foo.py"
        # MUST match an absolute files_touched entry ending in "/backend/foo.py", or
        # every real run would false-block. Reaching unsurfaced_source (not
        # uncommitted_source) proves the commit check credited it.
        (tmp_path / "Projects" / "TP" / ".artifacts" / "runs").mkdir(parents=True)
        self._write_run(tmp_path, "run_e2",
                        commits=[{"repo": "swarmai", "sha": "abc", "files": ["backend/foo.py"]}],
                        files_touched=["/Users/x/repo/backend/foo.py"],
                        local_pr_surfaced=None)
        _, out = self._completed(tmp_path, "run_e2")
        assert "unsurfaced_source" in out, f"dir-anchored suffix failed to match: {out}"
        assert "uncommitted_source" not in out, out

    def test_same_call_status_and_ack_stagejson_COMPLETES(self, tmp_path):
        # Gate-2 MEDIUM 1: a SINGLE call `--status completed --stage-json
        # '{deliver...local_pr_surfaced:true}'` must NOT false-block. The gate reads
        # run_state (pre-update), so it must also honor the incoming stage_json ack.
        (tmp_path / "Projects" / "TP" / ".artifacts" / "runs").mkdir(parents=True)
        self._write_run(tmp_path, "run_f",
                        commits=[{"repo": "swarmai", "sha": "abc", "files": ["backend/foo.py"]}],
                        files_touched=["backend/foo.py"],
                        local_pr_surfaced=None)  # NOT pre-set — supplied in the same call
        self._completed(tmp_path, "run_f",
                        stage_json='{"stage":"deliver","status":"completed","local_pr_surfaced":true,"stage_doc_consumed":true}')
        import json
        data = json.loads((tmp_path / "Projects/TP/.artifacts/runs/run_f/run.json").read_text())
        assert data["status"] == "completed", "same-call ack false-blocked (MEDIUM 1 regression)"


class TestStageDocsWiring:
    """AC3 — deliver.md + complete.md must instruct open-canvas-file with the full
    run-scoped path + run-surface-changes, and NOT the stale open-canvas /
    'no open-file action' text."""

    def _docs(self):
        base = Path(__file__).resolve().parents[1] / "skills" / "s_autonomous-pipeline" / "stages"
        return (base / "deliver.md").read_text(), (base / "complete.md").read_text()

    def test_docs_use_surface_run_outputs_batch(self):
        # run_b8ea6d5c: the finish source deliverable is a batch of PR-review rail
        # rows via surface_run_outputs — NOT an aggregated LOCAL_PR.md (removed).
        deliver, complete = self._docs()
        for doc, name in ((deliver, "deliver.md"), (complete, "complete.md")):
            assert "surface_run_outputs" in doc, f"{name} must instruct the surface_run_outputs batch tool"
            assert "outputs_surfaced" in doc, f"{name} must record outputs_surfaced (the repurposed gate flag)"
            # LOCAL_PR must not be an INSTRUCTION anymore (a "removed run_b8ea6d5c" note is
            # fine). The stale instruction was `open-canvas-file` pointed at a LOCAL_PR.md
            # path — assert THAT specific pattern is gone, not the mere word.
            assert "runs/<run_id>/LOCAL_PR.md" not in doc, (
                f"{name} must not INSTRUCT opening the removed LOCAL_PR.md path")
            assert "local_pr_surfaced" not in doc, (
                f"{name} must use outputs_surfaced, not the old local_pr_surfaced flag")

    def test_docs_drop_stale_open_canvas_instruction(self):
        deliver, _ = self._docs()
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
