#!/usr/bin/env python3
"""s_library skill script — mount / search / list the Library.

Thin CLI over the ALREADY-TESTED core.library_mounts engines (add_mount,
judge_mount_kind, index_code_mount, index_docs_mount, recall_mounts) + recall_all.
This skill reinvents NOTHING — it is the agent-facing entry to the same functions
the +Add Folder API uses. Mounting indexes AT MOUNT TIME (code → graph, docs →
shared Knowledge FTS5); there is no separate briefing step (B1, run_3f837bdd).

Locates backend/ by walking up (same pattern as s_estimate-tokens) so it works
from the dev checkout, the projected .claude/skills copy, or the workspace.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path


def _bootstrap_backend() -> None:
    """Put backend/ on sys.path by locating backend/core/library_mounts.py."""
    marker = Path("backend") / "core" / "library_mounts.py"
    candidates = []
    env_root = os.environ.get("SWARM_REPO_ROOT")
    if env_root:
        candidates.append(Path(env_root).resolve())
    here = Path(__file__).resolve()
    candidates.extend(here.parents)
    candidates.extend(Path.cwd().resolve().parents)
    candidates.append(Path.cwd().resolve())
    candidates.append(Path("/Users/gawan/Desktop/SwarmAI-Workspace/swarmai"))
    seen = set()
    for base in candidates:
        if base in seen:
            continue
        seen.add(base)
        if (base / marker).is_file():
            backend = str(base / "backend")
            if backend not in sys.path:
                sys.path.insert(0, backend)
            return
    print("ERROR: could not locate backend/core/library_mounts.py — run inside the "
          "SwarmAI repo/workspace (or set SWARM_REPO_ROOT).", file=sys.stderr)
    sys.exit(2)


def _app_data_dir() -> Path:
    return Path(os.environ.get("SWARM_APP_DATA_DIR", Path.home() / ".swarm-ai"))


def _open_store():
    from core.library_mounts import LibraryMounts  # type: ignore
    db_path = _app_data_dir() / "data.db"
    conn = sqlite3.connect(str(db_path), timeout=5.0)
    conn.row_factory = sqlite3.Row
    store = LibraryMounts(conn)
    store.ensure_table()
    return store


def cmd_mount(args) -> None:
    from core.library_mounts import (  # type: ignore
        judge_mount_kind, index_code_mount, index_docs_mount,
    )
    src = Path(args.path).expanduser()
    if not src.is_dir():
        print(json.dumps({"error": f"{args.path} is not a directory — a single file "
                                   f"goes to the Inbox; only directories are mounted."}))
        sys.exit(1)
    store = _open_store()
    kind = judge_mount_kind(str(src))
    mid = store.add_mount(scope=args.scope, path=str(src), kind=kind)
    if kind == "code":
        result = index_code_mount(store, mid)
        print(json.dumps({"id": mid, "kind": kind, "status": result.get("status"),
                          "symbols": result.get("symbols", 0),
                          "message": f"Mounted + indexed {result.get('symbols', 0)} symbols. "
                                     f"Recall now reaches this code dir."}))
    else:
        # docs → chunk its text content into the shared Knowledge FTS5 at mount time
        # (B1, run_3f837bdd): recall-reachable immediately, no briefing step.
        result = index_docs_mount(store, mid)
        chunks = result.get("chunks", 0)
        msg = (f"Mounted + indexed {chunks} chunks. Recall now reaches this docs dir."
               if result.get("status") == "indexed"
               else "Mounted, but no text content found (empty/all-binary); "
                    "recall can't reach it.")
        print(json.dumps({"id": mid, "kind": kind, "status": result.get("status"),
                          "chunks": chunks, "message": msg}))


def cmd_search(args) -> None:
    from core.recall_multi import recall_library_hits, LIBRARY_DOMAINS  # type: ignore
    result = recall_library_hits(args.query, args.scope)
    hits = []
    for domain in LIBRARY_DOMAINS:
        for h in (result.buckets.get(domain) or []):
            hits.append({"domain": domain,
                         "title": h.get("heading") or h.get("name") or h.get("source") or "",
                         "source": h.get("source") or h.get("file_path") or h.get("mount_path") or "",
                         "mount_id": h.get("mount_id")})
    print(json.dumps({"query": args.query, "count": len(hits), "hits": hits}, indent=2))


def cmd_list(args) -> None:
    store = _open_store()
    rows = store.list_mounts(scope=args.scope)
    mounts = [{"id": r["id"], "path": r["path"], "kind": r["kind"],
               "health": r["health"], "enabled": bool(r["enabled"])} for r in rows]
    print(json.dumps({"count": len(mounts), "mounts": mounts}, indent=2))


def main() -> None:
    _bootstrap_backend()
    ap = argparse.ArgumentParser(description="s_library — mount/search/list the Library")
    sub = ap.add_subparsers(dest="command", required=True)

    m = sub.add_parser("mount"); m.add_argument("--path", required=True)
    m.add_argument("--scope", default="GLOBAL"); m.set_defaults(fn=cmd_mount)

    s = sub.add_parser("search"); s.add_argument("--query", required=True)
    s.add_argument("--scope", default="GLOBAL"); s.set_defaults(fn=cmd_search)

    l = sub.add_parser("list"); l.add_argument("--scope", default="GLOBAL")
    l.set_defaults(fn=cmd_list)

    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
