"""Pipeline REFLECT five-part knowledge-report contract for CDF v2.

- ``prepare`` validates the five named sections and every contracts_v2 unit, and that
  an event closes only open gaps of the run's project.
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


def prepare(report: Mapping[str, Any], *, allowed_anchors: set[str],
            open_gap_ids: set[str] | None = None) -> dict[str, Any]:
    """Validate and normalize one complete five-part report.

    *open_gap_ids* is every open gap of the run's project (``gaps.open_gap_ids``): an
    event may close only those. ``None`` skips the check (the RF re-check of a report
    already prepared)."""
    if not isinstance(report, Mapping) or set(report) != set(SECTIONS):
        raise ReflectError(f"reflect report must contain exactly: {', '.join(SECTIONS)}")
    sections: dict[str, list[dict[str, Any]]] = {}
    for section in SECTIONS:
        raw_rows = report[section]
        if not isinstance(raw_rows, list):
            raise ReflectError(f"{section} must be a list")
        rows = []
        for index, raw in enumerate(raw_rows):
            try:
                unit = contracts_v2.SOURCE_UNIT.validate_python(raw)
            except Exception as exc:
                raise ReflectError(f"{section}[{index}] is not a contracts_v2 unit: {exc}") from exc
            if not _section_accepts(section, unit):
                raise ReflectError(f"{section}[{index}] has the wrong unit kind or cognition type")
            unknown = set(unit.evidence) - allowed_anchors
            if unknown:
                raise ReflectError(f"{section}[{index}] cites evidence outside this run: {sorted(unknown)}")
            closes = set(getattr(unit, "closes", None) or ())
            if open_gap_ids is not None and closes - open_gap_ids:
                raise ReflectError(
                    f"{section}[{index}] closes gaps that are not open in this project "
                    f"(pipeline-v1 events lists them): {sorted(closes - open_gap_ids)}")
            rows.append(unit.model_dump(mode="json", exclude_none=True))
        sections[section] = rows
    return {"schema": SCHEMA, "sections": sections}
