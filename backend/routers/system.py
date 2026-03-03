"""System status API endpoints."""
import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel

from database import db
from core.agent_manager import get_default_agent
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
    """Channel gateway status."""
    running: bool


class SwarmWorkspaceStatus(BaseModel):
    """Swarm Workspace initialization status."""
    ready: bool
    name: Optional[str] = None
    path: Optional[str] = None


class SystemStatusResponse(BaseModel):
    """System initialization status response."""
    database: DatabaseStatus
    agent: AgentStatus
    channel_gateway: ChannelGatewayStatus
    swarm_workspace: SwarmWorkspaceStatus
    initialized: bool
    initialization_mode: str  # 'first_run', 'quick_validation', or 'reset'
    initialization_complete: bool  # The persistent flag value
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
        agent = await get_default_agent()
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
    
    channel_gateway_status = ChannelGatewayStatus(running=gateway_running)
    
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
    
    # Overall initialization: all components must be ready
    initialized = (
        database_status.healthy and
        agent_status.ready and
        channel_gateway_status.running and
        swarm_workspace_status.ready
    )
    
    # Get initialization status from InitializationManager
    # Validates: Requirements 5.1, 5.2, 5.3
    init_status = await initialization_manager.get_initialization_status()
    initialization_mode = init_status.get("mode", "unknown")
    initialization_complete = init_status.get("initialization_complete", False)
    
    # ISO 8601 timestamp
    timestamp = datetime.now(timezone.utc).isoformat()
    
    return SystemStatusResponse(
        database=database_status,
        agent=agent_status,
        channel_gateway=channel_gateway_status,
        swarm_workspace=swarm_workspace_status,
        initialized=initialized,
        initialization_mode=initialization_mode,
        initialization_complete=initialization_complete,
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
