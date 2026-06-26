"""Tests for pipeline run lifecycle management: auto-abandon and cleanup.

Tests AC1-AC5 of pipeline run_fd0064e6:
- AC1: auto-abandon stale same-project running runs (>2h) on new run start
- AC2: cleanup-orphans subcommand for batch cleanup
- AC3: one-time data fix reclassifies 14 failed + 6 orphan
- AC4: legitimate concurrent runs (<2h) NOT touched
- AC5: all changes have passing tests (this file)
"""

import json
import os
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture
def workspace(tmp_path):
    """Create a fake workspace with pipeline runs."""
    project_dir = tmp_path / "Projects" / "TestProject" / ".artifacts" / "runs"
    project_dir.mkdir(parents=True)
    return tmp_path


def _create_run(workspace, project, run_id, status="running", hours_ago=0, stages=None):
    """Helper: create a run.json with given status and age."""
    run_dir = workspace / "Projects" / project / ".artifacts" / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    created = datetime.now(timezone.utc) - timedelta(hours=hours_ago)
    updated = created

    run_data = {
        "id": run_id,
        "project": project,
        "requirement": f"Test requirement for {run_id}",
        "profile": "full",
        "status": status,
        "stages": stages or [],
        "created_at": created.isoformat(),
        "updated_at": updated.isoformat(),
    }
    run_file = run_dir / "run.json"
    run_file.write_text(json.dumps(run_data, indent=2))
    return run_file


def _read_run(run_file):
    """Read and parse a run.json."""
    return json.loads(run_file.read_text())


class TestAutoAbandonOnNewRun:
    """AC1: When a new run starts, stale same-project running runs get abandoned."""

    def test_stale_run_gets_abandoned(self, workspace):
        """A run that's been 'running' for >2h gets auto-abandoned."""
        from scripts.artifact_cli import _auto_abandon_stale_runs

        # Create a stale run (3 hours old)
        stale_file = _create_run(workspace, "TestProject", "run_old123", "running", hours_ago=3)

        # Trigger auto-abandon
        with patch("scripts.artifact_cli._get_workspace", return_value=workspace):
            abandoned = _auto_abandon_stale_runs("TestProject", "run_new456")

        # Verify stale run was abandoned
        stale_data = _read_run(stale_file)
        assert stale_data["status"] == "abandoned"
        assert "superseded_by_run_new456" in stale_data.get("abandon_reason", "")
        assert abandoned == 1

    def test_fresh_run_not_touched(self, workspace):
        """AC4: A run that's <2h old is NOT abandoned."""
        from scripts.artifact_cli import _auto_abandon_stale_runs

        # Create a fresh run (30 min old)
        fresh_file = _create_run(workspace, "TestProject", "run_fresh", "running", hours_ago=0.5)

        with patch("scripts.artifact_cli._get_workspace", return_value=workspace):
            abandoned = _auto_abandon_stale_runs("TestProject", "run_new456")

        # Fresh run should still be running
        fresh_data = _read_run(fresh_file)
        assert fresh_data["status"] == "running"
        assert abandoned == 0

    def test_different_project_not_touched(self, workspace):
        """AC4: Runs in other projects are never touched."""
        from scripts.artifact_cli import _auto_abandon_stale_runs

        # Create stale run in a DIFFERENT project
        _create_run(workspace, "OtherProject", "run_other", "running", hours_ago=5)

        with patch("scripts.artifact_cli._get_workspace", return_value=workspace):
            abandoned = _auto_abandon_stale_runs("TestProject", "run_new456")

        assert abandoned == 0

    def test_completed_run_not_touched(self, workspace):
        """Only 'running' status gets abandoned — completed/failed stay."""
        from scripts.artifact_cli import _auto_abandon_stale_runs

        completed_file = _create_run(workspace, "TestProject", "run_done", "completed", hours_ago=10)

        with patch("scripts.artifact_cli._get_workspace", return_value=workspace):
            abandoned = _auto_abandon_stale_runs("TestProject", "run_new456")

        completed_data = _read_run(completed_file)
        assert completed_data["status"] == "completed"
        assert abandoned == 0


class TestCleanupOrphans:
    """AC2: cleanup-orphans subcommand marks all >2h stale running runs."""

    def test_cleanup_marks_all_stale_across_projects(self, workspace):
        """Batch mode cleans up all projects."""
        from scripts.artifact_cli import cleanup_orphans

        # Stale runs across 2 projects
        stale1 = _create_run(workspace, "ProjectA", "run_a1", "running", hours_ago=5)
        stale2 = _create_run(workspace, "ProjectB", "run_b1", "running", hours_ago=3)
        # Fresh run should survive
        fresh = _create_run(workspace, "ProjectA", "run_a2", "running", hours_ago=1)

        with patch("scripts.artifact_cli._get_workspace", return_value=workspace):
            result = cleanup_orphans(threshold_hours=2.0)

        assert result["abandoned_count"] == 2
        assert _read_run(stale1)["status"] == "abandoned"
        assert _read_run(stale2)["status"] == "abandoned"
        assert _read_run(fresh)["status"] == "running"

    def test_cleanup_adds_reason(self, workspace):
        """Abandoned runs get a reason field."""
        from scripts.artifact_cli import cleanup_orphans

        stale = _create_run(workspace, "ProjectA", "run_stale", "running", hours_ago=4)

        with patch("scripts.artifact_cli._get_workspace", return_value=workspace):
            cleanup_orphans(threshold_hours=2.0)

        data = _read_run(stale)
        assert "stale_orphan" in data.get("abandon_reason", "")
        assert "abandoned_at" in data


class TestOneTimeCleanup:
    """AC3: Reclassify zero-stage failed + orphan running."""

    def test_zero_stage_failed_becomes_abandoned(self, workspace):
        """Failed runs with no stages = never executed = abandoned."""
        from scripts.pipeline_cleanup import reclassify_stale_runs

        # Create a "failed" run with zero stages (session crash)
        failed_file = _create_run(workspace, "TestProject", "run_crash", "failed", hours_ago=24, stages=[])

        # Pass workspace directly — no mock needed
        result = reclassify_stale_runs(workspace)

        data = _read_run(failed_file)
        assert data["status"] == "abandoned"
        assert "zero_stages_reclassified" in data.get("abandon_reason", "")
        assert result["reclassified_failed"] >= 1

    def test_failed_with_stages_not_touched(self, workspace):
        """Failed runs that actually executed stages stay 'failed'."""
        from scripts.pipeline_cleanup import reclassify_stale_runs

        # Create a "failed" run with actual stages
        stages = [{"stage": "evaluate", "status": "done"}]
        failed_file = _create_run(workspace, "TestProject", "run_real_fail", "failed", hours_ago=24, stages=stages)

        # Pass workspace directly — no mock needed
        result = reclassify_stale_runs(workspace)

        data = _read_run(failed_file)
        assert data["status"] == "failed"  # NOT changed


class TestAutoRecordStage:
    """Regression: publish --stage must auto-record into run.json.

    Root cause: _append_stage_to_run referenced reg._workspace (does not
    exist — ArtifactRegistry has .workspace_root), raising AttributeError
    that was swallowed by a bare `except: pass` in cmd_publish. Result:
    publish returned a valid artifact_id but run.json stages stayed empty,
    silently breaking the completion gate.
    """

    def test_append_stage_to_run_uses_workspace_root(self, workspace):
        """_append_stage_to_run writes a stage record without AttributeError."""
        from core.artifact_registry import ArtifactRegistry
        from scripts.artifact_cli import _append_stage_to_run

        _create_run(workspace, "TestProject", "run_ar1", "running", stages=[])
        reg = ArtifactRegistry(workspace)

        _append_stage_to_run(
            "TestProject", "run_ar1",
            {"stage": "build", "status": "completed", "artifact_id": "art_x"},
            reg,
        )

        run_file = workspace / "Projects" / "TestProject" / ".artifacts" / "runs" / "run_ar1" / "run.json"
        data = _read_run(run_file)
        assert [s["stage"] for s in data["stages"]] == ["build"]

    def test_append_stage_no_duplicate(self, workspace):
        """Re-appending the same stage is a no-op (idempotent)."""
        from core.artifact_registry import ArtifactRegistry
        from scripts.artifact_cli import _append_stage_to_run

        _create_run(workspace, "TestProject", "run_ar2", "running",
                    stages=[{"stage": "build", "status": "completed"}])
        reg = ArtifactRegistry(workspace)

        _append_stage_to_run(
            "TestProject", "run_ar2",
            {"stage": "build", "status": "completed", "artifact_id": "art_y"},
            reg,
        )
        run_file = workspace / "Projects" / "TestProject" / ".artifacts" / "runs" / "run_ar2" / "run.json"
        data = _read_run(run_file)
        assert len([s for s in data["stages"] if s["stage"] == "build"]) == 1

    def test_auto_record_failure_is_not_silent(self, workspace, capsys, monkeypatch):
        """When auto-record raises, cmd_publish emits a stderr warning (not silent pass)."""
        import sys
        from pathlib import Path as _P
        # cmd_publish does `from pipeline_validator import ...` assuming its own
        # dir (backend/scripts) is on sys.path — true when run as a script,
        # not when imported as scripts.artifact_cli in pytest. Mirror the CLI.
        _scripts_dir = str(_P(__file__).resolve().parent.parent / "scripts")
        if _scripts_dir not in sys.path:
            sys.path.insert(0, _scripts_dir)
        import scripts.artifact_cli as cli
        from core.artifact_registry import ArtifactRegistry

        _create_run(workspace, "TestProject", "run_ar3", "running", stages=[])
        reg = ArtifactRegistry(workspace)

        # Force the auto-record helper to raise.
        def _boom(*a, **k):
            raise RuntimeError("simulated append failure")
        monkeypatch.setattr(cli, "_append_stage_to_run", _boom)

        # This test isolates the auto-record failure path. Schema validation is
        # a separate concern (tested elsewhere), so stub it to pass — we need
        # execution to reach the auto-record block, not to re-test the schema.
        import pipeline_validator
        monkeypatch.setattr(pipeline_validator, "validate_artifact_data",
                            lambda *a, **k: [])

        class _Args:
            project = "TestProject"
            type = "changeset"
            data = '{"branch":"x","commits":["abc1234"],"files_changed":["f.py"]}'
            producer = "s_autonomous-pipeline"
            summary = "test"
            topic = ""
            stage = "build"
            run_id = "run_ar3"

        try:
            cli.cmd_publish(_Args(), reg)
        except SystemExit:
            pass
        captured = capsys.readouterr()
        # The failure must surface SOMEWHERE visible — not be swallowed silently.
        assert "auto-record" in captured.err.lower() or "simulated append failure" in captured.err.lower()

    def test_auto_record_stub_does_not_bypass_completion_gate(self, workspace, capsys, monkeypatch):
        """The auto-recorded stub must mark status='recorded', NOT 'completed',
        so it cannot silently satisfy the completion gate (stage_doc_consumed
        bypass). The agent must still run-update to properly complete the stage."""
        import sys
        from pathlib import Path as _P
        _scripts_dir = str(_P(__file__).resolve().parent.parent / "scripts")
        if _scripts_dir not in sys.path:
            sys.path.insert(0, _scripts_dir)
        import scripts.artifact_cli as cli
        from core.artifact_registry import ArtifactRegistry
        import pipeline_validator

        _create_run(workspace, "TestProject", "run_stub", "running", stages=[])
        reg = ArtifactRegistry(workspace)
        monkeypatch.setattr(pipeline_validator, "validate_artifact_data", lambda *a, **k: [])

        class _Args:
            project = "TestProject"
            type = "changeset"
            data = '{"branch":"x","commits":["abc1234"],"files_changed":["f.py"]}'
            producer = "s_autonomous-pipeline"
            summary = "test"
            topic = ""
            stage = "build"
            run_id = "run_stub"

        cli.cmd_publish(_Args(), reg)
        run_file = workspace / "Projects" / "TestProject" / ".artifacts" / "runs" / "run_stub" / "run.json"
        data = _read_run(run_file)
        build = next(s for s in data["stages"] if s["stage"] == "build")
        # Stub captures the artifact link but is NOT 'completed' and has no
        # stage_doc_consumed — so the completion gate still requires run-update.
        assert build["status"] == "recorded"
        assert build.get("artifact_id")  # link preserved (the safety-net value)
        assert "stage_doc_consumed" not in build


class TestPublishQuietMode:
    """--quiet: parse-proof output for orchestrators (run_688b6487 DoD1).

    Success → ONLY {"artifact_id": ...} single line. Schema failure → a SHORT
    single-line {"validation_failed":true,"errors":[...]} instead of the verbose
    multi-KB indented schema dump that choked the orchestrator's JSON parse.
    """

    def _import_cli(self):
        import sys
        from pathlib import Path as _P
        _scripts_dir = str(_P(__file__).resolve().parent.parent / "scripts")
        if _scripts_dir not in sys.path:
            sys.path.insert(0, _scripts_dir)
        import scripts.artifact_cli as cli
        return cli

    def test_quiet_success_emits_only_artifact_id(self, workspace, capsys, monkeypatch):
        cli = self._import_cli()
        from core.artifact_registry import ArtifactRegistry
        import pipeline_validator
        _create_run(workspace, "TestProject", "run_q1", "running", stages=[])
        reg = ArtifactRegistry(workspace)
        monkeypatch.setattr(pipeline_validator, "validate_artifact_data", lambda *a, **k: [])

        class _Args:
            project = "TestProject"; type = "research"; producer = "test"
            summary = "t"; topic = ""; stage = "think"; run_id = "run_q1"
            data = '{"key_findings":["x"]}'; quiet = True

        cli.cmd_publish(_Args(), reg)
        out = capsys.readouterr().out.strip()
        parsed = json.loads(out)  # single line, parseable
        assert list(parsed.keys()) == ["artifact_id"], parsed
        assert out.count("\n") == 0

    def test_quiet_schema_failure_is_short_single_line(self, workspace, capsys, monkeypatch):
        cli = self._import_cli()
        from core.artifact_registry import ArtifactRegistry
        import pipeline_validator
        _create_run(workspace, "TestProject", "run_q2", "running", stages=[])
        reg = ArtifactRegistry(workspace)
        # Force a schema failure.
        monkeypatch.setattr(pipeline_validator, "validate_artifact_data",
                            lambda *a, **k: ["missing required field 'recommendation'"])

        class _Args:
            project = "TestProject"; type = "evaluation"; producer = "test"
            summary = "t"; topic = ""; stage = "evaluate"; run_id = "run_q2"
            data = '{"missing":"fields"}'; quiet = True

        with pytest.raises(SystemExit):
            cli.cmd_publish(_Args(), reg)
        err = capsys.readouterr().err.strip()
        parsed = json.loads(err)  # short + parseable
        assert parsed["validation_failed"] is True
        assert "expected_schema" not in parsed  # the verbose dump is suppressed
        assert err.count("\n") == 0
        assert len(err) < 300

    def test_non_quiet_schema_failure_keeps_verbose_schema(self, workspace, capsys, monkeypatch):
        """Regression guard: WITHOUT --quiet the verbose schema dump is preserved
        (humans still get the template). Proves --quiet is the discriminator, not
        a blanket truncation."""
        cli = self._import_cli()
        from core.artifact_registry import ArtifactRegistry
        import pipeline_validator
        _create_run(workspace, "TestProject", "run_q3", "running", stages=[])
        reg = ArtifactRegistry(workspace)
        monkeypatch.setattr(pipeline_validator, "validate_artifact_data",
                            lambda *a, **k: ["missing required field"])

        class _Args:
            project = "TestProject"; type = "evaluation"; producer = "test"
            summary = "t"; topic = ""; stage = "evaluate"; run_id = "run_q3"
            data = '{"missing":"fields"}'; quiet = False

        with pytest.raises(SystemExit):
            cli.cmd_publish(_Args(), reg)
        err = capsys.readouterr().err
        parsed = json.loads(err)
        assert "expected_schema" in parsed  # verbose template retained for humans


class TestAutoAggregateDelivery:
    """Regression: the completion-time auto-aggregate of the delivery artifact
    must call reg.publish() with VALID kwargs. A prior bug passed stage="deliver"
    (not a publish() param) → TypeError → every bugfix/full run's auto-aggregate
    silently failed → completion gate blocked on missing deliver artifact_id."""

    def test_publish_accepts_auto_aggregate_kwargs(self, workspace):
        """reg.publish must accept exactly the kwargs the auto-aggregate passes
        (project, artifact_type, producer, summary, data, run_id) — and must NOT
        require a 'stage' kwarg (the regressed argument)."""
        from core.artifact_registry import ArtifactRegistry
        import inspect

        sig = inspect.signature(ArtifactRegistry.publish)
        params = set(sig.parameters)
        # The auto-aggregate call site (artifact_cli.py) uses these:
        assert {"project", "artifact_type", "producer", "summary", "data", "run_id"} <= params
        # The regressed kwarg must NOT be a parameter (it never was):
        assert "stage" not in params

        # And an actual publish with those kwargs must succeed + land in the run dir.
        _create_run(workspace, "TestProject", "run_agg", "running", stages=[])
        reg = ArtifactRegistry(workspace)
        art_id = reg.publish(
            project="TestProject",
            artifact_type="delivery",
            producer="s_autonomous-pipeline",
            summary="[Auto-aggregated] test",
            data={"adversarial_review": {"profile_tier": "bugfix", "findings": []},
                  "completion_audit": {"all_green": True}, "push_ready": True},
            run_id="run_agg",
        )
        assert art_id
        run_dir = workspace / "Projects" / "TestProject" / ".artifacts" / "runs" / "run_agg"
        # delivery artifact stored under the run dir (run_id routing)
        assert any(p.name.startswith("delivery") for p in run_dir.iterdir())


class TestAdvanceDriftGuard:
    """AC4: advancing past a completed stage with no artifact_id warns."""

    def test_advance_warns_on_missing_artifact(self, workspace, capsys, monkeypatch):
        """A completed stage lacking artifact_id (likely silent publish failure)
        triggers a stderr warning when advancing."""
        import scripts.artifact_cli as cli

        # build stage marked completed but NO artifact_id = silent publish failure
        _create_run(workspace, "TestProject", "run_drift", "running",
                    stages=[{"stage": "build", "status": "completed"}])
        monkeypatch.setattr(cli, "_get_workspace", lambda: workspace)
        # Stub the validator subprocess so the test isolates the drift-guard.
        import subprocess as _sp
        monkeypatch.setattr(_sp, "run",
                            lambda *a, **k: type("R", (), {"stdout": '{"valid": true, "warnings": []}', "returncode": 0})())

        cli._auto_validate_before_advance("TestProject", "test")
        captured = capsys.readouterr()
        assert "no artifact_id" in captured.err or "failed silently" in captured.err

    def test_advance_no_warn_for_reflect(self, workspace, capsys, monkeypatch):
        """reflect legitimately has no artifact — no drift warning."""
        import scripts.artifact_cli as cli

        _create_run(workspace, "TestProject", "run_reflect", "running",
                    stages=[{"stage": "reflect", "status": "completed"}])
        monkeypatch.setattr(cli, "_get_workspace", lambda: workspace)
        import subprocess as _sp
        monkeypatch.setattr(_sp, "run",
                            lambda *a, **k: type("R", (), {"stdout": '{"valid": true, "warnings": []}', "returncode": 0})())

        cli._auto_validate_before_advance("TestProject", "complete")
        captured = capsys.readouterr()
        assert "no artifact_id" not in captured.err

    def test_run_report_survives_qualitative_dimension_score(self, workspace, capsys, monkeypatch):
        """run-report must not crash when an eval dimension score is a string.

        Regression: cmd_run_report formatted dimension scores with f"{score:.2f}".
        roi (line 1910) already guarded isinstance, but dimension scores (line 1906)
        read from the SAME untyped eval_scores dict were not — a string score like
        "high" raised `ValueError: Unknown format code 'f' for object of type 'str'`,
        crashing report generation. Same bug class as the roi crash (b5730fd9).
        """
        import scripts.artifact_cli as cli
        from datetime import datetime, timezone

        # Build a run created "today" so the date-scoped artifact loader matches.
        today = datetime.now(timezone.utc)
        run_id = "run_qualscore"
        run_dir = workspace / "Projects" / "TestProject" / ".artifacts" / "runs" / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "run.json").write_text(json.dumps({
            "id": run_id, "project": "TestProject",
            "requirement": "x", "profile": "trivial", "status": "running",
            "stages": [{"stage": "evaluate", "status": "completed"}],
            "created_at": today.isoformat(), "updated_at": today.isoformat(),
        }))

        # Date-scoped evaluation artifact with a QUALITATIVE (string) dimension score.
        date_str = today.isoformat()[:10].replace("-", "")
        artifacts_dir = workspace / "Projects" / "TestProject" / ".artifacts"
        (artifacts_dir / f"evaluation-{date_str}.json").write_text(json.dumps({
            "scores": {"strategic": "high", "priority": 3.0, "roi": "high — strong fit"},
            "recommendation": "GO",
        }))

        monkeypatch.setattr(cli, "_get_workspace", lambda: workspace)
        args = type("A", (), {"project": "TestProject", "run_id": run_id, "force": True})()

        # On buggy code this raises ValueError before REPORT.md is written.
        cli.cmd_run_report(args, None)

        report_path = run_dir / "REPORT.md"
        assert report_path.exists(), "REPORT.md should be written"
        body = report_path.read_text()
        # String score rendered as-is; numeric score still formatted with .2f.
        assert "high" in body
        assert "3.00" in body

    def test_as_list_coerces_nonlist_values(self):
        """_as_list: list passes through; non-empty str → [str]; everything else → [].

        The pure coercion that makes cmd_run_report's slices crash-proof against
        agent-freedom stage-json fields recorded with the wrong type.
        """
        import scripts.artifact_cli as cli
        assert cli._as_list([1, 2]) == [1, 2]
        assert cli._as_list("hello") == ["hello"]
        assert cli._as_list(3) == []          # the run_932c0991 crash input
        assert cli._as_list(0) == []
        assert cli._as_list("") == []         # empty str is not a useful 1-item list
        assert cli._as_list(None) == []
        assert cli._as_list({"a": 1}) == []   # dict is not a list of render items
        assert cli._as_list(()) == []         # non-list iterable → [] (only list passes through)

    def test_run_report_survives_nonlist_alternatives(self, workspace, capsys, monkeypatch):
        """run-report must not crash when a sliced think field is a truthy scalar.

        Regression (run_932c0991): cmd_run_report sliced `alternatives[:4]` (and
        key_findings[:8], eval_criteria[:12]) with NO container-level isinstance
        guard. A think stage-json recording `alternatives: 3` (an int) raised
        `TypeError: 'int' object is not subscriptable`, aborting REPORT.md
        generation. Same bug class as test_run_report_survives_qualitative_dimension_score.
        """
        import scripts.artifact_cli as cli
        from datetime import datetime, timezone

        today = datetime.now(timezone.utc)
        run_id = "run_nonlist"
        run_dir = workspace / "Projects" / "TestProject" / ".artifacts" / "runs" / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "run.json").write_text(json.dumps({
            "id": run_id, "project": "TestProject",
            "requirement": "x", "profile": "bugfix", "status": "running",
            "stages": [
                {"stage": "evaluate", "status": "completed",
                 "acceptance_criteria": 2},          # int, not list — was sliced [:12]
                {"stage": "think", "status": "completed",
                 "alternatives": 3,                  # the original crash input
                 "key_findings": "one big finding"},  # str, not list — was sliced [:8]
                # adversarial completeness: sibling agent-freedom fields of the
                # SAME provenance that the first fix missed (Gate-2 finding).
                {"stage": "plan", "status": "completed",
                 "boundaries": {"always": 1, "never": "no big-bang"}},  # scalar always/never
                {"stage": "deliver", "status": "completed",
                 "adversarial_review": {"findings": 4},                 # int findings
                 "completion_audit": {"criteria_met": 5, "criteria_unmet": "none"}},
            ],
            "created_at": today.isoformat(), "updated_at": today.isoformat(),
        }))
        monkeypatch.setattr(cli, "_get_workspace", lambda: workspace)
        args = type("A", (), {"project": "TestProject", "run_id": run_id, "force": True})()

        # On buggy code this raises TypeError before REPORT.md is written.
        cli.cmd_run_report(args, None)

        report_path = run_dir / "REPORT.md"
        assert report_path.exists(), "REPORT.md should be written despite non-list fields"
        body = report_path.read_text()
        # str key_findings coerced to a single rendered item; int fields skipped cleanly.
        assert "one big finding" in body

    def _c2_run(self, workspace, reg, run_id, profile, evaluate_data, monkeypatch):
        """Build a run with ALL profile stages completed (so the stage-completeness
        gate passes) and a given evaluate artifact, for C2 backstop testing."""
        import json as _json
        from datetime import datetime, timezone
        from core.pipeline_profiles import get_profile_stages
        # The completion gate loads pipeline_validator as a FRESH module whose
        # _load_artifact_data/_get_workspace resolve via SWARM_WORKSPACE env (not the
        # monkeypatched cli._get_workspace). Point it at the test workspace so the C2
        # backstop can load stage artifacts (in prod both resolve to the same dir).
        monkeypatch.setenv("SWARM_WORKSPACE", str(workspace))
        run_dir = workspace / "Projects" / "TestProject" / ".artifacts" / "runs" / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        ev = reg.publish(project="TestProject", artifact_type="evaluation",
                         producer="test", summary="c2", data=evaluate_data)
        ev_id = ev["id"] if isinstance(ev, dict) else ev
        dv = reg.publish(project="TestProject", artifact_type="delivery",
                         producer="test", summary="ok",
                         data={"title": "x",
                               "quality": {"tests_pass": True, "regressions": 0, "smoke_pass": True},
                               "adversarial_review": {"spawned": True, "profile_tier": "full",
                                                      "evidence": "Agent tool", "findings": []},
                               "completion_audit": {"all_green": True}, "meta_review": "CLEAR",
                               "convergence": {"iterations": 1, "all_pass": True, "final_status": "push-ready"}})
        dv_id = dv["id"] if isinstance(dv, dict) else dv
        stages = []
        for stg in get_profile_stages(profile):
            rec = {"stage": stg, "status": "completed"}
            if stg == "evaluate":
                rec["artifact_id"] = ev_id
            elif stg == "deliver":
                rec["artifact_id"] = dv_id
            elif stg == "reflect":
                rec["lessons"] = ["C2 backstop integration fixture — a substantive "
                                  "lesson over twenty chars so the reflect gate passes."]
            stages.append(rec)
        today = datetime.now(timezone.utc)
        (run_dir / "run.json").write_text(_json.dumps({
            "id": run_id, "project": "TestProject", "requirement": "x",
            "profile": profile, "status": "running", "stages": stages,
            "created_at": today.isoformat(), "updated_at": today.isoformat(),
        }))
        # Satisfy the REPORT.md completion gate (not what C2 tests).
        (run_dir / "REPORT.md").write_text("# Pipeline Report\n\n" + ("detail. " * 100))
        import argparse
        attrs = ("active_only actual_effort adversarial_count alternatives backend "
                 "categories command context data ddd_checksums dismissed escalated "
                 "evaluation_id event files_estimated fixed frontend full indicators "
                 "lessons limit modules outcome overlap partial probes producer profile "
                 "project reason requirement resolved retries review_count rp_violations "
                 "run_id scope stage stage_json state status summary taste_decision "
                 "timestamp tokens_consumed topic type types user_override").split()
        ns = argparse.Namespace(**{a: None for a in attrs})
        ns.project = "TestProject"
        ns.run_id = run_id
        ns.status = "completed"
        return ns

    def test_completion_blocks_gateless_strict_evaluate(self, workspace, capsys, monkeypatch):
        """C2 backstop (run_7cf9da85): a strict-profile run whose EVALUATE artifact
        was published WITHOUT the gate fields (publish-bypass / hand-edit) must be
        BLOCKED at completion. Before C2 the completion gate hardcoded stage='deliver'
        so a gateless evaluate sailed through (skeptic-confirmed hole)."""
        import scripts.artifact_cli as cli
        from core.artifact_registry import ArtifactRegistry
        monkeypatch.setattr(cli, "_get_workspace", lambda: workspace)
        reg = ArtifactRegistry(workspace)
        # bugfix profile = strict for the Understanding gate; gateless evaluate data.
        args = self._c2_run(workspace, reg, "run_c2block", "bugfix",
                            {"recommendation": "GO", "scope": "standard"}, monkeypatch)  # no understanding block
        cli.cmd_run_update(args, reg)
        out = capsys.readouterr().out
        assert "Cannot mark completed" in out and "valuate" in out, \
            f"gateless strict evaluate must BLOCK completion: {out}"

    def test_completion_allows_relaxed_gateless_evaluate(self, workspace, capsys, monkeypatch):
        """C2 must NOT over-block: a RELAXED profile (research) legitimately exempts
        the Understanding gate, so a gateless evaluate completes fine."""
        import scripts.artifact_cli as cli
        from core.artifact_registry import ArtifactRegistry
        monkeypatch.setattr(cli, "_get_workspace", lambda: workspace)
        reg = ArtifactRegistry(workspace)
        args = self._c2_run(workspace, reg, "run_c2ok", "research",
                            {"recommendation": "GO", "scope": "research"}, monkeypatch)
        cli.cmd_run_update(args, reg)
        out = capsys.readouterr().out
        assert "Cannot mark completed" not in out, \
            f"relaxed-profile gateless evaluate must NOT be blocked by C2: {out}"

    def test_advance_no_warn_for_goal_cycle(self, workspace, capsys, monkeypatch):
        """goal_cycle commits incrementally and has no artifact — no drift warning."""
        import scripts.artifact_cli as cli

        _create_run(workspace, "TestProject", "run_goal", "running",
                    stages=[{"stage": "goal_cycle", "status": "completed"}])
        monkeypatch.setattr(cli, "_get_workspace", lambda: workspace)
        import subprocess as _sp
        monkeypatch.setattr(_sp, "run",
                            lambda *a, **k: type("R", (), {"stdout": '{"valid": true, "warnings": []}', "returncode": 0})())

        cli._auto_validate_before_advance("TestProject", "complete")
        captured = capsys.readouterr()
        assert "no artifact_id" not in captured.err


class TestCompletionFailsClosedOnValidatorCrash:
    """run_84316b42: the DELIVER completion gate must FAIL CLOSED when the
    validator crashes (cannot produce a verdict), symmetric with the ADVANCE
    path. Reverses run_55710438 MED-8 fail-open. C037/CLASS A."""

    def _setup(self, workspace):
        import sys
        from pathlib import Path as _P
        _scripts_dir = str(_P(__file__).resolve().parent.parent / "scripts")
        if _scripts_dir not in sys.path:
            sys.path.insert(0, _scripts_dir)
        import scripts.artifact_cli as cli
        from core.artifact_registry import ArtifactRegistry
        # Substantive lessons (>20 chars, actionable) so the REFLECT gate passes
        # and the run reaches the VALIDATOR gate — the path under test.
        _L = "The completion gate must fail closed on validator crash, symmetric with advance"
        _create_run(workspace, "TestProject", "run_crash", "running", stages=[
            {"stage": "evaluate", "status": "completed", "stage_doc_consumed": True, "artifact_id": "a_e", "token_cost": 5000},
            {"stage": "think", "status": "completed", "stage_doc_consumed": True, "artifact_id": "a_t", "token_cost": 5000},
            {"stage": "plan", "status": "completed", "stage_doc_consumed": True, "artifact_id": "a_p", "token_cost": 5000},
            {"stage": "build", "status": "completed", "stage_doc_consumed": True, "artifact_id": "a_b", "token_cost": 5000},
            {"stage": "review", "status": "completed", "stage_doc_consumed": True, "artifact_id": "a_r", "token_cost": 5000},
            {"stage": "test", "status": "completed", "stage_doc_consumed": True, "artifact_id": "a_te", "token_cost": 5000},
            {"stage": "deliver", "status": "completed", "stage_doc_consumed": True, "artifact_id": "a_d", "token_cost": 5000,
             "adversarial_review": {"spawned": True, "profile_tier": "bugfix",
                                    "findings_total": 0, "findings_fixed": 0, "findings_remaining": 0, "findings": []}},
            {"stage": "reflect", "status": "completed", "stage_doc_consumed": True,
             "token_cost": 5000, "lessons": [_L, _L + " (two)"]},
        ])
        rf = workspace / "Projects" / "TestProject" / ".artifacts" / "runs" / "run_crash" / "run.json"
        d = _read_run(rf); d["profile"] = "bugfix"; rf.write_text(json.dumps(d))
        # REPORT.md must exist + be >500 bytes (completion gate checks it)
        (rf.parent / "REPORT.md").write_text("# Pipeline Report\n\n" + ("detail. " * 100))
        return cli, ArtifactRegistry(workspace), rf

    def _args(self):
        import argparse
        # All attrs cmd_run_update may read, defaulted None; override what matters.
        attrs = ("active_only actual_effort adversarial_count alternatives backend "
                 "categories command context data ddd_checksums dismissed escalated "
                 "evaluation_id event files_estimated fixed frontend full indicators "
                 "lessons limit modules outcome overlap partial probes producer profile "
                 "project reason requirement resolved retries review_count rp_violations "
                 "run_id scope stage stage_json state status summary taste_decision "
                 "timestamp tokens_consumed topic type types user_override").split()
        ns = argparse.Namespace(**{a: None for a in attrs})
        ns.project = "TestProject"
        ns.run_id = "run_crash"
        ns.status = "completed"
        return ns

    def test_completion_blocked_when_validator_raises(self, workspace, capsys, monkeypatch):
        """validate() raising mid-gate → completion BLOCKED, run NOT marked completed."""
        cli, reg, rf = self._setup(workspace)
        monkeypatch.setattr(cli, "_get_workspace", lambda: workspace)
        # Force the in-process validator load to raise on validate()
        import importlib.util as _ilu
        real_from_spec = _ilu.module_from_spec

        def boom_module(spec):
            mod = real_from_spec(spec)
            if spec.name == "pipeline_validator":
                # replace validate with a crasher after the module loads
                orig_exec = spec.loader.exec_module
                def exec_and_poison(m):
                    orig_exec(m)
                    def _crash(*a, **k):
                        raise RuntimeError("injected validate crash (pre-dict)")
                    m.validate = _crash
                spec.loader.exec_module = exec_and_poison
            return mod
        monkeypatch.setattr(_ilu, "module_from_spec", boom_module)

        cli.cmd_run_update(self._args(), reg)
        out = capsys.readouterr()
        data = _read_run(rf)
        # MUST NOT be completed — the gate failed closed
        assert data["status"] != "completed", \
            f"validator crash must block completion, got status={data['status']}"
        combined = out.out + out.err
        assert "ERRORED" in combined or "could not" in combined.lower() or "crash" in combined.lower(), \
            f"crash must be surfaced: {combined[:400]}"

    def test_normal_completion_still_succeeds(self, workspace, capsys, monkeypatch):
        """AC4 no-regression: when validate() returns a clean dict (no crash),
        completion proceeds — the fail-closed change must NOT block healthy runs."""
        cli, reg, rf = self._setup(workspace)
        monkeypatch.setattr(cli, "_get_workspace", lambda: workspace)
        # Make the in-process validator return a clean valid result (no crash).
        import importlib.util as _ilu
        real_from_spec = _ilu.module_from_spec

        def clean_module(spec):
            mod = real_from_spec(spec)
            if spec.name == "pipeline_validator":
                orig_exec = spec.loader.exec_module
                def exec_and_stub(m):
                    orig_exec(m)
                    m.validate = lambda *a, **k: {
                        "valid": True, "stage": "deliver", "errors": [],
                        "warnings": [], "errored": [], "check_results": [],
                        "checks_passed": 1, "checks_total": 1,
                    }
                spec.loader.exec_module = exec_and_stub
            return mod
        monkeypatch.setattr(_ilu, "module_from_spec", clean_module)

        cli.cmd_run_update(self._args(), reg)
        data = _read_run(rf)
        assert data["status"] == "completed", \
            f"clean validator result must complete, got {data['status']}"

    def test_genuine_validator_errors_still_block(self, workspace, capsys, monkeypatch):
        """AC5 no-regression: a validator that RETURNS valid=False (content error,
        not a crash) still blocks — unchanged behavior on the result path."""
        cli, reg, rf = self._setup(workspace)
        monkeypatch.setattr(cli, "_get_workspace", lambda: workspace)
        import importlib.util as _ilu
        real_from_spec = _ilu.module_from_spec

        def err_module(spec):
            mod = real_from_spec(spec)
            if spec.name == "pipeline_validator":
                orig_exec = spec.loader.exec_module
                def exec_and_stub(m):
                    orig_exec(m)
                    m.validate = lambda *a, **k: {
                        "valid": False, "stage": "deliver",
                        "errors": ["Depth: adversarial_review has 1 unresolved HIGH finding(s)"],
                        "warnings": [], "errored": [], "check_results": [],
                        "checks_passed": 0, "checks_total": 1,
                    }
                spec.loader.exec_module = exec_and_stub
            return mod
        monkeypatch.setattr(_ilu, "module_from_spec", err_module)

        cli.cmd_run_update(self._args(), reg)
        data = _read_run(rf)
        assert data["status"] != "completed", \
            f"validator content errors must block, got {data['status']}"

    def test_completion_blocked_on_validator_import_failure(self, workspace, capsys, monkeypatch):
        """AC2: outer except — validator IMPORT/load failure → completion BLOCKED.
        Covers the outer `except exc` path (was untested; spec review gap)."""
        cli, reg, rf = self._setup(workspace)
        monkeypatch.setattr(cli, "_get_workspace", lambda: workspace)
        # Make the validator module fail to LOAD (exec_module raises) → outer except.
        import importlib.util as _ilu
        real_from_spec = _ilu.module_from_spec

        def fail_load(spec):
            mod = real_from_spec(spec)
            if spec.name == "pipeline_validator":
                def boom_exec(m):
                    raise ImportError("simulated validator import failure")
                spec.loader.exec_module = boom_exec
            return mod
        monkeypatch.setattr(_ilu, "module_from_spec", fail_load)

        cli.cmd_run_update(self._args(), reg)
        out = capsys.readouterr()
        data = _read_run(rf)
        assert data["status"] != "completed", \
            f"validator import failure must block completion, got {data['status']}"
        combined = out.out + out.err
        assert "validator gate ERRORED" in combined or "could not be verified" in combined.lower() \
            or "completion cannot be verified" in combined.lower(), \
            f"outer crash must be surfaced as blocking: {combined[:400]}"

    def test_transient_io_error_blocks_with_retryable_message(self, workspace, capsys, monkeypatch):
        """Adversarial MED: a transient OSError from validate()'s internal read
        still BLOCKS (fail-closed) but is surfaced as RETRYABLE, not 'fix the validator'."""
        cli, reg, rf = self._setup(workspace)
        monkeypatch.setattr(cli, "_get_workspace", lambda: workspace)
        import importlib.util as _ilu
        real_from_spec = _ilu.module_from_spec

        def io_module(spec):
            mod = real_from_spec(spec)
            if spec.name == "pipeline_validator":
                orig_exec = spec.loader.exec_module
                def exec_and_stub(m):
                    orig_exec(m)
                    def _io_crash(*a, **k):
                        raise OSError("simulated transient file lock")
                    m.validate = _io_crash
                spec.loader.exec_module = exec_and_stub
            return mod
        monkeypatch.setattr(_ilu, "module_from_spec", io_module)

        cli.cmd_run_update(self._args(), reg)
        out = capsys.readouterr()
        data = _read_run(rf)
        assert data["status"] != "completed", "transient I/O must still block (fail-closed)"
        combined = (out.out + out.err).lower()
        assert "retry" in combined or "transient" in combined, \
            f"transient error must be surfaced as retryable, not 'fix the validator': {combined[:400]}"

    def test_corrupt_deliver_artifact_blocks_completion(self, workspace, capsys, monkeypatch):
        """run_95fc9b6a (deferred LOW from run_84316b42): when validate() RETURNS a
        dict (no crash) whose errors[] contains 'could not be loaded' for the deliver
        artifact, that is a REAL block — a missing/corrupt deliver artifact means the
        deliver stage cannot be verified. The _INFRA_PHRASES filter must NOT suppress
        it (it previously did → fail-open at the LAST gate, C037/CLASS A). Result-dict
        path, not the crash path."""
        cli, reg, rf = self._setup(workspace)
        monkeypatch.setattr(cli, "_get_workspace", lambda: workspace)
        import importlib.util as _ilu
        real_from_spec = _ilu.module_from_spec

        def err_module(spec):
            mod = real_from_spec(spec)
            if spec.name == "pipeline_validator":
                orig_exec = spec.loader.exec_module
                def exec_and_stub(m):
                    orig_exec(m)
                    # Exactly the message pipeline_validator emits at L1511 when the
                    # deliver artifact is None (file missing or corrupt).
                    m.validate = lambda *a, **k: {
                        "valid": False, "stage": "deliver",
                        "errors": ["Artifact a_d for 'deliver' could not be loaded — file missing or corrupt"],
                        "warnings": [], "errored": [], "check_results": [],
                        "checks_passed": 0, "checks_total": 1,
                    }
                spec.loader.exec_module = exec_and_stub
            return mod
        monkeypatch.setattr(_ilu, "module_from_spec", err_module)

        cli.cmd_run_update(self._args(), reg)
        out = capsys.readouterr()
        data = _read_run(rf)
        # MUST NOT complete — a corrupt deliver artifact is a real verification failure.
        assert data["status"] != "completed", \
            f"corrupt deliver artifact must block completion, got status={data['status']}"
        # And the error must actually reach the user (not be silently filtered).
        combined = (out.out + out.err).lower()
        assert "could not be loaded" in combined, \
            f"the corrupt-artifact error must be surfaced, not filtered: {combined[:400]}"

    def test_environmental_not_found_still_filtered(self, workspace, capsys, monkeypatch):
        """AC4 regression guard: genuinely-environmental phrases ('not found') remain
        filtered. A run-not-found style error (validator couldn't locate the run/stage
        in a test env) must NOT, on its own, manufacture a block — it is the kind of
        infra noise the filter legitimately suppresses. Pairs with the test above to
        prove the filter narrowed correctly, not collapsed entirely."""
        cli, reg, rf = self._setup(workspace)
        monkeypatch.setattr(cli, "_get_workspace", lambda: workspace)
        import importlib.util as _ilu
        real_from_spec = _ilu.module_from_spec

        def env_module(spec):
            mod = real_from_spec(spec)
            if spec.name == "pipeline_validator":
                orig_exec = spec.loader.exec_module
                def exec_and_stub(m):
                    orig_exec(m)
                    # valid=False but the ONLY error is environmental noise → filtered → no block.
                    m.validate = lambda *a, **k: {
                        "valid": False, "stage": "deliver",
                        "errors": ["Pipeline run run_crash not found for project TestProject"],
                        "warnings": [], "errored": [], "check_results": [],
                        "checks_passed": 0, "checks_total": 1,
                    }
                spec.loader.exec_module = exec_and_stub
            return mod
        monkeypatch.setattr(_ilu, "module_from_spec", env_module)

        cli.cmd_run_update(self._args(), reg)
        data = _read_run(rf)
        assert data["status"] == "completed", \
            f"environmental 'not found' must stay filtered (no block), got {data['status']}"

    def test_environmental_no_stage_record_still_filtered(self, workspace, capsys, monkeypatch):
        """AC4 (second retained phrase): 'no stage record' must also remain filtered.
        Spec review flagged it as retained-but-untested. Proves the narrowed filter
        kept BOTH environmental phrases, not just 'not found'."""
        cli, reg, rf = self._setup(workspace)
        monkeypatch.setattr(cli, "_get_workspace", lambda: workspace)
        import importlib.util as _ilu
        real_from_spec = _ilu.module_from_spec

        def env_module(spec):
            mod = real_from_spec(spec)
            if spec.name == "pipeline_validator":
                orig_exec = spec.loader.exec_module
                def exec_and_stub(m):
                    orig_exec(m)
                    # Exact validator sentinel (pipeline_validator L1493).
                    m.validate = lambda *a, **k: {
                        "valid": False, "stage": "deliver",
                        "errors": ["No stage record found for 'deliver' in run run_crash"],
                        "warnings": [], "errored": [], "check_results": [],
                        "checks_passed": 0, "checks_total": 1,
                    }
                spec.loader.exec_module = exec_and_stub
            return mod
        monkeypatch.setattr(_ilu, "module_from_spec", env_module)

        cli.cmd_run_update(self._args(), reg)
        data = _read_run(rf)
        assert data["status"] == "completed", \
            f"environmental 'no stage record' must stay filtered (no block), got {data['status']}"

    def test_substring_not_found_in_real_error_still_blocks(self, workspace, capsys, monkeypatch):
        """Adversarial MED (run_95fc9b6a): the filter must ANCHOR on the full
        environmental sentinel, not loosely match 'not found'. A fail-CLOSED
        crash message like a hard-check ERRORED with 'FileNotFoundError: ...
        not found' contains the substring 'not found' but is a REAL block — it
        must NOT be suppressed. Proves the anchored match closed the substring-
        collision fail-open hole."""
        cli, reg, rf = self._setup(workspace)
        monkeypatch.setattr(cli, "_get_workspace", lambda: workspace)
        import importlib.util as _ilu
        real_from_spec = _ilu.module_from_spec

        def err_module(spec):
            mod = real_from_spec(spec)
            if spec.name == "pipeline_validator":
                orig_exec = spec.loader.exec_module
                def exec_and_stub(m):
                    orig_exec(m)
                    # A hard-check crash string that happens to contain "not found"
                    # but is NOT the environmental run/stage-missing sentinel.
                    m.validate = lambda *a, **k: {
                        "valid": False, "stage": "deliver",
                        "errors": ["Check 'artifact_exists' ERRORED (could not run): "
                                   "FileNotFoundError: [Errno 2] deliver data file not found"],
                        "warnings": [], "errored": [], "check_results": [],
                        "checks_passed": 0, "checks_total": 1,
                    }
                spec.loader.exec_module = exec_and_stub
            return mod
        monkeypatch.setattr(_ilu, "module_from_spec", err_module)

        cli.cmd_run_update(self._args(), reg)
        out = capsys.readouterr()
        data = _read_run(rf)
        assert data["status"] != "completed", \
            f"a real error merely containing 'not found' must still block, got {data['status']}"
        combined = (out.out + out.err).lower()
        assert "errored" in combined or "filenotfounderror" in combined, \
            f"the real crash error must be surfaced, not filtered: {combined[:400]}"
