"""Tests for adversarial review meta-monitoring (Pipeline v1).

Verifies:
- AC6: adversarial_stats.json records per-run findings count, read from each v1
  run's run_audit.json (``details.findings``, ``details.experts``)
- AC7: WARNING when the 3 latest completed runs that dispatched review experts all
  closed with 0 findings; a run v1 sent no expert (a small change) never counts
"""
import json
from pathlib import Path


def _make_run(runs_dir: str, run_id: str, findings_total: int, files_changed: int,
              created: str | None = None):
    """A completed v1 run: run.json projection + run_audit.json. ``files_changed`` > 50
    stands for "v1 dispatched review experts" (it sends none to a small change)."""
    run_dir = Path(runs_dir) / run_id
    run_dir.mkdir(parents=True)
    (run_dir / "run.json").write_text(json.dumps({
        "id": run_id, "status": "completed", "stages": {"deliver": "completed"},
        "created_at": created or f"2026-10-01T00:00:{run_id[-2:]}Z"}))
    experts = [{"expert": "correctness", "stage": "review"}] if files_changed > 50 else []
    (run_dir / "run_audit.json").write_text(json.dumps({
        "run_id": run_id, "outcome": {"status": "completed"},
        "details": {"findings": [{"canonical_id": f"F{i}"} for i in range(findings_total)],
                    "experts": experts}}))


class TestAdversarialStatsRecording:
    """AC6: adversarial_stats.json records per-run findings count."""

    def test_import_function(self):
        from core.adversarial_meta import check_adversarial_health

        assert callable(check_adversarial_health)

    def test_stats_recorded(self, tmp_path):
        from core.adversarial_meta import check_adversarial_health

        runs_dir = tmp_path / ".artifacts" / "runs"
        runs_dir.mkdir(parents=True)
        _make_run(str(runs_dir), "run_001", findings_total=3, files_changed=60)

        result = check_adversarial_health(tmp_path / ".artifacts")
        assert result["runs_analyzed"] >= 1
        assert result["stats"][0]["run_id"] == "run_001"
        assert result["stats"][0]["findings_total"] == 3

    def test_persists_stats_file(self, tmp_path):
        from core.adversarial_meta import check_adversarial_health

        runs_dir = tmp_path / ".artifacts" / "runs"
        runs_dir.mkdir(parents=True)
        _make_run(str(runs_dir), "run_001", findings_total=2, files_changed=30)

        check_adversarial_health(tmp_path / ".artifacts")
        stats_file = tmp_path / ".artifacts" / "adversarial_stats.json"
        assert stats_file.exists()


class TestDegradationWarning:
    """AC7: WARNING when 3+ consecutive runs with >50 lines have 0 findings."""

    def test_no_warning_with_findings(self, tmp_path):
        from core.adversarial_meta import check_adversarial_health

        runs_dir = tmp_path / ".artifacts" / "runs"
        runs_dir.mkdir(parents=True)
        for i in range(3):
            _make_run(str(runs_dir), f"run_{i:03d}", findings_total=2, files_changed=60)

        result = check_adversarial_health(tmp_path / ".artifacts")
        assert result["degradation_warning"] is False

    def test_warning_on_3_consecutive_zero_findings(self, tmp_path):
        from core.adversarial_meta import check_adversarial_health

        runs_dir = tmp_path / ".artifacts" / "runs"
        runs_dir.mkdir(parents=True)
        for i in range(3):
            _make_run(str(runs_dir), f"run_{i:03d}", findings_total=0, files_changed=60)

        result = check_adversarial_health(tmp_path / ".artifacts")
        assert result["degradation_warning"] is True

    def test_no_warning_when_lines_under_50(self, tmp_path):
        from core.adversarial_meta import check_adversarial_health

        runs_dir = tmp_path / ".artifacts" / "runs"
        runs_dir.mkdir(parents=True)
        for i in range(3):
            _make_run(str(runs_dir), f"run_{i:03d}", findings_total=0, files_changed=10)

        result = check_adversarial_health(tmp_path / ".artifacts")
        # Small changesets — 0 findings is expected
        assert result["degradation_warning"] is False

    def test_mixed_runs_no_warning(self, tmp_path):
        from core.adversarial_meta import check_adversarial_health

        runs_dir = tmp_path / ".artifacts" / "runs"
        runs_dir.mkdir(parents=True)
        _make_run(str(runs_dir), "run_001", findings_total=0, files_changed=60)
        _make_run(str(runs_dir), "run_002", findings_total=1, files_changed=60)
        _make_run(str(runs_dir), "run_003", findings_total=0, files_changed=60)

        result = check_adversarial_health(tmp_path / ".artifacts")
        # Not consecutive — middle run has findings
        assert result["degradation_warning"] is False

    def test_an_old_pipeline_run_without_an_audit_is_not_counted(self, tmp_path):
        from core.adversarial_meta import check_adversarial_health

        runs_dir = tmp_path / ".artifacts" / "runs"
        (runs_dir / "run_old").mkdir(parents=True)
        (runs_dir / "run_old" / "run.json").write_text(json.dumps({
            "id": "run_old", "status": "completed",
            "stages": [{"stage": "deliver", "status": "completed", "artifact_id": "a"}]}))
        assert check_adversarial_health(tmp_path / ".artifacts")["runs_analyzed"] == 0

    def test_runs_are_ordered_by_creation_not_by_directory_name(self, tmp_path):
        from core.adversarial_meta import check_adversarial_health

        runs_dir = tmp_path / ".artifacts" / "runs"
        runs_dir.mkdir(parents=True)
        for rid, t in (("run_c3", "01"), ("run_b2", "02"), ("run_a1", "03")):
            _make_run(str(runs_dir), rid, findings_total=0, files_changed=60,
                      created=f"2026-10-01T00:00:{t}Z")
        _make_run(str(runs_dir), "run_z9", findings_total=1, files_changed=60,
                  created="2026-10-01T00:00:04Z")
        # newest run has findings: the trailing streak is 0
        assert check_adversarial_health(tmp_path / ".artifacts")["degradation_warning"] is False
