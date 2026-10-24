"""
Self-Eval hooks — the agent's nervous system for behavioral integrity.

These hooks make eval a native capability (proprioception), not an external harness:

(1) seed_from_correction: called from user_correction_detector → auto-grows the
    behavioral contract from failures (the agent learning from mistakes).
(2) post_run_promotion: called after eval run → promotes stable cases to quarterly
    cadence (the agent recognizing internalized behaviors).
"""

import logging
from typing import Any

logger = logging.getLogger(__name__)

def seed_from_correction(
    correction_id: str, correction_text: str, class_name: str = "UNCLASSIFIED",
    persist: bool = True,
) -> None:
    """Auto-seed a golden set DRAFT skeleton from a classified correction.

    Called post-session from governance_router.classify_new_corrections for
    cognitive (pending_confirm) corrections — the noise-gated auto-growth path.

    Non-blocking, best-effort. Must never raise.

    persist=False defers the golden_set.yaml write so a batch caller can flush
    once after seeding many in a loop (call get_eval_service().flush_golden_set()).
    """
    try:
        from core.eval_service import get_eval_service

        svc = get_eval_service()
        result = svc.auto_seed_case(correction_id, correction_text, class_name, persist=persist)
        if result:
            logger.info("[eval_hooks] Auto-seeded case %s from correction %s", result["id"], correction_id)
    except Exception as e:
        logger.debug("[eval_hooks] Case seeding failed (non-blocking): %s", e)


def get_eval_service_for_flush():
    """Return the eval service singleton, for a batch caller to flush_golden_set().

    Pairs with seed_from_correction(persist=False): seed many, then flush once.
    Kept here (not a direct core.eval_service import in the router) so the seam
    is mockable in router tests, mirroring seed_from_correction.
    """
    from core.eval_service import get_eval_service

    return get_eval_service()


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
