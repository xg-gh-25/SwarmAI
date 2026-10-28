"""Cultivation proposal approve/reject lifecycle guards (run_93594880 + run_24d9f714).

Covers latent bugs in backend/routers/cultivation.py + backend/core/ddd_cultivation.py:
  1. _find_proposal returned ANY id-matching proposal with no status/expiry filter →
     a terminal (applied/rejected) proposal was re-approvable, and an expired one was
     approvable though hidden from the list view. Fixed: filter by
     AWAITING_HUMAN_STATUSES + is_expired (SSOT constant shared with read_pending_proposals).
  2. find→apply→mark held no lock → concurrent approve+reject could tear the status.
     Fixed: a per-proposal flock (no unlink — inode-race guard, run_edcfd0e5).
  3. (run_24d9f714) list_proposals passed str(root) to read_pending_proposals(Path),
     whose body does `workspace_dir / "Projects"` → TypeError (str/str) → GET
     /api/cultivation/proposals returns 500. Fixed: pass the Path directly.
  4. (run_24d9f714) apply_to_ddd's finally unlinked the flock'd doc .lock → the same
     inode-divergence race as #2. Fixed: leave the .lock sidecar (matches _proposal_lock).

These drive the REAL functions under fix (list_proposals / apply_to_ddd), no mock of them.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from routers import cultivation as m
from core.ddd_cultivation import AWAITING_HUMAN_STATUSES, CultivationProposal, apply_to_ddd


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


class TestListProposalsPathType:
    """Bug 3 (run_24d9f714) — GET /api/cultivation/proposals must return 200, not 500.

    Drives the REAL async list_proposals handler. The workspace root is a temp dir with
    a full Projects/<project>/.artifacts/proposals/ tree AND a pending proposal, so the
    :46 project_dir.exists() 404-guard is passed and we actually reach the buggy line 49.
    Pre-fix (str(root)) → read_pending_proposals does str/"Projects" → TypeError → 500.
    """

    async def test_list_proposals_returns_200_not_500(self, tmp_path, monkeypatch):
        # Build the on-disk tree list_proposals requires (else it 404s at :46 for the
        # WRONG reason and would false-green — Gate-1 Fix B).
        project = "SwarmAI"
        project_dir = tmp_path / "Projects" / project
        _write_proposal(project_dir, "pp1", status="pending")

        monkeypatch.setattr(
            m.initialization_manager,
            "get_cached_workspace_path",
            lambda: str(tmp_path),
        )

        # Pre-fix this raises TypeError (uncaught → 500). Post-fix it returns a dict.
        result = await m.list_proposals(project=project)
        assert result["project"] == project
        assert result["count"] >= 1  # the pending proposal we planted
        assert any(p["id"] == "pp1" for p in result["proposals"])


class TestProjectParamTraversalGuard:
    """Bug 3 second-order (run_24d9f714, adversarial security MED): the str→Path fix
    makes the unvalidated `project` param's path traversal REACHABLE. The guard must
    fail-closed BEFORE the read sink on all 3 endpoints (list/approve/reject).

    Without the guard, project='../..' resolves project_dir to the workspace root,
    passes the .exists() check, and read_pending_proposals globs proposal JSON from
    an arbitrary dir → exfiltration. The guard rejects separators/.. with 400.
    """

    @pytest.mark.parametrize("bad", ["..", "../..", "../../etc", "a/b", "foo\\bar"])
    async def test_list_rejects_traversal(self, tmp_path, monkeypatch, bad):
        monkeypatch.setattr(
            m.initialization_manager, "get_cached_workspace_path", lambda: str(tmp_path)
        )
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as ei:
            await m.list_proposals(project=bad)
        assert ei.value.status_code == 400  # rejected BEFORE the read sink

    async def test_approve_rejects_traversal(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            m.initialization_manager, "get_cached_workspace_path", lambda: str(tmp_path)
        )
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as ei:
            await m.approve_proposal(proposal_id="x", project="../..")
        assert ei.value.status_code == 400

    async def test_reject_rejects_traversal(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            m.initialization_manager, "get_cached_workspace_path", lambda: str(tmp_path)
        )
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as ei:
            await m.reject_proposal(proposal_id="x", project="../..")
        assert ei.value.status_code == 400

    async def test_valid_project_still_resolves(self, tmp_path, monkeypatch):
        """The guard must NOT break the legit single-segment path."""
        project = "SwarmAI"
        _write_proposal(tmp_path / "Projects" / project, "pg1", status="pending")
        monkeypatch.setattr(
            m.initialization_manager, "get_cached_workspace_path", lambda: str(tmp_path)
        )
        result = await m.list_proposals(project=project)
        assert result["count"] >= 1  # legit project unaffected by the guard

    async def test_direct_call_query_sentinels_do_not_leak(self, tmp_path, monkeypatch):
        """An unsupplied Query() default must not be mistaken for a caller value.

        FastAPI substitutes the default only through the ASGI app; a DIRECT call leaves
        the raw Query object, and it is TRUTHY — so `if some_param:` treated the
        sentinel as user input. Latent while the branches merely assigned; live once one
        of them validated, and a real corruption on the reject path, where the sentinel
        is PERSISTED into the proposal JSON (not JSON-serializable → mid-write failure).

        This test exists because the normalisation was initially unpinned: deleting it
        left the whole suite green, which is how a fix becomes decoration.
        """
        project = "SwarmAI"
        pfile = _write_proposal(
            tmp_path / "Projects" / project, "qs1", status="pending"
        )
        monkeypatch.setattr(
            m.initialization_manager, "get_cached_workspace_path", lambda: str(tmp_path)
        )

        # 1. list_proposals with NO project: the sentinel must not reach `"/" in project`
        #    nor a `Path / project` join further downstream.
        result = await m.list_proposals()
        assert isinstance(result.get("count"), int)

        # 2. reject_proposal with NO reason: the sentinel must not be PERSISTED (it is
        #    not JSON-serializable, so it would fail mid-write).
        rejected = await m.reject_proposal(proposal_id="qs1", project=project)
        assert rejected["status"] == "rejected"
        stored = json.loads(pfile.read_text())
        assert stored["status"] == "rejected", "the status write did not land"
        assert stored.get("reject_reason") in (None, ""), (
            f"a Query sentinel was persisted: {stored.get('reject_reason')!r}"
        )

    # ── the SIBLING params: the original guard covered `project` only ──────────
    #
    # `project` was hardened here; `target_doc` and `target_section` on the SAME
    # endpoint were not. Both are assigned onto the proposal on truthiness alone and
    # then reach a filesystem write, so this class now pins the COMPLETE param set
    # rather than one of three.

    @pytest.mark.parametrize("bad_doc", [
        "../.context/STEERING.md",
        "../../.context/STEERING.md",
        "2-understanding/../../../.context/STEERING.md",
        "/etc/hosts",
    ])
    async def test_approve_rejects_traversal_target_doc(
        self, tmp_path, monkeypatch, bad_doc
    ):
        """A re-target to a non-routable doc is refused at the boundary (400).

        Outer layer only — the load-bearing guard is inside the appliers, because a
        poisoned proposal that already reached disk is re-hydrated verbatim and never
        passes through this handler again.
        """
        project = "SwarmAI"
        _write_proposal(tmp_path / "Projects" / project, "pd1", status="pending")
        monkeypatch.setattr(
            m.initialization_manager, "get_cached_workspace_path", lambda: str(tmp_path)
        )
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as ei:
            # target_section passed explicitly: calling the handler directly leaves
            # unsupplied Query() defaults as raw FastAPI objects, which would muddy
            # the assertion with an unrelated downstream failure.
            await m.approve_proposal(
                proposal_id="pd1", project=project,
                target_doc=bad_doc, target_section="Architecture",
            )
        assert ei.value.status_code == 400

    async def test_approve_rejects_multiline_target_section(
        self, tmp_path, monkeypatch
    ):
        """A section name carrying a line break cannot forge headings — refuse at 400."""
        project = "SwarmAI"
        _write_proposal(tmp_path / "Projects" / project, "ps1", status="pending")
        monkeypatch.setattr(
            m.initialization_manager, "get_cached_workspace_path", lambda: str(tmp_path)
        )
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as ei:
            await m.approve_proposal(
                proposal_id="ps1",
                project=project,
                target_section="Conventions\n## Forged",
            )
        assert ei.value.status_code == 400

    @pytest.mark.parametrize("good_doc", [
        "TECH.md", "IMPROVEMENT.md", "PRODUCT.md", "PROJECT.md",
        # The routing table legitimately routes to these three as well. They do not
        # exist inside a project (so an apply still reports doc_missing), but the
        # BOUNDARY must not turn a truthful "no such doc" into "invalid parameter" —
        # a wrong error code sends the next debugger to the wrong layer.
        "MEMORY.md", "EVOLUTION.md", "KNOWLEDGE.md",
    ])
    async def test_approve_accepts_every_routable_doc(
        self, tmp_path, monkeypatch, good_doc
    ):
        """The boundary check must admit the FULL routing set, not just the 4 canonical."""
        project = "SwarmAI"
        _write_proposal(tmp_path / "Projects" / project, "pok1", status="pending")
        monkeypatch.setattr(
            m.initialization_manager, "get_cached_workspace_path", lambda: str(tmp_path)
        )
        from fastapi import HTTPException
        try:
            await m.approve_proposal(
                proposal_id="pok1", project=project,
                target_doc=good_doc, target_section="Architecture",
            )
        except HTTPException as e:
            # A downstream apply failure (e.g. the doc does not exist) is fine here;
            # what must NOT happen is a 400 from the parameter guard.
            assert e.status_code != 400, (
                f"routable doc {good_doc!r} was rejected by the boundary guard"
            )


class TestApplyToDddEnqueues:
    """The compatibility entry point hands work to CDF and touches no DDD file."""

    def _make_proposal(self) -> CultivationProposal:
        return CultivationProposal.from_dict({
            "id": "applock1", "target_doc": "IMPROVEMENT.md",
            "target_section": "What Worked",
            "content": "Always validate Path inputs at the boundary before filesystem operations.",
            "source_run_id": "run_test", "confidence": 0.9, "status": "escalated",
            "change_type": "append", "created_at": datetime.now(timezone.utc).isoformat(),
            "ttl_days": 30,
        })

    def test_apply_to_ddd_enqueues_without_writing_or_locking(self, tmp_path, monkeypatch):
        from core.ingestion.api import EnqueueResult

        calls = []
        monkeypatch.setattr(
            "core.ingestion.api.enqueue",
            lambda *args, **kwargs: calls.append((args, kwargs)) or EnqueueResult(
                status="queued", candidate_id="cand-1"),
        )
        doc = tmp_path / "IMPROVEMENT.md"
        doc.write_text("# IMPROVEMENT\n\n## What Worked\n\n- prior lesson\n", encoding="utf-8")
        before = doc.read_bytes()

        assert apply_to_ddd(self._make_proposal(), tmp_path) == "queued"
        assert calls[0][0][0] == "cultivation"
        assert calls[0][0][3]["kind"] == "cultivation"
        assert doc.read_bytes() == before
        assert not doc.with_suffix(doc.suffix + ".lock").exists()


class TestRemovedRetireShapeIsNotActionable:
    """A proposal file written before the retire/rewrite path was removed still
    carries change_type "retire" and placeholder content. It must not surface or be
    approvable: approving it as an addition would write the placeholder as a lesson."""

    def _plant_retire(self, project_dir: Path, pid: str) -> None:
        fp = _write_proposal(project_dir, pid, status="escalated")
        data = json.loads(fp.read_text(encoding="utf-8"))
        data["change_type"] = "retire"
        data["content"] = ("Superseded by a newer polarity-flipped lesson; "
                           "human decide whether to retire.")
        fp.write_text(json.dumps(data), encoding="utf-8")

    def test_find_proposal_skips_a_retire_file(self, tmp_path):
        self._plant_retire(tmp_path, "r1")
        assert m._find_proposal(tmp_path, "r1") is None

    def test_pending_list_skips_a_retire_file(self, tmp_path):
        from core.ddd_cultivation import read_pending_proposals
        self._plant_retire(tmp_path / "Projects" / "P", "r2")
        _write_proposal(tmp_path / "Projects" / "P", "a2", status="pending")
        ids = {p.id for p in read_pending_proposals(tmp_path, "P")}
        assert ids == {"a2"}
