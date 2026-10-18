"""worktree_sweep — hourly (and daemon-start) sweep of pipeline-run worktrees
(docs/design/pipeline-run-worktree.md §8.3).

Runs in the daemon process, so it can see SessionRouter: a run is released only when
its owner session is not STREAMING / WAITING_INPUT (an unknown liveness keeps the run).
Unregistered directories under WT_ROOT are moved to ``.quarantine/``, never deleted;
an unintegrated worktree is deleted only after a verified ledger snapshot.

Public: ``run_worktree_sweep() -> dict`` (status success | error, plus counts).
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def owner_active(session_id: "str | None") -> bool:
    """True while the owner may still be working (live or unknown)."""
    if not session_id:
        return False
    from core.run_owner_gate import _owner_live
    live = _owner_live(session_id)
    return True if live is None else live


def run_worktree_sweep() -> dict:
    try:
        from core.run_worktree import sweep, wt_root
        from core.security_hooks import _pipeline_runs_root
        if not wt_root().is_dir():
            return {"status": "success", "released": 0, "quarantined": 0, "skipped": "no worktrees"}
        rep = sweep(_pipeline_runs_root(), owner_active=owner_active, release_orphans=True)
    except Exception as exc:  # noqa: BLE001 — a sweep failure is reported, never raised
        logger.exception("worktree sweep failed")
        return {"status": "error", "error": f"{type(exc).__name__}: {exc}"}
    if rep["released"] or rep["quarantined"] or rep["errors"]:
        logger.warning("worktree sweep: %s", rep)
    return {"status": "error" if rep["errors"] else "success",
            "released": len(rep["released"]), "quarantined": len(rep["quarantined"]),
            "branches": len(rep["branches"]), "errors": rep["errors"][:5]}
