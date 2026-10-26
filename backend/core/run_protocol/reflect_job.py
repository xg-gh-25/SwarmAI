"""Pipeline v1 REFLECT processing (fixing spec D1, design §B7; standalone §4.6).

The agent writes its lessons during DELIVER (``reflect-prepare``); everything after the
landing happens here. The CLI command that landed the run runs it before it returns
(``finish_in_cli``), so no daemon is needed; a SwarmAI daemon still queues a run it
finds ``reflecting`` (its CLI stopped). The agent has no command for the processing:

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
  not exist goes to the shared Library, ``Knowledge/Library/pipeline-lessons/<repo>.md``
  (``reflect_library``; outcome ``library``); without a repo for it (a multi-repo run's
  lesson that names none) or when that write fails, it is kept in the run's own record
  (``kept_in_run``). Both are listed in the run's REPORT.
- ``process_run`` — under the run's operation lock, runs ``reflect.process`` with its
  attempt / time caps, records each lesson's outcome in ``artifacts/reflect.json`` and
  ends REFLECT (``reflect_complete`` or, at a cap, ``reflect_exhausted``: degraded).
- ``schedule`` — fire-and-forget on one worker thread, after a pipeline command of the
  owning session; never on the chat path.
- ``finish_in_cli`` — the CLI's end of a run: ``process_run`` (or REFLECT degraded on a
  failure), then ``RunEngine.release_landed_worktrees``.

Public symbols: ``judge``, ``route``, ``annotate_entry``, ``process_run``, ``finish_in_cli``,
``schedule``,
``reschedule_reflecting``,
``candidates_for``,
``create_reflect_trigger``.
"""
from __future__ import annotations

import json
import logging
import os
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


def _plain_name(project: str) -> bool:
    """One plain path component, checked before anything touches the file system."""
    return bool(project) and project not in (".", "..") and "/" not in project \
        and "\\" not in project and "\x00" not in project


def _project_exists(workspace: Path, project: str) -> bool:
    """A directory entry of Projects/ with exactly this name. A case-insensitive file
    system would also open `Foo` for `foo`; the name must match as written."""
    try:
        return project in os.listdir(Path(workspace) / "Projects") \
            and _project_dir(workspace, project).is_dir()
    except OSError:
        return False


def _queue(workspace: Path, project: str, run_id: str, lesson: Mapping[str, Any],
           destination: str, **extra: Any) -> str:
    """Queue *lesson* for the human-reviewed flow. The file is created under the
    project's ``.artifacts/proposals`` through a directory-fd walk that follows no
    symlink, into a new O_EXCL file (K3 review r2); an existing file is a replay only when it
    is the same proposal as a regular file, and is otherwise rejected (K3 review r3)."""
    if not _plain_name(project):
        return "rejected_unknown_project"
    root = (Path(workspace) / "Projects").resolve()
    project_dir = _project_dir(workspace, project)
    if project_dir.resolve().parent != root or project_dir.is_symlink():
        return "rejected_unknown_project"
    if not _project_exists(workspace, project):
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
        except BaseException:
            # a prefix left behind would read as a different proposal on retry
            # (rejected_integrity); this O_EXCL file is ours, remove it
            os.close(out)
            out = -1
            try:
                os.unlink(name, dir_fd=fd)
            except OSError:
                pass
            raise
        finally:
            if out >= 0:
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
          project: str, run_id: str, repos: "Mapping[str, str] | None" = None) -> str:
    """``_route``, then a lesson it would keep in the run goes to the shared Library
    (``reflect_library``, knowledge-delivery-gaps §3.1): the file of the lesson's repo
    (its ``repo`` field, or the run's only repo). *repos*: the run's repo name -> main
    checkout. Without a repo for it, or when the Library cannot be written, the lesson
    stays ``kept_in_run``."""
    result = _route(lesson, destination, workspace=workspace, project=project, run_id=run_id)
    if result != KEPT_IN_RUN or not repos:
        return result
    name = lesson.get("repo")
    if name is None and len(repos) == 1:
        name = next(iter(repos))
    if not isinstance(name, str) or name not in repos:
        return KEPT_IN_RUN
    from core.run_protocol import reflect_library
    written = reflect_library.write(workspace, repos[name], lesson, destination, run_id)
    return written or KEPT_IN_RUN


def _route(lesson: Mapping[str, Any], destination: str, *, workspace: Path,
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
        if not _plain_name(target_project):
            return "rejected_unknown_project"
        root = (Path(workspace) / "Projects").resolve()
        target = _project_dir(workspace, target_project).resolve()
        if target.parent != root:                   # never outside Projects/
            return "rejected_unknown_project"
        doc, _, section = doc_section.partition(":")
        from core.ddd_paths import ddd_path
        if not _project_exists(workspace, target_project) \
                or not (ddd_path(target, f"{doc}.md").exists()
                                       or ddd_path(target, f"{doc}.md").is_symlink()):
            # the project, or its DDD doc, does not exist: keep the lesson in the run's
            # own REFLECT record and REPORT, never drop it (§3.3.2, r8)
            return KEPT_IN_RUN
        proposal = ddd_cultivation.CultivationProposal(
            target_doc=f"{doc}.md", target_section=section,
            content=str(lesson["statement"]), source_run_id=run_id, confidence=0.8)
        result = str(ddd_cultivation.apply_to_ddd(
            proposal, _project_dir(workspace, target_project)))
        # the doc went away between the check above and the write: same as absent
        return KEPT_IN_RUN if result == "doc_missing" else result
    return _queue(workspace, project, run_id, lesson, destination)


def candidates_for(lessons: Any, project: str,
                   knowledge: "list[Mapping[str, Any]] | None" = None) -> dict[str, list[str]]:
    """Dedup candidates for each lesson, computed by the CLI so the agent cannot
    choose them (RF-4): the DDD entries recalled into this run's knowledge.md at create
    (*knowledge*, from the contract; run-knowledge §3.5) and the project's entries the
    lesson's own words recall. A candidate is ``doc#section: <the entry's first line,
    without its bullet>``, the form ``annotate_entry`` finds the entry by."""
    from core.run_protocol.run_knowledge import entry_head
    known = [f"{row.get('source')}: {str(row.get('head'))[:120]}" for row in knowledge or []
             if isinstance(row, Mapping) and row.get("source") and row.get("head")]
    out: dict[str, list[str]] = {}
    try:
        from core.recall_multi import recall_ddd
    except Exception:  # noqa: BLE001 — no recall: only knowledge.md's, RF-4 still checks shape
        logger.debug("recall unavailable for reflect candidates", exc_info=True)
        recall_ddd = None
    for row in lessons if isinstance(lessons, list) else []:
        if not isinstance(row, Mapping) or not isinstance(row.get("id"), str):
            continue
        try:
            hits = [h for h in (recall_ddd(str(row.get("statement") or ""), project, "reflect")
                                if recall_ddd else []) if h.get("kind") == "entry"]
        except Exception:  # noqa: BLE001
            logger.debug("reflect candidate recall failed lesson=%s", row.get("id"), exc_info=True)
            hits = []
        recalled = [f"{h.get('doc')}#{h.get('section')}: {entry_head(h.get('content') or '')[:120]}"
                    for h in hits if isinstance(h, Mapping)]
        out[row["id"]] = list(dict.fromkeys(recalled + known))
    return out


def process_run(engine) -> dict[str, Any] | None:
    """Process a ``reflecting`` run once. None when it is not reflecting, or when another
    process holds the run (it is processing it). Under the run's operation lock, judged
    again inside it: the CLI that landed the run and the daemon's worker never both
    route its lessons."""
    from core.run_protocol.oplock import RunBusy, operation
    if engine.projection().get("status") != "reflecting":
        return None
    try:
        with operation(engine.audit_root, engine.run_id, timeout=MAX_SECONDS + 60.0):
            if engine.projection().get("status") != "reflecting":
                return None
            return _process_run(engine)
    except RunBusy:
        return None


def finish_in_cli(engine) -> dict[str, Any] | None:
    """The CLI ends a ``reflecting`` run in the command that put it there (standalone
    §4.6): REFLECT, then the run's worktrees and branch. A failure ends REFLECT
    degraded, so the run never stays ``reflecting`` for want of a daemon. None when
    the run is not reflecting."""
    if engine.projection().get("status") != "reflecting":
        return None
    try:
        result = process_run(engine)
    except Exception as exc:  # noqa: BLE001 — never leave the run reflecting
        logger.warning("v1 reflect of %s failed in the CLI", engine.run_id, exc_info=True)
        result = None
        if engine.projection().get("status") == "reflecting":
            engine.reflect_complete(degraded=True,
                                    reason=f"reflection failed: {type(exc).__name__}")
    out: dict[str, Any] = {"reflect": engine.projection().get("reflect") or "complete",
                           "lessons": len((result or {}).get("lessons") or [])}
    if engine.projection().get("status") != "reflecting":
        try:
            rows = engine.release_landed_worktrees()
            out["released"] = [row["repo"] for row in rows if row.get("ok")]
            errors = [f"{row['repo']}: {row.get('error')}" for row in rows if not row.get("ok")]
            if errors:
                out["release_errors"] = errors
        except Exception as exc:  # noqa: BLE001 — the worktree stays; nothing is lost
            logger.warning("release of %s failed in the CLI", engine.run_id, exc_info=True)
            out["release_errors"] = [str(exc)[:200]]
    return out


def _process_run(engine) -> dict[str, Any]:
    from core.run_protocol import reflect
    path = engine.run_dir / "artifacts" / "reflect.json"
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        document = {"schema": 1, "lessons": []}
    result = reflect.process(
        document, judge=judge,
        writer=lambda lesson, dest: route(lesson, dest, workspace=engine.workspace,
                                          project=engine.project, run_id=engine.run_id,
                                          repos=dict(engine.contract.get("repo_roots") or {})),
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
