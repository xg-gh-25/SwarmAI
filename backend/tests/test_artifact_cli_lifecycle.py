"""Tests for pipeline run lifecycle management: auto-abandon and cleanup.

Tests AC1-AC5 of pipeline run_fd0064e6:
- AC1: auto-abandon stale same-project running runs (>2h) on new run start
- AC2: cleanup-orphans subcommand for batch cleanup
- AC3: one-time data fix reclassifies 14 failed + 6 orphan
- AC4: legitimate concurrent runs (<2h) NOT touched
- AC5: all changes have passing tests (this file)
"""

import json
from datetime import datetime, timezone, timedelta
from unittest.mock import patch

import pytest


@pytest.fixture
def workspace(tmp_path):
    """Create a fake workspace with pipeline runs."""
    project_dir = tmp_path / "Projects" / "TestProject" / ".artifacts" / "runs"
    project_dir.mkdir(parents=True)
    return tmp_path


def _create_run(workspace, project, run_id, status="running", hours_ago=0,
                stages=None, checkpoint_reason=None):
    """Helper: create a run.json with given status and age.

    checkpoint_reason: when set, adds checkpoint={'reason': ...} — used to model
    crash-zombie paused runs (reason='session_crash_auto_detected') vs
    intentional pauses (a real decision reason).
    """
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
    if checkpoint_reason is not None:
        run_data["checkpoint"] = {"reason": checkpoint_reason}
    run_file = run_dir / "run.json"
    run_file.write_text(json.dumps(run_data, indent=2))
    return run_file


def _read_run(run_file):
    """Read and parse a run.json."""
    return json.loads(run_file.read_text())


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


class TestPublishBackfillsArtifactIdIntoExistingRecord:
    """publish is the SOLE writer of a stage's artifact_id (artifact_cli.py:213),
    so _append_stage_to_run must make the existing record reflect the LATEST
    published artifact. Two facets:

    1. BACK-FILL (run_b7620c6e): the REAL stage order is gate-1 writes a stage
       record (e.g. build + gate1_verdict) via run-update BEFORE publish runs. The
       original _append_stage_to_run skipped entirely when the stage existed → the
       artifact_id was never linked. Publish must BACK-FILL into the existing record.
    2. RE-POINT (run_59e9e36a / run_05b42b8b): re-publishing the SAME stage must
       update its artifact_id to the newest. The interim back-fill-ONLY guard
       (`not _existing.get('artifact_id')`) fixed (1) but left (2) broken — a second
       publish kept the FIRST id, so the validator read stale ACs from the old
       artifact → false 'AC not covered'. The condition is now `existing != incoming`
       (back-fill is the subset where existing is empty), covering BOTH."""

    def test_publish_backfills_artifact_id_when_record_preexists(self, workspace, monkeypatch):
        import scripts.artifact_cli as cli
        from core.artifact_registry import ArtifactRegistry
        monkeypatch.setattr(cli, "_get_workspace", lambda: workspace)
        reg = ArtifactRegistry(workspace)
        # Gate-1 already wrote a build record WITHOUT an artifact_id.
        _create_run(workspace, "TestProject", "run_bf1", "running",
                    stages=[{"stage": "build", "gate1_verdict": "WARN"}])
        cli._append_stage_to_run(
            "TestProject", "run_bf1",
            {"stage": "build", "status": "recorded", "artifact_id": "art_pub"},
            reg,
        )
        run_file = workspace / "Projects" / "TestProject" / ".artifacts" / "runs" / "run_bf1" / "run.json"
        stages = _read_run(run_file)["stages"]
        builds = [s for s in stages if s["stage"] == "build"]
        assert len(builds) == 1, "must not duplicate the stage"
        assert builds[0]["artifact_id"] == "art_pub", "artifact_id must be back-filled into the existing record"
        assert builds[0]["gate1_verdict"] == "WARN", "existing fields must be preserved"

    def test_publish_repoints_existing_artifact_id_to_newest(self, workspace, monkeypatch):
        """Re-publishing the SAME stage RE-POINTS its artifact_id to the newest.

        publish is the SOLE writer of a stage's artifact_id (artifact_cli.py:213),
        so on a second publish of the same stage the newly-published artifact IS
        the current one — the stage record must follow it. The old back-fill-ONLY
        behavior (`not _existing.get('artifact_id')`) left the stage pinned to the
        FIRST artifact_id, so the validator read stale acceptance-criteria from the
        old artifact → false 'AC not covered' errors (recurred run_59e9e36a plan+
        build, run_05b42b8b ~4 republish cycles, IMPROVEMENT.md:1685). Re-pointing
        is NOT the run_b7620c6e "don't skip an existing record" bug — that is the
        no-artifact_id back-fill case, covered by
        test_publish_backfills_artifact_id_when_record_preexists above.
        """
        import scripts.artifact_cli as cli
        from core.artifact_registry import ArtifactRegistry
        monkeypatch.setattr(cli, "_get_workspace", lambda: workspace)
        reg = ArtifactRegistry(workspace)
        _create_run(workspace, "TestProject", "run_bf2", "running",
                    stages=[{"stage": "build", "artifact_id": "art_first"}])
        cli._append_stage_to_run(
            "TestProject", "run_bf2",
            {"stage": "build", "status": "recorded", "artifact_id": "art_second"},
            reg,
        )
        run_file = workspace / "Projects" / "TestProject" / ".artifacts" / "runs" / "run_bf2" / "run.json"
        stages = _read_run(run_file)["stages"]
        builds = [s for s in stages if s["stage"] == "build"]
        assert len(builds) == 1, "re-point must not duplicate the stage (no-dup)"
        assert builds[0]["artifact_id"] == "art_second", \
            "re-publish must RE-POINT the stage to the newest artifact_id"

    def test_repoint_is_idempotent_same_id(self, workspace, monkeypatch):
        """AC5: re-recording the SAME artifact_id is a no-op (no dup, id unchanged)."""
        import scripts.artifact_cli as cli
        from core.artifact_registry import ArtifactRegistry
        monkeypatch.setattr(cli, "_get_workspace", lambda: workspace)
        reg = ArtifactRegistry(workspace)
        _create_run(workspace, "TestProject", "run_bf3", "running",
                    stages=[{"stage": "build", "artifact_id": "art_same"}])
        cli._append_stage_to_run(
            "TestProject", "run_bf3",
            {"stage": "build", "status": "recorded", "artifact_id": "art_same"},
            reg,
        )
        run_file = workspace / "Projects" / "TestProject" / ".artifacts" / "runs" / "run_bf3" / "run.json"
        builds = [s for s in _read_run(run_file)["stages"] if s["stage"] == "build"]
        assert len(builds) == 1 and builds[0]["artifact_id"] == "art_same"


class TestPublishDataFromFile:
    """run_b7620c6e #1: publish --data only accepted a raw JSON string. Add
    @FILE / @- (stdin) so large artifact payloads avoid shell-string gymnastics."""

    def _publish_args(self, data_arg):
        import argparse
        ns = argparse.Namespace(project="TestProject", type="changeset", data=data_arg,
                                producer="s_autonomous-pipeline", summary="t", topic="",
                                stage=None, run_id=None, quiet=True)
        return ns

    def test_data_at_file_is_read(self, workspace, tmp_path, monkeypatch, capsys):
        import scripts.artifact_cli as cli
        from core.artifact_registry import ArtifactRegistry
        monkeypatch.setattr(cli, "_get_workspace", lambda: workspace)
        reg = ArtifactRegistry(workspace)
        payload = tmp_path / "p.json"
        payload.write_text(json.dumps({"branch": "x", "commits": ["abc1234"], "files_changed": ["f.py"]}))
        cli.cmd_publish(self._publish_args("@" + str(payload)), reg)
        out = capsys.readouterr().out
        assert "artifact_id" in out, f"@file publish should succeed: {out}"

    def test_raw_string_still_works(self, workspace, monkeypatch, capsys):
        import scripts.artifact_cli as cli
        from core.artifact_registry import ArtifactRegistry
        monkeypatch.setattr(cli, "_get_workspace", lambda: workspace)
        reg = ArtifactRegistry(workspace)
        raw = json.dumps({"branch": "x", "commits": ["abc1234"], "files_changed": ["f.py"]})
        cli.cmd_publish(self._publish_args(raw), reg)
        out = capsys.readouterr().out
        assert "artifact_id" in out, f"raw-string publish must still work: {out}"

    def test_data_at_stdin_is_read(self, workspace, monkeypatch, capsys):
        import io
        import scripts.artifact_cli as cli
        from core.artifact_registry import ArtifactRegistry
        monkeypatch.setattr(cli, "_get_workspace", lambda: workspace)
        monkeypatch.setattr("sys.stdin",
                            io.StringIO(json.dumps({"branch": "x", "commits": ["abc1234"], "files_changed": ["f.py"]})))
        reg = ArtifactRegistry(workspace)
        cli.cmd_publish(self._publish_args("@-"), reg)
        out = capsys.readouterr().out
        assert "artifact_id" in out, f"@- stdin publish should succeed: {out}"


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

        # run_f3975b8b: the drift guard now validates ONLY the explicitly-named
        # run (no more guess-the-newest). Pass --run-id to exercise the guard.
        cli._auto_validate_before_advance("TestProject", "test", "run_drift")
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

        cli._auto_validate_before_advance("TestProject", "complete", "run_reflect")
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
        from core.artifact_registry import ArtifactRegistry

        today = datetime.now(timezone.utc)
        run_id = "run_qualscore"
        monkeypatch.setenv("SWARM_WORKSPACE", str(workspace))
        monkeypatch.setattr(cli, "_get_workspace", lambda: workspace)

        # project DDD docs (publish/report expect the project dir to exist).
        proj = workspace / "Projects" / "TestProject"
        for name in ("PRODUCT.md", "TECH.md", "IMPROVEMENT.md", "PROJECT.md"):
            (proj / name).write_text("# T\n")

        # Publish a RUN-SCOPED evaluation artifact (manifest + runs/<id>/ file) with a
        # QUALITATIVE (string) dimension score — the report loads it via the run.json
        # artifact_id (manifest path), NOT the deleted date-scoped fallback (#4).
        reg = ArtifactRegistry(workspace)
        ev = reg.publish(project="TestProject", artifact_type="evaluation",
                         producer="test", summary="qualscore", run_id=run_id,
                         data={
                             "scores": {"strategic": "high", "priority": 3.0,
                                        "roi": "high — strong fit"},
                             "recommendation": "GO",
                         })
        ev_id = ev["id"] if isinstance(ev, dict) else ev

        run_dir = workspace / "Projects" / "TestProject" / ".artifacts" / "runs" / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "run.json").write_text(json.dumps({
            "id": run_id, "project": "TestProject",
            "requirement": "x", "profile": "trivial", "status": "running",
            "stages": [{"stage": "evaluate", "status": "completed", "artifact_id": ev_id}],
            "created_at": today.isoformat(), "updated_at": today.isoformat(),
        }))

        args = type("A", (), {"project": "TestProject", "run_id": run_id, "force": True})()

        # On buggy code this raises ValueError before REPORT.md is written.
        cli.cmd_run_report(args, reg)

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
                 "evaluation_id event files_estimated files_touched fixed force_checkpoint "
                 "frontend full indicators "
                 "lessons limit modules outcome overlap partial probes producer profile "
                 "project reason requirement resolved retries review_count rp_violations "
                 "run_id scope stage stage_json state status summary taste_decision "
                 "timestamp tokens_consumed topic type types user_override").split()
        ns = argparse.Namespace(**{a: None for a in attrs})
        ns.project = "TestProject"
        ns.run_id = run_id
        ns.status = "completed"
        return ns


    def test_advance_no_warn_for_goal_cycle(self, workspace, capsys, monkeypatch):
        """goal_cycle commits incrementally and has no artifact — no drift warning."""
        import scripts.artifact_cli as cli

        _create_run(workspace, "TestProject", "run_goal", "running",
                    stages=[{"stage": "goal_cycle", "status": "completed"}])
        monkeypatch.setattr(cli, "_get_workspace", lambda: workspace)
        import subprocess as _sp
        monkeypatch.setattr(_sp, "run",
                            lambda *a, **k: type("R", (), {"stdout": '{"valid": true, "warnings": []}', "returncode": 0})())

        cli._auto_validate_before_advance("TestProject", "complete", "run_goal")
        captured = capsys.readouterr()
        assert "no artifact_id" not in captured.err


class TestAbandonReasonSurfacedInStatus:
    """AC2: run-status surfaces the abandoned count + abandon_reason, so the
    orphan-vs-superseded distinction (AC1) is actually consumable, not just
    written to a field nobody reads."""

    def test_status_entry_carries_abandon_reason(self):
        """_status_entry includes abandon_reason for an abandoned run (and the
        key is present-but-None for non-abandoned, mirroring the checkpoint key)."""
        import scripts.artifact_cli as cli

        abandoned_state = {
            "id": "run_orphan1", "requirement": "x", "status": "abandoned",
            "profile": "full", "stages": [], "abandon_reason": "orphaned_no_resume",
            "created_at": "2026-06-27T00:00:00+00:00",
            "updated_at": "2026-06-27T00:00:00+00:00",
        }
        entry = cli._status_entry(abandoned_state, "TestProject")
        assert entry["abandon_reason"] == "orphaned_no_resume"

        running_state = dict(abandoned_state, id="run_run1", status="running")
        running_state.pop("abandon_reason")
        rentry = cli._status_entry(running_state, "TestProject")
        # Key present (additive contract), value None for non-abandoned.
        assert rentry["abandon_reason"] is None

    def test_status_entry_presents_terminal_but_running_as_completed(self):
        """CLI `run-status` parity with the web dashboard (routers/pipelines.py
        _to_response): a terminal-but-crashed run (completed reflect/deliver, status
        left running/paused on disk) is presented as 'completed', NOT 'running'.
        Without this the CLI dashboard and web dashboard split-brain on the same
        run.json. Read-path only — the input dict's on-disk status is unchanged."""
        import scripts.artifact_cli as cli

        # A genuinely mid-pipeline running run is NOT coerced.
        mid = {
            "id": "run_mid", "requirement": "x", "status": "running", "profile": "bugfix",
            "stages": [{"stage": "evaluate", "status": "completed"}],
            "created_at": "2026-07-30T00:00:00+00:00", "updated_at": "2026-07-30T00:00:00+00:00",
        }
        assert cli._status_entry(mid, "P")["status"] == "running"

        # Terminal-but-running (reflect completed) → presented completed.
        term_running = dict(mid, id="run_term", stages=[
            {"stage": "evaluate", "status": "completed"},
            {"stage": "reflect", "status": "completed"},
        ])
        assert cli._status_entry(term_running, "P")["status"] == "completed"
        assert term_running["status"] == "running", "input dict must not be mutated"

        # Terminal-but-paused (deliver completed) → presented completed.
        term_paused = dict(mid, id="run_termp", status="paused", stages=[
            {"stage": "deliver", "status": "completed"},
        ])
        assert cli._status_entry(term_paused, "P")["status"] == "completed"

    def test_run_status_summary_counts_abandoned(self, tmp_path, monkeypatch, capsys):
        """run-status summary SPLITS abandoned into genuine failures vs replaced
        duplicates (Gap-2): a `superseded_by_*` run is a rerun replaced by a
        completed successor, NOT a failure — it must NOT inflate `abandoned`.
        run_a (orphan) → abandoned=1; run_b (superseded) → superseded=1."""
        import scripts.artifact_cli as cli

        runs = tmp_path / "Projects" / "P" / ".artifacts" / "runs"
        runs.mkdir(parents=True)
        for rid, status, reason in [
            ("run_a", "abandoned", "orphaned_no_resume"),
            ("run_b", "abandoned", "superseded_by_run_z"),
            ("run_c", "completed", None),
        ]:
            d = {"id": rid, "requirement": "x", "status": status, "profile": "full",
                 "stages": [], "created_at": "2026-06-27T00:00:00+00:00",
                 "updated_at": "2026-06-27T00:00:00+00:00"}
            if reason:
                d["abandon_reason"] = reason
            (runs / rid).mkdir()
            (runs / rid / "run.json").write_text(json.dumps(d))

        monkeypatch.setattr(cli, "_get_workspace", lambda: tmp_path)
        args = type("A", (), {"active_only": False})()
        cli.cmd_run_status(args, None)
        out = json.loads(capsys.readouterr().out)
        # Genuine failures only (orphan), superseded excluded.
        assert out["summary"]["abandoned"] == 1, \
            f"expected 1 genuine abandoned, got {out['summary'].get('abandoned')}"
        assert out["summary"]["superseded"] == 1, \
            f"expected 1 superseded, got {out['summary'].get('superseded')}"


class TestReportRegenerationFlag:
    """AC3: cmd_run_report must REGENERATE an auto-generated report (so reflect
    lessons added after an early deliver-time generation are inlined), while
    PRESERVING a hand-written report. The discriminator is a generator-owned
    run.json flag (report_autogenerated), NOT a fragile footer-substring match."""

    def _make_run(self, workspace, run_id, reflect_lessons):
        from datetime import datetime, timezone
        today = datetime.now(timezone.utc)
        run_dir = workspace / "Projects" / "TestProject" / ".artifacts" / "runs" / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "run.json").write_text(json.dumps({
            "id": run_id, "project": "TestProject",
            "requirement": "x", "profile": "full", "status": "running",
            "stages": [
                {"stage": "evaluate", "status": "completed"},
                {"stage": "reflect", "status": "completed", "lessons": reflect_lessons},
            ],
            "created_at": today.isoformat(), "updated_at": today.isoformat(),
        }))
        return run_dir

    def test_autogenerated_report_regenerates_without_force(self, workspace, monkeypatch):
        """An auto-generated report (flag set) is regenerated even without --force,
        so reflect lessons recorded after the first generation get inlined."""
        import scripts.artifact_cli as cli
        run_dir = self._make_run(
            workspace, "run_regen",
            ["LESSON_INLINE_MARKER: the reflect lesson that must appear"])
        # Simulate an EARLY auto-generated report missing the lessons, with the
        # generator-owned flag recorded in run.json.
        (run_dir / "REPORT.md").write_text("# Stale early report\n\n## 9. Lessons\nNone yet.\n")
        rf = run_dir / "run.json"
        data = json.loads(rf.read_text())
        data["report_autogenerated"] = True
        rf.write_text(json.dumps(data))

        monkeypatch.setattr(cli, "_get_workspace", lambda: workspace)
        args = type("A", (), {"project": "TestProject", "run_id": "run_regen", "force": False})()
        cli.cmd_run_report(args, None)

        body = (run_dir / "REPORT.md").read_text()
        assert "LESSON_INLINE_MARKER" in body, \
            "auto-report should have been regenerated to inline reflect lessons"
        # Flag persists across regeneration (still an auto-report).
        assert json.loads(rf.read_text()).get("report_autogenerated") is True

    def test_handwritten_report_preserved_without_force(self, workspace, monkeypatch, capsys):
        """A hand-written report (NO flag) is NOT overwritten without --force —
        the data-loss the skip-guard exists to prevent."""
        import scripts.artifact_cli as cli
        run_dir = self._make_run(
            workspace, "run_handwritten",
            ["a reflect lesson that must NOT clobber the human's report"])
        sentinel = "# HUMAN-AUTHORED REPORT — do not overwrite\n"
        (run_dir / "REPORT.md").write_text(sentinel)
        # No report_autogenerated flag in run.json → hand-written.

        monkeypatch.setattr(cli, "_get_workspace", lambda: workspace)
        args = type("A", (), {"project": "TestProject", "run_id": "run_handwritten", "force": False})()
        cli.cmd_run_report(args, None)

        body = (run_dir / "REPORT.md").read_text()
        assert body == sentinel, "hand-written report must be preserved without --force"
        out = json.loads(capsys.readouterr().out)
        assert out.get("skipped") is True

    def test_fresh_generation_sets_autogenerated_flag(self, workspace, monkeypatch):
        """When cmd_run_report writes a report, it stamps report_autogenerated=True
        in run.json so a later no-force call knows it owns the file."""
        import scripts.artifact_cli as cli
        run_dir = self._make_run(workspace, "run_fresh_flag", ["a lesson"])
        # No REPORT.md yet → first generation.
        monkeypatch.setattr(cli, "_get_workspace", lambda: workspace)
        args = type("A", (), {"project": "TestProject", "run_id": "run_fresh_flag", "force": False})()
        cli.cmd_run_report(args, None)

        assert (run_dir / "REPORT.md").exists()
        data = json.loads((run_dir / "run.json").read_text())
        assert data.get("report_autogenerated") is True


class TestTerminalRunNeverCrashZombie:
    """run_bf840159: a run whose STAGES are all completed (incl. reflect) but whose
    top-level status was flipped to paused+session_crash_auto_detected by the
    orphan-transition during a session refresh must NEVER be treated as a crash
    orphan. Stage-based terminal detection, because the status string is exactly
    what the false-positive corrupts.
    """

    CRASH = "session_crash_auto_detected"

    def test_predicate_true_on_all_stages_completed(self):
        from scripts.artifact_cli import is_terminal_run
        d = {"status": "paused",
             "checkpoint": {"reason": self.CRASH},
             "stages": [{"stage": "evaluate", "status": "completed"},
                        {"stage": "build", "status": "completed"},
                        {"stage": "reflect", "status": "completed"}]}
        assert is_terminal_run(d) is True

    def test_predicate_true_on_completed_reflect_marker(self):
        """A completed reflect stage alone marks terminal even if an earlier stage
        is only 'recorded' (the pipeline reached its end-of-run marker)."""
        from scripts.artifact_cli import is_terminal_run
        d = {"status": "paused",
             "stages": [{"stage": "think", "status": "recorded"},
                        {"stage": "reflect", "status": "completed"}]}
        assert is_terminal_run(d) is True

    def test_predicate_MUTATION_false_when_stages_unfinished(self):
        """RED-line: flip every stage to a non-done state → predicate MUST flip to
        False. If this stays True, the predicate is vacuous (would skip live runs)."""
        from scripts.artifact_cli import is_terminal_run
        d = {"status": "paused",
             "checkpoint": {"reason": self.CRASH},
             "stages": [{"stage": "evaluate", "status": "completed"},
                        {"stage": "build", "status": "recorded"},
                        {"stage": "reflect", "status": "recorded"}]}
        assert is_terminal_run(d) is False, \
            "an unfinished run (no completed reflect/deliver, not all-done) is NOT terminal"

    def test_predicate_false_on_empty_stages(self):
        """A fresh run with no stages yet is NOT terminal (guards the all([]) == True
        vacuous-truth trap)."""
        from scripts.artifact_cli import is_terminal_run
        assert is_terminal_run({"status": "running", "stages": []}) is False

    def test_predicate_false_on_midpipeline_pause(self):
        """CRITICAL regression guard: a paused mid-pipeline run lists only the
        stages done SO FAR (all 'completed') but has next_stage remaining. It is
        RESUMABLE, NOT terminal. If the predicate returned True here it would
        silently skip genuinely-resumable crash orphans (broke 3 auto-resume tests
        during development — this pins the fix)."""
        from scripts.artifact_cli import is_terminal_run
        d = {"status": "paused",
             "checkpoint": {"next_stage": "build", "reason": self.CRASH},
             "stages": [{"stage": "evaluate", "status": "completed"},
                        {"stage": "think", "status": "completed"}]}
        assert is_terminal_run(d) is False, \
            "evaluate+think done + next_stage=build is a RESUMABLE pause, not terminal"


class TestGarbageRunPredicate:
    """run_0e68e235: garbage = (abandoned OR crash-residue-paused) AND NOT delivered.
    A delivered run (completed reflect/deliver stage) is NEVER garbage even if its
    status string is 'abandoned'/'paused' (the mislabeled-done class, 22/182 measured).
    The predicate keys on the STAGE ARRAY, never on the circular status field."""

    CRASH = "session_crash_auto_detected"

    def test_delivered_by_stages_true_on_reflect(self):
        from scripts.artifact_cli import _delivered_by_stages
        assert _delivered_by_stages({"stages": [{"stage": "reflect", "status": "completed"}]}) is True

    def test_delivered_by_stages_true_on_deliver(self):
        from scripts.artifact_cli import _delivered_by_stages
        assert _delivered_by_stages({"stages": [{"stage": "deliver", "status": "completed"}]}) is True

    def test_delivered_by_stages_false_midpipeline(self):
        from scripts.artifact_cli import _delivered_by_stages
        # evaluate..test done but NO reflect/deliver = did not deliver
        assert _delivered_by_stages({"stages": [
            {"stage": "evaluate", "status": "completed"},
            {"stage": "build", "status": "completed"},
            {"stage": "test", "status": "completed"}]}) is False

    def test_delivered_by_stages_false_empty(self):
        from scripts.artifact_cli import _delivered_by_stages
        assert _delivered_by_stages({"stages": []}) is False
        assert _delivered_by_stages({}) is False
        assert _delivered_by_stages(None) is False

    def test_is_terminal_run_unchanged_after_extract(self):
        """REGRESSION: extracting _delivered_by_stages must NOT change is_terminal_run
        for any of its ~10 consumers. Assert identical behavior across all statuses."""
        from scripts.artifact_cli import is_terminal_run
        # status short-circuit cases (all True)
        for st in ("completed", "complete", "abandoned", "failed", "cancelled"):
            assert is_terminal_run({"status": st}) is True, f"{st} must be terminal"
        # running/paused with no deliver/reflect marker → NOT terminal
        assert is_terminal_run({"status": "running", "stages": [{"stage": "build", "status": "completed"}]}) is False
        assert is_terminal_run({"status": "paused", "stages": [{"stage": "evaluate", "status": "completed"}]}) is False
        # running/paused WITH deliver/reflect marker → terminal (finished-but-mislabeled)
        assert is_terminal_run({"status": "paused", "stages": [{"stage": "reflect", "status": "completed"}]}) is True
        # non-dict → False
        assert is_terminal_run(None) is False

    def test_garbage_abandoned_midpipeline(self):
        """160-class: abandoned run that died mid-pipeline = garbage."""
        from scripts.artifact_cli import _is_garbage_run
        assert _is_garbage_run({"status": "abandoned", "abandon_reason": "crash_zombie",
                                "stages": []}) is True
        assert _is_garbage_run({"status": "abandoned", "abandon_reason": "superseded_by_run_x",
                                "stages": [{"stage": "build", "status": "completed"}]}) is True

    def test_garbage_crash_residue_paused(self):
        """15-class: paused with the crash auto-checkpoint reason, not delivered = garbage."""
        from scripts.artifact_cli import _is_garbage_run
        assert _is_garbage_run({"status": "paused", "checkpoint": {"reason": self.CRASH},
                                "stages": []}) is True

    def test_delivered_abandoned_is_NOT_garbage(self):
        """22-class: abandoned/paused BUT has a completed reflect/deliver = delivered,
        NOT garbage (excluding it would undercount real completions)."""
        from scripts.artifact_cli import _is_garbage_run
        assert _is_garbage_run({"status": "abandoned", "abandon_reason": "superseded_by_run_x",
                                "stages": [{"stage": "reflect", "status": "completed"}]}) is False
        assert _is_garbage_run({"status": "paused", "checkpoint": {"reason": self.CRASH},
                                "stages": [{"stage": "deliver", "status": "completed"}]}) is False

    def test_real_statuses_never_garbage(self):
        """completed/cancelled/failed/running are NEVER garbage (cancelled=real user
        decision, failed=real signal — XG: only abandoned+crash-paused are garbage)."""
        from scripts.artifact_cli import _is_garbage_run
        for st in ("completed", "complete", "cancelled", "failed", "running"):
            assert _is_garbage_run({"status": st, "stages": []}) is False, f"{st} must not be garbage"
        # an intentional (non-crash) pause is NOT garbage
        assert _is_garbage_run({"status": "paused", "checkpoint": {"reason": "Gate 1 BLOCK"},
                                "stages": []}) is False

    def test_effective_analytics_status_recategorizes_delivered(self):
        """The 22 delivered-but-mislabeled (abandoned OR paused) count as completed."""
        from scripts.artifact_cli import _effective_analytics_status
        assert _effective_analytics_status({"status": "completed", "stages": []}) == "completed"
        assert _effective_analytics_status({"status": "complete", "stages": []}) == "completed"
        # abandoned + delivered → completed
        assert _effective_analytics_status({"status": "abandoned",
            "stages": [{"stage": "deliver", "status": "completed"}]}) == "completed"
        # paused + delivered → completed (Gate-1 #4)
        assert _effective_analytics_status({"status": "paused",
            "stages": [{"stage": "reflect", "status": "completed"}]}) == "completed"
        # abandoned mid-pipeline stays abandoned (it IS garbage, filtered elsewhere)
        assert _effective_analytics_status({"status": "abandoned", "stages": []}) == "abandoned"
        # cancelled/failed pass through unchanged
        assert _effective_analytics_status({"status": "cancelled", "stages": []}) == "cancelled"
        assert _effective_analytics_status({"status": "failed", "stages": []}) == "failed"


class TestPurgeGarbageRuns:
    """run_0e68e235: retention purge of garbage run DIRECTORIES (recoverable trash).
    Purges only garbage (abandoned/crash-paused, never delivered) older than N days;
    NEVER touches completed/cancelled/failed/delivered-abandoned/fresh-garbage."""

    CRASH = "session_crash_auto_detected"

    def _run_with_stages(self, workspace, project, run_id, status, hours_ago,
                         stages, abandon_reason=None, checkpoint_reason=None):
        f = _create_run(workspace, project, run_id, status=status, hours_ago=hours_ago,
                        stages=stages, checkpoint_reason=checkpoint_reason)
        if abandon_reason:
            d = _read_run(f); d["abandon_reason"] = abandon_reason
            f.write_text(json.dumps(d, indent=2))
        return f

    def test_dry_run_default_deletes_nothing(self, workspace):
        from scripts.artifact_cli import purge_garbage_runs
        # 240h ago = 10 days, older than the 7-day retention → eligible
        self._run_with_stages(workspace, "P", "run_g", "abandoned", 240, [], abandon_reason="crash_zombie")
        with patch("scripts.artifact_cli._get_workspace", return_value=workspace):
            result = purge_garbage_runs(retention_days=7, apply=False)
        # dir still exists (dry-run)
        assert (workspace / "Projects" / "P" / ".artifacts" / "runs" / "run_g").exists()
        assert result["would_purge"] == 1
        assert result["purged"] == 0

    def test_apply_trashes_only_old_garbage(self, workspace):
        from scripts.artifact_cli import purge_garbage_runs
        # old garbage → purged
        self._run_with_stages(workspace, "P", "run_oldgarbage", "abandoned", 240, [], abandon_reason="crash_zombie")
        # old crash-paused garbage → purged
        self._run_with_stages(workspace, "P", "run_crashp", "paused", 240, [], checkpoint_reason=self.CRASH)
        # FRESH garbage (< retention) → kept
        self._run_with_stages(workspace, "P", "run_freshgarbage", "abandoned", 1, [], abandon_reason="crash_zombie")
        # old COMPLETED → kept (not garbage)
        self._run_with_stages(workspace, "P", "run_done", "completed", 240,
                              [{"stage": "deliver", "status": "completed"}])
        # old CANCELLED → kept (real decision, not garbage)
        self._run_with_stages(workspace, "P", "run_cancel", "cancelled", 240, [])
        # old delivered-abandoned (mislabeled-done) → kept (delivered)
        self._run_with_stages(workspace, "P", "run_misdone", "abandoned", 240,
                              [{"stage": "reflect", "status": "completed"}], abandon_reason="superseded_by_x")
        runs_dir = workspace / "Projects" / "P" / ".artifacts" / "runs"
        with patch("scripts.artifact_cli._get_workspace", return_value=workspace):
            result = purge_garbage_runs(retention_days=7, apply=True)
        assert result["purged"] == 2, result
        assert not (runs_dir / "run_oldgarbage").exists()
        assert not (runs_dir / "run_crashp").exists()
        # everything else survives
        for keep in ("run_freshgarbage", "run_done", "run_cancel", "run_misdone"):
            assert (runs_dir / keep).exists(), f"{keep} must be kept"


class TestPurgeSkipsGitTracked:
    """Gate-2 CRITICAL (run_0e68e235): the retention purge must NEVER silently delete
    a git-TRACKED run dir — in the real workspace SwarmAI's .artifacts/runs/* ARE
    tracked (origin = the PUBLIC repo), so a raw trash would surface as an unattended
    mass `git rm` on the public working tree. Default: skip tracked dirs entirely.
    Only an explicit include_tracked=True opts into touching them."""

    def _git_workspace(self, tmp_path):
        import subprocess
        ws = tmp_path
        runs = ws / "Projects" / "P" / ".artifacts" / "runs"
        runs.mkdir(parents=True)
        subprocess.run(["git", "init", "-q"], cwd=ws, check=True)
        subprocess.run(["git", "config", "user.email", "t@t"], cwd=ws, check=True)
        subprocess.run(["git", "config", "user.name", "t"], cwd=ws, check=True)
        return ws

    def _garbage(self, ws, rid):
        _create_run(ws, "P", rid, status="abandoned", hours_ago=240)
        f = ws / "Projects" / "P" / ".artifacts" / "runs" / rid / "run.json"
        d = _read_run(f); d["abandon_reason"] = "crash_zombie"; f.write_text(json.dumps(d))
        return f

    def test_tracked_garbage_is_skipped_by_default(self, tmp_path):
        import subprocess
        from scripts.artifact_cli import purge_garbage_runs
        ws = self._git_workspace(tmp_path)
        self._garbage(ws, "run_tracked")
        subprocess.run(["git", "add", "-A"], cwd=ws, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "track"], cwd=ws, check=True)
        # untracked garbage alongside it
        self._garbage(ws, "run_untracked")
        with patch("scripts.artifact_cli._get_workspace", return_value=ws):
            result = purge_garbage_runs(retention_days=7, apply=True)
        runs = ws / "Projects" / "P" / ".artifacts" / "runs"
        # tracked dir survives (never git-rm'd); untracked is trashed
        assert (runs / "run_tracked").exists(), "a git-TRACKED garbage run must NOT be purged by default"
        assert not (runs / "run_untracked").exists(), "untracked garbage should still be trashed"
        assert result["purged"] == 1
        assert result.get("skipped_tracked", 0) == 1

    def test_non_git_workspace_still_purges(self, workspace):
        """A workspace with no git repo (nothing tracked) purges normally — the
        guard must fail-safe to 'not tracked' when git is unavailable."""
        from scripts.artifact_cli import purge_garbage_runs
        self._garbage(workspace, "run_g")
        with patch("scripts.artifact_cli._get_workspace", return_value=workspace):
            result = purge_garbage_runs(retention_days=7, apply=True)
        assert result["purged"] == 1
        assert not (workspace / "Projects" / "P" / ".artifacts" / "runs" / "run_g").exists()


class TestPurgeFailClosed:
    """run: purge fail-open fix. Two layers used to fail toward DELETION on an
    unattended destructive sweep; both are now fail-CLOSED (keep, don't delete)."""

    CRASH = "session_crash_auto_detected"

    def _git_ws_with_tracked_garbage(self, tmp_path):
        import subprocess
        ws = tmp_path
        runs = ws / "Projects" / "P" / ".artifacts" / "runs"
        runs.mkdir(parents=True)
        subprocess.run(["git", "init", "-q"], cwd=ws, check=True)
        subprocess.run(["git", "config", "user.email", "t@t"], cwd=ws, check=True)
        subprocess.run(["git", "config", "user.name", "t"], cwd=ws, check=True)
        _create_run(ws, "P", "run_tracked", status="abandoned", hours_ago=240)
        f = runs / "run_tracked" / "run.json"
        d = _read_run(f); d["abandon_reason"] = "crash_zombie"; f.write_text(json.dumps(d))
        subprocess.run(["git", "add", "-A"], cwd=ws, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "track"], cwd=ws, check=True)
        return ws, runs

    def test_git_unavailable_in_repo_fails_closed_keeps_tracked(self, tmp_path):
        """If the git SUBPROCESS errors (missing PATH / timeout / lock) INSIDE a git
        working tree, a tracked run must be treated as tracked → SKIPPED, not deleted.
        The old code returned False on any git error → would trash a tracked public run."""
        import subprocess
        from scripts.artifact_cli import purge_garbage_runs
        ws, runs = self._git_ws_with_tracked_garbage(tmp_path)
        # Simulate git being unavailable at ls-files time (the launchd failure mode).
        real_run = subprocess.run

        def _fake_run(cmd, *a, **k):
            if cmd and cmd[0] == "git" and "ls-files" in cmd:
                raise OSError("git not found")
            return real_run(cmd, *a, **k)

        with patch("scripts.artifact_cli._get_workspace", return_value=ws), \
             patch("subprocess.run", side_effect=_fake_run):
            result = purge_garbage_runs(retention_days=7, apply=True)
        assert (runs / "run_tracked").exists(), \
            "git-unavailable INSIDE a repo must fail-closed — tracked run kept, not trashed"
        assert result["purged"] == 0
        assert result.get("skipped_tracked", 0) == 1

    def test_git_fatal_returncode_fails_closed_keeps_tracked(self, tmp_path):
        """A FATAL git rc (128 — corrupt/locked index) at ls-files time must fail-CLOSED,
        NOT be read as 'untracked'. This is the rc-based fail-open the adversarial review
        found: the old `return rc==0` collapsed rc==1 (untracked) and rc==128 (fatal) into
        the same 'delete-eligible' answer, so a corrupt index would trash a TRACKED run."""
        import subprocess
        from scripts.artifact_cli import purge_garbage_runs
        ws, runs = self._git_ws_with_tracked_garbage(tmp_path)
        real_run = subprocess.run

        class _FatalResult:
            returncode = 128
            stdout = b""
            stderr = b"fatal: not a git repository (or any parent up to mount point)"

        def _fake_run(cmd, *a, **k):
            if cmd and cmd[0] == "git" and "ls-files" in cmd:
                return _FatalResult()  # git ran but FAILED fatally
            return real_run(cmd, *a, **k)

        with patch("scripts.artifact_cli._get_workspace", return_value=ws), \
             patch("subprocess.run", side_effect=_fake_run):
            result = purge_garbage_runs(retention_days=7, apply=True)
        assert (runs / "run_tracked").exists(), \
            "a FATAL git rc must fail-closed — tracked run kept, not trashed"
        assert result["purged"] == 0
        assert result.get("skipped_tracked", 0) == 1

    def test_undated_garbage_is_kept_not_purged(self, workspace):
        """Garbage with an unparseable/missing timestamp must be KEPT (fail-closed),
        not treated as old and purged — a schema drift dropping the ts fields must not
        silently make every run purge-eligible."""
        from scripts.artifact_cli import purge_garbage_runs
        runs = workspace / "Projects" / "P" / ".artifacts" / "runs"
        _create_run(workspace, "P", "run_undated", status="abandoned", hours_ago=240)
        f = runs / "run_undated" / "run.json"
        d = _read_run(f)
        d["abandon_reason"] = "crash_zombie"
        # Corrupt/blank all timestamp fields → unparseable
        d["abandoned_at"] = ""; d["updated_at"] = "not-a-date"; d["created_at"] = None
        f.write_text(json.dumps(d))
        with patch("scripts.artifact_cli._get_workspace", return_value=workspace):
            result = purge_garbage_runs(retention_days=7, apply=True)
        assert (runs / "run_undated").exists(), "undated garbage must be kept (fail-closed)"
        assert result["purged"] == 0

    def test_has_git_dir_detects_repo(self, tmp_path):
        from scripts.artifact_cli import _has_git_dir
        (tmp_path / ".git").mkdir()
        deep = tmp_path / "a" / "b" / "c"
        deep.mkdir(parents=True)
        assert _has_git_dir(deep) is True
        assert _has_git_dir(tmp_path) is True

    def test_has_git_dir_false_for_non_repo(self, tmp_path):
        from scripts.artifact_cli import _has_git_dir
        plain = tmp_path / "x" / "y"
        plain.mkdir(parents=True)
        # NOTE: relies on tmp_path having no .git ancestor (pytest tmp dirs don't).
        assert _has_git_dir(plain) is False


# ─────────────────────────────────────────────────────────────────────────────
# Trust boundary — CLI fail-closed order + phantom-run (run_44a06138, P0 sec)
# ─────────────────────────────────────────────────────────────────────────────


def _stub_validator(monkeypatch):
    import sys
    from pathlib import Path as _P
    _scripts_dir = str(_P(__file__).resolve().parent.parent / "scripts")
    if _scripts_dir not in sys.path:
        sys.path.insert(0, _scripts_dir)
    import pipeline_validator
    monkeypatch.setattr(pipeline_validator, "validate_artifact_data", lambda *a, **k: [])
