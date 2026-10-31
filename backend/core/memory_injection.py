"""Working and episodic memory injection.

- ``select_context_file``: MEMORY.md / EVOLUTION.md whole, every unit (no budget, no
  score: design 2026-10-30-cdf-agent-decides D11).
- ``select_daily_activity`` admits complete session rows of the last 7 days, newest
  first (design docs/design/2026-10-31-daily-activity-useful.md D7).
- ``select_timeline`` admits complete month summaries and event units, counted on the
  text the prompt shows.
- Nothing slices, shortens, or rewrites semantic content.
"""
from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)

from core.context_directory_loader import (
    DAILY_ACTIVITY_INJECTION_BUDGET as DAILY_ACTIVITY_BUDGET,
    TIMELINE_BUDGET_TOKENS as TIMELINE_BUDGET,
)
from core.daily_activity_writer import parse_frontmatter, read_jsonl_sidecar

# Seven days of rows, one per session-day, whole rows newest first; what does not fit
# is named in the suffix. The budgets are the loader's, which reserves the same room
# (EPHEMERAL_HEADROOM): one number, so the reservation and the selection never differ.
SELECTION_VERSION = "cdf-whole-v1"     # the recorded selection: every unit of the file


@dataclass(frozen=True)
class Selection:
    text: str
    omitted: int
    used_tokens: int
    anchors: tuple[str, ...] = ()
    recomputed: bool = False


def _admit(units: list[str], budget: int, estimate: Callable[[str], int]) -> Selection:
    kept: list[str] = []
    used = 0
    omitted = 0
    for unit in units:
        cost = estimate(unit)
        if used + cost <= budget:
            kept.append(unit)
            used += cost
        else:
            omitted += 1
    return Selection("\n".join(kept), omitted, used)


def _recent_days(today: date, count: int = 7) -> list[date]:
    return [today - timedelta(days=offset) for offset in range(count)]


def _legacy_records(path: Path) -> list[dict]:
    """Convert existing markdown session blocks to row data without slicing prose."""
    if not path.is_file():
        return []
    _, body = parse_frontmatter(path.read_text(encoding="utf-8"))
    headers = list(re.finditer(r"^##\s+(\d{2}:\d{2})\s+\|\s+([^|]+)\|\s+(.+)$", body, re.MULTILINE))
    records: list[dict] = []
    for index, match in enumerate(headers):
        end = headers[index + 1].start() if index + 1 < len(headers) else len(body)
        block = body[match.end():end]
        summary_match = re.search(r"^Summary:\s*(.+)$", block, re.MULTILINE)
        sentence = summary_match.group(1).strip() if summary_match else ""
        next_items: list[dict[str, str]] = []
        for next_text in re.findall(r"^\*\*Next:\*\*\s*(.+)$", block, re.MULTILINE):
            material = f"{match.group(2).strip()}\0{next_text}".encode("utf-8")
            next_items.append({"next_id": hashlib.sha256(material).hexdigest()[:12], "text": next_text})
        records.append({
            "session_id": match.group(2).strip(), "timestamp": match.group(1),
            "title": match.group(3).strip(), "summary_sentence": sentence,
            "next_items": next_items, "resolves": [],
        })
    return records


def _upgrade_record(record: dict) -> dict:
    """An old sidecar row with the fields a row needs. Its sentence is its
    deliverables when it has no summary; never its topics (the user's words)."""
    upgraded = dict(record)
    if not upgraded.get("summary_sentence"):
        delivered = [item for item in upgraded.get("deliverables", []) if isinstance(item, str)]
        upgraded["summary_sentence"] = "; ".join(delivered)
    upgraded.setdefault("next_items", [])
    upgraded.setdefault("resolves", [])
    return upgraded


def _row(day: date, record: dict, open_ids: set[str], shown: set[str]) -> str:
    """One session row: date, time, title, what was done, its open follow-ups."""
    title = str(record.get("title") or "Untitled").strip()
    sentence = str(record.get("summary_sentence") or "").strip()
    head = f"- {day.isoformat()} {record.get('timestamp', '')} {title}".rstrip()
    lines = [head + (f": {sentence}" if sentence else "")]
    for item in record.get("next_items", []):
        if not isinstance(item, dict):
            continue
        next_id, text = item.get("next_id"), item.get("text")
        if isinstance(next_id, str) and isinstance(text, str) and next_id in open_ids:
            key = " ".join(text.lower().split())
            if key in shown:
                continue                    # the same follow-up from another session
            shown.add(key)
            lines.append(f"  - Next [{next_id}]: {text}")
    return "\n".join(lines)


def select_daily_activity(
    workspace: Path,
    *,
    today: date,
    estimate: Callable[[str], int],
    budget: int = DAILY_ACTIVITY_BUDGET,
) -> Selection:
    """Session rows of the last 7 days as whole units: days newest first, rows newest
    first within a day. A row shows the follow-ups still open by ``open_next_items_in``
    (its session's latest row, not resolved), each text once."""
    directory = Path(workspace) / "Knowledge" / "DailyActivity"
    days = _recent_days(today)
    records_by_day = {}
    for day in days:
        records = read_jsonl_sidecar(directory / f"{day.isoformat()}.jsonl")
        raw_records = records or _legacy_records(directory / f"{day.isoformat()}.md")
        records_by_day[day] = [_upgrade_record(record) for record in raw_records]
    from core.daily_activity_writer import open_next_items_in
    open_ids = {item["next_id"] for item in open_next_items_in(directory, days=7, until=today)}
    shown: set[str] = set()
    units = [
        _row(day, record, open_ids, shown)
        for day in days
        for record in sorted(records_by_day[day], key=lambda r: str(r.get("timestamp") or ""),
                             reverse=True)
    ]
    selected = _admit(units, budget, estimate)
    if selected.omitted:
        suffix = (f"({selected.omitted} more sessions not shown; "
                  "they are in Knowledge/DailyActivity/)")
        return Selection(
            selected.text + ("\n" if selected.text else "") + suffix,
            selected.omitted,
            selected.used_tokens,
        )
    return selected


_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)


def _shown(text: str) -> str:
    """*text* as the prompt shows it: metadata comments and the blank lines they
    leave removed."""
    cleaned = _HTML_COMMENT_RE.sub("", text)
    return "\n".join(line for line in cleaned.splitlines() if line.strip()).strip()


def _utc_datetime(value: datetime | date | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return datetime.combine(value, datetime.min.time(), tzinfo=timezone.utc)


def _prompt_form(text: str) -> str:
    """Return the bytes the loader would expose after removing private metadata."""
    cleaned = _HTML_COMMENT_RE.sub("", text).strip()
    lines = cleaned.split("\n", 1)
    if lines and lines[0].strip().startswith("# ") and not lines[0].strip().startswith("## "):
        cleaned = lines[1].strip() if len(lines) > 1 else ""
    return cleaned


def select_context_file(
    path: Path,
    *,
    estimate: Callable[[str], int],
    now: datetime | date | None = None,
    conn: Any = None,
) -> Selection:
    """MEMORY.md / EVOLUTION.md as the prompt carries them: the whole file, every unit
    (design 2026-10-30-cdf-agent-decides D11: value first; what is in a main file is the
    valid set, kept lean by the agent's same / adds / overrides declarations, so nothing
    is chosen by a score or cut to a budget). Only the private metadata comments are
    removed. Which units were carried is recorded once per file version (recall's
    "outside the prompt" leg reads it)."""
    path = Path(path)
    if path.name not in {"MEMORY.md", "EVOLUTION.md"} or not path.is_file():
        return Selection("", 0, 0)
    raw = path.read_bytes()
    text = _prompt_form(raw.decode("utf-8"))
    file_hash = hashlib.sha256(raw).hexdigest()
    file_path = str(path.resolve(strict=False))
    from core.ingestion import standing
    owned = conn is None
    if owned:
        conn = standing.connect(write=True)
    try:
        cached = standing.latest_selection(conn, file_path) if conn is not None else None
        if cached is not None and cached.file_hash == file_hash and cached.version == SELECTION_VERSION:
            return Selection(text, 0, estimate(text), cached.anchors, False)
        from core.ingestion.units import parse_units
        anchors = tuple(unit.anchor for unit in parse_units(path, root=path.parent.parent))
        if conn is not None:
            standing.record_selection(conn, file_path=file_path, file_hash=file_hash, anchors=anchors,
                                      version=SELECTION_VERSION,
                                      computed_at=_utc_datetime(now).isoformat(), omitted=0)
        return Selection(text, 0, estimate(text), anchors, True)
    finally:
        if owned and conn is not None:
            conn.close()


def recorded_selection(path: Path, *, conn: Any = None) -> tuple[str, ...] | None:
    """Return the latest anchors for the current file hash, without creating a DB."""
    from core.ingestion import standing
    path = Path(path)
    if not path.is_file():
        return None
    owned = conn is None
    if owned:
        conn = standing.connect()
        if conn is None:
            return None
    try:
        row = standing.latest_selection(conn, str(path.resolve(strict=False)))
        if row is None or row.file_hash != hashlib.sha256(path.read_bytes()).hexdigest():
            return None
        return row.anchors
    except OSError:
        return None
    finally:
        if owned and conn is not None:
            conn.close()


def _previous_month_key(month: str) -> str:
    first = date.fromisoformat(month + "-01")
    return (first - timedelta(days=1)).strftime("%Y-%m")


def _is_month_summary(text: str) -> bool:
    return "month_summary" in text or "month_supplement" in text


def select_timeline(
    timeline: Path,
    *,
    today: date,
    estimate: Callable[[str], int],
    budget: int = TIMELINE_BUDGET,
) -> Selection:
    """Select month summaries first, then newest events, never partial units."""
    timeline = Path(timeline)
    if not timeline.is_file():
        return Selection("", 0, 0)
    from core.ingestion.units import parse_units

    units = [unit for unit in parse_units(timeline, root=timeline.parent.parent)
             if unit.section != "Not injected"]
    current = today.strftime("%Y-%m")
    previous = _previous_month_key(current)
    summaries = [
        unit for month in (current, previous) for unit in units
        if unit.section == month and _is_month_summary(unit.text)
    ]
    summary_identities = {unit.identity for unit in summaries}
    events = [unit for unit in reversed(units) if unit.identity not in summary_identities]
    # counted on what the prompt shows: the loader strips the metadata comments
    ordered = [_shown(unit.text) for unit in [*summaries, *events]]
    selected = _admit([text for text in ordered if text], budget, estimate)
    if selected.omitted:
        suffix = (f"({selected.omitted} more events not shown; "
                  "read .context/TIMELINE.md)")
        return Selection(
            selected.text + ("\n" if selected.text else "") + suffix,
            selected.omitted,
            selected.used_tokens,
        )
    return selected
