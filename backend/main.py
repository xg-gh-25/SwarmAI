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
from core.agent_manager import agent_manager
from utils.bundle_paths import get_resource_file
from routers import agents_router, skills_router, mcp_router, chat_router, chat_threads_router, auth_router, workspace_router, settings_router, plugins_router, tasks_router, channels_router, system_router, todos_router, search_router, workspace_config_router, workspace_api_router, projects_router, tscc_router
from routers.autonomous_jobs import router as autonomous_jobs_router
from channels.gateway import channel_gateway
from middleware.error_handler import setup_error_handlers
from middleware.rate_limit import limiter
from database import initialize_database

# Runtime flag to track if lifespan startup has completed
# This is different from initialization_complete in DB which persists across restarts
_startup_complete = False

# Startup timing instrumentation (populated by lifespan, read by system status endpoint).
# ``_startup_time_ms`` holds the total wall-clock time from lifespan entry to
# ``_startup_complete = True``.  ``_phase_timings`` holds per-phase durations
# keyed by phase name (e.g. ``"database_ms"``, ``"workspace_ms"``).
# Both are ``None`` until the lifespan completes its critical path.
_startup_time_ms: float | None = None
_phase_timings: dict[str, float] | None = None


def get_log_file_path() -> Path:
    """Get the log file path based on platform."""
    log_dir = get_app_data_dir() / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir / "backend.log"


# Configure logging
log_level = logging.DEBUG if settings.debug else logging.INFO
log_format = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"

# Create handlers
console_handler = logging.StreamHandler()
console_handler.setLevel(log_level)
console_handler.setFormatter(logging.Formatter(log_format))

# File handler - write logs to file
log_file = get_log_file_path()
file_handler = logging.FileHandler(log_file, encoding='utf-8')
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

    Args:
        label: Human-readable label for log messages (e.g. ``"fast path"``).
    """
    _t_start = time.monotonic()
    try:
        from core.initialization_manager import initialization_manager
        await initialization_manager.refresh_builtin_defaults()
        logger.info("Builtin defaults refreshed (deferred, %s)", label)
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
    # These replace the module-level singletons that were previously
    # created at import time in agent_manager.py.
    # Requirements: 1.2, 4.7, 4.8, 9.3
    from core.app_config_manager import AppConfigManager
    from core.cmd_permission_manager import CmdPermissionManager
    from core.credential_validator import CredentialValidator
    from routers.settings import set_config_manager

    app_config = AppConfigManager()
    app_config.load()
    logger.info("AppConfigManager loaded (config.json)")

    cmd_perm = CmdPermissionManager()
    cmd_perm.load()
    logger.info("CmdPermissionManager loaded (cmd_permissions/)")

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

    # Wire into AgentManager (replaces module-level singletons)
    agent_manager.configure(
        config_manager=app_config,
        cmd_permission_manager=cmd_perm,
        credential_validator=cred_validator,
    )
    logger.info("AgentManager configured with injected components")

    # ── Session lifecycle hooks ──────────────────────────────────────
    from core.session_hooks import SessionLifecycleHookManager, BackgroundHookExecutor
    from core.summarization import SummarizationPipeline
    from core.compliance import ComplianceTracker
    from hooks.daily_activity_hook import DailyActivityExtractionHook
    from hooks.auto_commit_hook import WorkspaceAutoCommitHook
    from hooks.distillation_hook import DistillationTriggerHook
    from hooks.evolution_maintenance_hook import EvolutionMaintenanceHook
    from routers.memory import set_compliance_tracker

    summarization_pipeline = SummarizationPipeline()
    compliance_tracker = ComplianceTracker()
    hook_manager = SessionLifecycleHookManager(timeout_seconds=30.0)

    # Create fire-and-forget executor — hooks never block the chat path
    hook_executor = BackgroundHookExecutor(hook_manager)

    # Order matters: extraction first, then commit, then distillation check, then evolution maintenance
    hook_manager.register(DailyActivityExtractionHook(
        summarization_pipeline=summarization_pipeline,
        compliance_tracker=compliance_tracker,
    ))
    # Pass shared git lock to auto-commit hook to prevent .git/index.lock contention
    hook_manager.register(WorkspaceAutoCommitHook(git_lock=hook_executor.git_lock))
    hook_manager.register(DistillationTriggerHook())
    hook_manager.register(EvolutionMaintenanceHook())

    agent_manager.set_hook_manager(hook_manager)
    agent_manager.set_hook_executor(hook_executor)
    set_compliance_tracker(compliance_tracker)
    logger.info("Session lifecycle hooks registered (4 hooks, background executor)")
    # ─────────────────────────────────────────────────────────────────

    t_agent = time.monotonic()
    phase_timings["agent_manager_ms"] = round((t_agent - t_config) * 1000)
    logger.info("Phase: agent manager configure — %dms", phase_timings["agent_manager_ms"])

    # Wire AppConfigManager into Settings router (DI).
    # Skip if already configured (e.g. test fixtures may pre-set).
    from routers import settings as _settings_mod
    if _settings_mod._config_manager is None:
        set_config_manager(app_config)
        logger.info("Settings router configured with AppConfigManager")
    else:
        logger.debug("Settings router already configured (skipping overwrite)")

    # Wire up TSCC state manager for the tscc router
    from core.agent_manager import _tscc_state_manager
    from routers.tscc import register_tscc_dependencies

    register_tscc_dependencies(_tscc_state_manager)
    logger.info("TSCC state manager initialized")

    # Mark startup as complete - health check will now return healthy
    _startup_complete = True
    total_ms = round((time.monotonic() - t0) * 1000)
    _startup_time_ms = total_ms
    _phase_timings = phase_timings
    logger.info(
        "Startup complete — total %dms (db=%dms, workspace=%dms, config=%dms, agent=%dms)",
        total_ms,
        phase_timings.get("database_ms", 0),
        phase_timings.get("workspace_ms", 0),
        phase_timings.get("config_ms", 0),
        phase_timings.get("agent_manager_ms", 0),
    )
    logger.info("Startup complete - ready to serve requests")

    yield
    # Shutdown
    _startup_complete = False
    logger.info("Shutting down...")
    await channel_gateway.shutdown()
    logger.info("Channel gateway stopped")
    await agent_manager.disconnect_all()
    logger.info("All clients disconnected")


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
        agent_manager.hook_executor.pending_count
        if agent_manager.hook_executor
        else 0
    )

    return {
        "status": "healthy",
        "version": settings.app_version,
        "sdk": "claude-agent-sdk",
        "pending_hook_tasks": pending_hooks,
    }


@app.post("/shutdown")
async def shutdown():
    """Graceful shutdown endpoint - disconnects all Claude SDK clients.

    This endpoint is called by the Tauri app before killing the backend process
    to ensure all Claude CLI child processes are properly terminated.
    """
    logger.info("Shutdown endpoint called - disconnecting all clients")
    await agent_manager.disconnect_all()
    logger.info("All clients disconnected via shutdown endpoint")
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
