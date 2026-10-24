#!/usr/bin/env python3
"""The backend program's entry point: the packaged ``python-backend`` binary and the dev
shim ``backend/bin/python-backend`` both run this file (design
docs/design/2026-10-24-swarmai-usable-by-anyone.md §3.2).

Subcommands are dispatched BEFORE FastAPI is imported, so they start fast and need no
system Python:

- ``daemon [--host H] [--port N]`` — the HTTP backend (also the default when the first
  argument is a flag or absent, which is how launchd and Tauri start it).
- ``pipeline-v1 <args>`` — the pipeline CLI (``scripts/pipeline_v1.py``). Not supported on
  Windows: it says so and exits 2.
- ``py <script.py> [args]`` / ``py -m <module> [args]`` / ``py -c <code> [args]`` — run
  Python with the interpreter and modules this program carries (``runpy``). Product code
  that used to spawn a system ``python`` (MCP scripts, the self-upgrader, scheduled jobs,
  skill scripts) uses this instead.

Public: ``main(argv)``, ``run_py(argv)``, ``run_pipeline_v1(argv)``, ``WINDOWS_PIPELINE_MESSAGE``.
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
    """``py <script> [args]`` | ``py -m <module> [args]`` | ``py -c <code> [args]``.
    Behaves like ``python`` for these three forms: ``sys.argv`` is set the same way,
    the script's directory goes first on ``sys.path``, ``SystemExit`` keeps its code."""
    import runpy
    if not argv:
        print("usage: python-backend py <script.py> [args] | -m <module> [args] | -c <code> [args]",
              file=sys.stderr)
        return 64
    head, rest = argv[0], argv[1:]
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
    from scripts.pipeline_v1 import main as pipeline_main
    return pipeline_main(argv)


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
    if argv and argv[0] == "daemon":
        argv = argv[1:]
    return run_daemon(argv)


if __name__ == "__main__":
    sys.exit(main())
