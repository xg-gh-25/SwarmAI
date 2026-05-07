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
import subprocess
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .hook_builder import HookRegistry

logger = logging.getLogger(__name__)

# Default corrections log path — can be overridden in factory functions
from config import get_app_data_dir as _get_app_data_dir
_DEFAULT_CORRECTIONS_PATH = str(
    _get_app_data_dir() / ".context" / "corrections.jsonl"
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

    Rotation: delegates to ``utils.jsonl_rotation.rotate_jsonl_if_oversized``
    (512 KB trigger, keeps newest 500 entries).  One stat() per write;
    rotation is rare (~monthly at normal usage).  Best-effort — never raises.
    """
    try:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)

        # Append the new entry
        line = json.dumps(entry, ensure_ascii=False) + "\n"
        with open(p, "a", encoding="utf-8") as f:
            f.write(line)

        # Rotate if oversized
        from utils.jsonl_rotation import rotate_jsonl_if_oversized
        rotate_jsonl_if_oversized(
            p,
            max_size_bytes=_MAX_CORRECTIONS_SIZE_BYTES,
            max_entries=_MAX_CORRECTIONS_ENTRIES,
        )

    except Exception:
        logger.exception("Failed to write correction to %s", path)


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
    ctx = session_context if session_context is not None else {}
    sid = ctx.get("sdk_session_id", "unknown")

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
        ctx["_corrections_count"] = ctx.get("_corrections_count", 0) + 1
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
            return {
                "hookSpecificOutput": {
                    "hookEventName": "PostToolUseFailure",
                    "additionalContext": hint,
                }
            }

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
    ctx = session_context if session_context is not None else {}
    sid = ctx.get("sdk_session_id", "unknown")

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
            ctx["_corrections_count"] = ctx.get("_corrections_count", 0) + 1

        return {}

    return _hook


# ---------------------------------------------------------------------------
# PostToolUse: file tracker — records Read/Edit/Write file paths
# ---------------------------------------------------------------------------

_TRACKED_TOOLS = {"Read", "Edit", "Write"}


def create_file_tracker(
    session_context: Optional[dict] = None,
):
    """Factory: creates a PostToolUse hook that tracks files touched during the session.

    Populates ``session_context["_files_touched"]`` (a set) with file paths
    from Read, Edit, and Write tool calls.  Used by PreCompact injection and
    session checkpoint.
    """
    ctx = session_context or {}

    async def _hook(input_data: Any, tool_use_id: Any, context: Any) -> dict:
        tool = _extract_field(input_data, "tool_name", "")
        if tool not in _TRACKED_TOOLS:
            return {}

        tool_input = _extract_field(input_data, "tool_input", {})
        file_path = tool_input.get("file_path", "") if isinstance(tool_input, dict) else ""
        if file_path:
            if "_files_touched" not in ctx:
                ctx["_files_touched"] = set()
            ctx["_files_touched"].add(file_path)

        return {}

    return _hook


# ---------------------------------------------------------------------------
# PostToolUse: session checkpoint — crash survival
# ---------------------------------------------------------------------------

_DEFAULT_CHECKPOINT_PATH = str(
    _get_app_data_dir() / ".context" / "session_checkpoint.json"
)
_DEFAULT_CHECKPOINT_INTERVAL = 10


def _get_recent_git_commits(workspace_dir: str, since_ts: float) -> list[str]:
    """Get recent git commits since a timestamp. Returns list of oneline strings.

    Subprocess with 2s timeout — never blocks the agent. Returns empty on any error.
    """
    from datetime import datetime, timezone

    try:
        since_str = datetime.fromtimestamp(since_ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
        result = subprocess.run(
            ["git", "log", "--oneline", "-5", f"--since={since_str}"],
            capture_output=True, text=True, timeout=2,
            cwd=workspace_dir,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip().split("\n")[:5]
    except Exception:
        pass
    return []


def create_session_checkpoint(
    session_context: Optional[dict] = None,
    checkpoint_path: Optional[str] = None,
    interval: int = _DEFAULT_CHECKPOINT_INTERVAL,
    workspace_dir: Optional[str] = None,
):
    """Factory: creates a PostToolUse hook that writes a session checkpoint.

    Every ``interval`` tool calls:
    1. Overwrites checkpoint JSON with current session state (crash recovery).
    2. Appends a content snapshot to today's DailyActivity (mid-session memory).

    On crash, ``recover_crash_checkpoint()`` reads the JSON and writes to
    DailyActivity on next startup.  For normal sessions, the DailyActivity
    append ensures content is captured even if post-session hooks don't fire.
    """
    path = checkpoint_path or _DEFAULT_CHECKPOINT_PATH
    ctx = session_context or {}
    from jobs.paths import SWARMWS as _SWARMWS
    ws = workspace_dir or str(_SWARMWS)
    counter_key = "_tool_count"
    start_ts_key = "_session_start_ts"
    last_da_count_key = "_last_da_checkpoint_count"

    if counter_key not in ctx:
        ctx[counter_key] = 0
    if start_ts_key not in ctx:
        ctx[start_ts_key] = time.time()

    async def _hook(input_data: Any, tool_use_id: Any, context: Any) -> dict:
        ctx[counter_key] = ctx.get(counter_key, 0) + 1
        count = ctx[counter_key]

        if count % interval != 0:
            return {}

        session_id = ctx.get("sdk_session_id", "unknown")
        files = sorted(ctx.get("_files_touched", set()))
        corrections = ctx.get("_corrections_count", 0)
        start_ts = ctx.get(start_ts_key, time.time())

        # Fetch recent git commits (2s timeout, never blocks)
        git_commits = _get_recent_git_commits(ws, start_ts)

        # 1. Write checkpoint JSON (crash recovery)
        checkpoint = {
            "session_id": session_id,
            "ts": time.time(),
            "tool_count": count,
            "files_touched": files[:20],  # Cap for JSON size
            "corrections_count": corrections,
            "git_commits": git_commits,
        }

        try:
            p = Path(path)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(checkpoint, ensure_ascii=False), encoding="utf-8")
        except Exception:
            logger.exception("Failed to write session checkpoint to %s", path)

        # 2. Append content snapshot to DailyActivity (mid-session memory)
        # Only write if new files or commits since last checkpoint
        last_count = ctx.get(last_da_count_key, 0)
        has_new_content = len(files) > last_count or git_commits
        if not has_new_content and count <= interval:
            # Skip first checkpoint if nothing meaningful happened yet
            return {}

        try:
            from datetime import datetime as dt
            now = dt.now()
            da_dir = Path(ws) / "Knowledge" / "DailyActivity"
            da_dir.mkdir(parents=True, exist_ok=True)
            da_file = da_dir / f"{now.strftime('%Y-%m-%d')}.md"

            # Build content-capped entry (target < 1KB)
            lines = [
                f"\n## {now.strftime('%H:%M')} | {session_id[:8]} | 📸 Mid-session checkpoint\n",
            ]
            if files:
                file_summary = ", ".join(f"`{Path(f).name}`" for f in files[:10])
                if len(files) > 10:
                    file_summary += f" (+{len(files) - 10} more)"
                lines.append(f"**Files:** {file_summary}\n")
            if git_commits:
                lines.append("**Git activity:**\n")
                for c in git_commits[:3]:
                    lines.append(f"- `{c[:72]}`\n")
            if corrections:
                lines.append(f"**Corrections:** {corrections}\n")

            entry = "".join(lines)
            # Hard cap at 1KB
            if len(entry.encode("utf-8")) > 1024:
                entry = entry[:1000] + "\n...(truncated)\n"

            # Concurrency note: we use plain open("a") instead of locked_write.py.
            # Our entries are <1KB (hard-capped above), and on macOS/APFS small
            # appends (<4KB) to a single file are effectively atomic at the
            # filesystem level.  These hooks are observe-only and crash-safe by
            # design — a torn write loses one checkpoint entry, which is
            # acceptable for mid-session snapshots.
            with open(da_file, "a", encoding="utf-8") as f:
                f.write(entry)

            ctx[last_da_count_key] = len(files)
            logger.debug(
                "Mid-session checkpoint written to DailyActivity: %d files, %d commits",
                len(files), len(git_commits),
            )
        except Exception:
            logger.debug("Failed to write mid-session checkpoint to DailyActivity", exc_info=True)

        return {}

    return _hook


# ---------------------------------------------------------------------------
# SubagentStop: transcript capture
# ---------------------------------------------------------------------------

_SUBAGENT_TAIL_BYTES = 5 * 1024  # Read last 5KB of transcript

_ERROR_PATTERNS = re.compile(
    r"""(?i)(?:
        Error:\s+\S+
      | Exception:\s+\S+
      | FAILED\s+tests/
      | AssertionError
      | ImportError
      | FileNotFoundError
      | KeyError
      | TypeError
      | ValueError
      | RuntimeError
    )""",
    re.VERBOSE,
)


def create_subagent_capture_hook(
    corrections_path: Optional[str] = None,
    session_context: Optional[dict] = None,
):
    """Factory: creates a SubagentStop hook that reads the agent transcript.

    Reads the last 5KB of the transcript, extracts error patterns via regex,
    and writes findings to corrections.jsonl.  Observe-only.
    """
    path = corrections_path or _DEFAULT_CORRECTIONS_PATH
    ctx = session_context if session_context is not None else {}
    sid = ctx.get("sdk_session_id", "unknown")

    async def _hook(input_data: Any, tool_use_id: Any, context: Any) -> dict:
        transcript_path = _extract_field(input_data, "agent_transcript_path", "")
        agent_id = _extract_field(input_data, "agent_id", "unknown")

        if not transcript_path:
            return {}

        try:
            p = Path(transcript_path)
            if not p.exists():
                return {}

            # Read tail of transcript
            size = p.stat().st_size
            with open(p, "r", encoding="utf-8", errors="replace") as f:
                if size > _SUBAGENT_TAIL_BYTES:
                    f.seek(size - _SUBAGENT_TAIL_BYTES)
                    f.readline()  # skip partial first line
                tail = f.read()

            # Extract error patterns
            errors = _ERROR_PATTERNS.findall(tail)
            if not errors:
                return {}

            summary = "; ".join(dict.fromkeys(errors))[:500]  # dedup, cap at 500 chars
            entry = {
                "ts": time.time(),
                "session_id": sid,
                "type": "subagent_finding",
                "agent_id": agent_id,
                "summary": summary,
            }
            _append_correction(path, entry)
            ctx["_corrections_count"] = ctx.get("_corrections_count", 0) + 1

        except Exception:
            logger.exception("Failed to capture subagent transcript from %s", transcript_path)

        return {}

    return _hook


# ---------------------------------------------------------------------------
# UserPromptSubmit: post-compact context injection
# ---------------------------------------------------------------------------

def create_post_compact_injection(
    session_context: Optional[dict] = None,
):
    """Factory: creates a UserPromptSubmit hook that injects context after compaction.

    When ``session_context["_compacted"]`` is True (set by PreCompact hook),
    the next UserPromptSubmit injects ``additionalContext`` with:
    - Files touched during this session (for re-reading)
    - Basic session continuity instructions

    Fire-once: resets ``_compacted`` flag after injection.
    """
    ctx = session_context or {}

    async def _hook(input_data: Any, tool_use_id: Any, context: Any) -> dict:
        if not ctx.get("_compacted"):
            return {}

        # Build compact survival instructions
        files = sorted(ctx.get("_files_touched", set()))
        parts = [
            "[System: Context was just compacted. Key session state below.]",
        ]
        if files:
            file_list = ", ".join(files[:20])  # cap at 20 files
            parts.append(f"Files touched this session (re-read if needed): {file_list}")

        # Reset flag — fire once
        ctx["_compacted"] = False

        return {
            "hookSpecificOutput": {
                "hookEventName": "UserPromptSubmit",
                "additionalContext": " ".join(parts),
            }
        }

    return _hook


# ---------------------------------------------------------------------------
# UserPromptSubmit: high-signal observation capture → DailyActivity
# ---------------------------------------------------------------------------

_HIGH_SIGNAL_PATTERNS = re.compile(
    r"""(?ix)(?:^|\b)(?:
        I\s+decid(?:ed|e)
      | we\s+decid(?:ed|e)
      | decision:\s
      | important:\s
      | rule:\s
      | lesson:\s
      | never\s+again
      | from\s+now\s+on
      | 我(?:们)?决定
      | 决定了
      | 以后(?:都|要|不)
      | 重要(?:：|:)
      | 教训(?:：|:)
      | 规则(?:：|:)
    )"""
)


def create_high_signal_capture(
    session_context: Optional[dict] = None,
    workspace_dir: Optional[str] = None,
):
    """Factory: creates a UserPromptSubmit hook that captures high-signal observations.

    Detects decision/lesson/rule signals in user prompts and appends them
    to today's DailyActivity file.  Does NOT write to MEMORY.md — distillation
    pipeline decides what gets promoted.  This is "faster capture without
    skipping the quality gate."

    Deduplication: tracks captured prompts in session_context to avoid
    writing the same signal twice if the user repeats.
    """
    ctx = session_context or {}
    from jobs.paths import SWARMWS as _SWARMWS_hs
    ws = workspace_dir or str(_SWARMWS_hs)
    captured_key = "_high_signal_captured"

    async def _hook(input_data: Any, tool_use_id: Any, context: Any) -> dict:
        prompt = _extract_field(input_data, "prompt", "")
        if not prompt or len(prompt) < 10:
            return {}

        if not _HIGH_SIGNAL_PATTERNS.search(prompt):
            return {}

        # Dedup within session
        if captured_key not in ctx:
            ctx[captured_key] = set()
        sig = prompt[:100]  # signature for dedup
        if sig in ctx[captured_key]:
            return {}
        ctx[captured_key].add(sig)

        # Append to today's DailyActivity
        try:
            from datetime import datetime
            now = datetime.now()
            da_dir = Path(ws) / "Knowledge" / "DailyActivity"
            da_dir.mkdir(parents=True, exist_ok=True)
            da_file = da_dir / f"{now.strftime('%Y-%m-%d')}.md"

            entry = (
                f"\n**🔔 High-signal capture** ({now.strftime('%H:%M')}): "
                f"{prompt[:500]}\n"
            )
            # Concurrency note: plain open("a") is safe here — entries are
            # well under 4KB (macOS/APFS atomic append threshold).  These are
            # observe-only hooks; a torn write loses one signal entry, which is
            # acceptable.  locked_write.py is not needed for this use case.
            with open(da_file, "a", encoding="utf-8") as f:
                f.write(entry)

            logger.debug("High-signal captured to DailyActivity: %.80s", prompt)
        except Exception:
            logger.exception("Failed to write high-signal to DailyActivity")

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

# ---------------------------------------------------------------------------
# PostToolUse: memory edit guard — validates Edit calls on MEMORY.md/EVOLUTION.md
# ---------------------------------------------------------------------------

_MEMORY_FILE_SUFFIXES = ("MEMORY.md", "EVOLUTION.md")


def create_memory_edit_guard():
    """Factory: creates a PostToolUse hook that validates Edit calls on memory files.

    Runs MemoryGuard on ``new_string`` when Edit targets a file ending in
    MEMORY.md or EVOLUTION.md.  Observe-only — the edit has already happened,
    but the hook injects a warning into additionalContext so the agent knows
    to self-correct.
    """
    async def _hook(tool_use: dict, tool_use_id: str, session: Any) -> dict:
        tool_name = tool_use.get("tool_name", "")
        if tool_name != "Edit":
            return {}

        tool_input = tool_use.get("tool_input", {})
        file_path = tool_input.get("file_path", "")

        # Only check files that end with MEMORY.md or EVOLUTION.md
        if not any(file_path.endswith(suffix) for suffix in _MEMORY_FILE_SUFFIXES):
            return {}

        new_string = tool_input.get("new_string", "")
        if not new_string:
            return {}

        # Run MemoryGuard on the new content
        try:
            from core.memory_guard import MemoryGuard
            guard = MemoryGuard()
            result = guard.scan(new_string)
            if result.rejected:
                categories = {f.category for f in result.findings if f.action == "reject"}
                warning = (
                    f"⚠️ MemoryGuard WARNING: Edit to {file_path.split('/')[-1]} "
                    f"contains dangerous patterns: {', '.join(categories)}. "
                    f"This content is now in the system prompt. "
                    f"Consider reverting the edit immediately."
                )
                logger.warning(
                    "MemoryGuard: Edit to %s rejected — %s",
                    file_path, categories,
                )
                return {
                    "hookSpecificOutput": {
                        "hookEventName": "PostToolUse",
                        "additionalContext": warning,
                    }
                }
        except ImportError:
            pass  # memory_guard not available
        except Exception as exc:
            logger.debug("MemoryGuard Edit check failed: %s", exc)

        return {}

    return _hook


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

    # Phase 2: PostToolUse file tracker
    registry.register(
        "PostToolUse",
        create_file_tracker(session_context),
        "file_tracker",
    )

    # Phase 2: PostToolUse session checkpoint
    registry.register(
        "PostToolUse",
        create_session_checkpoint(session_context),
        "session_checkpoint",
    )

    # Phase 2: PostToolUse memory edit guard (validates Edit on MEMORY.md/EVOLUTION.md)
    registry.register(
        "PostToolUse",
        create_memory_edit_guard(),
        "memory_edit_guard",
    )

    # Phase 2: SubagentStop transcript capture
    registry.register(
        "SubagentStop",
        create_subagent_capture_hook(path, session_context),
        "subagent_capture",
    )

    # UserPromptSubmit hooks
    registry.register(
        "UserPromptSubmit",
        create_user_correction_detector(path, session_context),
        "user_correction_detector",
    )

    # Phase 2: UserPromptSubmit post-compact injection
    registry.register(
        "UserPromptSubmit",
        create_post_compact_injection(session_context),
        "post_compact_injection",
    )

    # Phase 2: UserPromptSubmit high-signal observation capture
    registry.register(
        "UserPromptSubmit",
        create_high_signal_capture(session_context),
        "high_signal_capture",
    )

    logger.info(
        "Runtime hooks registered: correction_capture, error_pattern_detector, "
        "failure_tracker_reset, file_tracker, session_checkpoint, memory_edit_guard, "
        "subagent_capture, user_correction_detector, post_compact_injection, "
        "high_signal_capture"
    )
