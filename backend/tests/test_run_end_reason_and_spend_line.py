"""A run that stops says why, and big spend is the user's call.

What is tested (artifact_cli):
- run-update --status abandoned / failed / cancelled without a --reason of at least 10
  characters is refused (exit 2, status unchanged); with one it is stored as
  abandon_reason (abandoned) or end_reason (failed / cancelled);
- re-writing the same ending status (idempotent) needs no reason;
- _spend_stop_check: under SPEND_STOP_TOKENS passes; at or over it refuses with the
  user-decision text; the user's approval is recorded and moves the next line to
  double the approved spend; a too-short approval does not count;
- stage-start refuses past the line and records an approval given with --spend-approved.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import artifact_cli as cli  # noqa: E402


def _run_file(tmp_path, monkeypatch, **extra) -> Path:
    f = tmp_path / "run.json"
    f.write_text(json.dumps({"id": "run_x", "project": "P", "status": "running",
                             "profile": "full", "stages": [], **extra}))
    monkeypatch.setattr(cli, "_resolve_run_file", lambda p, r: f)
    return f


class _Args(SimpleNamespace):
    """run-update's argparse defaults are all None/False: a missing flag reads None."""

    def __getattr__(self, name):
        return None


def _update(status, reason=None):
    return _Args(project="P", run_id="run_x", status=status, reason=reason)


@pytest.mark.parametrize("status,key", [("abandoned", "abandon_reason"),
                                        ("failed", "end_reason"), ("cancelled", "end_reason")])
def test_ending_a_run_needs_a_reason(tmp_path, monkeypatch, capsys, status, key):
    f = _run_file(tmp_path, monkeypatch)
    for bad in (None, "  ", "too short"):
        with pytest.raises(SystemExit) as e:
            cli.cmd_run_update(_update(status, bad), None)
        assert e.value.code == 2
        assert "needs --reason" in capsys.readouterr().err
        assert json.loads(f.read_text())["status"] == "running"
    try:
        cli.cmd_run_update(_update(status, "superseded by run_y, same requirement"), None)
    except SystemExit as e:  # later steps (report, worktree) may exit on a bare fixture
        assert e.code in (0, None)
    st = json.loads(f.read_text())
    assert st["status"] == status and st[key] == "superseded by run_y, same requirement"


def test_spend_line_passes_under_and_stops_over():
    under = {"stages": [{"token_cost": cli.SPEND_STOP_TOKENS - 1}]}
    assert cli._spend_stop_check(under, None, "t") == []
    over = {"stages": [{"token_cost": cli.SPEND_STOP_TOKENS}]}
    err = cli._spend_stop_check(over, None, "t")
    assert len(err) == 1 and "user's decision" in err[0] and "--spend-approved" in err[0]
    assert cli._spend_stop_check(over, "ok go", "t") and "spend_approvals" not in over


def test_an_approval_is_recorded_and_doubles_the_next_line():
    st = {"stages": [{"token_cost": 2_000_000}]}
    assert cli._spend_stop_check(st, "XG: continue, it is close", "t1") == []
    assert st["spend_approvals"][0]["level"] == 2_000_000
    st["stages"].append({"token_cost": 1_000_000})          # 3.0 M < 4.0 M
    assert cli._spend_stop_check(st, None, "t2") == []
    st["stages"].append({"token_cost": 1_000_000})          # 4.0 M
    assert cli._spend_stop_check(st, None, "t3")


def test_stage_start_stops_past_the_line_and_records_an_approval(tmp_path, monkeypatch, capsys):
    f = _run_file(tmp_path, monkeypatch, stages=[{"stage": "build", "token_cost": 1_600_000}])
    monkeypatch.setattr(cli, "_load_run_for_stage",
                        lambda p, r: (f, json.loads(f.read_text())))
    args = SimpleNamespace(project="P", run_id="run_x", stage="review", spend_approved=None)
    with pytest.raises(SystemExit) as e:
        cli.cmd_stage_start(args, None)
    assert e.value.code == 1 and "Spend stop-line" in capsys.readouterr().out
    args.spend_approved = "XG said: keep going, finish it"
    try:
        cli.cmd_stage_start(args, None)
    except (SystemExit, Exception):  # the rest of stage-start needs a full workspace
        pass
    assert json.loads(f.read_text())["spend_approvals"][0]["approval"].startswith("XG said")


def test_deliver_and_reflect_are_never_stopped(tmp_path, monkeypatch, capsys):
    f = _run_file(tmp_path, monkeypatch, stages=[{"stage": "build", "token_cost": 9_000_000}])
    monkeypatch.setattr(cli, "_load_run_for_stage",
                        lambda p, r: (f, json.loads(f.read_text())))
    for stage in ("deliver", "reflect"):
        args = SimpleNamespace(project="P", run_id="run_x", stage=stage, spend_approved=None)
        try:
            cli.cmd_stage_start(args, None)
        except (SystemExit, Exception):  # the rest of stage-start needs a full workspace
            pass
        assert "Spend stop-line" not in capsys.readouterr().out
