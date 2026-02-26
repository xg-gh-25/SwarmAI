"""Context assembly preview API router.

This module provides the ``GET /api/projects/{project_id}/context``
endpoint that returns the assembled context layers for a project,
enabling the "Visible Planning Builds Trust" design principle — users
can inspect exactly what context an agent would see.

The endpoint supports:

- Optional ``thread_id`` query param for Layer 2 live work context
- Configurable ``token_budget`` and ``preview_limit``
- ``since_version`` query param for version-based polling
- ETag-based caching via ``If-None-Match`` / ``ETag`` headers
- 304 Not Modified when context is unchanged

All ``source_path`` values in the response are workspace-relative
(never absolute filesystem paths).

Key public symbols:

- ``router``                  — FastAPI ``APIRouter`` instance
- ``get_project_context``     — Handler for the context preview endpoint

Validates: Requirements 33.1, 33.2, 33.4, 33.7, 36.1, 36.2, 36.3
"""

import logging
from typing import Optional

from fastapi import APIRouter, Header, HTTPException, Query
from fastapi.responses import JSONResponse, Response

from core.context_assembler import ContextAssembler
from core.context_snapshot_cache import context_cache
from core.swarm_workspace_manager import swarm_workspace_manager
from database import db
from schemas.context import ContextLayerResponse, ContextPreviewResponse

logger = logging.getLogger(__name__)

router = APIRouter(tags=["context"])


async def _get_workspace_path() -> Optional[str]:
    """Resolve the active workspace path from the database config.

    Returns:
        Expanded absolute path to the workspace root, or ``None`` if
        no workspace config exists.
    """
    config = await db.workspace_config.get_config()
    if config is None or "file_path" not in config:
        return None
    return swarm_workspace_manager.expand_path(config["file_path"])


async def _validate_project_exists(project_id: str) -> None:
    """Validate that a project with the given ID exists.

    Raises:
        HTTPException: 404 if the project is not found.
    """
    workspace_path = await _get_workspace_path()
    if workspace_path is None:
        raise HTTPException(status_code=404, detail="Project not found")
    try:
        await swarm_workspace_manager.get_project(project_id, workspace_path)
    except ValueError:
        raise HTTPException(status_code=404, detail="Project not found")


@router.get("/projects/{project_id}/context", response_model=ContextPreviewResponse)
async def get_project_context(
    project_id: str,
    thread_id: Optional[str] = Query(None, description="Chat thread ID for Layer 2 live context"),
    token_budget: int = Query(10000, ge=1, description="Maximum token budget"),
    preview_limit: int = Query(500, ge=0, description="Max chars per layer content preview"),
    since_version: Optional[str] = Query(None, description="Version hash for polling"),
    if_none_match: Optional[str] = Header(None, alias="If-None-Match"),
) -> Response:
    """Return the assembled context preview for a project.

    Assembles context layers via ``ContextSnapshotCache`` and maps the
    result to a ``ContextPreviewResponse``.  Each layer's content is
    truncated to ``preview_limit`` characters for the ``content_preview``
    field.

    Supports ETag-based caching:

    - Returns an ``ETag`` header derived from the context version hash.
    - If the client sends ``If-None-Match`` matching the current ETag,
      returns 304 Not Modified without re-assembling context.

    All ``source_path`` values are workspace-relative (PE Fix #8).

    Validates: Requirements 33.1, 33.2, 33.4, 33.7, 36.1, 36.2, 36.3
    """
    # Validate project exists
    await _validate_project_exists(project_id)

    # Resolve workspace path
    workspace_path = await _get_workspace_path()
    if workspace_path is None:
        # Workspace not configured — return 200 with empty layers
        response = ContextPreviewResponse(
            project_id=project_id,
            thread_id=thread_id,
            layers=[],
            total_token_count=0,
            budget_exceeded=False,
            token_budget=token_budget,
            truncation_summary="",
            etag="",
        )
        return JSONResponse(content=response.model_dump())

    # Assemble context via cache
    assembler = ContextAssembler(
        workspace_path=workspace_path,
        token_budget=token_budget,
    )

    assembled = await context_cache.get_or_assemble(
        assembler=assembler,
        project_id=project_id,
        thread_id=thread_id,
        token_budget=token_budget,
    )

    # Compute ETag from version hash
    version_counters = await context_cache._read_version_counters(
        project_id, thread_id
    )
    etag_value = f'"{version_counters.compute_hash()}"'

    # Check If-None-Match for 304
    if if_none_match and if_none_match.strip() == etag_value:
        return Response(status_code=304, headers={"ETag": etag_value})

    # Check since_version for version-based polling
    current_hash = version_counters.compute_hash()
    if since_version and since_version == current_hash:
        return Response(status_code=304, headers={"ETag": etag_value})

    # Map assembled layers to response, truncating content previews
    layer_responses = []
    for layer in assembled.layers:
        content_preview = layer.content[:preview_limit] if layer.content else ""
        layer_responses.append(
            ContextLayerResponse(
                layer_number=layer.layer_number,
                name=layer.name,
                source_path=layer.source_path,
                token_count=layer.token_count,
                content_preview=content_preview,
                truncated=layer.truncated,
                truncation_stage=layer.truncation_stage,
            )
        )

    response = ContextPreviewResponse(
        project_id=project_id,
        thread_id=thread_id,
        layers=layer_responses,
        total_token_count=assembled.total_token_count,
        budget_exceeded=assembled.budget_exceeded,
        token_budget=assembled.token_budget,
        truncation_summary=assembled.truncation_summary,
        etag=current_hash,
    )

    return JSONResponse(
        content=response.model_dump(),
        headers={"ETag": etag_value},
    )
