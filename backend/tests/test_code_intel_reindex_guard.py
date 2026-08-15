"""Regression tests for the code_intel reindex ownership gate in
ContextHealthHook._refresh_code_intel.

BUG (run_864c23b4): the emit branch of _refresh_code_intel fired
`emit_event_atomic("code_intel_full_reindex")` for ANY stale+suggest_full_rebuild
graph WITHOUT an ownership check. A worktree:null DDD (e.g. IVTHub — a data-agent
brain that GOVERNs a remote Coral repo but has no local checkout) has a code_intel.db
whose graph_meta lacks repo_root, so check_freshness returns suggest_full_rebuild=True
forever. The reindex handler then no-ops it (unowned_repo) → re-emit next health tick
→ unbounded churn (CPU + log spam + scheduler pressure), never completes.

ROOT FIX: gate the whole per-project body on resolve_owned_repo_root(project_dir) —
a project that owns no resolvable local repo is skipped entirely (no emit, no
incremental index). This is the SAME ownership oracle the incremental branch and the
reindex handler already use; it just now also covers the emit branch.

check_freshness is deliberately NOT changed — recall_multi depends on
repo_root-absent → stale=True to stamp hits as needs-verify.

METHODOLOGY: drive the REAL ContextHealthHook._refresh_code_intel with a real tmp
workspace root + a real (empty) code_intel.db file, monkeypatching only the leaf
boundaries (load_project_graph, check_freshness, resolve_owned_repo_root,
emit_event_atomic). Assert emit is NOT called for an unowned project and IS reachable
for an owned one. Mutation-proof: reverting the gate makes the unowned test fail.
"""

from pathlib import Path
from unittest.mock import MagicMock

import pytest


def _make_stale_full_rebuild_freshness():
    """A FreshnessResult-like object matching the worktree:null case:
    stale + suggest_full_rebuild, 0 commits/files (the exact IVTHub log signature)."""
    fr = MagicMock()
    fr.stale = True
    fr.suggest_full_rebuild = True
    fr.commits_behind = 0
    fr.changed_files = []
    return fr


def _seed_project(root: Path, name: str) -> Path:
    """Create root/Projects/<name>/code_intel.db (empty file is enough — the loop
    only checks db_path.exists(), the graph is monkeypatched)."""
    proj = root / "Projects" / name
    proj.mkdir(parents=True, exist_ok=True)
    (proj / "code_intel.db").write_bytes(b"")
    return proj


class TestReindexOwnershipGate:
    """The emit branch must NOT fire a reindex for a project that owns no repo."""

    def _run(self, tmp_path, monkeypatch, *, owned: bool):
        """Drive the real _refresh_code_intel; return the emit_event_atomic mock."""
        import core.code_intel as code_intel_mod
        import core.code_intel.freshness as freshness_mod
        import jobs.scheduler as scheduler_mod
        from hooks.context_health_hook import ContextHealthHook

        _seed_project(tmp_path, "IVTHub")

        # Leaf boundaries: a non-empty graph + a stale/full-rebuild freshness verdict.
        graph = MagicMock()
        monkeypatch.setattr(code_intel_mod, "load_project_graph", lambda name: graph)
        # check_freshness is imported INSIDE the method via
        # `from core.code_intel.freshness import check_freshness`, so patch the source module.
        monkeypatch.setattr(
            freshness_mod, "check_freshness",
            lambda g: _make_stale_full_rebuild_freshness(),
        )

        # Ownership oracle — None = unowned (worktree:null), a real dir = owned.
        owned_dir = str(tmp_path) if owned else None
        monkeypatch.setattr(
            code_intel_mod, "resolve_owned_repo_root", lambda project_dir: owned_dir
        )
        # repo_root_is_owned is used by the incremental branch — keep it consistent.
        monkeypatch.setattr(
            code_intel_mod, "repo_root_is_owned",
            lambda project_dir, stored: owned,
        )

        # The thing we assert on: did the emit fire?
        emit_mock = MagicMock()
        monkeypatch.setattr(scheduler_mod, "emit_event_atomic", emit_mock)

        hook = ContextHealthHook()
        hook._refresh_code_intel(tmp_path)
        return emit_mock

    def test_unowned_project_does_not_emit_reindex(self, tmp_path, monkeypatch):
        """AC1/AC5 (mutation-proof): a worktree:null project (resolve_owned_repo_root
        → None) with a stale+full-rebuild graph must NOT emit code_intel_full_reindex.
        Reverting the ownership gate makes this assertion fail (emit fires)."""
        emit_mock = self._run(tmp_path, monkeypatch, owned=False)
        emit_calls = [
            c for c in emit_mock.call_args_list
            if c.args and c.args[0] == "code_intel_full_reindex"
        ]
        assert emit_calls == [], (
            "unowned (worktree:null) project must be skipped before the emit branch — "
            f"but emit_event_atomic fired {len(emit_calls)} reindex event(s) (the churn bug)"
        )

    def test_owned_project_is_not_blocked_by_the_gate(self, tmp_path, monkeypatch):
        """AC2: an OWNED project (resolve_owned_repo_root → real dir) must NOT be
        skipped by the ownership gate — the gate is scoped to unowned projects only.
        (It may or may not emit depending on downstream branches; the invariant is
        that the new gate does not short-circuit an owned project.)"""
        # If the gate wrongly blocked owned projects too, the emit branch would be
        # unreachable for a stale+full-rebuild OWNED graph. Assert it IS reachable.
        emit_mock = self._run(tmp_path, monkeypatch, owned=True)
        emit_calls = [
            c for c in emit_mock.call_args_list
            if c.args and c.args[0] == "code_intel_full_reindex"
        ]
        assert len(emit_calls) == 1, (
            "an owned project with a stale+full-rebuild graph should still reach the "
            f"emit branch — got {len(emit_calls)} reindex emits (gate over-blocked owned)"
        )
