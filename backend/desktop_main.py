#!/usr/bin/env python3
"""The backend program's entry point: the packaged ``python-backend`` binary and the dev
shim ``backend/bin/python-backend`` both run this file (design
docs/design/2026-10-24-swarmai-usable-by-anyone.md §3.2).

Subcommands are dispatched BEFORE FastAPI is imported, so they start fast and need no
system Python:

- ``daemon [--host H] [--port N]`` — the HTTP backend (also the default when the first
  argument is a flag or absent, which is how launchd and Tauri start it).
- ``pipeline-v1 <args>`` — the pipeline CLI (``core/run_protocol/cli_main.py``). Not supported on
  Windows: it says so and exits 2.
- ``knowledge map | find | get`` — read a project's DDD (or the Library) on demand, whole
  units with ``path:line`` and an id (``core/knowledge_cli.py``; knowledge addressing design).
- ``hook <event> --host <host>`` — a coding agent host's hook (Claude Code, ...) hands
  its event JSON on stdin (``core/host_hooks.py``). Always exits 0: it never blocks
  the host.
- ``ingest submit --source S1 --evidence <event-id|last-user> --payload-json JSON`` —
  enqueue one or more ``contracts_v2`` units tied to the current user turn; optional
  ``--wait N`` polls the stored outcome. It never processes work.
- ``ingest wake [--wait N]`` — nudge the daemon's CDF worker and print the 24h Flow
  overview (s_memory-distill's manual trigger). It never processes work either.
- ``py <script.py> [args]`` / ``py -m <module> [args]`` / ``py -c <code> [args]`` — run
  Python with the interpreter and modules this program carries (``runpy``). Product code
  that used to spawn a system ``python`` (MCP scripts, the self-upgrader, scheduled jobs,
  skill scripts) uses this instead.

Public: ``main(argv)``, ``run_py(argv)``, ``run_pipeline_v1(argv)``, ``run_hook(argv)``,
``run_knowledge(argv)``,
``WINDOWS_PIPELINE_MESSAGE``.
"""
import json
import os
import platform
import sys
import traceback
from datetime import datetime
from pathlib import Path

WINDOWS_PIPELINE_MESSAGE = (
    "The pipeline is not supported on Windows. Do the change natively instead "
    "(edit, test and commit directly)."
)

_HERE = Path(__file__).resolve().parent
if not getattr(sys, "frozen", False) and str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))


# ── py ────────────────────────────────────────────────────────────────────────

def run_py(argv: "list[str]") -> int:
    """``py <script> [args]`` | ``py -m <module> [args]`` | ``py -c <code> [args]`` |
    ``py - [args]`` (program on stdin). Behaves like ``python`` for these forms: ``sys.argv``
    is set the same way, the script's directory goes first on ``sys.path``, ``SystemExit``
    keeps its code. A module this program does not carry is named on stderr (exit 1)."""
    try:
        return _run_py(argv)
    except ModuleNotFoundError as exc:
        if exc.name is None:
            raise
        print(f"py: module '{exc.name}' was not found: it is not in this SwarmAI build and not "
              "next to the script. A skill that needs a module SwarmAI does not bundle lists it "
              "under python-optional-deps in its SKILL.md; tell the user it is missing rather "
              "than retrying.", file=sys.stderr)
        return 1


def _run_py(argv: "list[str]") -> int:
    import runpy
    if not argv:
        print("usage: python-backend py <script.py> [args] | -m <module> [args] | -c <code> [args]"
              " | - [args]", file=sys.stderr)
        return 64
    head, rest = argv[0], argv[1:]
    if head == "-":
        sys.argv = ["-", *rest]
        sys.path.insert(0, os.getcwd())
        exec(compile(sys.stdin.read(), "<stdin>", "exec"), {"__name__": "__main__"})  # noqa: S102 — `python -`
        return 0
    if head == "-m":
        if not rest:
            print("py -m: a module name is required", file=sys.stderr)
            return 64
        sys.argv = [rest[0], *rest[1:]]
        sys.path.insert(0, os.getcwd())
        runpy.run_module(rest[0], run_name="__main__", alter_sys=True)
        return 0
    if head == "-c":
        if not rest:
            print("py -c: code is required", file=sys.stderr)
            return 64
        sys.argv = ["-c", *rest[1:]]
        sys.path.insert(0, os.getcwd())
        exec(compile(rest[0], "<string>", "exec"), {"__name__": "__main__"})  # noqa: S102 — `python -c`
        return 0
    script = os.path.abspath(head)
    if not os.path.isfile(script):
        print(f"py: can't open file '{head}': no such file", file=sys.stderr)
        return 2
    sys.argv = [head, *rest]
    sys.path.insert(0, os.path.dirname(script))
    runpy.run_path(script, run_name="__main__")
    return 0


# ── pipeline-v1 ───────────────────────────────────────────────────────────────

def run_pipeline_v1(argv: "list[str]") -> int:
    if sys.platform == "win32":
        print(json.dumps({"ok": False, "error": "unsupported_platform",
                          "message": WINDOWS_PIPELINE_MESSAGE}))
        return 2
    from core.run_protocol.cli_main import main as pipeline_main
    return pipeline_main(argv)


# ── knowledge ─────────────────────────────────────────────────────────────────

def run_knowledge(argv: "list[str]") -> int:
    from core.knowledge_cli import main as knowledge_main
    return knowledge_main(argv)


# ── hook ──────────────────────────────────────────────────────────────────────

def run_hook(argv: "list[str]") -> int:
    from core.host_hooks import main as hook_main
    return hook_main(argv)


# ── daemon ────────────────────────────────────────────────────────────────────

def get_log_dir() -> Path:
    """Get the log directory based on platform."""
    if platform.system() == "Darwin":
        log_dir = Path.home() / "Library" / "Application Support" / "SwarmAI" / "logs"
    elif platform.system() == "Windows":
        log_dir = Path.home() / "AppData" / "Local" / "SwarmAI" / "logs"
    else:
        log_dir = Path.home() / ".local" / "share" / "swarmai" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir


# ── ingest ────────────────────────────────────────────────────────────────────

def run_ingest(argv: "list[str]") -> int:
    """Enqueue one CDF candidate and optionally poll its stored outcome."""
    import argparse
    import time

    parser = argparse.ArgumentParser(prog="python-backend ingest")
    commands = parser.add_subparsers(dest="command", required=True)
    submit = commands.add_parser("submit")
    submit.add_argument("--source", choices=("S1",), default="S1")
    submit.add_argument("--ref", dest="source_ref")
    submit.add_argument("--payload-json", required=True)
    submit.add_argument("--evidence", required=True)
    submit.add_argument("--wait", type=float, default=0.0)
    sync_record = commands.add_parser("sync-record")
    sync_record.add_argument("--origin", required=True)
    sync_record.add_argument("--paths", nargs="+", required=True)
    wake_cmd = commands.add_parser("wake")
    wake_cmd.add_argument("--wait", type=float, default=0.0)
    status_cmd = commands.add_parser("status")
    status_cmd.add_argument("--json", action="store_true")
    eval_cmd = commands.add_parser("eval-export")
    eval_cmd.add_argument("--since", required=True)
    eval_cmd.add_argument("--until")
    eval_cmd.add_argument("--out", required=True)
    args = parser.parse_args(argv)

    if args.command == "status":
        # How CDF is doing and whether what it writes is used (core/ingestion/status).
        # Read-only on ingestion.db; works with or without a running daemon.
        import sqlite3
        from core.ingestion.api import connect_ro
        from core.ingestion.status import render, status
        try:
            conn = connect_ro()
        except (FileNotFoundError, sqlite3.OperationalError):
            print("CDF has no store yet (ingestion.db not created)")
            return 0
        try:
            snapshot = status(conn)
        finally:
            conn.close()
        print(json.dumps(snapshot, ensure_ascii=False, indent=1) if args.json else render(snapshot))
        return 1 if snapshot["stalls"] else 0

    if args.command == "eval-export":
        # Design §14.2: every CDF outcome (judge stage live + archived, extractor
        # rejects) as JSONL for batch evaluation. Read-only on ingestion.db.
        import sqlite3
        from collections import Counter
        from core.ingestion.api import connect_ro
        from core.ingestion.eval_log import export
        try:
            conn = connect_ro()
        except (FileNotFoundError, sqlite3.OperationalError):
            print(json.dumps({"status": "empty", "detail": "no ingestion store yet"}))
            return 0
        counts: Counter = Counter()
        out = Path(args.out).expanduser()
        try:
            with out.open("w", encoding="utf-8") as handle:
                for item in export(conn, args.since, args.until):
                    counts[(item["stage"], item["state"])] += 1
                    handle.write(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n")
        finally:
            conn.close()
        print(json.dumps({"status": "exported", "out": str(out), "records": sum(counts.values()),
                          "by_stage_state": {f"{k[0]}:{k[1]}": v for k, v in sorted(counts.items())}},
                         ensure_ascii=False))
        return 0

    if args.command == "wake":
        # s_memory-distill's manual trigger: nudge the daemon's worker (it owns the
        # lanes and the writer; this process never processes work), then print the
        # Flow overview so the skill can summarise what was audited.
        from core.ingestion import queries
        from core.ingestion.wake import wake
        wake()
        if args.wait > 0:
            time.sleep(min(args.wait, 60.0))
        try:
            flow = queries.flow_overview("24h")
        except FileNotFoundError:   # a fresh install: the worker has not created its store yet
            print(json.dumps({"status": "woken", "flow": None,
                              "detail": "no ingestion store yet"}, ensure_ascii=False))
            return 0
        print(json.dumps({"status": "woken", "flow": flow}, ensure_ascii=False, default=str))
        return 0

    if args.command == "sync-record":
        from core.ingestion.governed_edit import (
            apply_sync, sync_baseline_revision, sync_revision_of,
        )
        from core.ingestion.writer import _relative, ensure_baseline

        baseline = ensure_baseline()
        paths = [Path(value).expanduser().resolve(strict=False) for value in args.paths]
        before = {_relative(path): sync_baseline_revision(path, baseline) for path in paths}
        after = {_relative(path): sync_revision_of(path) for path in paths}
        try:
            operations = apply_sync(paths, before, after, args.origin)
        except (OSError, PermissionError, ValueError) as exc:
            print(json.dumps({"status": "rejected", "detail": str(exc)}, ensure_ascii=False))
            return 2
        print(json.dumps({"status": "recorded", "operations": operations}, ensure_ascii=False))
        return 0

    raw = sys.stdin.read() if args.payload_json == "-" else args.payload_json
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        print(json.dumps({"status": "rejected", "detail": f"invalid JSON: {exc}"}, ensure_ascii=False))
        return 2

    session_id = os.environ.get("SWARM_APP_SESSION_ID", "").strip()
    evidence = args.evidence
    if evidence == "last-user":
        from core.session_inputs import latest_user_event_id
        evidence = latest_user_event_id(session_id) or ""
    if not session_id or not evidence:
        print(json.dumps({"status": "rejected", "detail": "current user evidence is required"},
                         ensure_ascii=False))
        return 2

    from core.ingestion.sources.s1_user import submit_units
    payloads = payload if isinstance(payload, list) else [payload]
    try:
        results = submit_units(
            payloads, session_id=session_id, evidence=evidence,
            turn_kind="user" if os.environ.get("SWARM_LAST_USER_EVENT_ID") else "non_user",
            source_ref=args.source_ref)
    except (TypeError, ValueError) as exc:
        print(json.dumps({"status": "rejected", "detail": str(exc)}, ensure_ascii=False))
        return 2
    for result in results:
        if result.detail:
            print(result.detail)
        else:
            print(json.dumps(result.__dict__, ensure_ascii=False))
    if not results or not args.wait:
        return 0 if results else 1
    result = results[0]
    if result.status not in {"queued", "duplicate"} or not result.candidate_id:
        return 1

    deadline = time.monotonic() + max(0.0, args.wait)
    from core.ingestion.store import connect_ro
    while time.monotonic() < deadline:
        try:
            conn = connect_ro()
            try:
                candidate = conn.execute(
                    "SELECT state, versions FROM ingestion_inbox WHERE id=?", (result.candidate_id,)
                ).fetchone()
                units = conn.execute(
                    "SELECT state, destination, reason FROM ingestion_units WHERE candidate_id=? ORDER BY unit_id",
                    (result.candidate_id,),
                ).fetchall()
            finally:
                conn.close()
            if candidate and candidate["state"] == "done":
                rendered_units = []
                for unit_row in units:
                    item = dict(unit_row)
                    destination = json.loads(item["destination"] or "{}")
                    item["destination"] = destination
                    item["text"] = payloads[0] if len(payloads) == 1 else payloads
                    if destination.get("store") == "COMMON_DDD":
                        item["hint"] = "要让它每轮生效，请用一句话直接说出这条规则。"
                    rendered_units.append(item)
                print(json.dumps({
                    "status": "done", "candidate_id": result.candidate_id,
                    "units": rendered_units,
                }, ensure_ascii=False))
                return 0
        except (FileNotFoundError, OSError):
            pass
        time.sleep(0.3)
    print("已收到，结果见 Cognitive Distillation Flow")
    return 0


def write_startup_log(message: str):
    """Write a startup log message before main logging is initialized."""
    try:
        log_file = get_log_dir() / "startup.log"
        timestamp = datetime.now().isoformat()
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(f"[{timestamp}] {message}\n")
        # Also print to stdout for Tauri to capture
        print(f"[STARTUP] {message}", flush=True)
    except Exception as e:
        print(f"[STARTUP ERROR] Failed to write log: {e}", flush=True)


def run_daemon(argv: "list[str]") -> int:
    write_startup_log("Desktop backend starting...")
    write_startup_log(f"Python version: {sys.version}")
    write_startup_log(f"Platform: {platform.system()} {platform.machine()}")
    write_startup_log(f"Executable: {sys.executable}")
    write_startup_log(f"Working directory: {os.getcwd()}")

    # Set environment for desktop mode BEFORE any imports
    os.environ.setdefault("DATABASE_TYPE", "sqlite")
    write_startup_log(f"DATABASE_TYPE: {os.environ.get('DATABASE_TYPE')}")

    try:
        import argparse
        import asyncio
        import uvicorn
        write_startup_log("Importing main app...")
        from main import app
        write_startup_log("All imports successful!")
    except Exception as e:
        write_startup_log(f"Import error: {type(e).__name__}: {e}\n{traceback.format_exc()}")
        return 1

    parser = argparse.ArgumentParser(description="SwarmAI backend")
    parser.add_argument("--port", type=int, default=8000, help="Port to run on")
    parser.add_argument("--host", type=str, default="127.0.0.1", help="Host to bind to")
    # parse_known_args: ignore unknown flags so the binary never crash-loops
    # if the daemon script or Tauri passes an unexpected argument.
    # See: 2026-04-02 --log-level crash loop (commit 6829745).
    args, unknown = parser.parse_known_args(argv)
    if unknown:
        write_startup_log(f"Ignoring unknown arguments: {unknown}")

    write_startup_log(f"Starting server on {args.host}:{args.port}")
    print(f"Starting backend server on {args.host}:{args.port}", flush=True)
    try:
        config = uvicorn.Config(
            app, host=args.host, port=args.port, log_level="info",
            loop="asyncio",   # Use asyncio loop explicitly
            reload=False,     # Disable reload in bundled app
            workers=1,        # Single worker for bundled app
        )
        asyncio.run(uvicorn.Server(config).serve())
        write_startup_log("Server stopped normally")
    except Exception as e:
        write_startup_log(f"Server error: {type(e).__name__}: {e}\n{traceback.format_exc()}")
        return 1
    return 0


def main(argv: "list[str] | None" = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "py":
        return run_py(argv[1:])
    if argv and argv[0] == "pipeline-v1":
        return run_pipeline_v1(argv[1:])
    if argv and argv[0] == "hook":
        return run_hook(argv[1:])
    if argv and argv[0] == "knowledge":
        return run_knowledge(argv[1:])
    if argv and argv[0] == "ingest":
        return run_ingest(argv[1:])
    if argv and argv[0] == "daemon":
        argv = argv[1:]
    return run_daemon(argv)


if __name__ == "__main__":
    sys.exit(main())
