"""Attention API — the unified "Need You" channel read endpoint.

Design: Knowledge/Designs/2026-08-08-unified-need-you-channel-design.md

    GET /api/attention            → aggregate all 5 sources, normalized+tiered
    GET /api/attention?brain=<P>  → only that brain's items (governance excluded)

Single source of truth behind BOTH the frontend needs-you overlay AND agent
SENSE ("show me / handle Need You"). Read-only, fail-soft (a broken source logs
+ returns [] rather than blanking the channel). No /act endpoint — an item's
action is dispatched into a chat tab via the existing onItemClick mechanism.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

from fastapi import APIRouter

from jobs.paths import SWARMWS
from core import attention_authority

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/attention", tags=["attention"])


@router.get("")
async def get_attention(brain: Optional[str] = None):
    """Return the unified attention queue.

    `brain` (optional): scope to one project. Governance (OS-level, brain=None)
    is excluded from a per-brain query — this is what makes a brain card's
    pending count truthful (includes escalation, excludes OS-level governance).

    Offloaded to a thread: the paused-run source stat()s+parses every run file
    (see pipelines._load_pipeline_runs), which would block the event loop inline.
    """
    result = await asyncio.to_thread(
        attention_authority.collect, SWARMWS, brain=brain
    )
    return result.to_dict()
