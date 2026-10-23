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
from typing import Callable, Any, Mapping

from core.run_protocol import checks, decision, schemas
from core.run_protocol.store import AuditStore


class DocumentError(ValueError):
    """A pipeline document or stage completion is invalid. ``issues`` maps each
    refusing check id to its message (empty for errors that are not a check)."""

    def __init__(self, message: str, issues: Mapping[str, str] | None = None):
        super().__init__(message)
        self.issues = dict(issues or {})


def _refuse(issues, blocking) -> None:
    """Raise for the issues whose check may stop the write (``stage`` effect);
    other checks are only recorded, so their issues let the document through."""
    stopping = [row for row in issues if blocking is None or row.check_id in blocking]
    if stopping:
        by_check: dict[str, str] = {}
        for row in stopping:
            by_check.setdefault(row.check_id, f"{row.path}: {row.message}")
        raise DocumentError("; ".join(f"{row.check_id} {row.path}: {row.message}"
                                      for row in stopping), by_check)


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


def _source_line(name: str, text: str) -> str:
    """The projection's last line (r6 §2.2): the source bytes it was rendered from (the
    very text just written), so a reader can tell a stale Markdown file from a current one."""
    import hashlib
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return f"\n<!-- rendered from {name} sha256:{digest} -->\n"


class DocumentStore:
    """Write validated JSON sources and generated Markdown into one run directory."""

    def __init__(self, run_dir: str | Path,
                 on_render_failed: "Callable[[str, Exception], None] | None" = None):
        self.run_dir = Path(run_dir)
        # the Markdown is a projection of the validated JSON source (K4, F23): with a
        # recorder, a failed render is recorded and the write still succeeds
        self.on_render_failed = on_render_failed

    def _render(self, document: str, path: Path, render: "Callable[[], str]") -> None:
        try:
            self._atomic(path, render())
        except Exception as exc:  # noqa: BLE001 — a projection, never the decision
            from core.run_protocol.infra import find
            if self.on_render_failed is None or find(exc) is not None:
                raise
            self.on_render_failed(document, exc)

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

    def write_requirements(self, value: Mapping[str, Any], *, variant: str, profile: str,
                           blocking: set[str] | None = None) -> list[str]:
        _refuse(schemas.validate_requirements(value, variant=variant, profile=profile), blocking)
        source = self.run_dir / "artifacts" / "requirements.json"
        rendered = self.run_dir / "REQUIREMENTS.md"
        text = json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2)
        self._atomic(source, text)
        self._render("REQUIREMENTS", rendered,
                     lambda: render_requirements(value) + _source_line(source.name, text))
        return [str(source), str(rendered)]

    def write_design(self, value: Mapping[str, Any], *, variant: str, profile: str,
                     requirements: Mapping[str, Any], requirement_variant: str,
                     existing_files: set[str], blocking: set[str] | None = None) -> list[str]:
        _refuse(schemas.validate_contract(
            requirements, value, requirement_variant=requirement_variant,
            design_variant=variant, profile=profile, existing_files=existing_files), blocking)
        source = self.run_dir / "artifacts" / "design_spec.json"
        rendered = self.run_dir / "DESIGN_SPEC.md"
        text = json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2)
        self._atomic(source, text)
        self._render("DESIGN_SPEC", rendered,
                     lambda: render_design(value) + _source_line(source.name, text))
        return [str(source), str(rendered)]


def complete_stage(store: AuditStore, *, profile: str, stage: str, variant: str | None,
                   context: checks.CheckContext, at: str, attempt: int,
                   check_ids: list[str] | None = None,
                   forced: Mapping[str, str] | None = None) -> dict[str, Any]:
    """Open one stage attempt and close it: every declared check gets a result.

    *attempt* comes from the lifecycle projection (the one place that numbers
    attempts). *check_ids* defaults to the registry roster. A check named in *forced*
    is recorded failed with that detail instead of being evaluated (a document the
    schema refused); when anything is forced, the other checks are recorded
    ``not evaluated`` — their inputs were never written."""
    roster = list(check_ids) if check_ids is not None else [
        spec.check_id for spec in checks.stage_checks(profile, stage, variant)]
    forced = dict(forced or {})
    attempt_event, _ = store.append_new(
        writer=store.authority.writer("cli"), kind="stage_attempt", at=at,
        stage=stage, attempt=attempt, payload={"check_ids": roster},
    )
    results: list[dict[str, Any]] = []
    for check_id in roster:
        if check_id in forced:
            passed, detail = False, forced[check_id]
        elif forced:
            passed, detail = False, "not evaluated: the document was refused"
        else:
            passed, detail = checks.evaluate(check_id, context)
        event, _ = store.append_new(
            writer=store.authority.writer("cli"), kind="check", at=at,
            stage=stage, attempt=attempt,
            payload={"stage_attempt_event_id": attempt_event["event_id"],
                     "check_id": check_id, "passed": passed, "detail": detail},
        )
        results.append({"id": check_id, "passed": passed,
                        "detail": detail, "event_id": event["event_id"]})
    return {"attempt": attempt, "stage_attempt_event_id": attempt_event["event_id"],
            "passed": all(row["passed"] for row in results), "checks": results}


def record_decision(store: AuditStore, value: Mapping[str, Any], *,
                    known_refs: Mapping[str, Mapping[str, Any]], document: str,
                    at: str, decisions_answered: int, cap: int,
                    after_cap: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Record a DC-1-valid decision. Below the cap it asks the person; at the cap the
    agent's evidence-based choice (*after_cap*: option_id + evidence_refs) is resolved
    by ``decision.resolve_after_cap`` and recorded instead — DC-2 then fails push
    (v13 §A1.2/§B5.2, conformance F12). At the cap without a choice it is refused."""
    issues = decision.validate(value, known_refs)
    if issues:
        raise DocumentError("; ".join(f"{row.path}: {row.message}" for row in issues))
    payload: dict[str, Any] = {
        "request_id": value["request_id"], "decision_kind": value["kind"],
        "document": document,
        "decision_json": json.dumps(dict(value), sort_keys=True, ensure_ascii=False)}
    if not decision.may_ask(decisions_answered, cap):
        if not isinstance(after_cap, Mapping):
            raise DocumentError("decision cap reached: choose from evidence — pass "
                                "after_cap {option_id, evidence_refs}; DC-2 records it")
        try:
            resolved = decision.resolve_after_cap(
                value, option_id=str(after_cap.get("option_id")),
                evidence_refs=list(after_cap.get("evidence_refs") or []),
                known_refs=known_refs, decisions_answered=decisions_answered, cap=cap)
        except decision.DecisionError as exc:
            raise DocumentError(str(exc)) from exc
        payload.update(decided_by="agent_after_cap", option_id=resolved["option_id"],
                       evidence_refs=resolved["evidence_refs"])
    elif after_cap is not None:
        raise DocumentError("after_cap is for a decision past the cap; ask the person")
    event, _ = store.append_new(writer=store.authority.writer("cli"), kind="decision",
                                at=at, payload=payload)
    return event
