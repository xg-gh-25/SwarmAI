"""Cultivation proposal approve/reject lifecycle guards (run_93594880).

Covers two fixed latent bugs in backend/routers/cultivation.py:
  1. _find_proposal returned ANY id-matching proposal with no status/expiry filter →
     a terminal (applied/rejected) proposal was re-approvable, and an expired one was
     approvable though hidden from the list view. Fixed: filter by
     AWAITING_HUMAN_STATUSES + is_expired (SSOT constant shared with read_pending_proposals).
  2. find→apply→mark held no lock → concurrent approve+reject could tear the status.
     Fixed: a per-proposal flock (no unlink — inode-race guard, run_edcfd0e5).

These drive the REAL _find_proposal / _proposal_lock (no mock of the function-under-fix).
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from routers import cultivation as m
from core.ddd_cultivation import AWAITING_HUMAN_STATUSES


def _write_proposal(project_dir: Path, pid: str, *, status: str = "pending",
                    ttl_days: int = 30, created: str | None = None) -> Path:
    """Plant a proposal JSON at the real proposals path."""
    pdir = project_dir / ".artifacts" / "proposals"
    pdir.mkdir(parents=True, exist_ok=True)
    if created is None:
        created = datetime.now(timezone.utc).isoformat()
    data = {
        "id": pid,
        "target_doc": "IMPROVEMENT.md",
        "target_section": "What Worked",
        "content": "some lesson content",
        "source_run_id": "run_test",
        "confidence": 0.9,
        "status": status,
        "change_type": "append",
        "created_at": created,
        "ttl_days": ttl_days,
    }
    fp = pdir / f"{pid}_20260101-000000.json"
    fp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return fp


class TestFindProposalActionabilityFilter:
    """AC1/AC2/AC3/AC4 — _find_proposal returns only awaiting-human, non-expired."""

    def test_pending_is_found(self, tmp_path):
        _write_proposal(tmp_path, "p1", status="pending")
        assert m._find_proposal(tmp_path, "p1") is not None  # AC3 no regression

    def test_escalated_is_found(self, tmp_path):
        _write_proposal(tmp_path, "p2", status="escalated")
        assert m._find_proposal(tmp_path, "p2") is not None

    def test_applied_is_not_found(self, tmp_path):
        """AC1: a terminal (already-applied) proposal is un-findable → re-approve 404."""
        _write_proposal(tmp_path, "p3", status="applied")
        assert m._find_proposal(tmp_path, "p3") is None

    def test_rejected_is_not_found(self, tmp_path):
        """AC1: a rejected proposal cannot be re-approved."""
        _write_proposal(tmp_path, "p4", status="rejected")
        assert m._find_proposal(tmp_path, "p4") is None

    def test_expired_is_not_found(self, tmp_path):
        """AC2: expired (past TTL) → None, parity with read_pending_proposals."""
        old = (datetime.now(timezone.utc) - timedelta(days=40)).isoformat()
        _write_proposal(tmp_path, "p5", status="pending", ttl_days=30, created=old)
        assert m._find_proposal(tmp_path, "p5") is None

    def test_unknown_id_is_none(self, tmp_path):
        _write_proposal(tmp_path, "p6", status="pending")
        assert m._find_proposal(tmp_path, "nope") is None

    def test_uses_the_shared_ssot_constant(self):
        """AC4: the filter keys off the SAME module constant read_pending_proposals uses."""
        assert AWAITING_HUMAN_STATUSES == frozenset({"pending", "escalated"})


class TestProposalLockSerializes:
    """AC5 — the per-proposal lock is exclusive (409 on contention) and unlink-free."""

    def test_lock_is_exclusive(self, tmp_path):
        (tmp_path / ".artifacts" / "proposals").mkdir(parents=True)
        from fastapi import HTTPException
        with m._proposal_lock(tmp_path, "lk"):
            # A second acquire of the SAME id must fail-fast with 409, not block.
            with pytest.raises(HTTPException) as ei:
                with m._proposal_lock(tmp_path, "lk"):
                    pass
            assert ei.value.status_code == 409

    def test_distinct_ids_do_not_contend(self, tmp_path):
        (tmp_path / ".artifacts" / "proposals").mkdir(parents=True)
        with m._proposal_lock(tmp_path, "a"):
            with m._proposal_lock(tmp_path, "b"):  # different id → no contention
                pass

    def test_lock_released_after_block(self, tmp_path):
        (tmp_path / ".artifacts" / "proposals").mkdir(parents=True)
        with m._proposal_lock(tmp_path, "rel"):
            pass
        # re-acquire must succeed (lock was released)
        with m._proposal_lock(tmp_path, "rel"):
            pass

    def test_lock_file_not_unlinked(self, tmp_path):
        """Inode-race guard (run_edcfd0e5): the .lock file must PERSIST after release,
        never be unlinked (unlinking a flock'd path lets a waiter grab a new inode)."""
        (tmp_path / ".artifacts" / "proposals").mkdir(parents=True)
        with m._proposal_lock(tmp_path, "keep"):
            pass
        lock_file = tmp_path / ".artifacts" / "proposals" / "keep.lock"
        assert lock_file.exists(), "lock file must NOT be unlinked (inode race)"
