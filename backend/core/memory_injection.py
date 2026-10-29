"""Whole-unit working and episodic memory injection selectors.

- ``select_daily_activity`` admits complete session rows/day summaries.
- ``select_timeline`` admits complete month summaries and event units.
- No selector slices, shortens, or rewrites semantic content.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import re
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

logger = logging.getLogger(__name__)

from core.daily_activity_writer import parse_frontmatter, read_jsonl_sidecar

DAILY_ACTIVITY_BUDGET = 4000
TIMELINE_BUDGET = 1500
MEMORY_INJECTION_BUDGET = 24_000
EVOLUTION_INJECTION_BUDGET = 8_000
SCORE_SNAPSHOT_VERSION = "cdf-launch-v2"
DECAY_HALF_LIFE_DAYS = 90


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


def _summary_blocks(path: Path) -> str:
    if not path.is_file():
        return ""
    _, body = parse_frontmatter(path.read_text(encoding="utf-8"))
    starts = list(re.finditer(r"^## 日汇总(?:（补充）)?\s*$", body, re.MULTILINE))
    blocks: list[str] = []
    for index, match in enumerate(starts):
        end = starts[index + 1].start() if index + 1 < len(starts) else len(body)
        block = body[match.start():end].strip()
        if block:
            blocks.append(block)
    return "\n\n".join(blocks)



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
        bullets = [item.strip() for item in re.findall(r"^-\s+(.+)$", block, re.MULTILINE)]
        sentence = summary_match.group(1).strip() if summary_match else (bullets[0] if bullets else match.group(3).strip())
        next_items: list[dict[str, str]] = []
        for next_text in re.findall(r"^\*\*Next:\*\*\s*(.+)$", block, re.MULTILINE):
            material = f"{match.group(2).strip()}\0{next_text}".encode("utf-8")
            import hashlib
            next_items.append({"next_id": hashlib.sha256(material).hexdigest()[:12], "text": next_text})
        records.append({
            "session_id": match.group(2).strip(), "timestamp": match.group(1),
            "title": match.group(3).strip(), "summary_sentence": sentence,
            "next_items": next_items, "resolves": [],
        })
    return records


def _upgrade_record(record: dict) -> dict:
    """Return an old sidecar record with deterministic v2 row fields added."""
    upgraded = dict(record)
    if not upgraded.get("summary_sentence"):
        delivered = [item for item in upgraded.get("deliverables", []) if isinstance(item, str)]
        topics = [item for item in upgraded.get("topics", []) if isinstance(item, str)]
        source = delivered or topics
        upgraded["summary_sentence"] = "; ".join(source) if source else str(upgraded.get("title") or "Session activity recorded.")
    if not upgraded.get("next_items") and isinstance(upgraded.get("continue_from"), str) and upgraded["continue_from"].strip():
        import hashlib
        text = upgraded["continue_from"].strip()
        material = f"{upgraded.get('session_id', '')}\0{text}".encode("utf-8")
        upgraded["next_items"] = [{
            "next_id": hashlib.sha256(material).hexdigest()[:12], "text": text,
        }]
    upgraded.setdefault("next_items", [])
    upgraded.setdefault("resolves", [])
    return upgraded

def _synthesized_day_summary(records: list[dict]) -> str:
    """Build one complete legacy day-summary block without an LLM or slicing."""
    if not records:
        return ""
    main = "; ".join(record.get("summary_sentence", "") for record in records if record.get("summary_sentence"))
    delivered = [item for record in records for item in record.get("deliverables", []) if isinstance(item, str)]
    next_ids = [
        item["next_id"] for record in records for item in record.get("next_items", [])
        if isinstance(item, dict) and isinstance(item.get("next_id"), str)
    ]
    lines = ["## 日汇总", f"主线：{main}", "交付："]
    lines.extend(f"- {item}" for item in delivered)
    if not delivered:
        lines.append("- 无")
    lines.append("未完成 Next：" + (", ".join(next_ids) if next_ids else "无"))
    return "\n".join(lines)


def select_daily_activity(
    workspace: Path,
    *,
    today: date,
    estimate: Callable[[str], int],
    budget: int = DAILY_ACTIVITY_BUDGET,
) -> Selection:
    """Select today/yesterday session rows and day 3–7 summaries as whole units."""
    directory = Path(workspace) / "Knowledge" / "DailyActivity"
    days = _recent_days(today)
    records_by_day = {}
    for day in days:
        records = read_jsonl_sidecar(directory / f"{day.isoformat()}.jsonl")
        raw_records = records or _legacy_records(directory / f"{day.isoformat()}.md")
        records_by_day[day] = [_upgrade_record(record) for record in raw_records]
    resolved = {
        next_id
        for records in records_by_day.values()
        for record in records
        for next_id in record.get("resolves", [])
        if isinstance(next_id, str)
    }
    units: list[str] = []
    for day in days[:2]:
        for record in reversed(records_by_day[day]):
            open_items = [
                item for item in record.get("next_items", [])
                if isinstance(item, dict) and item.get("next_id") not in resolved
            ]
            next_text = "; ".join(
                f"[{item['next_id']}] {item['text']}" for item in open_items
                if isinstance(item.get("next_id"), str) and isinstance(item.get("text"), str)
            )
            units.append(
                f"- {day.isoformat()} {record.get('timestamp', '')} | "
                f"{record.get('title', 'Untitled')} | {record.get('summary_sentence', '')} | "
                f"Next: {next_text or 'None'}"
            )
    for day in days[2:]:
        block = _summary_blocks(directory / f"{day.isoformat()}.md")
        if not block:
            block = _synthesized_day_summary(records_by_day[day])
        if block:
            units.append(f"### {day.isoformat()}\n{block}")
    selected = _admit(units, budget, estimate)
    if selected.omitted:
        suffix = f"（另有 {selected.omitted} 个 session 未注入，可用 recall 查看）"
        return Selection(
            selected.text + ("\n" if selected.text else "") + suffix,
            selected.omitted,
            selected.used_tokens,
        )
    return selected


_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
_EVOLUTION_LAYERS = {
    "Design Philosophy — What \"Evolution\" Means": "meta-cognitive",
    "Corrections Captured": "meta-cognitive",
    "Competence Learned": "meta-cognitive",
    "Optimizations Learned": "cognitive",
    "Governance Candidates": "cognitive",
    "Failed Evolutions": "cognitive",
    "Capabilities Built": "operational",
}


@dataclass(frozen=True)
class _RankedUnit:
    unit: object
    layer: str
    score: float
    evidence_strength: int
    evidence_at: datetime


def _utc_datetime(value: datetime | date | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return datetime.combine(value, datetime.min.time(), tzinfo=timezone.utc)


def _parse_time(value: object) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.astimezone(timezone.utc) if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        try:
            return datetime.combine(date.fromisoformat(str(value)), datetime.min.time(), tzinfo=timezone.utc)
        except (TypeError, ValueError):
            return None


def _prompt_form(text: str) -> str:
    """Return the bytes the loader would expose after removing private metadata."""
    cleaned = _HTML_COMMENT_RE.sub("", text).strip()
    lines = cleaned.split("\n", 1)
    if lines and lines[0].strip().startswith("# ") and not lines[0].strip().startswith("## "):
        cleaned = lines[1].strip() if len(lines) > 1 else ""
    return cleaned


def _filter_inactive_memory(text: str) -> str:
    """Remove complete dormant/archived entry spans before prompt rendering."""
    from core.ddd_entry_lifecycle import parse_entries

    lines = text.splitlines()
    removed: set[int] = set()
    for entry in parse_entries(text, skip_h3_spans=False):
        if entry.decay_state in {"dormant", "archived"}:
            removed.update(range(entry.line_number, entry.end_line + 1))
    return "\n".join(line for index, line in enumerate(lines) if index not in removed)


def _memory_metadata(path: Path) -> dict[tuple[str, str, str], list[object]]:
    if path.name != "MEMORY.md":
        return {}
    from core.ddd_entry_lifecycle import parse_entries

    result: dict[tuple[str, str, str], list[object]] = {}
    for entry in parse_entries(path.read_text(encoding="utf-8"), skip_h3_spans=False):
        result.setdefault((entry.section, entry.title, entry.raw_text), []).append(entry)
    return result


def _unit_layer(path: Path, unit: object) -> str:
    if path.name == "MEMORY.md":
        from core.ddd_entry_lifecycle import MEMORY_SECTIONS
        return str(MEMORY_SECTIONS.get(unit.section, {}).get("layer", "operational"))
    return _EVOLUTION_LAYERS.get(unit.section, "operational")


def _root_facts(conn: sqlite3.Connection, anchor: str) -> tuple[float, float, int, datetime | None]:
    """Return support, refute, evidence strength, and newest evidence time."""
    rows = conn.execute(
        """SELECT cs.weight,cs.polarity,r.trust,r.root_kind,r.observed_at
           FROM claim_support cs JOIN evidence_roots r ON r.root_id=cs.root_id
           WHERE cs.claim_anchor=? AND cs.inactive=0""",
        (anchor,),
    ).fetchall()
    identity = anchor.rsplit("#", 1)[-1]
    if identity.startswith("cand:"):
        unit_id = identity[5:]
        initial = conn.execute(
            """SELECT i.evidence_root_ids FROM ingestion_units u
               JOIN ingestion_inbox i ON i.id=u.candidate_id WHERE u.unit_id=?""",
            (unit_id,),
        ).fetchone()
        if initial:
            try:
                root_ids = [str(item) for item in json.loads(str(initial[0]) or "[]")]
            except (TypeError, ValueError, json.JSONDecodeError):
                root_ids = []
            if root_ids:
                marks = ",".join("?" for _ in root_ids)
                rows = [*rows, *conn.execute(
                    f"SELECT 0.0,'origin',trust,root_kind,observed_at FROM evidence_roots "
                    f"WHERE root_id IN ({marks})", root_ids,
                ).fetchall()]
    support = sum(float(row[0]) for row in rows if str(row[1]) in {"support", "origin"})
    refute = sum(float(row[0]) for row in rows if str(row[1]) == "refute")
    strength = 0
    observed: list[datetime] = []
    for row in rows:
        trust, kind = str(row[2]), str(row[3])
        strength = max(strength, 3 if trust == "user" else 2 if trust == "measured" else
                       1 if kind.startswith("run_") else 0)
        parsed = _parse_time(row[4])
        if parsed:
            observed.append(parsed)
    return support, refute, strength, max(observed) if observed else None


def _score_unit(conn: sqlite3.Connection | None, path: Path, unit: object,
                layer: str, current: datetime, metadata: object | None) -> _RankedUnit:
    support = refute = 0.0
    strength = 0
    evidence_at: datetime | None = None
    usage_count = 0
    last_used: datetime | None = None
    written_at: datetime | None = None
    if conn is not None:
        try:
            support, refute, strength, evidence_at = _root_facts(conn, unit.anchor)
            usage = conn.execute(
                "SELECT COUNT(*),MAX(at) FROM unit_usage WHERE anchor=?", (unit.anchor,),
            ).fetchone()
            if usage:
                usage_count = int(usage[0] or 0)
                last_used = _parse_time(usage[1])
            identity = unit.anchor.rsplit("#", 1)[-1]
            if identity.startswith("cand:"):
                row = conn.execute(
                    "SELECT updated_at FROM ingestion_writes WHERE unit_id=? AND step='done' "
                    "ORDER BY updated_at DESC LIMIT 1", (identity[5:],),
                ).fetchone()
                written_at = _parse_time(row[0]) if row else None
        except sqlite3.Error:
            logger.debug("CDF injection score lookup failed for %s", unit.anchor, exc_info=True)
    metadata_times = []
    if metadata is not None:
        metadata_times = [_parse_time(getattr(metadata, name, None))
                          for name in ("last_referenced", "created_date")]
    timestamps = [item for item in (evidence_at, last_used, written_at, *metadata_times) if item]
    newest = max(timestamps) if timestamps else datetime(1970, 1, 1, tzinfo=timezone.utc)
    age_days = max(0, (current.date() - newest.date()).days)
    # Value decides, age only slowly: halves every DECAY_HALF_LIFE_DAYS (meta-cognition
    # does not age), while each use keeps counting. 1/(1+age) let a 3-day-old entry
    # outrank a 30-day-old one with full support and use.
    decay = 1.0 if layer == "meta-cognitive" else 0.5 ** (age_days / DECAY_HALF_LIFE_DAYS)
    support_factor = (1.0 + support / (1.0 + support)) / (1.0 + refute)
    trust_factor = {3: 1.0, 2: 0.8, 1: 0.5, 0: 0.3}[strength] if timestamps else 1.0
    usage_factor = 1.0 + math.log1p(usage_count)
    score = decay * support_factor * trust_factor * usage_factor
    return _RankedUnit(unit, layer, score, strength, evidence_at or newest)


def _superseded_anchors(conn: sqlite3.Connection | None) -> set[str]:
    if conn is None:
        return set()
    try:
        return {str(row[0]) for row in conn.execute(
            "SELECT object_anchor FROM ingestion_relations "
            "WHERE predicate='supersedes' AND inactive=0"
        ).fetchall()}
    except sqlite3.Error:
        logger.debug("CDF supersession lookup failed during injection", exc_info=True)
        return set()


def _render_selected(ranked: list[_RankedUnit], selected: set[str]) -> str:
    parts: list[str] = []
    section = None
    for item in sorted(ranked, key=lambda value: value.unit.byte_start):
        if item.unit.anchor not in selected:
            continue
        if item.unit.section != section:
            section = item.unit.section
            if section:
                parts.append(f"## {section}")
        text = _prompt_form(item.unit.text)
        if text:
            parts.append(text)
    return "\n\n".join(parts)


def _latest_selection(conn: sqlite3.Connection, file_path: str):
    return conn.execute(
        "SELECT file_hash,selected_anchors,score_snapshot_version,computed_at,omitted_count "
        "FROM injection_selection WHERE file_path=? ORDER BY id DESC LIMIT 1",
        (file_path,),
    ).fetchone()


def select_context_file(
    path: Path,
    *,
    budget: int,
    estimate: Callable[[str], int],
    now: datetime | date | None = None,
    conn: sqlite3.Connection | None = None,
) -> Selection:
    """Select MEMORY/EVOLUTION whole units without any user-query input.

    Results are recomputed only after a source hash change or a UTC day boundary.
    Superseded units are never injected. Over budget, whole units compete by score
    layer by layer, meta-cognition first; rendering always follows file order.
    """
    path = Path(path)
    if path.name not in {"MEMORY.md", "EVOLUTION.md"} or not path.is_file():
        return Selection("", 0, 0)
    raw = path.read_bytes()
    file_hash = hashlib.sha256(raw).hexdigest()
    current = _utc_datetime(now)
    file_path = str(path.resolve(strict=False))
    owned = conn is None
    if owned:
        try:
            from core.ingestion.store import connect_rw
            conn = connect_rw(busy_timeout_ms=500)
        except (OSError, sqlite3.Error):
            conn = None
            logger.debug("CDF injection selection store unavailable", exc_info=True)

    from core.ingestion.units import parse_units
    units = parse_units(path, root=path.parent.parent)
    metadata_queues = _memory_metadata(path)
    metadata_by_anchor: dict[str, object] = {}
    visible = []
    for unit in units:
        queue = metadata_queues.get((unit.section, unit.title, unit.text), [])
        metadata = queue.pop(0) if queue else None
        if metadata is not None:
            metadata_by_anchor[unit.anchor] = metadata
            if getattr(metadata, "decay_state", "active") in {"dormant", "archived"}:
                continue
        visible.append(unit)
    unscored = [_RankedUnit(unit, _unit_layer(path, unit), 0.0, 0,
                            datetime(1970, 1, 1, tzinfo=timezone.utc)) for unit in visible]

    cached = None
    if conn is not None:
        try:
            cached = _latest_selection(conn, file_path)
        except sqlite3.Error:
            logger.debug("CDF injection selection cache lookup failed", exc_info=True)
    if cached is not None:
        cached_at = _parse_time(cached[3])
        if (str(cached[0]) == file_hash and cached_at is not None
                and cached_at.date() == current.date()
                and str(cached[2]) == SCORE_SNAPSHOT_VERSION):
            anchors = tuple(str(item) for item in json.loads(str(cached[1])))
            selected = set(anchors)
            rendered = _render_selected(unscored, selected)
            full = len(selected) == len(unscored)
            text = _prompt_form(_filter_inactive_memory(raw.decode("utf-8"))) if full else rendered
            if owned and conn is not None:
                conn.close()
            return Selection(text, int(cached[4]), estimate(text), anchors, False)

    ranked = [_score_unit(conn, path, unit, _unit_layer(path, unit), current,
                          metadata_by_anchor.get(unit.anchor)) for unit in visible]
    full_text = _prompt_form(_filter_inactive_memory(raw.decode("utf-8"))) \
        if path.name == "MEMORY.md" else _prompt_form(raw.decode("utf-8"))
    superseded = _superseded_anchors(conn)
    over_budget = estimate(full_text) > budget
    # A superseded unit is never injected, under budget too: its replacement says it
    # better, and the two together say two things.
    candidates = [item for item in ranked
                  if item.unit.anchor not in superseded
                  and not getattr(metadata_by_anchor.get(item.unit.anchor), "superseded_by", None)]
    if not over_budget and len(candidates) == len(ranked):
        selected = {item.unit.anchor for item in candidates}
        text = full_text
    elif not over_budget:
        selected = {item.unit.anchor for item in candidates}
        text = _render_selected(candidates, selected)
    else:
        # Every layer competes inside the budget, meta-cognition first: kept whole and
        # unbudgeted, it was already over the EVOLUTION budget and grew with every
        # correction CDF writes (2026-10-29-cdf-launch §2 C2).
        selected: set[str] = set()
        admission_order: list[str] = []
        selected_sections: set[str] = set()
        used = 0
        for layer in ("meta-cognitive", "cognitive", "operational"):
            ordered = sorted(
                (item for item in candidates if item.layer == layer),
                key=lambda item: (-item.score, -item.evidence_strength,
                                  -item.evidence_at.timestamp(), item.unit.anchor),
            )
            for item in ordered:
                prefix = f"## {item.unit.section}\n\n" if item.unit.section not in selected_sections else ""
                cost = estimate(prefix + _prompt_form(item.unit.text) + "\n\n")
                if used + cost <= budget:
                    selected.add(item.unit.anchor)
                    selected_sections.add(item.unit.section)
                    admission_order.append(item.unit.anchor)
                    used += cost
        text = _render_selected(candidates, selected)
        # The estimator is caller-supplied and need not be additive: remove the latest
        # admission until its estimate confirms the rendered result is within the cap.
        while estimate(text) > budget and admission_order:
            selected.remove(admission_order.pop())
            text = _render_selected(candidates, selected)
    anchors = tuple(item.unit.anchor for item in sorted(candidates, key=lambda value: value.unit.byte_start)
                    if item.unit.anchor in selected)
    omitted = len(ranked) - len(anchors)
    used = estimate(text)
    if conn is not None:
        try:
            conn.execute(
                """INSERT INTO injection_selection(
                     file_path,file_hash,selected_anchors,score_snapshot_version,computed_at,omitted_count
                   ) VALUES(?,?,?,?,?,?)""",
                (file_path, file_hash, json.dumps(anchors, ensure_ascii=False),
                 SCORE_SNAPSHOT_VERSION, current.isoformat(), omitted),
            )
            conn.commit()
        except sqlite3.Error:
            logger.debug("CDF injection selection record failed", exc_info=True)
    if owned and conn is not None:
        conn.close()
    return Selection(text, omitted, used, anchors, True)


def recorded_selection(path: Path, *, conn: sqlite3.Connection | None = None) -> tuple[str, ...] | None:
    """Return the latest anchors for the current file hash, without creating a DB."""
    path = Path(path)
    if not path.is_file():
        return None
    owned = conn is None
    if owned:
        try:
            from core.ingestion.store import connect_ro
            conn = connect_ro()
        except (OSError, sqlite3.Error):
            return None
    try:
        row = _latest_selection(conn, str(path.resolve(strict=False)))
        if row is None or str(row[0]) != hashlib.sha256(path.read_bytes()).hexdigest():
            return None
        return tuple(str(item) for item in json.loads(str(row[1])))
    except (OSError, sqlite3.Error, TypeError, ValueError, json.JSONDecodeError):
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
    ordered = [unit.text for unit in [*summaries, *events]]
    selected = _admit(ordered, budget, estimate)
    if selected.omitted:
        suffix = f"（另有 {selected.omitted} 个事件未注入，可用 recall 查看）"
        return Selection(
            selected.text + ("\n" if selected.text else "") + suffix,
            selected.omitted,
            selected.used_tokens,
        )
    return selected
