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




class TestRunDirPathTraversal:
    """#2 (run_dd8411f6): `_run_dir`/`_resolve_run_file` must reject a run_id
    that could escape its project's runs/ dir. Before the fix, `_run_dir`
    concatenated the caller-supplied run_id with zero validation, so a run_id
    like '../../../OTHER/.artifacts/runs/run_x' escaped to another project —
    every run-id-bearing command (update/checkpoint/resume/observe/report/commit)
    inherited the hole via `_run_dir`."""

    def _cli(self, tmp_path, monkeypatch):
        import sys
        from pathlib import Path as _P
        _scripts_dir = str(_P(__file__).resolve().parent.parent / "scripts")
        if _scripts_dir not in sys.path:
            sys.path.insert(0, _scripts_dir)
        import scripts.artifact_cli as cli
        monkeypatch.setattr(cli, "_get_workspace", lambda: tmp_path)
        return cli





    def test_main_translates_traversal_valueerror_to_clean_exit(self, tmp_path, monkeypatch, capsys):
        """AC3 (Gate-1 amendment B): a traversal run_id reaching main() must exit
        cleanly with the CLI's standard {"error":...} JSON + non-zero code (2),
        NOT crash with an un-caught ValueError traceback. The traversal is blocked
        either way; this makes the refusal legible rather than a stack trace."""
        import sys
        cli = self._cli(tmp_path, monkeypatch)
        (tmp_path / "Projects" / "P" / ".artifacts" / "runs").mkdir(parents=True)
        monkeypatch.setattr(sys, "argv", [
            "artifact_cli.py", "run-get",
            "--project", "P",
            "--run-id", "../../../etc/runs/run_evil",
        ])
        with pytest.raises(SystemExit) as exc:
            cli.main()
        assert exc.value.code == 2, "traversal must exit 2, not crash with a traceback"
        err = capsys.readouterr().err
        parsed = json.loads(err.strip().splitlines()[-1])
        assert "error" in parsed, f"stderr must be the standard JSON error, got: {err!r}"
