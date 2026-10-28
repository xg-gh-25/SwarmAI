"""cdf_rehearsal_replay: the full-rehearsal driver over a workspace copy.

Tests (the real sources, worker and writer; only the model is a fixture):
- the guard refuses the live ~/.swarm-ai and a data dir without data.db;
- a dry run counts the sessions in the window and calls nothing;
- a real replay of one session goes source -> Inbox -> worker -> writer and the report
  carries per-source receipts, outcomes and usage;
- the budget stops the replay before the next item.
"""
from __future__ import annotations

import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from tests.test_cdf_v2_e2e import (  # noqa: F401  (cdf_env is a fixture)
    SESSION, Extractor, Judge, _cognition, _record_person_message, cdf_env,
)

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import cdf_rehearsal_replay as replay_mod  # noqa: E402


def _session_row(data: Path, session_id: str, updated: datetime) -> None:
    with sqlite3.connect(data / "data.db") as conn:
        conn.execute("INSERT INTO sessions(id, title, created_at, updated_at) VALUES (?,?,?,?)",
                     (session_id, "t", updated.isoformat(), updated.isoformat()))


def test_guard_refuses_live_dir_and_missing_db(tmp_path: Path) -> None:
    live = Path.home() / ".swarm-ai"
    with pytest.raises(SystemExit, match="live"):
        replay_mod.guard_paths(live, tmp_path)
    with pytest.raises(SystemExit, match="data.db"):
        replay_mod.guard_paths(tmp_path, tmp_path)


def test_dry_run_counts_window_and_calls_nothing(cdf_env) -> None:
    now = datetime.now(timezone.utc)
    _session_row(cdf_env["data"], SESSION, now - timedelta(days=1))
    _session_row(cdf_env["data"], "old-session", now - timedelta(days=30))
    report = replay_mod.replay(cdf_env["data"], cdf_env["workspace"], days=7, dry_run=True)
    assert report["sources"]["s2"]["items"] == 1
    assert report["sources"]["s5"]["items"] == 1          # the copy's EVOLUTION.md
    assert report["sources"]["s6"]["items"] == 0          # no signal digest in the copy
    assert report["usage"]["calls"] == 0 and "outcomes" not in report


def test_replay_runs_one_session_end_to_end(cdf_env) -> None:
    _session_row(cdf_env["data"], SESSION, datetime.now(timezone.utc))
    event_ids = _record_person_message(cdf_env, "u1", "Prefer one writer for every governed file.")
    unit = _cognition("One writer", "Prefer one writer for every governed file.",
                      [f"event:{event_ids[0]}"])
    report = replay_mod.replay(cdf_env["data"], cdf_env["workspace"], sources=("s2",),
                               max_usd=1.0, llm=Extractor([unit], []), judge_factory=Judge)
    s2 = report["sources"]["s2"]
    assert s2["items"] == 1 and s2["receipts"] >= 1 and s2["errors"] == 0, s2
    assert report["outcomes"]["S2"].get("written", 0) >= 1, report["outcomes"]
    assert report["stopped"] is None and report["cost_usd_estimate"] == 0.0


def test_budget_stops_before_the_next_item(cdf_env, monkeypatch) -> None:
    _session_row(cdf_env["data"], SESSION, datetime.now(timezone.utc))
    import core.ingestion.llm_usage as usage_mod
    monkeypatch.setattr(usage_mod, "totals", lambda: {
        "calls": 10, "input_tokens": 1_000_000, "output_tokens": 0})
    report = replay_mod.replay(cdf_env["data"], cdf_env["workspace"], sources=("s2",),
                               max_usd=2.0, llm=Extractor(), judge_factory=Judge)
    assert report["stopped"].startswith("budget")
    assert report["sources"]["s2"]["receipts"] == 0


def test_main_migrates_the_copy_before_a_real_run(tmp_path: Path, monkeypatch) -> None:
    """The copy comes from the older running daemon: its data.db must get the new
    tables (session_inputs) before S2 reads them, as backend startup would do."""
    calls: list[str] = []
    monkeypatch.setattr(replay_mod, "guard_paths", lambda *a: None)
    monkeypatch.setattr(replay_mod, "migrate_app_db", lambda: calls.append("migrate"))
    monkeypatch.setattr(replay_mod, "replay",
                        lambda *a, **k: calls.append("replay") or {"dry_run": k["dry_run"]})
    args = ["--data-dir", str(tmp_path), "--workspace", str(tmp_path)]
    replay_mod.main(args + ["--max-usd", "1"])
    replay_mod.main(args + ["--dry-run"])
    assert calls == ["migrate", "replay", "replay"]
