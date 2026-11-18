"""How much project knowledge a run was given and how much it wrote back (S7 ③).

A read model for the Pipelines Eval (Knowledge Usage), beside the stage timeline:

- ``used``: the distinct entry ids recalled into the run — the contract's recall (at
  create) and each ``knowledge_recalled`` event (EVALUATE's design recall): what the
  agent was given, from where it cannot edit it (RunEngine._knowledge_records); None
  when no recall ran (each was ``unavailable``);
- ``new``: the cognition units the run's REFLECT report submitted to CDF, counted by the
  same rule the submission uses (the report as RunEngine.read_reflect_report reads it,
  each row validated by core.ingestion.sources.s3_run.report_units); None before that
  report exists. CDF admits them later and its unit ids do not join to the run, so this
  counts what the run submitted, and says so.

Exports: counts
"""
from __future__ import annotations

from typing import Any, Mapping, Optional


def counts(contract: Mapping[str, Any], events: list[Mapping[str, Any]],
           report: Optional[Mapping[str, Any]]) -> dict[str, Optional[int]]:
    """``{"used": n, "new": n | None}`` (see the module doc). *report* is the five-part
    report as ``RunEngine.read_reflect_report`` gives it (None before REFLECT wrote one)."""
    ids: set[str] = set()
    recalls: list[Any] = [contract.get("knowledge")] if isinstance(contract, Mapping) else []
    recalls += [e.get("payload") for e in events if e.get("kind") == "knowledge_recalled"]
    # a recall that could not run (run_knowledge.unavailable) gave nothing we can count:
    # with no recall that ran, how much was given is unknown, never 0
    recalls = [r for r in recalls if isinstance(r, Mapping) and r.get("status") != "unavailable"]
    for rec in recalls:
        items = rec.get("items") if isinstance(rec, Mapping) else None
        for i in items if isinstance(items, list) else []:
            if isinstance(i, (str, int)) and str(i):
                ids.add(str(i))
    new = None
    if report is not None:
        # counted by the rule the CDF submission uses (s3_run.report_units): the
        # cognition units among the rows the contract accepts
        from core.ingestion.sources.s3_run import report_units
        units, _invalid = report_units(report)
        new = sum(1 for u in units if getattr(u, "kind", None) == "cognition")
    return {"used": len(ids) if recalls else None, "new": new}
