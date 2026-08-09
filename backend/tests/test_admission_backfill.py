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

    def test_machine_broadcast_survivor_is_flagged_not_deleted(self, tmp_path):
        # STEERING "trash > rm": a now-noise SURVIVOR (human never saw it) is FLAGGED for
        # review, NOT silently unlinked — is_noise could false-positive on real knowledge.
        backfill = self._fn()
        pdir = self._setup(tmp_path)
        _write_proposal(pdir, "p_noise", status="escalated",
                        content="Architecture change detected:\n- new_module: `backend/core/x.py`")
        result = backfill(tmp_path)
        assert result["flagged_noise"] >= 1
        assert list(pdir.glob("p_noise.json")), "survivor must NOT be auto-deleted (reversibility)"

    def test_untrusted_survivor_stays_escalated(self, tmp_path):
        backfill = self._fn()
        pdir = self._setup(tmp_path)
        # trust n/a (no run.json) → review → stays in queue
        _write_proposal(pdir, "p_review", status="escalated")
        result = backfill(tmp_path)
        assert result["kept_review"] >= 1
        assert list(pdir.glob("p_review.json"))  # still there for human review

    def test_survivor_trust_is_restamped(self, tmp_path):
        # a survivor's stale stored stamp is re-derived + persisted (n/a here: no run.json)
        backfill = self._fn()
        pdir = self._setup(tmp_path)
        _write_proposal(pdir, "p_stale", status="escalated",
                        passed_adversarial_gate="passed", source_run_id="run_missing")
        backfill(tmp_path)
        restamped = json.loads((pdir / "p_stale.json").read_text())
        assert restamped["passed_adversarial_gate"] == "n/a", "stale 'passed' must be re-derived to n/a"

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
        # running twice must not double-process or crash
        backfill = self._fn()
        pdir = self._setup(tmp_path)
        _write_proposal(pdir, "p_rejected", status="rejected")
        _write_proposal(pdir, "p_review", status="escalated")
        r1 = backfill(tmp_path)
        r2 = backfill(tmp_path)
        assert r1["gc_removed"] == 1 and r2["gc_removed"] == 0  # nothing terminal left
        assert list(pdir.glob("p_review.json"))  # review survivor stable

    def test_no_proposals_dir_is_safe(self, tmp_path):
        backfill = self._fn()
        result = backfill(tmp_path)  # no .artifacts/proposals
        assert result == {"gc_removed": 0, "flagged_noise": 0, "kept_review": 0, "would_auto": 0}
