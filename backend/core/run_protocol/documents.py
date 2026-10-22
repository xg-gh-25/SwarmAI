"""Pipeline v1 JSON document contracts and generated Markdown projections.

Public symbols:
- ``DocumentStore`` — validate/write REQUIREMENTS and DESIGN_SPEC JSON then render .md.
- ``render_requirements`` / ``render_design`` — deterministic human-readable projections.
- ``complete_stage`` — evaluate the exact stage-check roster and append typed events.
- ``record_decision`` — DC-1 guarded transition input persisted as a decision event.
"""
from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from typing import Any, Mapping

from core.run_protocol import checks, decision, schemas
from core.run_protocol.store import AuditStore


class DocumentError(ValueError):
    """A pipeline document or stage completion is invalid."""


def _ears(row: Mapping[str, Any]) -> str:
    prefixes = {"event": "WHEN", "state": "WHILE", "unwanted": "IF",
                "optional": "WHERE"}
    prefix = prefixes.get(row["pattern"])
    head = f"{prefix} {row['condition']}, " if prefix else ""
    return f"{head}THE SYSTEM SHALL {row['response']}"


def render_requirements(document: Mapping[str, Any]) -> str:
    lines = ["# Requirements", ""]
    understanding = document.get("understanding")
    if isinstance(understanding, Mapping):
        lines += ["## Understanding", "", str(understanding.get("claim") or ""), ""]
    for requirement in document.get("requirements", []):
        lines += [f"## {requirement['id']}", ""]
        if requirement.get("story"):
            lines += [str(requirement["story"]), ""]
        for row in requirement["acceptance"]:
            lines.append(f"- **{row['id']}** — {_ears(row)}")
        lines.append("")
    bugfix = document.get("bugfix")
    if isinstance(bugfix, Mapping):
        lines += ["## Bugfix contract", ""]
        for key in ("current", "expected", "unchanged"):
            lines.append(f"### {key.title()}")
            lines += [f"- **{row['id']}** — {_ears(row)}" for row in bugfix.get(key, [])]
            lines.append("")
    if document.get("analysis"):
        lines += ["## Analysis", ""] + [
            f"- {row['kind']} ({', '.join(row['refs'])}): {row['note']} → {row['resolution']}"
            for row in document["analysis"]] + [""]
    if document.get("clarifications"):
        lines += ["## Clarifications", ""] + [
            f"- **{row['qid']}** {row['question']} — {row['answer']} ({row['answered_by']})"
            for row in document["clarifications"]] + [""]
    return "\n".join(lines).rstrip() + "\n"


def render_design(document: Mapping[str, Any]) -> str:
    lines = ["# Design Spec", ""]
    chosen = document.get("chosen")
    if chosen:
        lines += ["## Chosen approach", "", str(chosen), ""]
    design = document.get("design")
    if isinstance(design, Mapping):
        lines += ["## Design", "", f"**Workflow:** {design.get('user_workflow', '')}", "",
                  f"**Architecture:** {design.get('architecture', '')}", "",
                  f"**Flow:** {design.get('flow', '')}", ""]
    for title, key in (("Properties", "properties"), ("Failure modes", "failure_modes"),
                       ("Tasks", "tasks"), ("Exit criteria", "exit_criteria")):
        rows = document.get(key) or []
        if rows:
            lines += [f"## {title}", ""]
            for row in rows:
                identity = row.get("id", "?")
                body = row.get("statement") or row.get("scenario") or row.get("change") \
                    or row.get("ref") or row.get("argv")
                lines.append(f"- **{identity}** — {body}")
            lines.append("")
    consumers = document.get("consumers")
    lines += ["## Consumers", ""]
    if isinstance(consumers, list):
        lines += [f"- {json.dumps(row, ensure_ascii=False, sort_keys=True)}" for row in consumers]
    else:
        lines.append(str(document.get("no_contract_change_reason") or ""))
    return "\n".join(lines).rstrip() + "\n"


class DocumentStore:
    """Write validated JSON sources and generated Markdown into one run directory."""

    def __init__(self, run_dir: str | Path):
        self.run_dir = Path(run_dir)

    @staticmethod
    def _atomic(path: Path, text: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
        try:
            with open(temp, "x", encoding="utf-8") as handle:
                handle.write(text); handle.flush(); os.fsync(handle.fileno())
            os.replace(temp, path)
        finally:
            try: temp.unlink()
            except FileNotFoundError: pass

    def write_requirements(self, value: Mapping[str, Any], *, variant: str, profile: str) -> list[str]:
        issues = schemas.validate_requirements(value, variant=variant, profile=profile)
        if issues:
            raise DocumentError("; ".join(f"{row.check_id} {row.path}: {row.message}" for row in issues))
        source = self.run_dir / "artifacts" / "requirements.json"
        rendered = self.run_dir / "REQUIREMENTS.md"
        self._atomic(source, json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2))
        self._atomic(rendered, render_requirements(value))
        return [str(source), str(rendered)]

    def write_design(self, value: Mapping[str, Any], *, variant: str, profile: str,
                     requirements: Mapping[str, Any], requirement_variant: str,
                     existing_files: set[str]) -> list[str]:
        issues = schemas.validate_contract(
            requirements, value, requirement_variant=requirement_variant,
            design_variant=variant, profile=profile, existing_files=existing_files)
        if issues:
            raise DocumentError("; ".join(f"{row.check_id} {row.path}: {row.message}" for row in issues))
        source = self.run_dir / "artifacts" / "design_spec.json"
        rendered = self.run_dir / "DESIGN_SPEC.md"
        self._atomic(source, json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2))
        self._atomic(rendered, render_design(value))
        return [str(source), str(rendered)]


def complete_stage(store: AuditStore, *, profile: str, stage: str, variant: str,
                   context: checks.CheckContext, at: str) -> dict[str, Any]:
    """Evaluate every stage-effect check once and append attempt/check events."""
    roster = checks.stage_checks(profile, stage, variant)
    prior = [row for row in store.read_events()
             if row.get("kind") == "stage_attempt" and row.get("stage") == stage]
    attempt = len(prior) + 1
    attempt_event, _ = store.append_new(
        writer=store.authority.writer("cli"), kind="stage_attempt", at=at,
        stage=stage, attempt=attempt,
        payload={"check_ids": [spec.check_id for spec in roster]},
    )
    results: list[dict[str, Any]] = []
    for spec in roster:
        passed, detail = checks.evaluate(spec.check_id, context)
        event, _ = store.append_new(
            writer=store.authority.writer("cli"), kind="check", at=at,
            stage=stage, attempt=attempt,
            payload={"stage_attempt_event_id": attempt_event["event_id"],
                     "check_id": spec.check_id, "passed": passed, "detail": detail},
        )
        results.append({"id": spec.check_id, "passed": passed,
                        "detail": detail, "event_id": event["event_id"]})
    return {"attempt": attempt, "stage_attempt_event_id": attempt_event["event_id"],
            "passed": all(row["passed"] for row in results), "checks": results}


def record_decision(store: AuditStore, value: Mapping[str, Any], *,
                    known_refs: Mapping[str, Mapping[str, Any]], document: str,
                    at: str, decisions_answered: int, cap: int) -> dict[str, Any]:
    issues = decision.validate(value, known_refs)
    if issues:
        raise DocumentError("; ".join(f"{row.path}: {row.message}" for row in issues))
    if not decision.may_ask(decisions_answered, cap):
        raise DocumentError("decision cap reached; choose from evidence and record DC-2")
    event, _ = store.append_new(
        writer=store.authority.writer("cli"), kind="decision", at=at,
        payload={"request_id": value["request_id"], "decision_kind": value["kind"],
                 "document": document},
    )
    return event
