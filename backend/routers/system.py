"""System status API endpoints."""
import asyncio
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
from fastapi import APIRouter, Request
from pydantic import BaseModel

from config import get_bedrock_model_id, get_app_data_dir
from jobs.paths import PORT_FILE
from database import db
from core.agent_defaults import build_agent_config, DEFAULT_AGENT_ID
from core.app_config_manager import AppConfigManager, DEFAULT_CONFIG
from core.initialization_manager import initialization_manager
from core.swarm_workspace_manager import swarm_workspace_manager
from channels.gateway import channel_gateway

logger = logging.getLogger(__name__)

router = APIRouter()


class DatabaseStatus(BaseModel):
    """Database health status."""
    healthy: bool
    error: Optional[str] = None
    size_mb: Optional[float] = None  # DB file size on disk


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
    db_size_mb: Optional[float] = None
    try:
        db_healthy = await db.health_check()
        # Report DB file size for storage monitoring
        db_path = Path(db.db_path) if hasattr(db, "db_path") else None
        if db_path and db_path.exists():
            db_size_mb = round(db_path.stat().st_size / (1024 * 1024), 2)
    except Exception as e:
        logger.error(f"Database health check failed: {e}")
        db_error = str(e)

    database_status = DatabaseStatus(healthy=db_healthy, error=db_error, size_mb=db_size_mb)
    
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


def _get_auth_config(override: Optional[dict] = None) -> dict:
    """Read auth-related config from AppConfigManager.

    If ``override`` is provided (from a verify-auth request body), its
    recognized keys are merged over the stored config — so a caller can verify
    a NOT-YET-PERSISTED auth config (onboarding wizard) without writing it to
    disk first. Only known auth keys are honored; unknown keys are ignored.
    """
    try:
        config = AppConfigManager.instance()
        base = {
            "use_bedrock": config.get("use_bedrock", True),
            "aws_region": config.get("aws_region", "us-east-1"),
            "default_model": config.get("default_model", DEFAULT_CONFIG["default_model"]),
            "bedrock_model_map": config.get("bedrock_model_map"),
            "anthropic_base_url": config.get("anthropic_base_url"),
            "auth_method": config.get("auth_method"),
            # Persisted secrets (durable store) — so verify-auth can validate a
            # key/token the user entered in-app without it being in os.environ.
            "anthropic_api_key": config.get("anthropic_api_key"),
            "aws_bearer_token_bedrock": config.get("aws_bearer_token_bedrock"),
        }
    except Exception:
        # AppConfigManager not initialized (e.g., during tests)
        base = {
            "use_bedrock": True,
            "aws_region": "us-east-1",
            "default_model": DEFAULT_CONFIG["default_model"],
            "bedrock_model_map": None,
            "anthropic_base_url": None,
            "auth_method": None,
            "anthropic_api_key": None,
            "aws_bearer_token_bedrock": None,
        }
    if override:
        for k in (
            "use_bedrock", "aws_region", "default_model", "anthropic_base_url",
            "auth_method", "anthropic_api_key", "aws_bearer_token_bedrock",
        ):
            if k in override and override[k] is not None:
                base[k] = override[k]
    return base


def _auth_error(error: str, error_type: str, fix_hint: str) -> dict:
    """Build a standardized auth error response."""
    return {
        "success": False,
        "error": error,
        "error_type": error_type,
        "fix_hint": fix_hint,
    }


@router.post("/verify-auth")
async def verify_auth(request: Request):
    """Verify LLM authentication by making a minimal API call.

    Accepts an OPTIONAL JSON body (aws_region, use_bedrock, default_model,
    anthropic_base_url) merged over the stored config — so the onboarding wizard
    can verify a config the user has entered but NOT yet persisted, and only
    persist it AFTER a successful verify. An empty/absent body falls back to the
    stored config (the Settings-tab caller sends no body).

    Then:
    - Bedrock: boto3 bedrock-runtime.invoke_model with max_tokens=1
    - API key: httpx POST to messages API with max_tokens=1

    Always returns 200 -- success/failure is in the response body.
    """
    # Tolerate an empty/non-JSON body — request.json() raises on empty content.
    override: dict = {}
    try:
        parsed = await request.json()
        if isinstance(parsed, dict):
            override = parsed
    except Exception:
        override = {}

    config = _get_auth_config(override=override)
    use_bedrock = config.get("use_bedrock", True)

    if use_bedrock:
        return await _verify_bedrock(config)
    else:
        return await _verify_anthropic_api(config)


class SetApiKeyRequest(BaseModel):
    api_key: str


class SetBearerTokenRequest(BaseModel):
    bearer_token: str


class SetAuthMethodRequest(BaseModel):
    method: str  # "ada" | "sso" | "apikey" | "iam_role" | "bedrock_api_key"
    deployment_context: Optional[str] = None  # "internal" | "external"


_VALID_AUTH_METHODS = {"ada", "sso", "apikey", "iam_role", "bedrock_api_key"}


@router.post("/anthropic-api-key")
async def set_anthropic_api_key(req: SetApiKeyRequest):
    """Persist the user's Anthropic API key to the durable 0o600 secret store.

    This is the ONLY sanctioned write path for the key (never via PUT /settings,
    which strips secrets). The key is NEVER echoed back in any response. After
    this, _configure_claude_environment injects it into the SDK env at the next
    spawn (no daemon relaunch) and verify-auth can validate it.
    """
    key = (req.api_key or "").strip()
    if not key:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail="api_key must be non-empty")
    AppConfigManager.instance().set_secret("anthropic_api_key", key)
    return {"status": "ok", "configured": True}


@router.post("/bedrock-api-key")
async def set_bedrock_api_key(req: SetBearerTokenRequest):
    """Persist the user's Bedrock bearer token (AWS_BEARER_TOKEN_BEDROCK).

    Mirrors set_anthropic_api_key: the ONLY sanctioned write path for the token
    (never via PUT /settings, which strips secrets). NEVER echoed back. After
    this, _configure_claude_environment injects it as AWS_BEARER_TOKEN_BEDROCK
    into the SDK env at the next spawn (no daemon relaunch) when
    auth_method=="bedrock_api_key", and verify-auth can validate it.
    """
    token = (req.bearer_token or "").strip()
    if not token:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail="bearer_token must be non-empty")
    AppConfigManager.instance().set_secret("aws_bearer_token_bedrock", token)
    return {"status": "ok", "configured": True}


@router.post("/auth-method")
async def set_auth_method(req: SetAuthMethodRequest):
    """Persist the chosen auth method (+ optional deployment_context).

    Non-secret. Lets error remediation (CredentialBanner / spawn pre-flight) be
    method-aware — use_bedrock alone can't distinguish ada from sso.
    """
    if req.method not in _VALID_AUTH_METHODS:
        from fastapi import HTTPException
        raise HTTPException(
            status_code=400,
            detail=f"method must be one of {sorted(_VALID_AUTH_METHODS)}",
        )
    updates: dict = {"auth_method": req.method}
    if req.deployment_context in ("internal", "external"):
        updates["deployment_context"] = req.deployment_context
    AppConfigManager.instance().update(updates)

    # Eager cleanup on switch AWAY from bedrock_api_key (Meta-review MED,
    # run_9d9f7dff): the spawn-path clear in _configure_claude_environment is
    # LAZY (fires only on the next chat spawn). Between the switch and that
    # spawn, job executors / managed services that snapshot os.environ would
    # inherit the now-wrong bearer token. Pop it immediately (under _env_lock,
    # keyed on the "we-injected" marker so a user's ambient export is untouched)
    # to shrink the stale window to zero.
    if req.method != "bedrock_api_key":
        from core import claude_environment as _ce
        async with _ce._env_lock:
            if _ce._injected_bearer_token is not None and \
                    os.environ.get("AWS_BEARER_TOKEN_BEDROCK") == _ce._injected_bearer_token:
                os.environ.pop("AWS_BEARER_TOKEN_BEDROCK", None)
                _ce._injected_bearer_token = None
    return {"status": "ok", "auth_method": req.method}


_MISSING = object()  # sentinel: an env var that was ABSENT (restore = pop, not "")


async def _verify_bedrock(config: dict) -> dict:
    """Verify Bedrock auth with a minimal invoke.

    Supports bearer-token (bedrock_api_key) auth: botocore reads
    AWS_BEARER_TOKEN_BEDROCK from os.environ at CLIENT-CONSTRUCTION time (the
    token is frozen into the signer there — botocore>=1.35). So for a
    not-yet-persisted bearer token arriving in the verify override, we must set
    the env var around `boto3.client(...)` ONLY.

    Concurrency (Gate-1 fix): os.environ is process-global and shared with the
    spawn path (_configure_claude_environment), which mutates env under the
    module-level `_env_lock`. So the client-construction env mutation here MUST
    also hold `_env_lock` — otherwise a concurrent session spawn could read this
    verify token, or its finally-restore could strip a token a concurrent bedrock
    spawn just injected. The blocking invoke_model runs in a thread OUTSIDE the
    lock (the token is already frozen into the client), so the event loop and the
    lock are both freed during the ~15s network call.
    """
    region = config.get("aws_region", "us-east-1")
    model = config.get("default_model", DEFAULT_CONFIG["default_model"])
    bedrock_model = get_bedrock_model_id(model, config.get("bedrock_model_map"))
    # Only inject a bearer token when THIS verify is for the bedrock_api_key
    # method (else a stray persisted token could shadow a legit sigv4 verify).
    bearer_token = (
        config.get("aws_bearer_token_bedrock")
        if config.get("auth_method") == "bedrock_api_key"
        else None
    )

    start = time.monotonic()
    try:
        # Bounded timeouts so an unreachable/slow AWS returns an error in
        # seconds instead of hanging the "Verify Connection" spinner for the
        # ~60s botocore default × retries. Matches the timeout pattern used at
        # every other Bedrock call site (memory_extractor, embedding_client,
        # summarization, …). max_attempts=1 = no silent retry storm on verify.
        # NOTE: do NOT set signature_version here — it would set
        # has_in_code_configuration=True and suppress botocore's bearer-token
        # preference over sigv4 (Gate-0 skeptic finding).
        from botocore.config import Config as _BotoConfig
        _verify_cfg = _BotoConfig(
            connect_timeout=5,
            read_timeout=10,
            retries={"max_attempts": 1},
        )
        from core.claude_environment import _env_lock
        _KEY = "AWS_BEARER_TOKEN_BEDROCK"
        async with _env_lock:
            # Set the bearer token for a bedrock_api_key verify; for a sigv4
            # method (ada/sso), REMOVE any residual token so botocore can't
            # prefer a leftover bearer (injected by a prior bedrock_api_key
            # spawn in this long-lived daemon) over the sigv4 creds we mean to
            # test (Gate-2 correctness finding). Either way, restore os.environ
            # to its prior state after the client is constructed (token frozen
            # into the signer at construction, so this window is minimal).
            _prev = os.environ.get(_KEY, _MISSING)
            if bearer_token:
                os.environ[_KEY] = bearer_token
            else:
                os.environ.pop(_KEY, None)
            try:
                # botocore freezes the token/creds into the signer HERE.
                client = boto3.client(
                    "bedrock-runtime", region_name=region, config=_verify_cfg
                )
            finally:
                if _prev is _MISSING:
                    os.environ.pop(_KEY, None)
                else:
                    os.environ[_KEY] = _prev

        def _invoke():
            return client.invoke_model(
                modelId=bedrock_model,
                contentType="application/json",
                accept="application/json",
                body=_json.dumps({
                    "anthropic_version": "bedrock-2023-05-31",
                    "max_tokens": 1,
                    "messages": [{"role": "user", "content": "hi"}],
                }),
            )
        # Blocking network call off the event loop (token already frozen into
        # the client above; no env dependency remains here).
        await asyncio.to_thread(_invoke)
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
        # #4b: the raw boto/botocore exception string routinely carries the
        # inference-profile ARN, account-id, and request-id. Log it server-side
        # for debugging, but NEVER return it in the client-facing `error` field
        # (it is forwarded to the frontend and into frontend.log). The actionable
        # guidance lives in fix_hint; error_type below is classified from the RAW
        # string BEFORE generalization, so classification is unaffected.
        logger.warning("Bedrock verify failed (raw): %s", error_str)

        # Remediation must match the user's ACTUAL auth method — never hardcode
        # ADA (an Amazon-internal command) for an SSO / bedrock_api_key user
        # (F1). _verify_bedrock is only reached for Bedrock methods
        # (ada/sso/iam_role/bedrock_api_key), so remediation_for covers the set;
        # a None method (unpersisted onboarding) returns the safe generic
        # fallback rather than ADA jargon.
        from core.auth_remediation import remediation_for
        _rem_fix = remediation_for(config.get("auth_method"))["fix_text"]

        if "ExpiredToken" in error_str or "expired" in error_str.lower():
            return _auth_error("Credentials expired.", "expired_credentials", _rem_fix)
        if "InvalidIdentityToken" in error_str or "UnrecognizedClient" in error_str:
            return _auth_error("Credentials invalid.", "invalid_credentials", _rem_fix)
        if "not authorized" in error_str.lower() or "AccessDenied" in error_str:
            run_mode = os.environ.get("SWARMAI_MODE", "daemon")
            if run_mode == "hive":
                hint = (
                    "IAM instance role lacks bedrock:InvokeModel permission. "
                    "Add it to the role's policy, or check that Bedrock model access "
                    "is enabled in this region via the AWS Console."
                )
            else:
                hint = "Model access not enabled in this region. Check Bedrock console."
            return _auth_error("Access denied by Bedrock.", "access_denied", hint)
        return _auth_error("Bedrock verification failed.", "unknown",
                           "Check AWS configuration and try again.")


async def _verify_anthropic_api(config: dict) -> dict:
    """Verify Anthropic API key with a minimal messages call.

    Reads the key from the config secret store first (the in-app key the user
    just entered), falling back to the ANTHROPIC_API_KEY env var. Without the
    config fallback, a freshly-entered key could never verify (it isn't in the
    daemon's env) — the external Anthropic-direct path would be dead.
    """
    api_key = config.get("anthropic_api_key") or os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return _auth_error(
            "No Anthropic API key configured", "missing_key",
            "Enter your Anthropic API key in the setup wizard or Settings → AI & Models."
        )

    base_url = config.get("anthropic_base_url") or "https://api.anthropic.com"
    model = config.get("default_model", DEFAULT_CONFIG["default_model"])
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
        # #4b: log the raw provider message server-side, but return a
        # generalized, identifier-free message to the client. Classification is
        # by HTTP status (below), not by the message text, so generalizing is safe.
        logger.warning("Anthropic verify failed (status=%s, raw): %s",
                       resp.status_code, error_msg)

        if resp.status_code == 401:
            return _auth_error("API key rejected (401).", "invalid_key",
                               "API key is invalid. Check the key at console.anthropic.com.")
        if resp.status_code == 403:
            return _auth_error("Access forbidden (403).", "forbidden",
                               "API key doesn't have access to this model.")

        return _auth_error(f"Anthropic API error (status {resp.status_code}).",
                           "api_error", "Check Anthropic API status.")

    except httpx.ConnectError:
        return _auth_error("Cannot reach API endpoint", "network",
                           f"Check network connectivity to {base_url}")
    except Exception as e:
        logger.warning("Anthropic verify failed (raw): %s", e)
        return _auth_error("Anthropic verification failed.", "unknown",
                           "Check API configuration.")


@router.get("/auth-hint")
async def get_auth_hint():
    """Return hints about the local credential environment.

    Helps the frontend pick a sensible default auth method card
    and show real credential status when already configured.
    """
    has_ada = Path.home().joinpath(".ada").is_dir()
    has_midway = Path.home().joinpath(".midway").is_dir()
    has_sso_cache = bool(list(Path.home().joinpath(".aws/sso/cache").glob("*.json")))
    has_api_key = bool(os.environ.get("ANTHROPIC_API_KEY"))

    # Deployment context drives which auth-method cards the wizard shows:
    #   internal → [ADA, SSO]   external → [SSO, Anthropic Direct]
    # Detection is ASYMMETRIC: ~/.ada or ~/.midway present = confident internal
    # (Amazon-only tools). Their ABSENCE only DEFAULTS to external — the frontend
    # offers a one-click "Amazon employee?" toggle for internal users on a fresh
    # machine that hasn't run mwinit/ada yet. Deliberately NOT keyed on ~/.toolbox
    # (a generic name non-Amazon tools use → false-positives, Gate-1 FIX-E).
    deployment_context = "internal" if (has_ada or has_midway) else "external"

    # detection_confidence: "high" iff a POSITIVE internal signal (~/.ada|~/.midway)
    # or an SSO cache was found — i.e. we detected something, not just defaulted.
    # "low" when we saw NO signal and fell back to external — the frontend uses
    # this to make the internal/external toggle discoverable for a pre-mwinit
    # Amazon employee on a fresh machine (F3).
    detection_confidence = (
        "high" if (has_ada or has_midway or has_sso_cache) else "low"
    )

    # Suggested method MUST be consistent with deployment_context — the wizard
    # only renders the apikey card for EXTERNAL context, so suggesting "apikey"
    # on an internal machine (because ANTHROPIC_API_KEY happens to be exported)
    # would be silently discarded by the frontend's snap-to-first (F4). Only
    # suggest a method that will actually be in the resulting card set.
    if has_api_key and deployment_context == "external":
        suggested = "apikey"
    elif has_ada:
        suggested = "ada"
    elif has_sso_cache:
        suggested = "sso"
    else:
        suggested = "sso"  # safest default (in both internal & external card sets)

    # Probe real credential details for display. Run OFF the event loop:
    # _probe_ada_details shells out (subprocess.run, timeout=5, up to 2×) and
    # _probe_aws_profiles does file IO — a slow/hung ada CLI would otherwise
    # block ALL backend requests for up to ~10s. Mirrors _probe_iam_instance_role
    # below, which already uses to_thread for the same reason.
    ada_details = await asyncio.to_thread(_probe_ada_details) if has_ada else None
    aws_profiles = await asyncio.to_thread(_probe_aws_profiles)  # config file IO, off-loop

    # Detect run mode so frontend can adjust auth UX
    run_mode = os.environ.get("SWARMAI_MODE", "daemon")

    # Hive (EC2) uses IAM instance role — no ADA or SSO needed.
    # Run in thread to avoid blocking the event loop (3 sync httpx calls, 1s timeout each).
    iam_details = None
    if run_mode == "hive":
        iam_details = await asyncio.to_thread(_probe_iam_instance_role)
        # On Hive, ALWAYS suggest iam_role even if IMDS probe fails —
        # it's the only valid auth method. Desktop methods are noise.
        suggested = "iam_role"
        ada_details = None
        aws_profiles = None
        # Hive's auth method is unambiguous (IAM instance role) — no toggle needed.
        detection_confidence = "high"

    return {
        "has_ada_dir": has_ada if run_mode != "hive" else False,
        "has_sso_cache": has_sso_cache if run_mode != "hive" else False,
        "has_api_key": has_api_key,
        "deployment_context": deployment_context,
        "detection_confidence": detection_confidence,
        "suggested_method": suggested,
        "ada_details": ada_details,
        "aws_profiles": aws_profiles,
        "run_mode": run_mode,
        "iam_details": iam_details,
    }


def _probe_ada_details() -> dict | None:
    """Read ADA credential status from ada profile + credentials.

    Account/role come from ``ada profile print`` (the profile config).
    Key prefix + configured status come from ``~/.ada/credentials`` (the
    temporary STS tokens).  Previous implementation only read credentials,
    which never contains account_id or role_name — so the UI always showed
    empty placeholders.
    """
    import subprocess as _sp

    if not Path.home().joinpath(".ada").is_dir():
        return None

    details: dict = {}

    # 1. Read account/role from ada profile config (source of truth).
    #    Try "bedrock" profile first (standard for Bedrock API users),
    #    fall back to default profile if it doesn't exist.
    ada_bin = str(Path.home() / ".toolbox" / "bin" / "ada")
    for profile_args in (["--profile=bedrock"], []):
        try:
            result = _sp.run(
                [ada_bin, "profile", "print"] + profile_args,
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0:
                for line in result.stdout.splitlines():
                    line = line.strip()
                    if line.startswith("Account:"):
                        details["account_id"] = line.split(":", 1)[1].strip()
                    elif line.startswith("Role:"):
                        details["role_name"] = line.split(":", 1)[1].strip()
                if details.get("account_id"):
                    break  # got what we need, skip fallback
        except (OSError, _sp.TimeoutExpired):
            pass

    # 2. Read key prefix from ~/.ada/credentials (temporary tokens)
    creds_path = Path.home() / ".ada" / "credentials"
    if creds_path.exists():
        try:
            for line in creds_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if "=" in line and not line.startswith("["):
                    k, v = line.split("=", 1)
                    k, v = k.strip(), v.strip()
                    if k == "aws_access_key_id":
                        details["configured"] = True
                        details["key_prefix"] = v[:8] + "••••" if len(v) > 8 else "••••"
        except (OSError, UnicodeDecodeError):
            pass

    return details if details else None


def _probe_aws_profiles() -> list[str] | None:
    """List **real AWS SSO** profile names from ~/.aws/config.

    Only returns profiles that use ``sso_start_url`` or ``sso_session``
    — the markers of genuine IAM Identity Center configuration.  Profiles
    that use ``credential_process`` (e.g. Ada) are NOT SSO profiles and
    were previously reported as such, misleading the Settings UI.
    """
    config_path = Path.home() / ".aws" / "config"
    if not config_path.exists():
        return None
    try:
        profiles: list[str] = []
        current_profile: str | None = None
        is_sso = False
        for line in config_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("[profile ") or line.startswith("[default]"):
                # Flush previous profile
                if current_profile and is_sso:
                    profiles.append(current_profile)
                current_profile = line[9:-1] if line.startswith("[profile ") else "default"
                is_sso = False
            elif current_profile and ("sso_start_url" in line or "sso_session" in line):
                is_sso = True
        # Flush last profile
        if current_profile and is_sso:
            profiles.append(current_profile)
        return profiles[:10] if profiles else None
    except (OSError, UnicodeDecodeError):
        return None


def _probe_iam_instance_role() -> dict | None:
    """Probe EC2 instance metadata (IMDSv2) for IAM role and identity details.

    Returns a dict with account_id, region, instance_id, role_name when
    running on EC2 with an IAM instance role.  Returns None off-EC2.

    Timeout is aggressive (1s) since IMDS responds in <10ms on EC2 and
    is unreachable off-EC2.
    """
    try:
        import httpx as _httpx

        # IMDSv2: get session token
        token_resp = _httpx.put(
            "http://169.254.169.254/latest/api/token",
            headers={"X-aws-ec2-metadata-token-ttl-seconds": "60"},
            timeout=1.0,
        )
        if token_resp.status_code != 200:
            return None
        headers = {"X-aws-ec2-metadata-token": token_resp.text}

        details: dict = {}

        # Role name from security-credentials
        role_resp = _httpx.get(
            "http://169.254.169.254/latest/meta-data/iam/security-credentials/",
            headers=headers, timeout=1.0,
        )
        if role_resp.status_code == 200 and role_resp.text.strip():
            details["role_name"] = role_resp.text.strip().splitlines()[0]
        else:
            return None  # No role = no IAM instance role

        # Instance identity document (account_id, region, instance_id)
        try:
            identity_resp = _httpx.get(
                "http://169.254.169.254/latest/dynamic/instance-identity/document",
                headers=headers, timeout=1.0,
            )
            if identity_resp.status_code == 200:
                import json as _json
                doc = _json.loads(identity_resp.text)
                details["account_id"] = doc.get("accountId")
                details["region"] = doc.get("region")
                details["instance_id"] = doc.get("instanceId")
        except Exception:
            pass  # identity doc is bonus info, role presence is sufficient

        return details
    except Exception:
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


# ── Briefing cache ──────────────────────────────────────────────────
# In-memory cache with 60s TTL.  Briefing data changes infrequently
# (signals update 3×/day, jobs hourly, focus on new sessions) — no
# need to re-read 6 files on every tab open.
_briefing_cache: dict = {"data": None, "expires_at": 0.0}
_BRIEFING_CACHE_TTL = 60  # seconds
# Debounce window for the stale path: how long to suppress duplicate background
# refreshes WITHOUT masking a persistent failure. Deliberately short (≪ TTL) so a
# FAILING refresh re-converges to a cold, error-surfacing miss instead of pushing
# expiry a full TTL every request and staying stale forever (adversarial MEDIUM).
_BRIEFING_REFRESH_DEBOUNCE = 5  # seconds
_briefing_refresh_lock = asyncio.Lock()  # single-flight: one recompute at a time
# Strong refs to in-flight background refresh tasks — the event loop keeps only a
# WEAK ref, so without this a fire-and-forget task can be GC'd mid-flight and the
# revalidate silently never happens (adversarial HIGH). Discard on completion.
_briefing_bg_tasks: set = set()
_EMPTY_BRIEFING = {"focus": [], "signals": [], "jobs": [], "todos": [], "learning": None, "generated_at": None}


def _spawn_briefing_refresh() -> None:
    """Launch a background briefing refresh, holding a strong ref so it can't be
    GC'd, and logging (never swallowing) any failure so a persistently-failing
    builder is visible rather than silently serving stale forever."""
    async def _guarded() -> None:
        try:
            await _refresh_briefing_cache()
        except Exception as exc:  # never crash the loop; surface for remediation
            logger.warning("briefing background refresh failed: %s", exc)
    t = asyncio.create_task(_guarded())
    _briefing_bg_tasks.add(t)
    t.add_done_callback(_briefing_bg_tasks.discard)


async def _refresh_briefing_cache() -> dict:
    """Recompute briefing_data on the DEDICATED 'briefing' pool (NOT the default
    ThreadPoolExecutor) so the heavy glob+sqlite recompute can never starve the
    default pool the event loop uses to schedule /health (run_b36c7880). Single-
    flight: concurrent callers await the one in-flight recompute instead of each
    spawning their own (that 60s-expiry stampede is exactly what saturated the
    pool → 28.5s → offline)."""
    from core import executors
    from core.proactive_intelligence import build_session_briefing_data
    ws_path = swarm_workspace_manager.get_workspace_path()
    if not ws_path:
        return _EMPTY_BRIEFING
    async with _briefing_refresh_lock:
        # Someone may have refreshed while we waited for the lock.
        if _briefing_cache["data"] is not None and time.monotonic() < _briefing_cache["expires_at"]:
            return _briefing_cache["data"]
        result = await executors.run_in("briefing", build_session_briefing_data, ws_path)
        _briefing_cache["data"] = result
        _briefing_cache["expires_at"] = time.monotonic() + _BRIEFING_CACHE_TTL
        return result


@router.get("/briefing")
async def get_session_briefing() -> dict:
    """Return structured session briefing data for the Welcome Screen.

    STALE-WHILE-REVALIDATE + dedicated-pool (run_b36c7880): a request NEVER
    blocks on the recompute —
      • fresh cache        → return it;
      • stale-but-present  → return stale NOW, refresh in the background on the
                             dedicated 'briefing' pool (single-flight);
      • cold (no cache)    → first recompute on the 'briefing' pool (bounded),
                             not the default pool.
    Removes the failure mode where a 60s-expiry cache miss ran the ~28s recompute
    on a default-pool request thread and starved /health. Never fails."""
    now = time.monotonic()
    data = _briefing_cache["data"]
    if data is not None and now < _briefing_cache["expires_at"]:
        return data  # fresh

    if data is not None:
        # Serve stale immediately; refresh off the request path on the dedicated
        # pool. Push expiry out only by a SHORT debounce window (not a full TTL):
        # enough to suppress a per-request refresh stampede, but short enough that
        # a FAILING refresh re-converges to a cold error-surfacing miss instead of
        # staying stale forever (adversarial MEDIUM). A SUCCESSFUL refresh writes
        # the real now+TTL expiry inside _refresh_briefing_cache.
        _briefing_cache["expires_at"] = now + _BRIEFING_REFRESH_DEBOUNCE
        _spawn_briefing_refresh()
        return data

    # Cold start: compute once on the dedicated 'briefing' pool (single-flight).
    try:
        return await _refresh_briefing_cache()
    except Exception as exc:  # never fail the endpoint
        logger.debug("briefing cold refresh failed: %s", exc)
        return _EMPTY_BRIEFING


@router.post("/briefing/dismiss")
async def dismiss_focus_item(body: dict) -> dict:
    """Dismiss a focus item so it won't appear in future briefings.

    Stores the title in proactive_state.json with a 7-day TTL.
    """
    title = body.get("title", "").strip()
    if not title:
        return {"ok": False, "error": "title is required"}
    ws_path = swarm_workspace_manager.get_workspace_path()
    if not ws_path:
        return {"ok": False, "error": "workspace not found"}
    from core.proactive_learning import dismiss_focus_item as _dismiss
    _dismiss(Path(ws_path), title)
    # Invalidate briefing cache so dismiss takes effect immediately
    _briefing_cache["data"] = None
    _briefing_cache["expires_at"] = 0.0
    return {"ok": True}


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
    """Get status of all managed subsidiary services (Slack bot, etc.)."""
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
    subsidiary services.
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
    port_file = PORT_FILE
    try:
        port_file.unlink(missing_ok=True)
        results["port_file"] = "removed"
    except Exception:
        results["port_file"] = "already gone"

    logger.info("Uninstall cleanup completed: %s", results)
    return {"status": "cleaned", "details": results}


# ── Workspace Backup & Restore ────────────────────────────────────

class BackupConfigBody(BaseModel):
    """Request body for backup configuration."""
    repo_url: Optional[str] = None
    token: Optional[str] = None
    schedule: Optional[str] = None


def _get_backup_manager():
    """Lazy-init BackupManager singleton."""
    if not hasattr(_get_backup_manager, "_instance"):
        from core.backup_manager import BackupManager
        _get_backup_manager._instance = BackupManager()
    return _get_backup_manager._instance


_BACKUP_COOLDOWN_SECONDS = 300  # 5 minutes between backups
_last_backup_time: float = 0.0


@router.post("/backup")
async def run_backup() -> dict:
    """Run an immediate workspace backup.

    Exports DB L2 tables, copies config, commits and pushes to GitHub.
    Rate-limited to once per 5 minutes to prevent abuse.
    """
    global _last_backup_time
    now = time.monotonic()
    elapsed = now - _last_backup_time
    if _last_backup_time > 0 and elapsed < _BACKUP_COOLDOWN_SECONDS:
        remaining = int(_BACKUP_COOLDOWN_SECONDS - elapsed)
        return {"status": "rate_limited", "retry_after_seconds": remaining}
    mgr = _get_backup_manager()
    result = await mgr.backup()
    _last_backup_time = time.monotonic()
    return result


@router.get("/backup/status")
async def backup_status() -> dict:
    """Return current backup status: last_backup, repo_url, schedule, enabled."""
    mgr = _get_backup_manager()
    return mgr.get_status()


@router.put("/backup/config")
async def backup_config(body: BackupConfigBody) -> dict:
    """Update backup configuration: repo_url, token, schedule."""
    mgr = _get_backup_manager()
    return mgr.configure(
        repo_url=body.repo_url,
        token=body.token,
        schedule=body.schedule,
    )


class RestoreBody(BaseModel):
    """Request body for restore."""
    repo_url: str
    token: Optional[str] = None


@router.post("/backup/restore")
async def restore_backup(body: RestoreBody):
    """Restore workspace from a backup repo. Returns SSE event stream.

    Each event: {"stage": "clone|config|db_import|schema_migrate|verify",
                 "progress": 0-100, "detail": "..."}
    """
    from starlette.responses import StreamingResponse

    mgr = _get_backup_manager()

    async def event_stream():
        async for event in mgr.restore(repo_url=body.repo_url, token=body.token):
            yield f"data: {_json.dumps(event)}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


# ── Frontend log forwarding (observability) ──────────────────────
# The production webview console is not persisted anywhere — diagnosing UI
# issues required asking the user to open DevTools. This endpoint receives
# batched console errors/warnings + uncaught errors from the frontend and
# appends them to ~/.swarm-ai/logs/frontend.log so they can be grepped.

_FRONTEND_LOG_MAX_BYTES = 5 * 1024 * 1024  # 5MB cap (truncate-from-head on exceed)


class ClientLogEntry(BaseModel):
    """A single forwarded frontend log line."""
    level: str                      # "error" | "warn" | "log"
    message: str
    ts: Optional[str] = None        # ISO timestamp (frontend clock)
    source: Optional[str] = None    # "file:line:col" or component hint


class ClientLogBatch(BaseModel):
    """A batch of frontend log entries (flushed periodically)."""
    entries: list[ClientLogEntry]


@router.post("/client-logs")
async def ingest_client_logs(batch: ClientLogBatch) -> dict:
    """Append forwarded frontend console logs to ~/.swarm-ai/logs/frontend.log.

    Best-effort and defensive: never raises to the client, caps batch size,
    truncates the file from the head when it grows past the size cap.
    """
    try:
        log_path = get_app_data_dir() / "logs" / "frontend.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)

        # Size cap: keep the most recent half when the file exceeds the limit.
        try:
            if log_path.exists() and log_path.stat().st_size > _FRONTEND_LOG_MAX_BYTES:
                tail = log_path.read_bytes()[-(_FRONTEND_LOG_MAX_BYTES // 2):]
                log_path.write_bytes(tail)
        except OSError:
            pass  # rotation is best-effort

        lines = []
        for e in batch.entries[:200]:  # cap per request
            ts = e.ts or datetime.now(timezone.utc).isoformat()
            msg = (e.message or "")[:4000]
            src = f" ({e.source})" if e.source else ""
            lines.append(f"{ts} [{e.level.upper()}]{src} {msg}")

        if lines:
            with open(log_path, "a") as f:
                f.write("\n".join(lines) + "\n")
        return {"status": "ok", "written": len(lines)}
    except Exception as exc:  # never break the client on a logging path
        logger.debug("client-logs ingest failed (non-fatal): %s", exc)
        return {"status": "error"}
