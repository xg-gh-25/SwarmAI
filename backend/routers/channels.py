"""Channel management CRUD and lifecycle API endpoints."""
import json
import logging
from typing import Optional

from fastapi import APIRouter

from database import db
from channels.gateway import channel_gateway
from channels.registry import list_supported_types, get_adapter_class
from schemas.channel import (
    ChannelCreateRequest,
    ChannelUpdateRequest,
    ChannelResponse,
    ChannelStatusResponse,
    ChannelSessionResponse,
)
from core.exceptions import NotFoundException, ValidationException

logger = logging.getLogger(__name__)

router = APIRouter()


# ============== Helper Functions ==============

def _parse_json_list(value) -> list:
    """Parse a JSON string to a list, or return the value if already a list."""
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, list) else []
        except (json.JSONDecodeError, TypeError):
            return []
    return value if isinstance(value, list) else []


def _channel_to_response(channel_data: dict, agent_name: Optional[str] = None) -> ChannelResponse:
    """Convert a database channel dict to a ChannelResponse.

    Handles JSON parsing for list fields and integer-to-bool conversion
    for SQLite-stored boolean fields.
    """
    # Parse config if stored as JSON string
    config = channel_data.get("config", {})
    if isinstance(config, str):
        try:
            config = json.loads(config)
        except (json.JSONDecodeError, TypeError):
            config = {}

    return ChannelResponse(
        id=channel_data["id"],
        name=channel_data["name"],
        channel_type=channel_data["channel_type"],
        agent_id=channel_data["agent_id"],
        agent_name=agent_name,
        config=config,
        status=channel_data.get("status", "inactive"),
        error_message=channel_data.get("error_message"),
        access_mode=channel_data.get("access_mode", "allowlist"),
        allowed_senders=_parse_json_list(channel_data.get("allowed_senders", [])),
        blocked_senders=_parse_json_list(channel_data.get("blocked_senders", [])),
        rate_limit_per_minute=int(channel_data.get("rate_limit_per_minute", 10)),
        enable_skills=bool(channel_data.get("enable_skills", False)),
        enable_mcp=bool(channel_data.get("enable_mcp", False)),
        created_at=channel_data["created_at"],
        updated_at=channel_data["updated_at"],
    )


# ============== CRUD Endpoints ==============

@router.get("/", response_model=list[ChannelResponse])
async def list_channels():
    """List all channels, enriched with agent names."""
    channels = await db.channels.list()
    results = []
    # Build a cache of agent names to avoid repeated lookups
    agent_cache: dict[str, Optional[str]] = {}
    for ch in channels:
        agent_id = ch.get("agent_id")
        if agent_id not in agent_cache:
            agent = await db.agents.get(agent_id)
            agent_cache[agent_id] = agent["name"] if agent else None
        results.append(_channel_to_response(ch, agent_name=agent_cache[agent_id]))
    return results


@router.get("/types")
async def list_channel_types():
    """List all supported channel types with metadata."""
    return list_supported_types()


@router.get("/{channel_id}", response_model=ChannelResponse)
async def get_channel(channel_id: str):
    """Get a single channel by ID."""
    channel = await db.channels.get(channel_id)
    if not channel:
        raise NotFoundException(
            detail=f"Channel with ID '{channel_id}' not found"
        )
    # Enrich with agent name
    agent = await db.agents.get(channel["agent_id"])
    agent_name = agent["name"] if agent else None
    return _channel_to_response(channel, agent_name=agent_name)


@router.post("/", response_model=ChannelResponse, status_code=201)
async def create_channel(request: ChannelCreateRequest):
    """Create a new channel."""
    # Validate agent exists
    agent = await db.agents.get(request.agent_id)
    if not agent:
        raise NotFoundException(
            detail=f"Agent with ID '{request.agent_id}' not found"
        )

    channel_data = {
        "name": request.name,
        "channel_type": request.channel_type,
        "agent_id": request.agent_id,
        "config": request.config,
        "status": "inactive",
        "access_mode": request.access_mode,
        "allowed_senders": request.allowed_senders,
        "blocked_senders": [],
        "rate_limit_per_minute": request.rate_limit_per_minute,
        "enable_skills": request.enable_skills,
        "enable_mcp": request.enable_mcp,
    }

    channel = await db.channels.put(channel_data)
    logger.info(f"Created channel '{request.name}' (type={request.channel_type}, agent={request.agent_id})")
    return _channel_to_response(channel, agent_name=agent["name"])


@router.put("/{channel_id}", response_model=ChannelResponse)
async def update_channel(channel_id: str, request: ChannelUpdateRequest):
    """Update a channel. Only non-None fields are updated."""
    channel = await db.channels.get(channel_id)
    if not channel:
        raise NotFoundException(
            detail=f"Channel with ID '{channel_id}' not found"
        )

    updates = {}
    if request.name is not None:
        updates["name"] = request.name
    if request.config is not None:
        updates["config"] = request.config
    if request.agent_id is not None:
        # Validate the new agent exists
        agent = await db.agents.get(request.agent_id)
        if not agent:
            raise NotFoundException(
                detail=f"Agent with ID '{request.agent_id}' not found"
            )
        updates["agent_id"] = request.agent_id
    if request.access_mode is not None:
        updates["access_mode"] = request.access_mode
    if request.allowed_senders is not None:
        updates["allowed_senders"] = request.allowed_senders
    if request.blocked_senders is not None:
        updates["blocked_senders"] = request.blocked_senders
    if request.rate_limit_per_minute is not None:
        updates["rate_limit_per_minute"] = request.rate_limit_per_minute
    if request.enable_skills is not None:
        updates["enable_skills"] = request.enable_skills
    if request.enable_mcp is not None:
        updates["enable_mcp"] = request.enable_mcp

    if updates:
        updated = await db.channels.update(channel_id, updates)
        if updated:
            channel = updated

    # Enrich with agent name
    agent_id = channel.get("agent_id") if channel else None
    agent = await db.agents.get(agent_id) if agent_id else None
    agent_name = agent["name"] if agent else None

    logger.info(f"Updated channel '{channel_id}' with fields: {list(updates.keys())}")
    return _channel_to_response(channel, agent_name=agent_name)


@router.delete("/{channel_id}", status_code=204)
async def delete_channel(channel_id: str):
    """Delete a channel. Stops the channel if active and removes all sessions."""
    channel = await db.channels.get(channel_id)
    if not channel:
        raise NotFoundException(
            detail=f"Channel with ID '{channel_id}' not found"
        )

    # Stop channel and cancel any pending retries before deletion
    try:
        await channel_gateway.stop_channel(channel_id)
        logger.info(f"Stopped channel '{channel_id}' before deletion")
    except Exception as e:
        logger.warning(f"Error stopping channel '{channel_id}' during deletion: {e}")

    # Delete all channel sessions
    deleted_sessions = await db.channel_sessions.delete_by_channel(channel_id)
    logger.info(f"Deleted {deleted_sessions} sessions for channel '{channel_id}'")

    # Delete the channel
    await db.channels.delete(channel_id)
    logger.info(f"Deleted channel '{channel_id}'")


# ============== Lifecycle Endpoints ==============

@router.post("/{channel_id}/start")
async def start_channel(channel_id: str):
    """Start a channel, activating its adapter to listen for messages."""
    channel = await db.channels.get(channel_id)
    if not channel:
        raise NotFoundException(
            detail=f"Channel with ID '{channel_id}' not found"
        )

    try:
        await channel_gateway.start_channel(channel_id)
    except ValueError as e:
        raise ValidationException(
            message="Failed to start channel",
            detail=str(e),
        )
    logger.info(f"Started channel '{channel_id}'")

    status = await channel_gateway.get_channel_status(channel_id)
    return ChannelStatusResponse(
        channel_id=channel_id,
        status=status.get("status", "active"),
        uptime_seconds=status.get("uptime_seconds"),
        messages_processed=status.get("messages_processed", 0),
        active_sessions=status.get("active_sessions", 0),
        error_message=status.get("error_message"),
    )


@router.post("/{channel_id}/stop")
async def stop_channel(channel_id: str):
    """Stop a running channel."""
    channel = await db.channels.get(channel_id)
    if not channel:
        raise NotFoundException(
            detail=f"Channel with ID '{channel_id}' not found"
        )

    try:
        await channel_gateway.stop_channel(channel_id)
    except ValueError as e:
        raise ValidationException(
            message="Failed to stop channel",
            detail=str(e),
        )
    logger.info(f"Stopped channel '{channel_id}'")

    return {"channel_id": channel_id, "status": "inactive"}


@router.post("/{channel_id}/restart")
async def restart_channel(channel_id: str):
    """Restart a channel (stop then start)."""
    channel = await db.channels.get(channel_id)
    if not channel:
        raise NotFoundException(
            detail=f"Channel with ID '{channel_id}' not found"
        )

    try:
        await channel_gateway.restart_channel(channel_id)
    except ValueError as e:
        raise ValidationException(
            message="Failed to restart channel",
            detail=str(e),
        )
    logger.info(f"Restarted channel '{channel_id}'")

    status = await channel_gateway.get_channel_status(channel_id)
    return ChannelStatusResponse(
        channel_id=channel_id,
        status=status.get("status", "active"),
        uptime_seconds=status.get("uptime_seconds"),
        messages_processed=status.get("messages_processed", 0),
        active_sessions=status.get("active_sessions", 0),
        error_message=status.get("error_message"),
    )


@router.get("/{channel_id}/status", response_model=ChannelStatusResponse)
async def get_channel_status(channel_id: str):
    """Get the runtime status of a channel."""
    channel = await db.channels.get(channel_id)
    if not channel:
        raise NotFoundException(
            detail=f"Channel with ID '{channel_id}' not found"
        )

    status = await channel_gateway.get_channel_status(channel_id)
    return ChannelStatusResponse(
        channel_id=channel_id,
        status=status.get("status", "inactive"),
        uptime_seconds=status.get("uptime_seconds"),
        messages_processed=status.get("messages_processed", 0),
        active_sessions=status.get("active_sessions", 0),
        error_message=status.get("error_message"),
    )


@router.post("/{channel_id}/test")
async def test_channel(channel_id: str):
    """Test a channel's configuration without starting it.

    Instantiates the adapter and calls validate_config() to check
    that credentials and settings are valid.
    """
    channel = await db.channels.get(channel_id)
    if not channel:
        raise NotFoundException(
            detail=f"Channel with ID '{channel_id}' not found"
        )

    channel_type = channel["channel_type"]
    adapter_class = get_adapter_class(channel_type)
    if not adapter_class:
        raise ValidationException(
            message="Unsupported channel type",
            detail=f"No adapter available for channel type '{channel_type}'. "
                   f"The required dependencies may not be installed."
        )

    # Parse config if it's a JSON string
    config = channel.get("config", {})
    if isinstance(config, str):
        try:
            config = json.loads(config)
        except (json.JSONDecodeError, TypeError):
            config = {}

    # Instantiate adapter with a no-op callback (we're only validating)
    async def _noop_callback(_msg):
        pass

    adapter = adapter_class(
        channel_id=channel_id,
        config=config,
        on_message=_noop_callback,
    )

    is_valid, error_message = await adapter.validate_config()
    return {
        "channel_id": channel_id,
        "channel_type": channel_type,
        "valid": is_valid,
        "error": error_message,
    }


# ============== Session Endpoints ==============

@router.get("/{channel_id}/sessions", response_model=list[ChannelSessionResponse])
async def list_channel_sessions(channel_id: str):
    """List all sessions for a channel."""
    channel = await db.channels.get(channel_id)
    if not channel:
        raise NotFoundException(
            detail=f"Channel with ID '{channel_id}' not found"
        )

    sessions = await db.channel_sessions.list_by_channel(channel_id)
    return [
        ChannelSessionResponse(
            id=s["id"],
            channel_id=s["channel_id"],
            external_chat_id=s["external_chat_id"],
            external_sender_id=s.get("external_sender_id"),
            external_thread_id=s.get("external_thread_id"),
            session_id=s["session_id"],
            sender_display_name=s.get("sender_display_name"),
            message_count=int(s.get("message_count", 0)),
            last_message_at=s.get("last_message_at"),
            created_at=s["created_at"],
        )
        for s in sessions
    ]
