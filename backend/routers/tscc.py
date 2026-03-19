"""TSCC API router for thread cognitive context and system prompt viewer.

This module provides the ``tscc_router`` mounted at ``/api`` with endpoints
for retrieving live TSCC state and system prompt metadata.

Key endpoints:

- ``GET  /api/chat_threads/{thread_id}/tscc``          — current state
- ``GET  /api/chat/{session_id}/system-prompt``        — system prompt metadata

Both endpoints return a default empty state when no in-memory data exists
(e.g. after backend restart).  This avoids 404 console errors on the
frontend and is semantically correct — "not yet initialized" is a valid
state, not an error.

All responses use snake_case field names per backend convention.

Requirements: 6.1, 6.2, 6.7
"""

import logging
from datetime import datetime, timezone

from fastapi import APIRouter

from schemas.tscc import (
    SystemPromptMetadata,
    TSCCContext,
    TSCCLiveState,
    TSCCState,
)

logger = logging.getLogger(__name__)

tscc_router = APIRouter()


# Late-bound references — set by register_tscc_dependencies() at app startup
_state_manager = None


def register_tscc_dependencies(state_manager) -> None:
    """Wire up the TSCC state manager at app startup."""
    global _state_manager
    _state_manager = state_manager


def _make_default_tscc_state(thread_id: str) -> TSCCState:
    """Build a default TSCC state for a thread with no in-memory data."""
    return TSCCState(
        thread_id=thread_id,
        project_id=None,
        scope_type="workspace",
        last_updated_at=datetime.now(timezone.utc).isoformat(),
        lifecycle_state="new",
        live_state=TSCCLiveState(
            context=TSCCContext(
                scope_label="Workspace: SwarmWS (General)",
                thread_title="",
            ),
        ),
    )


@tscc_router.get(
    "/chat_threads/{thread_id}/tscc",
    response_model=TSCCState,
)
async def get_tscc_state(thread_id: str):
    """Return the current TSCC state for a thread.

    Returns a default empty state if no in-memory state exists (e.g. after
    backend restart).  This is not an error — the state will be populated
    when the next conversation starts on this thread.
    """
    state = await _state_manager.get_state(thread_id)
    if state is None:
        return _make_default_tscc_state(thread_id)
    return state


@tscc_router.get(
    "/chat/{session_id}/system-prompt",
    response_model=SystemPromptMetadata,
)
async def get_system_prompt(session_id: str):
    """Return the assembled system prompt metadata for a session.

    Returns an empty metadata object if no metadata exists for the given
    session_id (e.g. after backend restart).  The metadata will be
    populated when the next conversation starts on this session.
    """
    # NOTE: system_prompt_metadata is populated by PromptBuilder and stored
    # in session_registry. Shows metadata for sessions using the new architecture.
    from core import session_registry

    metadata = session_registry.system_prompt_metadata.get(session_id)
    if metadata is None:
        return SystemPromptMetadata()
    return SystemPromptMetadata(**metadata)
