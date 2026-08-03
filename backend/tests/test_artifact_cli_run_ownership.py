"""Regression tests for cross-run contamination (run_3caef1d3 → run_f3975b8b).

Bug: `artifact_cli` GUESSED which run to write/validate when `--run-id` was
absent — it picked the project-wide newest active (running/paused) run with NO
owner scoping. `cmd_publish` used that guess as the auto-record target (and for
profile detection); `_auto_validate_before_advance` used it to pick a run to
validate. With 2+ active runs in one project, a `publish --stage` lacking
`--run-id` auto-recorded its stage stub into a SIBLING session's run.json
(observed: run_4341fc50's publishes contaminated the unrelated run_b9ecb07a).

Fix (run_f3975b8b — eradicate guessing entirely): the ability to guess a target
run is the ROOT of all cross-run contamination, so it is removed wholesale —
NEVER guess, not even with a single active run (it may still be a sibling's,
per XG directive). Concretely:
- `publish --stage` with NO `--run-id` → hard-fails exit 3 + stderr, at ANY
  active-run count (0, 1, or N). The artifact is still published; only the
  guess of WHERE to record it is refused.
- profile-detection with no `--run-id` → defaults to "full" (strict), never
  reads a guessed run's profile.
- `advance` with no `--run-id` → SKIPS auto-validation (never validates/blocks a
  guessed run); with `--run-id` it validates ONLY the named run.
- the `_find_active_run`/`_find_active_runs` helpers are DELETED — they existed
  only to guess targets and now have zero production callers.

Tests below drive cmd_publish / cmd_advance / _auto_validate_before_advance
directly (the real behavior), not a helper.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta

import pytest


def _mk_run(workspace, run_id, status="running", minutes_ago=0, profile="full",
            stages=None):
    run_dir = workspace / "Projects" / "P" / ".artifacts" / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    created = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
    run_dir.joinpath("run.json").write_text(json.dumps({
        "id": run_id,
        "project": "P",
        "requirement": f"req {run_id}",
        "profile": profile,
        "status": status,
        "stages": stages or [],
        "created_at": created.isoformat(),
        "updated_at": created.isoformat(),
    }, indent=2))
    return run_id


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


class TestGuessHelpersDeleted:
    """DoD#6: the guess-by-newest helpers are gone — zero code path can pick a
    run by 'newest active' to mutate/validate it."""

    def test_find_active_run_helpers_removed(self):
        import scripts.artifact_cli as cli
        assert not hasattr(cli, "_find_active_run"), \
            "_find_active_run (singular) must be deleted — it guessed targets"
        assert not hasattr(cli, "_find_active_runs"), \
            "_find_active_runs (plural) must be deleted — it guessed targets"


class TestPublishAutoRecordOwnership:
    """cmd_publish must NEVER auto-record into a run it cannot prove it owns —
    and 'prove it owns' means an explicit --run-id, never a guess."""

    def test_no_run_id_fails_closed_with_two_active(self, tmp_path, capsys, monkeypatch):
        """AC1: 2+ active runs, no --run-id → fail closed (exit 3 + STDERR), NOT a
        silent guess into a sibling run. Neither run is contaminated."""
        cli, reg = _cli_and_reg(_real_workspace(tmp_path))
        import pipeline_validator
        monkeypatch.setattr(pipeline_validator, "validate_artifact_data", lambda *a, **k: [])

        _mk_run(tmp_path, "run_mine", status="running", minutes_ago=1)
        _mk_run(tmp_path, "run_sibling", status="running", minutes_ago=5)

        with pytest.raises(SystemExit) as exc:
            cli.cmd_publish(_publish_args(run_id=None), reg)
        assert exc.value.code == 3, "no --run-id must fail closed (exit 3)"

        # Error MUST be on STDERR (Gate-2 #1): the documented orchestrator guard
        # reads stderr on failure and feeds stdout to json.load(...)['artifact_id'].
        captured = capsys.readouterr()
        payload = json.loads(captured.err.strip().splitlines()[-1])
        assert "REFUSED" in payload["error"]

        for rid in ("run_mine", "run_sibling"):
            data = json.loads((tmp_path / "Projects" / "P" / ".artifacts" / "runs" / rid / "run.json").read_text())
            assert data["stages"] == [], f"{rid} was contaminated with a guessed stub"

    def test_no_run_id_fails_closed_with_single_active(self, tmp_path, capsys, monkeypatch):
        """AC1 (XG directive — the core eradication): EVEN with exactly ONE active
        run, no --run-id must FAIL CLOSED. That single run may belong to a sibling
        session, so guessing it is still contamination. Supersedes the old
        single-active 'safety-net' auto-record contract."""
        cli, reg = _cli_and_reg(_real_workspace(tmp_path))
        import pipeline_validator
        monkeypatch.setattr(pipeline_validator, "validate_artifact_data", lambda *a, **k: [])

        _mk_run(tmp_path, "run_solo", status="running")

        with pytest.raises(SystemExit) as exc:
            cli.cmd_publish(_publish_args(run_id=None), reg)
        assert exc.value.code == 3, "single active + no --run-id must STILL fail closed"

        captured = capsys.readouterr()
        payload = json.loads(captured.err.strip().splitlines()[-1])
        assert "REFUSED" in payload["error"]

        data = json.loads((tmp_path / "Projects" / "P" / ".artifacts" / "runs" / "run_solo" / "run.json").read_text())
        assert data["stages"] == [], "the lone run must NOT be guessed-into"

    def test_no_run_id_fails_closed_with_zero_active(self, tmp_path, capsys, monkeypatch):
        """AC1: no active runs at all + no --run-id → still exit 3 (no target to
        record into; refuse rather than no-op silently)."""
        cli, reg = _cli_and_reg(_real_workspace(tmp_path))
        import pipeline_validator
        monkeypatch.setattr(pipeline_validator, "validate_artifact_data", lambda *a, **k: [])

        with pytest.raises(SystemExit) as exc:
            cli.cmd_publish(_publish_args(run_id=None), reg)
        assert exc.value.code == 3

    def test_explicit_run_id_records_into_that_run_only(self, tmp_path, monkeypatch):
        """AC2: explicit --run-id is unchanged — records into THAT run even with a
        newer sibling present."""
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
        """AC2 (profile): explicit --run-id to a COMPLETED run must validate against
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

        _mk_run(tmp_path, "run_done", status="completed", profile="trivial")
        _mk_run(tmp_path, "run_other", status="running", profile="full")

        cli.cmd_publish(_publish_args(run_id="run_done"), reg)
        assert seen_profile["p"] == "trivial", (
            f"explicit --run-id should validate against the named run's profile "
            f"(trivial), got {seen_profile.get('p')}"
        )

    def test_no_run_id_profile_defaults_full_not_guessed(self, tmp_path, monkeypatch):
        """AC3: with no --run-id, profile validation must default to 'full' — it
        must NOT read a guessed active run's profile. (Publish still fails closed
        at auto-record, but validation runs first, so the profile path is exercised.)"""
        cli, reg = _cli_and_reg(_real_workspace(tmp_path))
        import pipeline_validator

        seen_profile = {}

        def _capture(stage, data, profile="full"):
            seen_profile["p"] = profile
            return []

        monkeypatch.setattr(pipeline_validator, "validate_artifact_data", _capture)

        # A single active 'trivial' run that the OLD code would have read the
        # profile from. New code must ignore it and use 'full'.
        _mk_run(tmp_path, "run_solo", status="running", profile="trivial")

        with pytest.raises(SystemExit):  # auto-record still fails closed (AC1)
            cli.cmd_publish(_publish_args(run_id=None), reg)
        assert seen_profile["p"] == "full", (
            "no --run-id must validate against 'full', never a guessed run's profile"
        )


class TestAdvanceValidationOwnership:
    """advance must validate ONLY the explicitly-named run, never a guessed one."""

    def _completed_stage(self):
        return [{"stage": "build", "status": "completed", "artifact_id": "art_x",
                 "stage_doc_consumed": True}]

    def test_advance_without_run_id_skips_validation(self, tmp_path, monkeypatch):
        """AC4: no --run-id → auto-validate is skipped (never guesses a run to
        validate/block). Must NOT raise even when an active run exists."""
        import sys
        from pathlib import Path as _P
        _scripts_dir = str(_P(__file__).resolve().parent.parent / "scripts")
        if _scripts_dir not in sys.path:
            sys.path.insert(0, _scripts_dir)
        import scripts.artifact_cli as cli
        monkeypatch.setattr(cli, "_get_workspace", lambda: tmp_path)

        # An active run whose stage would FAIL validation if it were picked.
        _mk_run(tmp_path, "run_sibling", status="running",
                stages=self._completed_stage())

        called = {"n": 0}
        import subprocess as _sp
        monkeypatch.setattr(_sp, "run", lambda *a, **k: called.__setitem__("n", called["n"] + 1) or _sp.CompletedProcess(a, 0, "", ""))

        # No run-id → must be a clean no-op (no validation, no exit).
        cli._auto_validate_before_advance("P", "review", None)
        assert called["n"] == 0, "validator must not run when --run-id is absent"

    def test_advance_with_unknown_run_id_is_noop(self, tmp_path, monkeypatch):
        """AC4: a typo'd / unknown --run-id → no-op (run.json not found), never
        touches another run."""
        import sys
        from pathlib import Path as _P
        _scripts_dir = str(_P(__file__).resolve().parent.parent / "scripts")
        if _scripts_dir not in sys.path:
            sys.path.insert(0, _scripts_dir)
        import scripts.artifact_cli as cli
        monkeypatch.setattr(cli, "_get_workspace", lambda: tmp_path)
        _mk_run(tmp_path, "run_real", status="running", stages=self._completed_stage())

        # Should simply return — no exception, no validation of run_real.
        cli._auto_validate_before_advance("P", "review", "run_typo_nonexistent")

    def test_advance_validates_only_named_run(self, tmp_path, monkeypatch):
        """AC4: with --run-id, validation reads exactly that run's stages (not a
        sibling's). We assert the validator is invoked against the named run's
        current stage."""
        import sys
        from pathlib import Path as _P
        _scripts_dir = str(_P(__file__).resolve().parent.parent / "scripts")
        if _scripts_dir not in sys.path:
            sys.path.insert(0, _scripts_dir)
        import scripts.artifact_cli as cli
        monkeypatch.setattr(cli, "_get_workspace", lambda: tmp_path)

        _mk_run(tmp_path, "run_named", status="running", stages=self._completed_stage())
        _mk_run(tmp_path, "run_sibling", status="running", minutes_ago=0,
                stages=self._completed_stage())

        seen = {}
        import subprocess as _sp

        def _fake_run(cmd, *a, **k):
            # capture the run.json path the validator was pointed at
            seen["cmd"] = cmd
            return _sp.CompletedProcess(cmd, 0, json.dumps({"valid": True}), "")

        monkeypatch.setattr(_sp, "run", _fake_run)
        cli._auto_validate_before_advance("P", "review", "run_named")
        # The validator subprocess must reference the NAMED run, never the sibling.
        joined = " ".join(seen.get("cmd", []))
        assert seen.get("cmd"), "validator must actually be invoked for the named run"
        assert "run_named" in joined, "validator must target the named run"
        assert "run_sibling" not in joined, "validator must NOT touch the sibling run"

    def test_advance_deliver_stage_checks_named_run_report(self, tmp_path, monkeypatch, capsys):
        """run_f3975b8b BLOCKER regression (Gate-2): the deliver-stage REPORT.md
        gate must build the run dir from the NAMED run_id — not the deleted
        `artifacts_dir` (which raised NameError on the live deliver→reflect path).
        A completed deliver stage with NO REPORT.md must BLOCK (exit 1), and the
        error must be the REPORT.md message, NOT a 'Validator crashed: name
        artifacts_dir is not defined' surrogate."""
        import sys
        from pathlib import Path as _P
        _scripts_dir = str(_P(__file__).resolve().parent.parent / "scripts")
        if _scripts_dir not in sys.path:
            sys.path.insert(0, _scripts_dir)
        import scripts.artifact_cli as cli
        monkeypatch.setattr(cli, "_get_workspace", lambda: tmp_path)

        _mk_run(tmp_path, "run_deliver", status="running",
                stages=[{"stage": "deliver", "status": "completed",
                         "artifact_id": "art_d", "stage_doc_consumed": True}])
        # No REPORT.md on disk → must block with the REPORT.md error (not NameError).
        with pytest.raises(SystemExit) as exc:
            cli._auto_validate_before_advance("P", "reflect", "run_deliver")
        assert exc.value.code == 1
        captured = capsys.readouterr()
        assert "REPORT.md not found" in captured.err
        assert "artifacts_dir" not in captured.err, "NameError must not surface"
