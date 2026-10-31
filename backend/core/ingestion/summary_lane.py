"""CDF v2 summary lane for TIMELINE.

- ``SummaryLane.run_due`` performs the monthly summary work.
- ``enqueue_month_summary`` submits S0 EventUnits through the frozen v2 API.

DailyActivity has no day summary: its rows are already one line per session, and the
injection shows them for all seven days (design 2026-10-31-daily-activity-useful D7).
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path


from .contracts_v2 import EventUnit

def _month_anchors(timeline: Path, month: str) -> list[str]:
    if not timeline.is_file():
        return []
    from .units import parse_units

    return [
        unit.anchor for unit in parse_units(timeline, root=timeline.parent.parent)
        if unit.section == month and "month_summary" not in unit.text
        and "month_supplement" not in unit.text
    ]


def _previous_month(today: date) -> str:
    return (today.replace(day=1) - timedelta(days=1)).strftime("%Y-%m")


def _existing_summary_evidence(conn: sqlite3.Connection, source_ref: str) -> set[str]:
    row = conn.execute(
        "SELECT payload FROM ingestion_inbox WHERE source_ref=? ORDER BY created_at LIMIT 1",
        (source_ref,),
    ).fetchone()
    if not row:
        return set()
    raw = row[0] if not isinstance(row, sqlite3.Row) else row["payload"]
    try:
        return set(json.loads(raw).get("evidence", []))
    except (ValueError, AttributeError):
        return set()


def enqueue_month_summary(conn: sqlite3.Connection, workspace: Path, month: str) -> str:
    """Enqueue one month summary, or a supplement for newly arrived events."""
    timeline = Path(workspace) / ".context" / "TIMELINE.md"
    anchors = _month_anchors(timeline, month)
    if not anchors:
        return "unchanged"
    source_ref = f"summary:{month}"
    previous = _existing_summary_evidence(conn, source_ref)
    fresh = [anchor for anchor in anchors if f"unit:{anchor}" not in previous]
    event_kind = "month_summary"
    if previous:
        if not fresh:
            return "unchanged"
        event_kind = "month_supplement"
        digest = hashlib.sha256("\n".join(fresh).encode("utf-8")).hexdigest()[:12]
        source_ref = f"summary:{month}:supplement:{digest}"
        anchors = fresh
    evidence = [f"unit:{anchor}" for anchor in anchors]
    unit = EventUnit(
        when=f"{month}-01",
        title=f"{month} 月度关键事件汇总",
        what=f"汇总 {month} 的关键事件，覆盖 {len(anchors)} 个可追溯事件锚点。",
        impact="为 Retro 提供按月的事件入口，并保留到原始事件的完整引用。",
        event_kind=event_kind,
        evidence=evidence,
    )
    from . import api_v2

    api_v2.enqueue_stored(
        "S0", source_ref, unit, session_id=None, run_id=None,
        read_root_ids=(), project_snapshot={}, extraction_pass="main",
    )
    return event_kind


class SummaryLane:
    """Daily scheduler target with bounded failure escalation."""

    def __init__(self, workspace: Path) -> None:
        self.workspace = Path(workspace)
        self.consecutive_failures = 0

    def run_due(self, conn: sqlite3.Connection, *, now: datetime | None = None) -> bool:
        """Summarize the previous month; return whether work changed."""
        moment = now or datetime.now(timezone.utc)
        changed = False
        try:
            changed |= enqueue_month_summary(
                conn, self.workspace, _previous_month(moment.date())
            ) != "unchanged"
            self.consecutive_failures = 0
            return changed
        except Exception as exc:
            self.consecutive_failures += 1
            if self.consecutive_failures >= 3:
                conn.execute(
                    "INSERT INTO ingestion_events(at, kind, detail) VALUES (?, 'summary_failed', ?)",
                    (moment.isoformat(), json.dumps({
                        "failures": self.consecutive_failures,
                        "error": f"{type(exc).__name__}: {exc}",
                    }, ensure_ascii=False)),
                )
            raise
