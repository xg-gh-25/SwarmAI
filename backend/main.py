"""FastAPI application entry point."""
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from contextlib import asynccontextmanager
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
import logging
import os
import shutil
import sqlite3
import subprocess
import sys
import time
import uuid
from pathlib import Path

from config import settings, get_app_data_dir, get_log_file_path
from core import session_registry
from utils.bundle_paths import get_resource_file
from routers import agents_router, skills_router, mcp_router, chat_router, chat_threads_router, auth_router, workspace_router, settings_router, plugins_router, tasks_router, channels_router, system_router, todos_router, search_router, workspace_config_router, workspace_api_router, projects_router, tscc_router, artifacts_router, escalations_router, voice_router, hive_router
from routers.autonomous_jobs import router as autonomous_jobs_router
from routers.pipelines import router as pipelines_router
from routers.pollinate import router as pollinate_router
from routers.jobs import router as jobs_router
from channels.gateway import channel_gateway
from middleware.error_handler import setup_error_handlers
from middleware.rate_limit import limiter
from database import initialize_database

# Runtime flag to track if lifespan startup has completed
# This is different from initialization_complete in DB which persists across restarts
_startup_complete = False


def _generate_permissions_json(workspace_path: Path, dangerous_patterns: list[str]) -> None:
    """Write read-only ``permissions.json`` for user visibility.

    Shows only the dangerous command patterns — all other tools are
    auto-approved via ``bypassPermissions``, so listing them adds no value.
    The file is regenerated at each startup; editing it has no effect.
    """
    import json as _json
    settings_dir = workspace_path / ".claude" / "settings"
    settings_dir.mkdir(parents=True, exist_ok=True)
    content = {
        "description": (
            "Commands matching these glob patterns require user approval "
            "per session. All other commands are auto-approved. "
            "Edit ~/.swarm-ai/dangerous_commands.json to customize."
        ),
        "dangerous_commands": dangerous_patterns,
    }
    (settings_dir / "permissions.json").write_text(
        _json.dumps(content, indent=2) + "\n", encoding="utf-8"
    )


# Startup timing instrumentation (populated by lifespan, read by system status endpoint).
# ``_startup_time_ms`` holds the total wall-clock time from lifespan entry to
# ``_startup_complete = True``.  ``_phase_timings`` holds per-phase durations
# keyed by phase name (e.g. ``"database_ms"``, ``"workspace_ms"``).
# Both are ``None`` until the lifespan completes its critical path.
_startup_time_ms: float | None = None
_phase_timings: dict[str, float] | None = None


# get_log_file_path() moved to config.py (leaf module, single source of truth) so
# job handlers can read the live log path without importing main.py's full app graph.


# Configure logging
log_level = logging.DEBUG if settings.debug else logging.INFO
log_format = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"

# In daemon mode the StreamHandler's output (stderr) is redirected by launchd
# to ``backend-stderr.log``, which has NO rotation and grows unbounded (observed
# at 132MB). The RotatingFileHandler below already keeps the full INFO stream
# capped at ~40MB, so the console only needs WARNING+ for crash diagnosis in
# daemon mode. Require the EXPLICIT ``SWARMAI_MODE=daemon`` that the launchd
# plist + wrapper set — never default to it here, or ``./dev.sh`` (which leaves
# SWARMAI_MODE unset) would also be silenced to WARNING on its live console.
console_level = (
    logging.WARNING if os.environ.get("SWARMAI_MODE") == "daemon" else log_level
)

# Create handlers
console_handler = logging.StreamHandler()
console_handler.setLevel(console_level)
console_handler.setFormatter(logging.Formatter(log_format))

# File handler - write logs to file with rotation (10MB × 3 backups)
# Plain FileHandler grows unbounded; RotatingFileHandler caps at ~40MB total.
log_file = get_log_file_path()
from logging.handlers import RotatingFileHandler
file_handler = RotatingFileHandler(
    log_file, maxBytes=10 * 1024 * 1024, backupCount=3, encoding='utf-8'
)
file_handler.setLevel(log_level)
file_handler.setFormatter(logging.Formatter(log_format))

# Configure root logger
logging.basicConfig(
    level=log_level,
    format=log_format,
    handlers=[console_handler, file_handler]
)
logger = logging.getLogger(__name__)
logger.info(f"Log file: {log_file}")

# Suppress noisy debug logs from third-party libraries
logging.getLogger("urllib3").setLevel(logging.WARNING)


# ---------------------------------------------------------------------------
# Backend-as-Daemon: mode detection + backend.json lifecycle
# ---------------------------------------------------------------------------

_BACKEND_JSON_DEFAULT = str(Path(get_app_data_dir()) / "backend.json")
_backend_start_monotonic: float = 0.0  # set during lifespan startup

# Unique boot identifier — changes on every process restart.
# Tauri daemon watchdog compares this to detect silent restarts
# (daemon restart too fast for poll interval to catch the gap).
_boot_id: str = __import__("uuid").uuid4().hex[:12]


def _resolve_sdk_version() -> str:
    """Resolve the real claude-agent-sdk version at runtime.

    Frozen-env-safe: PyInstaller strips dist-info, so importlib.metadata
    fails in production. The SDK ships a ``__version__`` attribute on the
    package itself, which survives freezing. Fallback chain:
    ``claude_agent_sdk.__version__`` → env ``CLAUDE_AGENT_SDK_VERSION`` →
    literal ``"unknown"``. Never raises.
    """
    try:
        import claude_agent_sdk
        v = getattr(claude_agent_sdk, "__version__", None)
        if v:
            return str(v)
    except Exception:
        pass
    return os.environ.get("CLAUDE_AGENT_SDK_VERSION") or "unknown"


def _resolve_cli_version() -> str:
    """Resolve the bundled Claude Code CLI version by running ``--version``.

    Called ONCE at boot (cached in ``_cli_version``); never per request.
    The bundled CLI path is resolved the same way the SDK's transport does:
    ``<subprocess_cli pkg>/.._bundled/claude``. 2s timeout, returns
    ``"unknown"`` on any failure (missing binary, timeout, parse error).
    Never raises.
    """
    try:
        from claude_agent_sdk._internal.transport import subprocess_cli as _sc
        cli_name = "claude.exe" if sys.platform == "win32" else "claude"
        cli_path = Path(_sc.__file__).resolve().parents[2] / "_bundled" / cli_name
        if not cli_path.is_file():
            return "unknown"
        out = subprocess.run(
            [str(cli_path), "--version"],
            capture_output=True, text=True, timeout=2.0,
        )
        # Non-zero exit (corrupt binary, auth/license error) may still print
        # diagnostic text to stdout — don't mistake its first token for a
        # version. Treat any failure as unknown.
        if out.returncode != 0:
            return "unknown"
        # Output form: "2.1.150 (Claude Code)" — take the first token.
        first = (out.stdout or "").strip().split()
        return first[0] if first else "unknown"
    except Exception:
        return "unknown"


# Real runtime versions for /health observability.
# _sdk_version is eager (attribute read, cheap). _cli_version is filled at
# boot in lifespan() (subprocess, must not run per health request).
_sdk_version: str = _resolve_sdk_version()
_cli_version: str = "unknown"


def _is_port_listening(host: str, port: int) -> bool:
    """Check if a TCP port is accepting connections."""
    import socket as _socket

    try:
        with _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM) as s:
            s.settimeout(1)
            s.connect((host, port))
            return True
    except (ConnectionRefusedError, OSError, TimeoutError):
        return False


def _detect_run_mode() -> str:
    """Detect backend run mode from SWARMAI_MODE env var.

    - ``"daemon"``     — macOS launchd 24/7 service (default)
    - ``"subprocess"`` — Windows/Linux desktop (Tauri child process, dies with app)
    - ``"hive"``       — EC2 cloud deployment (systemd, 24/7)
    - ``"dev"``        — Local development (manual start, no channels)

    Gateway runs only in {daemon, hive}. /shutdown blocked in {daemon, hive}.
    """
    return os.environ.get("SWARMAI_MODE", "daemon")


def _backend_json_lock(path: str) -> str:
    """Return the advisory lock file path for backend.json operations."""
    return path + ".lock"


def write_backend_json(
    port: int,
    mode: str,
    path: str = _BACKEND_JSON_DEFAULT,
) -> None:
    """Write ``backend.json`` so other processes can discover this backend.

    Uses an exclusive file lock (``flock_exclusive`` from ``utils.file_lock``,
    which wraps ``fcntl.flock`` on Unix and ``msvcrt.locking`` on Windows) to
    eliminate the TOCTOU race between conflict-check and file-write.  Without
    the lock, two backends starting simultaneously can both pass the conflict
    check and the last writer wins — corrupting discovery for the loser.

    **Conflict check (inside lock):** If an existing ``backend.json``
    records a PID that is alive AND the recorded port is accepting
    connections, we skip the write to prevent a competing backend from
    stealing the discovery file.

    **PID ownership guard:** After writing, only *this* process's PID is
    in the file — ``remove_backend_json`` checks PID before deleting to
    prevent a late-exiting process from removing a newer owner's file.
    """
    import json as _json
    from datetime import datetime, timezone

    from utils.file_lock import flock_exclusive, flock_unlock

    p = Path(path)
    lock_path = Path(_backend_json_lock(path))
    p.parent.mkdir(parents=True, exist_ok=True)

    with open(lock_path, "w") as lock_fd:
        flock_exclusive(lock_fd)  # blocking exclusive lock
        try:
            # Conflict check: don't overwrite if an active backend already owns this file
            if p.exists():
                try:
                    existing = _json.loads(p.read_text())
                    existing_pid = existing.get("pid")
                    existing_port = existing.get("port")
                    if (
                        existing_pid is not None
                        and existing_pid != os.getpid()
                        and existing_port is not None
                    ):
                        # Check PID alive
                        try:
                            os.kill(existing_pid, 0)
                        except PermissionError:
                            pid_alive = True  # alive but owned by another user
                        except (OSError, ProcessLookupError):
                            pid_alive = False  # dead PID — safe to overwrite
                        else:
                            pid_alive = True
                        if pid_alive:
                            # PID alive — check if port is also listening
                            if _is_port_listening("127.0.0.1", existing_port):
                                logger.warning(
                                    "backend.json conflict: PID %d alive and port %d listening "
                                    "— skipping write (our PID=%d, port=%d)",
                                    existing_pid, existing_port, os.getpid(), port,
                                )
                                return
                except (ValueError, OSError):
                    pass  # corrupt file — safe to overwrite

            data = {
                "pid": os.getpid(),
                "port": port,
                "mode": mode,
                "boot_id": _boot_id,
                "started_at": datetime.now(timezone.utc).isoformat(),
            }
            p.write_text(_json.dumps(data, indent=2))
        finally:
            flock_unlock(lock_fd)


def remove_backend_json(
    path: str = _BACKEND_JSON_DEFAULT,
    startup_mode: str | None = None,
) -> None:
    """Delete ``backend.json`` on clean shutdown.

    Uses an exclusive file lock to prevent races with concurrent writers.

    **Mode guard:** If ``startup_mode`` is provided, only delete when the
    mode recorded in the file matches.  This prevents a dev instance from
    deleting the daemon's discovery file on exit.

    **PID ownership guard:** Only delete if the file's PID matches our PID.
    This prevents a late-exiting old process from deleting a newer process's
    discovery file (e.g. during version sync restart).
    """
    from utils.file_lock import flock_exclusive, flock_unlock

    p = Path(path)
    if not p.exists():
        return

    lock_path = Path(_backend_json_lock(path))
    try:
        with open(lock_path, "w") as lock_fd:
            flock_exclusive(lock_fd)
            try:
                if not p.exists():
                    return  # deleted between our check and lock acquisition

                try:
                    import json as _json
                    data = _json.loads(p.read_text())
                except (ValueError, OSError):
                    # Corrupt file — safe to remove
                    p.unlink(missing_ok=True)
                    return

                # PID ownership: only delete if WE wrote this file
                file_pid = data.get("pid")
                if file_pid is not None and file_pid != os.getpid():
                    logger.info(
                        "Skipping backend.json removal: file PID=%d != our PID=%d",
                        file_pid, os.getpid(),
                    )
                    return

                # Mode guard
                if startup_mode is not None:
                    file_mode = data.get("mode")
                    if file_mode is not None and file_mode != startup_mode:
                        logger.info(
                            "Skipping backend.json removal: file mode=%s != startup mode=%s",
                            file_mode, startup_mode,
                        )
                        return

                p.unlink(missing_ok=True)
            finally:
                flock_unlock(lock_fd)
    except Exception:
        # Best-effort — don't crash on shutdown for a discovery file
        try:
            p.unlink(missing_ok=True)
        except Exception:
            pass


def read_backend_json(path: str = _BACKEND_JSON_DEFAULT) -> dict | None:
    """Read and validate ``backend.json``.

    Uses a shared file lock to prevent reading a half-written file.

    Returns the parsed dict if the file exists, is valid JSON, and the
    PID recorded in it is still alive.  Returns ``None`` otherwise
    (missing file, corrupt JSON, dead PID).
    """
    import json as _json

    from utils.file_lock import flock_shared, flock_unlock

    p = Path(path)
    if not p.exists():
        return None

    lock_path = Path(_backend_json_lock(path))
    try:
        with open(lock_path, "w") as lock_fd:
            flock_shared(lock_fd)  # shared lock — multiple readers OK on Unix
            try:
                if not p.exists():
                    return None
                try:
                    data = _json.loads(p.read_text())
                except (ValueError, OSError):
                    return None

                # Stale PID check: is the recorded process still alive?
                pid = data.get("pid")
                if pid is None:
                    return None
                try:
                    os.kill(pid, 0)  # signal 0 = existence check
                except (OSError, ProcessLookupError):
                    return None  # process is dead → stale file

                return data
            finally:
                flock_unlock(lock_fd)
    except (OSError, IOError):
        # Lock file inaccessible — fall back to lockless read
        try:
            data = _json.loads(p.read_text())
            pid = data.get("pid")
            if pid is None:
                return None
            os.kill(pid, 0)
            return data
        except Exception:
            return None


def _detect_backend_port() -> int:
    """Detect the port this backend is listening on.

    Resolution order:
    1. ``--port N`` in sys.argv (production — set by desktop_main.py via Tauri)
    2. ``PORT`` env var
    3. ``settings.port`` (dev default: 8000)
    """
    # 1. Check sys.argv for --port N
    for i, arg in enumerate(sys.argv):
        if arg == "--port" and i + 1 < len(sys.argv):
            try:
                return int(sys.argv[i + 1])
            except ValueError:
                pass

    # 2. Check PORT env var
    port_env = os.environ.get("PORT")
    if port_env:
        try:
            return int(port_env)
        except ValueError:
            pass

    # 3. Fall back to settings
    return settings.port


def _get_seed_database_path() -> Path | None:
    """Get the path to the bundled seed database.
    
    Returns:
        Path to seed.db or None if not found
        
    See utils.bundle_paths for Tauri bundle structure documentation.
    """
    backend_dir = Path(__file__).resolve().parent
    dev_seed_path = backend_dir.parent / "desktop" / "resources" / "seed.db"
    return get_resource_file("seed.db", dev_seed_path)


def _purge_corrupt_db(db_path: Path, reason: str) -> None:
    """Remove a corrupt data.db AND its -wal/-shm sidecars, so a fresh seed
    copy is not re-corrupted by a leftover foreign WAL replay.

    A malformed DB from a crash mid-write is EXACTLY when a hot -wal exists; a
    leftover -wal beside the fresh seed copy would be replayed into it →
    re-corruption (adversarial-caught HIGH, run_2d3417d9). So purge all three.
    """
    logger.warning("%s at %s — removing (incl. -wal/-shm) and re-seeding", reason, db_path)
    db_path.unlink(missing_ok=True)
    for suffix in ("-wal", "-shm"):
        Path(str(db_path) + suffix).unlink(missing_ok=True)


def _reseed_from_seed(user_db_path: Path) -> bool:
    """Atomically copy seed.db → user_db_path and set WAL pragmas.

    Returns True on success (db ready), False if no seed is available (dev mode
    → caller falls back to runtime init). Shared by first-launch and the
    corruption-recovery path so both re-seed identically.
    """
    seed_db_path = _get_seed_database_path()
    if not seed_db_path or not seed_db_path.exists():
        logger.warning("Seed database not found, falling back to runtime initialization")
        return False

    tmp_path = user_db_path.with_suffix(".db.tmp")
    try:
        shutil.copy2(seed_db_path, tmp_path)
        os.replace(tmp_path, user_db_path)  # atomic on POSIX
        logger.info(f"Copied seed database from {seed_db_path} to {user_db_path}")
    except Exception as e:
        logger.error(f"Failed to copy seed database: {e}")
        try:
            tmp_path.unlink(missing_ok=True)
            user_db_path.unlink(missing_ok=True)
        except OSError:
            pass
        logger.warning("Will fall back to runtime initialization")
        return False

    try:
        with sqlite3.connect(str(user_db_path)) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=5000")
        logger.info("Set WAL mode and busy_timeout on seed-copied database")
    except Exception as e:
        logger.warning(f"Failed to set database pragmas (non-fatal): {e}")
    return True


async def _init_db_bounded(skip_schema: bool, timeout: float = 45.0) -> None:
    """Run initialize_database() under a wall-clock timeout, for BOTH boot paths.

    Both the fast path (skip_schema=True) and full-init acquire an exclusive
    migration flock; a stale lock holder can hang the call forever. The full-init
    path was already bounded (asyncio.wait_for 45s) but the fast path was NOT —
    so a wedged lock hung boot indefinitely with no health signal. Route both
    through this helper so the fast path gets the same bound. On breach: raise
    RuntimeError (launchd KeepAlive then surfaces a bounded restart, not an
    infinite "initializing" hang).

    Integrity: the migration run inside initialize_database() IS the integrity
    gate. A malformed/torn data.db makes the very first migration query raise
    sqlite3.DatabaseError — that IS the crash-loop A2 defends against. We catch
    it HERE (fast path only, where a seed exists to recover from), purge the
    corrupt file + sidecars, re-seed, and retry the migration ONCE on the fresh
    db. If the retry ALSO raises → re-raise (bounded KeepAlive restart, never an
    infinite loop). This replaces the old O(db-size) PRAGMA quick_check pre-probe
    that scanned every b-tree page (~47s on a 1.29GB db) to detect exactly this
    class — the migration detects it for free.

    ⚠️ ORDER IS LOAD-BEARING: sqlite3.OperationalError IS A SUBCLASS of
    sqlite3.DatabaseError. A momentarily-locked-but-VALID db raises
    OperationalError ("database is locked"); it must NOT be treated as
    corruption (that would unlink()+re-seed a valid db → USER DATA DESTROYED,
    the run_2d3417d9 HIGH). So the OperationalError re-raise clause MUST precede
    the DatabaseError recovery clause.
    """
    try:
        await asyncio.wait_for(initialize_database(skip_schema=skip_schema), timeout=timeout)
    except asyncio.TimeoutError:
        logger.error(
            "Database initialization timed out after %ss (skip_schema=%s) — "
            "likely a stale migration lock; check for a wedged prior process",
            timeout, skip_schema,
        )
        raise RuntimeError("Database initialization timed out")
    except sqlite3.OperationalError:
        # Locked/busy — NOT corruption. Never destroy a valid-but-busy db.
        # (Subclass of DatabaseError → MUST be caught before the clause below.)
        raise
    except sqlite3.DatabaseError as e:
        # Malformed / not-a-database / torn page → the migration query raised.
        # This is the crash-loop trigger. Only the fast path can recover (a seed
        # exists); full-init has no seed to fall back to, so re-raise there.
        if not skip_schema:
            raise
        user_db_path = get_app_data_dir() / "data.db"
        logger.warning(
            "Database migration raised %s: %s — corrupt data.db; purging + re-seeding, retry once",
            type(e).__name__, e,
        )
        _purge_corrupt_db(user_db_path, "Malformed database (migration raised, prevents crash-loop)")
        if not _reseed_from_seed(user_db_path):
            # No seed to recover from — re-raise (bounded restart, not a silent hang).
            raise
        # Reset the DB INSTANCE's init flag so the retry actually re-runs
        # migrations on the FRESH db. SQLiteDatabase.initialize() short-circuits
        # on `if self._initialized: return` (sqlite.py:1738), and the failed
        # first attempt may have set it — so clear it on the singleton instance
        # (database.get_database()), not on the module.
        try:
            from database import get_database
            get_database()._initialized = False  # type: ignore[attr-defined]
        except Exception:
            pass
        # Retry ONCE on the fresh db. If THIS raises, let it propagate — a bounded
        # KeepAlive restart, never an infinite re-seed loop.
        await asyncio.wait_for(initialize_database(skip_schema=skip_schema), timeout=timeout)


def _ensure_database_initialized() -> bool:
    """Ensure the user database exists, copying from seed if needed.

    Checks whether a user database already exists at ``~/.swarm-ai/data.db``.

    * **Returning user** (``data.db`` exists): returns ``True`` immediately so
      the caller can skip the expensive init pipeline — user data is preserved.
    * **First launch** (``data.db`` missing, ``seed.db`` available): performs an
      atomic copy (write to a temp file, then ``os.replace``) and sets WAL mode
      + busy_timeout pragmas on the fresh copy.  Returns ``True``.
    * **Dev mode** (no ``seed.db``): logs a warning and returns ``False`` so the
      caller falls back to runtime initialization.

    Returns:
        ``True``  — database is ready; skip the init pipeline.
        ``False`` — no seed available; caller must run runtime init.

    Integrity note: there is intentionally NO content-scanning integrity probe
    here. The old ``_db_is_intact`` ran ``PRAGMA quick_check`` on every boot,
    which reads every b-tree page — O(db-size), ~47s on a 1.29GB db — to detect
    a malformed file. That check is redundant: the migration run by
    ``_init_db_bounded`` IS the integrity gate. A malformed/torn db makes the
    first migration query raise ``sqlite3.DatabaseError``, which ``_init_db_bounded``
    catches → purge + re-seed + retry-once. A transient lock raises
    ``OperationalError`` and is deliberately NOT treated as corruption. This
    detects exactly the crash-loop class for free and keeps cold-start at <1s.
    (Coverage note: ``_run_migrations`` → ``_run_data_cleanups`` runs
    unconditionally every boot and SELECTs over schema-owned tables — ``tasks``,
    ``chat_threads``, ``swarm_workspaces``, ``app_settings`` — so a torn page in
    ANY of those is also caught here as a migration DatabaseError, not just
    header/schema damage. The only shape NOT detected is a torn page in a table
    that no migration reads; that cannot crash-loop the boot and would surface
    later as a specific query error — out of scope for a boot-time guard.)

    Validates: Requirements 2.1, 2.2, 2.3, 2.5, 2.7, 2.8
    """
    user_db_path = get_app_data_dir() / "data.db"

    # Ensure the app data directory exists
    user_db_path.parent.mkdir(parents=True, exist_ok=True)

    # --- Returning user: preserve existing data.db, skip init pipeline ---
    if user_db_path.exists():
        if user_db_path.stat().st_size == 0:
            # 0-byte = an interrupted create. Purge + fall through to re-seed.
            # (Malformed-but-nonzero is handled by the migration catch in
            # _init_db_bounded, not by an expensive pre-scan here.)
            _purge_corrupt_db(user_db_path, "Empty database")
        else:
            logger.info(f"Using existing user database at {user_db_path}")
            return True

    # --- First launch (or post-purge): atomic seed copy ---
    return _reseed_from_seed(user_db_path)


async def _deferred_refresh_defaults(label: str) -> None:
    """Background task that refreshes built-in skills and context files.

    Shared by both the fast-path and full-init quick-validation paths to
    avoid duplicating the same closure.  Logs success/failure and records
    elapsed time into the module-level ``_phase_timings`` dict.

    Also runs MCP migration (idempotent) to ensure legacy user-mcp-servers.json
    entries are converted to the new .claude/mcps/mcp-dev.json format.
    This was previously only in run_full_initialization — existing users who
    already had initialization_complete=1 would never get their MCPs migrated.

    Args:
        label: Human-readable label for log messages (e.g. ``"fast path"``).
    """
    _t_start = time.monotonic()
    try:
        from core.initialization_manager import initialization_manager
        await initialization_manager.refresh_builtin_defaults()
        logger.info("Builtin defaults refreshed (deferred, %s)", label)

        # Ensure MCP migration runs on every startup path (idempotent).
        # Previously only ran in run_full_initialization, so returning users
        # with initialization_complete=1 never got their MCPs migrated.
        try:
            from pathlib import Path
            from core.mcp_migration import migrate_if_needed
            from core.mcp_config_loader import merge_catalog_template
            from utils.bundle_paths import get_resources_dir

            ws_path = Path(initialization_manager.get_cached_workspace_path())
            await migrate_if_needed(ws_path)

            # Also merge catalog template (adds new entries from product updates)
            _backend_dir = Path(__file__).resolve().parent
            _dev_resources = _backend_dir.parent / "desktop" / "resources"
            resources_dir = get_resources_dir(_dev_resources)
            template_path = resources_dir / "mcp-catalog.json"
            merge_catalog_template(ws_path, template_path)

            # Ensure directory exists
            (ws_path / ".claude" / "mcps").mkdir(parents=True, exist_ok=True)
            logger.info("MCP config ensured (deferred, %s)", label)
        except Exception:
            logger.exception("MCP config setup failed (non-fatal, %s)", label)
    except Exception:
        logger.exception("Deferred refresh_builtin_defaults failed (non-fatal, %s)", label)
    finally:
        elapsed = round((time.monotonic() - _t_start) * 1000)
        _phase_timings_ref = _phase_timings
        if _phase_timings_ref is not None:
            _phase_timings_ref["refresh_defaults_ms"] = elapsed
        logger.info("Phase: refresh_builtin_defaults (deferred) — %dms", elapsed)


_SCHEDULER_INTERVAL_SECONDS = 3600  # 1 hour — same as the old launchd StartCalendarInterval


_job_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="job-executor")


async def _start_code_intel_watchers() -> None:
    """Start FS watchers for all indexed projects (daemon/hive only).

    Scans Projects/ for those with code_intel.db, resolves repo_root from
    graph metadata, and starts a CodeIntelWatcher for each (up to max capacity).
    """
    await asyncio.sleep(10)  # Let startup settle
    try:
        from core.code_intel import load_project_graph, get_code_intel_db_path, repo_root_is_owned
        from core.code_intel.watcher import start_watcher
        from jobs.paths import PROJECTS_DIR

        if not PROJECTS_DIR.is_dir():
            return

        started = 0
        for project_dir in PROJECTS_DIR.iterdir():
            if not project_dir.is_dir():
                continue
            db_path = project_dir / "code_intel.db"
            if not db_path.exists():
                continue

            project_name = project_dir.name
            graph = load_project_graph(project_name)
            if not graph:
                continue

            repo_root = graph.get_meta("repo_root")
            if not repo_root or not Path(repo_root).is_dir():
                continue

            # OWNERSHIP GUARD (run_1950e67e): only watch a repo_root the project
            # actually OWNS (its own TECH.md declares it). A foreign/mis-seeded
            # repo_root (e.g. IVTHub's db pointing at the SwarmAI source tree) must
            # NOT be watched — else every save in that tree re-indexes foreign files
            # into this project's brain (self-perpetuating content contamination).
            if not repo_root_is_owned(project_dir, repo_root):
                logger.warning(
                    "Code Intelligence: %s repo_root %r is NOT owned by the project "
                    "(TECH.md declares no/different local repo) — skipping watcher to "
                    "prevent cross-project contamination", project_name, repo_root)
                continue

            ok = await start_watcher(project_name, Path(repo_root), graph)
            if ok:
                started += 1

        if started:
            logger.info(f"Code Intelligence: started {started} FS watcher(s)")
    except Exception:
        logger.debug("Code Intelligence watcher startup failed (non-fatal)", exc_info=True)


async def _run_inprocess_scheduler() -> None:
    """In-process job scheduler loop (daemon/hive only).

    Replaces the external com.swarmai.scheduler launchd job with an asyncio
    loop inside the daemon process. Eliminates 5 failure modes:
    1. Path fragility (venv rebuild breaks external plist)
    2. No catch-up after sleep (launchd StartCalendarInterval doesn't retry)
    3. Silent failure (KeepAlive=false means no restart)
    4. Dual management (dev/daemon both install plist)
    5. No monitoring (nobody detects "scheduler hasn't run in N hours")

    Runs run_scheduler() in a thread to avoid blocking the event loop
    (it does sync I/O: file reads, network fetches, Bedrock calls).
    """
    # Initial delay: let startup settle (30s) then run immediately to catch up
    await asyncio.sleep(30)

    while True:
        t0 = time.monotonic()
        try:
            logger.info("In-process scheduler: running cycle")
            # run_scheduler is synchronous and long-running (agent tasks up
            # to 480s). Uses dedicated _job_executor to avoid blocking default
            # thread pool (which health endpoint's aiosqlite needs).
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(_job_executor, _run_scheduler_safe)
            logger.info("In-process scheduler: cycle complete")
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("In-process scheduler: cycle failed (will retry next interval)")

        # Elapsed-aware sleep: maintain stable interval regardless of cycle duration
        elapsed = time.monotonic() - t0
        sleep_time = max(0, _SCHEDULER_INTERVAL_SECONDS - elapsed)
        await asyncio.sleep(sleep_time)


def _run_scheduler_safe() -> None:
    """Wrapper that imports and runs the scheduler with error isolation."""
    try:
        from jobs.scheduler import run_scheduler
        run_scheduler()
    except SystemExit:
        # run_scheduler calls sys.exit on config errors — don't kill daemon
        logger.warning("In-process scheduler: run_scheduler called sys.exit (suppressed)")
    except Exception:
        # Re-raise so the async wrapper can log it
        raise


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler."""
    global _startup_complete, _startup_time_ms, _phase_timings
    from core.initialization_manager import initialization_manager

    t0 = time.monotonic()
    phase_timings: dict[str, float] = {}

    # One-time migration: ~/.swarm-ai/.context/ → ~/.swarm-ai/state/
    from jobs.paths import _migrate_legacy_state_dir
    _migrate_legacy_state_dir()

    # Startup
    logger.info(f"Starting {settings.app_name} v{settings.app_version}")
    logger.info(f"Debug mode: {settings.debug}")
    logger.info(f"Database type: {settings.database_type}")
    logger.info(f"Rate limit: {settings.rate_limit_per_minute}/minute")

    # NOTE: SWARMAI_OWNER_PID is set in claude_environment._configure_claude_environment()
    # which runs before any child process is spawned.  Do NOT duplicate it here.

    # Ensure database exists (copy seed DB if needed)
    # Validates: Requirements 2.1, 2.2, 2.5, 2.6, 3.1
    skip_init_pipeline = _ensure_database_initialized()

    if skip_init_pipeline:
        # Fast startup path — seed-sourced or returning user.
        # Create the DB instance (connection pool) without running DDL or migrations.
        logger.info("Fast startup (seed-sourced) — skipping schema DDL, migrations, and full init")
        # Bounded (was unbounded): a stale migration flock must not hang boot forever.
        await _init_db_bounded(skip_schema=True)
        logger.info("Database instance created (schema skipped)")

        t_db = time.monotonic()
        phase_timings["database_ms"] = round((t_db - t0) * 1000)
        logger.info("Phase: database init — %dms", phase_timings["database_ms"])

        # Ensure workspace filesystem exists on disk.
        # The seed DB contains the workspace_config row but NOT the
        # actual directories/files.  For returning users this also
        # heals any missing system-managed items via verify_integrity().
        from database import db as _db
        from core.swarm_workspace_manager import swarm_workspace_manager
        try:
            workspace = await swarm_workspace_manager.ensure_default_workspace(_db)
            initialization_manager._cached_workspace_path = (
                swarm_workspace_manager.expand_path(workspace["file_path"])
            )
            logger.info("Workspace filesystem verified on fast startup path")
        except Exception as e:
            logger.error("Failed to ensure workspace on fast startup: %s", e)

        t_workspace = time.monotonic()
        phase_timings["workspace_ms"] = round((t_workspace - t_db) * 1000)
        logger.info("Phase: workspace verify — %dms", phase_timings["workspace_ms"])

        # Refresh built-in skills and context files — deferred to background
        # so it doesn't block _startup_complete.  The DB already has
        # skill/context data from the previous session (or the seed).
        phase_timings["refresh_defaults_ms"] = 0  # Updated by background task on completion
        asyncio.create_task(_deferred_refresh_defaults("fast path"))
        logger.info("refresh_builtin_defaults deferred to background")
    else:
        # Full initialization path — dev-mode fallback (no seed.db available).
        # Preserve the existing init pipeline exactly.
        logger.info("Full initialization (runtime) — running schema DDL + migrations + init")
        await _init_db_bounded(skip_schema=False)
        logger.info("Database initialized")

        t_db = time.monotonic()
        phase_timings["database_ms"] = round((t_db - t0) * 1000)
        logger.info("Phase: database init — %dms", phase_timings["database_ms"])

        # Check initialization state and run appropriate flow
        # Validates: Requirements 3.1
        if await initialization_manager.is_initialization_complete():
            # Quick validation path - fast startup for returning users
            logger.info("Initialization complete flag is set, running quick validation...")
            if not await initialization_manager.run_quick_validation():
                # Resources missing, fall back to full init
                logger.warning("Quick validation failed, falling back to full initialization...")
                await initialization_manager.run_full_initialization()
            else:
                logger.info("Quick validation passed - fast startup complete")
                
                # Refresh built-in skills and context files — deferred to
                # background since quick validation passed (data exists in DB).
                phase_timings["refresh_defaults_ms"] = 0  # Updated by background task
                asyncio.create_task(_deferred_refresh_defaults("quick-val path"))
                logger.info("refresh_builtin_defaults deferred to background (quick-val path)")
        else:
            # First-time initialization
            logger.info("First-time startup, running full initialization...")
            await initialization_manager.run_full_initialization()

        # On the full-init path, workspace is handled inside the init pipeline.
        # Record workspace_ms as the time from DB init to end of init pipeline.
        t_workspace = time.monotonic()
        phase_timings["workspace_ms"] = round((t_workspace - t_db) * 1000)
        logger.info("Phase: workspace/init pipeline — %dms", phase_timings["workspace_ms"])

        # refresh_defaults_ms: set to 0 if not already set (full init runs it synchronously)
        if "refresh_defaults_ms" not in phase_timings:
            phase_timings["refresh_defaults_ms"] = 0

    # Start channel gateway (deferred to background if channels exist)
    # Validates: Requirements 1.1, 1.2, 1.3, 1.4
    #
    # MODE GUARD: Only daemon and hive run the channel gateway.
    # Subprocess/dev must NOT start it — Socket Mode allows only ONE
    # connection per app token.  Two processes competing causes
    # rapid reconnect churn (770+ "connection closed" events observed).
    phase_timings["gateway_ms"] = 0  # Updated by background task on completion
    _run_mode = _detect_run_mode()
    _gateway_allowed = _run_mode in ("daemon", "hive")

    if not _gateway_allowed:
        channel_gateway._startup_state = "not_started"
        logger.info(
            "Channel gateway skipped — mode=%s (only daemon/hive run channels)",
            _run_mode,
        )
    else:

        _channels_count: int | None = None  # None = query failed, fall back to sync
        try:
            from database import db as _startup_db
            _channels_list = await _startup_db.channels.list()
            _channels_count = len(_channels_list)
        except Exception:
            logger.warning(
                "Failed to query channels count — falling back to synchronous gateway startup"
            )

        if _channels_count == 0:
            # No channels configured — skip gateway startup entirely.
            channel_gateway._startup_state = "not_started"
            logger.info("No channels configured — skipping channel gateway startup")
        elif _channels_count is not None and _channels_count > 0:
            # Channels exist — defer startup to a background task so it
            # doesn't block _startup_complete.
            async def _deferred_gateway_startup() -> None:
                _t_start = time.monotonic()
                try:
                    channel_gateway._startup_state = "starting"
                    await channel_gateway.startup()
                    channel_gateway._startup_state = "started"
                    logger.info(
                        "Channel gateway started (deferred, %d channels)",
                        _channels_count,
                    )
                except Exception:
                    channel_gateway._startup_state = "failed"
                    logger.exception("Deferred channel gateway startup failed")
                finally:
                    elapsed = round((time.monotonic() - _t_start) * 1000)
                    _phase_timings_ref = _phase_timings
                    if _phase_timings_ref is not None:
                        _phase_timings_ref["gateway_ms"] = elapsed
                    logger.info("Phase: channel gateway (deferred) — %dms", elapsed)

            asyncio.create_task(_deferred_gateway_startup())
            logger.info(
                "Channel gateway startup deferred to background (%d channels)",
                _channels_count,
            )
        else:
            # Fallback: channels count query failed (None) — run synchronously
            # (preserves current behavior).
            await channel_gateway.startup()
            channel_gateway._startup_state = "started"
            logger.info("Channel gateway started (synchronous fallback)")

    # --- Initialize file-based config and permission components ---
    # Requirements: 1.2, 4.7, 4.8, 9.3
    from core.app_config_manager import AppConfigManager
    from core.credential_validator import CredentialValidator
    from routers.settings import set_config_manager

    app_config = AppConfigManager.instance()
    app_config.load()
    logger.info("AppConfigManager loaded (config.json)")

    # Load dangerous command patterns (creates ~/.swarm-ai/dangerous_commands.json if missing)
    from core.security_hooks import load_dangerous_patterns
    dangerous_patterns = load_dangerous_patterns()
    logger.info("Dangerous command patterns loaded (%d patterns)", len(dangerous_patterns))

    cred_validator = CredentialValidator()
    logger.info("CredentialValidator initialized")

    t_config = time.monotonic()
    phase_timings["config_ms"] = round((t_config - t_workspace) * 1000)
    logger.info("Phase: config/permission load — %dms", phase_timings["config_ms"])

    # Pre-warm boto3 import so the first STS call doesn't pay the ~8s
    # PyInstaller import cost on the hot path.  This runs in a background
    # thread to avoid blocking startup.
    async def _prewarm_boto3():
        try:
            await asyncio.to_thread(lambda: __import__("boto3"))
            logger.info("boto3 pre-warmed for credential validation")
        except Exception:
            logger.debug("boto3 pre-warm failed (non-critical)", exc_info=True)
    asyncio.create_task(_prewarm_boto3())

    # Generate permissions.json for user visibility
    try:
        ws_path = getattr(initialization_manager, '_cached_workspace_path', None)
        if ws_path:
            _generate_permissions_json(Path(ws_path), dangerous_patterns)
            logger.info("permissions.json generated at %s/.claude/settings/", ws_path)
        else:
            logger.warning("Workspace path not available — skipping permissions.json generation")
    except Exception as exc:
        logger.warning("Failed to generate permissions.json (non-critical): %s", exc)

    # ── Recover crash checkpoint (if any) before session infra starts ─
    try:
        from hooks.daily_activity_hook import recover_crash_checkpoint as _recover_checkpoint
        _recovered = _recover_checkpoint()
        if _recovered:
            logger.info("Crash checkpoint recovered into DailyActivity")
    except Exception:
        logger.debug("Crash checkpoint recovery skipped (non-fatal)", exc_info=True)

    # ── Session lifecycle hooks ──────────────────────────────────────
    from core.session_hooks import SessionLifecycleHookManager, BackgroundHookExecutor
    from core.summarization import SummarizationPipeline
    from core.compliance import ComplianceTracker
    from hooks.daily_activity_hook import DailyActivityExtractionHook
    from hooks.knowledge_backflow_hook import KnowledgeBackflowHook
    from hooks.auto_commit_hook import WorkspaceAutoCommitHook
    from hooks.code_change_feed import CodeChangeFeed
    from hooks.context_health_hook import ContextHealthHook
    from hooks.distillation_hook import DistillationTriggerHook
    from hooks.evolution_maintenance_hook import EvolutionMaintenanceHook
    from hooks.improvement_writeback_hook import ImprovementWritebackHook
    from hooks.todo_lifecycle_hook import TodoLifecycleHook
    from routers.memory import set_compliance_tracker

    summarization_pipeline = SummarizationPipeline()
    compliance_tracker = ComplianceTracker()
    # 180s accommodates evolution_maintenance_hook which mines 1000+
    # transcripts + calls Bedrock LLM (~90s).  All other hooks finish <5s.
    # The evolution hook now runs in a thread pool (run_in_executor) so
    # the timeout actually fires instead of being bypassed by blocking code.
    hook_manager = SessionLifecycleHookManager(timeout_seconds=180.0)

    # Create fire-and-forget executor — hooks never block the chat path
    hook_executor = BackgroundHookExecutor(hook_manager)

    # Order matters: extraction → backflow → commit → distillation → health → evolution → improvement
    # Distillation BEFORE health so embeddings capture freshly-distilled entries.
    hook_manager.register(DailyActivityExtractionHook(
        summarization_pipeline=summarization_pipeline,
        compliance_tracker=compliance_tracker,
    ))
    # Knowledge Backflow: capture high-value analysis outputs as Knowledge/Notes/ pages.
    # After DailyActivity (shares message read) but before auto-commit (file gets committed).
    hook_manager.register(KnowledgeBackflowHook())
    # Pass shared git lock to auto-commit hook to prevent .git/index.lock contention
    hook_manager.register(WorkspaceAutoCommitHook(git_lock=hook_executor.git_lock))
    # Code Change Feed: analyze post-commit diff → propose TECH.md updates (Channel 1)
    hook_manager.register(CodeChangeFeed(git_lock=hook_executor.git_lock))
    hook_manager.register(DistillationTriggerHook())
    # Context health: light refresh every session (if changed), deep check daily.
    # Runs AFTER distillation so embedding sync picks up fresh MEMORY.md entries.
    hook_manager.register(ContextHealthHook())
    hook_manager.register(EvolutionMaintenanceHook())
    # IMPROVEMENT.md write-back: closes the DDD learning loop.
    # Runs after auto-commit so workspace state is settled.
    hook_manager.register(ImprovementWritebackHook(
        workspace_path=app_config.get("workspace_path", str(get_app_data_dir() / "SwarmWS")),
    ))
    # ToDo lifecycle: auto-complete bound todos, implicit file matching
    # Runs after auto-commit so git log reflects the session's work.
    hook_manager.register(TodoLifecycleHook())

    # UserObserverHook: tracks user interaction patterns for evolution
    try:
        from hooks.user_observer_hook import UserObserverHook
        hook_manager.register(UserObserverHook())
        logger.info("Registered UserObserverHook")
    except Exception as exc:
        logger.warning("UserObserverHook registration failed: %s", exc)

    # SkillMetricsHook: records skill invocation metrics post-session
    try:
        from hooks.skill_metrics_hook import SkillMetricsHook
        hook_manager.register(SkillMetricsHook())
        logger.info("Registered SkillMetricsHook")
    except Exception as exc:
        logger.warning("SkillMetricsHook registration failed: %s", exc)

    # Wire hooks into session_registry (new architecture)
    set_compliance_tracker(compliance_tracker)
    logger.info("Session lifecycle hooks registered (8 hooks, background executor)")

    # ── Initialize new session architecture ──────────────────────────
    session_registry.initialize(app_config)
    # Wire hooks AFTER initialize so lifecycle_manager exists
    session_registry.configure_hooks(executor=hook_executor, manager=hook_manager)
    logger.info("SessionRouter architecture initialized")
    await session_registry.start_lifecycle()
    logger.info("LifecycleManager started at startup")

    # Root-1 SSOT: reopen any pending messages left in the 'claimed' phase by a
    # crash (claimed_at set, sent=0) so they re-drain on the next IDLE instead of
    # being stuck forever. Idempotent, DB-only, non-fatal — never blocks startup.
    try:
        from core.session_pending import reopen_dangling_claims
        reopened = await reopen_dangling_claims()
        if reopened:
            logger.info("Reopened %d dangling pending-message claim(s)", reopened)
    except Exception as exc:  # pragma: no cover - non-fatal startup guard
        logger.warning("reopen_dangling_claims failed (non-fatal): %s", exc)
    # ─────────────────────────────────────────────────────────────────

    t_agent = time.monotonic()
    phase_timings["session_infra_ms"] = round((t_agent - t_config) * 1000)
    logger.info("Phase: session infrastructure — %dms", phase_timings["session_infra_ms"])

    # Wire AppConfigManager into Settings router (DI).
    # Skip if already configured (e.g. test fixtures may pre-set).
    from routers import settings as _settings_mod
    if _settings_mod._config_manager is None:
        set_config_manager(app_config)
        logger.info("Settings router configured with AppConfigManager")
    else:
        logger.debug("Settings router already configured (skipping overwrite)")

    # Wire up TSCC state manager for the tscc router
    from core.tscc_state_manager import TSCCStateManager
    from routers.tscc import register_tscc_dependencies

    _tscc_state_manager = TSCCStateManager()
    register_tscc_dependencies(_tscc_state_manager)
    logger.info("TSCC state manager initialized")

    # Kill ALL leftover claude CLI processes from previous instance.
    # At startup, no claude processes should be running — any that exist are
    # zombies from a crash or unclean shutdown. These hold vnodes and can
    # cause kernel panics (COE 2026-03-15: 80 zombies -> vnode exhaustion -> panic).
    startup_killed = session_registry.kill_all_claude_processes()
    if startup_killed:
        logger.warning("Killed %d leftover claude process(es) at startup", startup_killed)

    # Write backend.json so Tauri (or other processes) can discover us
    global _backend_start_monotonic
    _backend_start_monotonic = time.monotonic()
    backend_port = _detect_backend_port()
    backend_mode = _detect_run_mode()
    write_backend_json(port=backend_port, mode=backend_mode)
    logger.info("backend.json written (port=%d, mode=%s)", backend_port, backend_mode)

    # Resolve the bundled CLI version ONCE at boot (subprocess — must never
    # run inside the per-request health handler, which is polled every 5s).
    global _cli_version
    _cli_version = _resolve_cli_version()
    logger.info("runtime versions: sdk=%s cli=%s", _sdk_version, _cli_version)

    # Mark startup as complete - health check will now return healthy
    _startup_complete = True
    total_ms = round((time.monotonic() - t0) * 1000)
    _startup_time_ms = total_ms
    _phase_timings = phase_timings
    logger.info(
        "Startup complete — total %dms (db=%dms, workspace=%dms, config=%dms, session=%dms)",
        total_ms,
        phase_timings.get("database_ms", 0),
        phase_timings.get("workspace_ms", 0),
        phase_timings.get("config_ms", 0),
        phase_timings.get("session_infra_ms", 0),
    )
    logger.info("Startup complete - ready to serve requests")

    # ── Observability: surface any partial binary deploy (deployed_no_restart) ──
    # The /api/system/upgrade upgrader writes a result file; a
    # ``deployed_no_restart`` status means a new binary was rsynced but the
    # daemon never came back (bootstrap failed). The guardian normally
    # self-heals this, so it's observability-only — but if WE are the daemon
    # that came up, it means recovery already happened; log it so the partial
    # deploy is visible rather than silent (LL18 — never silent-degrade).
    try:
        from core.daemon_guard import scan_deployed_no_restart
        for finding in scan_deployed_no_restart():
            logger.warning(
                "Partial deploy detected (deployed_no_restart): version=%s file=%s "
                "— guardian likely recovered the daemon; review upgrade logs.",
                finding.get("version", "?"), finding.get("file", "?"),
            )
    except Exception:
        logger.debug("deployed_no_restart scan skipped", exc_info=True)

    # ── Start managed subsidiary services (Slack bot, etc.) ─────────────
    # Deferred to background so it never blocks startup.  Services
    # discover the backend via ~/.swarm-ai/backend.port written here.
    from core.service_manager import service_manager as _svc_mgr

    async def _deferred_services_startup() -> None:
        try:
            ws_path = initialization_manager.get_cached_workspace_path()
            backend_port = _detect_backend_port()
            await _svc_mgr.start_all(ws_path, backend_port)
        except Exception:
            logger.exception("Managed services startup failed (non-fatal)")

    asyncio.create_task(_deferred_services_startup())

    # ── In-process job scheduler (daemon/hive only) ───────────────────
    # Replaces the external com.swarmai.scheduler launchd job.
    # Benefits: no path fragility, catches up after sleep, self-monitoring,
    # and inherits the daemon's credentials/env.
    _scheduler_task: asyncio.Task | None = None
    if backend_mode in ("daemon", "hive"):
        _scheduler_task = asyncio.create_task(
            _run_inprocess_scheduler(),
            name="inprocess-scheduler",
        )
        logger.info("In-process scheduler started (60min interval)")

    # ── Code Intelligence FS watchers (daemon/hive only) ─────────────
    # Start watchers for indexed projects so code-intel stays fresh.
    if backend_mode in ("daemon", "hive"):
        asyncio.create_task(_start_code_intel_watchers())

    # Readiness sampler (run_7e8a2030): samples DB + auth health OFF the /health
    # request path so liveness never blocks on a slow dependency. See
    # core/readiness_sampler.py + the health_check liveness/readiness split.
    from core.readiness_sampler import readiness_sampler_loop
    _readiness_task = asyncio.create_task(readiness_sampler_loop())

    # Independent-thread liveness heartbeat (run_5b0d6ec3): decouples "process
    # alive" from the asyncio loop/GIL so a *busy* backend (heavy CPU on the loop
    # thread) can never be misread as *dead* by the Tauri watchdog → false
    # offline. daemon/hive only — the subprocess (dev/Windows/Linux) path has no
    # launchd-managed liveness contract and the Tauri watchdog reads the file
    # only in daemon mode. See core/heartbeat.py.
    _loop_tick_task = None
    if backend_mode in ("daemon", "hive"):
        from core import heartbeat
        heartbeat.start_heartbeat()
        _loop_tick_task = asyncio.create_task(heartbeat.loop_tick_loop())

    yield
    # Shutdown
    _startup_complete = False
    logger.info("Shutting down...")
    if _readiness_task and not _readiness_task.done():
        _readiness_task.cancel()
        try:
            await _readiness_task
        except asyncio.CancelledError:
            pass
    # Stop the liveness heartbeat (run_5b0d6ec3): cancel the loop-tick task +
    # stop the independent writer thread (removes the heartbeat file so a
    # restart doesn't read a stale one from this process).
    if _loop_tick_task and not _loop_tick_task.done():
        _loop_tick_task.cancel()
        try:
            await _loop_tick_task
        except asyncio.CancelledError:
            pass
    try:
        from core import heartbeat
        heartbeat.stop_heartbeat()
    except Exception:
        logger.debug("heartbeat stop on shutdown skipped", exc_info=True)
    # Drain the SQLite connection pool (run_7e8a2030) — join aiosqlite worker
    # threads so shutdown is clean and no connection leaks.
    try:
        from database.sqlite import close_all_pools
        await close_all_pools()
        logger.info("SQLite connection pools closed")
    except Exception:
        logger.debug("pool close on shutdown skipped", exc_info=True)
    # Stop Code Intelligence FS watchers
    try:
        from core.code_intel.watcher import stop_all_watchers
        await stop_all_watchers()
        logger.info("Code Intelligence watchers stopped")
    except Exception:
        logger.debug("Code Intel watcher shutdown skipped", exc_info=True)
    if _scheduler_task and not _scheduler_task.done():
        _scheduler_task.cancel()
        try:
            await _scheduler_task
        except asyncio.CancelledError:
            pass
        logger.info("In-process scheduler stopped")
    await _svc_mgr.stop_all()
    logger.info("Managed services stopped")
    await channel_gateway.shutdown()
    logger.info("Channel gateway stopped")
    await session_registry.stop_lifecycle()
    logger.info("LifecycleManager stopped")
    await session_registry.disconnect_all()
    logger.info("All sessions disconnected")
    remove_backend_json(startup_mode=backend_mode)
    logger.info("backend.json removed (mode=%s)", backend_mode)


# Create FastAPI application
app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description="AI Agent Platform API - Manage agents, skills, and MCP servers",
    lifespan=lifespan,
)

# Configure rate limiter
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# Configure CORS
# In production, you should set CORS_ORIGINS environment variable
# to restrict origins to your domain(s)
cors_origins = settings.cors_origins
if settings.debug:
    # In debug mode, also allow common development origins
    cors_origins = list(set(cors_origins + [
        "http://localhost:5173",  # Vite default
        "http://localhost:3000",  # CRA default
        "http://127.0.0.1:5173",
        "http://127.0.0.1:3000",
    ]))

# Hive mode: Caddy serves frontend on the same origin, so CORS isn't strictly
# needed. But add the domain explicitly for direct API access from other tools.
# Only HTTPS — Caddy enforces TLS termination; HTTP should never reach the app.
_hive_domain = os.environ.get("HIVE_DOMAIN", "")
if _hive_domain:
    cors_origins = list(set(cors_origins + [
        f"https://{_hive_domain}",
    ]))

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS", "PATCH"],
    allow_headers=[
        "Authorization",
        "Content-Type",
        "Accept",
        "Origin",
        "X-Requested-With",
        "X-Request-ID",
    ],
    expose_headers=[
        "X-Request-ID",
        "X-RateLimit-Limit",
        "X-RateLimit-Remaining",
        "X-RateLimit-Reset",
        "Retry-After",
    ],
    max_age=600,  # Cache preflight requests for 10 minutes
)

# Setup error handlers
setup_error_handlers(app)

# Include routers
app.include_router(auth_router, prefix="/api/auth", tags=["auth"])
app.include_router(agents_router, prefix="/api/agents", tags=["agents"])
app.include_router(skills_router, prefix="/api/skills", tags=["skills"])
app.include_router(mcp_router, prefix="/api/mcp", tags=["mcp"])
app.include_router(chat_router, prefix="/api/chat", tags=["chat"])
app.include_router(chat_threads_router, prefix="/api", tags=["chat-threads"])
app.include_router(workspace_router, prefix="/api/workspace", tags=["workspace"])
app.include_router(settings_router, prefix="/api/settings", tags=["settings"])
app.include_router(plugins_router, prefix="/api/plugins", tags=["plugins"])
app.include_router(tasks_router, prefix="/api/tasks", tags=["tasks"])
app.include_router(channels_router, prefix="/api/channels", tags=["channels"])
app.include_router(system_router, prefix="/api/system", tags=["system"])
app.include_router(todos_router, prefix="/api/todos", tags=["todos"])
app.include_router(search_router, prefix="/api/search", tags=["search"])
app.include_router(workspace_config_router, prefix="/api/workspaces", tags=["workspace-config"])
app.include_router(workspace_api_router, prefix="/api", tags=["workspace-api"])
app.include_router(projects_router, prefix="/api", tags=["projects"])
app.include_router(tscc_router, prefix="/api", tags=["tscc"])
app.include_router(autonomous_jobs_router, prefix="/api/autonomous-jobs", tags=["autonomous-jobs"])
app.include_router(pipelines_router, prefix="/api/pipelines", tags=["pipelines"])
app.include_router(pollinate_router, prefix="/api/pollinate", tags=["pollinate"])
app.include_router(jobs_router, tags=["jobs"])  # prefix already set in router
app.include_router(artifacts_router, prefix="/api", tags=["artifacts"])
app.include_router(escalations_router, tags=["escalations"])
app.include_router(voice_router, prefix="/api/voice", tags=["voice"])
app.include_router(hive_router, prefix="/api/hive", tags=["hive"])

# DDD Cultivation API (list/approve/reject proposals)
from routers.cultivation import router as cultivation_router
app.include_router(cultivation_router, tags=["cultivation"])

# Memory compliance router (no prefix — router defines /api internally)
from routers.memory import router as memory_router
app.include_router(memory_router, tags=["memory"])

# Code Intelligence API
from routers.code_intel import router as code_intel_router
app.include_router(code_intel_router, prefix="/api/code-intel", tags=["code-intel"])

# OS Eval API
from routers.eval import router as eval_router
app.include_router(eval_router, prefix="/api/eval", tags=["eval"])

# DDD Brain Hub API (read-only projection over ddd_paths + parse_entries + git)
from routers.ddd_brain import router as ddd_brain_router
app.include_router(ddd_brain_router, prefix="/api/ddd", tags=["ddd-brain"])

from routers.library_api import router as library_router
app.include_router(library_router, tags=["library"])  # prefix set in router

# Register development-only router when DEBUG=true
if settings.debug:
    from routers.dev import router as dev_router
    app.include_router(dev_router, prefix="/api/dev", tags=["dev"])


@app.get("/health")
async def health_check():
    """Health check endpoint.
    
    Returns healthy only after the lifespan startup has completed.
    This prevents race conditions where the frontend tries to load
    resources before they're ready.
    """
    # Check runtime flag - this is set after lifespan startup completes
    if not _startup_complete:
        return {
            "status": "initializing",
            "version": settings.app_version,
            "sdk": "claude-agent-sdk",
            "sdk_version": _sdk_version,
            "cli_version": _cli_version,
            # boto3 is not pre-warmed yet during startup — never call STS here.
            "auth": "unknown",
        }
    
    # PE Review Finding #5: Use property directly, not hasattr
    pending_hooks = (
        session_registry.hook_executor.pending_count
        if session_registry.hook_executor
        else 0
    )

    # LIVENESS / READINESS SEPARATION (run_7e8a2030): the DB + auth checks used to
    # run HERE on the request critical path (asyncio.gather with 2s/1s caps). Under
    # executor-thread-pool starvation (unpooled aiosqlite → 20+ threads) even a
    # 2s-capped wait_for could not get SCHEDULED, so the /health round-trip blew past
    # the Rust watchdog's 3s budget → false "Backend offline" + disabled inputs, while
    # the daemon was alive. Fix: liveness (this handler) does ZERO awaited I/O — it
    # reads a snapshot the background readiness sampler maintains off the request path
    # (core/readiness_sampler.py). The dependency signal still reaches the frontend
    # banner via db_healthy/auth below; it just no longer GATES liveness or adds
    # latency. A stale snapshot (sampler wedged) reports "unknown", never a frozen value.
    from core.readiness_sampler import readiness_cache
    _readiness = readiness_cache.snapshot()
    _db_ready = _readiness["db_healthy"]  # True | False | None(unknown)
    auth_result = _readiness["auth"]

    if _db_ready is None:
        # Not yet sampled OR sampler stale → unknown. Liveness stays healthy (the
        # process IS serving this request); we just can't assert DB state.
        db_healthy: Any = "unknown"
    elif _db_ready is False:
        db_healthy = False
    else:
        db_healthy = True

    if isinstance(auth_result, str) and auth_result in ("valid", "expired", "unknown"):
        auth_status = auth_result
    else:
        # TimeoutError / any exception / unexpected value → unknown (fail-open).
        auth_status = "unknown"

    # status="healthy" unless readiness SAMPLED the DB as genuinely down (False).
    # "unknown" (not-yet-sampled or stale snapshot) stays healthy — the process IS
    # serving this request, and a transient sampler gap must not flip the UI to
    # "backend not ready". Only a confirmed DB-down degrades. Note: the DB check no
    # longer runs on THIS request path (readiness is sampled off-path), so a slow
    # DB can never drag /health latency — that severing is the offline-flap fix.
    status = "healthy" if db_healthy is not False else "degraded"

    # P3: Expose channel gateway state so monitoring can detect
    # "healthy but Slack is down" (silent failure of deferred startup)
    gw_state = channel_gateway.startup_state

    return {
        "status": status,
        "version": settings.app_version,
        "sdk": "claude-agent-sdk",
        "sdk_version": _sdk_version,
        "cli_version": _cli_version,
        "pending_hook_tasks": pending_hooks,
        "boot_id": _boot_id,
        "db_healthy": db_healthy,
        "channel_gateway": gw_state,
        "auth": auth_status,
    }


@app.get("/api/system/verify-import")
async def verify_import(module: str):
    """Check if a module is importable in this binary. Used by verify_build.py.

    Gated behind SWARMAI_VERIFY_BUILD=1 to prevent arbitrary import in
    normal operation.
    """
    if os.environ.get("SWARMAI_VERIFY_BUILD") != "1":
        return {"available": False, "error": "verify endpoints require SWARMAI_VERIFY_BUILD=1"}
    try:
        __import__(module)
        return {"available": True, "module": module}
    except ImportError as e:
        return {"available": False, "module": module, "error": str(e)}


@app.get("/api/system/verify-data")
async def verify_data(path: str):
    """Check if a bundled data file/dir exists. Used by verify_build.py.

    Gated behind SWARMAI_VERIFY_BUILD=1. Path traversal blocked.
    """
    if os.environ.get("SWARMAI_VERIFY_BUILD") != "1":
        return {"exists": False, "detail": "verify endpoints require SWARMAI_VERIFY_BUILD=1"}
    if ".." in path or path.startswith("/"):
        return {"exists": False, "detail": "invalid path"}

    import sys as _sys
    # Check in _MEIPASS (PyInstaller) or relative to backend dir
    bases = []
    if getattr(_sys, "frozen", False):
        bases.append(Path(_sys._MEIPASS))
    bases.append(Path(__file__).resolve().parent)

    for base in bases:
        target = base / path
        if target.exists():
            kind = "directory" if target.is_dir() else "file"
            return {"exists": True, "path": str(target), "detail": kind}
    return {"exists": False, "path": path, "detail": f"not found in {[str(b) for b in bases]}"}


@app.get("/api/system/verify-native")
async def verify_native(path: str):
    """Check if a native extension is loadable. Used by verify_build.py.

    Gated behind SWARMAI_VERIFY_BUILD=1.
    """
    if os.environ.get("SWARMAI_VERIFY_BUILD") != "1":
        return {"loadable": False, "detail": "verify endpoints require SWARMAI_VERIFY_BUILD=1"}

    import sqlite3
    # path format: "sqlite_vec/vec0" (without .dylib suffix)
    parts = path.split("/", 1)
    if len(parts) == 2 and parts[0] == "sqlite_vec":
        try:
            import sqlite_vec
            conn = sqlite3.connect(":memory:")
            conn.enable_load_extension(True)
            sqlite_vec.load(conn)
            conn.enable_load_extension(False)
            version = conn.execute("select vec_version()").fetchone()[0]
            conn.close()
            return {"loadable": True, "detail": f"sqlite-vec {version}"}
        except Exception as e:
            return {"loadable": False, "detail": str(e)}
    # tree_sitter/parse: a FUNCTIONAL AST probe, not a bare import. verify-import
    # only does __import__('tree_sitter'), which SUCCEEDS even when the AST path is
    # broken (run_2e46f2af: the old get_parser returned an old-ABI object whose
    # .parse(bytes) raised — import fine, parsing dead). So the real gate must
    # construct a parser and confirm a node comes back — mirroring the sqlite_vec
    # load-and-call probe above.
    if len(parts) == 2 and parts[0] == "tree_sitter":
        try:
            import tree_sitter
            from tree_sitter_language_pack import get_language
            parser = tree_sitter.Parser(get_language("python"))
            tree = parser.parse(b"def _probe():\n    return 1\n")
            root_type = tree.root_node.type
            if root_type != "module":
                return {"loadable": False,
                        "detail": f"tree-sitter parsed but root type is {root_type!r}, expected 'module'"}
            return {"loadable": True, "detail": f"tree-sitter AST functional (root={root_type})"}
        except Exception as e:
            return {"loadable": False, "detail": f"tree-sitter AST non-functional: {e}"}
    return {"loadable": False, "detail": f"unknown native extension: {path}"}


@app.get("/api/system/capabilities")
async def get_capabilities():
    """Report all capability flags for this binary. Shows dev/prod divergence at a glance.

    Intentionally NOT gated behind SWARMAI_VERIFY_BUILD — this endpoint
    is always available for runtime diagnostics (e.g., Titus reports a
    broken feature → curl capabilities to see what's degraded). The
    verify-import/verify-data/verify-native endpoints are gated because
    they accept arbitrary input; this one has no parameters.
    """
    caps = {}

    # sqlite_vec
    try:
        from core.vec_db import VEC_AVAILABLE
        caps["sqlite_vec"] = VEC_AVAILABLE
    except ImportError:
        caps["sqlite_vec"] = False

    # psutil
    try:
        import psutil  # noqa: F401
        caps["psutil"] = True
    except ImportError:
        caps["psutil"] = False

    # Slack
    try:
        import slack_bolt  # noqa: F401
        caps["slack_bolt"] = True
    except ImportError:
        caps["slack_bolt"] = False

    # Key local modules
    for mod in ["core.recall_engine", "core.manifest_loader", "core.llm_optimizer",
                "scripts.locked_write", "hooks.distillation_hook"]:
        try:
            __import__(mod)
            caps[mod.split(".")[-1]] = True
        except ImportError:
            caps[mod.split(".")[-1]] = False

    # Frozen mode
    import sys as _sys
    caps["frozen"] = getattr(_sys, "frozen", False)
    caps["mode"] = os.environ.get("SWARMAI_MODE", "unknown")

    return {"capabilities": caps}


@app.get("/api/system/mode")
async def get_system_mode():
    """Return the backend's running mode (daemon or hive)."""
    uptime = time.monotonic() - _backend_start_monotonic if _backend_start_monotonic else 0
    return {
        "mode": _detect_run_mode(),
        "pid": os.getpid(),
        "port": _detect_backend_port(),
        "uptime_seconds": round(uptime, 1),
    }


@app.post("/shutdown")
async def shutdown():
    """Graceful shutdown endpoint - disconnects all Claude SDK clients.

    Blocked in daemon and hive modes — these run 24/7 background services
    (Slack channels, scheduled jobs) that must survive app window close.
    Allowed in dev and subprocess modes (no persistent background services).
    """
    mode = _detect_run_mode()
    if mode in ("daemon", "hive"):
        logger.warning("Shutdown endpoint blocked in %s mode", mode)
        return JSONResponse(
            status_code=403,
            content={"status": "forbidden", "reason": f"shutdown disabled in {mode} mode"},
        )
    logger.info("Shutdown endpoint called - disconnecting all clients")
    t0 = time.monotonic()
    try:
        # Timeout 8s — 2s shorter than Tauri's 10s curl timeout.
        # Ensures normal path finishes before force-kill.
        await asyncio.wait_for(session_registry.disconnect_all(), timeout=8.0)
    except asyncio.TimeoutError:
        logger.warning("disconnect_all timed out after 8s — proceeding with shutdown")
    elapsed = time.monotonic() - t0
    logger.info("Shutdown endpoint completed in %.2fs", elapsed)
    return {"status": "shutting_down"}


# ── Upgrade (daemon/hive only) ────────────────────────────────────────────────

# Upgrade state — protected by asyncio.Lock (F3: race condition fix)
_upgrade_lock = asyncio.Lock()
_upgrade_in_progress: bool = False
_upgrade_started_at: float | None = None
_upgrade_result_file: str | None = None

# Timeout for auto-clearing stuck upgrades (F2: lock leak prevention)
_UPGRADE_TIMEOUT_S = 180


def _resolve_project_root() -> Path | None:
    """Locate swarmai project root. Works in dev, frozen, and hive contexts.

    Resolution order (F5: robust project root detection):
    1. SWARMAI_PROJECT_ROOT env var (explicit override)
    2. __file__ relative (dev mode: backend/main.py → swarmai/)
    3. Hardcoded fallback (XG's Mac layout)
    """
    # 1. Environment variable — most reliable, works everywhere
    env_root = os.environ.get("SWARMAI_PROJECT_ROOT")
    if env_root:
        p = Path(env_root)
        if (p / "prod.sh").exists():
            return p

    # 2. Relative to __file__ (dev mode: swarmai/backend/main.py)
    if not getattr(sys, "frozen", False):
        p = Path(__file__).resolve().parent.parent
        if (p / "prod.sh").exists():
            return p

    # 3. Hardcoded fallback (frozen daemon on XG's Mac)
    p = Path.home() / "Desktop" / "SwarmAI-Workspace" / "swarmai"
    if (p / "prod.sh").exists():
        return p

    return None


def _resolve_sidecar_binary(project_root: Path) -> Path | None:
    """Locate built backend binary, platform-aware (F4: not just aarch64-apple-darwin).

    Checks current platform's triple first, then falls back to any existing triple.
    """
    import platform as _platform

    # Determine current platform triple
    machine = _platform.machine()
    arch = "aarch64" if machine in ("arm64", "aarch64") else "x86_64"
    if sys.platform == "darwin":
        triple = f"python-backend-{arch}-apple-darwin"
    elif sys.platform == "linux":
        triple = f"python-backend-{arch}-unknown-linux-gnu"
    else:
        triple = f"python-backend-{arch}-pc-windows-msvc"

    binaries_dir = project_root / "desktop" / "src-tauri" / "binaries"

    # Try platform-specific path first
    candidate = binaries_dir / triple / "python-backend"
    if candidate.exists():
        return candidate

    # Fallback: find any existing binary (covers cross-compilation scenarios)
    if binaries_dir.exists():
        for child in binaries_dir.iterdir():
            if child.is_dir() and (child / "python-backend").exists():
                return child / "python-backend"

    return None


@app.post("/api/system/upgrade")
async def upgrade_daemon():
    """Trigger a binary upgrade without killing the agent session.

    The daemon spawns a detached upgrader process (in a new session/process group)
    that:
      1. SIGKILL daemon (instant death — SSE cannot block)
      2. bootout (deregister service — disables KeepAlive to prevent race)
      3. rsync new binary (safe — no live process)
      4. Deploy fresh plist + bootstrap (re-register + start)

    The upgrader survives daemon death because ``start_new_session=True`` puts it
    in a separate process group that launchd won't kill.

    Allowed ONLY in daemon/hive modes (requires KeepAlive/Restart=always).
    """
    global _upgrade_in_progress, _upgrade_result_file, _upgrade_started_at

    mode = _detect_run_mode()
    if mode not in ("daemon", "hive"):
        return JSONResponse(
            status_code=403,
            content={
                "status": "forbidden",
                "reason": f"upgrade requires daemon/hive mode (current: {mode})",
            },
        )

    # F3: asyncio.Lock prevents check-then-act race between concurrent requests
    async with _upgrade_lock:
        if _upgrade_in_progress:
            # F2: auto-clear if upgrade process timed out (crash recovery)
            if _upgrade_started_at and (time.time() - _upgrade_started_at) > _UPGRADE_TIMEOUT_S:
                logger.warning("Clearing stale upgrade lock (started %.0fs ago)", time.time() - _upgrade_started_at)
                _upgrade_in_progress = False
                _upgrade_started_at = None
            else:
                return JSONResponse(
                    status_code=409,
                    content={
                        "status": "conflict",
                        "reason": "upgrade already in progress",
                        "result_file": _upgrade_result_file,
                    },
                )

        # F5: Robust project root resolution
        project_root = _resolve_project_root()
        if project_root is None:
            return JSONResponse(
                status_code=400,
                content={
                    "status": "error",
                    "reason": "Cannot locate swarmai project root. Set SWARMAI_PROJECT_ROOT env var.",
                },
            )

        # F4: Platform-aware binary detection
        binary_path = _resolve_sidecar_binary(project_root)
        if binary_path is None:
            return JSONResponse(
                status_code=400,
                content={
                    "status": "error",
                    "reason": f"No built binary found in {project_root}/desktop/src-tauri/binaries/. Run build first.",
                },
            )

        # Set lock state inside the critical section (F3: atomic check-and-set)
        upgrade_id = uuid.uuid4().hex[:8]
        result_file = f"/tmp/swarm-upgrade-{upgrade_id}.json"
        _upgrade_result_file = result_file
        _upgrade_in_progress = True
        _upgrade_started_at = time.time()

    # Upgrader script: deploy directly in Python (no shell subprocess).
    # Why not shell scripts (prod.sh/dev.sh)? Because shells are daemon children —
    # killing daemon sends SIGHUP → shell dies → result file never written → lock stuck.
    # Instead: pure Python deployer in a new session group (start_new_session=True).
    # Sequence: SIGKILL + bootout + rsync + bootstrap. Kill is atomic — anything
    # after it is best-effort (process survives in its own session group).
    # Use repr() for Python string literals (not shlex.quote which is for shell).
    safe_result_file = repr(result_file)
    safe_binary_path = repr(str(binary_path.parent))  # onedir bundle directory
    safe_project_root = repr(str(project_root))

    upgrader_script = f"""
import subprocess, json, time, pathlib, os

result_file = {safe_result_file}
bundle_src = {safe_binary_path}
project_root = {safe_project_root}
daemon_dir = os.path.expanduser("~/.swarm-ai/daemon")
version_file = os.path.join(daemon_dir, ".version")
env = {{**os.environ, "PATH": "/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin"}}
TS_FMT = "%Y-%m-%d %H:%M:%S"

result = {{"status": "unknown", "completed_at": time.strftime(TS_FMT)}}

try:
    os.makedirs(daemon_dir, exist_ok=True)
    uid = os.getuid()

    # Step 1: Two-phase stop: SIGKILL (instant death) + bootout (deregister).
    # Why not bootout alone? bootout sends SIGTERM → waits ExitTimeOut (15-20s) → SIGKILL.
    # SSE streams block SIGTERM indefinitely → guaranteed 15-20s wait. Too slow.
    # Why not kill alone? KeepAlive restarts old binary in ~1-3s → rsync races.
    # Correct combo: kill first (instant), then bootout (deregister, no process = instant).
    gui_target = "gui/" + str(uid) + "/com.swarmai.backend"

    # 1-sentinel: write the intent sentinel BEFORE the SIGKILL+bootout below so
    # the guardian agent knows this deregistration is intentional and must NOT
    # re-bootstrap the daemon mid-rsync (bootstrapping the OLD binary while
    # rsync is replacing it corrupts the onedir bundle — COE 2026-05-01
    # PYZ/zlib corruption). Cleared after a confirmed-healthy bootstrap below.
    sentinel_path = os.path.expanduser("~/.swarm-ai/.daemon-intentional-down")
    try:
        pathlib.Path(sentinel_path).write_text(json.dumps({{
            "reason": "binary upgrade", "written_by": "upgrade",
            "written_at": time.time(), "written_at_iso": time.strftime(TS_FMT),
        }}))
    except OSError:
        pass

    # 1a: SIGKILL — instant process death (no SIGTERM wait, no SSE blocking)
    subprocess.run(
        ["launchctl", "kill", "SIGKILL", gui_target],
        timeout=5, env=env, capture_output=True,
    )

    # 1b: BOOTOUT — deregister service (KeepAlive can't restart a deregistered service).
    # Process is already dead → bootout just removes registration → instant return.
    # ThrottleInterval=10s DOES NOT protect us (only applies to rapid-crash scenarios,
    # not to a service that ran for hours then was killed). Must deregister.
    subprocess.run(
        ["launchctl", "bootout", gui_target],
        timeout=10, env=env, capture_output=True,
    )

    # Step 2: Wait for port release (should already be free since SIGKILL)
    import socket
    for _i in range(10):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(0.5)
        try:
            s.connect(("127.0.0.1", 18321))
            s.close()
            time.sleep(1)  # Port still held — shouldn't happen after SIGKILL
        except (ConnectionRefusedError, OSError):
            s.close()
            break  # Port free
    else:
        # Fallback: nuclear option. Use exact binary path to avoid mis-killing.
        subprocess.run(["pkill", "-9", "-f", daemon_dir + "/python-backend"], timeout=5, env=env, capture_output=True)
        time.sleep(2)

    # Step 3: rsync bundle → daemon dir (safe — daemon is dead)
    # Anchored --exclude '/resources' '/.version': the bundle has no resources/
    # or .version, so a bare --delete would PERMANENTLY wipe them (this upgrader
    # never re-copies resources, unlike daemon-lib.sh). Preserve the existing
    # daemon resources/ + .version across the upgrade. Leading '/' anchors to the
    # transfer root so a nested bundle 'resources' dir (_internal/limits/resources)
    # still syncs normally.
    r = subprocess.run(
        ["rsync", "-a", "--delete", "--exclude", "/resources", "--exclude", "/.version",
         bundle_src + "/", daemon_dir + "/"],
        capture_output=True, text=True, timeout=60, env=env,
    )
    if r.returncode != 0:
        result = {{"status": "failed", "stage": "rsync", "stderr": r.stderr[-500:],
                  "completed_at": time.strftime(TS_FMT)}}
        pathlib.Path(result_file).write_text(json.dumps(result, indent=2))
        raise SystemExit(1)

    # Step 4: chmod binary
    binary = os.path.join(daemon_dir, "python-backend")
    os.chmod(binary, 0o755)

    # Step 5: write .version file (format: "semver git_hash timestamp")
    version_str = "unknown"
    try:
        v = pathlib.Path(os.path.join(project_root, "VERSION")).read_text().strip()
        h = subprocess.run(["git", "-C", project_root, "rev-parse", "--short", "HEAD"],
                          capture_output=True, text=True, timeout=5).stdout.strip() or "unknown"
        version_str = v + " " + h + " " + time.strftime(TS_FMT)
    except Exception:
        pass
    pathlib.Path(version_file).write_text(version_str + "\\n")

    # Step 6: Deploy fresh plist (ensures ExitTimeOut=15 and latest PATH are active)
    plist_src = os.path.join(project_root, "backend", "channels", "com.swarmai.backend.plist")
    plist_dst = os.path.expanduser("~/Library/LaunchAgents/com.swarmai.backend.plist")
    if os.path.exists(plist_src):
        # Template uses __HOME__, __WRAPPER_PATH__, __LOG_DIR__ placeholders
        home = os.path.expanduser("~")
        wrapper = os.path.join(home, ".swarm-ai", "swarmai_backend.sh")
        log_dir = os.path.join(home, ".swarm-ai", "logs")
        content = pathlib.Path(plist_src).read_text()
        content = content.replace("__HOME__", home)
        content = content.replace("__WRAPPER_PATH__", wrapper)
        content = content.replace("__LOG_DIR__", log_dir)
        pathlib.Path(plist_dst).write_text(content)

    # Step 7: Bootstrap daemon (re-register with new plist + start new binary)
    # Retry up to 3 times — launchd sometimes returns "service already loaded"
    # if bootout hasn't fully cleaned up internal state (race in launchd).
    bootstrap_ok = False
    for _attempt in range(3):
        r = subprocess.run(
            ["launchctl", "bootstrap", "gui/" + str(uid), plist_dst],
            timeout=10, env=env, capture_output=True, text=True,
        )
        if r.returncode == 0:
            bootstrap_ok = True
            break
        time.sleep(2)  # Let launchd finish cleanup

    # Step 7-sentinel: clear the intent sentinel now that the rsync window has
    # closed (rsync completed at Step 3 — the binary is consistent, so a
    # guardian bootstrap is no longer dangerous). Clearing on BOTH outcomes is
    # deliberate: on success the daemon is up (guardian skips anyway); on
    # bootstrap failure (deployed_no_restart) the guardian can recover in ~90s
    # instead of waiting out the stale-guard (5min). The stale-guard remains the
    # backstop for an upgrader that crashes mid-rsync before reaching here.
    try:
        pathlib.Path(sentinel_path).unlink()
    except FileNotFoundError:
        pass
    except OSError:
        pass

    # Step 8: Write result file — distinguish success vs partial failure
    status = "success" if bootstrap_ok else "deployed_no_restart"
    result = {{
        "status": status,
        "version": version_str,
        "bootstrap_ok": bootstrap_ok,
        "bundle_size_mb": round(sum(
            f.stat().st_size for f in pathlib.Path(daemon_dir).rglob("*") if f.is_file()
        ) / 1024 / 1024, 1),
        "completed_at": time.strftime(TS_FMT),
    }}
    pathlib.Path(result_file).write_text(json.dumps(result, indent=2))

except SystemExit:
    pass  # Already wrote result
except Exception as e:
    result = {{"status": "error", "error": str(e),
              "completed_at": time.strftime(TS_FMT)}}
    pathlib.Path(result_file).write_text(json.dumps(result, indent=2))
"""

    # F1: Use get_python_executable() — sys.executable points to frozen binary
    # in PyInstaller bundles (e.g. ~/.swarm-ai/daemon/python-backend), which
    # doesn't accept -c flag. get_python_executable() resolves to a real Python.
    from utils.bundle_paths import get_python_executable

    python_path = get_python_executable()

    subprocess.Popen(
        [python_path, "-c", upgrader_script],
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
    )

    logger.info(
        "Upgrade initiated: id=%s python=%s result_file=%s project=%s",
        upgrade_id, python_path, result_file, project_root,
    )

    return JSONResponse(
        status_code=202,
        content={
            "status": "initiated",
            "upgrade_id": upgrade_id,
            "result_file": result_file,
            "python_used": python_path,
            "message": "Upgrade process spawned. Daemon will restart in ~10s via SIGKILL+bootout+bootstrap.",
        },
    )


@app.get("/api/system/upgrade/status")
async def upgrade_status():
    """Check the result of the last upgrade operation."""
    global _upgrade_in_progress, _upgrade_result_file, _upgrade_started_at

    if _upgrade_result_file is None:
        return {"status": "no_upgrade", "message": "No upgrade has been initiated"}

    result_path = Path(_upgrade_result_file)
    if not result_path.exists():
        # F2: Auto-clear if upgrade timed out (upgrader crashed without writing result)
        if _upgrade_started_at and (time.time() - _upgrade_started_at) > _UPGRADE_TIMEOUT_S:
            _upgrade_in_progress = False
            _upgrade_started_at = None
            return {
                "status": "timeout",
                "message": f"Upgrade process did not complete within {_UPGRADE_TIMEOUT_S}s — lock cleared",
                "result_file": _upgrade_result_file,
            }
        return {
            "status": "in_progress",
            "result_file": _upgrade_result_file,
            "elapsed_s": round(time.time() - _upgrade_started_at, 1) if _upgrade_started_at else None,
            "message": "Upgrade is still running",
        }

    try:
        result = json.loads(result_path.read_text())
    except Exception as e:
        result = {"status": "error", "parse_error": str(e)}

    # Reset the lock once we've read the result
    _upgrade_in_progress = False
    _upgrade_started_at = None
    return {"status": "completed", "result": result}


@app.get("/")
async def root():
    """Root endpoint."""
    return {
        "name": settings.app_name,
        "version": settings.app_version,
        "docs": "/docs",
        "health": "/health",
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.debug,
    )
