"""TSCC API router for thread cognitive context and system prompt viewer.

This module provides the ``tscc_router`` mounted at ``/api`` with endpoints
for retrieving live TSCC state and system prompt metadata.

Key endpoints:

- ``GET  /api/chat_threads/{thread_id}/tscc``          — current state
- ``GET  /api/chat/{session_id}/system-prompt``        — system prompt metadata

All responses use snake_case field names per backend convention.

Requirements: 6.1, 6.2, 6.7
"""

import logging

from fastapi import APIRouter, HTTPException

from schemas.tscc import SystemPromptMetadata, TSCCState

logger = logging.getLogger(__name__)

tscc_router = APIRouter()


# Late-bound references — set by register_tscc_dependencies() at app startup
_state_manager = None


def register_tscc_dependencies(state_manager) -> None:
    """Wire up the TSCC state manager at app startup."""
    global _state_manager
    _state_manager = state_manager


@tscc_router.get(
    "/chat_threads/{thread_id}/tscc",
    response_model=TSCCState,
)
async def get_tscc_state(thread_id: str):
    """Return the current TSCC state for a thread.

    Returns 404 if no state exists for the given thread_id.
    """
    state = await _state_manager.get_state(thread_id)
    if state is None:
        raise HTTPException(status_code=404, detail="Thread not found")
    return state


@tscc_router.get(
    "/chat/{session_id}/system-prompt",
    response_model=SystemPromptMetadata,
)
async def get_system_prompt(session_id: str):
    """Return the assembled system prompt metadata for a session.

    Returns the list of context files loaded, their token counts,
    truncation status, and the full assembled prompt text.

    Returns 404 if no metadata exists for the given session_id.
    """
    from core.agent_manager import _system_prompt_metadata

    metadata = _system_prompt_metadata.get(session_id)
    if metadata is None:
        raise HTTPException(status_code=404, detail="No system prompt metadata for session")
    return SystemPromptMetadata(**metadata)
