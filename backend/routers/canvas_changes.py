"""Canvas tabs of one chat session: GET /session/{id} = its Artifacts and Changes
(core/canvas_session → core/session_changes); /changes/tree-diff is one file of a
turn card or of a landing (both are two trees).

Read-only. Everything runs on the "canvas_changes" pool (core/executors): never on
'subprocess', whose fail-open safety gates must not wait on it, and never on
"turn_changes", which a streaming turn's finish waits on. Roots are bound repos only
(repo_changes.authorized_root). 404 when a diff is not available, 503 when git
itself fails.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Path, Query

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/canvas", tags=["canvas"])


@router.get("/session/{session_id}")
async def get_session_outputs(session_id: str = Path(..., min_length=1, max_length=128),
                              parts: str = Query("all", pattern="^(all|artifacts|changes)$")):
    """What this session produced: {artifacts, changes} (core/canvas_session). The two
    parts are separate requests (``parts=artifacts`` / ``parts=changes``); a part not
    asked for is null (not computed). 404 for a session id that does not exist."""
    from core.canvas_session import build
    from database import db
    if not await db.sessions.get(session_id):
        # an unknown id gets no record created for it (build backfills on first read)
        raise HTTPException(status_code=404, detail="unknown session")
    return await build(session_id, parts)


@router.get("/session/{session_id}/working-repo")
async def get_working_repo(session_id: str = Path(..., min_length=1, max_length=128)):
    """{workingRepo: {path, name} | null} — the bound repo the session's newest
    "files changed" card names (core/session_changes.working_repo); the terminal opens
    there. No run.json; an unknown session simply has none."""
    from core import executors, session_changes
    from database import db
    return {"workingRepo": await executors.run_in("canvas_changes", session_changes.working_repo,
                                                  session_id, str(db.db_path))}


@router.get("/changes/tree-diff")
async def get_tree_diff(root: str = Query(..., max_length=4096), start: str = Query(..., max_length=64),
                        end: str = Query(..., max_length=64), path: str = Query(..., max_length=4096)):
    """One file of a turn card in the Changes tab (core/session_changes.tree_diff).
    404 when it is not available (an unbound repo, bad input, a pruned tree), 503 on a
    transient failure."""
    from core import executors
    from core.session_changes import tree_diff
    from core.turn_changes import DiffUnavailable
    try:
        text = await executors.run_in("canvas_changes", tree_diff, root, start, end, path)
    except DiffUnavailable:
        raise HTTPException(status_code=503, detail="Diff temporarily unavailable")
    if text is None:
        raise HTTPException(status_code=404, detail="Diff not available")
    return {"diff": text}
