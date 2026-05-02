"""Runtime hooks for real-time correction capture and error pattern detection.

These hooks fire DURING agent execution (not post-session) via the Claude
Agent SDK hook system.  All hooks are observe-only — they log and inject
additionalContext but never block or modify tool inputs/outputs.

Key public symbols:

- ``register_runtime_hooks``         — Wire all runtime hooks into a HookRegistry
- ``create_correction_capture_hook`` — PostToolUseFailure → corrections.jsonl
- ``create_error_pattern_detector``  — PostToolUseFailure → hint after 2+ failures
- ``create_user_correction_detector``— UserPromptSubmit → corrections.jsonl
"""

import json
import logging
import re
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .hook_builder import HookRegistry

logger = logging.getLogger(__name__)

# Default corrections log path — can be overridden in factory functions
_DEFAULT_CORRECTIONS_PATH = str(
    Path.home() / ".swarm-ai" / ".context" / "corrections.jsonl"
)

# Consecutive failure threshold before injecting a hint
_FAILURE_HINT_THRESHOLD = 2

# Rotation: keep the newest N entries when file exceeds MAX_SIZE_BYTES.
# 500 entries × ~1.5KB = ~750KB — well within reason for a local log.
_MAX_CORRECTIONS_ENTRIES = 500
_MAX_CORRECTIONS_SIZE_BYTES = 512 * 1024  # 512KB trigger threshold

# Correction pattern regex — conservative to minimize false positives.
# Matches when patterns appear at word boundaries or start of string.
_CORRECTION_PATTERNS_EN = re.compile(
    r"""(?ix)                   # case-insensitive, verbose
    (?:^|\b)(?:
        (?:that(?:'s|s)?\s+)?(?:wrong|incorrect|not\s+right|not\s+correct)
      | no[\s,]+(?:that|it|this)
      | actually[\s,]+(?:no\b|not\b|don'?t|shouldn'?t|isn'?t|wasn'?t|can'?t|won'?t|wouldn'?t|never\b)
      | you(?:'re|\s+are)\s+wrong
      | that(?:'s|s)?\s+not\s+(?:what|how)
      | I\s+(?:said|meant|asked)
      | 不对
      | 错了
      | 搞错
      | 你搞错了
      | 不是这样
      | 应该是
    )
    """
)


def _append_correction(path: str, entry: dict) -> None:
    """Append a correction entry to JSONL file, rotating when oversized.

    Rotation strategy: when file exceeds ``_MAX_CORRECTIONS_SIZE_BYTES``,
    read all lines, keep the newest ``_MAX_CORRECTIONS_ENTRIES``, rewrite.
    One stat() per write; rotation is rare (~monthly at normal usage).
    Best-effort — never raises.
    """
    try:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)

        # Append the new entry
        line = json.dumps(entry, ensure_ascii=False) + "\n"
        with open(p, "a", encoding="utf-8") as f:
            f.write(line)

        # Check size — one stat(), cheap
        if p.stat().st_size > _MAX_CORRECTIONS_SIZE_BYTES:
            _rotate_corrections(p)

    except Exception:
        logger.exception("Failed to write correction to %s", path)


def _rotate_corrections(p: Path) -> None:
    """Keep newest ``_MAX_CORRECTIONS_ENTRIES`` lines in the file.

    Atomic: write to .tmp, then rename (same filesystem = atomic on POSIX).
    """
    try:
        lines = p.read_text(encoding="utf-8").strip().split("\n")
        if len(lines) <= _MAX_CORRECTIONS_ENTRIES:
            return  # size exceeded but entry count is fine — skip

        kept = lines[-_MAX_CORRECTIONS_ENTRIES:]
        tmp = p.with_suffix(".jsonl.tmp")
        tmp.write_text("\n".join(kept) + "\n", encoding="utf-8")
        tmp.rename(p)
        logger.info(
            "Rotated corrections.jsonl: %d → %d entries",
            len(lines), len(kept),
        )
    except Exception:
        logger.exception("Failed to rotate %s", p)


def _extract_field(data: Any, field: str, default: Any = "") -> Any:
    """Extract field from dict or object — SDK hook inputs can be either."""
    if isinstance(data, dict):
        return data.get(field, default)
    return getattr(data, field, default)


# ---------------------------------------------------------------------------
# PostToolUseFailure: correction capture → corrections.jsonl
# ---------------------------------------------------------------------------

def create_correction_capture_hook(
    corrections_path: Optional[str] = None,
    session_context: Optional[dict] = None,
):
    """Factory: creates a PostToolUseFailure hook that logs tool failures.

    Args:
        corrections_path: Path to corrections.jsonl (default: ~/.swarm-ai/.context/)
        session_context: Session context dict for session_id extraction
    """
    path = corrections_path or _DEFAULT_CORRECTIONS_PATH
    sid = (session_context or {}).get("sdk_session_id", "unknown")

    async def _hook(input_data: Any, tool_use_id: Any, context: Any) -> dict:
        tool = _extract_field(input_data, "tool_name", "unknown")
        tool_input = _extract_field(input_data, "tool_input", {})
        error = _extract_field(input_data, "error", "")

        entry = {
            "ts": time.time(),
            "session_id": sid,
            "type": "tool_failure",
            "tool": tool,
            "input_summary": str(tool_input)[:500],
            "error": str(error)[:1000],
        }
        _append_correction(path, entry)
        return {}

    return _hook


# ---------------------------------------------------------------------------
# PostToolUseFailure: error pattern detection → additionalContext hint
# ---------------------------------------------------------------------------

def create_error_pattern_detector(
    session_context: Optional[dict] = None,
):
    """Factory: creates a PostToolUseFailure hook that detects consecutive failures.

    After 2+ consecutive failures on the same tool, injects an additionalContext
    hint to nudge the agent toward a different approach.

    State is stored in session_context["_failure_tracker"] (per-session, no globals).
    A paired success hook (from ``create_failure_tracker_reset``) clears the
    counter when a tool succeeds — so "consecutive" means truly consecutive.
    """
    # Per-tool consecutive failure counter — stored in session_context
    tracker_key = "_failure_tracker"
    ctx = session_context or {}
    if tracker_key not in ctx:
        ctx[tracker_key] = defaultdict(int)

    async def _hook(input_data: Any, tool_use_id: Any, context: Any) -> dict:
        tool = _extract_field(input_data, "tool_name", "unknown")
        error = _extract_field(input_data, "error", "")
        tracker = ctx[tracker_key]
        tracker[tool] += 1
        count = tracker[tool]

        if count >= _FAILURE_HINT_THRESHOLD:
            hint = (
                f"[System: {tool} has failed {count} consecutive times. "
                f"Last error: {str(error)[:200]}. Consider a different approach.]"
            )
            return {"additionalContext": hint}

        return {}

    return _hook


# ---------------------------------------------------------------------------
# PostToolUse: reset failure tracker on success
# ---------------------------------------------------------------------------

def create_failure_tracker_reset(
    session_context: Optional[dict] = None,
):
    """Factory: creates a PostToolUse hook that resets the consecutive failure counter.

    When a tool succeeds, its entry in ``_failure_tracker`` is cleared so that
    the error pattern detector only counts truly consecutive failures.
    """
    tracker_key = "_failure_tracker"
    ctx = session_context or {}

    async def _hook(input_data: Any, tool_use_id: Any, context: Any) -> dict:
        tool = _extract_field(input_data, "tool_name", "unknown")
        tracker = ctx.get(tracker_key)
        if tracker and tool in tracker:
            tracker[tool] = 0
        return {}

    return _hook


# ---------------------------------------------------------------------------
# UserPromptSubmit: correction pattern detection → corrections.jsonl
# ---------------------------------------------------------------------------

def create_user_correction_detector(
    corrections_path: Optional[str] = None,
    session_context: Optional[dict] = None,
):
    """Factory: creates a UserPromptSubmit hook that detects user corrections.

    Scans user prompts for correction signals (CN + EN patterns) and logs
    to corrections.jsonl.  Observe-only — does not inject additionalContext.
    """
    path = corrections_path or _DEFAULT_CORRECTIONS_PATH
    sid = (session_context or {}).get("sdk_session_id", "unknown")

    async def _hook(input_data: Any, tool_use_id: Any, context: Any) -> dict:
        prompt = _extract_field(input_data, "prompt", "")
        if not prompt:
            return {}

        if _CORRECTION_PATTERNS_EN.search(prompt):
            entry = {
                "ts": time.time(),
                "session_id": sid,
                "type": "user_correction",
                "prompt": prompt[:1000],
            }
            _append_correction(path, entry)

        return {}

    return _hook


# ---------------------------------------------------------------------------
# Reader: aggregate corrections.jsonl data for evolution optimizer
# ---------------------------------------------------------------------------

def read_correction_stats(
    corrections_path: Optional[str] = None,
    recency_days: int = 7,
) -> dict[str, dict]:
    """Read corrections.jsonl and compute per-skill stats for the optimizer.

    Returns a dict keyed by skill/tool name::

        {
            "Bash": {"recent_corrections": 3, "repeat_count": 5, "total": 12},
            "s_evaluate": {"recent_corrections": 1, "repeat_count": 2, "total": 4},
        }

    - ``recent_corrections``: entries within the last ``recency_days`` days.
    - ``repeat_count``: total entries for this skill (proxy for how often
      the same skill keeps failing).
    - ``total``: same as repeat_count (explicit alias for clarity).

    Returns empty dict on any error — caller should handle gracefully.
    """
    path = Path(corrections_path or _DEFAULT_CORRECTIONS_PATH)
    if not path.exists():
        return {}

    cutoff = time.time() - (recency_days * 86400)
    stats: dict[str, dict] = {}

    try:
        for line in path.read_text(encoding="utf-8").strip().split("\n"):
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue

            # Use "tool" for tool_failure, try to extract skill name for user_correction
            key = entry.get("tool", "")
            if not key and entry.get("type") == "user_correction":
                key = "_user_correction"  # aggregate bucket

            if not key:
                continue

            if key not in stats:
                stats[key] = {"recent_corrections": 0, "repeat_count": 0, "total": 0}

            stats[key]["total"] += 1
            stats[key]["repeat_count"] += 1
            ts = entry.get("ts", 0)
            if ts >= cutoff:
                stats[key]["recent_corrections"] += 1

    except Exception:
        logger.exception("Failed to read correction stats from %s", path)
        return {}

    return stats


# ---------------------------------------------------------------------------
# Registration helper
# ---------------------------------------------------------------------------

def register_runtime_hooks(
    registry: "HookRegistry",
    session_context: dict,
    corrections_path: Optional[str] = None,
) -> None:
    """Register all runtime hooks into a HookRegistry.

    Called from hook_builder.build_hooks() to wire runtime observation.
    """
    path = corrections_path or _DEFAULT_CORRECTIONS_PATH

    # PostToolUseFailure hooks
    registry.register(
        "PostToolUseFailure",
        create_correction_capture_hook(path, session_context),
        "correction_capture",
    )
    registry.register(
        "PostToolUseFailure",
        create_error_pattern_detector(session_context),
        "error_pattern_detector",
    )

    # PostToolUse: reset failure tracker on success
    registry.register(
        "PostToolUse",
        create_failure_tracker_reset(session_context),
        "failure_tracker_reset",
    )

    # UserPromptSubmit hooks
    registry.register(
        "UserPromptSubmit",
        create_user_correction_detector(path, session_context),
        "user_correction_detector",
    )

    logger.info(
        "Runtime hooks registered: correction_capture, error_pattern_detector, "
        "failure_tracker_reset, user_correction_detector"
    )
