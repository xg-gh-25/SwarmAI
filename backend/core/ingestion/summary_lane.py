"""CDF v2 summary lane for DailyActivity and TIMELINE.

- ``SummaryLane.run_due`` performs daily and monthly summary work.
- ``append_daily_summary`` appends idempotent day summaries and late addenda.
- ``enqueue_month_summary`` submits S0 EventUnits through the frozen v2 API.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from core.daily_activity_writer import append_daily_text, read_jsonl_sidecar
from .contracts_v2 import EventUnit

_DAILY = "<!-- daily-summary:{day} -->"
_SUPPLEMENT = "<!-- daily-summary-supplement:{day}:{digest} -->"


def _session_ids_after(text: str, marker: str) -> set[str]:
    tail = text.split(marker, 1)[1] if marker in text else text
    ids: set[str] = set()
    for line in tail.splitlines():
        if line.startswith("<!-- da:summary:") and line.endswith(" -->"):
            ids.add(line.removeprefix("<!-- da:summary:").removesuffix(" -->"))
    return ids


def _open_next(records: list[dict]) -> list[str]:
    resolved = {item for row in records for item in row.get("resolves", []) if isinstance(item, str)}
    return [
        item["next_id"]
        for row in records
        for item in row.get("next_items", [])
        if isinstance(item, dict) and isinstance(item.get("next_id"), str)
        and item["next_id"] not in resolved
    ]


def _daily_block(day: str, records: list[dict], *, supplement: bool = False) -> str:
    heading = "## 日汇总（补充）" if supplement else "## 日汇总"
    main = "; ".join(row.get("summary_sentence", "") for row in records if row.get("summary_sentence"))
    deliverables = [item for row in records for item in row.get("deliverables", []) if isinstance(item, str)]
    lines = [heading, f"主线：{main or '当日没有可汇总的会话。'}", "交付："]
    lines.extend(f"- {item}" for item in deliverables)
    if not deliverables:
        lines.append("- 无")
    ids = _open_next(records)
    lines.append("未完成 Next：" + (", ".join(ids) if ids else "无"))
    return "\n".join(lines)


def append_daily_summary(day_file: Path, synthesizer: Callable | None = None) -> str:
    """Append the first day summary or one content-addressed late addendum."""
    day_file = Path(day_file)
    day = day_file.stem
    sidecar = day_file.with_suffix(".jsonl")
    records = read_jsonl_sidecar(sidecar)
    text = day_file.read_text(encoding="utf-8") if day_file.exists() else ""
    marker = _DAILY.format(day=day)
    if marker not in text:
        block = synthesizer(day, records, False) if synthesizer else _daily_block(day, records)
        append_daily_text(day_file, f"\n{marker}\n{block}\n")
        return "daily"

    late_ids = _session_ids_after(text, marker)
    late = [row for row in records if row.get("session_id") in late_ids]
    if not late:
        return "unchanged"
    material = json.dumps(late, ensure_ascii=False, sort_keys=True).encode("utf-8")
    digest = hashlib.sha256(material).hexdigest()[:12]
    supplement_marker = _SUPPLEMENT.format(day=day, digest=digest)
    if supplement_marker in text:
        return "unchanged"
    block = synthesizer(day, late, True) if synthesizer else _daily_block(day, late, supplement=True)
    append_daily_text(
        day_file,
        f"\n{supplement_marker}\n{block}\n",
    )
    return "supplement"


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

    api_v2.enqueue_unit(
        "S0", source_ref, unit, session_id=None, run_id=None,
        read_root_ids=(), project_snapshot={}, extraction_pass="main",
    )
    return event_kind


class SummaryLane:
    """Daily scheduler target with bounded failure escalation."""

    def __init__(self, workspace: Path, synthesizer: Callable | None = None) -> None:
        self.workspace = Path(workspace)
        self.synthesizer = synthesizer
        self.consecutive_failures = 0

    def run_due(self, conn: sqlite3.Connection, *, now: datetime | None = None) -> bool:
        """Summarize yesterday and the previous month; return whether work changed."""
        moment = now or datetime.now(timezone.utc)
        changed = False
        try:
            yesterday = moment.date() - timedelta(days=1)
            day_file = self.workspace / "Knowledge" / "DailyActivity" / f"{yesterday}.md"
            if day_file.is_file():
                changed |= append_daily_summary(day_file, self.synthesizer) != "unchanged"
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
