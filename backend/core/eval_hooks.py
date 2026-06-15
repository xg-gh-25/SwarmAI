"""
Self-Eval hooks — the agent's nervous system for behavioral integrity.

These hooks make eval a native capability (proprioception), not an external harness:

(1) change_triggered_eval: PostToolUse hook that fires after governance file edits
    → runs affected eval cases in background (the agent sensing its own change).
(2) seed_from_correction: called from user_correction_detector → auto-grows the
    behavioral contract from failures (the agent learning from mistakes).
(3) post_run_promotion: called after eval run → promotes stable cases to quarterly
    cadence (the agent recognizing internalized behaviors).
"""

import logging
from pathlib import Path
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

# Governance files that trigger scoped eval on edit
_GOVERNANCE_FILES = {"STEERING.md", "AGENT.md", "SOUL.md", "MEMORY.md", "EVOLUTION.md"}


def create_change_triggered_eval(session_context: Optional[dict] = None) -> Callable:
    """Factory: PostToolUse hook that triggers eval on governance file edits.

    After a successful Edit/Write to a governance file, runs eval cases
    whose `affected_by` includes that file. Non-blocking (background thread).
    """
    ctx = session_context or {}

    async def _hook(input_data: Any, tool_use_id: Any, context: Any) -> dict:
        # Only fire on Edit/Write
        tool_name = _extract(input_data, "tool_name", "")
        if tool_name not in ("Edit", "Write"):
            return {}

        # Check if the file is a governance file
        tool_input = _extract(input_data, "tool_input", {})
        file_path = tool_input.get("file_path", "") if isinstance(tool_input, dict) else ""
        filename = Path(file_path).name if file_path else ""

        if filename not in _GOVERNANCE_FILES:
            return {}

        # Don't fire repeatedly for same file in same session
        triggered_key = f"_eval_triggered_{filename}"
        if ctx.get(triggered_key):
            return {}
        ctx[triggered_key] = True

        # Trigger scoped eval in background
        try:
            from core.eval_service import get_eval_service

            svc = get_eval_service()
            affected = svc.get_affected_cases([filename])

            if affected:
                case_ids = [c["id"] for c in affected]
                logger.info(
                    "[eval_hooks] Governance file %s edited — triggering eval for %d cases: %s",
                    filename, len(case_ids), case_ids,
                )
                try:
                    svc.trigger_run(
                        trigger=f"change_{filename.replace('.md', '').lower()}",
                        case_ids=case_ids,
                    )
                except RuntimeError:
                    # Another run already in progress — skip
                    logger.debug("[eval_hooks] Eval run already in progress, skipping change trigger")
        except Exception as e:
            logger.warning("[eval_hooks] Change-triggered eval failed: %s", e)

        return {}

    return _hook


def seed_from_correction(correction_id: str, correction_text: str, class_name: str = "UNCLASSIFIED") -> None:
    """Called from user_correction_detector to auto-seed a golden set case.

    Non-blocking, best-effort. Must never raise.
    """
    try:
        from core.eval_service import get_eval_service

        svc = get_eval_service()
        result = svc.auto_seed_case(correction_id, correction_text, class_name)
        if result:
            logger.info("[eval_hooks] Auto-seeded case %s from correction %s", result["id"], correction_id)
    except Exception as e:
        logger.debug("[eval_hooks] Case seeding failed (non-blocking): %s", e)


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


def _extract(data: Any, field: str, default: Any = "") -> Any:
    """Extract field from dict or object."""
    if isinstance(data, dict):
        return data.get(field, default)
    return getattr(data, field, default)
