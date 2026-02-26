"""TSCC API router for thread cognitive context and snapshots.

This module provides the ``tscc_router`` mounted at ``/api`` with endpoints
for retrieving live TSCC state and managing filesystem-based snapshots.

Key endpoints:

- ``GET  /api/chat_threads/{thread_id}/tscc``                    — current state
- ``POST /api/chat_threads/{thread_id}/snapshots``               — create snapshot
- ``GET  /api/chat_threads/{thread_id}/snapshots``               — list snapshots
- ``GET  /api/chat_threads/{thread_id}/snapshots/{snapshot_id}`` — get snapshot

All responses use snake_case field names per backend convention.

Requirements: 15.1, 15.2, 15.3, 15.4, 15.5, 11.4, 11.5
"""

import logging

from fastapi import APIRouter, HTTPException

from schemas.tscc import SnapshotCreateRequest, TSCCSnapshot, TSCCState

logger = logging.getLogger(__name__)

tscc_router = APIRouter()


# Late-bound references — set by register_tscc_dependencies() at app startup
_state_manager = None
_snapshot_manager = None


def register_tscc_dependencies(state_manager, snapshot_manager) -> None:
    """Wire up the TSCC managers at app startup.

    Called from ``main.py`` after the managers are instantiated.
    """
    global _state_manager, _snapshot_manager
    _state_manager = state_manager
    _snapshot_manager = snapshot_manager


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


@tscc_router.post(
    "/chat_threads/{thread_id}/snapshots",
    response_model=TSCCSnapshot,
)
async def create_snapshot(thread_id: str, body: SnapshotCreateRequest):
    """Create a point-in-time snapshot of the thread's TSCC state.

    Returns 404 if no state exists for the thread.
    Returns 409 if a duplicate snapshot (same reason within 30s) was skipped.
    """
    state = await _state_manager.get_state(thread_id)
    if state is None:
        raise HTTPException(status_code=404, detail="Thread not found")

    snapshot = _snapshot_manager.create_snapshot(thread_id, state, body.reason)
    if snapshot is None:
        raise HTTPException(
            status_code=409,
            detail="Duplicate snapshot within dedup window",
        )
    return snapshot


@tscc_router.get(
    "/chat_threads/{thread_id}/snapshots",
    response_model=list[TSCCSnapshot],
)
async def list_snapshots(thread_id: str):
    """Return all snapshots for a thread in chronological order."""
    return _snapshot_manager.list_snapshots(thread_id)


@tscc_router.get(
    "/chat_threads/{thread_id}/snapshots/{snapshot_id}",
    response_model=TSCCSnapshot,
)
async def get_snapshot(thread_id: str, snapshot_id: str):
    """Return a single snapshot by ID.

    Returns 404 if the snapshot is not found.
    """
    snapshot = _snapshot_manager.get_snapshot(thread_id, snapshot_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="Snapshot not found")
    return snapshot
