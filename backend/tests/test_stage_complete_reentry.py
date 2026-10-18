"""stage-complete refuses to re-complete a stage whose measured window is closed (run_376299fa).

Bug: ``cmd_stage_complete`` measures a stage's token cost over
``[stage_timing.started_at, now]``. Only ``stage-start`` re-opens a completed stage (new
started_at, completed_at popped, first_started_at kept). Calling stage-complete again on a
completed stage WITHOUT stage-start measured from the ORIGINAL start, across every later
stage (run_a87bfe3f: a re-recorded BUILD read 816,406 measured tokens against 363,143,
its window spanning REVIEW and TEST), and overwrote the original completed_at.

What is tested
    - AC1: a stage with BOTH started_at and completed_at is refused (also on --check)
      with E_STAGE_NOT_OPEN naming the stage-start command; nothing is written.
    - AC2: stage-start then stage-complete re-records it; the measured window starts at
      the new started_at, and first_started_at keeps the original start.
    - AC3: a stage with completed_at but NO started_at (never entered through stage-start)
      is not refused — it measures nothing either way. The MED-3 replan tests in
      test_stage_boundary_commands.py rely on this: their fixture completes plan/build
      without stage-start. If that fixture ever seeds started_at, the new guard fires
      before the exit-criteria check and those tests must call stage-start first.
    - AC4: the plan card's replan line names stage-start.
"""
from __future__ import annotations

import json
from pathlib import Path

from tests.test_stage_boundary_commands import (  # noqa: F401 — `ws` is a fixture
    DECISION, PROJECT, _build_artifact, _complete, _make_run, _plan_artifact, _run_dir,
    _run_state, _snapshot, _start, cli, ws,
)

_PLAN_CARD = (Path(__file__).resolve().parent.parent
              / "skills" / "s_autonomous-pipeline" / "stages" / "plan.md")


def _completed_plan(root, reg, capsys, rid):
    _make_run(root, rid, "bugfix", stages=["evaluate", "think"])
    assert _start(reg, capsys, rid, "plan")[0] == 0
    code, out = _complete(reg, capsys, rid, "plan", _plan_artifact(), {"decisions": DECISION})
    assert code == 0, out
    t = _run_state(root, rid)["stage_timing"]["plan"]
    assert t.get("started_at") and t.get("completed_at")
    return t


def test_recomplete_without_stage_start_is_refused_and_writes_nothing(ws, capsys):
    root, reg = ws
    rid = "run_reent1"
    _completed_plan(root, reg, capsys, rid)
    before = _snapshot(root)
    for check in (True, False):
        code, out = _complete(reg, capsys, rid, "plan", _plan_artifact(),
                              {"decisions": DECISION}, check=check)
        assert code == 1, out
        err = out["errors"][0]
        assert err["code"] == "E_STAGE_NOT_OPEN", out
        assert f"stage-start --project {PROJECT} --run-id {rid} --stage plan" in err["message"], out
        assert _snapshot(root) == before


def test_stage_start_reopens_and_measures_only_the_new_window(ws, capsys, monkeypatch):
    root, reg = ws
    rid = "run_reent2"
    first = _completed_plan(root, reg, capsys, rid)
    seen = []
    real = cli._measure_stage_tokens

    def _spy(session_id, start_iso, end_iso, **kw):
        seen.append(start_iso)
        return real(session_id, start_iso, end_iso, **kw)
    monkeypatch.setattr(cli, "_measure_stage_tokens", _spy)

    assert _start(reg, capsys, rid, "plan")[0] == 0
    reopened = _run_state(root, rid)["stage_timing"]["plan"]
    assert reopened["first_started_at"] == first["started_at"]
    assert reopened["started_at"] != first["started_at"], "stage-start must open a NEW window"
    assert "completed_at" not in reopened
    code, out = _complete(reg, capsys, rid, "plan", _plan_artifact(), {"decisions": DECISION})
    assert code == 0, out
    assert seen and seen[-1] == reopened["started_at"]
    assert seen[-1] >= first["completed_at"], "the re-record window must start after the old one closed"


def test_stage_without_started_at_is_not_refused(ws, capsys):
    root, reg = ws
    rid = "run_reent3"
    _make_run(root, rid, "bugfix", stages=["evaluate", "think"],
              stage_timing={"plan": {"completed_at": "2026-01-01T00:05:00+00:00"}})
    code, out = _complete(reg, capsys, rid, "plan", _plan_artifact(), {"decisions": DECISION})
    assert code == 0, out


def test_plan_card_replan_line_names_stage_start():
    text = _PLAN_CARD.read_text(encoding="utf-8")
    line = next(ln for ln in text.splitlines() if "A replan after a later stage completed" in ln)
    assert "stage-start --project <P> --run-id <R> --stage plan" in line, line


# ── Gate-2 (run_376299fa): a re-record covers the stage's OWN windows only ──────
def test_stage_start_records_the_closed_window(ws, capsys):
    root, reg = ws
    rid = "run_reent4"
    first = _completed_plan(root, reg, capsys, rid)
    assert _start(reg, capsys, rid, "plan")[0] == 0
    t = _run_state(root, rid)["stage_timing"]["plan"]
    assert t["prior_windows"] == [{"started_at": first["started_at"],
                                   "completed_at": first["completed_at"]}]


def test_rerecord_adds_the_earlier_window_cost(ws, capsys, monkeypatch):
    """Red-team MED: measuring only the new window replaced the first run's cost."""
    root, reg = ws
    rid = "run_reent5"
    usage = {"input_tokens": 100, "output_tokens": 10, "cache_creation_tokens": 0}
    monkeypatch.setattr(cli, "_measure_stage_tokens", lambda *a, **k: dict(usage))
    _completed_plan(root, reg, capsys, rid)
    first_rec = next(s for s in _run_state(root, rid)["stages"] if s["stage"] == "plan")
    assert first_rec["token_cost"] == 110 and first_rec["token_cost_source"] == "measured"
    assert _start(reg, capsys, rid, "plan")[0] == 0
    code, out = _complete(reg, capsys, rid, "plan", _plan_artifact(), {"decisions": DECISION})
    assert code == 0, out
    rec = next(s for s in _run_state(root, rid)["stages"] if s["stage"] == "plan")
    assert rec["token_cost"] == 220, rec
    assert rec["token_usage"]["input_tokens"] == 200


def test_budget_counts_only_the_stage_windows(monkeypatch, tmp_path):
    """Correctness MED: spawns in the gap (later stages) are not charged to a re-record."""
    calls = []
    monkeypatch.setattr(cli, "_completed_spawns",
                        lambda run_id, lo, hi: calls.append((lo, hi)) or
                        {"by_expert": {"gate1-skeptic": 1}, "unattributed": 0, "legacy": 0})
    state = {"stage_timing": {"build": {
        "first_started_at": "2026-01-01T00:00:00+00:00",
        "prior_windows": [{"started_at": "2026-01-01T00:00:00+00:00",
                           "completed_at": "2026-01-01T00:10:00+00:00"}],
        "started_at": "2026-01-01T01:00:00+00:00"}}}
    out = cli._stage_budget("run_b", tmp_path, state, "build", {})
    gap_lo, gap_hi = cli._iso_epoch("2026-01-01T00:10:00+00:00"), cli._iso_epoch("2026-01-01T01:00:00+00:00")
    assert len(calls) == 2
    assert all(hi <= gap_lo or lo >= gap_hi for lo, hi in calls), calls
    assert out["spawns"]["by_expert"]["gate1-skeptic"] == 2


def test_budget_without_prior_windows_is_unchanged(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(cli, "_completed_spawns",
                        lambda run_id, lo, hi: calls.append((lo, hi)) or
                        {"by_expert": {}, "unattributed": 0, "legacy": 0})
    state = {"stage_timing": {"build": {"first_started_at": "2026-01-01T00:00:00+00:00",
                                        "started_at": "2026-01-01T01:00:00+00:00"}}}
    cli._stage_budget("run_b", tmp_path, state, "build", {})
    assert len(calls) == 1 and calls[0][0] == cli._iso_epoch("2026-01-01T00:00:00+00:00")


def test_two_rerecords_sum_every_window_once(ws, capsys, monkeypatch):
    root, reg = ws
    rid = "run_reent6"
    usage = {"input_tokens": 100, "output_tokens": 10, "cache_creation_tokens": 0,
             "cost_usd": 0.5, "complete": True, "source": "transcript"}
    monkeypatch.setattr(cli, "_measure_stage_tokens", lambda *a, **k: dict(usage))
    _completed_plan(root, reg, capsys, rid)
    for _ in range(2):
        assert _start(reg, capsys, rid, "plan")[0] == 0
        assert _complete(reg, capsys, rid, "plan", _plan_artifact(), {"decisions": DECISION})[0] == 0
    st = _run_state(root, rid)
    rec = next(s for s in st["stages"] if s["stage"] == "plan")
    assert rec["token_cost"] == 330 and rec["token_cost_source"] == "measured"
    assert len(st["stage_timing"]["plan"]["prior_windows"]) == 2
    assert rec["token_usage"]["cost_usd"] == 1.5
    assert rec["token_usage"]["complete"] is True and rec["token_usage"]["source"] == "transcript"


def test_unmeasured_new_window_keeps_the_earlier_cost(ws, capsys, monkeypatch):
    root, reg = ws
    rid = "run_reent7"
    calls = []
    monkeypatch.setattr(cli, "_measure_stage_tokens",
                        lambda *a, **k: calls.append(1) or
                        ({"input_tokens": 100, "output_tokens": 10, "cache_creation_tokens": 0}
                         if len(calls) == 1 else None))
    _completed_plan(root, reg, capsys, rid)
    assert _start(reg, capsys, rid, "plan")[0] == 0
    assert _complete(reg, capsys, rid, "plan", _plan_artifact(), {"decisions": DECISION})[0] == 0
    rec = next(s for s in _run_state(root, rid)["stages"] if s["stage"] == "plan")
    assert rec["token_cost"] == 110, rec
    assert rec["token_cost_source"] != "measured"
