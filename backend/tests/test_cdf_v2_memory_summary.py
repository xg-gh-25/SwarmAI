"""CDF v2 summary-lane behavior and scheduling tests."""
from __future__ import annotations


def _queued():
    from core.ingestion.api import EnqueueResult
    return EnqueueResult("queued", "cand-x")

import json
import sqlite3
from datetime import datetime, timezone

from core.ingestion import summary_lane
from core.ingestion.summary_lane import enqueue_month_summary
from core.ingestion.worker import LANE_WEIGHTS, MAX_WAIT


def _records(path, rows):
    path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n")


def test_the_lane_writes_no_day_summary_into_dailyactivity(tmp_path, monkeypatch):
    # its rows are already one line per session; a day summary concatenated them
    day = tmp_path / "Knowledge" / "DailyActivity" / "2026-10-26.md"
    day.parent.mkdir(parents=True)
    day.write_text("<!-- da:summary:s1 -->\nblock\n<!-- /da -->\n")
    _records(day.with_suffix(".jsonl"), [{"session_id": "s1", "summary_sentence": "x"}])
    before = day.read_text()
    monkeypatch.setattr(summary_lane, "enqueue_month_summary", lambda *_: "unchanged")
    summary_lane.SummaryLane(tmp_path).run_due(_conn(), now=datetime(2026, 10, 27, tzinfo=timezone.utc))
    assert day.read_text() == before


def _conn():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE ingestion_inbox(source_ref TEXT, payload TEXT, created_at TEXT)")
    return conn


def test_monthly_summary_enqueued_through_v2_api(tmp_path, monkeypatch):
    conn = _conn()
    captured = []
    monkeypatch.setattr(summary_lane, "_month_units", lambda *_: [
        (".context/TIMELINE.md#2026-09#cand:e1", "### 2026-09-03 Decided one writer")])
    monkeypatch.setattr(
        "core.ingestion.api_v2.enqueue_unit",
        lambda *args, **kwargs: captured.append((args, kwargs)) or _queued(),
    )
    writer = lambda month, kind, events: "- Decided one writer."  # noqa: E731
    assert enqueue_month_summary(conn, tmp_path, "2026-09", writer=writer) == "month_summary"
    source, source_ref, unit = captured[0][0]
    assert source == "S0" and source_ref == "summary:2026-09"
    assert unit.event_kind == "month_summary"
    assert unit.evidence == ["unit:.context/TIMELINE.md#2026-09#cand:e1"]


def test_summary_lane_scheduler_policy():
    # month summaries are stored in the scheduled lane: the inbox admits no lane of its own
    from core.ingestion.contracts_v2 import SOURCES_V2
    assert SOURCES_V2["S0"]["lane"] == "scheduled"
    assert "summary" not in LANE_WEIGHTS and "summary" not in MAX_WAIT
    assert MAX_WAIT["scheduled"].total_seconds() == 12 * 60 * 60


def test_three_failures_raise_need_you_event(tmp_path, monkeypatch):
    conn = _conn()
    conn.execute("CREATE TABLE ingestion_events(at TEXT, kind TEXT, detail TEXT)")
    lane = summary_lane.SummaryLane(tmp_path)
    monkeypatch.setattr(summary_lane, "enqueue_month_summary", lambda *_: (_ for _ in ()).throw(RuntimeError("down")))
    now = datetime(2026, 10, 27, tzinfo=timezone.utc)
    for _ in range(3):
        try:
            lane.run_due(conn, now=now)
        except RuntimeError:
            pass
    row = conn.execute("SELECT kind, detail FROM ingestion_events").fetchone()
    assert row[0] == "summary_failed"
    assert json.loads(row[1])["failures"] == 3


def test_late_month_event_enqueues_supplement(tmp_path, monkeypatch):
    conn = _conn()
    conn.execute(
        "INSERT INTO ingestion_inbox VALUES (?, ?, ?)",
        ("summary:2026-09", json.dumps({
            "evidence": ["unit:.context/TIMELINE.md#2026-09#cand:e1"]
        }), "2026-10-01T00:00:00+00:00"),
    )
    monkeypatch.setattr(summary_lane, "_month_units", lambda *_: [
        (".context/TIMELINE.md#2026-09#cand:e1", "### 2026-09-03 first"),
        (".context/TIMELINE.md#2026-09#cand:e2", "### 2026-09-28 later"),
    ])
    captured = []
    monkeypatch.setattr(
        "core.ingestion.api_v2.enqueue_unit",
        lambda *args, **kwargs: captured.append((args, kwargs)) or _queued(),
    )
    seen = []
    writer = lambda month, kind, events: seen.append(events) or "- A later decision."  # noqa: E731
    assert enqueue_month_summary(conn, tmp_path, "2026-09", writer=writer) == "month_supplement"
    assert seen == [["### 2026-09-28 later"]]   # only what arrived after the summary
    _, source_ref, unit = captured[0][0]
    assert source_ref.startswith("summary:2026-09:supplement:")
    assert unit.event_kind == "month_supplement"
    assert unit.evidence == ["unit:.context/TIMELINE.md#2026-09#cand:e2"]


def test_an_unreachable_model_is_not_a_counted_failure(tmp_path, monkeypatch):
    """A night without credentials or DNS used to count three failures, raise summary_failed
    (a Need You item) and push the next try a day out. Neither credentials nor an
    unreachable endpoint says anything about the summary: not counted, nothing raised."""
    from botocore.exceptions import EndpointConnectionError, NoCredentialsError
    conn = _conn()
    conn.execute("CREATE TABLE ingestion_events(at TEXT, kind TEXT, detail TEXT)")
    lane = summary_lane.SummaryLane(tmp_path)
    now = datetime(2026, 10, 27, tzinfo=timezone.utc)
    for err in (NoCredentialsError(), EndpointConnectionError(endpoint_url="https://bedrock")):
        def fail(*_a, err=err, **_k):
            try:
                raise err
            except Exception as exc:  # wrapped as enqueue_month_summary wraps a writer failure
                raise summary_lane.SummaryNotWritten(f"writer failed: {exc}") from exc
        monkeypatch.setattr(summary_lane, "enqueue_month_summary", fail)
        for _ in range(4):
            try:
                lane.run_due(conn, now=now)
            except summary_lane.SummaryNotWritten:
                pass
    assert lane.consecutive_failures == 0
    assert conn.execute("SELECT COUNT(*) FROM ingestion_events").fetchone()[0] == 0
