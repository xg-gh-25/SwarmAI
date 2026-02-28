"""System status API endpoints."""
import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel

from database import db
from core.agent_manager import get_default_agent
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


class SystemStatusResponse(BaseModel):
    """System initialization status response."""
    database: DatabaseStatus
    agent: AgentStatus
    channel_gateway: ChannelGatewayStatus
    initialized: bool
    timestamp: str


@router.get("/status", response_model=SystemStatusResponse)
async def get_system_status() -> SystemStatusResponse:
    """Get current system initialization status.
    
    Returns the status of all system components:
    - Database health
    - SwarmAgent readiness with bound skills/MCP servers count
    - Channel gateway running status
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
            skill_ids = agent.get("skill_ids", [])
            mcp_ids = agent.get("mcp_ids", [])
            skills_count = len(skill_ids) if skill_ids else 0
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
    
    # Overall initialization: all components must be ready
    initialized = (
        database_status.healthy and
        agent_status.ready and
        channel_gateway_status.running
    )
    
    # ISO 8601 timestamp
    timestamp = datetime.now(timezone.utc).isoformat()
    
    return SystemStatusResponse(
        database=database_status,
        agent=agent_status,
        channel_gateway=channel_gateway_status,
        initialized=initialized,
        timestamp=timestamp
    )
