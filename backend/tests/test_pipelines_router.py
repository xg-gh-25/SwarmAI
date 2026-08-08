"""Tests for the /api/pipelines endpoint.

Tests cover:
- Empty dashboard (no pipeline runs)
- Dashboard with active runs from filesystem
- Active-only filter
- Multi-project aggregation
- Corrupt/invalid JSON files are skipped gracefully
- Summary counts are accurate
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

# Patch the SwarmWS path before importing the router
_test_workspace = None


def _patched_swarmws():
    return _test_workspace


@pytest.fixture
def workspace(tmp_path):
    """Create a test workspace with Projects/ directory."""
    global _test_workspace
    _test_workspace = tmp_path
    return tmp_path


@pytest.fixture
def client(workspace):
    """Create a test client with patched workspace path."""
    with patch("routers.pipelines._get_swarmws", return_value=workspace):
        from main import app
        yield TestClient(app)


def _create_run(workspace: Path, project: str, run_id: str, **overrides) -> Path:
    """Helper: create a pipeline run file on disk."""
    artifacts_dir = workspace / "Projects" / project / ".artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    # Use current time as default for running pipelines (avoids stale detection)
    now_iso = datetime.now(timezone.utc).isoformat()
    status = overrides.get("status", "running")

    state = {
        "id": run_id,
        "project": project,
        "requirement": overrides.get("requirement", "Test requirement"),
        "profile": overrides.get("profile", "full"),
        "status": status,
        "stages": overrides.get("stages", []),
        "taste_decisions": overrides.get("taste_decisions", []),
        "budget": {},
        "checkpoint": overrides.get("checkpoint", None),
        "created_at": "2026-03-24T10:00:00+00:00",
        "updated_at": overrides.get("updated_at", now_iso),
        "completed_at": overrides.get("completed_at", None),
    }
    if "abandon_reason" in overrides:
        state["abandon_reason"] = overrides["abandon_reason"]

    run_file = artifacts_dir / f"pipeline-run-{run_id}.json"
    run_file.write_text(json.dumps(state), encoding="utf-8")
    return run_file


class TestPipelinesEndpoint:
    def test_empty_dashboard(self, client, workspace):
        resp = client.get("/api/pipelines")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 0
        assert data["pipelines"] == []
        assert data["summary"]["running"] == 0

    def test_returns_active_run(self, client, workspace):
        _create_run(workspace, "TestProject", "run_abc123",
                     requirement="Add payment retry",
                     stages=[
                         {"stage": "evaluate", "status": "completed", "token_cost": 9000},
                         {"stage": "build", "status": "completed", "token_cost": 45000},
                     ])

        resp = client.get("/api/pipelines")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 1

        pipeline = data["pipelines"][0]
        assert pipeline["id"] == "run_abc123"
        assert pipeline["project"] == "TestProject"
        assert pipeline["requirement"] == "Add payment retry"
        assert pipeline["status"] == "running"
        assert pipeline["stages_completed"] == 2
        assert pipeline["stages_total"] == 8
        assert pipeline["tokens_consumed"] == 54000
        assert pipeline["progress"] == "2/8"

    def test_active_only_filter(self, client, workspace):
        now_iso = datetime.now(timezone.utc).isoformat()
        _create_run(workspace, "Proj", "run_active", status="running",
                     updated_at=now_iso)
        _create_run(workspace, "Proj", "run_done", status="completed",
                     updated_at="2026-03-24T10:00:00+00:00")

        # Without filter: both
        resp = client.get("/api/pipelines")
        assert resp.json()["count"] == 2

        # With filter: only active
        resp = client.get("/api/pipelines?active=true")
        data = resp.json()
        assert data["count"] == 1
        assert data["pipelines"][0]["id"] == "run_active"

    def test_aged_paused_decision_run_still_active(self, client, workspace):
        """run_2568c3fb: a paused-decision run whose FILE MTIME is old (paused runs
        are never re-touched and never auto-abandoned) MUST still appear in
        ?active=true. A mtime pre-filter was rejected precisely because it would
        silently drop this run from the 🔔 attention queue — the one item that most
        needs to stay surfaced. The `active` filter is a pure status filter, not an
        mtime heuristic."""
        import os
        old = _create_run(workspace, "Proj", "run_old_paused", status="paused",
                          updated_at="2026-01-01T00:00:00+00:00",
                          checkpoint={"reason": "Gate BLOCK", "stage": "build"})
        old_epoch = datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp()
        os.utime(old, (old_epoch, old_epoch))  # freeze mtime far in the past

        active = client.get("/api/pipelines?active=true").json()
        ids_active = {p["id"] for p in active["pipelines"]}
        assert "run_old_paused" in ids_active, \
            "an aged paused-decision run must remain in the active/attention queue"

    def test_multi_project(self, client, workspace):
        _create_run(workspace, "ProjectA", "run_a1")
        _create_run(workspace, "ProjectB", "run_b1")

        resp = client.get("/api/pipelines")
        data = resp.json()
        assert data["count"] == 2
        projects = {p["project"] for p in data["pipelines"]}
        assert projects == {"ProjectA", "ProjectB"}

    def test_paused_run_with_checkpoint(self, client, workspace):
        _create_run(workspace, "Proj", "run_paused",
                     status="paused",
                     checkpoint={
                         "reason": "L2 BLOCK: ambiguous scope",
                         "stage": "plan",
                         "checkpointed_at": "2026-03-24T10:05:00+00:00",
                         "completed_stages": ["evaluate", "think"],
                     })

        resp = client.get("/api/pipelines")
        pipeline = resp.json()["pipelines"][0]
        assert pipeline["status"] == "paused"
        assert pipeline["checkpoint"]["reason"] == "L2 BLOCK: ambiguous scope"
        assert pipeline["checkpoint"]["stage"] == "plan"

    def test_summary_counts(self, client, workspace):
        _create_run(workspace, "P", "run_1", status="running",
                     stages=[{"stage": "evaluate", "status": "completed", "token_cost": 10000}])
        _create_run(workspace, "P", "run_2", status="paused",
                     stages=[{"stage": "evaluate", "status": "completed", "token_cost": 8000}])
        _create_run(workspace, "P", "run_3", status="completed",
                     stages=[{"stage": "evaluate", "status": "completed", "token_cost": 5000}])

        resp = client.get("/api/pipelines")
        summary = resp.json()["summary"]
        assert summary["running"] == 1
        assert summary["paused"] == 1
        assert summary["completed"] == 1
        assert summary["total_tokens"] == 23000

    def test_corrupt_json_skipped(self, client, workspace):
        # Create a valid run
        _create_run(workspace, "Proj", "run_valid")

        # Create a corrupt file
        artifacts_dir = workspace / "Projects" / "Proj" / ".artifacts"
        (artifacts_dir / "pipeline-run-run_corrupt.json").write_text("not json{{{")

        resp = client.get("/api/pipelines")
        assert resp.status_code == 200
        assert resp.json()["count"] == 1  # corrupt skipped

    def test_invalid_status_falls_back(self, client, workspace):
        _create_run(workspace, "Proj", "run_bad_status", status="invalid_status")

        resp = client.get("/api/pipelines")
        assert resp.status_code == 200
        # Should fall back to "running" instead of crashing
        assert resp.json()["pipelines"][0]["status"] == "running"

    def test_trivial_profile_stage_count(self, client, workspace):
        _create_run(workspace, "Proj", "run_trivial", profile="trivial")

        resp = client.get("/api/pipelines")
        # trivial has 7 stages: evaluate,think,build,review,test,deliver,reflect.
        # THINK was added to bugfix/trivial in commit 15ce90e2 ("Not thinking
        # before coding = patching"); that commit updated test_pipeline_run.py +
        # test_pipeline_profiles.py but missed this sibling file (6 was stale).
        assert resp.json()["pipelines"][0]["stages_total"] == 7

    def test_abandoned_surfaced_in_http_dashboard(self, client, workspace):
        """AC2 parity: the HTTP dashboard (consumed by the Radar sidebar) must
        surface abandoned runs — both the summary count and per-run abandon_reason
        — distinguishing an unrecovered orphan from a real supersession. Before
        the fix, 'abandoned' fell back to 'running' (no enum value) and the
        reason was dropped entirely."""
        _create_run(workspace, "Proj", "run_orphan", status="abandoned",
                    abandon_reason="orphaned_no_resume")
        _create_run(workspace, "Proj", "run_superseded", status="abandoned",
                    abandon_reason="superseded_by_run_xyz")

        resp = client.get("/api/pipelines")
        assert resp.status_code == 200
        body = resp.json()
        assert body["summary"]["abandoned"] == 2, \
            f"expected 2 abandoned, got {body['summary'].get('abandoned')}"
        by_id = {p["id"]: p for p in body["pipelines"]}
        # status must render as 'abandoned', not fall back to 'running'
        assert by_id["run_orphan"]["status"] == "abandoned"
        assert by_id["run_orphan"]["abandon_reason"] == "orphaned_no_resume"
        assert by_id["run_superseded"]["abandon_reason"] == "superseded_by_run_xyz"

    def test_taste_decisions_counted(self, client, workspace):
        _create_run(workspace, "Proj", "run_taste",
                     taste_decisions=[
                         {"stage": "think", "description": "d1", "classification": "taste"},
                         {"stage": "build", "description": "d2", "classification": "taste"},
                     ])

        resp = client.get("/api/pipelines")
        assert resp.json()["pipelines"][0]["taste_decisions"] == 2

    def test_stale_running_presented_failed_disk_untouched(self, client, workspace):
        """A run stuck 'running' >60min is PRESENTED as failed — but the GET path is
        READ-ONLY (run_2568c3fb): it must NOT rewrite the file. The real on-disk
        stale→failed transition is owned by the reaper (artifact_cli), never a GET."""
        stale_time = "2026-01-01T00:00:00+00:00"  # definitely stale
        run_file = _create_run(workspace, "Proj", "run_stale",
                               status="running", updated_at=stale_time)

        resp = client.get("/api/pipelines")
        pipeline = resp.json()["pipelines"][0]
        assert pipeline["status"] == "failed"  # presentation coercion

        # A GET must NOT write disk — on-disk status is preserved as 'running'.
        on_disk = json.loads(run_file.read_text())
        assert on_disk["status"] == "running", "GET must not mutate the run file"
        assert "failure_reason" not in on_disk, "GET must not stamp failure_reason"

    def test_stale_detection_ignores_non_running(self, client, workspace):
        """Completed and paused runs are not affected by stale detection."""
        old_time = "2026-01-01T00:00:00+00:00"
        _create_run(workspace, "Proj", "run_completed",
                     status="completed", updated_at=old_time)
        _create_run(workspace, "Proj", "run_paused",
                     status="paused", updated_at=old_time)

        resp = client.get("/api/pipelines")
        statuses = {p["id"]: p["status"] for p in resp.json()["pipelines"]}
        assert statuses["run_completed"] == "completed"
        assert statuses["run_paused"] == "paused"

    def test_recent_running_not_marked_stale(self, client, workspace):
        """A run updated within the threshold stays running."""
        recent = datetime.now(timezone.utc).isoformat()
        _create_run(workspace, "Proj", "run_fresh",
                     status="running", updated_at=recent)

        resp = client.get("/api/pipelines")
        assert resp.json()["pipelines"][0]["status"] == "running"

    # --- terminal-but-crashed stale runs must NOT be false-failed (run_0f03fa9d) --
    #
    # A run that finished all stages (a completed reflect/deliver marker) but
    # crashed before `run-update --status completed` is left status="running" on
    # disk. The stale detector must honor is_terminal_run (the SAME predicate
    # artifact_cli._abandon_verdict:804 and proactive_intelligence:858 use to SKIP
    # terminal runs) and NOT flip it to "failed" — that mislabels a delivered run.
    # Real mid-pipeline orphans (no reflect/deliver) keep the "failed" verdict.

    def test_stale_terminal_run_not_marked_failed(self, client, workspace):
        """A stale status=running run with a completed reflect stage is terminal
        (it delivered, just crashed before the completion gate) — the stale
        detector must NOT rewrite it to 'failed'. On-disk status is preserved."""
        stale_time = "2026-01-01T00:00:00+00:00"
        run_file = _create_run(
            workspace, "Proj", "run_terminal_stale",
            status="running", updated_at=stale_time, profile="bugfix",
            stages=[
                {"stage": "evaluate", "status": "completed", "token_cost": 5000},
                {"stage": "think", "status": "completed", "token_cost": 5000},
                {"stage": "plan", "status": "completed", "token_cost": 5000},
                {"stage": "build", "status": "completed", "token_cost": 5000},
                {"stage": "review", "status": "completed", "token_cost": 5000},
                {"stage": "test", "status": "completed", "token_cost": 5000},
                {"stage": "deliver", "status": "completed", "token_cost": 5000},
                {"stage": "reflect", "status": "completed", "token_cost": 5000},
            ],
        )
        client.get("/api/pipelines")
        on_disk = json.loads(run_file.read_text())
        assert on_disk["status"] != "failed", \
            "terminal-but-crashed run must not be false-failed"
        # No spurious failure_reason written
        assert "failure_reason" not in on_disk

    def test_stale_nonterminal_run_presented_failed(self, client, workspace):
        """Regression guard: a genuinely mid-pipeline stale run (no reflect/deliver
        completed) is a real orphan and MUST still be PRESENTED as failed — the fix
        must not over-broaden and hide real failures. READ-ONLY: disk preserved
        (run_2568c3fb — the reaper owns the on-disk transition, not the GET path)."""
        stale_time = "2026-01-01T00:00:00+00:00"
        run_file = _create_run(
            workspace, "Proj", "run_nonterminal_stale",
            status="running", updated_at=stale_time, profile="bugfix",
            stages=[
                {"stage": "evaluate", "status": "completed", "token_cost": 5000},
                {"stage": "think", "status": "completed", "token_cost": 5000},
            ],
        )
        resp = client.get("/api/pipelines")
        pipeline = next(p for p in resp.json()["pipelines"] if p["id"] == "run_nonterminal_stale")
        assert pipeline["status"] == "failed"  # presentation coercion
        on_disk = json.loads(run_file.read_text())
        assert on_disk["status"] == "running", "GET must not mutate the run file"

    def test_stale_terminal_run_excluded_from_default_active(self, client, workspace):
        """A terminal-but-running run must not render as an active 'running' run in
        the DEFAULT dashboard view either (not just ?active=true). Otherwise it
        shows 'running forever' — the lie the false-fail was masking."""
        stale_time = "2026-01-01T00:00:00+00:00"
        _create_run(
            workspace, "Proj", "run_terminal_default",
            status="running", updated_at=stale_time, profile="bugfix",
            stages=[
                {"stage": "evaluate", "status": "completed", "token_cost": 5000},
                {"stage": "deliver", "status": "completed", "token_cost": 5000},
            ],
        )
        resp = client.get("/api/pipelines")
        summary = resp.json()["summary"]
        # A terminal zombie must not inflate the 'running' count in the default view.
        assert summary["running"] == 0, \
            "terminal-but-crashed run must not count as running in default view"


# --- pause_kind classification + terminal-zombie exclude (run_3d61db5b) -------
#
# A crashed session leaves paused runs stamped checkpoint.reason ==
# "session_crash_auto_detected" (the canonical _CRASH_ZOMBIE_REASON). These are
# NOT real decision-pauses (Gate BLOCK / L2 / budget) — they are residue. The
# API must classify them (pause_kind) so consumers (Radar NEEDS YOU) can drop
# them, AND must exclude an already-FINISHED zombie (reflect/deliver done but
# status flipped to paused+crash-reason) from the active list entirely.

_CRASH_REASON = "session_crash_auto_detected"  # mirrors artifact_cli._CRASH_ZOMBIE_REASON


class TestPauseKindClassification:
    def test_crash_residue_paused_gets_pause_kind_crash_residue(self, client, workspace):
        """AC1/AC4: a paused run whose reason IS the crash marker → pause_kind='crash_residue'."""
        _create_run(workspace, "Proj", "run_crash",
                    status="paused",
                    stages=[{"stage": "evaluate", "status": "completed", "token_cost": 5000}],
                    checkpoint={"reason": _CRASH_REASON, "stage": "think",
                                "checkpointed_at": "2026-07-03T10:00:00+00:00",
                                "completed_stages": ["evaluate"]})
        resp = client.get("/api/pipelines")
        p = next(x for x in resp.json()["pipelines"] if x["id"] == "run_crash")
        assert p["status"] == "paused"
        assert p["pause_kind"] == "crash_residue"

    def test_decision_paused_gets_pause_kind_decision(self, client, workspace):
        """AC2/AC4: a paused run with a REAL decision reason → pause_kind='decision'."""
        _create_run(workspace, "Proj", "run_decision",
                    status="paused",
                    stages=[{"stage": "evaluate", "status": "completed", "token_cost": 5000}],
                    checkpoint={"reason": "Gate-1 BLOCK: ambiguous scope", "stage": "plan",
                                "checkpointed_at": "2026-07-03T10:00:00+00:00",
                                "completed_stages": ["evaluate", "think"]})
        resp = client.get("/api/pipelines")
        p = next(x for x in resp.json()["pipelines"] if x["id"] == "run_decision")
        assert p["status"] == "paused"
        assert p["pause_kind"] == "decision"

    def test_non_paused_run_has_null_pause_kind(self, client, workspace):
        """A running/completed run carries no pause_kind (None)."""
        _create_run(workspace, "Proj", "run_running", status="running",
                    stages=[{"stage": "evaluate", "status": "completed", "token_cost": 5000}])
        resp = client.get("/api/pipelines")
        p = next(x for x in resp.json()["pipelines"] if x["id"] == "run_running")
        assert p["pause_kind"] is None

    def test_terminal_zombie_excluded_from_active(self, client, workspace):
        """AC3: a FINISHED run (reflect done) flipped to paused+crash-reason is a
        terminal zombie — is_terminal_run true — and must NOT appear in ?active=true."""
        now_iso = datetime.now(timezone.utc).isoformat()
        _create_run(workspace, "Proj", "run_zombie",
                    status="paused", updated_at=now_iso,
                    stages=[
                        {"stage": "evaluate", "status": "completed", "token_cost": 5000},
                        {"stage": "think", "status": "completed", "token_cost": 5000},
                        {"stage": "build", "status": "completed", "token_cost": 5000},
                        {"stage": "review", "status": "completed", "token_cost": 5000},
                        {"stage": "test", "status": "completed", "token_cost": 5000},
                        {"stage": "deliver", "status": "completed", "token_cost": 5000},
                        {"stage": "reflect", "status": "completed", "token_cost": 5000},
                    ],
                    checkpoint={"reason": _CRASH_REASON, "stage": "reflect",
                                "checkpointed_at": now_iso, "completed_stages": []})
        # A genuinely-resumable mid-pipeline paused run (NOT terminal) must survive.
        _create_run(workspace, "Proj", "run_midpause",
                    status="paused", updated_at=now_iso,
                    stages=[
                        {"stage": "evaluate", "status": "completed", "token_cost": 5000},
                        {"stage": "think", "status": "completed", "token_cost": 5000},
                    ],
                    checkpoint={"reason": _CRASH_REASON, "stage": "plan",
                                "checkpointed_at": now_iso, "completed_stages": ["evaluate", "think"]})

        resp = client.get("/api/pipelines?active=true")
        ids = {p["id"] for p in resp.json()["pipelines"]}
        assert "run_zombie" not in ids, "terminal zombie must be excluded from active"
        assert "run_midpause" in ids, "a mid-pipeline paused run must remain active/resumable"


# ── Retro-Analytics endpoints (run_f8494370) ─────────────────────────────────

def _create_run_dir(workspace: Path, project: str, run_id: str, *, status="completed",
                    profile="full", requirement="Do X", created_at="2026-08-01T10:00:00+00:00",
                    completed_at=None, stages=None, budget=None, commits=None,
                    metrics=None, report_md=None, checkpoint=None):
    """Create a NEW-path run: Projects/<p>/.artifacts/runs/<id>/{run.json,METRICS.json,REPORT.md}."""
    run_dir = workspace / "Projects" / project / ".artifacts" / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    state = {
        "id": run_id, "project": project, "requirement": requirement,
        "profile": profile, "status": status,
        "stages": stages if stages is not None else [
            {"stage": "evaluate", "status": "completed", "token_cost": 4000},
        ],
        "taste_decisions": [], "created_at": created_at,
        "updated_at": completed_at or created_at,
        "budget": budget or {"stage_estimates": {"evaluate": 6000, "build": 40000}},
        "checkpoint": checkpoint,
    }
    if completed_at:
        state["completed_at"] = completed_at
    if commits is not None:
        state["commits"] = commits
    (run_dir / "run.json").write_text(json.dumps(state), encoding="utf-8")
    if metrics is not None:
        (run_dir / "METRICS.json").write_text(json.dumps(metrics), encoding="utf-8")
    if report_md is not None:
        (run_dir / "REPORT.md").write_text(report_md, encoding="utf-8")
    return run_dir


class TestPipelineAnalytics:
    def test_empty_analytics_200(self, client, workspace):
        resp = client.get("/api/pipelines/analytics")
        assert resp.status_code == 200
        d = resp.json()
        assert d["overall"]["total_runs"] == 0
        assert d["by_project"] == []
        assert d["window"] == "30d"

    def test_overall_and_by_project(self, client, workspace):
        now = datetime.now(timezone.utc).isoformat()
        # Project A: 1 completed (with metrics), 1 abandoned
        _create_run_dir(workspace, "ProjA", "run_a1", status="completed",
                        created_at=now, completed_at=now,
                        metrics={"total_tokens": 30000, "duration_minutes": 12.0,
                                 "stage_tokens": {"evaluate": 4000}, "stages_completed": 7, "stages_total": 7,
                                 "status": "completed"})
        _create_run_dir(workspace, "ProjA", "run_a2", status="abandoned", created_at=now,
                        metrics={"total_tokens": 5000, "duration_minutes": None,
                                 "stage_tokens": {}, "stages_completed": 2, "stages_total": 8, "status": "abandoned"})
        # Project B: 1 completed
        _create_run_dir(workspace, "ProjB", "run_b1", status="completed", profile="bugfix",
                        created_at=now, completed_at=now,
                        metrics={"total_tokens": 20000, "duration_minutes": 8.0,
                                 "stage_tokens": {"evaluate": 3000}, "stages_completed": 8, "stages_total": 8, "status": "completed"})
        resp = client.get("/api/pipelines/analytics?window=ytd")
        assert resp.status_code == 200
        d = resp.json()
        assert d["window"] == "ytd"
        assert d["overall"]["total_runs"] == 3
        assert d["overall"]["completed"] == 2
        assert d["overall"]["aborted_count"] == 1
        assert d["overall"]["tokens_actual"] == 55000
        assert d["overall"]["profile_mix"]["full"] == 2
        assert d["overall"]["profile_mix"]["bugfix"] == 1
        # by-project grouping present, both projects
        projects = {g["project"]: g for g in d["by_project"]}
        assert "ProjA" in projects and "ProjB" in projects
        assert projects["ProjA"]["run_count"] == 2
        assert projects["ProjA"]["aborted_count"] == 1
        assert projects["ProjB"]["completion_rate"] == 1.0

    def test_est_tokens_from_budget(self, client, workspace):
        now = datetime.now(timezone.utc).isoformat()
        _create_run_dir(workspace, "ProjE", "run_e1", status="completed",
                        created_at=now, completed_at=now,
                        budget={"stage_estimates": {"evaluate": 6000, "build": 40000}},
                        metrics={"total_tokens": 30000, "duration_minutes": 5.0,
                                 "stage_tokens": {}, "stages_completed": 7, "stages_total": 7, "status": "completed"})
        d = client.get("/api/pipelines/analytics?window=ytd").json()
        assert d["overall"]["tokens_est"] == 46000  # 6000+40000


class TestPipelineRunDetail:
    def test_detail_returns_retro(self, client, workspace):
        _create_run_dir(
            workspace, "ProjD", "run_d1", status="completed",
            requirement="Ship the thing",
            completed_at="2026-08-01T10:20:00+00:00",
            stages=[
                {"stage": "evaluate", "status": "completed", "token_cost": 4000},
                {"stage": "reflect", "status": "completed",
                 "lessons": ["Lesson one about X", "Lesson two about Y"]},
            ],
            budget={"stage_estimates": {"evaluate": 6000, "build": 40000}},
            commits=[{"repo": "/src", "sha": "abc123", "files": ["a.py"]}],
            metrics={"total_tokens": 30000, "duration_minutes": 20.0,
                     "stage_tokens": {"evaluate": 4000, "build": 38000},
                     "stages_completed": 7, "stages_total": 7, "status": "completed"},
            report_md="# Report\nTL;DR: it works",
        )
        resp = client.get("/api/pipelines/run_d1")
        assert resp.status_code == 200
        d = resp.json()
        assert d["id"] == "run_d1"
        assert d["requirement"] == "Ship the thing"
        assert "TL;DR: it works" in d["report_md"]
        assert d["reflect_lessons"] == ["Lesson one about X", "Lesson two about Y"]
        assert d["commits"][0]["sha"] == "abc123"
        assert d["cycle_time_min"] == 20.0
        # est-vs-actual per stage
        st = {s["stage"]: s for s in d["stage_tokens"]}
        assert st["evaluate"]["est"] == 6000 and st["evaluate"]["actual"] == 4000
        assert st["build"]["est"] == 40000 and st["build"]["actual"] == 38000

    def test_detail_report_path_present_when_report_exists(self, client, workspace):
        """run_929024a8: detail exposes a workspace-relative report_path so the overlay
        can open REPORT.md in Canvas via swarm:open-file. Present iff REPORT.md exists."""
        _create_run_dir(
            workspace, "ProjRP", "run_rp1", status="completed",
            completed_at="2026-08-01T10:20:00+00:00",
            metrics={"total_tokens": 100, "duration_minutes": 5.0, "stage_tokens": {},
                     "stages_completed": 7, "stages_total": 7, "status": "completed"},
            report_md="# Report\nbody",
        )
        d = client.get("/api/pipelines/run_rp1").json()
        assert d["report_path"] == "Projects/ProjRP/.artifacts/runs/run_rp1/REPORT.md", \
            "report_path must be the workspace-relative REPORT.md path (resolve Stage 1)"

    def test_detail_report_path_none_when_no_report(self, client, workspace):
        """No REPORT.md on disk → report_path is None (the Canvas button hides)."""
        _create_run_dir(
            workspace, "ProjRP", "run_rp2", status="paused",
            created_at="2026-08-01T10:00:00+00:00",
            stages=[{"stage": "evaluate", "status": "completed", "token_cost": 4000}],
            # report_md omitted → no REPORT.md written
        )
        d = client.get("/api/pipelines/run_rp2").json()
        assert d["report_path"] is None, "no REPORT.md → report_path None"

    def test_detail_404_for_missing_run(self, client, workspace):
        assert client.get("/api/pipelines/run_nope").status_code == 404

    def test_detail_rejects_path_traversal(self, client, workspace):
        # The Gate-1 BLOCK: a traversal token must NEVER read a file outside runs/.
        # FastAPI won't match a raw slash in {run_id}; test the encoded + dotted forms.
        for tok in ["..%2f..%2f..%2fetc%2fpasswd", "run_..%2f..%2fsecret", "..", "run_"]:
            r = client.get(f"/api/pipelines/{tok}")
            assert r.status_code == 404, f"traversal token {tok!r} must 404, got {r.status_code}"

    def test_detail_partial_metrics_for_paused(self, client, workspace):
        # A paused run with NO METRICS.json → endpoint generates partial on-read (G2).
        _create_run_dir(
            workspace, "ProjP", "run_p1", status="paused",
            created_at="2026-08-01T10:00:00+00:00",  # no completed_at, no metrics file
            stages=[{"stage": "evaluate", "status": "completed", "token_cost": 4000}],
            checkpoint={"reason": "Gate 1 BLOCK: needs decision", "stage": "build"},
        )
        resp = client.get("/api/pipelines/run_p1")
        assert resp.status_code == 200
        d = resp.json()
        assert d["status"] == "paused"
        assert d["cycle_time_min"] is None  # None-safe, no completed_at
        assert d["checkpoint_reason"] == "Gate 1 BLOCK: needs decision"


class TestAnalyticsCapAndCache:
    """run_258290ed: by-project run details capped at 20 (run_count keeps true
    total), and on-read generated METRICS are persisted for terminal runs."""

    def test_caps_run_details_at_20_but_run_count_is_true_total(self, client, workspace):
        now = datetime.now(timezone.utc).isoformat()
        for i in range(25):
            _create_run_dir(
                workspace, "BigProj", f"run_big{i:02d}", status="completed",
                created_at=now, completed_at=now,
                metrics={"total_tokens": 1000, "duration_minutes": 5.0,
                         "stage_tokens": {}, "stages_completed": 7, "stages_total": 7, "status": "completed"},
            )
        d = client.get("/api/pipelines/analytics?window=ytd").json()
        g = next(x for x in d["by_project"] if x["project"] == "BigProj")
        assert g["run_count"] == 25, "run_count must be the TRUE total"
        assert len(g["runs"]) == 20, "detail list capped at 20"

    def test_needy_runs_are_never_capped_away(self, client, workspace):
        """Gate-2 MEDIUM (run_929024a8): the overlay's pinned Needs-you region +
        focus filter must surface EVERY needy (abandoned/paused-decision) run — so a
        needy run OLDER than the newest-20 must still appear in the payload, else the
        'N need you' count and the visible needy rows disagree in busy projects (the
        exact motivating case). Completed runs stay capped at 20; needy runs don't."""
        # 22 completed (newest) + 1 abandoned that is the OLDEST (sorts past the cap).
        for i in range(22):
            _create_run_dir(
                workspace, "BusyProj", f"run_ok{i:02d}", status="completed",
                created_at=f"2026-08-02T{i:02d}:00:00+00:00",
                completed_at=f"2026-08-02T{i:02d}:30:00+00:00",
                metrics={"total_tokens": 1, "duration_minutes": 1.0, "stage_tokens": {},
                         "stages_completed": 7, "stages_total": 7, "status": "completed"},
            )
        _create_run_dir(
            workspace, "BusyProj", "run_needy_old", status="abandoned",
            created_at="2026-01-01T00:00:00+00:00",  # oldest (no completed_at → updated_at=created_at)
        )
        d = client.get("/api/pipelines/analytics?window=ytd").json()
        g = next(x for x in d["by_project"] if x["project"] == "BusyProj")
        assert g["run_count"] == 23, "run_count = true total"
        ids = {r["id"] for r in g["runs"]}
        assert "run_needy_old" in ids, "an abandoned run must NEVER be capped away (Gate-2 MEDIUM)"
        # completed runs still bounded (20 newest completed + the 1 needy = 21)
        assert len(g["runs"]) == 21, "newest-20 completed + all needy (1) = 21"
        assert g["aborted_count"] == 1, "count matches the surfaced needy row"

    def test_summary_report_path_present_only_for_runs_with_report(self, client, workspace):
        """run_929024a8: the by-project summary rows carry report_path so the LIST's
        row report-button can open Canvas without a detail fetch. Present iff REPORT.md
        exists; None otherwise. (Gate-1 #6: only the visible ≤20 are stat'd.)"""
        now = datetime.now(timezone.utc).isoformat()
        _create_run_dir(workspace, "RPProj", "run_has", status="completed",
                        created_at=now, completed_at=now,
                        metrics={"total_tokens": 1, "duration_minutes": 1.0, "stage_tokens": {},
                                 "stages_completed": 7, "stages_total": 7, "status": "completed"},
                        report_md="# has a report")
        _create_run_dir(workspace, "RPProj", "run_none", status="completed",
                        created_at=now, completed_at=now,
                        metrics={"total_tokens": 1, "duration_minutes": 1.0, "stage_tokens": {},
                                 "stages_completed": 7, "stages_total": 7, "status": "completed"})
        d = client.get("/api/pipelines/analytics?window=ytd").json()
        g = next(x for x in d["by_project"] if x["project"] == "RPProj")
        by_id = {r["id"]: r for r in g["runs"]}
        assert by_id["run_has"]["report_path"] == "Projects/RPProj/.artifacts/runs/run_has/REPORT.md"
        assert by_id["run_none"]["report_path"] is None

    def test_persists_metrics_for_terminal_run(self, client, workspace):
        now = datetime.now(timezone.utc).isoformat()
        _create_run_dir(workspace, "CacheProj", "run_c1", status="completed",
                        created_at=now, completed_at=now)  # NO metrics file
        mfile = workspace / "Projects/CacheProj/.artifacts/runs/run_c1/METRICS.json"
        assert not mfile.exists()
        client.get("/api/pipelines/analytics?window=ytd")
        assert mfile.exists(), "on-read metrics for a terminal run must be persisted (cache)"

    def test_does_not_persist_metrics_for_running_run(self, client, workspace):
        now = datetime.now(timezone.utc).isoformat()
        # running, no reflect/deliver stage → NOT terminal → must not cache
        _create_run_dir(workspace, "LiveProj", "run_l1", status="running",
                        created_at=now,
                        stages=[{"stage": "build", "status": "completed"}])
        mfile = workspace / "Projects/LiveProj/.artifacts/runs/run_l1/METRICS.json"
        client.get("/api/pipelines/analytics?window=ytd")
        assert not mfile.exists(), "a running run's metrics must NOT be cached (stale-freeze)"

    def test_persists_metrics_for_finished_but_paused_run(self, client, workspace):
        # Gate-2 MED (run_258290ed): the orphan-transition class — a run whose
        # stages are all done (reflect completed) but status flipped to 'paused'
        # by a session-refresh crash marker. is_terminal_run catches it; a naive
        # status-tuple would regenerate its metrics on every open forever.
        now = datetime.now(timezone.utc).isoformat()
        _create_run_dir(
            workspace, "OrphanProj", "run_o1", status="paused", created_at=now,
            stages=[
                {"stage": "deliver", "status": "completed"},
                {"stage": "reflect", "status": "completed"},
            ],
            checkpoint={"reason": "session_crash_auto_detected", "stage": "reflect"},
        )
        mfile = workspace / "Projects/OrphanProj/.artifacts/runs/run_o1/METRICS.json"
        client.get("/api/pipelines/analytics?window=ytd")
        assert mfile.exists(), "a finished-but-paused (terminal) run's metrics must be cached"
