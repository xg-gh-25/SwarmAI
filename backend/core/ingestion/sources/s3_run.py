"""S3 run source (design §5.5 S3, §5.9 run-close protocol).

- ``enqueue_report``: the main pass. The agent's five-part report was validated at
  DELIVER (``reflect.prepare``: every item is a ``contracts_v2`` unit citing this run's
  anchors), so it is enqueued deterministically, in the landing command, with no LLM.
  An event's ``closes`` (the gaps it fixed) stays only when the run landed code.
- ``extract_human_answers``: the trusted pass. Only person-authored text on
  ``human_answer`` events (an optional annotation, a pipeline human-loop dependency)
  is extracted, with the LLM extractor; option ids and agent-written option labels are
  never input. With no such text it records one ``no_unit`` attempt.
"""
from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from core.ingestion import api_v2

from . import common
from .extract_llm import EXTRACTOR_VERSION

logger = logging.getLogger(__name__)
_ALLOWED = ("cognition", "event", "gap", "reference")


def _roots(engine: Any) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Return (anchors, root ids) registered for this run by the provenance owner."""
    from core.run_protocol.provenance import run_roots
    value = run_roots(engine.run_id)
    return tuple(value), tuple(value.values())


def _record_empty(run_id: str, extraction_pass: str) -> None:
    attempt = api_v2.ExtractionAttempt(
        source="S3", evidence_range={"run_id": run_id, "pass": extraction_pass},
        extractor_version=EXTRACTOR_VERSION,
        attempt_id=f"attempt:{uuid.uuid4().hex}")
    api_v2.record_extraction_attempt(attempt, status="no_unit")


def _human_texts(events: list[dict[str, Any]]) -> list[dict[str, str]]:
    rows = []
    for event in events:
        if event.get("kind") != "human_answer":
            continue
        payload = event.get("payload") or {}
        text = payload.get("text") or payload.get("human_text")
        if isinstance(text, str) and text.strip():
            rows.append({"event_id": str(event["event_id"]), "text": text})
    return rows


def _snapshot(engine: Any) -> dict[str, Any]:
    return {"project": engine.project, "repos": dict(engine.contract.get("repo_roots") or {})}


LANDED = ("landed", "human_override")


def _drop_closes_unless_landed(engine: Any, units: list[Any]) -> list[Any]:
    """An event keeps only the ``closes`` ids that are open gaps of the run's project,
    and none when the run landed no code (it fixed nothing; design
    2026-10-30-cdf-gap-lifecycle §3). Checked again here, not only at prepare: the
    report file can be rewritten after it. The dropped ids are logged."""
    from core.ingestion import gaps
    landing = (engine.projection() or {}).get("landing")
    allowed = gaps.open_gap_ids(engine.project) if landing in LANDED else set()
    out = []
    for unit in units:
        closes = getattr(unit, "closes", None) or []
        if not closes:
            out.append(unit)
            continue
        kept = [gap for gap in closes if gap in allowed]
        if kept != closes:
            logger.info("S3 %s (%s): closes ignored %s", engine.run_id, landing,
                        sorted(set(closes) - set(kept)))
        out.append(unit.model_copy(update={"closes": kept or None}))
    return out


def _drop_overrides_unless_landed(engine: Any, units: list[Any]) -> list[Any]:
    """A run declares an override only from its own change: none when it landed no code
    (brain-driven §2.1). The project is checked when the unit is judged. Dropped
    declarations are logged."""
    landing = (engine.projection() or {}).get("landing")
    if landing in LANDED:
        return units
    out = []
    for unit in units:
        if getattr(unit, "overrides", None):
            logger.info("S3 %s (%s): overrides ignored %s", engine.run_id, landing, unit.overrides)
            unit = unit.model_copy(update={"overrides": None})
        out.append(unit)
    return out


def enqueue_report(engine: Any) -> list[Any]:
    """Main pass: enqueue every unit of the validated five-part report, no LLM.

    The read set is every root registered for the run (anti selective citation, §9)."""
    from core.ingestion import contracts_v2
    _anchors, root_ids = _roots(engine)
    report = engine.read_reflect_report()
    units = [contracts_v2.SOURCE_UNIT.validate_python(raw)
             for rows in report.values() for raw in rows]
    if any(getattr(unit, "closes", None) for unit in units):
        units = _drop_closes_unless_landed(engine, units)
    if any(getattr(unit, "overrides", None) for unit in units):
        units = _drop_overrides_unless_landed(engine, units)
    attempt = api_v2.ExtractionAttempt(
        source="S3", evidence_range={"run_id": engine.run_id, "pass": "main"},
        extractor_version=f"{EXTRACTOR_VERSION}+report", attempt_id=f"attempt:{uuid.uuid4().hex}")
    outcomes = [api_v2.enqueue_unit(
        "S3", engine.run_id, unit, session_id=None, run_id=engine.run_id,
        read_root_ids=root_ids, project_snapshot=_snapshot(engine),
        attempt_id=attempt.attempt_id, extraction_pass="main") for unit in units]
    api_v2.record_extraction_attempt(
        attempt, status="ok" if units else "no_unit", units_emitted=len(units))
    return outcomes


def has_human_text(engine: Any) -> bool:
    return bool(_human_texts(list(engine.store.read_events())))


def extract_human_answers(engine: Any, *, llm: Any | None = None) -> list[Any]:
    """Trusted pass over person-authored human_answer text only (LLM extractor)."""
    events = list(engine.store.read_events())
    human = _human_texts(events)
    if human:
        human_anchors = tuple(f"run_event:{row['event_id']}" for row in human)
        return common.extract_and_enqueue(
            "S3", engine.run_id, json.dumps(human, ensure_ascii=False), human_anchors,
            evidence_range={"run_id": engine.run_id, "pass": "human_answer"},
            run_id=engine.run_id, read_root_ids=tuple(human_anchors),
            project_snapshot=_snapshot(engine),
            extraction_pass="human_answer", allowed_kinds=_ALLOWED, llm=llm)
    _record_empty(engine.run_id, "human_answer")
    return []


def extract_run(engine: Any, *, llm: Any | None = None) -> dict[str, list[Any]]:
    """Both passes in order (trusted pass first, §9.1); used by tests and replay."""
    human = extract_human_answers(engine, llm=llm)
    return {"human_answer": human, "main": enqueue_report(engine)}


def previous_pending_run(data_dir: Any, workspace: Any, project: str,
                         current_run_id: str) -> str | None:
    """Newest previous run with a non-terminal S3 unit, or None; never waits."""
    from pathlib import Path
    import sqlite3
    run_root = Path(workspace) / "Projects" / project / ".artifacts" / "runs"
    candidates = []
    for path in run_root.glob("*/run.json") if run_root.is_dir() else ():
        if path.parent.name == current_run_id:
            continue
        try:
            row = json.loads(path.read_text(encoding="utf-8"))
            candidates.append((str(row.get("created_at") or ""), path.parent.name))
        except (OSError, ValueError, AttributeError):
            continue
    if not candidates:
        return None
    previous = max(candidates)[1]
    db_path = Path(data_dir) / "ingestion.db"
    if not db_path.exists():
        return None
    terminal = ("merged", "written", "reinforced", "rejected",
                "failed_poison", "expired_unprocessed")
    try:
        uri = f"file:{db_path.resolve()}?mode=ro"
        with sqlite3.connect(uri, uri=True, timeout=1.0) as conn:
            marks = ",".join("?" for _ in terminal)
            row = conn.execute(
                "SELECT 1 FROM ingestion_inbox i JOIN ingestion_units u "
                "ON u.candidate_id=i.id WHERE i.source_ref=? "
                "AND COALESCE(i.source_v2, i.source)='S3' "
                f"AND u.state NOT IN ({marks}) LIMIT 1", (previous, *terminal)).fetchone()
        return previous if row else None
    except sqlite3.Error:
        return None
