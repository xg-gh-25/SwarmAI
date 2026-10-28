"""CDF v2 summary-lane behavior and scheduling tests."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

from core.ingestion import summary_lane
from core.ingestion.summary_lane import append_daily_summary, enqueue_month_summary
from core.ingestion.worker import LANE_WEIGHTS, MAX_WAIT


def _records(path, rows):
    path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n")


def test_daily_summary_once_and_late_addendum(tmp_path):
    day = tmp_path / "2026-10-26.md"
    sidecar = day.with_suffix(".jsonl")
    day.write_text("<!-- da:summary:s1 -->\nblock\n<!-- /da -->\n")
    _records(sidecar, [{
        "session_id": "s1", "summary_sentence": "完成第一项工作。",
        "deliverables": ["交付一"],
        "next_items": [{"next_id": "n1", "text": "继续"}], "resolves": [],
    }])
    assert append_daily_summary(day) == "daily"
    assert append_daily_summary(day) == "unchanged"
    day.write_text(day.read_text() + "<!-- da:summary:s2 -->\nlate\n<!-- /da -->\n")
    _records(sidecar, [
        {"session_id": "s1", "summary_sentence": "完成第一项工作。", "deliverables": [],
         "next_items": [{"next_id": "n1", "text": "继续"}], "resolves": []},
        {"session_id": "s2", "summary_sentence": "完成迟到工作。", "deliverables": ["交付二"],
         "next_items": [], "resolves": ["n1"]},
    ])
    assert append_daily_summary(day) == "supplement"
    assert append_daily_summary(day) == "unchanged"
    text = day.read_text()
    assert text.count("## 日汇总\n") == 1
    assert text.count("## 日汇总（补充）") == 1


def _conn():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE ingestion_inbox(source_ref TEXT, payload TEXT, created_at TEXT)")
    return conn


def test_monthly_summary_enqueued_through_v2_api(tmp_path, monkeypatch):
    conn = _conn()
    captured = []
    monkeypatch.setattr(summary_lane, "_month_anchors", lambda *_: [".context/TIMELINE.md#2026-09#cand:e1"])
    monkeypatch.setattr(
        "core.ingestion.api_v2.enqueue_unit",
        lambda *args, **kwargs: captured.append((args, kwargs)),
    )
    assert enqueue_month_summary(conn, tmp_path, "2026-09") == "month_summary"
    source, source_ref, unit = captured[0][0]
    assert source == "S0" and source_ref == "summary:2026-09"
    assert unit.event_kind == "month_summary"
    assert unit.evidence == ["unit:.context/TIMELINE.md#2026-09#cand:e1"]


def test_summary_lane_scheduler_policy():
    assert LANE_WEIGHTS["summary"] == 2
    assert MAX_WAIT["summary"].total_seconds() == 36 * 60 * 60


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
    monkeypatch.setattr(summary_lane, "_month_anchors", lambda *_: [
        ".context/TIMELINE.md#2026-09#cand:e1",
        ".context/TIMELINE.md#2026-09#cand:e2",
    ])
    captured = []
    monkeypatch.setattr(
        "core.ingestion.api_v2.enqueue_unit",
        lambda *args, **kwargs: captured.append((args, kwargs)),
    )
    assert enqueue_month_summary(conn, tmp_path, "2026-09") == "month_supplement"
    _, source_ref, unit = captured[0][0]
    assert source_ref.startswith("summary:2026-09:supplement:")
    assert unit.event_kind == "month_supplement"
    assert unit.evidence == ["unit:.context/TIMELINE.md#2026-09#cand:e2"]


def test_daily_summary_accepts_llm_capable_synthesizer(tmp_path):
    day = tmp_path / "2026-10-26.md"
    day.write_text("<!-- da:summary:s1 -->\nblock\n<!-- /da -->\n")
    _records(day.with_suffix(".jsonl"), [{"session_id": "s1"}])
    calls = []

    def synth(day_key, records, supplement):
        calls.append((day_key, len(records), supplement))
        return "## 日汇总\n主线：LLM 生成了完整日汇总。\n交付：\n- 无\n未完成 Next：无"

    assert append_daily_summary(day, synth) == "daily"
    assert calls == [("2026-10-26", 1, False)]
    assert "LLM 生成了完整日汇总" in day.read_text()
