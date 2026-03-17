"""System status API endpoints."""
import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel

from database import db
from core.agent_defaults import build_agent_config, DEFAULT_AGENT_ID
from core.initialization_manager import initialization_manager
from core.swarm_workspace_manager import swarm_workspace_manager
from channels.gateway import channel_gateway

logger = logging.getLogger(__name__)

router = APIRouter()


class DatabaseStatus(BaseModel):
    """Database health status."""
    healthy: bool
    error: Optional[str] = None


class AgentStatus(BaseModel):
    """SwarmAgent status."""
    ready: bool
    name: Optional[str] = None
    skills_count: int = 0
    mcp_servers_count: int = 0


class ChannelGatewayStatus(BaseModel):
    """Channel gateway status.

    Attributes:
        running: Whether the gateway is actively running (not shutting down).
        startup_state: Lifecycle state — one of ``"not_started"``,
            ``"starting"``, ``"started"``, or ``"failed"``.
    """
    running: bool
    startup_state: str = "not_started"


class SwarmWorkspaceStatus(BaseModel):
    """Swarm Workspace initialization status."""
    ready: bool
    name: Optional[str] = None
    path: Optional[str] = None


class SystemStatusResponse(BaseModel):
    """System initialization status response.

    Attributes:
        database: Database health status.
        agent: SwarmAgent readiness status.
        channel_gateway: Channel gateway running and startup state.
        swarm_workspace: Workspace initialization status.
        initialized: Overall readiness flag (all critical components ready).
        initialization_mode: How the backend was initialized
            (``'first_run'``, ``'quick_validation'``, or ``'reset'``).
        initialization_complete: Persistent flag from the database.
        startup_time_ms: Total backend startup duration in milliseconds,
            or ``None`` if not yet available.
        phase_timings: Per-phase durations (e.g. ``database_ms``,
            ``workspace_ms``), or ``None`` if not yet available.
        timestamp: ISO 8601 UTC timestamp of the response.
    """
    database: DatabaseStatus
    agent: AgentStatus
    channel_gateway: ChannelGatewayStatus
    swarm_workspace: SwarmWorkspaceStatus
    initialized: bool
    initialization_mode: str  # 'first_run', 'quick_validation', or 'reset'
    initialization_complete: bool  # The persistent flag value
    startup_time_ms: Optional[float] = None
    phase_timings: Optional[dict[str, float]] = None
    timestamp: str


class ResetToDefaultsResponse(BaseModel):
    """Response for reset-to-defaults endpoint."""
    success: bool
    error: Optional[str] = None


@router.get("/status", response_model=SystemStatusResponse)
async def get_system_status() -> SystemStatusResponse:
    """Get current system initialization status.
    
    Returns the status of all system components:
    - Database health
    - SwarmAgent readiness with bound skills/MCP servers count
    - Channel gateway running status
    - Swarm Workspace initialization status with name and path
    - Overall initialization status (true only if all components ready)
    """
    # Check database health
    db_healthy = False
    db_error: Optional[str] = None
    try:
        db_healthy = await db.health_check()
    except Exception as e:
        logger.error(f"Database health check failed: {e}")
        db_error = str(e)
    
    database_status = DatabaseStatus(healthy=db_healthy, error=db_error)
    
    # Check SwarmAgent status
    agent_ready = False
    agent_name: Optional[str] = None
    skills_count = 0
    mcp_servers_count = 0
    
    try:
        agent = await build_agent_config(DEFAULT_AGENT_ID)
        if agent:
            agent_ready = True
            agent_name = agent.get("name")
            # Count bound skills and MCP servers
            skill_names = agent.get("allowed_skills", [])
            mcp_ids = agent.get("mcp_ids", [])
            skills_count = len(skill_names) if skill_names else 0
            mcp_servers_count = len(mcp_ids) if mcp_ids else 0
    except Exception as e:
        logger.error(f"Failed to get default agent: {e}")
    
    agent_status = AgentStatus(
        ready=agent_ready,
        name=agent_name,
        skills_count=skills_count,
        mcp_servers_count=mcp_servers_count
    )
    
    # Check channel gateway status
    # Gateway is considered running if it has been started (not shutting down)
    gateway_running = not channel_gateway._shutting_down
    
    channel_gateway_status = ChannelGatewayStatus(
        running=gateway_running,
        startup_state=channel_gateway.startup_state,
    )
    
    # Check Swarm Workspace status
    workspace_ready = False
    workspace_name: Optional[str] = None
    workspace_path: Optional[str] = None
    
    try:
        workspace_config = await db.workspace_config.get_config()
        if workspace_config:
            workspace_ready = True
            workspace_name = workspace_config.get("name")
            # Expand {app_data_dir} placeholder to actual path
            raw_path = workspace_config.get("file_path")
            workspace_path = swarm_workspace_manager.expand_path(raw_path) if raw_path else None
    except Exception as e:
        logger.error(f"Failed to get workspace config: {e}")
    
    swarm_workspace_status = SwarmWorkspaceStatus(
        ready=workspace_ready,
        name=workspace_name,
        path=workspace_path
    )
    
    # Overall initialization: all critical components must be ready.
    # When no channels are configured (startup_state == "not_started"),
    # the gateway's running flag is irrelevant — the user simply has no
    # channels, so we don't gate readiness on the gateway.
    gateway_ok = (
        channel_gateway_status.startup_state == "not_started"
        or channel_gateway_status.running
    )
    initialized = (
        database_status.healthy and
        agent_status.ready and
        gateway_ok and
        swarm_workspace_status.ready
    )
    
    # Get initialization status from InitializationManager
    # Validates: Requirements 5.1, 5.2, 5.3
    init_status = await initialization_manager.get_initialization_status()
    initialization_mode = init_status.get("mode", "unknown")
    initialization_complete = init_status.get("initialization_complete", False)
    
    # ISO 8601 timestamp
    timestamp = datetime.now(timezone.utc).isoformat()
    
    # Lazy import to avoid circular dependency (main -> routers -> system -> main).
    import main as _main_module

    return SystemStatusResponse(
        database=database_status,
        agent=agent_status,
        channel_gateway=channel_gateway_status,
        swarm_workspace=swarm_workspace_status,
        initialized=initialized,
        initialization_mode=initialization_mode,
        initialization_complete=initialization_complete,
        startup_time_ms=_main_module._startup_time_ms,
        phase_timings=_main_module._phase_timings,
        timestamp=timestamp
    )


@router.post("/reset-to-defaults", response_model=ResetToDefaultsResponse)
async def reset_to_defaults() -> ResetToDefaultsResponse:
    """Reset application to default state and re-run initialization.
    
    This endpoint clears the initialization_complete flag and triggers
    full initialization, useful for recovering from configuration issues.
    
    Returns:
        ResetToDefaultsResponse with success status and optional error message.
    
    Validates: Requirements 4.1, 4.4, 4.5
    """
    logger.info("Reset to defaults endpoint called")
    
    result = await initialization_manager.reset_to_defaults()
    
    return ResetToDefaultsResponse(
        success=result["success"],
        error=result.get("error")
    )
