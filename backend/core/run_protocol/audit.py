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
          commits: Mapping[str, str] | None = None,
          repos_isolated: int | None = None,
          retained: Sequence[Mapping[str, Any]] = (),
          manual: Mapping[str, Any] | None = None) -> dict[str, Any]:
    if audit_through not in {"DELIVER", "COMPLETED"}:
        raise AuditError("audit_through must be DELIVER or COMPLETED")
    if projection.get("run_id") != run_id or contract.get("run_id") != run_id:
        raise AuditError("audit inputs belong to another run")
    event_rows = list(events)
    event_times = [value for value in (_time(row.get("at")) for row in event_rows)
                   if value is not None]
    # too few timed events: the time is unknown (null), not zero (§E9.2)
    wall = max(event_times) - min(event_times) if len(event_times) >= 2 else None
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
    active = max(0.0, wall - waiting) if wall is not None else None
    budget_seconds = contract.get("budget_seconds")
    speed_complete = isinstance(budget_seconds, (int, float)) and budget_seconds > 0
    speed = {"verdict": ("PASS" if speed_complete and active is not None
                         and active <= budget_seconds else "WARN"),
             "wall_seconds": round(wall, 3) if wall is not None else None,
             "active_seconds": round(active, 3) if active is not None else None,
             "budget_seconds": budget_seconds if speed_complete else None,
             "waiting_seconds": round(waiting, 3)}

    repos = final.get("repos") if isinstance(final, Mapping) else {}
    expected = list(contract.get("expected_repos") or [])
    contamination = sum(1 for event in event_rows if event.get("kind") == "integrity"
                        and str(event.get("payload", {}).get("conflict_type", "")).startswith("cross_run"))
    isolated = contract.get("mode") == "isolated"
    # measured by the engine (repos really worked in a run worktree), else unknown
    isolated_count = repos_isolated
    if isolated and repos_isolated != len(expected):
        isolated = False            # fewer (or unknown) worktrees than repos: not proven
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

    # answered decisions + run-land (both in the projection) + accept_finding (§E9.3, D4)
    human_decisions = int(projection.get("human_decisions") or 0) + sum(
        1 for event in event_rows if event.get("kind") == "auth_consumed"
        and event.get("payload", {}).get("action") == "accept_finding")
    rescues = sum(1 for event in event_rows if event.get("kind") == "integrity"
                  and event.get("payload", {}).get("conflict_type") == "manual_rescue")
    approvals = sum(1 for event in event_rows if event.get("kind") == "metric"
                    and event.get("payload", {}).get("name") == "required_approval")
    autonomy = {"verdict": "BLOCK" if rescues else "WARN" if human_decisions else "PASS",
                "human_decisions": human_decisions, "manual_rescues": rescues,
                "required_approvals": approvals}

    # Token accounting (D3). A transcript measurement (``measure-usage``, cli) covers the
    # main agent AND every sub-agent of the run's transcripts: its LATEST set is the
    # token total, and the per-expert SubagentStop records (runner) are already in it,
    # so they are not added. Without one, only the experts' tokens are known.
    measured: dict[str, float] = {}
    experts: dict[str, float] = {}
    expert_records = 0
    for event in event_rows:
        if event.get("kind") != "metric" or not isinstance(event.get("payload"), Mapping):
            continue
        name, value = event["payload"].get("name"), event["payload"].get("value")
        if not isinstance(name, str) or not isinstance(value, (int, float)) \
                or name == "required_approval":
            continue
        if event.get("producer") == "cli":
            measured[name] = value                     # a later measurement replaces
        else:
            experts[name] = experts.get(name, 0) + value
            expert_records += name == "input_tokens"
    answers = sum(event.get("kind") in {"answer", "answer_reuse"} for event in event_rows)
    # Completeness (E5, conformance F7), counted in model SESSIONS from events, never
    # from the measurement itself: the main agent is one, and every handout an expert
    # was spawned for is one (answered or not — a crashed expert still cost a call),
    # except a handout served by answer_reuse. A session is accounted when
    # a measurement covers it: the main agent by a transcript measurement, an expert
    # by its own SubagentStop usage record.
    spawned = sum(event.get("kind") == "handout" for event in event_rows) \
        - sum(event.get("kind") == "answer_reuse" for event in event_rows)
    total_calls: int | None = 1 + spawned
    accounted = (1 if measured else 0) + min(expert_records, spawned)
    if measured:
        input_tokens = measured.get("input_tokens")
        output_tokens = measured.get("output_tokens")
    elif experts:
        input_tokens, output_tokens = experts.get("input_tokens"), experts.get("output_tokens")
    else:
        input_tokens = output_tokens = None
    total_tokens = ((input_tokens + output_tokens)
                    if isinstance(input_tokens, (int, float)) and isinstance(output_tokens, (int, float))
                    else None)
    budget_tokens = contract.get("budget_tokens")
    if accounted == 0:
        token_verdict = "BLOCK"                        # nothing was metered at all
    elif accounted < total_calls or total_tokens is None \
            or not isinstance(budget_tokens, (int, float)):
        token_verdict = "WARN"
    else:
        token_verdict = "PASS" if total_tokens <= budget_tokens else "WARN"
    token = {"verdict": token_verdict, "total_tokens": total_tokens,
             "input_tokens": input_tokens, "output_tokens": output_tokens,
             "budget_tokens": budget_tokens if isinstance(budget_tokens, (int, float)) else None,
             "accounted_calls": accounted, "total_calls": total_calls,
             "expert_answers": answers, "expert_records": expert_records}

    commit_map = dict(commits or {})
    high_policy = [row["canonical_id"] for row in findings if push_effect(row)
                   and row.get("kind") == "security_policy"
                   and str(row.get("severity")).upper() in ("HIGH", "CRITICAL")]
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
               "requirement": contract.get("requirement"),
               "high_security_policy": high_policy,
               "retained": [dict(row) for row in retained],
               "manual": dict(manual) if manual else None}
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


def _duration(seconds: Any) -> str:
    if not isinstance(seconds, (int, float)):
        return "unknown"
    seconds = int(round(seconds))
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    return (f"{hours}h {minutes}m" if hours else f"{minutes}m {secs}s" if minutes
            else f"{secs}s")


def _k(value: Any) -> str:
    if not isinstance(value, (int, float)):
        return "unknown"
    return f"{value / 1000:.0f}k" if value >= 1000 else f"{value:g}"


def _speed_text(speed: Mapping[str, Any]) -> str:
    """§E9.4: wall, active, and the share of the reference line, or why it is missing."""
    if speed.get("wall_seconds") is None:
        return "time incomplete"
    text = f"{_duration(speed['wall_seconds'])} wall · {_duration(speed.get('active_seconds'))} active"
    budget = speed.get("budget_seconds")
    if isinstance(budget, (int, float)) and budget > 0 and isinstance(speed.get("active_seconds"), (int, float)):
        return text + f" · {100 * speed['active_seconds'] / budget:.1f}% budget"
    return text + " · no baseline"


def _token_text(token: Mapping[str, Any]) -> str:
    """§E9.4: observed totals; an incomplete count says so, never a made-up total."""
    total, accounted, calls = token.get("total_tokens"), token.get("accounted_calls"), token.get("total_calls")
    if total is None:
        return "not measured"
    if calls is None or accounted != calls:
        shown = f"{accounted}/{calls}" if calls is not None else f"{accounted}/unknown"
        return f"{_k(total)} observed · {shown} calls accounted"
    text = f"{_k(total)} total · {_k(token.get('input_tokens'))} input · {_k(token.get('output_tokens'))} output"
    budget = token.get("budget_tokens")
    return text + (f" · {100 * total / budget:.1f}% budget"
                   if isinstance(budget, (int, float)) and budget > 0 else " · no baseline")


def _quality_text(quality: Mapping[str, Any], reason: Any) -> str:
    if quality.get("verdict") == "N/A":
        return str(reason or "no code")
    return (f"{quality['verification_passed']}/{quality['verification_required']} verification · "
            f"{quality['findings_closed']}/{quality['findings_total']} findings closed")


def _isolation_text(row: Mapping[str, Any]) -> str:
    done = row.get("repos_isolated")
    shown = f"{done}/{row['repos_total']}" if done is not None else f"?/{row['repos_total']}"
    return f"{row['mode']} · {shown} repos · {row['contamination_count']} contamination"


def _conclusion(audit: Mapping[str, Any]) -> str:
    """§B8 item 1: what was delivered and whether landing passed; else the reason."""
    outcome, details = audit["outcome"], audit.get("details") or {}
    what = " ".join(str(details.get("requirement") or f"run {audit['run_id']}").split())[:200]
    landing = outcome.get("landing")
    if audit.get("audit_through") == "DELIVER" and landing is None:
        return f"交付：{what}。落地守卫通过。"
    if landing in ("landed", "human_override"):
        return f"交付：{what}。已落地（{'人工' if landing == 'human_override' else '守卫通过'}）。"
    return f"未落地：{what}。reason：{outcome.get('reason') or landing or 'unknown'}。"


def render(audit: Mapping[str, Any], *, fmt: str) -> str:
    """One renderer for REPORT and the chat card (§B8, §E9.5): the same five numbers."""
    if fmt not in {"report", "card"}:
        raise AuditError("format must be report or card")
    outcome, summary = audit["outcome"], audit["summary"]
    details = audit.get("details") or {}
    rows = [
        ("Run Isolation", summary["isolation"], _isolation_text(summary["isolation"])),
        ("Speed", summary["speed"], _speed_text(summary["speed"])),
        ("Quality", summary["quality"], _quality_text(summary["quality"], outcome.get("reason"))),
        ("Autonomy", summary["autonomy"],
         f"{summary['autonomy']['human_decisions']} decisions · {summary['autonomy']['manual_rescues']} rescues · {summary['autonomy']['required_approvals']} required approval"
         + ("s" if summary['autonomy']['required_approvals'] != 1 else "")),
        ("Token Cost", summary["token_cost"], _token_text(summary["token_cost"])),
    ]
    state = f"{str(outcome.get('status') or '').upper()} · {str(outcome.get('landing') or '').upper()} · " \
            + ("PUSH READY" if outcome.get("push_ready") else "NOT PUSH READY")
    if outcome.get("commit"):
        state += " · " + str(outcome["commit"])[:8]
    if fmt == "card":
        return "\n".join([f"Run Audit · {audit['run_id']}", state, ""] + [
            f"{name:<14} {row['verdict']} · {detail}" for name, row, detail in rows]
            + ["", "Final audit through COMPLETED"])
    title = "Final audit through COMPLETED" if audit["audit_through"] == "COMPLETED" \
        else "Audit through DELIVER"
    lines = [f"# Pipeline Delivery　　{title}", "", _conclusion(audit), "",
             "## Run Audit", "", "| Dimension | Result | This run |", "|---|---|---|"]
    lines += [f"| {name} | {row['verdict']} | {detail} |" for name, row, detail in rows]
    lines += ["", "Full machine-readable audit is stored with this run."]
    if not outcome.get("push_ready"):
        reasons = dict(details.get("push_reasons") or {})
        high = [f"- {cid}: HIGH security_policy finding still has push effect"
                for cid in details.get("high_security_policy") or []]
        items = [f"- {key}: {detail}" for key, detail in reasons.items()]
        if high or items:
            lines += ["", "## Push 前需要你看的"] + high + items[:5]
            if len(items) > 5:
                lines.append(f"- 另有 {len(items) - 5} 条，见 run_audit.json")
    manual = details.get("manual")
    if manual:
        lines += ["", f"人工落地：request {manual.get('request_id')}"
                  + (f" — {manual['note']}" if manual.get("note") else "")]
    lines += ["", "push 前还需要：构建 + 部署验证。"]
    retained = details.get("retained") or []
    if retained and outcome.get("landing") not in ("landed", "human_override"):
        where = ", ".join(str(row.get("ledger") or row.get("repo_name")) for row in retained)
        lines += ["", f"未集成的工作保存在 {where}；用 `pipeline_v1.py restore` 恢复，"
                  "或 `pipeline_v1.py run-land --prepare` 交给人落地。"]
    return "\n".join(lines).rstrip() + "\n"


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
