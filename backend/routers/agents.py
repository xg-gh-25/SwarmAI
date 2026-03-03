"""Agent CRUD API endpoints."""
import logging
from fastapi import APIRouter
from pydantic import BaseModel
from schemas.agent import AgentCreateRequest, AgentUpdateRequest, AgentResponse
from database import db
from config import ANTHROPIC_TO_BEDROCK_MODEL_MAP
from core.exceptions import (
    AgentNotFoundException,
    ValidationException,
)
from core.task_manager import task_manager
from core.agent_manager import SWARM_AGENT_NAME


class WorkingDirectoryResponse(BaseModel):
    """Response model for agent working directory."""
    path: str
    is_global_mode: bool

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/models", response_model=list[str])
async def list_available_models():
    """List all available Claude models.

    Returns the Anthropic model IDs that have Bedrock mappings configured.
    """
    return list(ANTHROPIC_TO_BEDROCK_MODEL_MAP.keys())


@router.get("", response_model=list[AgentResponse])
async def list_agents():
    """List all agents including the default agent."""
    agents = await db.agents.list()
    return agents


@router.get("/default", response_model=AgentResponse)
async def get_default_agent():
    """Get the default system agent."""
    agent = await db.agents.get("default")
    if not agent:
        raise AgentNotFoundException(
            detail="Default agent configuration is missing",
            suggested_action="Contact the administrator to set up the default agent"
        )
    return agent


@router.get("/{agent_id}", response_model=AgentResponse)
async def get_agent(agent_id: str):
    """Get a specific agent by ID."""
    agent = await db.agents.get(agent_id)
    if not agent:
        raise AgentNotFoundException(
            detail=f"Agent with ID '{agent_id}' does not exist",
            suggested_action="Please check the agent ID and try again"
        )
    return agent


@router.get("/{agent_id}/working-directory", response_model=WorkingDirectoryResponse)
async def get_agent_working_directory(agent_id: str):
    """Get the effective working directory for an agent.

    Returns the agent's working directory based on its configuration:
    - Global User Mode: Returns home directory (~/)
    - Isolated Mode: Returns the per-agent workspace directory

    Note: If a session-level workDir is set (from "work in a folder"),
    that should override this on the frontend.
    """
    agent = await db.agents.get(agent_id)
    if not agent:
        raise AgentNotFoundException(
            detail=f"Agent with ID '{agent_id}' does not exist",
            suggested_action="Please check the agent ID and try again"
        )

    global_user_mode = agent.get("global_user_mode", True)  # Default to True now

    # All agents use the single SwarmWorkspace path
    from core.initialization_manager import initialization_manager
    working_dir = initialization_manager.get_cached_workspace_path()

    return WorkingDirectoryResponse(
        path=working_dir,
        is_global_mode=global_user_mode
    )


@router.post("", response_model=AgentResponse, status_code=201)
async def create_agent(request: AgentCreateRequest):
    """Create a new agent."""
    # Global User Mode requires allow_all_skills=True (skill restrictions not supported)
    global_user_mode = request.global_user_mode
    allow_all_skills = request.allow_all_skills
    allowed_skills = request.allowed_skills

    if global_user_mode:
        allow_all_skills = True
        allowed_skills = []  # Clear allowed_skills since all skills are allowed
        logger.info("Global User Mode enabled - setting allow_all_skills=True, clearing allowed_skills")

    agent_data = {
        "name": request.name,
        "description": request.description,
        "model": request.model,
        "permission_mode": request.permission_mode,
        "max_turns": request.max_turns,
        "system_prompt": request.system_prompt,
        "allowed_tools": request.allowed_tools,
        "plugin_ids": request.plugin_ids,
        "allowed_skills": allowed_skills,
        "allow_all_skills": allow_all_skills,
        "mcp_ids": request.mcp_ids,
        "working_directory": None,  # Uses cached SwarmWorkspace path via initialization_manager
        "enable_bash_tool": request.enable_bash_tool,
        "enable_file_tools": request.enable_file_tools,
        "enable_web_tools": request.enable_web_tools,
        "enable_tool_logging": True,
        "enable_safety_checks": True,
        "enable_file_access_control": request.enable_file_access_control,
        "allowed_directories": request.allowed_directories,
        "global_user_mode": global_user_mode,
        "enable_human_approval": request.enable_human_approval,
        "sandbox_enabled": request.sandbox_enabled,
        "status": "active",
    }
    agent = await db.agents.put(agent_data)

    return agent


@router.put("/{agent_id}", response_model=AgentResponse)
async def update_agent(agent_id: str, request: AgentUpdateRequest):
    """Update an existing agent."""
    existing = await db.agents.get(agent_id)
    if not existing:
        raise AgentNotFoundException(
            detail=f"Agent with ID '{agent_id}' does not exist",
            suggested_action="Please check the agent ID and try again"
        )

    # Protect system agent name from being changed
    if existing.get("is_system_agent"):
        if request.name is not None and request.name != SWARM_AGENT_NAME:
            raise ValidationException(
                message="Cannot change the name of the system agent",
                detail="The SwarmAgent name is protected and cannot be modified",
                suggested_action="If you need a custom agent, create a new one instead"
            )

    # Protect system resources from being unbound from SwarmAgent
    if existing.get("is_system_agent"):
        # Get all built-in (system) skills from SkillManager
        from core.skill_manager import skill_manager
        cache = await skill_manager.get_cache()
        builtin_skill_folders = {folder for folder, info in cache.items() if info.source_tier == "built-in"}
        
        # Check if allowed_skills update would remove built-in skills
        if request.allowed_skills is not None:
            new_skill_folders = set(request.allowed_skills)
            if not builtin_skill_folders.issubset(new_skill_folders):
                raise ValidationException(
                    message="Cannot unbind system skills from SwarmAgent",
                    detail="Built-in skills are permanently bound to the system agent",
                    suggested_action="You can add your own skills, but built-in skills cannot be removed"
                )
        
        # Get all system MCPs
        system_mcps = await db.mcp_servers.list_by_system()
        system_mcp_ids = {m["id"] for m in system_mcps}

        # Check if mcp_ids update would remove system MCPs
        if request.mcp_ids is not None:
            new_mcp_ids = set(request.mcp_ids)
            if not system_mcp_ids.issubset(new_mcp_ids):
                raise ValidationException(
                    message="Cannot unbind system MCP servers from SwarmAgent",
                    detail="System MCP servers are permanently bound to the system agent",
                    suggested_action="You can add your own MCP servers, but system MCPs cannot be removed"
                )

    updates = request.model_dump(exclude_unset=True)

    # Global User Mode requires allow_all_skills=True (skill restrictions not supported)
    # Check if global_user_mode is being set or was already set
    global_user_mode = updates.get("global_user_mode", existing.get("global_user_mode", False))

    if global_user_mode:
        updates["allow_all_skills"] = True
        updates["allowed_skills"] = []  # Clear allowed_skills since all skills are allowed
        logger.info(f"Global User Mode enabled for agent {agent_id} - setting allow_all_skills=True, clearing allowed_skills")

    agent = await db.agents.update(agent_id, updates)

    return agent


@router.delete("/{agent_id}", status_code=204)
async def delete_agent(agent_id: str):
    """Delete an agent and all associated tasks (cascade delete)."""
    if agent_id == "default":
        raise ValidationException(
            message="Cannot delete the default agent",
            detail="The default agent is a system resource and cannot be deleted",
            suggested_action="If you need to modify the default agent, use the update endpoint instead"
        )

    # Check if agent exists and if it's a system agent
    agent = await db.agents.get(agent_id)
    if agent and agent.get("is_system_agent"):
        raise ValidationException(
            message="Cannot delete the system agent",
            detail="The system agent (SwarmAgent) is a protected resource and cannot be deleted",
            suggested_action="If you need to modify the system agent, use the update endpoint instead"
        )

    deleted = await db.agents.delete(agent_id)
    if not deleted:
        raise AgentNotFoundException(
            detail=f"Agent with ID '{agent_id}' does not exist",
            suggested_action="Please check the agent ID and try again"
        )

    # Clean up associated tasks (cascade delete behavior)
    try:
        # First cancel any running tasks for this agent
        tasks = await db.tasks.list_by_agent_id(agent_id)
        for task in tasks:
            if task.get("status") == "running":
                await task_manager.cancel_task(task["id"])
        # Then delete all task records
        deleted_count = await db.tasks.delete_by_agent_id(agent_id)
        if deleted_count > 0:
            logger.info(f"Deleted {deleted_count} tasks for agent {agent_id}")
    except Exception as e:
        logger.error(f"Failed to delete tasks for agent {agent_id}: {e}")
        # Don't fail agent deletion if task cleanup fails
