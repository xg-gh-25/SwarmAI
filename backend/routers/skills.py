"""Filesystem-based Skills API endpoints.

This module was rewritten to replace all database-backed skill operations
with pure filesystem operations via ``SkillManager`` and ``ProjectionLayer``.
No SQLAlchemy or database imports remain.

Key endpoints:

- ``GET  /skills``                    — List all skills (cached, no content)
- ``POST /skills``                    — Create a user skill
- ``POST /skills/rescan``             — Invalidate cache, return fresh list
- ``POST /skills/generate-with-agent``— AI skill generation (streaming SSE)
- ``GET  /skills/{folder_name}``      — Get single skill with content
- ``PUT  /skills/{folder_name}``      — Update a user skill
- ``DELETE /skills/{folder_name}``    — Delete a user skill

Route ordering is critical: fixed-path routes are registered before
the parameterised ``/{folder_name}`` routes so FastAPI does not match
``rescan`` or ``generate-with-agent`` as folder names.

Requirements: 5.1–5.12
"""

import asyncio
import json
import logging
import re
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from core.agent_manager import agent_manager  # Skill creator uses AgentManager directly (separate flow from SessionRouter)
from core.initialization_manager import initialization_manager
from core.projection_layer import ProjectionLayer
from core.skill_manager import SkillInfo, skill_manager
from schemas.skill import (
    SkillCreateRequest,
    SkillResponse,
    SkillUpdateRequest,
)

logger = logging.getLogger(__name__)

router = APIRouter()

# ---------------------------------------------------------------------------
# Module-level singletons
# ---------------------------------------------------------------------------
# ProjectionLayer wraps the global skill_manager singleton.  It is created
# once at import time and reused by every endpoint that needs re-projection.
projection_layer = ProjectionLayer(skill_manager)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _skill_info_to_response(
    info: SkillInfo,
    include_content: bool = False,
) -> SkillResponse:
    """Convert a ``SkillInfo`` to the API ``SkillResponse`` model."""
    return SkillResponse(
        folder_name=info.folder_name,
        name=info.name,
        description=info.description,
        version=info.version,
        source_tier=info.source_tier,
        read_only=info.source_tier != "user",
        content=info.content if include_content else None,
    )


async def _trigger_projection() -> None:
    """Best-effort re-projection after CRUD."""
    try:
        workspace_path = initialization_manager.get_cached_workspace_path()
        await projection_layer.project_skills(
            Path(workspace_path), allow_all=True,
        )
    except Exception as e:
        logger.error("Failed to re-project skills: %s", e)


# ===================================================================
# FIXED-PATH ROUTES — registered BEFORE /{folder_name} routes
# ===================================================================


@router.get("", response_model=list[SkillResponse])
async def list_skills():
    """Return all skills from cache, sorted by folder_name, without content.

    Requirements: 5.1, 5.11, 5.12
    """
    cache = await skill_manager.get_cache()
    responses = [
        _skill_info_to_response(info, include_content=False)
        for info in cache.values()
    ]
    responses.sort(key=lambda r: r.folder_name)
    return responses


@router.post("", response_model=SkillResponse, status_code=201)
async def create_skill(request: SkillCreateRequest):
    """Create a new user skill in ``~/.swarm-ai/skills/``.

    Requirements: 5.3, 5.7
    """
    info = await skill_manager.create_skill(
        folder_name=request.folder_name,
        name=request.name,
        description=request.description,
        content=request.content,
    )
    await _trigger_projection()
    return _skill_info_to_response(info, include_content=True)


@router.post("/rescan", response_model=list[SkillResponse])
async def rescan_skills():
    """Invalidate the in-memory cache and return a freshly scanned list.

    Requirements: 5.9
    """
    skill_manager.invalidate_cache()
    cache = await skill_manager.get_cache()
    await _trigger_projection()
    responses = [
        _skill_info_to_response(info, include_content=False)
        for info in cache.values()
    ]
    responses.sort(key=lambda r: r.folder_name)
    return responses


@router.post("/generate-with-agent")
async def generate_skill_with_agent(request: Request):
    """Generate a skill using an AI agent with streaming SSE response.

    The agent creates files in ``~/.swarm-ai/skills/{skill_name}/``.
    After generation completes the cache is invalidated and projection
    is triggered — no separate ``finalize`` call is needed.

    Requirements: 10.1, 10.2, 10.3, 10.4
    """
    try:
        body = await request.json()
        skill_name = body.get("skill_name")
        skill_description = body.get("skill_description")
        session_id = body.get("session_id")
        message = body.get("message")
        model = body.get("model")

        if not skill_name:
            raise HTTPException(
                status_code=422,
                detail="skill_name is required",
            )

        if not skill_description and not message:
            raise HTTPException(
                status_code=422,
                detail=(
                    "Either skill_description (for initial creation) "
                    "or message (for follow-up) is required"
                ),
            )

        # Sanitize skill name for use as folder name
        sanitized_name = re.sub(r"[^a-zA-Z0-9_-]", "-", skill_name.lower())

        # Check for name conflict — 409 if target directory already exists
        existing = await skill_manager.get_skill(sanitized_name)
        if existing and not session_id:
            raise HTTPException(
                status_code=409,
                detail=f"Skill '{sanitized_name}' already exists",
            )

        logger.info(
            "Starting skill generation with agent: %s, model: %s",
            sanitized_name,
            model or "default",
        )

        async def event_generator():
            """Yield SSE events from the agent conversation."""
            try:
                async for event in agent_manager.run_skill_creator_conversation(
                    skill_name=sanitized_name,
                    skill_description=skill_description or "",
                    user_message=message,
                    session_id=session_id,
                    model=model,
                ):
                    yield f"data: {json.dumps(event)}\n\n"

                # Generation finished — invalidate cache & project
                skill_manager.invalidate_cache()
                await _trigger_projection()

            except asyncio.CancelledError:
                logger.info("Client disconnected from skill generation stream")
                raise
            except Exception as e:
                logger.error("Error in skill generation stream: %s", e)
                yield f"data: {json.dumps({'type': 'error', 'error': str(e)})}\n\n"

        return StreamingResponse(
            event_generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to start skill generation: %s", e)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to start skill generation: {e}",
        )


# ===================================================================
# PARAMETERISED ROUTES — registered AFTER fixed-path routes
# ===================================================================


@router.get("/{folder_name}", response_model=SkillResponse)
async def get_skill(folder_name: str):
    """Return a single skill by folder name, with content loaded from disk.

    Requirements: 5.2
    """
    info = await skill_manager.get_skill(folder_name)
    if info is None:
        raise HTTPException(
            status_code=404,
            detail=f"Skill '{folder_name}' not found",
        )
    return _skill_info_to_response(info, include_content=True)


@router.put("/{folder_name}", response_model=SkillResponse)
async def update_skill(folder_name: str, request: SkillUpdateRequest):
    """Update an existing user skill's SKILL.md.

    Returns 403 for built-in or plugin skills.

    Requirements: 5.4, 5.6, 5.7
    """
    info = await skill_manager.update_skill(
        folder_name=folder_name,
        name=request.name,
        description=request.description,
        content=request.content,
    )
    await _trigger_projection()
    return _skill_info_to_response(info, include_content=True)


@router.delete("/{folder_name}", status_code=204)
async def delete_skill(folder_name: str):
    """Delete a user skill directory.

    Returns 403 for built-in or plugin skills.

    Requirements: 5.5, 5.6, 5.7
    """
    await skill_manager.delete_skill(folder_name)
    await _trigger_projection()
