"""Pipeline REFLECT -> CDF S3 (design §5.9 run-close protocol).

``finish_in_cli`` runs inside the landing command: it enqueues the validated five-part
report deterministically (no LLM), completes REFLECT and releases the landed worktrees,
all before the command returns. Card processing happens later on the CDF worker's
dedicated ``run`` lane; nothing here waits for it. Only the trusted human-answer pass
(LLM, person-authored text only) is handed to one bounded background thread, and REFLECT
never waits for it either.
"""
from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any

logger = logging.getLogger(__name__)
MAX_SECONDS = 600.0
_POOL = ThreadPoolExecutor(max_workers=1, thread_name_prefix="cdf-s3-human")
_RUNNING: set[str] = set()
_LOCK = threading.Lock()


def _process_run(engine: Any) -> dict[str, Any]:
    """Enqueue the report, complete REFLECT; schedule the human-answer pass if any."""
    from core.ingestion.sources import s3_run
    try:
        main = s3_run.enqueue_report(engine)
    except Exception as exc:
        logger.warning("S3 enqueue failed for %s", engine.run_id, exc_info=True)
        engine.reflect_complete(degraded=True, reason=f"S3 enqueue failed: {type(exc).__name__}")
        return {"reflect": "degraded", "main": 0}
    engine.reflect_complete(degraded=False, reason="")
    if s3_run.has_human_text(engine):
        _queue_human(engine.run_id)
    return {"reflect": "ok", "main": len(main)}


def process_run(engine: Any) -> dict[str, Any] | None:
    """Process one reflecting run under its operation lock."""
    from core.run_protocol.oplock import RunBusy, operation
    if engine.projection().get("status") != "reflecting":
        return None
    try:
        with operation(engine.audit_root, engine.run_id, timeout=MAX_SECONDS):
            if engine.projection().get("status") != "reflecting":
                return None
            return _process_run(engine)
    except RunBusy:
        return None


def _human_pass(run_id: str) -> None:
    from core import pipeline_human
    from core.ingestion.sources import s3_run
    try:
        engine = pipeline_human.load_engine(run_id)
        if engine is not None:
            s3_run.extract_human_answers(engine)
    except Exception:
        logger.warning("S3 human-answer pass failed for %s", run_id, exc_info=True)
    finally:
        with _LOCK:
            _RUNNING.discard(run_id)


def _queue_human(run_id: str) -> bool:
    with _LOCK:
        if run_id in _RUNNING:
            return False
        _RUNNING.add(run_id)
    _POOL.submit(_human_pass, run_id)
    return True


def finish_in_cli(engine: Any) -> dict[str, Any] | None:
    """In the landing command: enqueue S3, complete REFLECT, release worktrees. Never
    waits for the CDF worker."""
    if engine.projection().get("status") != "reflecting":
        return None
    result = _process_run(engine)
    if engine.projection().get("status") != "reflecting":
        engine.release_landed_worktrees()
    return result


def _finish_by_id(run_id: str) -> None:
    from core import pipeline_human
    engine = pipeline_human.load_engine(run_id)
    if engine is not None:
        from core.run_protocol.oplock import RunBusy, operation
        try:
            with operation(engine.audit_root, run_id, timeout=MAX_SECONDS):
                finish_in_cli(engine)
        except RunBusy:
            return


def schedule(session_id: str) -> list[str]:
    """Finish any run of this session left reflecting (its CLI stopped after landing)."""
    from core import pipeline_human
    done = []
    for run_id in pipeline_human.owned_runs(session_id, status="reflecting"):
        try:
            _finish_by_id(run_id)
            done.append(run_id)
        except Exception:
            logger.warning("S3 finish failed for %s", run_id, exc_info=True)
    return done


def reschedule_reflecting(workspace: Any) -> list[str]:
    """After a backend restart, finish runs left reflecting."""
    from core import run_worktree
    done = []
    for path, view in run_worktree._iter_runs(workspace):
        if view.get("status") == "reflecting":
            try:
                _finish_by_id(path.parent.name)
                done.append(path.parent.name)
            except Exception:
                logger.warning("S3 finish failed for %s", path.parent.name, exc_info=True)
    return done


def create_reflect_trigger(session_context: dict):
    """Post-tool hook that schedules any newly reflecting run without waiting."""
    async def _hook(input_data: dict, tool_use_id: Any, context: Any) -> dict:
        try:
            command = ((input_data or {}).get("tool_input") or {}).get("command") or ""
            session_id = session_context.get("sdk_session_id")
            from core import pipeline_command
            if pipeline_command.mentions(command) and session_id:
                _POOL.submit(schedule, str(session_id))
        except Exception:
            logger.debug("S3 trigger failed", exc_info=True)
        return {}
    return _hook


__all__ = ["process_run", "finish_in_cli", "schedule", "reschedule_reflecting",
           "create_reflect_trigger"]
