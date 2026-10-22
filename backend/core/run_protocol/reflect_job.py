"""Pipeline v1 REFLECT background processing (fixing spec D1, design §B7).

The agent writes its lessons during DELIVER (``reflect-prepare``); everything after the
landing happens here, in the daemon, never in the agent's CLI:

- ``judge`` — the existing deterministic lesson gate (``ddd_cultivation.is_noise`` and
  ``is_quality_lesson``): ``keep`` / ``noise`` / ``suspect``. No model call.
- ``route`` — writes a kept lesson to its destination: a ``domain:<project>:<section>``
  lesson is appended to that project's DDD doc through ``ddd_cultivation.apply_to_ddd``;
  ``MEMORY`` / ``EVOLUTION`` / ``self-evolution-proposal`` lessons go to the project's
  proposal queue (``.artifacts/proposals/``) for the existing human-reviewed flow —
  this job never edits a governed context file.
- ``process_run`` — runs ``reflect.process`` with its attempt / time caps, records each
  lesson's outcome in ``artifacts/reflect.json`` and ends REFLECT as the daemon
  (``reflect_complete`` or, when a cap was hit, ``reflect_exhausted``: degraded).
- ``schedule`` — fire-and-forget on one worker thread, after a pipeline command of the
  owning session; never on the chat path.

Public symbols: ``judge``, ``route``, ``process_run``, ``schedule``, ``candidates_for``,
``create_reflect_trigger``.
"""
from __future__ import annotations

import json
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Mapping

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 2
MAX_SECONDS = 60.0
_POOL: list[ThreadPoolExecutor] = []
_RUNNING: set[str] = set()
_LOCK = threading.Lock()


def judge(lesson: Mapping[str, Any]) -> str:
    """keep | noise | suspect, from the shared lesson gates (deterministic)."""
    from core import ddd_cultivation
    text = str(lesson.get("statement") or "")
    noisy, _reason = ddd_cultivation.is_noise(text)
    if noisy:
        return "noise"
    return "keep" if ddd_cultivation.is_quality_lesson(text) else "suspect"


def _project_dir(workspace: Path, project: str) -> Path:
    return Path(workspace) / "Projects" / project


def route(lesson: Mapping[str, Any], destination: str, *, workspace: Path,
          project: str, run_id: str) -> str:
    """Write one kept lesson; returns the writer's result string."""
    from core import ddd_cultivation
    if destination.startswith("domain:"):
        _, target_project, doc_section = destination.split(":", 2)
        root = (Path(workspace) / "Projects").resolve()
        target = _project_dir(workspace, target_project).resolve()
        if target.parent != root or not target.is_dir():   # never outside Projects/
            return "rejected_unknown_project"
        doc, _, section = doc_section.partition(":")
        proposal = ddd_cultivation.CultivationProposal(
            target_doc=f"{doc}.md", target_section=section,
            content=str(lesson["statement"]), source_run_id=run_id, confidence=0.8)
        return str(ddd_cultivation.apply_to_ddd(
            proposal, _project_dir(workspace, target_project)))
    queue = _project_dir(workspace, project) / ".artifacts" / "proposals"
    queue.mkdir(parents=True, exist_ok=True)
    # run id and lesson id are both validated shapes (run_*, L<n>)
    path = queue / f"reflect-{run_id}-{lesson['id']}.json"
    path.write_text(json.dumps({"source_run_id": run_id, "destination": destination,
                                "lesson": dict(lesson), "status": "pending"},
                               ensure_ascii=False, indent=2), encoding="utf-8")
    return f"queued:{path.name}"


def candidates_for(lessons: Any, project: str) -> dict[str, list[str]]:
    """Dedup candidates for each lesson from the project's DDD entries (existing
    keyword recall), computed by the CLI so the agent cannot choose them (RF-4)."""
    out: dict[str, list[str]] = {}
    try:
        from core.recall_multi import recall_ddd_entries
    except Exception:  # noqa: BLE001 — no recall: no candidate, RF-4 still checks shape
        return out
    for row in lessons if isinstance(lessons, list) else []:
        if not isinstance(row, Mapping) or not isinstance(row.get("id"), str):
            continue
        try:
            hits = recall_ddd_entries(str(row.get("statement") or ""), project, top_n=3)
        except Exception:  # noqa: BLE001
            hits = []
        out[row["id"]] = [f"{h.get('doc')}#{h.get('section')}: {str(h.get('content'))[:120]}"
                          for h in hits if isinstance(h, Mapping)]
    return out


def process_run(engine) -> dict[str, Any] | None:
    """Process a ``reflecting`` run once (daemon only). None when it is not reflecting."""
    from core.run_protocol import reflect
    if engine.projection().get("status") != "reflecting":
        return None
    path = engine.run_dir / "artifacts" / "reflect.json"
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        document = {"schema": 1, "lessons": []}
    result = reflect.process(
        document, judge=judge,
        writer=lambda lesson, dest: route(lesson, dest, workspace=engine.workspace,
                                          project=engine.project, run_id=engine.run_id),
        max_attempts=MAX_ATTEMPTS, max_seconds=MAX_SECONDS)
    for row in result["lessons"]:      # RF-5 reads the judge's verdict
        row["judge"] = (row.get("outcome") or {}).get("status")
    path.parent.mkdir(parents=True, exist_ok=True)   # a run that never prepared lessons
    path.write_text(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2),
                    encoding="utf-8")
    degraded = result["reflect"] == "degraded"
    engine.reflect_complete(degraded=degraded,
                            reason="background reflection hit its cap" if degraded else "")
    return result


def _pool() -> ThreadPoolExecutor:
    if not _POOL:
        _POOL.append(ThreadPoolExecutor(max_workers=1, thread_name_prefix="v1-reflect"))
    return _POOL[0]


def schedule(session_id: str) -> list[str]:
    """Queue REFLECT for this session's runs that are ``reflecting`` (fire-and-forget)."""
    from core import pipeline_human
    queued = []
    for run_id in pipeline_human.owned_runs(session_id, status="reflecting"):
        with _LOCK:
            if run_id in _RUNNING:
                continue
            _RUNNING.add(run_id)

        def job(rid=run_id):
            engine = None
            try:
                engine = pipeline_human.load_engine(rid)
                if engine is not None:
                    process_run(engine)
            except Exception:  # noqa: BLE001 — never leave the run reflecting forever
                logger.warning("v1 reflect of %s failed", rid, exc_info=True)
                try:
                    if engine is not None and engine.projection().get("status") == "reflecting":
                        engine.reflect_complete(degraded=True,
                                                reason="background reflection failed")
                except Exception:  # noqa: BLE001
                    logger.warning("v1 reflect of %s could not end degraded", rid,
                                   exc_info=True)
            finally:
                with _LOCK:
                    _RUNNING.discard(rid)
        _pool().submit(job)
        queued.append(run_id)
    return queued


def create_reflect_trigger(session_context: dict):
    """The PostToolUse (Bash) hook: a pipeline_v1 command may have moved a run into
    REFLECT; queue it. Stat-light (run.json reads) and off the chat path."""
    async def _hook(input_data: dict, tool_use_id, context) -> dict:
        try:
            command = ((input_data or {}).get("tool_input") or {}).get("command") or ""
            sid = session_context.get("sdk_session_id")
            if "pipeline_v1" in command and sid:
                schedule(str(sid))
        except Exception:  # noqa: BLE001 — never fail the tool call
            logger.debug("v1 reflect trigger failed", exc_info=True)
        return {}
    return _hook
