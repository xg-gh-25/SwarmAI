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

        repo_root = Path(graph.get_meta("repo_root") or "").resolve()
        if not repo_root.is_dir():
            results.append({"project": project_name, "status": "no_repo"})
            continue
        # Ensure repo_root is stored as absolute (fixes '.' from early indexing)
        graph.set_meta("repo_root", str(repo_root))

        if full or freshness.suggest_full_rebuild:
            # Full reindex: clear + re-parse entire repo
            from core.code_intel.parser import parse_repo, LANGUAGE_MAP
            from core.code_intel import extract_and_store_routes
            parse_results = parse_repo(repo_root)
            if parse_results:
                graph.clear()
                graph.bulk_insert(parse_results)
                # bulk_insert already rebuilds FTS + resolves cross-file
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
            # Export code-intel.json v2 after full reindex
            _export_json(graph, project_name, project_dir)
            results.append({
                "project": project_name,
                "status": "full_reindex",
                "nodes": total_nodes,
            })
        else:
            # Incremental: only changed files
            from core.code_intel.parser import parse_file, LANGUAGE_MAP
            from core.code_intel import extract_and_store_routes

            refreshed = 0
            for rel_path in freshness.changed_files[:200]:  # generous cap
                full_path = repo_root / rel_path
                if full_path.exists():
                    try:
                        result = parse_file(full_path, repo_root)
                        if result.nodes:
                            file_hash = result.nodes[0].sha256 or ""
                            graph.store_file_nodes_edges(
                                rel_path, result.nodes, result.edges, file_hash
                            )
                            # Extract routes from the same file content
                            lang = LANGUAGE_MAP.get(full_path.suffix, "unknown")
                            content = full_path.read_text(encoding="utf-8", errors="replace")
                            extract_and_store_routes(graph, rel_path, content, lang)
                            refreshed += 1
                    except Exception:
                        pass
                else:
                    graph.remove_file(rel_path)
                    refreshed += 1

            # Apply router prefix resolution for any routes just inserted
            _resolve_prefixes(graph, repo_root)
            graph.rebuild_fts()
            if freshness.current_head:
                graph.set_meta("last_indexed_commit", freshness.current_head)

            results.append({
                "project": project_name,
                "status": "incremental",
                "files_refreshed": refreshed,
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

    # Load all routes from DB, apply prefix, re-store
    import sqlite3
    db_path = graph._db_path
    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()
    cur.execute("SELECT id, method, path, handler_node_id, framework, file_path, line_number, middleware FROM code_routes")
    rows = cur.fetchall()

    updated = 0
    for row in rows:
        old_id, method, path, handler_node_id, framework, file_path, line_number, middleware = row
        prefix = prefix_map.get(file_path, "")
        if not prefix or path.startswith(prefix):
            continue  # Already resolved or no prefix applies

        # Apply prefix
        new_path = prefix.rstrip("/") + "/" + path.lstrip("/") if path != "/" else prefix
        from core.code_intel.route_parser import _make_route_id
        new_id = _make_route_id(file_path, method, new_path)

        cur.execute(
            "UPDATE code_routes SET path = ?, id = ? WHERE id = ?",
            (new_path, new_id, old_id)
        )
        updated += 1

    conn.commit()
    conn.close()
    if updated:
        logger.info(f"Prefix resolution: updated {updated} routes")


def _export_json(graph, project_name: str, project_dir: Path) -> None:
    """Export code-intel.json v2 after reindex. Non-fatal on failure."""
    try:
        from core.code_intel.json_exporter import export_code_intel_json
        output_path = project_dir / "code-intel.json"
        export_code_intel_json(graph, project_name, output_path)
    except Exception as e:
        logger.warning(f"JSON export failed for {project_name}: {e}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    full_mode = "--full" in sys.argv
    summary = reindex_projects(full=full_mode)
    logger.info("Code intel reindex complete: %s", summary)
