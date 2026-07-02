"""Tests for pipeline_analytics.py — Run C (run_38f03634).

Covers the three Run-C hardening changes to the ALREADY-EXISTING aggregator:
  1. Completeness gating: generate_report renders a dimension-cell with n<3 as
     "insufficient data (n=X)", never a confident number (anti-C044).
  2. Time bound: _load_all_metrics skips run dirs older than 90 days (file mtime,
     RP30 no-op-path scaling).
  3. Additive-only schema: analyze_all_runs still emits the keys the EVALUATE/BUILD
     stages consume (high_risk_shapes / stage_estimates / build_injection_recommendations).

Method: pure-function tests on hand-built dicts (gating) + real-fs tmp dirs (mtime).
No mocks of the functions under change (RP47).
"""
import json
import os
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import pipeline_analytics as pa  # noqa: E402


class TestCompletenessGating:
    """AC2: a dimension-cell with n<3 must render 'insufficient data (n=X)',
    NOT a confident number. Absolute per-cell threshold (skeptic: ratio denominator
    is ambiguous per-dimension)."""

    def _report_with_stage_n(self, n: int) -> str:
        intel = {
            "generated_at": "2026-07-02T00:00:00Z",
            "runs_analyzed": n,
            "projects": ["P"],
            "dimensions": {
                "stage_efficiency": {"stages": {
                    "build": {"avg_tokens": 40000, "median_tokens": 38000, "sample_count": n},
                }},
            },
        }
        return pa.generate_report(intel)

    def test_thin_stage_rendered_insufficient(self):
        rep = self._report_with_stage_n(2)
        assert "insufficient data" in rep.lower()
        # the raw avg must NOT be presented as a confident number for n<3
        assert "40000" not in rep

    def test_sufficient_stage_rendered_normally(self):
        rep = self._report_with_stage_n(5)
        assert "40000" in rep
        # a sufficient cell is not labelled insufficient
        assert "insufficient data (n=5)" not in rep.lower()

    def test_threshold_boundary_n3_is_sufficient(self):
        # n==3 is the boundary: sufficient (>= _INSUFFICIENT_N)
        rep = self._report_with_stage_n(3)
        assert "40000" in rep


class TestMtimeBound:
    """AC3: _load_all_metrics excludes run dirs whose mtime is older than the 90d
    window, on FILE MTIME (created_at is absent in 328/328 METRICS.json)."""

    def _make_run(self, ws: Path, run_id: str, age_days: float):
        run_dir = ws / "Projects" / "P" / ".artifacts" / "runs" / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        mf = run_dir / "METRICS.json"
        mf.write_text(json.dumps({
            "run_id": run_id, "project": "P", "profile": "bugfix",
            "status": "completed", "generated_at": "2026-01-01T00:00:00Z",
        }))
        # backdate BOTH the file and its dir mtime
        old = time.time() - age_days * 86400
        os.utime(mf, (old, old))
        os.utime(run_dir, (old, old))

    def test_old_run_excluded(self, tmp_path):
        self._make_run(tmp_path, "run_recent", age_days=5)
        self._make_run(tmp_path, "run_ancient", age_days=200)
        metrics = pa._load_all_metrics(tmp_path)
        ids = {m["run_id"] for m in metrics}
        assert "run_recent" in ids
        assert "run_ancient" not in ids

    def test_boundary_within_90d_included(self, tmp_path):
        self._make_run(tmp_path, "run_edge", age_days=89)
        metrics = pa._load_all_metrics(tmp_path)
        assert {m["run_id"] for m in metrics} == {"run_edge"}


class TestAdditiveSchema:
    """AC4: analyze_all_runs must keep emitting the exact keys EVALUATE/BUILD read
    (evaluate.md:425/435/440, build.md:64), so the gating change is additive-only."""

    def test_consumer_keys_present(self, tmp_path):
        # enough completed bugfix runs that estimation_accuracy populates its
        # keys (thin-data estimation legitimately returns {'sample_count': 0} —
        # the additive-schema invariant is: when data IS present, the keys the
        # consumers read are STILL there after the gating change).
        for i in range(5):
            rd = tmp_path / "Projects" / "P" / ".artifacts" / "runs" / f"run_{i}"
            rd.mkdir(parents=True, exist_ok=True)
            (rd / "METRICS.json").write_text(json.dumps({
                "run_id": f"run_{i}", "project": "P", "profile": "bugfix",
                "status": "completed", "generated_at": "2026-07-01T00:00:00Z",
                "total_tokens": 200000,
                "stage_tokens": {"build": 40000, "evaluate": 6000},
                "duration_minutes": 30,
            }))
        intel = pa.analyze_all_runs(tmp_path)
        dims = intel["dimensions"]
        # the consumer read-paths (evaluate.md:425/435/440, build.md:64) must resolve
        assert "high_risk_shapes" in dims["abandon_patterns"]
        assert "stage_estimates" in dims["estimation_accuracy"]
        assert "adversarial_value" in dims  # build_injection_recommendations lives here


class TestReportPathOption:
    """AC1 support: --report-path writes the markdown DIRECTLY to a given path
    (the weekly job's mechanism — no fragile cp/filename-guess, RP34/RP39)."""

    def test_report_path_writes_directly_and_mkdirs(self, tmp_path, monkeypatch):
        # a tiny real workspace so analyze_all_runs has 1 run
        rd = tmp_path / "Projects" / "P" / ".artifacts" / "runs" / "run_x"
        rd.mkdir(parents=True, exist_ok=True)
        (rd / "METRICS.json").write_text(json.dumps({
            "run_id": "run_x", "project": "P", "profile": "bugfix",
            "status": "completed", "generated_at": "2026-07-01T00:00:00Z",
        }))
        out = tmp_path / "pi.json"
        report = tmp_path / "nested" / "dir" / "pipeline-weekly.md"  # parent missing
        monkeypatch.setattr(sys, "argv", [
            "pipeline_analytics.py", "--workspace", str(tmp_path),
            "--output", str(out), "--report-path", str(report),
        ])
        pa.main()
        # report written to the EXACT path, parent auto-created
        assert report.exists()
        assert "Pipeline Health Report" in report.read_text()
