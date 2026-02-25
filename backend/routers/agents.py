"""Agent CRUD API endpoints."""
import logging
from pathlib import Path
from fastapi import APIRouter
from pydantic import BaseModel
from schemas.agent import AgentCreateRequest, AgentUpdateRequest, AgentResponse
from database import db
from config import ANTHROPIC_TO_BEDROCK_MODEL_MAP
from core.exceptions import (
    AgentNotFoundException,
    ValidationException,
)
from core.workspace_manager import workspace_manager
from core.task_manager import task_manager
from core.agent_manager import expand_skill_ids_with_plugins


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
    """List all agents."""
    agents = await db.agents.list()
    # Filter out the default agent from the list
    return [a for a in agents if a.get("id") != "default"]


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

    if global_user_mode:
        # Global User Mode: use home directory
        working_dir = str(Path.home())
    else:
        # Isolated Mode: use per-agent workspace
        working_dir = str(workspace_manager.get_agent_workspace(agent_id))

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
    skill_ids = request.skill_ids

    if global_user_mode:
        allow_all_skills = True
        skill_ids = []  # Clear skill_ids since all skills are allowed
        logger.info("Global User Mode enabled - setting allow_all_skills=True, clearing skill_ids")

    agent_data = {
        "name": request.name,
        "description": request.description,
        "model": request.model,
        "permission_mode": request.permission_mode,
        "max_turns": request.max_turns,
        "system_prompt": request.system_prompt,
        "allowed_tools": request.allowed_tools,
        "plugin_ids": request.plugin_ids,
        "skill_ids": skill_ids,
        "allow_all_skills": allow_all_skills,
        "mcp_ids": request.mcp_ids,
        "working_directory": None,  # Use default from settings.agent_workspace_dir
        "enable_bash_tool": request.enable_bash_tool,
        "enable_file_tools": request.enable_file_tools,
        "enable_web_tools": request.enable_web_tools,
        "enable_tool_logging": True,
        "enable_safety_checks": True,
        "enable_file_access_control": request.enable_file_access_control,
        "allowed_directories": request.allowed_directories,
        "global_user_mode": global_user_mode,
        "enable_human_approval": request.enable_human_approval,
        "status": "active",
    }
    agent = await db.agents.put(agent_data)

    # Build per-agent workspace with symlinks to allowed skills
    try:
        effective_skill_ids = await expand_skill_ids_with_plugins(
            skill_ids, request.plugin_ids, allow_all_skills
        )
        await workspace_manager.rebuild_agent_workspace(
            agent_id=agent["id"],
            skill_ids=effective_skill_ids,
            allow_all_skills=allow_all_skills
        )
        logger.info(f"Created workspace for agent {agent['id']}")
    except Exception as e:
        logger.error(f"Failed to create workspace for agent {agent['id']}: {e}")
        # Don't fail agent creation if workspace creation fails

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

    updates = request.model_dump(exclude_unset=True)

    # Global User Mode requires allow_all_skills=True (skill restrictions not supported)
    # Check if global_user_mode is being set or was already set
    global_user_mode = updates.get("global_user_mode", existing.get("global_user_mode", False))

    if global_user_mode:
        updates["allow_all_skills"] = True
        updates["skill_ids"] = []  # Clear skill_ids since all skills are allowed
        logger.info(f"Global User Mode enabled for agent {agent_id} - setting allow_all_skills=True, clearing skill_ids")

    agent = await db.agents.update(agent_id, updates)

    # Check if skill_ids, allow_all_skills, or plugin_ids changed - if so, rebuild workspace
    skill_ids_changed = "skill_ids" in updates
    allow_all_changed = "allow_all_skills" in updates
    plugin_ids_changed = "plugin_ids" in updates

    if skill_ids_changed or allow_all_changed or plugin_ids_changed:
        try:
            skill_ids = agent.get("skill_ids", [])
            allow_all_skills = agent.get("allow_all_skills", False)
            plugin_ids = agent.get("plugin_ids", [])

            effective_skill_ids = await expand_skill_ids_with_plugins(
                skill_ids, plugin_ids, allow_all_skills
            )
            await workspace_manager.rebuild_agent_workspace(
                agent_id=agent_id,
                skill_ids=effective_skill_ids,
                allow_all_skills=allow_all_skills
            )
            logger.info(f"Rebuilt workspace for agent {agent_id} after skill config change")
        except Exception as e:
            logger.error(f"Failed to rebuild workspace for agent {agent_id}: {e}")
            # Don't fail agent update if workspace rebuild fails

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

    # Clean up agent workspace
    try:
        await workspace_manager.delete_agent_workspace(agent_id)
        logger.info(f"Deleted workspace for agent {agent_id}")
    except Exception as e:
        logger.error(f"Failed to delete workspace for agent {agent_id}: {e}")
        # Don't fail agent deletion if workspace cleanup fails
