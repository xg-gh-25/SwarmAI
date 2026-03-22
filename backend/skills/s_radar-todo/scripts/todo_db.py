"""Direct SQLite CRUD for SwarmAI Radar ToDos.

Bypasses the backend API (agent sandbox can't reach dynamic port).
Writes directly to ~/.swarm-ai/data.db — the same DB the backend reads.

Each todo is a **self-contained work packet** — when dragged into a chat tab,
the agent gets all context needed to start executing immediately:
- What to do (title + description)
- Why (origin, source reference)
- How to start (next_step, acceptance_criteria)
- Where (files, design_docs, related_commits)
- Dependencies and blockers

Usage:
    python3 todo_db.py add --title "Fix bug" --priority high --source-type chat
    python3 todo_db.py list [--status pending] [--limit 10]
    python3 todo_db.py get <todo_id>           # structured work packet output
    python3 todo_db.py update <todo_id> --title "New title" --priority medium
    python3 todo_db.py status <todo_id> <new_status>
    python3 todo_db.py delete <todo_id>
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path.home() / ".swarm-ai" / "data.db"
WORKSPACE_ID = "swarmws"

VALID_STATUSES = ("pending", "overdue", "in_discussion", "handled", "cancelled", "deleted")
VALID_PRIORITIES = ("high", "medium", "low", "none")
VALID_SOURCE_TYPES = ("manual", "email", "slack", "meeting", "integration", "chat", "ai_detected")


def _connect() -> sqlite3.Connection:
    if not DB_PATH.exists():
        print(f"ERROR: Database not found at {DB_PATH}", file=sys.stderr)
        sys.exit(1)
    conn = sqlite3.connect(str(DB_PATH), timeout=5.0)
    conn.row_factory = sqlite3.Row
    # Match backend WAL mode to avoid SQLITE_BUSY with concurrent access
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")


def _row_to_dict(row: sqlite3.Row) -> dict:
    return {k: row[k] for k in row.keys()}


def _build_linked_context(args: argparse.Namespace) -> str | None:
    """Build linked_context JSON from structured args.

    linked_context schema:
    {
        "files": ["path/to/file.py", ...],         # source files to read/modify
        "design_docs": ["path/to/design.md", ...],  # design docs for reference
        "commits": ["abc1234", ...],                 # related git commits
        "sessions": ["session_id", ...],             # related chat sessions
        "memory_refs": ["COE:2026-03-22", ...],      # MEMORY.md entries
        "next_step": "concrete first action",        # what to do first
        "acceptance": "how to know it's done",       # definition of done
        "blockers": ["what's blocking", ...],        # current blockers
        "notes": "free-form context"                 # anything else
    }
    """
    # If raw JSON provided, use it directly
    if hasattr(args, "linked_context") and args.linked_context:
        return args.linked_context

    ctx: dict = {}
    if hasattr(args, "files") and args.files:
        ctx["files"] = args.files
    if hasattr(args, "design_docs") and args.design_docs:
        ctx["design_docs"] = args.design_docs
    if hasattr(args, "commits") and args.commits:
        ctx["commits"] = args.commits
    if hasattr(args, "sessions") and args.sessions:
        ctx["sessions"] = args.sessions
    if hasattr(args, "memory_refs") and args.memory_refs:
        ctx["memory_refs"] = args.memory_refs
    if hasattr(args, "next_step") and args.next_step:
        ctx["next_step"] = args.next_step
    if hasattr(args, "acceptance") and args.acceptance:
        ctx["acceptance"] = args.acceptance
    if hasattr(args, "blockers") and args.blockers:
        ctx["blockers"] = args.blockers
    if hasattr(args, "notes") and args.notes:
        ctx["notes"] = args.notes

    return json.dumps(ctx) if ctx else None


def _parse_linked_context(raw: str | None) -> dict:
    """Parse linked_context JSON string to dict, with fallback."""
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {"notes": raw}


def _print_summary(todos: list[dict]) -> None:
    """Compact list view — one line per todo."""
    if not todos:
        print("No todos found.")
        return

    for t in todos:
        pri = {"high": "🔴", "medium": "🟡", "low": "🔵", "none": "⚪"}.get(t["priority"], "⚪")
        sts = {
            "pending": "⏳", "overdue": "⚠️", "in_discussion": "💬",
            "handled": "✅", "cancelled": "❌", "deleted": "🗑️",
        }.get(t["status"], "❓")
        ctx = _parse_linked_context(t.get("linked_context"))
        next_step = ctx.get("next_step", "")
        next_hint = f" → {next_step}" if next_step else ""
        print(f"  {sts} {pri} [{t['id'][:8]}] {t['title']}{next_hint}")


def _print_work_packet(todo: dict) -> None:
    """Full work packet output — everything an agent needs to start working."""
    ctx = _parse_linked_context(todo.get("linked_context"))
    pri = {"high": "🔴 HIGH", "medium": "🟡 MEDIUM", "low": "🔵 LOW", "none": "⚪ NONE"}.get(todo["priority"], todo["priority"])

    print(f"{'=' * 70}")
    print(f"TODO: {todo['title']}")
    print(f"{'=' * 70}")
    print(f"  ID:       {todo['id']}")
    print(f"  Priority: {pri}")
    print(f"  Status:   {todo['status']}")
    print(f"  Source:   {todo['source_type']}" + (f" ({todo['source']})" if todo.get("source") else ""))
    print(f"  Created:  {todo['created_at']}")
    if todo.get("due_date"):
        print(f"  Due:      {todo['due_date']}")
    print()

    if todo.get("description"):
        print(f"  WHY: {todo['description']}")
        print()

    if ctx.get("next_step"):
        print(f"  NEXT STEP: {ctx['next_step']}")
        print()

    if ctx.get("acceptance"):
        print(f"  DONE WHEN: {ctx['acceptance']}")
        print()

    if ctx.get("files"):
        print("  FILES:")
        for f in ctx["files"]:
            print(f"    - {f}")
        print()

    if ctx.get("design_docs"):
        print("  DESIGN DOCS:")
        for d in ctx["design_docs"]:
            print(f"    - {d}")
        print()

    if ctx.get("commits"):
        print("  RELATED COMMITS:")
        for c in ctx["commits"]:
            print(f"    - {c}")
        print()

    if ctx.get("sessions"):
        print("  RELATED SESSIONS:")
        for s in ctx["sessions"]:
            print(f"    - {s}")
        print()

    if ctx.get("memory_refs"):
        print("  MEMORY REFS:")
        for m in ctx["memory_refs"]:
            print(f"    - {m}")
        print()

    if ctx.get("blockers"):
        print("  BLOCKERS:")
        for b in ctx["blockers"]:
            print(f"    ⛔ {b}")
        print()

    if ctx.get("notes"):
        print(f"  NOTES: {ctx['notes']}")
        print()

    # Also emit JSON for machine consumption
    print("---JSON---")
    full = _row_to_dict_safe(todo)
    full["linked_context_parsed"] = ctx
    print(json.dumps(full, indent=2, default=str))


def _row_to_dict_safe(row) -> dict:
    """Convert sqlite3.Row or dict to plain dict."""
    if isinstance(row, dict):
        return row
    return {k: row[k] for k in row.keys()}


def _add_context_args(parser: argparse.ArgumentParser) -> None:
    """Add shared linked-context arguments to a subparser."""
    g = parser.add_argument_group("context", "Structured context for work packet")
    g.add_argument("--files", nargs="+", metavar="PATH", help="Source files to read/modify")
    g.add_argument("--design-docs", nargs="+", metavar="PATH", help="Design docs for reference")
    g.add_argument("--commits", nargs="+", metavar="SHA", help="Related git commits")
    g.add_argument("--sessions", nargs="+", metavar="ID", help="Related chat session IDs")
    g.add_argument("--memory-refs", nargs="+", metavar="REF", help="MEMORY.md refs (e.g. COE:2026-03-22)")
    g.add_argument("--next-step", metavar="TEXT", help="Concrete first action to take")
    g.add_argument("--acceptance", metavar="TEXT", help="Definition of done")
    g.add_argument("--blockers", nargs="+", metavar="TEXT", help="Current blockers")
    g.add_argument("--notes", metavar="TEXT", help="Free-form context notes")
    g.add_argument("--linked-context", metavar="JSON", help="Raw JSON (overrides structured args)")


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_add(args: argparse.Namespace) -> None:
    conn = _connect()
    now = _now_iso()
    todo_id = str(uuid.uuid4())
    linked_context = _build_linked_context(args)

    conn.execute(
        """INSERT INTO todos (id, workspace_id, title, description, source,
           source_type, status, priority, due_date, linked_context, task_id,
           created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?, NULL, ?, ?)""",
        (
            todo_id, WORKSPACE_ID, args.title, args.description, args.source,
            args.source_type, args.priority, args.due_date, linked_context,
            now, now,
        ),
    )
    conn.commit()
    conn.close()

    # Structured output for machine consumption
    result = {
        "id": todo_id, "title": args.title, "status": "pending",
        "priority": args.priority, "source_type": args.source_type,
    }
    if linked_context:
        result["linked_context"] = json.loads(linked_context)

    # Chat announcement — agent MUST show this line to user (never swallow it)
    pri_icon = {"high": "🔴 high", "medium": "🟡 medium", "low": "🔵 low", "none": "⚪"}.get(args.priority, args.priority)
    print(f'📌 Added to Radar: "{args.title}" ({pri_icon})')
    print(json.dumps(result, indent=2))


def cmd_list(args: argparse.Namespace) -> None:
    conn = _connect()
    query = "SELECT * FROM todos WHERE workspace_id = ?"
    params: list = [WORKSPACE_ID]

    if args.status:
        query += " AND status = ?"
        params.append(args.status)
    elif not args.all:
        query += " AND status IN ('pending', 'overdue')"

    query += " ORDER BY CASE priority WHEN 'high' THEN 0 WHEN 'medium' THEN 1 WHEN 'low' THEN 2 ELSE 3 END, created_at DESC"
    query += f" LIMIT {args.limit}"

    rows = conn.execute(query, params).fetchall()
    conn.close()

    todos = [_row_to_dict(r) for r in rows]

    if args.verbose:
        for t in todos:
            _print_work_packet(t)
    else:
        _print_summary(todos)


def _resolve_todo(conn: sqlite3.Connection, prefix: str) -> sqlite3.Row:
    """Resolve a todo by ID or prefix. Exits on not-found or ambiguous match."""
    rows = conn.execute("SELECT * FROM todos WHERE id LIKE ?", (prefix + "%",)).fetchall()
    if not rows:
        print(f"Todo not found: {prefix}", file=sys.stderr)
        sys.exit(1)
    if len(rows) > 1:
        print(f"Ambiguous prefix '{prefix}' matches {len(rows)} todos:", file=sys.stderr)
        for r in rows:
            print(f"  [{r['id'][:8]}] {r['title']}", file=sys.stderr)
        print("Use a longer prefix.", file=sys.stderr)
        sys.exit(1)
    return rows[0]


def cmd_get(args: argparse.Namespace) -> None:
    """Get a todo as a full work packet — all context for immediate execution."""
    conn = _connect()
    row = _resolve_todo(conn, args.todo_id)
    conn.close()

    _print_work_packet(_row_to_dict(row))


def cmd_update(args: argparse.Namespace) -> None:
    conn = _connect()
    row = _resolve_todo(conn, args.todo_id)
    full_id = row["id"]
    updates = []
    params = []

    if args.title:
        updates.append("title = ?")
        params.append(args.title)
    if args.description is not None:
        updates.append("description = ?")
        params.append(args.description)
    if args.priority:
        updates.append("priority = ?")
        params.append(args.priority)
    if args.due_date is not None:
        updates.append("due_date = ?")
        params.append(args.due_date if args.due_date != "clear" else None)

    # Merge context: existing + new structured args
    new_ctx = _build_linked_context(args)
    if new_ctx:
        existing_ctx = _parse_linked_context(row["linked_context"])
        incoming_ctx = json.loads(new_ctx)
        # Merge: new values overwrite, lists extend
        for k, v in incoming_ctx.items():
            if isinstance(v, list) and isinstance(existing_ctx.get(k), list):
                # Deduplicate when extending lists
                merged = existing_ctx[k] + [x for x in v if x not in existing_ctx[k]]
                existing_ctx[k] = merged
            else:
                existing_ctx[k] = v
        updates.append("linked_context = ?")
        params.append(json.dumps(existing_ctx))

    if not updates:
        print("Nothing to update.")
        return

    updates.append("updated_at = ?")
    params.append(_now_iso())
    params.append(full_id)

    conn.execute(f"UPDATE todos SET {', '.join(updates)} WHERE id = ?", params)
    conn.commit()
    conn.close()
    print(f"Updated todo [{full_id[:8]}]")


def cmd_status(args: argparse.Namespace) -> None:
    conn = _connect()
    row = _resolve_todo(conn, args.todo_id)
    full_id = row["id"]
    old_status = row["status"]

    conn.execute(
        "UPDATE todos SET status = ?, updated_at = ? WHERE id = ?",
        (args.new_status, _now_iso(), full_id),
    )
    conn.commit()
    conn.close()
    print(f"[{full_id[:8]}] {old_status} → {args.new_status}")


def cmd_delete(args: argparse.Namespace) -> None:
    conn = _connect()
    row = _resolve_todo(conn, args.todo_id)
    full_id = row["id"]

    if args.hard:
        conn.execute("DELETE FROM todos WHERE id = ?", (full_id,))
    else:
        conn.execute(
            "UPDATE todos SET status = 'deleted', updated_at = ? WHERE id = ?",
            (_now_iso(), full_id),
        )
    conn.commit()
    conn.close()
    print(f"{'Deleted' if args.hard else 'Soft-deleted'} todo [{full_id[:8]}]: {row['title']}")


def main() -> None:
    parser = argparse.ArgumentParser(description="SwarmAI Radar ToDo manager")
    sub = parser.add_subparsers(dest="command", required=True)

    # --- add ---
    p_add = sub.add_parser("add", help="Create a new todo")
    p_add.add_argument("--title", "-t", required=True, help="Todo title")
    p_add.add_argument("--description", "-d", default=None, help="Why this todo exists + background")
    p_add.add_argument("--priority", "-p", choices=VALID_PRIORITIES, default="none", help="Priority level")
    p_add.add_argument("--source", default=None, help="Source reference (e.g. Slack thread URL)")
    p_add.add_argument("--source-type", choices=VALID_SOURCE_TYPES, default="manual", help="Source type")
    p_add.add_argument("--due-date", default=None, help="Due date (ISO format)")
    _add_context_args(p_add)

    # --- list ---
    p_list = sub.add_parser("list", help="List todos")
    p_list.add_argument("--status", "-s", choices=VALID_STATUSES, default=None, help="Filter by status")
    p_list.add_argument("--all", "-a", action="store_true", help="Show all statuses")
    p_list.add_argument("--limit", "-l", type=int, default=20, help="Max results")
    p_list.add_argument("--verbose", "-v", action="store_true", help="Show full work packets")

    # --- get ---
    p_get = sub.add_parser("get", help="Get todo as full work packet")
    p_get.add_argument("todo_id", help="Todo ID (or prefix)")

    # --- update ---
    p_update = sub.add_parser("update", help="Update a todo")
    p_update.add_argument("todo_id", help="Todo ID (or prefix)")
    p_update.add_argument("--title", "-t", default=None, help="New title")
    p_update.add_argument("--description", "-d", default=None, help="New description")
    p_update.add_argument("--priority", "-p", choices=VALID_PRIORITIES, default=None, help="New priority")
    p_update.add_argument("--due-date", default=None, help="New due date (ISO format, or 'clear')")
    _add_context_args(p_update)

    # --- status ---
    p_status = sub.add_parser("status", help="Change todo status")
    p_status.add_argument("todo_id", help="Todo ID (or prefix)")
    p_status.add_argument("new_status", choices=VALID_STATUSES, help="New status")

    # --- delete ---
    p_delete = sub.add_parser("delete", help="Delete a todo")
    p_delete.add_argument("todo_id", help="Todo ID (or prefix)")
    p_delete.add_argument("--hard", action="store_true", help="Hard delete (remove from DB)")

    args = parser.parse_args()

    handlers = {
        "add": cmd_add,
        "list": cmd_list,
        "get": cmd_get,
        "update": cmd_update,
        "status": cmd_status,
        "delete": cmd_delete,
    }
    handlers[args.command](args)


if __name__ == "__main__":
    main()
