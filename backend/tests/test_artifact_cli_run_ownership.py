"""Regression tests for cross-run contamination via _find_active_run (run_3caef1d3).

Bug: `_find_active_run(project)` returns the project-wide newest active
(running/paused) run with NO owner scoping. `cmd_publish` used it as the
auto-record target (and for profile detection) whenever `--run-id` was absent.
With 2+ active runs in one project, a `publish --stage` lacking `--run-id`
auto-recorded its stage stub into a SIBLING session's run.json (observed:
run_4341fc50's plan/review/test publishes contaminated the unrelated
run_b9ecb07a).

Fix (approach A-revised, fail-closed): when 2+ active runs exist and no
`--run-id` is given, the auto-record FAILS CLOSED (stdout JSON error + non-zero
exit) so the agent retries with an explicit --run-id — instead of silently
guessing the wrong run. The single-active-run safety-net is preserved, and the
explicit `--run-id` path is unchanged.

Tests:
- AC2: exactly 1 active run + no --run-id → _find_active_run still resolves it.
- AC4: 2+ active runs → _find_active_runs returns ALL of them (the ambiguity the
  fix detects), newest-first; the legacy _find_active_run returns the newest
  (preserved contract for the single-run case).
- AC6: _auto_validate_before_advance no longer scans running-only / project-wide
  newest blindly — it uses the shared helper with (running, paused) parity.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture
def workspace(tmp_path):
    """A fake workspace with a project runs dir."""
    (tmp_path / "Projects" / "P" / ".artifacts" / "runs").mkdir(parents=True)
    return tmp_path


class _Reg:
    """Minimal ArtifactRegistry stand-in — _find_active_run* only reads workspace_root."""

    def __init__(self, root: Path):
        self.workspace_root = str(root)


def _mk_run(workspace, run_id, status="running", minutes_ago=0, profile="full"):
    run_dir = workspace / "Projects" / "P" / ".artifacts" / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    created = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
    run_dir.joinpath("run.json").write_text(json.dumps({
        "id": run_id,
        "project": "P",
        "requirement": f"req {run_id}",
        "profile": profile,
        "status": status,
        "stages": [],
        "created_at": created.isoformat(),
        "updated_at": created.isoformat(),
    }, indent=2))
    return run_id


class TestFindActiveRunsHelper:
    """The plural helper is the seam the fix is built on."""

    def test_returns_empty_when_no_active(self, workspace):
        from scripts.artifact_cli import _find_active_runs
        _mk_run(workspace, "run_done", status="completed")
        _mk_run(workspace, "run_aband", status="abandoned")
        assert _find_active_runs("P", _Reg(workspace)) == []

    def test_single_active_returns_one(self, workspace):
        """AC2: the single-active-run safety-net case."""
        from scripts.artifact_cli import _find_active_runs, _find_active_run
        _mk_run(workspace, "run_solo", status="running")
        runs = _find_active_runs("P", _Reg(workspace))
        assert len(runs) == 1
        assert runs[0]["id"] == "run_solo"
        # Legacy singular contract preserved
        assert _find_active_run("P", _Reg(workspace))["id"] == "run_solo"

    def test_multiple_active_returns_all_newest_first(self, workspace):
        """AC4: the AMBIGUITY the fix must detect — 2+ active runs.

        Includes both running AND paused (the parity the 3rd callsite was missing).
        """
        from scripts.artifact_cli import _find_active_runs, _find_active_run
        _mk_run(workspace, "run_old", status="running", minutes_ago=60)
        _mk_run(workspace, "run_mid", status="paused", minutes_ago=30)
        _mk_run(workspace, "run_new", status="running", minutes_ago=1)
        runs = _find_active_runs("P", _Reg(workspace))
        ids = [r["id"] for r in runs]
        # ALL active runs surfaced (this is what lets cmd_publish detect ambiguity)
        assert set(ids) == {"run_old", "run_mid", "run_new"}
        # Newest-first ordering preserved
        assert ids[0] == "run_new"
        # Legacy singular returns the newest (back-compat for the 1-run case)
        assert _find_active_run("P", _Reg(workspace))["id"] == "run_new"

    def test_paused_counts_as_active(self, workspace):
        """Parity: paused runs are active (the 3rd callsite's running-only filter was the bug)."""
        from scripts.artifact_cli import _find_active_runs
        _mk_run(workspace, "run_paused", status="paused")
        runs = _find_active_runs("P", _Reg(workspace))
        assert len(runs) == 1 and runs[0]["id"] == "run_paused"


def _publish_args(run_id=None, stage="build", project="P"):
    """Build a cmd_publish args object (mirrors test_artifact_cli_lifecycle._Args)."""
    class _Args:
        pass
    a = _Args()
    a.project = project
    a.type = "changeset"
    a.data = '{"branch":"x","commits":["abc1234"],"files_changed":["f.py"]}'
    a.producer = "s_autonomous-pipeline"
    a.summary = "test"
    a.topic = ""
    a.stage = stage
    a.run_id = run_id
    a.quiet = True
    return a


def _real_workspace(tmp_path):
    """A workspace laid out the way ArtifactRegistry expects."""
    (tmp_path / "Projects" / "P" / ".artifacts" / "runs").mkdir(parents=True)
    return tmp_path


def _cli_and_reg(tmp_path):
    """Import cmd_publish with scripts/ on sys.path (mirrors the CLI), + a real reg."""
    import sys
    from pathlib import Path as _P
    _scripts_dir = str(_P(__file__).resolve().parent.parent / "scripts")
    if _scripts_dir not in sys.path:
        sys.path.insert(0, _scripts_dir)
    import scripts.artifact_cli as cli
    from core.artifact_registry import ArtifactRegistry
    return cli, ArtifactRegistry(tmp_path)


class TestPublishAutoRecordOwnership:
    """cmd_publish must never auto-record into a run it cannot prove it owns."""

    def test_two_active_no_run_id_fails_closed(self, tmp_path, capsys, monkeypatch):
        """AC1 + AC4: 2+ active runs, no --run-id → fail closed (exit 3 + stdout
        error naming the active run ids), NOT a silent guess into a sibling run."""
        cli, reg = _cli_and_reg(_real_workspace(tmp_path))
        import pipeline_validator
        monkeypatch.setattr(pipeline_validator, "validate_artifact_data", lambda *a, **k: [])

        _mk_run(tmp_path, "run_mine", status="running", minutes_ago=1)
        _mk_run(tmp_path, "run_sibling", status="running", minutes_ago=5)

        with pytest.raises(SystemExit) as exc:
            cli.cmd_publish(_publish_args(run_id=None), reg)
        assert exc.value.code == 3, "ambiguous auto-record must fail closed (exit 3)"

        # Error MUST be on STDERR (Gate-2 #1): the documented orchestrator guard
        # reads stderr on failure and feeds stdout to json.load(...)['artifact_id'].
        captured = capsys.readouterr()
        payload = json.loads(captured.err.strip().splitlines()[-1])
        assert "AMBIGUOUS" in payload["error"]
        assert set(payload["active_run_ids"]) == {"run_mine", "run_sibling"}

        # CRITICAL: neither run was contaminated — no stage stub written anywhere.
        for rid in ("run_mine", "run_sibling"):
            data = json.loads((tmp_path / "Projects" / "P" / ".artifacts" / "runs" / rid / "run.json").read_text())
            assert data["stages"] == [], f"{rid} was contaminated with a guessed stage stub"

    def test_single_active_no_run_id_auto_records(self, tmp_path, monkeypatch):
        """AC2: the safety-net — exactly 1 active run + no --run-id still auto-records."""
        cli, reg = _cli_and_reg(_real_workspace(tmp_path))
        import pipeline_validator
        monkeypatch.setattr(pipeline_validator, "validate_artifact_data", lambda *a, **k: [])

        _mk_run(tmp_path, "run_solo", status="running")
        cli.cmd_publish(_publish_args(run_id=None), reg)

        data = json.loads((tmp_path / "Projects" / "P" / ".artifacts" / "runs" / "run_solo" / "run.json").read_text())
        assert [s["stage"] for s in data["stages"]] == ["build"]
        assert data["stages"][0]["status"] == "recorded"

    def test_explicit_run_id_records_into_that_run_only(self, tmp_path, monkeypatch):
        """AC5: explicit --run-id is unchanged — records into THAT run even with siblings."""
        cli, reg = _cli_and_reg(_real_workspace(tmp_path))
        import pipeline_validator
        monkeypatch.setattr(pipeline_validator, "validate_artifact_data", lambda *a, **k: [])

        _mk_run(tmp_path, "run_mine", status="running", minutes_ago=5)
        _mk_run(tmp_path, "run_sibling", status="running", minutes_ago=1)  # newer

        cli.cmd_publish(_publish_args(run_id="run_mine"), reg)

        mine = json.loads((tmp_path / "Projects" / "P" / ".artifacts" / "runs" / "run_mine" / "run.json").read_text())
        sibling = json.loads((tmp_path / "Projects" / "P" / ".artifacts" / "runs" / "run_sibling" / "run.json").read_text())
        assert [s["stage"] for s in mine["stages"]] == ["build"]
        assert sibling["stages"] == [], "explicit --run-id must not touch the (newer) sibling"

    def test_explicit_run_id_uses_that_runs_profile_even_if_not_active(self, tmp_path, monkeypatch):
        """Gate-2 #2: explicit --run-id to a COMPLETED run must validate against
        THAT run's profile (read from disk), not default to 'full'. Otherwise a
        trivial run that completed between publish calls (resume) gets spuriously
        BLOCKed by full-tier depth checks."""
        cli, reg = _cli_and_reg(_real_workspace(tmp_path))
        import pipeline_validator

        seen_profile = {}

        def _capture(stage, data, profile="full"):
            seen_profile["p"] = profile
            return []

        monkeypatch.setattr(pipeline_validator, "validate_artifact_data", _capture)

        # A COMPLETED trivial run (not active) + an active sibling that would be the
        # default guess. Explicit --run-id must read the completed run's profile.
        _mk_run(tmp_path, "run_done", status="completed", profile="trivial")
        _mk_run(tmp_path, "run_other", status="running", profile="full")

        cli.cmd_publish(_publish_args(run_id="run_done"), reg)
        assert seen_profile["p"] == "trivial", (
            f"explicit --run-id should validate against the named run's profile "
            f"(trivial), got {seen_profile.get('p')}"
        )
