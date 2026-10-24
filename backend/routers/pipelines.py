"""Pipeline v1 runs API router (prefix ``/api/pipelines``, registered in main.py).

Lists only runs with a protected v1 contract (``core.pipeline_metrics.is_v1``); every
value comes from the run's projection through ``core.pipeline_metrics``, and a value
the projection does not carry is left empty, never estimated.

Key endpoints:
- ``GET /``                — runs in the 30-day window (``?active=true``: live ones)
- ``GET /analytics``       — rollup, weekly trend and by-project groups
- ``GET /{run_id}``        — one run: REPORT.md, reflect lessons, commits, decision
- ``PATCH /{run_id}/cancel`` / ``/resume`` — signed abandon / resume events
- ``POST /{run_id}/decision`` — a person answers a pending decision (human-only)
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

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
    """The prompt the dashboard puts in the owner session to continue a run: it names the
    real status command (core.pipeline_command via worktree_write_gate.v1_command)."""
    from core.worktree_write_gate import v1_command
    status = v1_command(f"status --project {project} --run-id {run_id}")
    return (f"Continue pipeline run {run_id} (project {project}): run `{status}` first, "
            "then carry on from its current stage.")


def _summary(raw: dict, project: str) -> PipelineRunSummary:
    status = pm.display_status(raw)
    kind = pm.pause_kind(raw)
    done, total = pm.progress(raw)
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
        needs_you=pm.needs_you(status, kind, raw.get("reason")),
        checkpoint_reason=raw.get("reason"),
        owner_session_id=raw.get("session_id"),
        verified=(raw.get("push_ready") if status == "completed"
                  and isinstance(raw.get("push_ready"), bool) else None),
        report_path=(f"Projects/{project}/.artifacts/runs/{raw.get('id')}/REPORT.md"
                     if report.is_file() else None),
        audit_card=raw.get("audit_card"),
    )


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

    def _read():
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
        return raw, report_md, report_path, lessons, _v1_decision(engine), engine.project

    found = await executors.run_nav(_read)
    if found is None:
        return missing
    raw, report_md, report_path, lessons, decision, project = found
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
        created_at=_text(raw.get("created_at")), updated_at=_text(raw.get("updated_at")),
        audit_card=raw.get("audit_card"))
