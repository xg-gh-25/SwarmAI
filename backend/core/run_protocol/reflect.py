"""Pipeline REFLECT five-part knowledge-report contract for CDF v2.

- ``prepare_lenient`` keeps every valid contracts_v2 unit of the five named sections
  and drops each one that is not valid (wrong shape or section, evidence outside the
  run, a gap close that is not open), saying why: one bad entry never costs the rest
  of the report (design 2026-11-01-pipeline-delivery-friction E1). A missing section is
  empty; an unknown key is dropped. Only a report that is not the five-part form at all
  (the old lesson list) is refused.
- ``prepare`` is the strict form: the same validation, and any dropped entry raises
  (the RF re-check of a report already prepared).
- ``ReflectError`` reports malformed, cross-section, or foreign-evidence content.

Old lesson-list artifacts are intentionally incompatible.
"""
from __future__ import annotations

from typing import Any, Mapping

from core.ingestion import contracts_v2

SCHEMA = 2
SECTIONS = ("delivered", "decisions", "corrections", "practices", "gaps")


class ReflectError(ValueError):
    """The five-part report is malformed or cites evidence outside this run."""


def _section_accepts(section: str, unit: contracts_v2.SourceUnit) -> bool:
    if section == "delivered":
        return unit.kind in {"event", "cognition"}
    if section == "decisions":
        return unit.kind == "cognition" and unit.type == "decision"
    if section == "corrections":
        return unit.kind == "cognition" and unit.type in {"correction", "pitfall"}
    if section == "practices":
        return unit.kind == "cognition" and unit.type in {"guideline", "process"}
    return unit.kind == "gap"


def _unit(section: str, raw: Any, *, allowed_anchors: set[str],
          open_gap_ids: set[str] | None) -> dict[str, Any]:
    """One validated unit of *section*, or ReflectError naming why it is not one."""
    try:
        unit = contracts_v2.SOURCE_UNIT.validate_python(raw)
    except Exception as exc:
        raise ReflectError(f"not a contracts_v2 unit: {exc}") from exc
    if not _section_accepts(section, unit):
        raise ReflectError("wrong unit kind or cognition type for this section")
    unknown = set(unit.evidence) - allowed_anchors
    if unknown:
        raise ReflectError(f"cites evidence outside this run: {sorted(unknown)}")
    closes = set(getattr(unit, "closes", None) or ())
    if open_gap_ids is not None and closes - open_gap_ids:
        raise ReflectError(
            f"closes gaps that are not open in this project (pipeline-v1 events lists "
            f"them): {sorted(closes - open_gap_ids)}")
    return unit.model_dump(mode="json", exclude_none=True)


def prepare_lenient(report: Mapping[str, Any], *, allowed_anchors: set[str],
                    open_gap_ids: set[str] | None = None) -> dict[str, Any]:
    """``{"schema", "sections", "dropped"}``: every valid unit kept, each invalid one in
    ``dropped`` as ``{"section", "index", "reason"}`` (index None for a whole section or
    an unknown key). *open_gap_ids* is every open gap of the run's project
    (``gaps.open_gap_ids``): an event may close only those; ``None`` skips that check.
    Raises ReflectError only for a report that is not a mapping or has none of the five
    sections (the old lesson-list artifact)."""
    if not isinstance(report, Mapping) or not set(report) & set(SECTIONS):
        raise ReflectError(f"reflect report must contain the sections: {', '.join(SECTIONS)}"
                           " (each a list, any of them empty)")
    dropped: list[dict[str, Any]] = [
        {"section": str(key), "index": None, "reason": "not a section of the report"}
        for key in report if key not in SECTIONS]
    sections: dict[str, list[dict[str, Any]]] = {}
    for section in SECTIONS:
        raw_rows = report.get(section, [])
        if not isinstance(raw_rows, list):
            dropped.append({"section": section, "index": None, "reason": "must be a list"})
            sections[section] = []
            continue
        rows = []
        for index, raw in enumerate(raw_rows):
            try:
                rows.append(_unit(section, raw, allowed_anchors=allowed_anchors,
                                  open_gap_ids=open_gap_ids))
            except ReflectError as exc:
                dropped.append({"section": section, "index": index, "reason": str(exc)[:500]})
        sections[section] = rows
    return {"schema": SCHEMA, "sections": sections, "dropped": dropped}


def prepare(report: Mapping[str, Any], *, allowed_anchors: set[str],
            open_gap_ids: set[str] | None = None) -> dict[str, Any]:
    """Validate and normalize one complete five-part report; any entry that
    ``prepare_lenient`` would drop raises instead.

    *open_gap_ids* is every open gap of the run's project (``gaps.open_gap_ids``): an
    event may close only those. ``None`` skips the check (the RF re-check of a report
    already prepared)."""
    if not isinstance(report, Mapping) or set(report) != set(SECTIONS):
        raise ReflectError(f"reflect report must contain exactly: {', '.join(SECTIONS)}")
    document = prepare_lenient(report, allowed_anchors=allowed_anchors,
                               open_gap_ids=open_gap_ids)
    if document["dropped"]:
        first = document["dropped"][0]
        where = first["section"] + (f"[{first['index']}]" if first["index"] is not None else "")
        raise ReflectError(f"{where} {first['reason']}")
    return {"schema": document["schema"], "sections": document["sections"]}
