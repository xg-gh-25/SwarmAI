"""Code Intelligence Reindex Handler.

Triggered by event-driven jobs:
- on:git_commit → incremental reindex (changed files only)
- on:code_intel_full_reindex → full rebuild

Runs for all projects that have an existing code_intel.db.
Standalone script: python -m backend.jobs.handlers.code_intel_reindex [--full]
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

logger = logging.getLogger("swarm.jobs.code_intel_reindex")


def reindex_projects(full: bool = False) -> dict:
    """Reindex all projects with code_intel.db.

    Args:
        full: If True, do a full rebuild. If False, incremental (changed files).

    Returns:
        Summary dict with projects processed and files refreshed.
    """
    from core.code_intel import load_project_graph
    from core.code_intel.freshness import check_freshness

    ws_path = Path.home() / ".swarm-ai" / "SwarmWS"
    projects_dir = ws_path / "Projects"

    if not projects_dir.is_dir():
        return {"status": "skipped", "reason": "no Projects/ directory"}

    results = []
    for project_dir in projects_dir.iterdir():
        if not project_dir.is_dir():
            continue
        db_path = project_dir / "code_intel.db"
        if not db_path.exists():
            continue

        project_name = project_dir.name
        graph = load_project_graph(project_name)
        if not graph:
            continue

        freshness = check_freshness(graph)
        if not freshness.stale and not full:
            results.append({"project": project_name, "status": "fresh"})
            continue

        stored_root = graph.get_meta("repo_root")
        repo_root = Path(stored_root or "").resolve()
        if not repo_root.is_dir():
            results.append({"project": project_name, "status": "no_repo"})
            continue
        # OWNERSHIP GUARD (run_1950e67e): never re-index a repo_root the project
        # doesn't OWN (its own TECH.md must declare it). A foreign/mis-seeded
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
            # parse failure) are ACCOUNTED for in code-intel.json, never silently
            # dropped (Run AB — the "never report-done-but-not-understood" guarantee).
            from core.code_intel.parser import parse_repo_with_coverage, LANGUAGE_MAP
            from core.code_intel import extract_and_store_routes
            parse_out = parse_repo_with_coverage(repo_root)
            parse_results = parse_out.results
            coverage_holes = parse_out.coverage_holes
            parse_status = parse_out.status
            if parse_results:
                graph.clear()
                graph.bulk_insert(parse_results)
                # bulk_insert already rebuilds FTS + resolves cross-file
                # Persist the freshness marker ONLY after a genuine rebuild.
                # freshness.current_head is now populated even on the
                # never-indexed path (freshness.py), so this breaks the
                # perpetual-full-rebuild loop (run_9a23dd4a). MUST stay INSIDE
                # `if parse_results:` — a transient empty parse (pyo3 teardown,
                # ThreadPool timeout, repo-not-yet-on-disk) must NOT advance the
                # marker over a stale/empty graph, else check_freshness would
                # report fresh forever (Gate-2 HIGH, run_9a23dd4a).
                if freshness.current_head:
                    graph.set_meta("last_indexed_commit", freshness.current_head)
                # Preserve repo_root metadata
                graph.set_meta("repo_root", str(repo_root))
                # Extract routes from all parsed files
                for pr in parse_results:
                    fp = pr.file_path if hasattr(pr, "file_path") else pr.get("file_path", "")
                    if fp:
                        full_fp = repo_root / fp
                        if full_fp.exists():
                            try:
                                lang = LANGUAGE_MAP.get(full_fp.suffix, "unknown")
                                content = full_fp.read_text(encoding="utf-8", errors="replace")
                                extract_and_store_routes(graph, fp, content, lang)
                            except Exception:
                                pass
                # Apply router prefix resolution (FastAPI include_router)
                _resolve_prefixes(graph, repo_root)
            total_nodes = sum(len(pr.nodes) for pr in parse_results)
            # Export code-intel.json after full reindex — preserving the v3 layer
            # and carrying the coverage ledger + status stamp.
            _export_json(graph, project_name, project_dir,
                         coverage_holes=coverage_holes, parse_status=parse_status)
            results.append({
                "project": project_name,
                "status": "full_reindex",
                "nodes": total_nodes,
                "coverage_holes": len(coverage_holes),
                "parse_status": parse_status,
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
            from core.code_intel.parser import parse_file, LANGUAGE_MAP, _tree_sitter_live
            from core.code_intel import extract_and_store_routes
            from core.code_intel import grading

            refreshed = 0
            grades: list[str] = []
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
                    lang = LANGUAGE_MAP.get(full_path.suffix, "unknown")
                    is_supported = lang != "unknown" and _tree_sitter_live(lang)

                    grade = grading.file_grade(
                        old_sig, result, byte_changed=byte_changed, is_supported=is_supported
                    )

                    if grading.is_skippable(grade):
                        # NONE/COSMETIC: leave stored nodes UNTOUCHED (never remove).
                        pass
                    elif result.nodes:
                        # STRUCTURAL with symbols: re-store via the existing
                        # atomic-replace path (also re-derives routes for the file).
                        file_hash = new_hash or ""
                        graph.store_file_nodes_edges(
                            rel_path, result.nodes, result.edges, file_hash
                        )
                        content = full_path.read_text(encoding="utf-8", errors="replace")
                        extract_and_store_routes(graph, rel_path, content, lang)
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

            verdict = grading.classify_changeset(
                grades,
                new_or_deleted_topdirs=0,  # topology signal reserved for a future pass
                total_graph_files=graph.count_files(),
            )
            graph.set_meta("last_change_class", verdict)

            if verdict != grading.SKIP:
                # Structural change stored → refresh derived state.
                _resolve_prefixes(graph, repo_root)
                graph.rebuild_fts()
            # Always advance the freshness marker (we DID process this commit range,
            # even if the verdict was SKIP — otherwise we'd re-grade forever).
            if freshness.current_head:
                graph.set_meta("last_indexed_commit", freshness.current_head)
            # Reclaim WAL disk space after the batch (store_file_nodes_edges +
            # rebuild_fts bypass incremental_update, so checkpoint here — Gate-2 finding E).
            graph.checkpoint_truncate()

            results.append({
                "project": project_name,
                "status": "incremental",
                "files_refreshed": refreshed,
                "change_class": verdict,
                "grades": {g: grades.count(g) for g in set(grades)},
            })

    return {"status": "success", "projects": results}


def _resolve_prefixes(graph, repo_root: Path) -> None:
    """Apply FastAPI include_router prefix resolution to stored routes.

    Scans for common entrypoint files (main.py, app.py) in the repo,
    builds a prefix map from include_router() calls, then updates
    routes in the DB whose paths are bare (missing the mount prefix).
    """
    from core.code_intel.route_parser import build_prefix_map

    # Find entrypoint candidates
    candidates = [
        "backend/main.py", "main.py", "app.py", "src/main.py", "src/app.py",
        "server.py", "backend/app.py",
    ]
    entrypoint_path = None
    entrypoint_content = None
    for candidate in candidates:
        fp = repo_root / candidate
        if fp.exists():
            entrypoint_path = candidate
            entrypoint_content = fp.read_text(encoding="utf-8", errors="replace")
            break

    if not entrypoint_content:
        return

    prefix_map = build_prefix_map(entrypoint_content, entrypoint_path)
    if not prefix_map:
        return

    # Use graph's own connection (WAL mode + busy_timeout already set)
    from core.code_intel.route_parser import _make_route_id
    conn = graph._conn
    cur = conn.cursor()
    cur.execute("SELECT id, method, path, file_path FROM code_routes")
    rows = cur.fetchall()

    updated = 0
    for old_id, method, path, file_path in rows:
        prefix = prefix_map.get(file_path, "")
        if not prefix or path.startswith(prefix):
            continue  # Already resolved or no prefix applies
        # Guard: if route path already contains the prefix as a substring,
        # it was likely applied inline via APIRouter(prefix=...) — skip
        if prefix.lstrip("/") in path:
            continue

        # Apply prefix
        new_path = prefix.rstrip("/") + "/" + path.lstrip("/") if path != "/" else prefix
        new_id = _make_route_id(file_path, method, new_path)

        cur.execute(
            "UPDATE code_routes SET path = ?, id = ? WHERE id = ?",
            (new_path, new_id, old_id)
        )
        updated += 1

    conn.commit()
    if updated:
        logger.info(f"Prefix resolution: updated {updated} routes")


def _export_json(graph, project_name: str, project_dir: Path,
                 coverage_holes: list[dict] | None = None,
                 parse_status: str = "complete") -> None:
    """Export code-intel.json after reindex (preserves v3 layer + carries coverage
    ledger). Non-fatal on failure."""
    try:
        from core.code_intel.json_exporter import export_code_intel_json
        output_path = project_dir / "code-intel.json"
        export_code_intel_json(graph, project_name, output_path,
                               coverage_holes=coverage_holes, parse_status=parse_status)
    except Exception as e:
        logger.warning(f"JSON export failed for {project_name}: {e}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    full_mode = "--full" in sys.argv
    summary = reindex_projects(full=full_mode)
    logger.info("Code intel reindex complete: %s", summary)
