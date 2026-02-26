"""FastAPI application entry point."""
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from contextlib import asynccontextmanager
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
import logging
from pathlib import Path

from config import settings, get_app_data_dir
from core.agent_manager import agent_manager
from routers import agents_router, skills_router, mcp_router, chat_router, auth_router, workspace_router, settings_router, plugins_router, tasks_router, channels_router
from channels.gateway import channel_gateway
from middleware.error_handler import setup_error_handlers
from middleware.rate_limit import limiter
from database import initialize_database


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


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler."""
    # Startup
    logger.info(f"Starting {settings.app_name} v{settings.app_version}")
    logger.info(f"Debug mode: {settings.debug}")
    logger.info(f"Database type: {settings.database_type}")
    logger.info(f"Rate limit: {settings.rate_limit_per_minute}/minute")

    # Initialize database
    await initialize_database()
    logger.info("Database initialized")

    # Start channel gateway (auto-starts active channels)
    await channel_gateway.startup()
    logger.info("Channel gateway started")

    yield
    # Shutdown
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
app.include_router(workspace_router, prefix="/api/workspace", tags=["workspace"])
app.include_router(settings_router, prefix="/api/settings", tags=["settings"])
app.include_router(plugins_router, prefix="/api/plugins", tags=["plugins"])
app.include_router(tasks_router, prefix="/api/tasks", tags=["tasks"])
app.include_router(channels_router, prefix="/api/channels", tags=["channels"])


@app.get("/health")
async def health_check():
    """Health check endpoint."""
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
