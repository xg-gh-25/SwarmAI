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


# ── Resource observability models ──────────────────────────────────

class SystemMemoryResponse(BaseModel):
    """System RAM snapshot."""
    total_mb: float
    available_mb: float
    used_mb: float
    percent_used: float
    pressure_level: str  # ok | warning | critical


class ProcessMetricsResponse(BaseModel):
    """Per-subprocess resource metrics."""
    pid: int
    session_id: str
    rss_mb: float
    cpu_percent: float
    num_threads: int
    state: str
    uptime_seconds: float


class SpawnBudgetResponse(BaseModel):
    """Spawn gate decision."""
    can_spawn: bool
    reason: str
    available_mb: float
    estimated_cost_mb: float
    headroom_mb: float


class MaxTabsResponse(BaseModel):
    """Dynamic tab limit and memory pressure level."""
    max_tabs: int
    memory_pressure: str  # ok | warning | critical


class SystemResourcesResponse(BaseModel):
    """Full resource observability surface."""
    memory: SystemMemoryResponse
    spawn_budget: SpawnBudgetResponse
    processes: list[ProcessMetricsResponse]
    total_subprocess_rss_mb: float
    timestamp: str


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


@router.get("/resources", response_model=SystemResourcesResponse)
async def get_system_resources() -> SystemResourcesResponse:
    """Get system resource metrics: memory, spawn budget, per-process RSS.

    Designed for the frontend resource ring and diagnostics panel.
    Cheap to call — psutil reads are cached for 5s.
    """
    from core.resource_monitor import resource_monitor
    from core import session_registry

    mem = resource_monitor.system_memory()
    budget = resource_monitor.spawn_budget()

    # Collect per-process metrics from alive SessionUnits
    processes: list[ProcessMetricsResponse] = []
    total_rss = 0.0
    router_inst = session_registry.session_router
    if router_inst:
        for unit in router_inst.list_units():
            metrics = getattr(unit, "_last_metrics", None)
            if metrics:
                rss_mb = round(metrics.rss_bytes / (1024 * 1024), 1)
                total_rss += rss_mb
                processes.append(ProcessMetricsResponse(
                    pid=metrics.pid,
                    session_id=metrics.session_id,
                    rss_mb=rss_mb,
                    cpu_percent=metrics.cpu_percent,
                    num_threads=metrics.num_threads,
                    state=metrics.state,
                    uptime_seconds=metrics.uptime_seconds,
                ))

    return SystemResourcesResponse(
        memory=SystemMemoryResponse(
            total_mb=round(mem.total / (1024 * 1024), 1),
            available_mb=round(mem.available / (1024 * 1024), 1),
            used_mb=round(mem.used / (1024 * 1024), 1),
            percent_used=mem.percent_used,
            pressure_level=mem.pressure_level,
        ),
        spawn_budget=SpawnBudgetResponse(
            can_spawn=budget.can_spawn,
            reason=budget.reason,
            available_mb=budget.available_mb,
            estimated_cost_mb=budget.estimated_cost_mb,
            headroom_mb=budget.headroom_mb,
        ),
        processes=processes,
        total_subprocess_rss_mb=round(total_rss, 1),
        timestamp=datetime.now(timezone.utc).isoformat(),
    )


@router.get("/max-tabs", response_model=MaxTabsResponse)
async def get_max_tabs() -> MaxTabsResponse:
    """Get dynamic tab limit and current memory pressure level.

    Invalidates the memory cache first so the response reflects
    up-to-date system conditions.  On failure, returns a safe
    fallback of 1 tab with critical pressure.
    """
    from core.resource_monitor import resource_monitor
    try:
        resource_monitor.invalidate_cache()
        mem = resource_monitor.system_memory()
        return MaxTabsResponse(
            max_tabs=resource_monitor.compute_max_tabs(),
            memory_pressure=mem.pressure_level,
        )
    except Exception:
        logger.exception("Failed to compute max tabs")
        return MaxTabsResponse(max_tabs=1, memory_pressure="critical")


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
