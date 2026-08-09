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


class TestReplacedDuplicateExclusion:
    """Gap-2: a run superseded by a COMPLETED successor is a replaced duplicate
    (a rerun), not a failure. It must be excluded from completion/abandon rate
    denominators. Negative controls (F7): a superseded-by-ABANDONED-successor
    and a plain crash-abandoned run must STAY counted as failures.
    """

    def _m(self, rid, status, profile="bugfix", reason=None, project="P"):
        d = {"run_id": rid, "project": project, "profile": profile,
             "status": status, "lifecycle_status": status, "telemetry": "legacy"}
        if reason is not None:
            d["abandon_reason"] = reason
        return d

    def _corpus(self):
        # A: superseded by completed B  -> EXCLUDED (replaced duplicate)
        # B: completed successor        -> counted, completed
        # C: superseded by abandoned-crash D -> KEPT as failure (F3 neg-control)
        # D: crash-abandoned successor  -> KEPT as failure
        # E: plain crash-abandoned      -> KEPT as failure (neg-control)
        return [
            self._m("A", "abandoned", reason="superseded_by_B"),
            self._m("B", "completed"),
            self._m("C", "abandoned", reason="superseded_by_D"),
            self._m("D", "abandoned", reason="orphaned_no_resume"),
            self._m("E", "abandoned", reason="crash_zombie"),
        ]

    def test_replaced_ids_identifies_only_completed_successor(self):
        ws = Path("/nonexistent")  # all successors are in-list; no disk needed
        replaced = pa._replaced_duplicate_ids(self._corpus(), ws)
        assert replaced == {("P", "A")}, replaced  # only A, NOT C (successor abandoned)

    def test_no_id_variant_excluded(self):
        m = [self._m("X", "abandoned", reason="superseded_by_completed_run"),
             self._m("Y", "completed")]
        replaced = pa._replaced_duplicate_ids(m, Path("/nonexistent"))
        assert replaced == {("P", "X")}

    def test_abandon_rate_excludes_replaced(self):
        replaced = pa._replaced_duplicate_ids(self._corpus(), Path("/nonexistent"))
        out = pa.analyze_abandon_patterns(self._corpus(), replaced)
        # live corpus = 4 (A excluded). genuine abandoned = C,D,E = 3.
        assert out["replaced_duplicates"] == 1
        assert out["total_abandoned"] == 3
        assert out["abandon_rate"] == pa._safe_pct(3, 4)

    def test_completion_rate_excludes_replaced(self):
        replaced = pa._replaced_duplicate_ids(self._corpus(), Path("/nonexistent"))
        out = pa.analyze_profile_accuracy(self._corpus(), replaced)
        # bugfix profile: A excluded; B completed; C,D,E abandoned => total 4, completed 1
        bug = out["profile_success_rates"]["bugfix"]
        assert bug["total_runs"] == 4
        assert bug["completion_rate"] == pa._safe_pct(1, 4)

    def test_revert_makes_it_red(self):
        """Non-vacuity: WITHOUT the exclusion (replaced=empty), A is counted as
        an abandoned failure and the numbers change — proving the exclusion is
        load-bearing (RED on revert)."""
        no_excl = pa.analyze_abandon_patterns(self._corpus(), set())
        assert no_excl["total_abandoned"] == 4  # A wrongly counted
        assert no_excl["abandon_rate"] == pa._safe_pct(4, 5)
        # and the corrected path differs:
        replaced = pa._replaced_duplicate_ids(self._corpus(), Path("/nonexistent"))
        corrected = pa.analyze_abandon_patterns(self._corpus(), replaced)
        assert corrected["abandon_rate"] != no_excl["abandon_rate"]

    def test_chain_two_hop_completed_terminal(self):
        # A -> B(superseded) -> C(completed): A and B are both replaced duplicates.
        m = [self._m("A", "abandoned", reason="superseded_by_B"),
             self._m("B", "abandoned", reason="superseded_by_C"),
             self._m("C", "completed")]
        replaced = pa._replaced_duplicate_ids(m, Path("/nonexistent"))
        assert replaced == {("P", "A"), ("P", "B")}

    def test_cycle_guard_fail_safe(self):
        # A -> B -> A cycle: neither has a completed terminal -> keep both.
        m = [self._m("A", "abandoned", reason="superseded_by_B"),
             self._m("B", "abandoned", reason="superseded_by_A")]
        replaced = pa._replaced_duplicate_ids(m, Path("/nonexistent"))
        assert replaced == set()

    def test_unresolvable_successor_kept(self):
        # successor id not in list and not on disk -> fail-safe keep.
        m = [self._m("A", "abandoned", reason="superseded_by_GHOST")]
        replaced = pa._replaced_duplicate_ids(m, Path("/nonexistent"))
        assert replaced == set()

    def test_disk_fallback_resolves_out_of_window_successor(self, tmp_path):
        """Successor outside the metrics list is resolved from run.json on disk."""
        proj = tmp_path / "Projects" / "P" / ".artifacts" / "runs"
        succ = proj / "run_succ"
        succ.mkdir(parents=True)
        (succ / "run.json").write_text(json.dumps({"id": "run_succ", "status": "completed"}))
        # A is in the metrics list; its successor is ONLY on disk.
        m = [self._m("A", "abandoned", reason="superseded_by_run_succ")]
        replaced = pa._replaced_duplicate_ids(m, tmp_path)
        assert replaced == {("P", "A")}

    def test_completed_then_superseded_keeps_telemetry(self):
        """F2: a run that COMPLETED and was later superseded must keep its
        telemetry status='completed' for the 4 telemetry dimensions, while the
        rate functions still treat it as a replaced duplicate via lifecycle."""
        # METRICS-path shape: status=completed (at-completion), lifecycle=abandoned.
        m = {"run_id": "A", "project": "P", "profile": "bugfix",
             "status": "completed", "lifecycle_status": "abandoned",
             "abandon_reason": "superseded_by_B", "total_tokens": 5000,
             "telemetry": "full"}
        succ = {"run_id": "B", "project": "P", "profile": "bugfix",
                "status": "completed", "lifecycle_status": "completed"}
        m["stage_tokens"] = {"build": 5000}  # A carries telemetry
        corpus = [m, succ]
        replaced = pa._replaced_duplicate_ids(corpus, Path("/nonexistent"))
        assert ("P", "A") in replaced  # excluded from rates (lifecycle abandoned)
        # F2 core: telemetry dimensions filter on the AT-COMPLETION `status`
        # (='completed'), NOT lifecycle_status — so A is NOT dropped from
        # telemetry despite being a replaced duplicate for rate purposes.
        eff = pa.analyze_stage_efficiency(corpus)
        assert "build" in eff["stages"], eff  # A's telemetry survived the supersede
        assert eff["stages"]["build"]["sample_count"] == 1

    def test_goal_performance_excludes_replaced(self):
        """Gap-2 same-class (Gate-2 F1): goal completion_rate must also exclude
        replaced duplicates and use lifecycle_status."""
        m = [
            self._m("A", "abandoned", profile="goal", reason="superseded_by_B"),
            self._m("B", "completed", profile="goal"),
            self._m("C", "abandoned", profile="goal", reason="crash_zombie"),
        ]
        replaced = pa._replaced_duplicate_ids(m, Path("/nonexistent"))
        out = pa.analyze_goal_performance(m, replaced)
        # A excluded (replaced) -> goal_runs = B,C = 2; completed = B = 1
        assert out["total_goal_runs"] == 2
        assert out["completion_rate"] == pa._safe_pct(1, 2)
        # revert (no exclusion) would count A -> 3 runs, different rate
        no_excl = pa.analyze_goal_performance(m, set())
        assert no_excl["total_goal_runs"] == 3
        assert no_excl["completion_rate"] != out["completion_rate"]

    def test_resolve_run_rejects_path_traversal(self, tmp_path):
        """Gate-2 F2: a path-escaping successor id must not read outside the
        runs dir — resolver returns None (fail-safe keep)."""
        cache = {}
        assert pa._resolve_run("P", "../../etc/passwd", tmp_path, cache) is None
        assert pa._resolve_run("P", "..", tmp_path, cache) is None
        assert pa._resolve_run("P", "a/b", tmp_path, cache) is None
        # and a traversal reason therefore never excludes:
        m = [self._m("A", "abandoned", reason="superseded_by_../../x")]
        assert pa._replaced_duplicate_ids(m, tmp_path) == set()


class TestGarbageExclusion:
    """run_0e68e235: garbage runs (abandoned/crash-paused, never delivered) must be
    excluded from the weekly-intelligence corpus — the THIRD stats consumer (Gate-1 #8).
    Delivered-but-mislabeled (abandoned WITH a completed reflect/deliver) stays and
    counts as a real completion. Integration test on real run dirs (run.json carries
    the stages that _is_garbage_run keys on; METRICS.json alone cannot decide)."""

    def _run(self, ws: Path, rid: str, *, status, stages, profile="full",
             abandon_reason=None, checkpoint=None, with_metrics=True):
        run_dir = ws / "Projects" / "P" / ".artifacts" / "runs" / rid
        run_dir.mkdir(parents=True, exist_ok=True)
        r = {"id": rid, "run_id": rid, "project": "P", "profile": profile,
             "status": status, "stages": stages,
             "created_at": "2026-08-01T00:00:00+00:00",
             "updated_at": "2026-08-01T00:00:00+00:00"}
        if abandon_reason:
            r["abandon_reason"] = abandon_reason
        if checkpoint:
            r["checkpoint"] = checkpoint
        (run_dir / "run.json").write_text(json.dumps(r))
        if with_metrics:
            (run_dir / "METRICS.json").write_text(json.dumps({
                "run_id": rid, "project": "P", "profile": profile, "status": status,
                "total_tokens": 1000, "stages_completed": len(stages),
                "stages_total": 9, "generated_at": "2026-08-01T00:00:00Z"}))
        return run_dir

    def test_garbage_excluded_from_corpus(self, tmp_path):
        # a real completion
        self._run(tmp_path, "run_done", status="completed",
                  stages=[{"stage": "deliver", "status": "completed"}])
        # genuine garbage: abandoned, died mid-pipeline
        self._run(tmp_path, "run_garbage", status="abandoned", abandon_reason="crash_zombie",
                  stages=[{"stage": "evaluate", "status": "completed"}])
        # crash-residue paused: garbage
        self._run(tmp_path, "run_crashpause", status="paused",
                  checkpoint={"reason": "session_crash_auto_detected"}, stages=[])
        out = pa.analyze_all_runs(tmp_path)
        # runs_analyzed counts only NON-garbage (garbage never enters the corpus)
        assert out["runs_analyzed"] == 1, out
        rates = out["dimensions"]["profile_accuracy"]["profile_success_rates"]
        assert rates["full"]["total_runs"] == 1
        assert rates["full"]["completion_rate"] == pa._safe_pct(1, 1)

    def test_delivered_abandoned_kept_as_completion(self, tmp_path):
        # mislabeled-done: abandoned BUT delivered → NOT garbage, stays in corpus
        self._run(tmp_path, "run_misdone", status="abandoned", abandon_reason="superseded_by_x",
                  stages=[{"stage": "deliver", "status": "completed"},
                          {"stage": "reflect", "status": "completed"}])
        out = pa.analyze_all_runs(tmp_path)
        assert out["runs_analyzed"] == 1, out
        # it survives as a run in the corpus (not dropped as garbage)
        rates = out["dimensions"]["profile_accuracy"]["profile_success_rates"]
        assert rates.get("full", {}).get("total_runs", 0) == 1

    def test_cancelled_and_failed_not_garbage(self, tmp_path):
        self._run(tmp_path, "run_cancel", status="cancelled", stages=[])
        self._run(tmp_path, "run_fail", status="failed", stages=[])
        out = pa.analyze_all_runs(tmp_path)
        assert out["runs_analyzed"] == 2, out
