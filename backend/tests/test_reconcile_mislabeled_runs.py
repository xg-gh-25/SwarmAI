"""Tests for the one-time reconciliation of stale-detector-mislabeled runs.

WHAT: `scripts/reconcile_mislabeled_runs.py` rewrites on-disk run.json files from
status="failed" -> "completed" ONLY for runs the OLD stale-detector mislabeled —
the triple-gate: status=="failed" AND failure_reason==_STALE_FAILURE_REASON AND a
completed `reflect` stage exists (the honest end-marker; last stage in every
profile). It is dry-run by default, backs up + atomic-writes on --apply, is
idempotent, and NEVER touches partial runs, real orphans, or other-reason failures.

METHODOLOGY: build tmp run.json fixtures of every shape (match / deliver-only-no-
reflect / other-reason / real-orphan-running / already-completed) and assert the
selector picks EXACTLY the match set; assert dry-run writes nothing; assert --apply
rewrites + backs up + is idempotent; assert the input dict for a partial run is
never touched.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import scripts.reconcile_mislabeled_runs as recon
from routers.pipelines import _STALE_FAILURE_REASON


def _write_run(runs_dir: Path, run_id: str, **state) -> Path:
    d = runs_dir / run_id
    d.mkdir(parents=True, exist_ok=True)
    base = {"id": run_id, "project": "P", "profile": "bugfix"}
    base.update(state)
    f = d / "run.json"
    f.write_text(json.dumps(base, indent=2), encoding="utf-8")
    return f


def _stages(*pairs):
    return [{"stage": s, "status": st} for s, st in pairs]


@pytest.fixture
def runs_root(tmp_path):
    root = tmp_path / "Projects" / "P" / ".artifacts" / "runs"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _all_shapes(runs_root):
    # MATCH: failed + signature + completed reflect
    _write_run(runs_root, "run_match1", status="failed", failure_reason=_STALE_FAILURE_REASON,
               stages=_stages(("evaluate", "completed"), ("deliver", "completed"), ("reflect", "completed")))
    # DELIVER-ONLY: failed + signature but NO reflect -> partial, must NOT match
    _write_run(runs_root, "run_deliveronly", status="failed", failure_reason=_STALE_FAILURE_REASON,
               stages=_stages(("deliver", "completed")))
    # OTHER-REASON: failed + reflect but a different failure_reason -> real failure, must NOT match
    _write_run(runs_root, "run_otherreason", status="failed", failure_reason="tests failed: 3 red",
               stages=_stages(("evaluate", "completed"), ("reflect", "completed")))
    # REAL-ORPHAN: running (not failed) -> must NOT match
    _write_run(runs_root, "run_orphan", status="running", failure_reason=_STALE_FAILURE_REASON,
               stages=_stages(("evaluate", "completed")))
    # ALREADY-COMPLETED: not failed -> must NOT match (idempotency anchor)
    _write_run(runs_root, "run_done", status="completed",
               stages=_stages(("reflect", "completed")))


def test_select_picks_only_triple_gate_matches(runs_root):
    _all_shapes(runs_root)
    selected = {r["id"] for r in recon.select_mislabeled(runs_root.parent.parent.parent.parent)}
    assert selected == {"run_match1"}, f"selection must be exactly the match, got {selected}"


def test_dry_run_writes_nothing(runs_root):
    _all_shapes(runs_root)
    match_file = runs_root / "run_match1" / "run.json"
    before = match_file.read_text()
    n = recon.reconcile(runs_root.parent.parent.parent.parent, apply=False)
    assert n == 1, "dry-run reports 1 would-be-rewritten"
    assert match_file.read_text() == before, "dry-run must not modify disk"
    assert not (runs_root / "run_match1" / "run.json.bak").exists(), "dry-run must not create backup"


def test_apply_rewrites_backs_up_and_is_idempotent(runs_root):
    _all_shapes(runs_root)
    root = runs_root.parent.parent.parent.parent
    match_file = runs_root / "run_match1" / "run.json"

    n = recon.reconcile(root, apply=True)
    assert n == 1
    after = json.loads(match_file.read_text())
    assert after["status"] == "completed", "matched run rewritten to completed"
    # audit marker preserves BOTH the original status and the stale reason (not thinned)
    assert after["reconciled_from"] == {
        "status": "failed", "failure_reason": _STALE_FAILURE_REASON,
    }, "audit marker records original status + reason"
    assert "failure_reason" not in after, "stale reason cleared from top level"
    assert (runs_root / "run_match1" / "run.json.bak").exists(), "backup created"
    # backup preserves the original failed status
    bak = json.loads((runs_root / "run_match1" / "run.json.bak").read_text())
    assert bak["status"] == "failed"

    # Idempotent: re-run selects nothing (completed no longer matches).
    assert recon.reconcile(root, apply=True) == 0, "re-run must be a no-op"


def test_partial_and_real_failures_untouched_after_apply(runs_root):
    _all_shapes(runs_root)
    root = runs_root.parent.parent.parent.parent
    recon.reconcile(root, apply=True)
    for rid, want in [("run_deliveronly", "failed"), ("run_otherreason", "failed"),
                      ("run_orphan", "running"), ("run_done", "completed")]:
        st = json.loads((runs_root / rid / "run.json").read_text())["status"]
        assert st == want, f"{rid} must stay {want}, got {st}"
        assert not (runs_root / rid / "run.json.bak").exists(), f"{rid} must not be backed up (untouched)"


def test_signature_is_shared_constant_not_hardcoded():
    # The script must reference the imported constant, guaranteeing byte-parity
    # with the writer. If someone hardcodes a copy, this drifts silently.
    assert recon._STALE_FAILURE_REASON == _STALE_FAILURE_REASON
