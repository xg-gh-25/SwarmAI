"""Canvas tabs of one chat session: GET /session/{id} = its Artifacts and Changes
(core/canvas_session); /changes/commit and /changes/diff drill into one commit
(core/repo_changes).

Read-only. Every git call runs on the "turn_changes" pool (core/executors), never on
'subprocess', whose fail-open safety gates must not wait on it. 400 for bad input
(unknown repo, malformed sha or path), 503 when git itself fails.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Path, Query

router = APIRouter(prefix="/api/canvas", tags=["canvas"])


async def _run(fn, *args):
    from core import executors
    from core.repo_changes import RepoChangesError
    try:
        return await executors.run_in("turn_changes", fn, *args)
    except RepoChangesError as exc:
        msg = str(exc)
        bad_input = msg in ("unknown repo", "bad sha", "bad path")
        raise HTTPException(status_code=400 if bad_input else 503, detail=msg)


@router.get("/session/{session_id}")
async def get_session_outputs(session_id: str = Path(..., min_length=1, max_length=128),
                              parts: str = Query("all", pattern="^(all|artifacts)$")):
    """What this session produced: {artifacts, changes} (core/canvas_session).
    ``parts=artifacts`` = the cheap live refresh (changes is null: not computed).
    404 for a session id that does not exist."""
    from core.canvas_session import build
    from database import db
    if not await db.sessions.get(session_id):
        # an unknown id gets no record created for it (build backfills on first read)
        raise HTTPException(status_code=404, detail="unknown session")
    return await build(session_id, changes=parts == "all")


@router.get("/changes/commit")
async def get_commit(root: str = Query(..., max_length=4096), sha: str = Query(..., max_length=64)):
    from core.repo_changes import commit_files
    return {"files": await _run(commit_files, root, sha)}


@router.get("/changes/diff")
async def get_diff(root: str = Query(..., max_length=4096), sha: str = Query(..., max_length=64),
                   path: str = Query(..., max_length=4096)):
    from core.repo_changes import commit_diff
    return await _run(commit_diff, root, sha, path)
