"""The run's traceability matrix: requirement / property / failure mode → task →
exit criterion → result (design §E9.2, conformance F22; NE §9.2 reuses it).

One definition, two readers: ``run_audit.json`` details and, later, the human-readable
spec renderer. The structure comes from the validated DESIGN_SPEC (tasks and exit
criteria, with the run's decisions overlaid); the results come only from *final*'s
trace rows, per repo. Since S6-1 there is no runner final (callers pass ``{}``), so
every result is ``None`` until the audit reads validation reports (S6-4). Nothing is
inferred; a run without a DESIGN_SPEC has no matrix and says why.

- ``matrix(design, final)`` — ``{"rows": {id: row}, "unavailable": reason | None}``.
"""
from __future__ import annotations

from typing import Any, Mapping


def _results(final: Mapping[str, Any] | None) -> dict[str, dict[str, Any]]:
    """exit id → {repo: result} from the final's trace rows."""
    out: dict[str, dict[str, Any]] = {}
    repos = (final or {}).get("repos") if isinstance(final, Mapping) else None
    for repo, row in sorted((repos or {}).items()):
        for trace in (row or {}).get("trace") or []:
            if isinstance(trace, Mapping) and isinstance(trace.get("exit"), str):
                out.setdefault(trace["exit"], {})[repo] = trace.get("result")
    return out


def matrix(design: Mapping[str, Any] | None,
           final: Mapping[str, Any] | None) -> dict[str, Any]:
    if not isinstance(design, Mapping) or not isinstance(design.get("exit_criteria"), list):
        return {"rows": {}, "unavailable": "no DESIGN_SPEC for this run"}
    results = _results(final)
    rows: dict[str, dict[str, Any]] = {}

    def row(identifier: str) -> dict[str, Any]:
        return rows.setdefault(identifier, {"tasks": [], "exits": []})

    for task in design.get("tasks") or []:
        if isinstance(task, Mapping) and isinstance(task.get("id"), str):
            for ref in task.get("requirements") or []:
                if isinstance(ref, str):
                    row(ref)["tasks"].append(task["id"])
    for exit_row in design["exit_criteria"]:
        if not isinstance(exit_row, Mapping) or not isinstance(exit_row.get("id"), str):
            continue
        entry = {"exit": exit_row["id"], "kind": exit_row.get("kind"),
                 "ref": exit_row.get("ref"),
                 "results": results.get(exit_row["id"], {}) or None}
        for covered in exit_row.get("covers") or []:
            if isinstance(covered, str):
                row(covered)["exits"].append(entry)
    for key, field in (("properties", "validates"), ("failure_modes", None)):
        for item in design.get(key) or []:
            if isinstance(item, Mapping) and isinstance(item.get("id"), str):
                entry = row(item["id"])
                entry["test"] = item.get("test")
                if field:
                    entry["validates"] = [v for v in item.get(field) or [] if isinstance(v, str)]
    return {"rows": dict(sorted(rows.items())), "unavailable": None}
