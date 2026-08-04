"""Tests for needs_human_review — the git-based review-trigger authority.

The FIRST and most load-bearing test (AC2b) is the 2026-08-02 regression guard:
an ABSOLUTE top-level SwarmWS deliverable MUST resolve to review_worthy=True. The
whole workspace lives under ~/.swarm-ai/, so any dot-segment scan on the ABSOLUTE
path would drop it (`.swarm-ai` is a hidden segment of every absolute path). This
suite proves the scan runs on the TREE-RELATIVE path instead.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from core.needs_human_review import (
    ReviewVerdict,
    _has_dot_segment,
    needs_human_review,
)


@pytest.fixture
def swarmws(tmp_path: Path) -> Path:
    """A throwaway SwarmWS git tree with a .gitignore mirroring the real one's shape."""
    root = tmp_path / ".swarm-ai" / "SwarmWS"  # NOTE: under a .swarm-ai dot-dir, like prod
    root.mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    (root / ".gitignore").write_text(
        "*.db\n*.lock\nnode_modules/\nconfig.json\n*_state.json\n.context/*.jsonl\n",
        encoding="utf-8",
    )
    (root / "Knowledge" / "Designs").mkdir(parents=True)
    (root / ".context").mkdir()
    (root / "Projects").mkdir()
    return root


def _write(root: Path, rel: str, body: str = "x") -> Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")
    return p


# ── AC2b: THE regression guard (must be first / most important) ──────────────

def test_absolute_toplevel_swarmws_deliverable_is_review_worthy(swarmws: Path):
    """FATAL-class guard (2026-08-02 regression): an ABSOLUTE deliverable path
    under ~/.swarm-ai/SwarmWS must be REVIEW, not dropped on the `.swarm-ai`
    hidden segment. Proves the dot-scan runs on the tree-relative path."""
    p = _write(swarmws, "Knowledge/Designs/foo.md")
    v = needs_human_review(str(p.resolve()), "written", swarmws_root=swarmws)
    assert v.review_worthy is True
    assert v.kind == "content"


def test_the_absolute_path_WOULD_have_a_dot_segment(swarmws: Path):
    """The complement of the guard: prove the bug is real — scanning the ABSOLUTE
    path DOES find a hidden `.swarm-ai` segment (so scanning it would wrongly drop
    the file). If this ever stops being true the guard above loses its meaning."""
    p = _write(swarmws, "Knowledge/Designs/foo.md")
    assert _has_dot_segment(str(p.resolve())) is True          # absolute → hidden seg present
    rel = str(p.resolve().relative_to(swarmws.resolve()))
    assert _has_dot_segment(rel) is False                      # relative → clean


# ── Layer 2: dot-segment (machine state that IS tracked/not-ignored) ─────────

def test_artifacts_runjson_is_process_never(swarmws: Path):
    """A run's machine-state record (run.json under .artifacts) is process — never
    surfaced. (This is the .artifacts=process rule; the REPORT.md carve-out below is
    the deliberate exception.)"""
    p = _write(swarmws, "Projects/SwarmAI/.artifacts/runs/x/run.json")
    v = needs_human_review(str(p.resolve()), "written", swarmws_root=swarmws)
    assert v.review_worthy is False
    assert v.kind == "process"


def test_artifacts_REPORT_md_surfaces_as_knowledge(swarmws: Path):
    """A pipeline REPORT.md under .artifacts/runs/ IS a user-facing deliverable and
    surfaces as knowledge — the deliberate _is_surfaceable_knowledge carve-out
    (run_b8ea6d5c). This corrects a stale pre-existing test that asserted `process`,
    predating that allowlist; the shipped contract intentionally surfaces REPORT.md
    for human review (aligned run_4de279ca)."""
    p = _write(swarmws, "Projects/SwarmAI/.artifacts/runs/x/REPORT.md")
    v = needs_human_review(str(p.resolve()), "written", swarmws_root=swarmws)
    assert v.review_worthy is True
    assert v.kind == "knowledge"


def test_context_json_is_process_never(swarmws: Path):
    p = _write(swarmws, ".context/.eval-canary.json")
    v = needs_human_review(str(p.resolve()), "written", swarmws_root=swarmws)
    assert v.review_worthy is False and v.kind == "process"


# ── Layer 1: .gitignore subtraction ──────────────────────────────────────────

def test_gitignored_db_is_not_review_worthy(swarmws: Path):
    p = _write(swarmws, "data.db")
    v = needs_human_review(str(p.resolve()), "written", swarmws_root=swarmws)
    assert v.review_worthy is False and v.kind == "process"


def test_gitignored_node_modules_not_review_worthy(swarmws: Path):
    p = _write(swarmws, "node_modules/pkg/index.js")
    v = needs_human_review(str(p.resolve()), "written", swarmws_root=swarmws)
    assert v.review_worthy is False


# ── kind classification (AC4 precedence) ─────────────────────────────────────

def test_memory_md_is_knowledge(swarmws: Path):
    p = _write(swarmws, "MEMORY.md")
    v = needs_human_review(str(p.resolve()), "written", swarmws_root=swarmws)
    assert v.review_worthy is True and v.kind == "knowledge"


def test_design_doc_is_content(swarmws: Path):
    p = _write(swarmws, "Knowledge/Designs/2026-08-03-foo.md")
    v = needs_human_review(str(p.resolve()), "written", swarmws_root=swarmws)
    assert v.review_worthy is True and v.kind == "content"


def test_dotted_filename_stem_is_not_hidden_segment(swarmws: Path):
    """A dot in the FILENAME (date stem) is not a hidden segment → still content."""
    assert _has_dot_segment("Knowledge/Designs/2026-08-03-foo.md") is False


# ── owning-tree: bound repo (separate tree, different .gitignore) ─────────────

def test_bound_repo_source_is_source_kind(swarmws: Path, tmp_path: Path):
    """A file inside a bound-repo worktree (a SEPARATE git tree) → kind=source,
    review-worthy, repo set. Its own .gitignore governs it, not SwarmWS's."""
    repo = tmp_path / "bound-repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    (repo / ".gitignore").write_text("build/\n", encoding="utf-8")
    _write(repo, "backend/core/foo.py")
    # a bindings.yaml pointing at it
    proj = swarmws / "Projects" / "TestProj"
    proj.mkdir(parents=True)
    (proj / "bindings.yaml").write_text(
        "bindings:\n"
        "  - repo: bound-repo\n"
        "    kind: external\n"
        "    clone: https://example/x.git\n"
        f"    worktree: {repo}\n"
        "    delivery_contract:\n"
        "      remote_kind: github-pr\n"
        "      build_system: none\n"
        "      branch: main\n"
        "      review_path: pr\n"
        "      auto_send: never\n",
        encoding="utf-8",
    )
    from core.needs_human_review import clear_worktree_cache
    clear_worktree_cache()
    src = repo / "backend" / "core" / "foo.py"
    v = needs_human_review(str(src.resolve()), "written", swarmws_root=swarmws)
    clear_worktree_cache()
    assert v.review_worthy is True
    assert v.kind == "source"
    assert v.repo == "bound-repo"


# ── fail-safe / edge ─────────────────────────────────────────────────────────

def test_outside_all_trees_is_not_review_worthy(swarmws: Path, tmp_path: Path):
    """A path outside SwarmWS and every worktree → not our concern (NOT fail-open junk)."""
    outsider = tmp_path / "somewhere-else" / "x.md"
    outsider.parent.mkdir(parents=True)
    outsider.write_text("x", encoding="utf-8")
    v = needs_human_review(str(outsider.resolve()), "written", swarmws_root=swarmws)
    assert v.review_worthy is False and v.kind == "process"


def test_empty_and_null_byte_are_safe(swarmws: Path):
    assert needs_human_review("", swarmws_root=swarmws).review_worthy is False
    assert needs_human_review("a\x00b", swarmws_root=swarmws).review_worthy is False


def test_never_raises_on_garbage(swarmws: Path):
    for junk in ["../../etc/passwd", "\\\\weird", "///", "a" * 5000]:
        v = needs_human_review(junk, swarmws_root=swarmws)
        assert isinstance(v, ReviewVerdict)  # returned a verdict, did not raise


# ── BATCH (run_4de279ca): --stdin batch verdict == N single verdicts, 1 subprocess/tree ──

def test_batch_verdict_equals_single_on_mixed_paths(swarmws: Path):
    """needs_human_review_batch(paths) must return the SAME verdict per path as N
    single needs_human_review calls — a mixed fixture (deliverable / .artifacts
    dot-seg / gitignored .db / bound-repo source / knowledge md)."""
    from core.needs_human_review import needs_human_review_batch
    _write(swarmws, "Knowledge/Designs/foo.md")
    _write(swarmws, ".artifacts/runs/r1/x.json")          # dot-segment → process
    _write(swarmws, "node_modules/dep/y.js")              # gitignored → process
    _write(swarmws, "cfg_state.json")                      # *_state.json gitignored → process
    _write(swarmws, "Projects/SwarmAI/2-understanding/TECH.md")  # knowledge
    paths = [
        str((swarmws / "Knowledge/Designs/foo.md").resolve()),
        str((swarmws / ".artifacts/runs/r1/x.json").resolve()),
        str((swarmws / "node_modules/dep/y.js").resolve()),
        str((swarmws / "cfg_state.json").resolve()),
        str((swarmws / "Projects/SwarmAI/2-understanding/TECH.md").resolve()),
    ]
    batch = needs_human_review_batch(paths, "written", swarmws_root=swarmws)
    assert len(batch) == len(paths)
    for p in paths:
        single = needs_human_review(p, "written", swarmws_root=swarmws)
        assert batch[p].review_worthy == single.review_worthy, f"mismatch {p}"
        assert batch[p].kind == single.kind, f"kind mismatch {p}"
    # spot-check the semantics directly
    assert batch[paths[0]].review_worthy is True and batch[paths[0]].kind == "content"
    assert batch[paths[1]].review_worthy is False  # .artifacts dot-seg
    assert batch[paths[2]].review_worthy is False  # gitignored
    assert batch[paths[3]].review_worthy is False  # *_state.json
    assert batch[paths[4]].kind == "knowledge"


def test_batch_runs_one_check_ignore_subprocess_per_tree(swarmws: Path, monkeypatch):
    """The perf contract (P1/P3): N paths in ONE tree → exactly ONE
    `git check-ignore --stdin` subprocess, not N. Counts subprocess.run calls
    whose argv contains 'check-ignore'."""
    import core.needs_human_review as mod
    calls = {"check_ignore": 0}
    real_run = subprocess.run

    def counting_run(argv, *a, **k):
        if isinstance(argv, (list, tuple)) and "check-ignore" in argv:
            calls["check_ignore"] += 1
        return real_run(argv, *a, **k)

    monkeypatch.setattr(mod.subprocess, "run", counting_run)
    # 4 non-dot, non-knowledge paths that all reach the check-ignore layer
    for rel in ["a.md", "b.txt", "c.md", "d.txt"]:
        _write(swarmws, f"Knowledge/Designs/{rel}")
    paths = [str((swarmws / f"Knowledge/Designs/{rel}").resolve())
             for rel in ["a.md", "b.txt", "c.md", "d.txt"]]
    mod.needs_human_review_batch(paths, "written", swarmws_root=swarmws)
    assert calls["check_ignore"] == 1, f"expected 1 batch check-ignore, got {calls['check_ignore']}"


def test_batch_stdin_mapping_not_index_aligned(swarmws: Path):
    """check-ignore --stdin prints ONLY ignored paths (no line for not-ignored).
    The batch must map via set-membership, NOT index-align. Mix ignored+not so a
    naive zip would misalign."""
    from core.needs_human_review import needs_human_review_batch
    _write(swarmws, "Knowledge/Designs/keep.md")   # not ignored → content
    _write(swarmws, "app.db")                        # *.db ignored → process
    _write(swarmws, "Knowledge/Designs/keep2.md")   # not ignored → content
    paths = [
        str((swarmws / "Knowledge/Designs/keep.md").resolve()),
        str((swarmws / "app.db").resolve()),
        str((swarmws / "Knowledge/Designs/keep2.md").resolve()),
    ]
    b = needs_human_review_batch(paths, "written", swarmws_root=swarmws)
    assert b[paths[0]].review_worthy is True
    assert b[paths[1]].review_worthy is False   # db ignored
    assert b[paths[2]].review_worthy is True     # would be wrong if index-aligned to db's line
