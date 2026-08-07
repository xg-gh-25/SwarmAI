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
import os
import re
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from core import session_registry  # Skill creator uses SessionRouter
from core.initialization_manager import initialization_manager
from core.projection_layer import ProjectionLayer
from core.skill_manager import SkillInfo, skill_manager
from core.skill_registry import derive_category, derive_visibility
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
    """Convert a ``SkillInfo`` to the API ``SkillResponse`` model.

    Category + visibility are derived purely from the folder name (with an
    optional frontmatter override carried on ``SkillInfo``) — no session
    context needed (run_b5d98151).
    """
    return SkillResponse(
        folder_name=info.folder_name,
        name=info.name,
        description=info.description,
        version=info.version,
        source_tier=info.source_tier,
        read_only=info.source_tier != "user",
        content=info.content if include_content else None,
        category=derive_category(info.folder_name, info.category),
        visibility=derive_visibility(info.folder_name, info.visibility),
        tier=info.tier,
    )


# Run modes where the caller is the LOCAL-DESKTOP OWNER (internal skills are
# served). Any other mode (hive / unknown) fails closed to public-only — a
# non-owner surface never receives an internal skill name (Gate-1 adopted,
# backend-primary; the route is unauthenticated + context-free so we key on
# the process run mode, the existing desktop-vs-hive discriminator, NOT a
# spoofable request header). See ddd_brain.py:27 (no per-router auth) +
# hive/Caddyfile (Hive proxies as loopback, so client-IP is not a safe signal).
_OWNER_RUN_MODES = frozenset({"daemon", "subprocess", "dev"})


def _is_owner_session() -> bool:
    """True only when this process is a local-desktop owner runtime.

    Fail-closed: an unset/unknown SWARMAI_MODE is treated as NON-owner.
    """
    return os.environ.get("SWARMAI_MODE", "") in _OWNER_RUN_MODES


def _reject_internal_folder_name(folder_name: str) -> None:
    """Refuse to CREATE a skill whose name derives internal visibility.

    Two things at once (Gate-2 security): (1) a user-tier skill named with an
    internal prefix (s_cmhk-/s_ivt-/s_internal-/meddpicc) would derive
    visibility=internal and become invisible to the very user who made it; (2) a
    UNIFORM rejection here — BEFORE any existence check — closes the enumeration
    leak (an internal name returns the SAME 400 whether or not it already exists,
    so the create/generate 409 can't be used to probe which internal skills
    exist). Built-in internal skills ship on disk, never via this API, so this
    never blocks a legitimate create.
    """
    if derive_visibility(folder_name) == "internal":
        raise HTTPException(
            status_code=400,
            detail="Skill name uses a reserved (internal) prefix and cannot be created.",
        )


def _visible_to_caller(response: SkillResponse) -> bool:
    """A single skill is visible iff it is public OR the caller is the owner.

    The internal-skill gate. Applied at EVERY read surface that returns a
    SkillResponse (list, rescan, single-detail) — not just the list. LIST and
    DETAIL are independent leak surfaces (the eval denylist-twice / C041 lesson):
    filtering only the list leaves the by-name detail endpoint as an open bypass.
    """
    return response.visibility != "internal" or _is_owner_session()


def _load_skill_health_stats() -> list:
    """Read per-skill stats from the production metrics DB (READ-ONLY).

    Isolated into its own function so the health endpoint can be tested fail-safe
    (the test monkeypatches this to raise, asserting the endpoint still 200s). Opens
    a short-lived ``SkillMetricsStore`` on the app data.db and closes it. Never
    mutates the HIGH-risk store queries — calls ``get_all_stats()`` only.
    """
    from config import get_app_data_dir
    from core.skill_metrics import SkillMetricsStore

    store = SkillMetricsStore(get_app_data_dir() / "data.db")
    try:
        return store.get_all_stats()
    finally:
        store.close()


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
    # Backend-primary internal filter (Gate-1 adopted, fail-closed): a non-owner
    # runtime (hive / unknown SWARMAI_MODE) never receives an internal skill.
    # The frontend then CONSUMES this verdict (groups + renders), never
    # re-derives it — the established backend-owns-the-discriminator pattern.
    responses = [r for r in responses if _visible_to_caller(r)]
    responses.sort(key=lambda r: r.folder_name)
    return responses


@router.post("", response_model=SkillResponse, status_code=201)
async def create_skill(request: SkillCreateRequest):
    """Create a new user skill in ``~/.swarm-ai/skills/``.

    Requirements: 5.3, 5.7
    """
    # Reject internal-prefix names BEFORE the collision check (uniform 400 closes
    # the 409-enumeration leak + prevents a self-invisible user skill).
    _reject_internal_folder_name(request.folder_name)
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
    # Same internal-skill gate as list_skills — rescan is a parallel read surface.
    responses = [r for r in responses if _visible_to_caller(r)]
    responses.sort(key=lambda r: r.folder_name)
    return responses


@router.get("/health")
async def skills_health() -> dict[str, dict]:
    """Per-skill qualitative health status for the Capabilities panel (run_a85e6641).

    Returns ``{folder_name: {status, success_rate, last_used, invocation_count}}`` where
    status is one of healthy / low_success / never_used / stale. The panel LAZY-fetches this
    after the fast /api/skills list and renders a status dot per row (raw counts stay off the
    row — R30#4; success_rate/last_used are for the detail drawer). ``invocation_count`` is
    the raw frequency the panel uses to ORDER cards (Most-Used strip + within-group sort),
    never shown as a number (R30#4).

    Three load-bearing properties:
    - FAIL-SAFE (AC7 / Gate-1): any error reading the metrics DB → an EMPTY map + 200,
      NEVER a 500. A missing/locked metrics DB must not break the panel — the dots just
      don't light up.
    - VISIBILITY (Gate-1 BLOCK-3): the map is folded over the SAME _visible_to_caller
      skill list that GET /skills returns, so a non-owner (hive) never receives an
      internal skill NAME as a map key (map keys = a subset of the visible list).
    - NO-DATA vs NEVER-USED (meta-review MED): if the metrics table is entirely empty
      (fresh install, or a hive/EC2 DB where metrics are recorded only on the desktop),
      return {} so the panel renders NO dots — rather than a wall of grey `never_used`
      dots that misread as "everything is broken/unused". never_used is meaningful only
      when SOME skills have data to contrast against.
    """
    from core.skill_health import build_health_map

    try:
        all_stats = _load_skill_health_stats()
        if not all_stats:
            return {}  # no metrics at all → no dots, not a grey wall
        cache = await skill_manager.get_cache()
        visible_names = [
            info.folder_name
            for info in cache.values()
            if _visible_to_caller(_skill_info_to_response(info, include_content=False))
        ]
        return build_health_map(all_stats, visible_names)
    except Exception as e:  # noqa: BLE001 — fail-safe: never 500 the panel
        logger.warning("skills_health failed (non-blocking, returning empty): %s", e)
        return {}


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

        # Reject internal-prefix names BEFORE the conflict check — a uniform 400
        # (same whether or not the skill exists) closes the 409-enumeration leak.
        _reject_internal_folder_name(sanitized_name)

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
                async for event in session_registry.run_skill_creator(
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
    response = _skill_info_to_response(info, include_content=True)
    # LEAK GUARD (detail is a SEPARATE surface from list): a non-owner must NOT
    # fetch an internal skill's full content by name. 404 (not 403) so we don't
    # even confirm the internal skill exists.
    if not _visible_to_caller(response):
        raise HTTPException(
            status_code=404,
            detail=f"Skill '{folder_name}' not found",
        )
    return response


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
