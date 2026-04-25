"""System status API endpoints."""
import json as _json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import boto3
import httpx
from fastapi import APIRouter
from pydantic import BaseModel

from config import get_bedrock_model_id
from database import db
from core.agent_defaults import build_agent_config, DEFAULT_AGENT_ID
from core.app_config_manager import AppConfigManager
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
    onboarding_complete: bool = False  # True after first-run onboarding wizard
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
    chat_max: int  # max_tabs - 1 (1 slot reserved for channel)
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
    
    # Read onboarding_complete flag from app_settings
    onboarding_complete = False
    try:
        settings = await db.app_settings.get("default")
        if settings:
            onboarding_complete = bool(settings.get("onboarding_complete", 0))
    except Exception:
        pass

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
        onboarding_complete=onboarding_complete,
        startup_time_ms=_main_module._startup_time_ms,
        phase_timings=_main_module._phase_timings,
        timestamp=timestamp
    )


# ── Onboarding endpoints ──────────────────────────────────────────────


def _get_auth_config() -> dict:
    """Read auth-related config from AppConfigManager."""
    try:
        config = AppConfigManager.instance()
        return {
            "use_bedrock": config.get("use_bedrock", False),
            "aws_region": config.get("aws_region", "us-east-1"),
            "default_model": config.get("default_model", "claude-opus-4-6"),
            "bedrock_model_map": config.get("bedrock_model_map"),
            "anthropic_base_url": config.get("anthropic_base_url"),
        }
    except Exception:
        # AppConfigManager not initialized (e.g., during tests)
        return {
            "use_bedrock": True,
            "aws_region": "us-east-1",
            "default_model": "claude-opus-4-6",
            "bedrock_model_map": None,
            "anthropic_base_url": None,
        }


def _auth_error(error: str, error_type: str, fix_hint: str) -> dict:
    """Build a standardized auth error response."""
    return {
        "success": False,
        "error": error,
        "error_type": error_type,
        "fix_hint": fix_hint,
    }


@router.post("/verify-auth")
async def verify_auth():
    """Verify LLM authentication by making a minimal API call.

    Reads auth config from AppConfigManager, then:
    - Bedrock: boto3 bedrock-runtime.invoke_model with max_tokens=1
    - API key: httpx POST to messages API with max_tokens=1

    Always returns 200 -- success/failure is in the response body.
    """
    config = _get_auth_config()
    use_bedrock = config.get("use_bedrock", False)

    if use_bedrock:
        return _verify_bedrock(config)
    else:
        return await _verify_anthropic_api(config)


def _verify_bedrock(config: dict) -> dict:
    """Verify Bedrock auth with a minimal invoke."""
    region = config.get("aws_region", "us-east-1")
    model = config.get("default_model", "claude-opus-4-6")
    bedrock_model = get_bedrock_model_id(model, config.get("bedrock_model_map"))

    start = time.monotonic()
    try:
        client = boto3.client("bedrock-runtime", region_name=region)
        client.invoke_model(
            modelId=bedrock_model,
            contentType="application/json",
            accept="application/json",
            body=_json.dumps({
                "anthropic_version": "bedrock-2023-05-31",
                "max_tokens": 1,
                "messages": [{"role": "user", "content": "hi"}],
            }),
        )
        latency = int((time.monotonic() - start) * 1000)
        return {
            "success": True,
            "model": model,
            "bedrock_model": bedrock_model,
            "region": region,
            "latency_ms": latency,
        }
    except Exception as e:
        error_str = str(e)

        if "ExpiredToken" in error_str or "expired" in error_str.lower():
            return _auth_error(
                error_str, "expired_credentials",
                "Refresh credentials: ada credentials update --account=ACCOUNT --role=ROLE"
            )
        if "InvalidIdentityToken" in error_str or "UnrecognizedClient" in error_str:
            return _auth_error(
                error_str, "invalid_credentials",
                "Credentials are invalid. Re-authenticate with ada or aws sso login."
            )
        if "not authorized" in error_str.lower() or "AccessDenied" in error_str:
            return _auth_error(
                error_str, "access_denied",
                "Model access not enabled in this region. Check Bedrock console."
            )
        return _auth_error(error_str, "unknown", "Check AWS configuration and try again.")


async def _verify_anthropic_api(config: dict) -> dict:
    """Verify Anthropic API key with a minimal messages call."""
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return _auth_error(
            "ANTHROPIC_API_KEY not set", "missing_key",
            "Set ANTHROPIC_API_KEY environment variable before launching SwarmAI."
        )

    base_url = config.get("anthropic_base_url") or "https://api.anthropic.com"
    model = config.get("default_model", "claude-opus-4-6")
    start = time.monotonic()

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{base_url}/v1/messages",
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": model,
                    "max_tokens": 1,
                    "messages": [{"role": "user", "content": "hi"}],
                },
            )

        latency = int((time.monotonic() - start) * 1000)

        if resp.status_code == 200:
            return {"success": True, "model": model, "latency_ms": latency}

        body = resp.json()
        error_msg = body.get("error", {}).get("message", resp.text)

        if resp.status_code == 401:
            return _auth_error(error_msg, "invalid_key",
                               "API key is invalid. Check the key at console.anthropic.com.")
        if resp.status_code == 403:
            return _auth_error(error_msg, "forbidden",
                               "API key doesn't have access to this model.")

        return _auth_error(error_msg, "api_error", "Check Anthropic API status.")

    except httpx.ConnectError:
        return _auth_error("Cannot reach API endpoint", "network",
                           f"Check network connectivity to {base_url}")
    except Exception as e:
        return _auth_error(str(e), "unknown", "Check API configuration.")


@router.get("/auth-hint")
async def get_auth_hint():
    """Return hints about the local credential environment.

    Helps the frontend pick a sensible default auth method card
    and show real credential status when already configured.
    """
    has_ada = Path.home().joinpath(".ada").is_dir()
    has_sso_cache = bool(list(Path.home().joinpath(".aws/sso/cache").glob("*.json")))
    has_api_key = bool(os.environ.get("ANTHROPIC_API_KEY"))

    if has_api_key:
        suggested = "apikey"
    elif has_ada:
        suggested = "ada"
    elif has_sso_cache:
        suggested = "sso"
    else:
        suggested = "sso"  # safest default for external users

    # Probe real credential details for display
    ada_details = _probe_ada_details() if has_ada else None
    aws_profiles = _probe_aws_profiles() if has_sso_cache else None

    return {
        "has_ada_dir": has_ada,
        "has_sso_cache": has_sso_cache,
        "has_api_key": has_api_key,
        "suggested_method": suggested,
        "ada_details": ada_details,
        "aws_profiles": aws_profiles,
    }


def _probe_ada_details() -> dict | None:
    """Read ADA credential status from ~/.ada/credentials (INI-style)."""
    creds_path = Path.home() / ".ada" / "credentials"
    if not creds_path.exists():
        return None
    try:
        content = creds_path.read_text(encoding="utf-8")
        details: dict = {}
        for line in content.splitlines():
            line = line.strip()
            if "=" in line and not line.startswith("["):
                k, v = line.split("=", 1)
                k, v = k.strip(), v.strip()
                if k == "aws_account_id":
                    details["account_id"] = v
                elif k == "aws_role_name":
                    details["role_name"] = v
                elif k == "region":
                    details["region"] = v
                elif k == "aws_access_key_id":
                    details["configured"] = True
                    details["key_prefix"] = v[:8] + "••••" if len(v) > 8 else "••••"
        return details
    except (OSError, UnicodeDecodeError):
        return None


def _probe_aws_profiles() -> list[str] | None:
    """List AWS CLI profile names from ~/.aws/config."""
    config_path = Path.home() / ".aws" / "config"
    if not config_path.exists():
        return None
    try:
        profiles = []
        for line in config_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("[profile "):
                profiles.append(line[9:-1])
            elif line.startswith("[default]"):
                profiles.append("default")
        return profiles[:10]
    except (OSError, UnicodeDecodeError):
        return None


@router.put("/onboarding-complete")
async def set_onboarding_complete():
    """Mark onboarding as complete. Called once when user finishes setup wizard."""
    settings = await db.app_settings.get("default")
    if settings:
        await db.app_settings.update("default", {"onboarding_complete": 1})
    else:
        await db.app_settings.put({"id": "default", "onboarding_complete": 1})
    return {"status": "ok"}


@router.delete("/onboarding-complete")
async def reset_onboarding():
    """Reset onboarding flag. Used by 'Re-run Setup Wizard' in Settings."""
    settings = await db.app_settings.get("default")
    if settings:
        await db.app_settings.update("default", {"onboarding_complete": 0})
    return {"status": "ok"}


@router.get("/resources", response_model=SystemResourcesResponse)
async def get_system_resources() -> SystemResourcesResponse:
    """Get system resource metrics: memory, spawn budget, per-process RSS.

    Designed for the frontend resource ring and diagnostics panel.
    Cheap to call — psutil reads are cached for 5s.
    """
    from core.resource_monitor import resource_monitor
    from core import session_registry

    mem = resource_monitor.system_memory()
    router_inst = session_registry.session_router
    _alive = router_inst.alive_count if router_inst else 0
    budget = resource_monitor.spawn_budget(alive_count=_alive)

    # Collect per-process metrics from alive SessionUnits
    processes: list[ProcessMetricsResponse] = []
    total_rss = 0.0
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
        max_tabs = resource_monitor.compute_max_tabs()
        return MaxTabsResponse(
            max_tabs=max_tabs,
            chat_max=max(1, max_tabs - 1),
            memory_pressure=mem.pressure_level,
        )
    except Exception:
        logger.exception("Failed to compute max tabs")
        return MaxTabsResponse(max_tabs=1, chat_max=1, memory_pressure="critical")


@router.get("/briefing")
async def get_session_briefing() -> dict:
    """Return structured session briefing data for the Welcome Screen.

    Calls proactive_intelligence.build_session_briefing_data() which
    reads MEMORY.md, signal_digest.json, and job results. Never fails
    — returns an empty structure on any error.
    """
    from core.proactive_intelligence import build_session_briefing_data
    ws_path = swarm_workspace_manager.get_workspace_path()
    if not ws_path:
        return {"focus": [], "signals": [], "jobs": [], "learning": None, "generated_at": None}
    return build_session_briefing_data(ws_path)


@router.get("/engine-metrics")
async def get_engine_metrics() -> dict:
    """Return Core Engine growth metrics for the dashboard.

    Aggregates: learning state, memory effectiveness, DDD health,
    hook stats, session volume. All filesystem reads — no LLM, <500ms.
    """
    from core.engine_metrics import collect_engine_metrics

    ws_path = swarm_workspace_manager.get_workspace_path()
    if not ws_path:
        return {"error": "Workspace not initialized"}
    return collect_engine_metrics(ws_path)


@router.get("/tokens/usage")
async def get_token_usage() -> dict:
    """Return token usage summary for TopBar display.

    Returns today and total token counts in millions (1 decimal)
    plus cost in USD. Zero external deps — reads from local SQLite.
    """
    import database

    summary = await database.db.get_token_usage_summary()
    return {
        "today_tokens_m": round(summary["today_tokens"] / 1_000_000, 1),
        "total_tokens_m": round(summary["total_tokens"] / 1_000_000, 1),
        "today_cost_usd": round(summary["today_cost_usd"], 2),
        "total_cost_usd": round(summary["total_cost_usd"], 2),
    }


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


@router.get("/services")
async def get_managed_services():
    """Get status of all managed sidecar services (Slack bot, etc.)."""
    from core.service_manager import service_manager
    return {"services": service_manager.get_status()}


def _run_install_daemon() -> dict:
    """Run the daemon installer and return result.

    Separated for testability (mock target).
    """
    from channels.install_backend_daemon import install
    install()
    return {"status": "installed", "port": 18321}


@router.post("/install-daemon")
async def install_daemon():
    """Install the SwarmAI backend daemon (launchd plist).

    Enables 24/7 operation: channels (Slack) and background jobs stay
    alive even when the desktop app is closed.  macOS only.
    Idempotent — safe to call when already installed.
    """
    if sys.platform != "darwin":
        from fastapi.responses import JSONResponse
        return JSONResponse(
            status_code=400,
            content={"status": "error", "detail": "Daemon mode is only available on macOS"},
        )
    try:
        result = _run_install_daemon()
        return result
    except Exception as e:
        logger.error("Failed to install daemon: %s", e)
        from fastapi.responses import JSONResponse
        return JSONResponse(
            status_code=500,
            content={"status": "error", "detail": str(e)},
        )


@router.post("/uninstall-cleanup")
async def uninstall_cleanup():
    """Remove launchd scheduler plist and clean up background processes.

    Call this before deleting the app to stop the hourly scheduler.
    Safe to call multiple times — idempotent.  Also stops managed
    sidecar services.
    """
    results: dict[str, str] = {}

    # 1. Unload and remove launchd plist
    try:
        from jobs.install_scheduler import uninstall as uninstall_scheduler
        uninstall_scheduler()
        results["scheduler_plist"] = "removed"
    except Exception as e:
        logger.error("Failed to remove scheduler plist: %s", e)
        results["scheduler_plist"] = f"error: {e}"

    # 2. Stop managed services
    try:
        from core.service_manager import service_manager
        await service_manager.stop_all()
        results["services"] = "stopped"
    except Exception as e:
        logger.error("Failed to stop services: %s", e)
        results["services"] = f"error: {e}"

    # 3. Remove port file
    port_file = Path.home() / ".swarm-ai" / "backend.port"
    try:
        port_file.unlink(missing_ok=True)
        results["port_file"] = "removed"
    except Exception:
        results["port_file"] = "already gone"

    logger.info("Uninstall cleanup completed: %s", results)
    return {"status": "cleaned", "details": results}
