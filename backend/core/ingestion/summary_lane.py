"""CDF v2 summary lane for TIMELINE.

- ``SummaryLane.run_due`` performs the monthly summary work.
- ``enqueue_month_summary`` writes the summary text from the month's events (a model call
  through ``jobs.bedrock.structured``; a *writer* can be given instead) and submits it as an
  S0 EventUnit through the frozen v2 API.

The month summaries are the only part of TIMELINE the prompt carries
(memory_injection.select_timeline, TIMELINE_BUDGET tokens for two months), so a summary
states the month's key events and their outcomes, never a count of them. A summary that
could not be written, or that the store refused, is a failure: the lane counts it and tries
again later, and nothing contentless is stored in its place.

DailyActivity has no day summary: its rows are already one line per session, and the
injection shows them for all seven days (design 2026-10-31-daily-activity-useful D7).
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable


from core.memory_injection import TIMELINE_BUDGET

from .contracts_v2 import EventUnit

# How much of the prompt a summary may take, measured as the prompt measures it
# (ContextDirectoryLoader.estimate_tokens on the block it shows): select_timeline admits
# only whole units within memory_injection.TIMELINE_BUDGET, and the current month has no
# summary until it ends, so the previous month's summary takes two thirds and its later
# supplements share the rest. The writer is held to items and a line length, which a model
# follows; it does not keep to a character count (asked for 1,800 characters, a reply on
# 78 real events ran to about 2,450).
SUMMARY_MAX_TOKENS = TIMELINE_BUDGET * 2 // 3
SUPPLEMENT_MAX_TOKENS = TIMELINE_BUDGET // 3
SUMMARY_LINE_CHARS = 200
SUPPLEMENT_MAX_ITEMS = 4
SUMMARY_MAX_ITEMS = 12
# The standard the writer extracts to (the knowledge comes from extraction, never from a
# check afterwards): what an agent needs from a month at the start of a session.
SUMMARY_STANDARD = (
    "Write the key events of {month} for an agent that reads this summary at the start of "
    "every session, so it knows what was decided and what happened that month. Keep only "
    "what changes later judgment: decisions and changes of direction with their reason, "
    "milestones delivered, incidents and how they were resolved, real loss or rework. Merge "
    "the events that record the same piece of work into one item. Leave out routine "
    "operations, commit-level detail, statuses (where the work sits: pushed, on local main, "
    "under review) and work still to do. Use only what the events below say. Write one item "
    "per line, each starting with '- ', the most consequential first, in plain English: at "
    "most {items} items, each under {line} characters.\n"
)
SUPPLEMENT_NOTE = ("The events below were recorded after the summary of {month} was "
                   "written: summarize only what they add.\n")


class SummaryNotWritten(RuntimeError):
    """The month summary could not be written or stored; nothing was enqueued."""


Writer = Callable[[str, str, list[str]], str]


def _month_units(timeline: Path, month: str) -> list[tuple[str, str]]:
    """The events of *month*'s section, as (anchor, text shown in the prompt)."""
    if not timeline.is_file():
        return []
    from .units import parse_units
    from core.memory_injection import _shown

    return [
        (unit.anchor, _shown(unit.text)) for unit in parse_units(timeline, root=timeline.parent.parent)
        if unit.section == month and "month_summary" not in unit.text
        and "month_supplement" not in unit.text
    ]


def _month_anchors(timeline: Path, month: str) -> list[str]:
    return [anchor for anchor, _text in _month_units(timeline, month)]


def _previous_month(today: date) -> str:
    return (today.replace(day=1) - timedelta(days=1)).strftime("%Y-%m")


def _summarized(conn: sqlite3.Connection, month: str) -> tuple[bool, set[str]]:
    """Whether *month* has a summary, and the evidence of it and of every supplement: an
    event any of them already rests on is not summarized again (each writing is a model
    call, and a repeated one would put the same events into the prompt twice)."""
    rows = conn.execute(
        "SELECT source_ref, payload FROM ingestion_inbox WHERE source_ref=? OR source_ref LIKE ?",
        (f"summary:{month}", f"summary:{month}:supplement:%"),
    ).fetchall()
    evidence: set[str] = set()
    for _ref, raw in rows:
        try:
            evidence |= set(json.loads(raw).get("evidence", []))
        except (ValueError, AttributeError, TypeError):
            continue
    return any(ref == f"summary:{month}" for ref, _raw in rows), evidence


def _shown_tokens(when: str, title: str, what: str, impact: str) -> int:
    """The summary's size as the prompt measures the block the writer renders for it."""
    from core.context_directory_loader import ContextDirectoryLoader
    return ContextDirectoryLoader.estimate_tokens(
        f"### {when} {title}\n- what: {what}\n- impact: {impact}")


def model_writer(month: str, event_kind: str, events: list[str]) -> str:
    """The summary text, written by the CDF model from *events* (each an event as shown)."""
    from jobs.bedrock import structured
    from . import llm_usage
    from .llm import BedrockJudge

    judge = BedrockJudge()
    items = SUPPLEMENT_MAX_ITEMS if event_kind == "month_supplement" else SUMMARY_MAX_ITEMS
    prompt = SUMMARY_STANDARD.format(month=month, items=items, line=SUMMARY_LINE_CHARS)
    if event_kind == "month_supplement":
        prompt += SUPPLEMENT_NOTE.format(month=month)
    prompt += "\nThe events:\n\n" + "\n\n".join(events)
    usage: dict[str, int] = {}
    try:
        with llm_usage.attribute("S0"):
            answer = structured(
                prompt, model_id=judge.model_id, max_tokens=2000, name="month_summary",
                description="Record the summary of the month's key events.",
                schema={"type": "object", "properties": {"summary": {
                    "type": "string",
                    "description": f"at most {items} '- ' items, one per line, each under {SUMMARY_LINE_CHARS} characters"}},
                    "required": ["summary"]},
                temperature=judge.temperature, read_timeout=120, max_attempts=2,
                usage=usage, feature=llm_usage.feature())
    finally:
        if usage:
            llm_usage.record(usage)
    return str(answer.get("summary") or "")


def enqueue_month_summary(conn: sqlite3.Connection, workspace: Path, month: str, *,
                          writer: Writer | None = None) -> str:
    """Enqueue one month summary, or a supplement for newly arrived events. Returns
    ``unchanged`` when there is nothing new; raises SummaryNotWritten when the text could
    not be written or the store did not take it."""
    timeline = Path(workspace) / ".context" / "TIMELINE.md"
    units = _month_units(timeline, month)
    if not units:
        return "unchanged"
    source_ref = f"summary:{month}"
    summarized, previous = _summarized(conn, month)
    fresh = [(anchor, text) for anchor, text in units if f"unit:{anchor}" not in previous]
    event_kind = "month_summary"
    if summarized:
        if not fresh:
            return "unchanged"
        event_kind = "month_supplement"
        digest = hashlib.sha256("\n".join(a for a, _t in fresh).encode("utf-8")).hexdigest()[:12]
        source_ref = f"summary:{month}:supplement:{digest}"
        units = fresh
    try:
        text = (writer or model_writer)(month, event_kind, [t for _a, t in units]).strip()
    except Exception as exc:  # noqa: BLE001 — any writer failure is one counted failure
        raise SummaryNotWritten(f"writer failed: {type(exc).__name__}: {exc}") from exc
    if not text:
        raise SummaryNotWritten("writer returned no summary")
    when = f"{month}-01"
    title = f"{month} key events" if event_kind == "month_summary" else f"{month} key events, later"
    # an event needs an impact or an outcome: say what the summary rests on, briefly
    impact = f"Drawn from {len(units)} events of the month, each cited in its evidence."
    limit = SUMMARY_MAX_TOKENS if event_kind == "month_summary" else SUPPLEMENT_MAX_TOKENS
    size = _shown_tokens(when, title, text, impact)
    if size > limit:
        raise SummaryNotWritten(f"summary is {size} tokens as shown, over {limit}")
    unit = EventUnit(
        when=when,
        title=title,
        what=text,
        impact=impact,
        event_kind=event_kind,
        evidence=[f"unit:{anchor}" for anchor, _t in units],
    )
    from . import api_v2
    from .writer import ensure_baseline

    # the evidence is the month's events as corpus units: their roots are registered from
    # the writer's baseline (a unit the writer just wrote may not be registered yet)
    ensure_baseline(conn=conn)
    result = api_v2.enqueue_stored(
        "S0", source_ref, unit, session_id=None, run_id=None,
        read_root_ids=(), project_snapshot={}, extraction_pass="main",
    )
    if result.status not in ("queued", "duplicate"):
        raise SummaryNotWritten(f"store refused the summary: {result.status} {result.detail}")
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
