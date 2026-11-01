"""Pipeline v1 runs API router (prefix ``/api/pipelines``, registered in main.py).

Lists only runs with a protected v1 contract (``core.pipeline_metrics.is_v1``); every
value comes from the run's projection through ``core.pipeline_metrics``, and a value
the projection does not carry is left empty, never estimated.

Key endpoints:
- ``GET /``                — runs in the 30-day window (``?active=true``: live ones)
- ``GET /analytics``       — rollup, weekly trend and by-project groups
- ``GET /{run_id}``        — one run: REPORT.md, reflect lessons, commits, decision
- ``GET /{run_id}/view``   — everything the run's big card shows, with the commands its
  state offers (``actions``; design 2026-11-01-brain-eval-and-runs §4.2)
- ``PATCH /{run_id}/cancel`` / ``/resume`` — signed abandon / resume events
- ``POST /{run_id}/decision`` — a person answers a pending decision (human-only)
- ``POST /{run_id}/confirm-landed`` — a person says an abandoned run's change is on main
  as these commits, where the engine could not show it (human-only)
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Query

from core import executors, pipeline_metrics as pm
from jobs.paths import SWARMWS
from schemas.pipeline_run import (
    PipelineAnalytics,
    PipelineCommit,
    PipelineDashboard,
    PipelineDecision,
    PipelineDecisionAnswer,
    PipelineOverall,
    PipelineProjectGroup,
    PipelineRunDetail,
    PipelineRunResponse,
    PipelineRunStatus,
    PipelineRunSummary,
    PipelineStatusSummary,
    PipelineTrendPoint,
)

logger = logging.getLogger(__name__)
router = APIRouter()

_LIVE = (PipelineRunStatus.RUNNING, PipelineRunStatus.PAUSED)


def _get_swarmws() -> Path:
    return SWARMWS


def _valid_run_id(run_id: str) -> bool:
    from core.run_protocol.identifiers import IdentifierError, validate_run_id
    try:
        validate_run_id(run_id)
        return True
    except IdentifierError:
        return False


def _text(value) -> str:
    return value if isinstance(value, str) else ""


def _normalized(state: dict, run_id: str, project: str) -> dict:
    """A projection made safe to display (R1b review): the run's identity is its
    directory, never the agent-writable ``id``/``project``; text fields that are not
    strings are dropped, so one malformed run.json cannot break the whole list."""
    out = dict(state)
    out["id"], out["_project"] = run_id, project
    for key in ("requirement", "created_at", "updated_at", "profile", "reason",
                "session_id", "audit_card", "pending_decision", "finished_at"):
        if key in out and not isinstance(out[key], str):
            out[key] = None if key in ("audit_card", "pending_decision", "finished_at",
                                       "reason", "session_id") else ""
    return out


def _load_pipeline_runs() -> list[dict]:
    """The v1 runs in the 30-day window (``run_index.recent``), newest first. Blocking
    file IO: callers run it on the nav pool. Never writes."""
    from core import run_index
    runs = []
    for r in run_index.recent(_get_swarmws()):
        run_id = r.path.parent.name
        if not isinstance(r.data, dict) or not _valid_run_id(run_id) or not pm.is_v1(run_id):
            continue
        runs.append(_normalized(r.data, run_id, r.path.parents[3].name))
    runs.sort(key=lambda r: _text(r.get("updated_at")), reverse=True)
    return runs


def _v1_decision(engine) -> Optional[PipelineDecision]:
    """The pending decision of a run waiting on a person, read from its signed events
    (the fold), as its validated event recorded it (conformance K1), or None."""
    try:
        state = engine.projection()
        request_id = state.get("pending_decision")
        if state.get("status") != "awaiting_human" or not isinstance(request_id, str):
            return None
        value = engine.decision_value(request_id)
        if not isinstance(value, dict):
            return None
        return PipelineDecision(**{key: value.get(key) for key in (
            "request_id", "kind", "question", "why_evidence_insufficient")},
            options=[row for row in value.get("options") or [] if isinstance(row, dict)])
    except Exception:  # noqa: BLE001 — a malformed document shows no card, never a 500
        logger.warning("pending decision of %s is unreadable", engine.run_id, exc_info=True)
        return None


def _status(raw: dict) -> PipelineRunStatus:
    """An unknown status is shown as failed (not live), never as running."""
    try:
        return PipelineRunStatus(pm.display_status(raw))
    except ValueError:
        return PipelineRunStatus.FAILED


def _sum(values) -> Optional[int]:
    """A token total, or None when any part is unmeasured (never a partial sum)."""
    values = list(values)
    return None if any(v is None for v in values) else sum(values)


def _to_response(raw: dict) -> PipelineRunResponse:
    done, total = pm.progress(raw)
    return PipelineRunResponse(
        id=raw.get("id", "unknown"),
        project=raw.get("_project", raw.get("project", "unknown")),
        requirement=_text(raw.get("requirement"))[:80],
        status=_status(raw),
        profile=raw.get("profile") or "",
        progress=f"{done}/{total}",
        stages_completed=done,
        stages_total=total,
        tokens_consumed=pm.tokens(raw),
        pause_kind=pm.pause_kind(raw),
        created_at=raw.get("created_at", ""),
        updated_at=raw.get("updated_at", ""),
        audit_card=raw.get("audit_card"),
    )


def _load_v1_engine(run_id: str):
    """Load a v1 engine from its protected contract and workspace identity."""
    from core import pipeline_human
    return pipeline_human.load_engine(run_id, _get_swarmws())


@router.patch("/{run_id}/cancel")
async def cancel_pipeline(run_id: str) -> dict:
    """Cancel a v1 run by appending a signed ``abandon`` event.

    A run without a v1 contract is not found.
    """
    from fastapi.responses import JSONResponse

    if not _valid_run_id(run_id):
        return JSONResponse(status_code=404, content={"status": "not_found", "run_id": run_id})

    def _cancel() -> str:
        try:
            engine = _load_v1_engine(run_id)
            if engine is None:
                return "missing"
            if engine.projection().get("status") not in ("completed", "abandoned", "failed"):
                engine.abandon(reason=pm.USER_CANCEL_REASON)
            return "cancelled"
        except Exception:  # v1 must fail closed; never rewrite its projection directly
            logger.exception("Protocol-aware cancellation failed for %s", run_id)
            return "error"

    outcome = await executors.run_nav(_cancel)
    if outcome == "cancelled":
        logger.info("Cancelled pipeline %s", run_id)
        return {"status": "cancelled", "run_id": run_id}
    if outcome == "error":
        return JSONResponse(status_code=500, content={
            "status": "error", "run_id": run_id, "detail": "v1 cancellation failed"})
    return JSONResponse(status_code=404, content={"status": "not_found", "run_id": run_id})


@router.patch("/{run_id}/resume")
async def resume_pipeline(run_id: str) -> dict:
    """Resume a paused v1 run by appending a signed ``resume`` event.

    A run without a v1 contract is not found.
    """
    from fastapi.responses import JSONResponse

    if not _valid_run_id(run_id):
        return JSONResponse(status_code=404, content={"status": "not_found", "run_id": run_id})

    def _resume() -> tuple[str, str | None]:
        try:
            engine = _load_v1_engine(run_id)
            if engine is None:
                return ("missing", None)
            state = engine.projection()
            if state.get("status") != "paused":
                return ("conflict", f"run is {state.get('status')}")
            resumed = engine.resume()
            if not resumed.get("ok"):
                return ("conflict", "worktree is still unavailable")
            return ("resumed", (resumed["state"].get("stage"), engine.owner_session()))
        except Exception:
            logger.exception("Protocol-aware resume failed for %s", run_id)
            return ("error", "v1 resume failed")

    outcome, detail = await executors.run_nav(_resume)
    if outcome == "resumed":
        stage, owner = detail
        # Nothing drives the run until its session continues it: the dashboard
        # opens owner_session_id with a "continue" prompt for the user to send.
        return {"status": "running", "run_id": run_id, "resumed_from": stage,
                "owner_session_id": owner}
    if outcome == "missing":
        return JSONResponse(status_code=404, content={"status": "not_found", "run_id": run_id})
    code = 409 if outcome == "conflict" else 500
    return JSONResponse(status_code=code, content={"status": outcome, "run_id": run_id,
                                                   "detail": detail})


@router.post("/{run_id}/decision")
async def answer_pipeline_decision(run_id: str, body: PipelineDecisionAnswer) -> dict:
    """A person answers a v1 run's pending decision from the dashboard.

    Human-only: run_owner_gate denies an agent's Bash call to this route, and the
    agent's CLI has no answer command (docs/design/pipeline-v1-human-loop.md).
    """
    from fastapi.responses import JSONResponse

    if not _valid_run_id(run_id):
        return JSONResponse(status_code=404, content={"status": "not_found", "run_id": run_id})

    def _answer():
        from core import pipeline_human
        from core.run_protocol.decision import DecisionError
        from core.run_protocol.engine import EngineError
        try:
            return ("answered", pipeline_human.answer_from_dashboard(
                run_id, body.request_id, body.option_id, _get_swarmws()))
        except LookupError:
            return ("missing", None)
        except (EngineError, DecisionError) as exc:
            return ("conflict", str(exc))
        except Exception:
            logger.exception("Dashboard decision answer failed for %s", run_id)
            return ("error", "v1 decision answer failed")

    outcome, detail = await executors.run_nav(_answer)
    if outcome == "answered":
        return {"status": "answered", "run_id": run_id,
                "option_id": detail["answer"]["option_id"],
                "owner_session_id": detail["owner_session_id"]}
    if outcome == "missing":
        return JSONResponse(status_code=404, content={"status": "not_found", "run_id": run_id})
    code = 409 if outcome == "conflict" else 500
    return JSONResponse(status_code=code, content={"status": outcome, "run_id": run_id,
                                                   "detail": detail})


@router.get("", response_model=PipelineDashboard)
async def list_pipelines(
    active: Optional[bool] = Query(None, description="If true, only running/paused"),
) -> PipelineDashboard:
    """All v1 runs: every live one plus the newest 5 finished per project. Always 200."""
    all_runs = await executors.run_nav(_load_pipeline_runs)
    if not all_runs:
        return PipelineDashboard()
    responses = [_to_response(raw) for raw in all_runs]
    live = [r for r in responses if r.status in _LIVE]
    if active:
        responses = live
    else:
        seen: dict[str, int] = {}
        finished = []
        for r in responses:
            if r.status in _LIVE:
                continue
            if seen.get(r.project, 0) < 5:
                finished.append(r)
                seen[r.project] = seen.get(r.project, 0) + 1
        responses = live + finished
    summary = PipelineStatusSummary(
        running=sum(1 for r in responses if r.status == PipelineRunStatus.RUNNING),
        paused=sum(1 for r in responses if r.status == PipelineRunStatus.PAUSED),
        completed=sum(1 for r in responses if r.status == PipelineRunStatus.COMPLETED),
        abandoned=sum(1 for r in responses if r.status == PipelineRunStatus.ABANDONED),
        total_tokens=_sum(r.tokens_consumed for r in responses),
    )
    return PipelineDashboard(pipelines=responses, count=len(responses), summary=summary)


def _iso_week_start(iso_ts: str) -> Optional[str]:
    """Monday (YYYY-MM-DD) of the ISO week containing iso_ts. None if unparseable."""
    try:
        d = datetime.fromisoformat(iso_ts)
        return (d - timedelta(days=d.weekday())).date().isoformat()
    except (ValueError, TypeError):
        return None


def continue_prompt(run_id: str, project: str) -> str:
    """The prompt the dashboard puts in the owner session to continue a run
    (core.pipeline_human.continue_prompt, which Swarm Out's bridge uses too)."""
    from core.pipeline_human import continue_prompt as _continue_prompt
    return _continue_prompt(run_id, project)


def _summary(raw: dict, project: str) -> PipelineRunSummary:
    status = pm.display_status(raw)
    kind = pm.pause_kind(raw)
    done, total = pm.progress(raw)
    # an abandoned run whose change is on main all the same (design 2026-10-31 §3)
    landed_as = raw.get("landed_as") if isinstance(raw.get("landed_as"), dict) else None
    report = _get_swarmws() / "Projects" / project / ".artifacts" / "runs" / raw["id"] \
        / "REPORT.md"
    return PipelineRunSummary(
        id=raw.get("id", "unknown"),
        requirement=_text(raw.get("requirement"))[:100],
        status=status,
        profile=raw.get("profile") or "",
        progress=f"{done}/{total}",
        cycle_time_min=pm.duration_minutes(raw),
        tokens_actual=pm.tokens(raw),
        created_at=raw.get("created_at", ""),
        updated_at=raw.get("updated_at", ""),
        pause_kind=kind,
        needs_you=pm.needs_you(status, kind, raw.get("reason"), landed_as),
        landed_as=landed_as,
        checkpoint_reason=raw.get("reason"),
        owner_session_id=raw.get("session_id"),
        not_landed=status == "completed" and raw.get("landing") == "not_landed",
        report_path=(f"Projects/{project}/.artifacts/runs/{raw.get('id')}/REPORT.md"
                     if report.is_file() else None),
        audit_card=raw.get("audit_card"),
        **_card_facts(raw, status),
    )


def _num(v: Any) -> Optional[float]:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _card_facts(raw: dict, status: str) -> dict[str, Any]:
    """What a Run's card shows beside a Hive run's, from the projection (run.json): each
    None when the run never recorded it (design 2026-10-30-brain-runs-one-card)."""
    summary = raw.get("audit_summary") if isinstance(raw.get("audit_summary"), dict) else {}
    usage = raw.get("usage") if isinstance(raw.get("usage"), dict) else {}
    billed = pm.tokens(raw)
    # times a person stepped in: answers, corrections (messages into the run's session
    # while it ran) and rescues; approvals and review are oversight, not counted
    # (core.run_interventions; design 2026-11-01-brain-eval-and-runs §3)
    from core import run_interventions
    try:
        interventions = run_interventions.for_run(raw)
    except Exception:  # noqa: BLE001 -- a card fact never fails the summary
        interventions = None
    human = interventions["total"] if interventions else None
    attempts = raw.get("stage_attempts") if isinstance(raw.get("stage_attempts"), dict) else None
    rework = (sum(max(0, int(v) - 1) for v in attempts.values() if isinstance(v, int))
              if attempts else None)
    speed = summary.get("speed") if isinstance(summary.get("speed"), dict) else {}
    landing = raw.get("landing") if isinstance(raw.get("landing"), str) else None
    rows = raw.get("audit_rows") if isinstance(raw.get("audit_rows"), list) else []
    return {
        "finished_at": raw.get("finished_at") if isinstance(raw.get("finished_at"), str) else None,
        "active_s": _num(speed.get("active_seconds")),
        "billed_tokens": billed,
        "cost_usd": _num(usage.get("book_cost_usd")),
        "human_decisions": human,
        "interventions": interventions,
        "landing": landing,
        "not_landed_reason": (_text(raw.get("reason"))[:200] or None)
        if status == "completed" and landing == "not_landed" else None,
        "rework": rework,
        "audit": [{"dim": _text(r.get("dim"))[:40], "verdict": _text(r.get("verdict"))[:16],
                   "detail": _text(r.get("detail"))[:200]}
                  for r in rows if isinstance(r, dict) and r.get("dim")][:8],
    }


@router.get("/analytics", response_model=PipelineAnalytics)
async def pipeline_analytics(
    window: str = Query("30d", description="Trend window: always 30d (kept for old clients)"),
) -> PipelineAnalytics:
    """Overall rollup + weekly trend + by-project groups over the v1 runs of the
    30-day window. Each project lists its newest 20 runs plus every run that needs
    you. Always 200."""
    all_runs = await executors.run_nav(_load_pipeline_runs)

    def _build() -> list[tuple[dict, PipelineRunSummary, str]]:
        return [(raw, _summary(raw, raw.get("_project", "unknown")),
                 raw.get("_project", "unknown")) for raw in all_runs]

    rows = await executors.run_nav(_build)
    by_project: dict[str, list[PipelineRunSummary]] = {}
    trend: dict[str, dict] = {}
    profile_mix: dict[str, int] = {}
    cycles: list[float] = []
    completed = aborted = 0
    for raw, s, project in rows:
        by_project.setdefault(project, []).append(s)
        profile_mix[s.profile] = profile_mix.get(s.profile, 0) + 1
        completed += s.status == "completed"
        aborted += s.needs_you
        if s.cycle_time_min is not None:
            cycles.append(s.cycle_time_min)
        week = _iso_week_start(_text(raw.get("created_at")))
        if week:
            b = trend.setdefault(week, {"runs": 0, "completed": 0, "cycles": [], "tokens": []})
            b["runs"] += 1
            b["tokens"].append(s.tokens_actual)
            b["completed"] += s.status == "completed"
            if s.cycle_time_min is not None:
                b["cycles"].append(s.cycle_time_min)

    def _avg(xs: list[float]) -> Optional[float]:
        return round(sum(xs) / len(xs), 1) if xs else None

    groups = []
    for project, summaries in sorted(by_project.items()):
        summaries.sort(key=lambda s: s.updated_at or s.created_at, reverse=True)
        newest = summaries[:20]
        visible = newest + [s for s in summaries[20:] if s.needs_you]
        done = sum(1 for s in summaries if s.status == "completed")
        groups.append(PipelineProjectGroup(
            project=project, run_count=len(summaries),
            completion_rate=round(done / len(summaries), 3),
            avg_cycle_min=_avg([s.cycle_time_min for s in summaries
                                if s.cycle_time_min is not None]),
            aborted_count=sum(1 for s in summaries if s.needs_you), runs=visible))
    groups.sort(key=lambda g: g.run_count, reverse=True)
    total = len(rows)
    return PipelineAnalytics(
        window="30d",
        overall=PipelineOverall(
            total_runs=total, completed=completed,
            completion_rate=round(completed / total, 3) if total else 0.0,
            avg_cycle_min=_avg(cycles),
            tokens_actual=_sum(s.tokens_actual for _raw, s, _p in rows) if rows else None,
            profile_mix=profile_mix, aborted_count=aborted),
        trend=[PipelineTrendPoint(week=wk, runs=b["runs"], completed=b["completed"],
                                  avg_cycle_min=_avg(b["cycles"]), tokens=_sum(b["tokens"]))
               for wk, b in sorted(trend.items())],
        by_project=groups)


@router.get("/{run_id}", response_model=PipelineRunDetail)
async def pipeline_run_detail(run_id: str):
    """One v1 run: REPORT.md, reflect lessons, landed commits and the pending
    decision. The run dir is the contract's (never a path built from the request)."""
    from fastapi.responses import JSONResponse
    missing = JSONResponse(status_code=404, content={"status": "not_found", "run_id": run_id})
    if not _valid_run_id(run_id):
        return missing

    found = await executors.run_nav(_read_detail, run_id)
    if found is None:
        return missing
    return _detail_model(run_id, *found[:6])


def _read_detail(run_id: str):
    """(raw, report_md, report_path, lessons, decision, project, engine) of a v1 run, or
    None. Blocking file IO: callers run it on the nav pool."""
    if not pm.is_v1(run_id):
        return None
    engine = _load_v1_engine(run_id)
    if engine is None:
        return None
    run_dir = engine.run_dir
    try:
        raw = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    report_md, report_path = "", None
    report = run_dir / "REPORT.md"
    if report.is_file():
        try:
            report_md = report.read_text(encoding="utf-8")
            report_path = str(report.relative_to(_get_swarmws()))
        except (OSError, ValueError):
            pass
    try:
        reflect = json.loads((run_dir / "artifacts" / "reflect.json").read_text(
            encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        reflect = {}
    lessons = reflect.get("lessons") if isinstance(reflect, dict) else None
    lessons = [str(item.get("lesson") or item.get("text") or item)
               if isinstance(item, dict) else str(item)
               for item in (lessons if isinstance(lessons, list) else [])]
    raw = _normalized(raw if isinstance(raw, dict) else {}, engine.run_id, engine.project)
    return raw, report_md, report_path, lessons, _v1_decision(engine), engine.project, engine


def _detail_model(run_id, raw, report_md, report_path, lessons, decision, project) -> PipelineRunDetail:

    landed_as = raw.get("landed_as") if isinstance(raw.get("landed_as"), dict) else None
    return PipelineRunDetail(
        id=run_id, project=project, requirement=_text(raw.get("requirement")),
        continue_prompt=continue_prompt(run_id, project),
        status=pm.display_status(raw), profile=raw.get("profile") or "",
        cycle_time_min=pm.duration_minutes(raw), report_md=report_md,
        report_path=report_path, reflect_lessons=lessons,
        commits=[PipelineCommit(repo=c.get("repo", ""), sha=c.get("sha", ""),
                                files=list(c.get("files", [])))
                 for c in raw.get("commits") or [] if isinstance(c, dict)],
        checkpoint_reason=raw.get("reason"), pause_kind=pm.pause_kind(raw),
        decision=decision, owner_session_id=raw.get("session_id"),
        needs_you=pm.needs_you(pm.display_status(raw), pm.pause_kind(raw), raw.get("reason"),
                               landed_as),
        landed_as=landed_as,
        created_at=_text(raw.get("created_at")), updated_at=_text(raw.get("updated_at")),
        audit_card=raw.get("audit_card"))


# ── the run's big card (design 2026-11-01-brain-eval-and-runs §4.2) ─────────────────

#: a pause a person resumes (Stop, a closed tab, a worktree that failed); a run waiting
#: for a decision is answered, an idle one is continued in its session
_RESUMABLE = ("stopped", "worktree")


def run_actions(summary: PipelineRunSummary, decision: Optional[PipelineDecision]) -> list[dict]:
    """The commands a local run's state offers, in order, the first one primary when it
    asks something of the person (the frontend renders these and decides nothing; the
    Hive side is ``core.swarm_out_settle.derive``). Only existing routes: answer
    (``/decision``), resume, continue (its session), confirm landed (``/confirm-landed``,
    an abandoned run nobody cancelled whose change may be on main), cancel, open session."""
    out: list[dict] = []
    if decision is not None:
        out.append({"key": "answer", "primary": True})
    elif summary.pause_kind == "decision" and summary.owner_session_id:
        # the question could not be read here: its chat card can still answer it
        out.append({"key": "open_session", "primary": True})
    if summary.pause_kind in _RESUMABLE:
        out.append({"key": "resume", "primary": not out})
    if summary.pause_kind == "idle":
        out.append({"key": "continue", "primary": not out})
    if summary.status == "abandoned" and summary.needs_you:
        out.append({"key": "confirm_landed", "primary": not out})
    if summary.status in ("running", "paused"):
        out.append({"key": "cancel", "primary": False})
    if summary.owner_session_id and not any(a["key"] == "open_session" for a in out):
        out.append({"key": "open_session", "primary": False})
    return out


def _acceptance(run_dir: Path) -> list[dict]:
    """The run's acceptance criteria from its REQUIREMENTS (``requirements[].acceptance``):
    ``{id, text}`` each, "when <condition>: <response>"; [] when it wrote none."""
    try:
        doc = json.loads((run_dir / "artifacts" / "requirements.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    out = []
    for req in (doc.get("requirements") if isinstance(doc, dict) else None) or []:
        for ac in (req.get("acceptance") if isinstance(req, dict) else None) or []:
            if not isinstance(ac, dict):
                continue
            cond, resp = _text(ac.get("condition")).strip(), _text(ac.get("response")).strip()
            text = f"{cond} → {resp}" if cond and resp else (resp or cond)
            if text:
                out.append({"id": _text(ac.get("id"))[:20], "text": text[:400]})
    return out[:30]


def _stages(raw: dict) -> list[dict]:
    """Each stage of the run's profile: its status and how many attempts it took."""
    from core.run_protocol import lifecycle
    stages = raw.get("stages") if isinstance(raw.get("stages"), dict) else {}
    attempts = raw.get("stage_attempts") if isinstance(raw.get("stage_attempts"), dict) else {}
    return [{"name": name, "status": stages.get(name) if isinstance(stages.get(name), str) else None,
             "attempts": attempts.get(name) if isinstance(attempts.get(name), int) else None}
            for name in lifecycle.PROFILE_STAGES.get(str(raw.get("profile") or ""), ())]


@router.get("/{run_id}/view")
async def pipeline_run_view(run_id: str):
    """Everything a local run's big card shows, in one read: the row's facts
    (``summary``), the retrospective (``detail``), the stages with their attempts, each
    time a person stepped in, the acceptance criteria and the commands its state offers.
    The run dir is the contract's (never a path built from the request)."""
    from fastapi.responses import JSONResponse
    missing = JSONResponse(status_code=404, content={"status": "not_found", "run_id": run_id})
    if not _valid_run_id(run_id):
        return missing

    def _read():
        found = _read_detail(run_id)
        if found is None:
            return None
        raw, report_md, report_path, lessons, decision, project, engine = found
        from core import run_interventions

        def question(request_id: str):
            value = engine.decision_value(request_id)
            return value.get("question") if isinstance(value, dict) else None
        try:
            events = engine.store.read_events()
            steps = run_interventions.details(raw, events, question=question)
        except Exception:  # noqa: BLE001 -- the list behind the counts never fails the card
            logger.warning("interventions of %s are unreadable", run_id, exc_info=True)
            steps = None
        summary = _summary(raw, project)
        return {
            "summary": summary.model_dump(),
            "detail": _detail_model(run_id, raw, report_md, report_path, lessons, decision,
                                    project).model_dump(),
            "stages": _stages(raw),
            "interventions": steps,
            "acceptance": _acceptance(engine.run_dir),
            "actions": run_actions(summary, decision),
        }

    found = await executors.run_nav(_read)
    return missing if found is None else found


@router.post("/{run_id}/confirm-landed")
async def confirm_landed(run_id: str, body: dict) -> dict:
    """A person says this abandoned run's change is on main as ``body.commits``
    (repo -> commit; a single-repo run may give ``{"": commit}``), where the engine
    could not show it. Human-only: run_owner_gate refuses this route from a command
    (``_HUMAN_ONLY_API_RE``); an agent asks the person through ``landed-as --prepare``
    and ``--request`` in chat."""
    from fastapi.responses import JSONResponse
    if not _valid_run_id(run_id):
        return JSONResponse(status_code=404, content={"status": "not_found", "run_id": run_id})
    commits = body.get("commits") if isinstance(body, dict) else None
    if not isinstance(commits, dict) or not commits or not all(
            isinstance(k, str) and isinstance(v, str) and v.strip() and len(v) <= 80
            for k, v in commits.items()):
        return JSONResponse(status_code=422, content={"status": "invalid", "run_id": run_id,
                                                      "detail": "commits: repo -> commit"})

    def _confirm():
        from core.run_protocol.engine import EngineError
        try:
            engine = _load_v1_engine(run_id)
            if engine is None:
                return ("missing", None)
            return ("confirmed", engine.confirm_landed_by_person(
                {k: v.strip() for k, v in commits.items()})["landed_as"])
        except EngineError as exc:
            return ("conflict", str(exc)[:400])
        except Exception:
            logger.exception("confirm-landed failed for %s", run_id)
            return ("error", "confirm-landed failed")

    outcome, detail = await executors.run_nav(_confirm)
    if outcome == "confirmed":
        return {"status": "landed", "run_id": run_id, "landed_as": detail}
    if outcome == "missing":
        return JSONResponse(status_code=404, content={"status": "not_found", "run_id": run_id})
    code = 409 if outcome == "conflict" else 500
    return JSONResponse(status_code=code, content={"status": outcome, "run_id": run_id,
                                                   "detail": detail})
