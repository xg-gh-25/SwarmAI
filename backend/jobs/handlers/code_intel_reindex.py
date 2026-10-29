"""Code Intelligence refresh job (hourly, ``code-intel-reindex``).

Every graph a project owns is brought to its repo's HEAD through
``core.code_intel.build.ensure_fresh``, the same path a reader takes: a repo whose
HEAD did not move costs one ``git rev-parse``; up to 100 changed files are updated
in this process; more, or a graph not built yet, start a detached rebuild child. The repo's documents follow
(``doc_index.sync_repo_after_head_move``). It runs as a job (a separate process),
never in the daemon (design 2026-10-29-code-intel-unification §0.2).

Standalone script: python -m jobs.handlers.code_intel_reindex

Hang diagnostics: ``main`` arms ``faulthandler.dump_traceback_later`` 10s
before the job's wall-clock budget (``SWARM_JOB_TIMEOUT_S``, set by the
executor's ``_handle_script``) so a stuck run dumps every thread's stack to
stderr before it is killed; the executor keeps that stderr tail in the failed
JobResult. Per-phase timings are also printed to stderr.

Key symbols: ``reindex_projects``, ``main``, ``_dump_after_seconds``.
"""

from __future__ import annotations

import faulthandler
import logging
import os
import sys
import time
from pathlib import Path

logger = logging.getLogger("swarm.jobs.code_intel_reindex")

_DEFAULT_JOB_TIMEOUT_S = 120


def _dump_after_seconds() -> int:
    """Seconds after which to dump stacks: job budget minus 10s, floor 5s."""
    raw = os.environ.get("SWARM_JOB_TIMEOUT_S", str(_DEFAULT_JOB_TIMEOUT_S))
    try:
        budget = int(float(raw))
    except (TypeError, ValueError):
        logger.warning(
            "Invalid SWARM_JOB_TIMEOUT_S=%r, assuming %ss", raw, _DEFAULT_JOB_TIMEOUT_S,
        )
        budget = _DEFAULT_JOB_TIMEOUT_S
    return max(5, budget - 10)


def _phase(project: str, phase: str, started: float) -> float:
    """Print one phase timing line to stderr (flushed, survives a kill).

    Returns a fresh ``time.monotonic()`` mark for the next phase.
    """
    now = time.monotonic()
    print(
        f"[code_intel_reindex] {project}: {phase} {now - started:.2f}s",
        file=sys.stderr, flush=True,
    )
    return now


def reindex_projects(full: bool = False) -> dict:
    """Bring every owned graph of every project to its repo's HEAD.

    *full*: rebuild every graph (``ensure_graph(full=True)``) instead — for a person
    asking for it from the command line; the schedule never passes it.
    Returns ``{"status": "success", "projects": [{project, repo, status, ...}]}``.
    """
    from core.code_intel import load_project_graphs, owned_graphs
    from core.code_intel.build import ensure_fresh, ensure_graph, spawn_build

    from config import get_swarmws
    projects_dir = get_swarmws() / "Projects"
    if not projects_dir.is_dir():
        return {"status": "skipped", "reason": "no Projects/ directory"}

    results: list[dict] = []
    for project_dir in sorted(projects_dir.iterdir()):
        if not project_dir.is_dir() or not owned_graphs(project_dir):
            continue
        name = project_dir.name
        mark = time.monotonic()
        if full:
            for row in ensure_graph(project_dir, full=True):
                results.append({"project": name, "repo": row["repo"], "status": row["action"],
                                **({"error": row["error"]} if row.get("error") else {})})
            _phase(name, "rebuild", mark)
            continue
        # A graph not built yet (a Confirm whose build child failed) is built by a
        # detached child: a first build can outlast this job's budget.
        for ref in owned_graphs(project_dir):
            if ref.db_path.exists() or ref.repo_root is None or not Path(ref.repo_root).is_dir():
                continue
            try:
                spawn_build(name, repo=ref.repo, only_if_stale=True)
            except OSError as exc:
                results.append({"project": name, "repo": ref.repo, "status": "error",
                                "error": type(exc).__name__})
                continue
            results.append({"project": name, "repo": ref.repo, "status": "rebuilding"})
        for ref, graph in load_project_graphs(name):
            try:
                fr = ensure_fresh(project_dir, ref, graph)
            except Exception as exc:  # noqa: BLE001 — one graph must not stop the others
                # e.g. "database is locked" past the busy timeout
                logger.warning("code_intel %s: refresh of %s failed: %s: %s", name,
                               ref.repo, type(exc).__name__, exc)
                results.append({"project": name, "repo": ref.repo, "status": "error",
                                "error": type(exc).__name__})
                continue
            results.append({"project": name, "repo": ref.repo, "status": fr.state,
                            "changed": len(fr.changed),
                            **({"message": fr.message} if fr.message else {})})
        _phase(name, "refresh", mark)
    return {"status": "success", "projects": results}


def main(argv: list[str] | None = None) -> dict:
    """Script entrypoint: reindex under a pre-timeout stack-dump watchdog.

    ``dump_traceback_later(exit=False)`` only DUMPS (all threads, to stderr) —
    it never kills; the executor's wall clock still owns termination. The
    dump is always cancelled in ``finally`` so a run that finishes (or raises)
    early never emits a spurious traceback.
    """
    args = sys.argv[1:] if argv is None else argv
    full_mode = "--full" in args
    dump_after = _dump_after_seconds()
    faulthandler.dump_traceback_later(dump_after, exit=False)
    print(
        f"[code_intel_reindex] start full={full_mode} stack_dump_at={dump_after}s",
        file=sys.stderr, flush=True,
    )
    started = time.monotonic()
    try:
        summary = reindex_projects(full=full_mode)
    finally:
        faulthandler.cancel_dump_traceback_later()
        _phase("all", "total", started)
    logger.info("Code intel reindex complete: %s", summary)
    return summary


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
