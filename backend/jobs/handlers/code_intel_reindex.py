"""Code Intelligence Reindex Handler.

Triggered by event-driven jobs:
- on:git_commit → incremental reindex (changed files only)
- on:code_intel_full_reindex → full rebuild

Runs for all projects that have an existing code_intel.db.
Standalone script: python -m backend.jobs.handlers.code_intel_reindex [--full]

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
    """Reindex all projects with code_intel.db.

    Args:
        full: If True, do a full rebuild. If False, incremental (changed files).

    Returns:
        Summary dict with projects processed and files refreshed.
    """
    from core.code_intel import load_project_graph
    from core.code_intel.freshness import check_freshness

    from config import get_swarmws
    ws_path = get_swarmws()
    projects_dir = ws_path / "Projects"

    if not projects_dir.is_dir():
        return {"status": "skipped", "reason": "no Projects/ directory"}

    results = []
    for project_dir in projects_dir.iterdir():
        if not project_dir.is_dir():
            continue
        # The project's own db OR a bind-managed one (code_intel_db_for) — a graph
        # built by binding a repo was skipped here when only Projects/<p>/ was read.
        from core.code_intel import code_intel_db_for
        if code_intel_db_for(project_dir) is None:
            continue

        project_name = project_dir.name
        mark = time.monotonic()
        graph = load_project_graph(project_name)
        mark = _phase(project_name, "load_graph", mark)
        if not graph:
            continue

        freshness = check_freshness(graph)
        mark = _phase(project_name, "check_freshness", mark)
        if not freshness.stale and not full:
            results.append({"project": project_name, "status": "fresh"})
            continue

        stored_root = graph.get_meta("repo_root")
        repo_root = Path(stored_root or "").resolve()
        if not repo_root.is_dir():
            results.append({"project": project_name, "status": "no_repo"})
            continue
        # OWNERSHIP GUARD (run_1950e67e): never re-index a repo_root the project
        # doesn't OWN (declared in its TECH.md or bindings.yaml — owned_repo_roots). A foreign/mis-seeded
        # repo_root would keep pulling another project's code into this brain (the
        # IVTHub-indexed-SwarmAI contamination). Defense at BOTH trigger sites
        # (this + the startup watcher, main.py) — R27 all-consumers.
        from core.code_intel import repo_root_is_owned
        if not repo_root_is_owned(project_dir, stored_root):
            results.append({"project": project_name, "status": "unowned_repo"})
            continue
        # Ensure repo_root is stored as absolute (fixes '.' from early indexing)
        graph.set_meta("repo_root", str(repo_root))

        # A full rebuild triggered from the INCREMENTAL job (full=False, e.g.
        # on:git_commit, 120s timeout) must NOT run inline: a full reparse
        # measures ~85-118s and hugs/exceeds the 120s wall, so it gets killed
        # before persisting the marker — the exact flap run_9a23dd4a fixes.
        # Delegate to the code_intel_full_reindex event (300s job), mirroring
        # context_health_hook's never-indexed path. When invoked as --full
        # (that 300s job itself, full=True) we DO run inline. (Gate-2 HIGH)
        if freshness.suggest_full_rebuild and not full:
            try:
                from jobs.scheduler import emit_event_atomic
                emit_event_atomic("code_intel_full_reindex", data={
                    "project": project_name,
                    "commits_behind": freshness.commits_behind,
                    "files_changed": len(freshness.changed_files),
                })
                results.append({"project": project_name, "status": "delegated_full_reindex"})
            except Exception as emit_err:
                logger.warning(
                    "code_intel %s: failed to delegate full reindex, "
                    "falling back to inline: %s", project_name, emit_err,
                )
            else:
                continue

        if full or freshness.suggest_full_rebuild:
            # Full reindex: clear + re-parse entire repo. Use the coverage-aware
            # parse so files the parser could not read (unknown ext, unreadable,
            # parse failure) are ACCOUNTED for in the graph's meta (coverage_holes,
            # parse_status — the analysis structure layer reports them), never
            # silently dropped (the "never report-done-but-not-understood" guarantee).
            # The one full rebuild (core.code_intel.build.rebuild): coverage holes,
            # routes and prefixes stored; an empty parse leaves the graph and its
            # freshness marker untouched (a transient empty parse must not make an
            # empty graph read fresh forever). freshness.current_head was read
            # before the parse, so a commit landing meanwhile stays unindexed.
            from core.code_intel.build import rebuild
            built = rebuild(graph, repo_root, head=freshness.current_head)
            mark = _phase(project_name, "rebuild", mark)
            results.append({
                "project": project_name,
                "status": "full_reindex",
                "nodes": built.nodes,
                "coverage_holes": len(built.coverage_holes),
                "parse_status": built.parse_status,
            })
        else:
            # Incremental: only changed files, GRADED (run_4602932d).
            #
            # Each changed file is graded NONE/COSMETIC/STRUCTURAL by comparing its
            # stored code SIGNATURE (node ids+kinds+flags + outbound edge triples,
            # LINE-AGNOSTIC) against the fresh parse:
            #   NONE     — bytes identical (nothing changed) → skip.
            #   COSMETIC — bytes differ but signature identical (comment/whitespace)
            #              → skip re-store, LEAVE stored nodes UNTOUCHED. This is the
            #              token win, and the "never silently drop" bond: skip means
            #              leave-in-place, NEVER remove (UA #402/#546/#484 data-loss
            #              class). Line numbers may drift — the documented tradeoff.
            #   STRUCTURAL — signature differs, OR language not tree-sitter-live
            #              (regex fidelity too low, Gate-1 #3), OR id collision
            #              (Gate-1 #1), OR no baseline/empty parse → re-store via the
            #              existing atomic store_file_nodes_edges path.
            # The aggregate verdict drives control flow HERE (not a sink-less write,
            # Gate-1 #6): a SKIP changeset (0 structural files) short-circuits the
            # batch-level rebuild_fts + prefix-resolution below, since nothing
            # structural was stored. Note the short-circuit reads the LOCAL `verdict`
            # variable; the `set_meta("last_change_class", verdict)` write is the
            # DURABLE record of that decision (observability + a hook can later read
            # it to decide domain-layer staleness, design §7.4) — it is persisted for
            # that, not consumed by this function's own control flow.
            from core.code_intel.parser import parse_file, LANGUAGE_MAP, _tree_sitter_live, grammar_for
            from core.code_intel.build import routes_for
            from core.code_intel import grading

            refreshed = 0
            grades: list[str] = []
            restored: list[str] = []  # files re-stored with symbols → scope of the prune
            for rel_path in freshness.changed_files[:200]:  # generous cap
                full_path = repo_root / rel_path
                # Scope guard: the graph only ever contains LANGUAGE_MAP files
                # (mirror the full-rebuild path, parser.py:805 `suffix in
                # LANGUAGE_MAP`). A non-source file (docs, data, the code_intel.db
                # itself + its -wal/-shm, images) was never a graph node, so it has
                # nothing to conserve OR re-store — grade NONE and DO NOT let it
                # push the changeset verdict. (An UNKNOWN-source ext like COBOL is a
                # full-rebuild coverage-hole concern, not an incremental re-store
                # target — it has no extractor, so STRUCTURAL would be a no-op that
                # falsely inflates the verdict. run_4602932d E2E: committing the DB
                # into the repo surfaced this.)
                if Path(rel_path).suffix not in LANGUAGE_MAP:
                    grades.append(grading.NONE)
                    continue
                if not full_path.exists():
                    # Deletion — MUST remove (never guarded by a skip). A deleted
                    # file has no new signature; leaving its nodes would be stale.
                    # remove_file clears nodes+edges+FTS but NOT routes — clear those
                    # too, or a deleted router file leaves phantom routes in
                    # code_routes (Gate-2 MED, run_4602932d; mirrors the lost-all-
                    # symbols purge branch below which already does both).
                    graph.remove_file(rel_path)
                    graph.delete_routes_for_file(rel_path)
                    grades.append(grading.STRUCTURAL)
                    refreshed += 1
                    continue
                grade = None  # single append point (below) — never double-count
                try:
                    result = parse_file(full_path, repo_root)
                    new_hash = result.nodes[0].sha256 if result.nodes else None
                    old_nodes = graph.get_nodes_by_file(rel_path)

                    # NONE-detection independent of parse-emptiness: a comment-only
                    # or symbol-less file (0 nodes) whose CONTENT is unchanged from
                    # the stored baseline must read NONE, not empty-parse STRUCTURAL
                    # (which would force rebuild_fts on a no-op commit). Hash the raw
                    # file bytes (the ground truth), not nodes[0].sha256 (absent when
                    # the parse yields no nodes). Review MED.
                    try:
                        import hashlib
                        content_hash = hashlib.sha256(full_path.read_bytes()).hexdigest()
                    except OSError:
                        content_hash = new_hash  # unreadable → fall back
                    old_hash = old_nodes[0]["file_hash"] if old_nodes else None

                    # Build the OLD signature from what's stored for this file.
                    old_sig = (
                        grading.compute_signature(old_nodes, graph.get_edges_by_file(rel_path))
                        if old_nodes else None
                    )
                    byte_changed = (content_hash is None) or (content_hash != old_hash)
                    grammar = grammar_for(full_path.suffix)
                    is_supported = grammar is not None and _tree_sitter_live(grammar)

                    grade = grading.file_grade(
                        old_sig, result, byte_changed=byte_changed, is_supported=is_supported
                    )

                    if grading.is_skippable(grade):
                        # NONE/COSMETIC: leave stored nodes UNTOUCHED (never remove).
                        # A changed import line with no change to the symbols grades
                        # COSMETIC, so the file's imports are refreshed on their own.
                        if byte_changed:
                            graph.replace_imports(rel_path, result.imports)
                            restored.append(rel_path)
                    elif result.nodes:
                        # STRUCTURAL with symbols: re-store via the existing
                        # atomic-replace path (also re-derives routes for the file).
                        file_hash = new_hash or ""
                        graph.store_file_nodes_edges(
                            rel_path, result.nodes, result.edges, file_hash,
                            imports=result.imports,
                        )
                        restored.append(rel_path)
                        routes_for(graph, repo_root, [rel_path], prefixes=False)
                        refreshed += 1
                    elif old_nodes:
                        # STRUCTURAL but the file lost ALL symbols (edited down to
                        # comments/constants) AND it previously had nodes → PURGE the
                        # stale nodes + routes. Leaving them is the silent-stale-graph
                        # data-loss class this feature exists to prevent (Review HIGH:
                        # the full-rebuild path drops them; the old incremental path
                        # leaked them). remove_file clears nodes+edges; also clear routes.
                        graph.remove_file(rel_path)
                        graph.delete_routes_for_file(rel_path)
                        refreshed += 1
                    # else: STRUCTURAL, 0 new nodes, 0 old nodes → nothing to store or
                    # purge (a newly-added symbol-less file); no-op is correct.
                except Exception:
                    # Fail-safe: an errored file is treated as STRUCTURAL (we could
                    # not prove it cosmetic), so the changeset never mis-reads as SKIP.
                    grade = grading.STRUCTURAL
                grades.append(grade if grade is not None else grading.STRUCTURAL)
            mark = _phase(project_name, f"grade_files({len(grades)})", mark)

            verdict = grading.classify_changeset(
                grades,
                new_or_deleted_topdirs=0,  # topology signal reserved for a future pass
                total_graph_files=graph.count_files(),
            )
            graph.set_meta("last_change_class", verdict)

            if verdict != grading.SKIP:
                # Structural change stored → refresh derived state. Cross-file call
                # targets the re-stored files emit resolve here exactly as in a full
                # rebuild (resolve_and_prune), not only at the next full rebuild. The
                # prune is scoped to the re-stored files: a graph-wide one could delete
                # edges another writer has not finished resolving.
                graph.resolve_and_prune(files=restored)
                from core.code_intel import resolve_router_prefixes
                resolve_router_prefixes(graph, repo_root)
                graph.rebuild_fts()
                mark = _phase(project_name, "resolve_and_prune+prefixes+rebuild_fts", mark)
            elif restored:
                # Only imports changed (cosmetic for the symbols): resolve them, scoped.
                graph.resolve_and_prune(files=restored)
            # Always advance the freshness marker (we DID process this commit range,
            # even if the verdict was SKIP — otherwise we'd re-grade forever).
            graph.mark_indexed(full=False, head=freshness.current_head)
            # Reclaim WAL disk space after the batch (store_file_nodes_edges +
            # rebuild_fts bypass incremental_update, so checkpoint here — Gate-2 finding E).
            graph.checkpoint_truncate()
            mark = _phase(project_name, "checkpoint", mark)

            results.append({
                "project": project_name,
                "status": "incremental",
                "files_refreshed": refreshed,
                "change_class": verdict,
                "grades": {g: grades.count(g) for g in set(grades)},
            })

        # The repo moved past its last index (or was rebuilt in full): its documents
        # follow — re-indexed in place for recall and re-ranked in survey.json
        # (design 2026-10-27 §5.3). Deterministic, no LLM; a failure here is logged
        # and never fails the code reindex.
        try:
            from core.brain_bootstrap.doc_index import sync_repo_after_head_move
            # The commit range's changed files let a commit that touched no document
            # skip the sync; a full rebuild does not know the range (None → sync).
            changed = (None if full or freshness.suggest_full_rebuild
                       else list(freshness.changed_files))
            doc_sync = sync_repo_after_head_move(project_dir, repo_root, changed)
            if doc_sync is not None:
                results[-1]["docs"] = {k: doc_sync.get(k) for k in
                                       ("skipped", "files", "written", "removed",
                                        "survey_refreshed")}
            mark = _phase(project_name, "repo_docs", mark)
        except Exception as exc:  # noqa: BLE001
            logger.warning("code_intel %s: repo document sync failed: %s: %s",
                           project_name, type(exc).__name__, exc)

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
