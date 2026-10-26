"""Five-dimension Pipeline v1 run audit and shared renderers.

Public symbols:
- ``build`` — derive run_audit.json from events, findings, contract and the TEST reports.
- ``render`` — render the same summary as REPORT or completed chat card.
- ``card_rows`` — the card's five rows as data, for the chat's run details.
- ``write`` — atomically persist the machine-readable audit.
"""
from __future__ import annotations

import json
import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

from core.run_protocol import trace as trace_protocol
from core.run_protocol.findings import is_closed, push_effect


class AuditError(ValueError):
    """Audit inputs are malformed or internally inconsistent."""




# metric(delivery_untracked): written by the landing (landing.record_untracked); a count
# of files, never a token measurement
UNTRACKED_METRIC = "delivery_untracked"


def _token_metrics(event_rows):
    """(cli measurements, summed expert records, usage_not_available reason, expert
    record count) from the metric events. The main agent's transcript measurement
    (producer cli) already includes every sub-agent, so its LATEST set is the total;
    without one only the experts' own SubagentStop records are known."""
    measured: dict[str, float] = {}
    experts: dict[str, float] = {}
    unavailable: str | None = None
    expert_records = 0
    for event in event_rows:
        if event.get("kind") != "metric" or not isinstance(event.get("payload"), Mapping):
            continue
        name, value = event["payload"].get("name"), event["payload"].get("value")
        if name == "usage_not_available":
            unavailable = event["payload"].get("reason") or "not measured"
            continue
        if not isinstance(name, str) or not isinstance(value, (int, float)) \
                or name in ("required_approval", UNTRACKED_METRIC) \
                or name.startswith("cap_exceeded."):
            continue
        if event.get("producer") == "cli":
            measured[name] = value                     # a later measurement replaces
        else:
            experts[name] = experts.get(name, 0) + value
            expert_records += name == "input_tokens"
    return measured, experts, unavailable, expert_records


def token_totals(event_rows) -> "tuple[float | None, float | None]":
    """(input, output) tokens of a run: the latest transcript measurement, else the sum
    of the experts' records, else (None, None). One rule for the audit and the
    dashboard read model."""
    measured, experts, _unavailable, _records = _token_metrics(event_rows)
    source = measured or experts
    return source.get("input_tokens"), source.get("output_tokens")


def _time(value: Any) -> float | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _verdict(value: bool, warn: bool = False) -> str:
    return "PASS" if value and not warn else "WARN" if value else "BLOCK"


def _reason(projection: Mapping[str, Any], events: Sequence[Mapping[str, Any]]) -> Any:
    """The run's not-landed reason; a person's run-land that raced (a logged
    partial_land later than every finished landing) is the outcome too, although the
    lifecycle leaves the run's state to the person (conformance K2, review r3)."""
    last_partial = last_finished = -1
    for index, event in enumerate(events):
        if event.get("kind") == "partial_land":
            last_partial = index
        elif event.get("kind") == "land" and (event.get("payload") or {}).get("landing") in (
                "landed", "human_override"):
            last_finished = index
    return "partial_land" if last_partial > last_finished else projection.get("reason")


def _unsettled(events: Sequence[Mapping[str, Any]]) -> list[str]:
    """Shared checkouts the latest landing outcome (a finished land, or a later
    partial_land) left unfinished (K5 review r2/r3)."""
    for event in reversed(list(events)):
        payload = event.get("payload") or {}
        if event.get("kind") == "partial_land" or (
                event.get("kind") == "land"
                and payload.get("landing") in ("landed", "human_override")):
            return [str(name) for name in payload.get("unsettled") or []]
    return []


def _union_seconds(spans: Sequence[tuple[float, float]]) -> float:
    """Length of the union of [start, end] intervals: parallel work counts once."""
    total, last = 0.0, None
    for start, end in sorted(spans):
        if last is None or start > last:
            total += end - start
            last = end
        elif end > last:
            total += end - last
            last = end
    return total


def _critical_path(events: Sequence[Mapping[str, Any]],
                   waits: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Each stage's span, from the event that opened it (run_create, or the previous
    stage_complete) to its own stage_complete (or the landing for the last one).

    - ``seconds``: the wall span; ``active_seconds``: the span minus the human waits
      (decision, auth) inside it;
    - ``dispatch_seconds``: the union of its handout→answer intervals (parallel
      experts count once).
    An end that is not timed makes the span null, not zero."""
    answers = {(row.get("payload") or {}).get("handout_event_id"): _time(row.get("at"))
               for row in events if row.get("kind") == "answer"}
    closed = [(_time(w.get("opened_at")), _time(w.get("closed_at"))) for w in waits]
    closed = [(a, b) for a, b in closed if a is not None and b is not None]
    spans: list[dict[str, Any]] = []
    start = next((row.get("at") for row in events if row.get("kind") == "run_create"), None)
    current: dict[str, Any] = {"attempts": 0, "dispatches": 0}
    dispatch: list[tuple[float, float]] = []

    def close(stage: Any, ended_at: Any) -> None:
        begin, end = _time(start), _time(ended_at)
        seconds = end - begin if begin is not None and end is not None else None
        waited = (_union_seconds([(max(a, begin), min(b, end)) for a, b in closed
                                  if b > begin and a < end])
                  if seconds is not None else None)
        spans.append({"stage": stage, "started_at": start, "ended_at": ended_at,
                      "seconds": round(seconds, 3) if seconds is not None else None,
                      "active_seconds": (round(max(0.0, seconds - waited), 3)
                                         if seconds is not None else None),
                      "dispatch_seconds": round(_union_seconds(dispatch), 3),
                      **current})

    for event in events:
        kind = event.get("kind")
        if kind == "stage_attempt":
            current["attempts"] += 1
        elif kind == "handout":
            current["dispatches"] += 1
            begin, end = _time(event.get("at")), answers.get(event.get("event_id"))
            if begin is not None and end is not None:
                dispatch.append((begin, end))
        if kind in {"stage_complete", "land"}:
            close(event.get("stage") or "deliver", event.get("at"))
            start = event.get("at")
            current = {"attempts": 0, "dispatches": 0}
            dispatch = []
    if any(current.values()):
        spans.append({"stage": "open", "started_at": start, "ended_at": None,
                      "seconds": None, "active_seconds": None,
                      "dispatch_seconds": round(_union_seconds(dispatch), 3),
                      **current})
    return spans


def _experts(events: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """One row per expert dispatch: handout to answer, and the answer's parse state."""
    answers = {(row.get("payload") or {}).get("handout_event_id"): row
               for row in events if row.get("kind") == "answer"}
    answers.pop(None, None)
    rows = []
    for handout in (row for row in events if row.get("kind") == "handout"):
        answer = answers.get(handout.get("event_id"))
        begin = _time(handout.get("at"))
        end = _time(answer.get("at")) if answer else None
        rows.append({"expert": (handout.get("payload") or {}).get("expert_id"),
                     "stage": handout.get("stage"), "round": handout.get("round"),
                     "handout_event_id": handout.get("event_id"),
                     "answer_event_id": answer.get("event_id") if answer else None,
                     "state": (answer.get("payload") or {}).get("state") if answer else None,
                     "seconds": round(end - begin, 3) if begin is not None
                     and end is not None else None})
    return rows


def _commands(test_reports: Mapping[str, Mapping[str, Any]] | None) -> dict[str, list]:
    """repo -> the commands of its TEST report: command, result, reason when given."""
    out: dict[str, list] = {}
    for repo, report in sorted((test_reports or {}).items()):
        if (report or {}).get("unreadable"):
            out[repo] = [{"command": None, "result": "unreadable",
                          "reason": str(report["unreadable"])}]
            continue
        rows = []
        for cmd in (report or {}).get("commands") or []:
            if isinstance(cmd, Mapping):
                rows.append({key: cmd[key] for key in ("command", "result", "reason")
                             if cmd.get(key) is not None})
        out[repo] = rows
    return out


def _answer_sources(event_rows) -> dict[str, int]:
    """Expert answers per ``source``; an answer from before T6 has none and is a host's."""
    out: dict[str, int] = {}
    for event in event_rows:
        if event.get("kind") == "answer":
            source = str((event.get("payload") or {}).get("source") or "host")
            out[source] = out.get(source, 0) + 1
    return out


def _validation_lines(event_rows) -> list[str]:
    from core.run_protocol.validation_report import report_lines
    return report_lines(event_rows)


def select_untracked(event_rows, read_blob) -> dict[str, Any] | None:
    """``{files: repo -> paths, reason}`` of the landing's metric(delivery_untracked),
    or None when no landing recorded one. Only the landing's own (cli) records count,
    and once a land event exists, only the record of the landing it names (its
    request_id through auth_consumed, or its stage_attempt_event_id): a later prepare or
    a failed attempt records a list of its own. Before landing, the latest record. The
    list is read from its blob (*read_blob*: digest -> bytes), never from the worktree,
    which is gone by the COMPLETED rebuild."""
    rows = [event for event in event_rows if event.get("kind") == "metric"
            and event.get("producer") == "cli"
            and (event.get("payload") or {}).get("name") == UNTRACKED_METRIC]
    if not rows:
        return None
    chosen = rows[-1]
    lands = [event.get("payload") or {} for event in event_rows if event.get("kind") == "land"]
    if lands:
        key = lands[-1].get("stage_attempt_event_id")
        consumed_id = lands[-1].get("auth_consumed_event_id")
        if consumed_id:
            key = next(((event.get("payload") or {}).get("request_id")
                        for event in event_rows if event.get("kind") == "auth_consumed"
                        and event.get("event_id") == consumed_id), None)
        chosen = next((row for row in reversed(rows)
                       if (row.get("payload") or {}).get("landing") == key), chosen)
    payload = chosen.get("payload") or {}
    reason = payload.get("reason")
    raw: Any = {}
    if payload.get("files_blob"):
        try:
            raw = json.loads(read_blob(payload["files_blob"]).decode("utf-8")).get("files") or {}
        except (OSError, ValueError, AttributeError) as exc:
            raw, reason = {}, f"the recorded list could not be read: {type(exc).__name__}"
    files = {str(repo): sorted(str(p) for p in paths)
             for repo, paths in sorted((raw if isinstance(raw, Mapping) else {}).items())
             if isinstance(paths, list) and paths}
    return {"files": files, "reason": reason}


UNTRACKED_SHOWN = 20     # per repo in REPORT; run_audit.json keeps every path


def _reports_reviewed(event_rows) -> bool:
    from core.run_protocol.validation_report import reports_reviewed
    return reports_reviewed(event_rows)


def build(*, run_id: str, projection: Mapping[str, Any], events: Sequence[Mapping[str, Any]],
          findings: Sequence[Mapping[str, Any]],
          contract: Mapping[str, Any], open_checks: Mapping[str, Any], audit_through: str,
          commits: Mapping[str, str] | None = None,
          repos_isolated: int | None = None,
          retained: Sequence[Mapping[str, Any]] = (),
          manual: Mapping[str, Any] | None = None,
          title: str | None = None,
          design: Mapping[str, Any] | None = None,
          test_reports: Mapping[str, Mapping[str, Any]] | None = None,
          lessons: Sequence[Mapping[str, Any]] = (),
          knowledge: Mapping[str, Any] | None = None,
          untracked: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """*test_reports*: repo -> the latest TEST validation report (its commands are
    listed, never re-judged); *lessons*: the processed REFLECT lessons (those kept in
    the run because their project has no DDD are listed in the REPORT); *open_checks*:
    check id -> detail of each audit-effect check that does not pass (one line in the
    Quality cell; no push verdict, XG 2026-10-26); *knowledge*: ``{status, entries,
    cited}`` of the run's knowledge.md (run-knowledge §3.5), None for a run created
    before it -- REPORT gets one line, never the recalled text; *untracked*: the
    landing's untracked files from ``select_untracked`` (REPORT lists them; no verdict
    reads them)."""
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
    waits: list[dict[str, Any]] = []
    opens: dict[tuple[str, str], tuple[float, Any]] = {}
    for event in event_rows:
        when = _time(event.get("at"))
        if when is None:
            continue
        if event.get("kind") in {"decision", "land_request"}:
            opens[("decision" if event["kind"] == "decision" else "auth",
                   event["payload"]["request_id"])] = (when, event.get("at"))
        elif event.get("kind") in {"human_answer", "auth"}:
            key = ("decision" if event["kind"] == "human_answer" else "auth",
                   event["payload"]["request_id"])
            if key in opens:
                opened, opened_at = opens.pop(key)
                waiting += max(0.0, when - opened)
                waits.append({"kind": key[0], "request_id": key[1], "opened_at": opened_at,
                              "closed_at": event.get("at"),
                              "seconds": round(max(0.0, when - opened), 3)})
    waits += [{"kind": kind, "request_id": request_id, "opened_at": opened_at,
               "closed_at": None, "seconds": None}
              for (kind, request_id), (_, opened_at) in opens.items()]
    active = max(0.0, wall - waiting) if wall is not None else None
    budget_seconds = contract.get("budget_seconds")
    speed_complete = isinstance(budget_seconds, (int, float)) and budget_seconds > 0
    # a count cap that was hit is unbounded / non-converging work: BLOCK without any
    # baseline (v13 E4/E5, conformance F18); a dispatch cap is a Token BLOCK too
    caps_hit = sorted({str(event["payload"].get("cap")) for event in event_rows
                       if event.get("kind") == "metric"
                       and str(event.get("payload", {}).get("name", "")).startswith("cap_exceeded.")})
    speed = {"verdict": ("BLOCK" if caps_hit else
                         "PASS" if speed_complete and active is not None
                         and active <= budget_seconds else "WARN"),
             "caps_exceeded": caps_hit,
             "wall_seconds": round(wall, 3) if wall is not None else None,
             "active_seconds": round(active, 3) if active is not None else None,
             "budget_seconds": budget_seconds if speed_complete else None,
             "waiting_seconds": round(waiting, 3)}

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

    # S6-1: one TEST report per repo, counted as verified when it reports passing
    # commands and no failed one (the agent's own report; review judges its content)
    from core.run_protocol.validation_report import latest as _latest_reports
    test_events = _latest_reports(event_rows, "test")
    required = len(expected)
    passed = sum(1 for name in expected
                 if (test_events.get(name) or {}).get("payload", {}).get("n_pass", 0) > 0
                 and not test_events[name]["payload"].get("n_fail"))
    # commands the agent says it did not run (its reason, not checked): never a PASS
    # (design 2026-10-26-pipeline-review-closure §4.3); a fail still blocks TEST / LG-1
    not_run = sum(int((test_events.get(name) or {}).get("payload", {}).get("n_not_run") or 0)
                  for name in expected)
    # "closed" is findings.is_closed (the closure table): a finding closed by a reported
    # test counts only once R2 judged the test ADEQUATE, a reviewed-only HIGH never
    confirmed = sum(row.get("status") == "closed_proven" and row.get("r2_status") == "ADEQUATE"
                    for row in findings)
    closed = sum(is_closed(row) for row in findings)
    landing = projection.get("landing")
    reason = _reason(projection, event_rows)
    from core.run_protocol.lifecycle import QUALITY_BLOCK_REASONS   # one list
    if reason in QUALITY_BLOCK_REASONS:
        quality_verdict = "BLOCK"
    elif landing in {"no_code", "not_landed"}:
        quality_verdict = "N/A"
    elif landing == "human_override" or passed < required or closed < len(findings) \
            or open_checks or not_run:
        quality_verdict = "WARN"
    else:
        quality_verdict = "PASS"
    quality = {"verdict": quality_verdict, "verification_passed": passed,
               "verification_required": required, "verification_not_run": not_run,
               "findings_closed": closed,
               "findings_confirmed": confirmed, "findings_total": len(findings),
               "open": _open_lines(open_checks, [row["canonical_id"] for row in findings
                                                 if push_effect(row)])}

    # answered decisions + run-land (both in the projection) + accept_finding (§E9.3, D4)
    human_decisions = int(projection.get("human_decisions") or 0) + sum(
        1 for event in event_rows if event.get("kind") == "auth_consumed"
        and event.get("payload", {}).get("action") == "accept_finding")
    approvals = sum(1 for event in event_rows if event.get("kind") == "metric"
                    and event.get("payload", {}).get("name") == "required_approval")
    # manual rescues have no producer: the count is not measured, so it is null and
    # never decides the verdict (§E9.2 "what cannot be measured is null"; spec §5)
    autonomy = {"verdict": "WARN" if human_decisions else "PASS",
                "human_decisions": human_decisions, "manual_rescues": None,
                "required_approvals": approvals}

    # Token accounting (D3). A transcript measurement (cli; the daemon takes it when
    # REFLECT ends, r6 §2.3) covers the
    # main agent AND every sub-agent of the run's transcripts: its LATEST set is the
    # token total, and the per-expert SubagentStop records (runner) are already in it,
    # so they are not added. Without one, only the experts' tokens are known.
    measured, experts, unavailable, expert_records = _token_metrics(event_rows)
    answers = sum(event.get("kind") == "answer" for event in event_rows)
    # Completeness (E5, conformance F7), counted in model SESSIONS from events, never
    # from the measurement itself: the main agent is one, and every handout an expert
    # was spawned for is one (answered or not — a crashed expert still cost a call).
    # A session is accounted when
    # a measurement covers it: the main agent by a transcript measurement, an expert
    # by its own SubagentStop usage record.
    spawned = sum(event.get("kind") == "handout" for event in event_rows)
    total_calls: int | None = 1 + spawned
    accounted = (1 if measured else 0) + min(expert_records, spawned)
    input_tokens, output_tokens = token_totals(event_rows)
    total_tokens = ((input_tokens + output_tokens)
                    if isinstance(input_tokens, (int, float)) and isinstance(output_tokens, (int, float))
                    else None)
    budget_tokens = contract.get("budget_tokens")
    from core.run_protocol.lifecycle import DISPATCH_CAPS
    if set(caps_hit) & DISPATCH_CAPS:
        token_verdict = "BLOCK"                        # model dispatches hit their cap
    elif accounted == 0 and unavailable is None:
        token_verdict = "BLOCK"                        # nothing was metered at all
    elif spawned and not expert_records and not measured:
        # the main transcript is not available, and no expert was metered either:
        # sub-agent tokens cannot be counted (v13 §E5 BLOCK)
        token_verdict = "BLOCK"
    elif accounted < total_calls or total_tokens is None \
            or not isinstance(budget_tokens, (int, float)):
        token_verdict = "WARN"
    else:
        token_verdict = "PASS" if total_tokens <= budget_tokens else "WARN"
    token = {"verdict": token_verdict, "total_tokens": total_tokens,
             "input_tokens": input_tokens, "output_tokens": output_tokens,
             "budget_tokens": budget_tokens if isinstance(budget_tokens, (int, float)) else None,
             "accounted_calls": accounted, "total_calls": total_calls,
             "expert_answers": answers, "expert_records": expert_records,
             "not_available": None if measured else unavailable}

    commit_map = dict(commits or {})
    outcome = {"status": projection.get("status"), "landing": landing,
               "reason": _reason(projection, event_rows),
               "commit": next(iter(commit_map.values()), None)}
    details = {"events": [row.get("event_id") for row in event_rows],
               "open_checks": dict(open_checks),
               "findings": [dict(row) for row in findings],
               "commits": commit_map,
               "expected_repos": list(contract.get("expected_repos") or []),
               "unsettled_checkouts": _unsettled(event_rows),
               "unclosed_high": [row["canonical_id"] for row in findings if push_effect(row)],
               # direct: the user's words are the requirement (no REQUIREMENTS doc)
               "requirement": contract.get("requirement"),
               "retained": [dict(row) for row in retained],
               "manual": dict(manual) if manual else None,
               "title": title or run_id,
               # §E9.2 (conformance F22): measured from the events only
               "critical_path": _critical_path(event_rows, waits),
               "experts": _experts(event_rows),
               # standalone T6: how each expert answer arrived (host hook / the agent)
               "answer_sources": _answer_sources(event_rows),
               "waits": waits,
               # S6-1: the agent's own validation reports (counts per stage and repo)
               "validation": _validation_lines(event_rows),
               # whether a review expert answered after reading the agent's reports
               "reports_reviewed": _reports_reviewed(event_rows),
               "trace_matrix": trace_protocol.matrix(design),
               # S6-4d: what the agent says it ran, per repo (the latest TEST report)
               "test_reports": _commands(test_reports),
               # files the landing's code tree took in only because they were untracked
               # in the worktree (shown in REPORT; no verdict reads it)
               "untracked_in_delivery": dict(untracked) if untracked else None,
               "kept_lessons": [{"id": row.get("id"), "statement": row.get("statement")}
                                for row in lessons if isinstance(row, Mapping)
                                and (row.get("outcome") or {}).get("status") == "kept_in_run"],
               # knowledge-delivery-gaps §3.1: lessons no project DDD took, in the Library
               "library_lessons": [{"id": row.get("id"), "statement": row.get("statement"),
                                    "path": str((row.get("outcome") or {}).get("result") or "")
                                    .removeprefix("library:")}
                                   for row in lessons if isinstance(row, Mapping)
                                   and (row.get("outcome") or {}).get("status") == "library"],
               "knowledge": ({"status": str(knowledge.get("status") or "none"),
                              "entries": int(knowledge.get("entries") or 0),
                              "parts": [dict(p) for p in knowledge.get("parts") or ()
                                        if isinstance(p, Mapping)],
                              "cited": sorted({str(i) for i in knowledge.get("cited") or ()})}
                             if isinstance(knowledge, Mapping) else None)}
    audit = {"run_id": run_id, "audit_through": audit_through, "card": None,
             "outcome": outcome,
             "summary": {"isolation": isolation, "speed": speed, "quality": quality,
                         "autonomy": autonomy, "token_cost": token},
             "details": details}
    if audit_through == "COMPLETED":
        audit["card"] = render(audit, fmt="card")
        audit["card_rows"] = card_rows(audit)
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
        why = token.get("not_available")
        return f"not measured ({_text(why, 120)})" if why else "not measured"
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
    confirmed = quality.get("findings_confirmed")
    opened = [str(line) for line in quality.get("open") or []]
    not_run = int(quality.get("verification_not_run") or 0)
    return (f"{quality['verification_passed']}/{quality['verification_required']} verification · "
            + (f"{not_run} not run · " if not_run else "")
            + f"{quality['findings_closed']}/{quality['findings_total']} findings closed"
            + (f" ({confirmed} test-closed, confirmed by review)" if confirmed else "")
            + (f" · {opened[0]}" if opened else "")
            + (f" · +{len(opened) - 1} more" if len(opened) > 1 else ""))


# one short sentence per open audit check, for the Quality cell (XG 2026-10-26)
OPEN_TEXT = {"RV-1a": "correctness review not answered",
             "TS-1": "test report not on the final code",
             "DS-1": "a requirement not covered by the design",
             "DC-2": "agent chose after the decision cap"}


def _open_lines(open_checks: Mapping[str, Any], unclosed_high: Sequence[str]) -> list[str]:
    lines = []
    for check_id in sorted(open_checks, key=lambda cid: (cid != "RV-2", cid)):
        if check_id == "RV-2":
            n = len(unclosed_high) or 1
            lines.append(f"{n} HIGH finding{'s' if n != 1 else ''} open")
        else:
            lines.append(OPEN_TEXT.get(check_id, f"{check_id} open"))
    return lines


def _isolation_text(row: Mapping[str, Any]) -> str:
    done = row.get("repos_isolated")
    shown = f"{done}/{row['repos_total']}" if done is not None else f"?/{row['repos_total']}"
    return f"{row['mode']} · {shown} repos · {row['contamination_count']} contamination"


# mid-line Markdown that can change meaning: emphasis, code, links/images, tables
_MD_SPECIAL = str.maketrans({c: "\\" + c for c in "\\`*_[]!|~"})


def _text(value: Any, limit: int = 300) -> str:
    """Untrusted words (the user's requirement, a person's note, a check's detail) as
    plain Markdown text (K4 r1): one line, HTML-escaped, Markdown syntax escaped, so a
    REPORT committed into a repo carries no link, image, HTML or heading it did not
    write itself."""
    flat = " ".join(str(value).split())[:limit]
    flat = flat.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return flat.translate(_MD_SPECIAL)


def _conclusion(audit: Mapping[str, Any]) -> str:
    """§B8 item 1: what was delivered and whether landing passed; else the reason."""
    outcome, details = audit["outcome"], audit.get("details") or {}
    # the sentence adds its own full stop: a requirement ending in one would read ".."
    what = _text(details.get("requirement") or f"run {audit['run_id']}", 200).rstrip(" .\u3002")
    landing = outcome.get("landing")
    agent_only = (" Test results are reported by the agent only; no review expert checked them."
                  if details.get("validation") and not details.get("reports_reviewed") else "")
    if audit.get("audit_through") == "DELIVER" and landing is None:
        return f"Delivered: {what}. Landing checks passed.{agent_only}"
    if landing in ("landed", "human_override"):
        return (f"Delivered: {what}. Landed ({'by a person' if landing == 'human_override' else 'landing checks passed'})."
                + agent_only)
    return f"Not landed: {what}. Reason: {outcome.get('reason') or landing or 'unknown'}."


def card_rows(audit: Mapping[str, Any]) -> list[dict[str, str]]:
    """The five dimension rows REPORT and the card print: ``{dim, verdict, detail}``.
    The chat shows them as rows (design 2026-10-26-turn-cards-unified.md §5.1)."""
    outcome, summary = audit["outcome"], audit["summary"]
    approvals = summary["autonomy"]["required_approvals"]
    rows = [
        ("Run Isolation", summary["isolation"], _isolation_text(summary["isolation"])),
        ("Speed", summary["speed"], _speed_text(summary["speed"])),
        ("Quality", summary["quality"], _quality_text(summary["quality"], outcome.get("reason"))),
        ("Autonomy", summary["autonomy"],
         f"{summary['autonomy']['human_decisions']} decisions · rescues not measured · "
         f"{approvals} required approval" + ("s" if approvals != 1 else "")),
        ("Token Cost", summary["token_cost"], _token_text(summary["token_cost"])),
    ]
    return [{"dim": name, "verdict": str(row["verdict"]), "detail": detail}
            for name, row, detail in rows]


def render(audit: Mapping[str, Any], *, fmt: str) -> str:
    """One renderer for REPORT and the chat card (§B8, §E9.5): the same five numbers."""
    if fmt not in {"report", "card"}:
        raise AuditError("format must be report or card")
    outcome = audit["outcome"]
    details = audit.get("details") or {}
    rows = [(r["dim"], {"verdict": r["verdict"]}, r["detail"]) for r in card_rows(audit)]
    state = f"{str(outcome.get('status') or '').upper()} · {str(outcome.get('landing') or '').upper()}"
    if outcome.get("commit"):
        state += " · " + str(outcome["commit"])[:8]
    if fmt == "card":
        return "\n".join([f"Run Audit · {audit['run_id']}", state, ""] + [
            f"{name:<14} {row['verdict']} · {detail}" for name, row, detail in rows]
            + ["", "Final audit through COMPLETED"])
    cutoff = "Final audit through COMPLETED" if audit["audit_through"] == "COMPLETED" \
        else "Audit through DELIVER"
    run_title = _text(details.get("title") or audit["run_id"], 120)
    lines = [f"# {run_title} · {cutoff}", "", _conclusion(audit), "",
             "## Run Audit", "", "| Dimension | Result | This run |", "|---|---|---|"]
    lines += [f"| {name} | {row['verdict']} | {detail} |" for name, row, detail in rows]
    known = details.get("knowledge")
    if isinstance(known, Mapping):
        parts = [p for p in known.get("parts") or () if isinstance(p, Mapping)] or \
            [{"purpose": "requirement", "status": known.get("status"),
              "entries": known.get("entries")}]
        lines += ["", "Knowledge: " + " · ".join(
            f"{_text(p.get('purpose'), 12)} {_text(p.get('status'), 20)} · "
            f"{int(p.get('entries') or 0)}" for p in parts)
            + f" · cited {len(known.get('cited') or ())}"]
    sources = details.get("answer_sources") or {}
    relayed = int(sources.get("relayed") or 0) if isinstance(sources, Mapping) else 0
    if relayed:
        total = sum(int(v or 0) for v in sources.values())
        lines += ["", f"Review answers: {relayed} of {total} relayed by the agent "
                  "(no host hook recorded them)"]
    lines += ["", "Full machine-readable audit is stored with this run."]
    if details.get("validation"):
        lines += [""] + [str(line) for line in details["validation"]]
    for repo, commands in (details.get("test_reports") or {}).items():
        if commands and commands[0].get("result") == "unreadable":
            lines += ["", f"TEST · {_text(repo, 80)}: the report could not be read ("
                      f"{_text(commands[0].get('reason'), 200)}); no commands listed"]
        elif commands:
            lines += ["", f"TEST · {_text(repo, 80)}:"]
            lines += [f"- {_text(cmd.get('command'), 200)} → {_text(cmd.get('result'), 20)}"
                      + (f" ({_text(cmd['reason'], 200)})" if cmd.get("reason") else "")
                      for cmd in commands[:10]]
            if len(commands) > 10:
                lines.append(f"- {len(commands) - 10} more in run_audit.json")
    skipped = [(repo, cmd) for repo, commands in (details.get("test_reports") or {}).items()
               for cmd in commands or () if cmd.get("result") == "not_run"]
    if skipped:
        # every one, with its reason: "red at base" is a reason like any other, and the
        # person decides whether main needs fixing first (§4.3)
        lines += ["", "## Not run (the agent's reason, not checked)", ""]
        lines += [f"- {_text(repo, 80)} · {_text(cmd.get('command'), 200)}: "
                  f"{_text(cmd.get('reason') or 'no reason given', 300)}"
                  for repo, cmd in skipped[:50]]
        if len(skipped) > 50:
            lines.append(f"- {len(skipped) - 50} more in run_audit.json")
    untracked = details.get("untracked_in_delivery") or {}
    if untracked.get("files"):
        lines += ["", "## Files delivered without being added to git", "",
                  "These files were untracked in the run's worktree (not ignored, never "
                  "added to git), so they are in this delivery. Check that they belong "
                  "before pushing: build or test output here usually means the repo's "
                  "ignore rules do not cover it.", ""]
        for repo, paths in untracked["files"].items():
            lines += [f"- {_text(repo, 80)}: {_text(path, 300)}"
                      for path in paths[:UNTRACKED_SHOWN]]
            if len(paths) > UNTRACKED_SHOWN:
                lines.append(f"- {_text(repo, 80)}: {len(paths) - UNTRACKED_SHOWN} more "
                             "in run_audit.json")
    elif untracked.get("reason"):
        lines += ["", f"Untracked files in this delivery: not listed "
                  f"({_text(untracked['reason'], 300)})"]
    library = details.get("library_lessons") or []
    if library:
        lines += ["", "## Lessons written to the shared Library", "",
                  "This project has no DDD doc, so these lessons went to the shared Library "
                  "(later sessions recall them; they are a starting point for a DDD for this "
                  "repo):", ""]
        lines += [f"- {_text(row.get('id'), 20)}: {_text(row.get('statement'), 400)}"
                  f" (`{_text(row.get('path'), 200)}`)" for row in library]
    kept = details.get("kept_lessons") or []
    if kept:
        lines += ["", "## Lessons kept in this run", "",
                  "This project has no DDD doc and no repo to file them under, so these "
                  "lessons are kept in this run's record only:", ""]
        lines += [f"- {_text(row.get('id'), 20)}: {_text(row.get('statement'), 400)}"
                  for row in kept]
    if outcome.get("reason") == "partial_land":
        landed = dict(details.get("commits") or {})
        expected = [str(name) for name in details.get("expected_repos") or []]
        missing = [name for name in expected if name not in landed]
        lines += ["", "## Only part of the change landed", ""]
        lines += [f"- landed: {name} @ {str(sha)[:12]}" for name, sha in sorted(landed.items())]
        lines += [f"- not landed: {name}" for name in missing]
        lines.append("- main now holds only part of this change: the repos above marked "
                     "not landed are not in it.")
    for name in details.get("unsettled_checkouts") or []:
        lines.append(f"- {name}: landed on the branch, but the checkout's index / REPORT was not "
                     "settled; `git status` may show differences from HEAD. No file was lost.")
    manual = details.get("manual")
    if manual:
        lines += ["", f"Landed by a person: request {manual.get('request_id')}"
                  + (f" — {_text(manual['note'])}" if manual.get("note") else "")]
    retained = details.get("retained") or []
    if retained and outcome.get("landing") not in ("landed", "human_override"):
        where = ", ".join(str(row.get("ledger") or row.get("repo_name")) for row in retained)
        lines += ["", f"The unintegrated work is saved in {where}; restore it with "
                  "`\"$SWARM_BACKEND_BIN\" pipeline-v1 restore`, or hand it to a person with "
                  "`\"$SWARM_BACKEND_BIN\" pipeline-v1 run-land --prepare`."]
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
