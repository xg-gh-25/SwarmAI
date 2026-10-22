"""Five-dimension Pipeline v1 run audit and shared renderers.

Public symbols:
- ``build`` — derive run_audit.json solely from events/final/findings/contract.
- ``render`` — render the same summary as REPORT or completed chat card.
- ``write`` — atomically persist the machine-readable audit.
"""
from __future__ import annotations

import json
import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

from core.run_protocol.findings import push_effect


class AuditError(ValueError):
    """Audit inputs are malformed or internally inconsistent."""


def _time(value: Any) -> float | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _verdict(value: bool, warn: bool = False) -> str:
    return "PASS" if value and not warn else "WARN" if value else "BLOCK"


def build(*, run_id: str, projection: Mapping[str, Any], events: Sequence[Mapping[str, Any]],
          final: Mapping[str, Any], findings: Sequence[Mapping[str, Any]],
          contract: Mapping[str, Any], push: Mapping[str, Any], audit_through: str,
          commits: Mapping[str, str] | None = None) -> dict[str, Any]:
    if audit_through not in {"DELIVER", "COMPLETED"}:
        raise AuditError("audit_through must be DELIVER or COMPLETED")
    if projection.get("run_id") != run_id or contract.get("run_id") != run_id:
        raise AuditError("audit inputs belong to another run")
    event_rows = list(events)
    event_times = [value for value in (_time(row.get("at")) for row in event_rows)
                   if value is not None]
    wall = max(event_times) - min(event_times) if len(event_times) >= 2 else 0.0
    waiting = 0.0
    opens: dict[tuple[str, str], float] = {}
    for event in event_rows:
        when = _time(event.get("at"))
        if when is None:
            continue
        if event.get("kind") in {"decision", "land_request"}:
            opens[("decision" if event["kind"] == "decision" else "auth",
                   event["payload"]["request_id"])] = when
        elif event.get("kind") in {"human_answer", "auth"}:
            key = ("decision" if event["kind"] == "human_answer" else "auth",
                   event["payload"]["request_id"])
            if key in opens:
                waiting += max(0.0, when - opens.pop(key))
    active = max(0.0, wall - waiting)
    budget_seconds = contract.get("budget_seconds")
    speed_complete = isinstance(budget_seconds, (int, float)) and budget_seconds > 0
    speed = {"verdict": ("PASS" if speed_complete and active <= budget_seconds else "WARN"),
             "wall_seconds": round(wall, 3), "active_seconds": round(active, 3),
             "budget_seconds": budget_seconds if speed_complete else None,
             "waiting_seconds": round(waiting, 3)}

    repos = final.get("repos") if isinstance(final, Mapping) else {}
    expected = list(contract.get("expected_repos") or [])
    contamination = sum(1 for event in event_rows if event.get("kind") == "integrity"
                        and str(event.get("payload", {}).get("conflict_type", "")).startswith("cross_run"))
    isolated = contract.get("mode") == "isolated"
    isolated_count = len(expected) if isolated else 0
    isolation = {"verdict": "BLOCK" if contamination else "PASS" if isolated else "WARN",
                 "mode": contract.get("mode"), "repos_isolated": isolated_count,
                 "repos_total": len(expected), "contamination_count": contamination}

    required, passed = 0, 0
    if isinstance(repos, Mapping):
        for row in repos.values():
            required += 1
            if (row.get("tests") or {}).get("verdict") == "green":
                passed += 1
            for validation in row.get("validations", []):
                required += 1
                if validation.get("verdict") == "pass":
                    passed += 1
    closed_states = {"closed_proven", "closed_asserted", "refuted", "accepted"}
    closed = sum(row.get("status") in closed_states for row in findings)
    landing = projection.get("landing")
    quality_block_reasons = {"tests_red", "tests_incomplete", "validation_failed",
                             "final_stale", "report_mismatch"}
    if projection.get("reason") in quality_block_reasons:
        quality_verdict = "BLOCK"
    elif landing in {"no_code", "not_landed"}:
        quality_verdict = "N/A"
    elif landing == "human_override" or passed < required or closed < len(findings) \
            or not push.get("push_ready", False):
        quality_verdict = "WARN"
    else:
        quality_verdict = "PASS"
    quality = {"verdict": quality_verdict, "verification_passed": passed,
               "verification_required": required, "findings_closed": closed,
               "findings_total": len(findings)}

    human_decisions = int(projection.get("human_decisions") or 0)
    rescues = sum(1 for event in event_rows if event.get("kind") == "integrity"
                  and event.get("payload", {}).get("conflict_type") == "manual_rescue")
    approvals = sum(1 for event in event_rows if event.get("kind") == "metric"
                    and event.get("payload", {}).get("name") == "required_approval")
    autonomy = {"verdict": "BLOCK" if rescues else "WARN" if human_decisions else "PASS",
                "human_decisions": human_decisions, "manual_rescues": rescues,
                "required_approvals": approvals}

    metrics: dict[str, float] = {}
    for event in event_rows:
        if event.get("kind") != "metric" or not isinstance(event.get("payload"), Mapping):
            continue
        name, value = event["payload"].get("name"), event["payload"].get("value")
        if isinstance(name, str) and isinstance(value, (int, float)):
            metrics[name] = metrics.get(name, 0) + value
    input_tokens = metrics.get("input_tokens")
    output_tokens = metrics.get("output_tokens")
    total_tokens = ((input_tokens + output_tokens)
                    if isinstance(input_tokens, (int, float)) and isinstance(output_tokens, (int, float))
                    else None)
    total_calls = sum(event.get("kind") in {"handout", "turn"} for event in event_rows)
    accounted = int(metrics.get("accounted_calls") or 0)
    budget_tokens = contract.get("budget_tokens")
    if total_calls == 0:
        token_verdict = "PASS"
    elif accounted == 0:
        token_verdict = "BLOCK"
    elif accounted < total_calls or total_tokens is None or not isinstance(budget_tokens, (int, float)):
        token_verdict = "WARN"
    else:
        token_verdict = "PASS" if total_tokens <= budget_tokens else "WARN"
    token = {"verdict": token_verdict, "total_tokens": total_tokens,
             "input_tokens": input_tokens, "output_tokens": output_tokens,
             "budget_tokens": budget_tokens if isinstance(budget_tokens, (int, float)) else None,
             "accounted_calls": accounted, "total_calls": total_calls}

    commit_map = dict(commits or {})
    outcome = {"status": projection.get("status"), "landing": landing,
               "reason": projection.get("reason"),
               "push_ready": bool(push.get("push_ready", False)),
               "commit": next(iter(commit_map.values()), None)}
    details = {"events": [row.get("event_id") for row in event_rows],
               "push_reasons": dict(push.get("reasons") or {}),
               "findings": [dict(row) for row in findings],
               "repos": dict(repos) if isinstance(repos, Mapping) else {},
               "commits": commit_map,
               "unclosed_high": [row["canonical_id"] for row in findings if push_effect(row)],
               # direct: the user's words are the requirement (no REQUIREMENTS doc)
               "requirement": contract.get("requirement")}
    audit = {"run_id": run_id, "audit_through": audit_through, "card": None,
             "outcome": outcome,
             "summary": {"isolation": isolation, "speed": speed, "quality": quality,
                         "autonomy": autonomy, "token_cost": token},
             "details": details}
    if audit_through == "COMPLETED":
        audit["card"] = render(audit, fmt="card")
    return audit


def _n(value: Any, suffix: str = "") -> str:
    return "unknown" if value is None else f"{value:g}{suffix}" if isinstance(value, float) else f"{value}{suffix}"


def render(audit: Mapping[str, Any], *, fmt: str) -> str:
    if fmt not in {"report", "card"}:
        raise AuditError("format must be report or card")
    outcome, summary = audit["outcome"], audit["summary"]
    rows = [
        ("Run Isolation", summary["isolation"],
         f"{summary['isolation']['mode']} · {summary['isolation']['repos_isolated']}/{summary['isolation']['repos_total']} repos · {summary['isolation']['contamination_count']} contamination"),
        ("Speed", summary["speed"],
         f"{_n(summary['speed']['wall_seconds'], 's')} wall · {_n(summary['speed']['active_seconds'], 's')} active"),
        ("Quality", summary["quality"],
         f"{summary['quality']['verification_passed']}/{summary['quality']['verification_required']} verification · {summary['quality']['findings_closed']}/{summary['quality']['findings_total']} findings closed"),
        ("Autonomy", summary["autonomy"],
         f"{summary['autonomy']['human_decisions']} decisions · {summary['autonomy']['manual_rescues']} rescues · {summary['autonomy']['required_approvals']} required approvals"),
        ("Token Cost", summary["token_cost"],
         f"{_n(summary['token_cost']['total_tokens'])} total · {summary['token_cost']['accounted_calls']}/{summary['token_cost']['total_calls']} calls accounted"),
    ]
    state = f"{str(outcome.get('status') or '').upper()} · {str(outcome.get('landing') or '').upper()} · " \
            + ("PUSH READY" if outcome.get("push_ready") else "NOT PUSH READY")
    if outcome.get("commit"):
        state += " · " + str(outcome["commit"])[:8]
    if fmt == "card":
        return "\n".join([f"Run Audit · {audit['run_id']}", state, ""] + [
            f"{name:<14} {row['verdict']} · {detail}" for name, row, detail in rows])
    lines = [f"# Pipeline Delivery — Audit through {audit['audit_through']}", "", state, ""]
    requirement = audit.get("details", {}).get("requirement")
    if requirement:
        lines += [f"Requirement: {' '.join(str(requirement).split())[:500]}", ""]
    lines += [
             "## Run Audit", "", "| Dimension | Result | This run |", "|---|---|---|"]
    lines += [f"| {name} | {row['verdict']} | {detail} |" for name, row, detail in rows]
    reasons = audit.get("details", {}).get("push_reasons", {})
    if reasons:
        lines += ["", "## Push 前需要你看的"]
        items = list(reasons.items())
        lines += [f"- {key}: {detail}" for key, detail in items[:5]]
        if len(items) > 5:
            lines.append(f"- 另有 {len(items) - 5} 条，见 run_audit.json")
    lines += ["", "Push 前还需要：构建 + 部署验证。", ""]
    return "\n".join(lines)


def write(path: str | Path, audit: Mapping[str, Any]) -> None:
    target = Path(path); target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(f".{target.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        with open(temp, "x", encoding="utf-8") as handle:
            json.dump(audit, handle, ensure_ascii=False, sort_keys=True, indent=2)
            handle.flush(); os.fsync(handle.fileno())
        os.replace(temp, target)
    finally:
        try: temp.unlink()
        except FileNotFoundError: pass
