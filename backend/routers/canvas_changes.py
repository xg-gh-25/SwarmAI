"""Canvas "Changes" tab: local state of each bound code repo (core/repo_changes).

Read-only. Every git call runs on the "turn_changes" pool (core/executors), never on
'subprocess', whose fail-open safety gates must not wait on it. 400 for bad input
(unknown repo, malformed sha or path), 503 when git itself fails.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

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


@router.get("/changes")
async def get_changes():
    from core.repo_changes import repos
    return {"repos": await _run(repos)}


@router.get("/changes/commit")
async def get_commit(root: str = Query(..., max_length=4096), sha: str = Query(..., max_length=64)):
    from core.repo_changes import commit_files
    return {"files": await _run(commit_files, root, sha)}


@router.get("/changes/diff")
async def get_diff(root: str = Query(..., max_length=4096), sha: str = Query(..., max_length=64),
                   path: str = Query(..., max_length=4096)):
    from core.repo_changes import commit_diff
    return await _run(commit_diff, root, sha, path)
