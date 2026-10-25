"""Pipeline v1 REFLECT background processing (fixing spec D1, design §B7).

The agent writes its lessons during DELIVER (``reflect-prepare``); everything after the
landing happens here, in the daemon, never in the agent's CLI:

- ``judge`` — the existing deterministic lesson gate (``ddd_cultivation.is_noise`` and
  ``is_quality_lesson``): ``keep`` / ``noise`` / ``suspect``. No model call.
- ``route`` — writes a kept lesson by its relation (conformance F19): ``reinforces``
  adds an evidence reference after the existing entry, ``refines`` adds the revision
  after it (``annotate_entry``; the old text is kept, never copied), ``contradicts``
  is queued for review naming the conflicting entry and writes nothing. A ``new``
  lesson goes to its destination: a ``domain:<project>:<section>``
  lesson is appended to that project's DDD doc through ``ddd_cultivation.apply_to_ddd``;
  ``MEMORY`` / ``EVOLUTION`` / ``self-evolution-proposal`` lessons go to the project's
  proposal queue (``.artifacts/proposals/``) for the existing human-reviewed flow —
  this job never edits a governed context file. A lesson whose project or DDD doc does
  not exist is kept in the run's own record (``kept_in_run``) and listed in its REPORT.
- ``process_run`` — runs ``reflect.process`` with its attempt / time caps, records each
  lesson's outcome in ``artifacts/reflect.json`` and ends REFLECT as the daemon
  (``reflect_complete`` or, when a cap was hit, ``reflect_exhausted``: degraded).
- ``schedule`` — fire-and-forget on one worker thread, after a pipeline command of the
  owning session; never on the chat path.

Public symbols: ``judge``, ``route``, ``annotate_entry``, ``process_run``, ``schedule``,
``reschedule_reflecting``,
``candidates_for``,
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
# a lesson whose project (or DDD doc) does not exist: kept in artifacts/reflect.json and
# listed in the run's REPORT (design usable-by-anyone §3.3.2), not rejected
KEPT_IN_RUN = "kept_in_run"
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


def _queue(workspace: Path, project: str, run_id: str, lesson: Mapping[str, Any],
           destination: str, **extra: Any) -> str:
    """Queue *lesson* for the human-reviewed flow. The file is created under the
    project's ``.artifacts/proposals`` through a directory-fd walk that follows no
    symlink, into a new O_EXCL file (K3 review r2); an existing file is a replay only when it
    is the same proposal as a regular file, and is otherwise rejected (K3 review r3)."""
    import os
    root = (Path(workspace) / "Projects").resolve()
    project_dir = _project_dir(workspace, project)
    if project_dir.resolve().parent != root or project_dir.is_symlink():
        return "rejected_unknown_project"
    if not project_dir.is_dir():
        return KEPT_IN_RUN          # no such project: the lesson stays in the run (§3.3.2)
    # run id and lesson id are both validated shapes (run_*, L<n>)
    name = f"reflect-{run_id}-{lesson['id']}.json"
    data = json.dumps({"source_run_id": run_id, "destination": destination,
                       "lesson": dict(lesson), "status": "pending", **extra},
                      ensure_ascii=False, indent=2).encode("utf-8")
    fd = os.open(project_dir, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in (".artifacts", "proposals"):
            try:
                os.mkdir(part, 0o755, dir_fd=fd)
            except FileExistsError:
                pass
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        try:
            out = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                          0o644, dir_fd=fd)
        except FileExistsError:
            # only the same proposal, already written as a regular file, is a replay
            try:
                held = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=fd)
            except OSError:
                return "rejected_integrity"
            try:
                import stat
                same = stat.S_ISREG(os.fstat(held).st_mode) and \
                    os.read(held, len(data) + 1) == data
            finally:
                os.close(held)
            return f"queued:{name}" if same else "rejected_integrity"
        try:
            view = memoryview(data)
            while view:
                view = view[os.write(out, view):]
            os.fsync(out)
        finally:
            os.close(out)
    finally:
        os.close(fd)
    return f"queued:{name}"


def annotate_entry(workspace: Path, project: str, entry: str, note: str) -> str:
    """Add *note* as a sub-bullet right after an existing DDD entry, never copying or
    rewriting the entry. *entry* is a dedup candidate (``doc#section: text``, from
    ``candidates_for``). Returns applied / entry_missing / doc_missing / locked."""
    from core.ddd_paths import ddd_path
    from utils.file_lock import md_lock
    head, _, text = entry.partition(": ")
    doc, _, section = head.partition("#")
    root = (Path(workspace) / "Projects").resolve()
    project_dir = _project_dir(workspace, project).resolve()
    if project_dir.parent != root or not doc or not section or not text.strip():
        return "entry_missing"
    path = ddd_path(project_dir, doc)
    if not path.is_file() or path.is_symlink():
        return "doc_missing"
    if project_dir not in path.resolve().parents:
        return "entry_missing"          # never a doc outside its project
    with md_lock(path, blocking=False) as got:
        if not got:
            return "locked"
        lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
        in_section, at = False, None
        for index, line in enumerate(lines):
            if line.startswith("## "):
                in_section = line[3:].strip() == section.strip()
            elif in_section and line.startswith("- ") and line[2:].strip().startswith(
                    text.strip()):
                at = index
                break
        if at is None:
            return "entry_missing"
        end = at + 1
        while end < len(lines) and lines[end].startswith(("  ", "\t")) and lines[end].strip():
            end += 1                    # the entry's own sub-bullets stay with it
        line = "  - " + " ".join(note.split()) + "\n"   # one line, always
        if line in lines[at + 1:end]:
            return "applied"            # a replay after a crash: already written
        lines.insert(end, line)
        import os
        import uuid
        temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.reflect.tmp")
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write("".join(lines))
            os.replace(temp, path)
        finally:
            try:
                temp.unlink()
            except FileNotFoundError:
                pass
    return "applied"


def route(lesson: Mapping[str, Any], destination: str, *, workspace: Path,
          project: str, run_id: str) -> str:
    """Write one kept lesson; returns the writer's result string. Each relation to an
    existing entry has its own write (design §B7, conformance F19): ``reinforces``
    adds an evidence reference after that entry (its text is not copied),
    ``refines`` keeps the old text and adds the revision after it, ``contradicts``
    writes nothing and queues the lesson for review naming the entry it conflicts
    with; a ``new`` lesson goes to its destination.

    The destination decides the store first (K3 review r3): an entry is annotated
    only when the lesson's destination is this project's own DDD, the store its
    candidates were recalled from; a relation to any other destination is queued for
    review with the entry it relates to."""
    from core import ddd_cultivation
    relation = lesson.get("relation") if isinstance(lesson.get("relation"), Mapping) else {}
    kind, entry = relation.get("kind"), str(relation.get("entry") or "")
    own_ddd = destination.startswith(f"domain:{project}:")
    if kind in ("reinforces", "refines") and not own_ddd:
        return _queue(workspace, project, run_id, lesson, destination,
                      review=kind, relates_to=entry)
    if kind == "reinforces":
        evidence = ", ".join(str(ref)[:12] for ref in lesson.get("evidence") or [])
        return annotate_entry(workspace, project, entry,
                              f"reinforced by {run_id} {lesson['id']} (evidence {evidence})")
    if kind == "refines":
        return annotate_entry(workspace, project, entry,
                              f"refined by {run_id} {lesson['id']}: {lesson['statement']}")
    if kind == "contradicts":
        return _queue(workspace, project, run_id, lesson, destination,
                      review="contradiction", conflicts_with=entry)
    if destination.startswith("domain:"):
        _, target_project, doc_section = destination.split(":", 2)
        root = (Path(workspace) / "Projects").resolve()
        target = _project_dir(workspace, target_project).resolve()
        if target.parent != root:                   # never outside Projects/
            return "rejected_unknown_project"
        doc, _, section = doc_section.partition(":")
        from core.ddd_paths import ddd_path
        if not target.is_dir() or not (ddd_path(target, f"{doc}.md").exists()
                                       or ddd_path(target, f"{doc}.md").is_symlink()):
            # the project, or its DDD doc, does not exist: keep the lesson in the run's
            # own REFLECT record and REPORT, never drop it (§3.3.2, r8)
            return KEPT_IN_RUN
        proposal = ddd_cultivation.CultivationProposal(
            target_doc=f"{doc}.md", target_section=section,
            content=str(lesson["statement"]), source_run_id=run_id, confidence=0.8)
        return str(ddd_cultivation.apply_to_ddd(
            proposal, _project_dir(workspace, target_project)))
    return _queue(workspace, project, run_id, lesson, destination)


def candidates_for(lessons: Any, project: str) -> dict[str, list[str]]:
    """Dedup candidates for each lesson from the project's DDD entries (existing
    keyword recall), computed by the CLI so the agent cannot choose them (RF-4)."""
    out: dict[str, list[str]] = {}
    try:
        from core.recall_multi import recall_ddd_entries
    except Exception:  # noqa: BLE001 — no recall: no candidate, RF-4 still checks shape
        logger.debug("recall unavailable for reflect candidates", exc_info=True)
        return out
    for row in lessons if isinstance(lessons, list) else []:
        if not isinstance(row, Mapping) or not isinstance(row.get("id"), str):
            continue
        try:
            hits = recall_ddd_entries(str(row.get("statement") or ""), project, top_n=3)
        except Exception:  # noqa: BLE001
            logger.debug("reflect candidate recall failed lesson=%s", row.get("id"), exc_info=True)
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
    return [run_id for run_id in pipeline_human.owned_runs(session_id, status="reflecting")
            if _queue_run(run_id)]


def reschedule_reflecting(workspace: "str | Path") -> list[str]:
    """Queue every run whose SIGNED status is ``reflecting`` (daemon startup): the queue
    lives in daemon memory, so a restart would otherwise leave such a run reflecting
    until its session ran another pipeline command (re-audit ca91762f)."""
    from core import run_worktree
    return [f.parent.name for f, view in run_worktree._iter_runs(workspace)
            if view.get("status") == "reflecting" and _queue_run(f.parent.name)]


def _queue_run(run_id: str) -> bool:
    """Submit one run's REFLECT to the worker unless it is already queued."""
    with _LOCK:
        if run_id in _RUNNING:
            return False
        _RUNNING.add(run_id)

    def job(rid=run_id):
        from core import pipeline_human
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
    return True


def create_reflect_trigger(session_context: dict):
    """The PostToolUse (Bash) hook: a pipeline_v1 command may have moved a run into
    REFLECT; queue it. The run lookup itself runs on the reflect worker, never on
    the event loop: the hook only hands the session id over and returns."""
    async def _hook(input_data: dict, tool_use_id, context) -> dict:
        try:
            command = ((input_data or {}).get("tool_input") or {}).get("command") or ""
            sid = session_context.get("sdk_session_id")
            from core import pipeline_command
            if pipeline_command.mentions(command) and sid:
                _pool().submit(_schedule_logged, str(sid))
        except Exception:  # noqa: BLE001 — never fail the tool call
            logger.debug("v1 reflect trigger failed", exc_info=True)
        return {}
    return _hook


def _schedule_logged(session_id: str) -> None:
    try:
        schedule(session_id)
    except Exception:  # noqa: BLE001 — a background trigger must not raise into the pool
        logger.warning("v1 reflect schedule for %s failed", session_id, exc_info=True)
