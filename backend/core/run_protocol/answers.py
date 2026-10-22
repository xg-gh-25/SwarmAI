"""Strict Pipeline v1 review-answer envelope parser.

Public symbols:
- ``parse_answer`` — parse Gate-0 or REVIEW R1/R2 output without prose guessing.
- ``ParsedAnswer`` — normalized answer, findings, statuses and integrity notes.
- ``AnswerError`` — malformed envelope that cannot be accepted as parsed output.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

GATE0_VERDICTS = frozenset({
    "SUPPORTED", "SUPPORTED-WITH-CHANGES", "UNSUPPORTED",
    "WRONG-FRAME", "ALREADY-SATISFIED",
})
FINDING_SEVERITIES = frozenset({"CRITICAL", "HIGH", "MEDIUM", "LOW"})
FINDING_KINDS = frozenset({
    "behavior", "contract", "absence", "docs", "design", "security_policy",
})
R2_STATUSES = frozenset({
    "RESOLVED", "OPEN", "REFUTED", "ADEQUATE", "TEST_INADEQUATE",
})
_JSON_FENCE = re.compile(r"^```(?:json)?\s*$", re.IGNORECASE)


class AnswerError(ValueError):
    """The answer does not satisfy the frozen v13 output envelope."""


@dataclass(frozen=True)
class ParsedAnswer:
    """Normalized answer data suitable for typed event construction."""

    state: str
    verdict: str | None
    findings: tuple[dict[str, Any], ...]
    statuses: tuple[dict[str, Any], ...]
    missing_statuses: tuple[str, ...]
    integrity: tuple[str, ...]
    errors: tuple[str, ...]
    raw: str

    @property
    def answer_payload(self) -> dict[str, Any]:
        return {"state": self.state, "verdict": self.verdict,
                "n_findings": len(self.findings), "n_status": len(self.statuses)}


def _object_lines(text: str) -> tuple[list[tuple[int, Mapping[str, Any]]], list[str]]:
    rows: list[tuple[int, Mapping[str, Any]]] = []
    errors: list[str] = []
    fenced = False
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if _JSON_FENCE.fullmatch(line):
            fenced = not fenced
            continue
        candidate = line.lstrip("-*• ").strip()
        if not (candidate.startswith("{") or candidate.endswith("}")):
            continue
        try:
            value = json.loads(candidate.rstrip(","))
        except json.JSONDecodeError:
            errors.append(f"line {number}: malformed JSON object")
            continue
        if not isinstance(value, Mapping):
            errors.append(f"line {number}: JSON answer row must be an object")
            continue
        rows.append((number, value))
    if fenced:
        errors.append("unclosed JSON fence")
    return rows, errors


def _finding(row: Mapping[str, Any], number: int) -> dict[str, Any] | None:
    keys = {"severity", "kind", "file", "line", "claim", "fix"}
    if not ({"severity", "kind", "file", "line", "claim"} & set(row)):
        return None
    unknown = set(row) - keys
    missing = keys - set(row)
    if unknown or missing:
        raise AnswerError(
            f"line {number}: finding fields mismatch; missing={sorted(missing)}, unknown={sorted(unknown)}"
        )
    severity = row["severity"]
    kind = row["kind"]
    if not isinstance(severity, str) or severity.upper() not in FINDING_SEVERITIES:
        raise AnswerError(f"line {number}: invalid finding severity")
    if not isinstance(kind, str) or kind not in FINDING_KINDS:
        raise AnswerError(f"line {number}: invalid finding kind")
    if not isinstance(row["file"], str) or not row["file"].strip():
        raise AnswerError(f"line {number}: finding file is required")
    if not isinstance(row["line"], int) or isinstance(row["line"], bool) or row["line"] < 1:
        raise AnswerError(f"line {number}: finding line must be a positive integer")
    if not isinstance(row["claim"], str) or not row["claim"].strip():
        raise AnswerError(f"line {number}: finding claim is required")
    if not isinstance(row["fix"], str) or not row["fix"].strip():
        raise AnswerError(f"line {number}: finding fix is required")
    return {**row, "severity": severity.upper()}


def _status(row: Mapping[str, Any], number: int) -> dict[str, Any] | None:
    if "finding_id" not in row and "status" not in row:
        return None
    allowed = {"finding_id", "status", "kind_confirm", "note"}
    unknown = set(row) - allowed
    missing = {"finding_id", "status"} - set(row)
    if unknown or missing:
        raise AnswerError(
            f"line {number}: status fields mismatch; missing={sorted(missing)}, unknown={sorted(unknown)}"
        )
    finding_id, status = row["finding_id"], row["status"]
    if not isinstance(finding_id, str) or not re.fullmatch(r"CF-[0-9a-f]{8}", finding_id):
        raise AnswerError(f"line {number}: invalid canonical finding id")
    if not isinstance(status, str) or status not in R2_STATUSES:
        raise AnswerError(f"line {number}: invalid R2 status")
    kind = row.get("kind_confirm")
    if kind is not None and (not isinstance(kind, str) or kind not in FINDING_KINDS):
        raise AnswerError(f"line {number}: invalid kind_confirm")
    note = row.get("note")
    if note is not None and not isinstance(note, str):
        raise AnswerError(f"line {number}: note must be text")
    return dict(row)


def parse_answer(
    text: str,
    *,
    expert_id: str,
    round: int,
    finding_ids: Sequence[str] = (),
) -> ParsedAnswer:
    """Parse one answer exactly as v13 §A3.2 specifies.

    Gate-0 requires its verdict on the first non-empty line. REVIEW experts have no
    verdict. R2 status rows are limited to the exact handout roster; foreign rows are
    ignored and surfaced as integrity notes. Missing roster rows make the answer
    partial and are later folded as OPEN.
    """
    if not isinstance(text, str) or not isinstance(expert_id, str):
        raise AnswerError("text and expert_id are required")
    if round not in (1, 2):
        raise AnswerError("review round must be 1 or 2")
    roster = tuple(finding_ids)
    if len(roster) != len(set(roster)) or any(
        not isinstance(value, str) or not re.fullmatch(r"CF-[0-9a-f]{8}", value)
        for value in roster
    ):
        raise AnswerError("finding_ids must be unique canonical ids")
    if round == 1 and roster:
        raise AnswerError("R1 cannot carry an R2 finding roster")
    if round == 2 and not roster:
        raise AnswerError("R2 requires a non-empty finding roster")

    nonempty = [line.strip() for line in text.splitlines() if line.strip()]
    verdict: str | None = None
    errors: list[str] = []
    if expert_id == "gate0-skeptic":
        if not nonempty or not nonempty[0].startswith("VERDICT:"):
            errors.append("Gate-0 verdict must be the first non-empty line")
        else:
            token = nonempty[0].partition(":")[2].strip()
            if token not in GATE0_VERDICTS:
                errors.append("invalid Gate-0 verdict")
            else:
                verdict = token
    elif any(line.startswith("VERDICT:") for line in nonempty):
        errors.append("REVIEW experts must not emit a VERDICT line")

    rows, json_errors = _object_lines(text)
    errors.extend(json_errors)
    findings: list[dict[str, Any]] = []
    statuses: list[dict[str, Any]] = []
    integrity: list[str] = []
    status_ids: set[str] = set()
    try:
        for number, row in rows:
            status = _status(row, number)
            finding = _finding(row, number)
            if status is not None and finding is not None:
                raise AnswerError(f"line {number}: row cannot be status and finding")
            if status is not None:
                if round != 2:
                    raise AnswerError(f"line {number}: status row is only valid in R2")
                finding_id = status["finding_id"]
                if finding_id not in roster:
                    integrity.append(f"foreign R2 status ignored: {finding_id}")
                    continue
                if finding_id in status_ids:
                    raise AnswerError(f"line {number}: duplicate R2 status for {finding_id}")
                status_ids.add(finding_id)
                statuses.append(status)
            elif finding is not None:
                findings.append(finding)
            else:
                raise AnswerError(f"line {number}: unknown answer object")
    except AnswerError as exc:
        errors.append(str(exc))

    no_findings = any(line == "NO FINDINGS" for line in nonempty)
    if no_findings and findings:
        errors.append("NO FINDINGS conflicts with finding rows")
    if expert_id != "gate0-skeptic" and not findings and not no_findings and not statuses:
        errors.append("answer has neither findings, statuses, nor NO FINDINGS")
    missing = tuple(value for value in roster if value not in status_ids)

    if errors:
        state = "unparsed"
    elif round == 2 and missing:
        state = "partial"
    else:
        state = "parsed"
    return ParsedAnswer(
        state=state, verdict=verdict, findings=tuple(findings),
        statuses=tuple(statuses), missing_statuses=missing,
        integrity=tuple(integrity), errors=tuple(errors), raw=text,
    )
