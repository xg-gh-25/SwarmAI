"""FastAPI application entry point."""
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from contextlib import asynccontextmanager
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
import asyncio
import logging
import os
import shutil
import sqlite3
import sys
import time
from pathlib import Path

from config import settings, get_app_data_dir
from core import session_registry
from utils.bundle_paths import get_resource_file
from routers import agents_router, skills_router, mcp_router, chat_router, chat_threads_router, auth_router, workspace_router, settings_router, plugins_router, tasks_router, channels_router, system_router, todos_router, search_router, workspace_config_router, workspace_api_router, projects_router, tscc_router, artifacts_router, escalations_router, voice_router, hive_router
from routers.autonomous_jobs import router as autonomous_jobs_router
from routers.pipelines import router as pipelines_router
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


def get_log_file_path() -> Path:
    """Get the log file path based on run mode.

    Daemon and sidecar write separate log files to avoid RotatingFileHandler
    multi-process race (rename collisions during rotation).  dev.sh already
    redirects to backend-dev.log, so three processes never share a file.
    """
    log_dir = get_app_data_dir() / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    mode = os.environ.get("SWARMAI_MODE", "sidecar")
    if mode == "daemon":
        return log_dir / "backend-daemon.log"
    return log_dir / "backend.log"


# Configure logging
log_level = logging.DEBUG if settings.debug else logging.INFO
log_format = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"

# Create handlers
console_handler = logging.StreamHandler()
console_handler.setLevel(log_level)
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
    """Detect whether this backend is running as daemon, sidecar, or hive.

    Resolution: ``SWARMAI_MODE`` env var.
    - ``"daemon"`` — macOS launchd 24/7 service
    - ``"hive"``   — EC2 cloud deployment (systemd)
    - ``"sidecar"`` — Tauri desktop app (default)
    """
    return os.environ.get("SWARMAI_MODE", "sidecar")


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
    mode recorded in the file matches.  This prevents a sidecar that briefly
    co-existed with a daemon from deleting the daemon's discovery file on exit.

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

    Validates: Requirements 2.1, 2.2, 2.3, 2.5, 2.7, 2.8
    """
    user_db_path = get_app_data_dir() / "data.db"

    # Ensure the app data directory exists
    user_db_path.parent.mkdir(parents=True, exist_ok=True)

    # --- Returning user: preserve existing data.db, skip init pipeline ---
    if user_db_path.exists():
        # Guard against 0-byte corrupt DB (e.g. from a failed previous startup)
        if user_db_path.stat().st_size == 0:
            logger.warning(f"Empty database at {user_db_path} — removing and re-seeding")
            user_db_path.unlink()
        else:
            logger.info(f"Using existing user database at {user_db_path}")
            return True

    # --- First launch: attempt atomic seed copy ---
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
        # Clean up partial file to avoid leaving corrupted state
        try:
            tmp_path.unlink(missing_ok=True)
            user_db_path.unlink(missing_ok=True)
        except OSError:
            pass
        logger.warning("Will fall back to runtime initialization")
        return False

    # --- Set WAL mode and busy_timeout on the freshly copied database ---
    try:
        with sqlite3.connect(str(user_db_path)) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=5000")
        logger.info("Set WAL mode and busy_timeout on seed-copied database")
    except Exception as e:
        logger.warning(f"Failed to set database pragmas (non-fatal): {e}")

    return True


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


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler."""
    global _startup_complete, _startup_time_ms, _phase_timings
    from core.initialization_manager import initialization_manager

    t0 = time.monotonic()
    phase_timings: dict[str, float] = {}
    
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
        await initialize_database(skip_schema=True)
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
        try:
            await asyncio.wait_for(initialize_database(), timeout=45.0)
        except asyncio.TimeoutError:
            logger.error("Database initialization timed out after 45 seconds — check migrations")
            raise RuntimeError("Database initialization timed out")
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
    phase_timings["gateway_ms"] = 0  # Updated by background task on completion

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

    # ── Session lifecycle hooks ──────────────────────────────────────
    from core.session_hooks import SessionLifecycleHookManager, BackgroundHookExecutor
    from core.summarization import SummarizationPipeline
    from core.compliance import ComplianceTracker
    from hooks.daily_activity_hook import DailyActivityExtractionHook
    from hooks.auto_commit_hook import WorkspaceAutoCommitHook
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

    # Order matters: extraction → commit → distillation → health → evolution → improvement
    # Distillation BEFORE health so embeddings capture freshly-distilled entries.
    hook_manager.register(DailyActivityExtractionHook(
        summarization_pipeline=summarization_pipeline,
        compliance_tracker=compliance_tracker,
    ))
    # Pass shared git lock to auto-commit hook to prevent .git/index.lock contention
    hook_manager.register(WorkspaceAutoCommitHook(git_lock=hook_executor.git_lock))
    hook_manager.register(DistillationTriggerHook())
    # Context health: light refresh every session (if changed), deep check daily.
    # Runs AFTER distillation so embedding sync picks up fresh MEMORY.md entries.
    hook_manager.register(ContextHealthHook())
    hook_manager.register(EvolutionMaintenanceHook())
    # IMPROVEMENT.md write-back: closes the DDD learning loop.
    # Runs after auto-commit so workspace state is settled.
    hook_manager.register(ImprovementWritebackHook(
        workspace_path=app_config.get("workspace_path", str(Path.home() / ".swarm-ai" / "SwarmWS")),
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

    # ── Start managed sidecar services (Slack bot, etc.) ─────────────
    # Deferred to background so it never blocks startup.  Services
    # discover the backend via ~/.swarm-ai/backend.port written here.
    from core.service_manager import service_manager as _svc_mgr

    async def _deferred_services_startup() -> None:
        try:
            ws_path = initialization_manager.get_cached_workspace_path()
            backend_port = _detect_backend_port()
            await _svc_mgr.start_all(ws_path, backend_port)
        except Exception:
            logger.exception("Sidecar services startup failed (non-fatal)")

    asyncio.create_task(_deferred_services_startup())

    yield
    # Shutdown
    _startup_complete = False
    logger.info("Shutting down...")
    await _svc_mgr.stop_all()
    logger.info("Sidecar services stopped")
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
app.include_router(jobs_router, tags=["jobs"])  # prefix already set in router
app.include_router(artifacts_router, prefix="/api", tags=["artifacts"])
app.include_router(escalations_router, tags=["escalations"])
app.include_router(voice_router, prefix="/api/voice", tags=["voice"])
app.include_router(hive_router, prefix="/api/hive", tags=["hive"])

# Memory compliance router (no prefix — router defines /api internally)
from routers.memory import router as memory_router
app.include_router(memory_router, tags=["memory"])

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
        }
    
    # PE Review Finding #5: Use property directly, not hasattr
    pending_hooks = (
        session_registry.hook_executor.pending_count
        if session_registry.hook_executor
        else 0
    )

    # F6: Verify DB is actually reachable — prevents "false healthy"
    db_healthy = True
    try:
        from database import db
        db_healthy = await db.health_check()
    except Exception:
        db_healthy = False

    status = "healthy" if db_healthy else "degraded"

    return {
        "status": status,
        "version": settings.app_version,
        "sdk": "claude-agent-sdk",
        "pending_hook_tasks": pending_hooks,
        "boot_id": _boot_id,
        "db_healthy": db_healthy,
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
    """Return the backend's running mode (daemon vs sidecar)."""
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

    This endpoint is called by the Tauri app before killing the backend process
    to ensure all Claude CLI child processes are properly terminated.

    Blocked in Hive mode — any authenticated user could kill the shared
    backend. Desktop-only (Tauri close handler is the sole caller).
    """
    if _detect_run_mode() == "hive":
        logger.warning("Shutdown endpoint blocked in Hive mode")
        return {"status": "ignored", "reason": "shutdown disabled in hive mode"}
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
