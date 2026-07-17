"""Tests for bind_project() — the CREATE→BIND→PULL orchestration (run_8a3e7ebf).

Purpose: bind_repo() was an ORPHAN (0 production callers — only tests + a python -c
prose recipe in s_project-manager/SKILL.md). bind_project() is its first real caller:
it loads a project's bindings.yaml, loops ALL doc.bindings, and invokes bind_repo per
binding with PER-BINDING error isolation, so:
  - multi-repo: N bindings each processed independently (one failure never aborts the rest)
  - deferred: an internal/brazil binding → NotImplementedError → status='deferred'
  - negative: an unreachable/invalid clone → RuntimeError → status='failed' (NOT a crash)

Mock boundary: bind_repo is monkeypatched for the loop-logic tests (avoid real network
clone in unit tests). The real clone+index path is already covered by test_ddd_bindings.py
::test_bind_repo_clones_and_indexes.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from core.ddd_bindings import (
    BindResult,
    bind_project,  # RED: does not exist until bind_project is implemented
)


def _write_bindings(project_dir: Path, bindings: list[dict]) -> None:
    project_dir.mkdir(parents=True, exist_ok=True)
    (project_dir / "bindings.yaml").write_text(
        yaml.safe_dump({"bindings": bindings}), encoding="utf-8"
    )


def _external(repo: str, clone: str = "https://github.com/example/x.git") -> dict:
    return {
        "repo": repo, "kind": "external", "clone": clone, "worktree": None,
        "delivery_contract": {
            "remote_kind": "github-pr", "branch": "main",
            "review_path": "s_internal-crux-review", "auto_send": "on-clean-review",
        },
    }


def _internal(repo: str) -> dict:
    return {
        "repo": repo, "kind": "internal", "clone": f"brazil ws create --name {repo}",
        "worktree": None,
        "delivery_contract": {
            "remote_kind": "code-amazon-cr", "build_system": "brazil",
            "branch": "mainline", "version_set": f"{repo}/development",
            "review_path": "s_internal-crux-review", "auto_send": "on-clean-review",
        },
    }


class TestBindProjectOrchestration:
    def test_bind_project_is_bind_repo_caller(self):
        """DoD#1: bind_project must actually CALL bind_repo (closes the orphan)."""
        import inspect
        import core.ddd_bindings as m
        src = inspect.getsource(m.bind_project)
        assert "bind_repo(" in src, "bind_project must invoke bind_repo (it is the orphan's caller)"

    def test_multi_repo_each_processed_independently(self, tmp_path, monkeypatch):
        """DoD#3: N bindings each produce an outcome; the loop covers ALL of them."""
        proj = tmp_path / "Projects" / "Demo"
        _write_bindings(proj, [_external("adlc-workflows"), _external("second-repo")])

        calls = []

        def fake_bind_repo(binding, worktree_root=None):
            calls.append(binding.repo)
            return BindResult(worktree=f"/wt/{binding.repo}",
                              code_intel_db=f"/wt/{binding.repo}.db", node_count=42)

        monkeypatch.setattr("core.ddd_bindings.bind_repo", fake_bind_repo)
        outcomes = bind_project("Demo", projects_dir=tmp_path / "Projects")

        assert len(outcomes) == 2, f"both bindings must be processed, got {outcomes}"
        assert calls == ["adlc-workflows", "second-repo"], "loop must hit every binding in order"
        assert all(o.status == "bound" for o in outcomes)
        assert {o.repo for o in outcomes} == {"adlc-workflows", "second-repo"}
        assert next(o for o in outcomes if o.repo == "adlc-workflows").node_count == 42

    def test_one_failure_does_not_abort_the_rest(self, tmp_path, monkeypatch):
        """DoD#4 (isolation): a failing binding is captured as status=failed; siblings still bind."""
        proj = tmp_path / "Projects" / "Demo"
        _write_bindings(proj, [_external("good-a"), _external("bad-mid"), _external("good-b")])

        def fake_bind_repo(binding, worktree_root=None):
            if binding.repo == "bad-mid":
                raise RuntimeError("git clone failed for binding 'bad-mid'")
            return BindResult(worktree=f"/wt/{binding.repo}",
                              code_intel_db=f"/wt/{binding.repo}.db", node_count=7)

        monkeypatch.setattr("core.ddd_bindings.bind_repo", fake_bind_repo)
        outcomes = bind_project("Demo", projects_dir=tmp_path / "Projects")

        by_repo = {o.repo: o for o in outcomes}
        assert len(outcomes) == 3, "the bad binding must NOT abort the loop"
        assert by_repo["good-a"].status == "bound"
        assert by_repo["good-b"].status == "bound"
        assert by_repo["bad-mid"].status == "failed"
        assert by_repo["bad-mid"].error and "clone failed" in by_repo["bad-mid"].error

    def test_bad_url_is_failed_not_crash(self, tmp_path, monkeypatch):
        """DoD#4 (negative): a single bad binding → status=failed, bind_project does NOT raise."""
        proj = tmp_path / "Projects" / "Demo"
        _write_bindings(proj, [_external("only", clone="https://invalid.invalid/nope.git")])

        def fake_bind_repo(binding, worktree_root=None):
            raise RuntimeError("git clone failed (unreachable host)")

        monkeypatch.setattr("core.ddd_bindings.bind_repo", fake_bind_repo)
        # Must NOT raise:
        outcomes = bind_project("Demo", projects_dir=tmp_path / "Projects")
        assert len(outcomes) == 1
        assert outcomes[0].status == "failed"
        assert outcomes[0].error

    def test_internal_binding_is_deferred(self, tmp_path):
        """A real internal/brazil binding → bind_repo raises NotImplementedError → status=deferred.

        No mock — exercises the REAL bind_repo deferral branch (ddd_bindings.py:201).
        """
        proj = tmp_path / "Projects" / "Demo"
        _write_bindings(proj, [_internal("GCRAIDLCPreset")])
        outcomes = bind_project("Demo", projects_dir=tmp_path / "Projects")
        assert len(outcomes) == 1
        assert outcomes[0].status == "deferred", f"internal binding must defer, got {outcomes[0]}"
        assert outcomes[0].repo == "GCRAIDLCPreset"

    def test_mixed_set_bound_and_deferred(self, tmp_path, monkeypatch):
        """The AIDLC-shaped dogfood: one external (bound) + one internal (deferred) in one run."""
        proj = tmp_path / "Projects" / "Demo"
        _write_bindings(proj, [_external("adlc-workflows"), _internal("GCRAIDLCPreset")])

        real_bind_repo = None
        import core.ddd_bindings as m

        def fake_bind_repo(binding, worktree_root=None):
            # external → succeed; internal still raises NotImplementedError via the real guard
            if binding.kind == "internal" or binding.delivery_contract.build_system == "brazil":
                raise NotImplementedError("internal/brazil deferred")
            return BindResult(worktree=f"/wt/{binding.repo}",
                              code_intel_db=f"/wt/{binding.repo}.db", node_count=100)

        monkeypatch.setattr("core.ddd_bindings.bind_repo", fake_bind_repo)
        outcomes = bind_project("Demo", projects_dir=tmp_path / "Projects")
        by_repo = {o.repo: o for o in outcomes}
        assert by_repo["adlc-workflows"].status == "bound"
        assert by_repo["GCRAIDLCPreset"].status == "deferred"

    def test_missing_bindings_yaml_returns_empty(self, tmp_path):
        """A no-repo project (no bindings.yaml) → empty outcome list, no crash."""
        (tmp_path / "Projects" / "NoRepo").mkdir(parents=True)
        outcomes = bind_project("NoRepo", projects_dir=tmp_path / "Projects")
        assert outcomes == []

    def test_empty_bindings_list_returns_empty(self, tmp_path):
        """A bindings.yaml with an empty list → [] (load_bindings accepts it), no crash."""
        proj = tmp_path / "Projects" / "Demo"
        _write_bindings(proj, [])
        outcomes = bind_project("Demo", projects_dir=tmp_path / "Projects")
        assert outcomes == []

    def test_malformed_bindings_raises_value_error(self, tmp_path):
        """DOC-LEVEL failure (distinct from per-binding 'failed'): a malformed bindings.yaml
        makes bind_project raise ValueError naming the bad field — it does NOT get swallowed
        into a per-binding outcome (Gate-2 cross-path finding, run_8a3e7ebf). This is the
        path cmd_bind catches to exit 1."""
        proj = tmp_path / "Projects" / "Demo"
        proj.mkdir(parents=True, exist_ok=True)
        # a binding missing the required delivery_contract → load_bindings ValueError
        (proj / "bindings.yaml").write_text(
            yaml.safe_dump({"bindings": [{"repo": "x", "kind": "external", "clone": "u"}]}),
            encoding="utf-8",
        )
        with pytest.raises(ValueError) as ei:
            bind_project("Demo", projects_dir=tmp_path / "Projects")
        assert "delivery_contract" in str(ei.value), (
            f"ValueError must name the offending field, got {ei.value}")


class TestBindCliExitCode:
    """cmd_bind exit-code contract (Gate-2 cross-path finding, run_8a3e7ebf).

    Exercised via the real CLI subprocess so the argparse dispatch + sys.exit path
    are covered — the branch bind_project's unit tests structurally cannot reach.
    """

    REPO = Path("/Users/gawan/Desktop/SwarmAI-Workspace/swarmai")

    def _run_bind(self, project: str):
        import subprocess
        return subprocess.run(
            ["python", "backend/scripts/artifact_cli.py", "bind", "--project", project],
            cwd=self.REPO, capture_output=True, text=True, timeout=60,
        )

    def test_malformed_bindings_exits_1(self, tmp_path):
        """A malformed bindings.yaml → cmd_bind exits 1 with an error on stderr.

        Uses a throwaway project under the REAL Projects dir so the default
        PROJECTS_DIR resolution (no --projects-dir on the CLI) is exercised, then
        cleans it up.
        """
        import shutil
        proj = self.REPO.parent  # placeholder; resolved below
        # Resolve the real PROJECTS_DIR the CLI will use.
        import sys as _sys
        _sys.path.insert(0, str(self.REPO / "backend"))
        from jobs.paths import PROJECTS_DIR  # type: ignore
        proj = Path(PROJECTS_DIR) / "_ZZ_bind_exit_test"
        proj.mkdir(parents=True, exist_ok=True)
        try:
            (proj / "bindings.yaml").write_text(
                yaml.safe_dump({"bindings": [{"repo": "x", "kind": "external", "clone": "u"}]}),
                encoding="utf-8",
            )
            r = self._run_bind("_ZZ_bind_exit_test")
            assert r.returncode == 1, f"malformed bindings.yaml must exit 1, got {r.returncode}: {r.stdout}{r.stderr}"
            assert "error" in (r.stderr + r.stdout).lower()
        finally:
            shutil.rmtree(proj, ignore_errors=True)
