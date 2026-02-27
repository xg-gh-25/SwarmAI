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
from pathlib import Path

from config import settings, get_app_data_dir
from core.agent_manager import agent_manager
from utils.bundle_paths import get_resource_file
from routers import agents_router, skills_router, mcp_router, chat_router, chat_threads_router, auth_router, workspace_router, settings_router, plugins_router, tasks_router, channels_router, system_router, todos_router, search_router, workspace_config_router, workspace_api_router, projects_router, context_router, tscc_router
from routers.autonomous_jobs import router as autonomous_jobs_router
from channels.gateway import channel_gateway
from middleware.error_handler import setup_error_handlers
from middleware.rate_limit import limiter
from database import initialize_database

# Runtime flag to track if lifespan startup has completed
# This is different from initialization_complete in DB which persists across restarts
_startup_complete = False


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


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler."""
    global _startup_complete
    from core.initialization_manager import initialization_manager
    
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
        else:
            # First-time initialization
            logger.info("First-time startup, running full initialization...")
            await initialization_manager.run_full_initialization()

    # Start channel gateway (auto-starts active channels)
    await channel_gateway.startup()
    logger.info("Channel gateway started")
    
    # Wire up TSCC managers for the tscc router
    from core.agent_manager import _tscc_state_manager, set_tscc_snapshot_manager
    from core.tscc_snapshot_manager import TSCCSnapshotManager
    from core.swarm_workspace_manager import swarm_workspace_manager
    from routers.tscc import register_tscc_dependencies

    tscc_snapshot_mgr = TSCCSnapshotManager(swarm_workspace_manager, _tscc_state_manager)
    register_tscc_dependencies(_tscc_state_manager, tscc_snapshot_mgr)
    set_tscc_snapshot_manager(tscc_snapshot_mgr)
    logger.info("TSCC managers initialized (shared state manager from agent_manager)")

    # Mark startup as complete - health check will now return healthy
    _startup_complete = True
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
app.include_router(context_router, prefix="/api", tags=["context"])
app.include_router(tscc_router, prefix="/api", tags=["tscc"])
app.include_router(autonomous_jobs_router, prefix="/api/autonomous-jobs", tags=["autonomous-jobs"])

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
    
    return {
        "status": "healthy",
        "version": settings.app_version,
        "sdk": "claude-agent-sdk",
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
