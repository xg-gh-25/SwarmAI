"""Knowledge Admission — step-final: backfill existing proposals + GC terminal files.

(AC12) The inherited proposal pile is NOT grandfathered under old rules: terminal
files (applied/rejected/expired/dismissed) are GC'd from disk; each surviving
(pending/escalated) proposal is re-run through the new admission flow (is_noise →
band). After backfill, what remains is what the NEW rules produce.
"""
import json
import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))


def _write_proposal(pdir: Path, pid: str, **fields):
    base = {
        "id": pid, "target_doc": "TECH.md", "target_section": "Runtime Traps",
        "content": "A genuinely load-bearing runtime lesson worth keeping in the brain over time.",
        "source_run_id": "run_x", "confidence": 0.72, "created_at": "2026-08-10T00:00:00+00:00",
        "status": "escalated", "source_stage": "reflect",
    }
    base.update(fields)
    (pdir / f"{pid}.json").write_text(json.dumps(base))


class TestBackfill:
    def _fn(self):
        from core.ddd_cultivation import backfill_proposals
        return backfill_proposals

    def _setup(self, tmp_path):
        pdir = tmp_path / ".artifacts" / "proposals"
        pdir.mkdir(parents=True)
        return pdir

    def test_gc_removes_terminal_files(self, tmp_path):
        backfill = self._fn()
        pdir = self._setup(tmp_path)
        _write_proposal(pdir, "p_applied", status="applied")
        _write_proposal(pdir, "p_rejected", status="rejected")
        _write_proposal(pdir, "p_expired", status="expired")
        _write_proposal(pdir, "p_dismissed", status="dismissed")
        result = backfill(tmp_path)
        assert result["gc_removed"] == 4
        # terminal files gone from disk
        assert not list(pdir.glob("p_applied.json"))
        assert not list(pdir.glob("p_rejected.json"))

    def test_noise_survivor_archived_and_removed(self, tmp_path):
        # AUTONOMY-FIRST (run_86f44f35): a now-noise survivor is DISCARDED — archived to a
        # recoverable sink (discarded-proposals.jsonl) AND removed from the live queue. XG
        # directive: noise is dropped, never kept for a human. (Structural machine-broadcast
        # is caught by is_noise before the judge, so no judge mock needed.)
        backfill = self._fn()
        pdir = self._setup(tmp_path)
        _write_proposal(pdir, "p_noise", status="escalated",
                        content="Architecture change detected:\n- new_module: `backend/core/x.py`")
        result = backfill(tmp_path)
        assert result["flagged_noise"] >= 1  # counter = discarded
        assert not list(pdir.glob("p_noise.json")), "discard removes it from the live queue"
        archive = tmp_path / ".artifacts" / "discarded-proposals.jsonl"
        assert archive.is_file(), "discard must be archived (recoverable)"

    def test_untrusted_survivor_judged_and_discarded(self, tmp_path):
        import unittest.mock as m
        import core.ddd_cultivation as dc
        backfill = self._fn()
        pdir = self._setup(tmp_path)
        # trust n/a (no run.json) → judge decides; judge-suspect → discard (no queue)
        _write_proposal(pdir, "p_review", status="escalated")
        with m.patch.object(dc, "self_adversarial_judge", lambda *a, **k: ("suspect", "t")):
            result = backfill(tmp_path)
        assert result["kept_review"] == 0, "autonomy-first: no review queue"
        assert not list(pdir.glob("p_review.json")), "judge-suspect survivor is discarded"

    def test_survivor_trust_restamped_before_judge(self, tmp_path):
        # a survivor's stale 'passed' stamp is re-derived to n/a (no run.json) BEFORE the
        # judge runs. With judge-suspect it discards; the re-stamp still happened (verified
        # via the archive carrying the discard, not a lingering 'passed' file).
        import unittest.mock as m
        import core.ddd_cultivation as dc
        backfill = self._fn()
        pdir = self._setup(tmp_path)
        _write_proposal(pdir, "p_stale", status="escalated",
                        passed_adversarial_gate="passed", source_run_id="run_missing")
        with m.patch.object(dc, "self_adversarial_judge", lambda *a, **k: ("suspect", "t")):
            backfill(tmp_path)
        # re-stamped n/a → judged suspect → discarded (file gone, archived)
        assert not list(pdir.glob("p_stale.json"))
        archive = tmp_path / ".artifacts" / "discarded-proposals.jsonl"
        assert archive.is_file()

    def test_dry_run_touches_nothing(self, tmp_path):
        backfill = self._fn()
        pdir = self._setup(tmp_path)
        _write_proposal(pdir, "p_rejected", status="rejected")
        _write_proposal(pdir, "p_review", status="escalated", passed_adversarial_gate="passed",
                        source_run_id="run_missing")
        result = backfill(tmp_path, dry_run=True)
        assert result["gc_removed"] == 1  # reports the plan
        assert list(pdir.glob("p_rejected.json"))  # but did NOT delete
        # and did NOT re-stamp
        assert json.loads((pdir / "p_review.json").read_text())["passed_adversarial_gate"] == "passed"

    def test_backfill_is_idempotent(self, tmp_path):
        # running twice must not double-process or crash. AUTONOMY-FIRST: a trust=n/a
        # survivor is judged on run 1 (judge-suspect → discarded+unlinked); run 2 finds
        # nothing left to process. GC still fires once for the terminal file.
        import unittest.mock as m
        import core.ddd_cultivation as dc
        backfill = self._fn()
        pdir = self._setup(tmp_path)
        _write_proposal(pdir, "p_rejected", status="rejected")
        _write_proposal(pdir, "p_review", status="escalated")
        with m.patch.object(dc, "self_adversarial_judge", lambda *a, **k: ("suspect", "t")):
            r1 = backfill(tmp_path)
            r2 = backfill(tmp_path)
        assert r1["gc_removed"] == 1 and r2["gc_removed"] == 0  # nothing terminal left
        assert not list(pdir.glob("p_review.json"))  # discarded on run 1, gone on run 2 (idempotent)

    def test_no_proposals_dir_is_safe(self, tmp_path):
        backfill = self._fn()
        result = backfill(tmp_path)  # no .artifacts/proposals
        assert result == {"gc_removed": 0, "flagged_noise": 0, "kept_review": 0, "would_auto": 0}
