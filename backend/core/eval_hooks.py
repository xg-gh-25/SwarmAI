"""
Self-Eval hooks — the agent's nervous system for behavioral integrity.

These hooks make eval a native capability (proprioception), not an external harness:

post_run_promotion: called after eval run → promotes stable cases to quarterly
cadence (the agent recognizing internalized behaviors). (Seeding cases from
corrections and from low-scoring sessions is gone: auto-made cases could not pass
the golden set's admission bar, design 2026-10-26-eval-as-product §3.5/§3.7.)
"""

import logging
from typing import Any

logger = logging.getLogger(__name__)

def post_run_promotion() -> list[str]:
    """Called after eval run completes to promote stable cases.

    Returns list of promoted case IDs. Non-blocking.
    """
    try:
        from core.eval_service import get_eval_service

        svc = get_eval_service()
        promoted = svc.promote_stable_cases()
        return promoted
    except Exception as e:
        logger.debug("[eval_hooks] Stable promotion failed (non-blocking): %s", e)
        return []
