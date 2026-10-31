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
    SESSION, Extractor, _cognition, _record_person_message, cdf_env,
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
    assert "s5" not in report["sources"]                  # governance is read, not replayed
    assert report["sources"]["s6"]["items"] == 0          # no signal digest in the copy
    assert report["usage"]["calls"] == 0 and "outcomes" not in report


def test_replay_runs_one_session_end_to_end(cdf_env) -> None:
    _session_row(cdf_env["data"], SESSION, datetime.now(timezone.utc))
    event_ids = _record_person_message(cdf_env, "u1", "Prefer one writer for every governed file.")
    unit = _cognition("One writer", "Prefer one writer for every governed file.",
                      [f"event:{event_ids[0]}"])
    unit["user_quote"] = "Prefer one writer for every governed file."
    report = replay_mod.replay(cdf_env["data"], cdf_env["workspace"], sources=("s2",),
                               max_usd=1.0, llm=lambda *a, **k: {"cognition_units": [unit]})
    s2 = report["sources"]["s2"]
    assert s2["items"] == 1 and s2["receipts"] >= 1 and s2["errors"] == 0, s2
    assert report["outcomes"]["S7"].get("written", 0) >= 1, report["outcomes"]
    assert report["stopped"] is None and report["cost_usd_estimate"] == 0.0


def test_budget_counts_this_run_only(cdf_env, monkeypatch) -> None:
    """The copy already holds the live daemon's usage (50M tokens here): the cap applies
    to what this run spends, and the run stops before the next item once it is spent."""
    now = datetime.now(timezone.utc)
    _session_row(cdf_env["data"], SESSION, now - timedelta(hours=2))
    _session_row(cdf_env["data"], "second-session", now - timedelta(hours=1))
    import core.ingestion.llm_usage as usage_mod
    spent = {"tokens": 0}
    monkeypatch.setattr(usage_mod, "totals", lambda: {
        "calls": 1, "input_tokens": 50_000_000 + spent["tokens"], "output_tokens": 0})

    ran = []

    def costly(source, item, workspace, llm):
        ran.append(item)
        spent["tokens"] += 1_000_000              # $3 at the default price
        return 1

    monkeypatch.setattr(replay_mod, "_enqueue", costly)
    report = replay_mod.replay(cdf_env["data"], cdf_env["workspace"], sources=("s2",),
                               max_usd=2.0, llm=Extractor())
    assert ran == [SESSION]                                   # the first item ran
    assert report["stopped"].startswith("budget")             # the second did not
    assert report["usage_in_copy_before_run"]["input_tokens"] == 50_000_000
    assert report["usage"]["input_tokens"] == spent["tokens"]


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


def test_s2_sample_spreads_over_size_smallest_first(tmp_path: Path) -> None:
    db = sqlite3.connect(tmp_path / "data.db")
    db.execute("CREATE TABLE messages (session_id TEXT, content TEXT)")
    for i in range(10):
        db.execute("INSERT INTO messages VALUES (?,?)", (f"s{i}", "x" * (100 * (10 - i))))
    db.commit()
    db.close()
    sessions = [f"s{i}" for i in range(10)]
    picked = replay_mod.sample_by_size(tmp_path, sessions, 4)
    assert picked == ["s9", "s6", "s3", "s0"]          # smallest .. largest, both ends kept
    assert replay_mod.sample_by_size(tmp_path, sessions, 0) == sorted(
        sessions, key=lambda s: -int(s[1:]))            # all, smallest first


def test_backfill_records_old_user_messages_once(cdf_env) -> None:
    """A session sent before session_inputs existed gets the user events the send path
    records today (ref = message id, quotes split out as external), exactly once."""
    data = cdf_env["data"]
    with sqlite3.connect(data / "data.db") as conn:
        conn.execute("INSERT INTO messages(id, session_id, role, content, created_at, updated_at) VALUES "
                     "('old-1', ?, 'user', ?, '2026-10-20T00:00:00', '2026-10-20T00:00:00')",
                     (SESSION, '[{"type": "text", "text": "Always run the linter first.\\n> pasted"}]'))
        conn.execute("INSERT INTO messages(id, session_id, role, content, created_at, updated_at) VALUES "
                     "('old-2', ?, 'assistant', 'ok', '2026-10-20T00:00:01', '2026-10-20T00:00:01')", (SESSION,))
    assert replay_mod.backfill_user_inputs(data, SESSION) == 1
    with sqlite3.connect(data / "data.db") as conn:
        rows = conn.execute("SELECT kind, trust, ref FROM session_inputs WHERE session_id=? "
                            "ORDER BY kind", (SESSION,)).fetchall()
    assert ("user_message", "user", "old-1") in rows
    assert all(ref.startswith("old-1") for _, _, ref in rows)
    assert replay_mod.backfill_user_inputs(data, SESSION) == 0
