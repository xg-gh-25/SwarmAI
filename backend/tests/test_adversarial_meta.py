"""Tests for adversarial review meta-monitoring.

Verifies:
- AC6: adversarial_stats.json records per-run findings count
- AC7: WARNING emitted when 3+ consecutive runs with >50 changed lines have 0 findings
"""
import json
from pathlib import Path



def _make_run(runs_dir: str, run_id: str, findings_total: int, files_changed: int):
    """Helper: create a run.json with deliver stage containing adversarial review."""
    run_dir = Path(runs_dir) / run_id
    run_dir.mkdir(parents=True)
    run_data = {
        "id": run_id,
        "status": "completed",
        "stages": [
            {
                "stage": "deliver",
                "status": "completed",
                "artifact_id": f"art_{run_id}",
            }
        ],
    }
    (run_dir / "run.json").write_text(json.dumps(run_data))

    # Create deliver artifact
    artifacts_dir = run_dir.parent.parent
    deliver_art = {
        "type": "delivery",
        "adversarial_review": {
            "findings_total": findings_total,
            "findings_fixed": findings_total,
            "findings_remaining": 0,
        },
        "files_changed": files_changed,
    }
    art_file = artifacts_dir / f"art_{run_id}.json"
    art_file.write_text(json.dumps(deliver_art))


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
