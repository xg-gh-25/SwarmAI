"""Conversation context injection for resumed sessions.

Five-layer enriched resume: structured checkpoint (~5-10K tokens),
uncommitted git state (~500), assistant conclusions (~5-8K),
key tool results (~5-15K), and recent conversation (~20-60K).

The checkpoint gives the new agent a full picture of what was happening;
the enrichment layers provide enough substance to continue naturally.
Budget is model-aware and generous (150K for 1M models).

**Stability contract**: if any extraction fails, the module falls back
to the legacy raw-history injection.  Every extraction helper is
wrapped in its own try/except — one failure never cascades to another.

Public API (unchanged):
- ``build_resume_context(app_session_id, ...)`` → str
"""

import json
import logging
import re
import subprocess as _subprocess
from datetime import datetime
from collections import OrderedDict
from pathlib import Path

from core import executors

logger = logging.getLogger(__name__)

_TOOL_ONLY_TYPES = {"tool_use", "tool_result"}

# ─── Section headers & preamble ─────────────────────────────────────

_SECTION_HEADER = "## Session Resume"

_CHECKPOINT_PREAMBLE = (
    "The previous session ended (app restart, timeout, or eviction). "
    "Below is a structured checkpoint extracted from that session's "
    "message history, followed by assistant conclusions, key tool "
    "results, and recent conversation turns.\n"
    "\n"
    "RULES:\n"
    "- Do NOT re-execute any actions, tool calls, or code changes.\n"
    "- Use the checkpoint to understand what was happening.\n"
    "- Use the recent turns to understand conversational context.\n"
    "- Wait for the user's NEW message and respond ONLY to that.\n"
    "- If the user says 'resume' or 'continue', pick up the task "
    "described in the checkpoint."
)

# Legacy preamble kept for fallback path
_LEGACY_PREAMBLE = (
    "The user resumed this chat after an app restart. The turns below are "
    "READ-ONLY history from the previous session — treat them as background "
    "context, NOT as prompts to respond to.\n"
    "\n"
    "RULES:\n"
    "- Do NOT re-answer, re-apologize, or re-explain anything from the history.\n"
    "- Do NOT re-execute any actions, tool calls, or code changes mentioned below.\n"
    "- Do NOT reference this history section unless the user explicitly asks about it.\n"
    "- Wait for the user's NEW message (after this section) and respond ONLY to that."
)

_TRUNCATION_NOTE = "[Earlier messages truncated to fit token budget]"


# ─── Message helpers (shared by both paths) ──────────────────────────

def _filter_tool_only_messages(messages: list[dict]) -> list[dict]:
    """Remove messages whose content blocks are exclusively tool_use or tool_result."""
    result = []
    for msg in messages:
        content = msg.get("content")
        if not isinstance(content, list) or len(content) == 0:
            continue
        has_non_tool = any(
            block.get("type") not in _TOOL_ONLY_TYPES
            for block in content
            if isinstance(block, dict)
        )
        if has_non_tool:
            result.append(msg)
    return result


def _compact_tool_args(inp: dict) -> str:
    """Produce a compact summary of tool arguments (file paths, key params)."""
    parts: list[str] = []
    for key in ("file_path", "path", "command", "pattern", "query", "content"):
        val = inp.get(key)
        if val is not None:
            s = str(val)
            if len(s) > 80:
                s = s[:77] + "..."
            parts.append(f"{key}={s}")
        if len(parts) >= 2:
            break
    return ", ".join(parts) if parts else ""


def _summarize_tool_blocks(content: list[dict]) -> list[str]:
    """Summarize tool_use blocks as compact action descriptions."""
    summaries: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "tool_use":
            name = block.get("name", "unknown")
            inp = block.get("input", {})
            brief = _compact_tool_args(inp) if isinstance(inp, dict) else ""
            summaries.append(f"  → {name}({brief})")
    return summaries


def _format_message(message: dict) -> str | None:
    """Format a single message as ``Role: content`` with placeholder handling."""
    try:
        role = message.get("role", "")
        prefix = "User:" if role == "user" else "Assistant:"

        content = message.get("content", [])
        if not isinstance(content, list):
            return None

        parts: list[str] = []
        for block in content:
            if not isinstance(block, dict):
                continue
            block_type = block.get("type", "")
            if block_type == "text":
                text = block.get("text", "")
                if text:
                    parts.append(text)
            elif block_type == "image":
                parts.append("[image attachment]")
            elif block_type == "document":
                parts.append("[document attachment]")

        has_text = any(
            isinstance(b, dict) and b.get("type") == "text" and b.get("text")
            for b in content
        )
        if not has_text:
            tool_summaries = _summarize_tool_blocks(content)
            if tool_summaries:
                parts.append("[Tools used:]")
                parts.extend(tool_summaries)

        if not parts:
            return None

        return f"{prefix} {chr(10).join(parts)}"
    except Exception:
        logger.warning("Failed to format message: %s", message.get("id", "unknown"), exc_info=True)
        return None


def _apply_token_budget(
    formatted_messages: list[str], token_budget: int
) -> tuple[list[str], bool]:
    """Remove oldest messages until total estimated tokens fit within budget."""
    try:
        from .context_directory_loader import ContextDirectoryLoader

        messages = list(formatted_messages)
        was_truncated = False

        token_counts = [ContextDirectoryLoader.estimate_tokens(m) for m in messages]
        total = sum(token_counts)

        start_idx = 0
        while total > token_budget and start_idx < len(messages):
            total -= token_counts[start_idx]
            start_idx += 1
            was_truncated = True

        return (messages[start_idx:], was_truncated)
    except Exception:
        logger.warning("Token budget estimation failed", exc_info=True)
        return ([], False)


# ─── Checkpoint extraction helpers ───────────────────────────────────
# Each function is independently guarded.  Returns empty/default on error.

def _extract_text_from_content(content: list | None) -> str:
    """Pull concatenated text blocks from a message's content list."""
    if not isinstance(content, list):
        return ""
    parts = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "text":
            t = block.get("text", "")
            if t:
                parts.append(t)
    return "\n".join(parts)


def _find_last_user_text(messages: list[dict]) -> str:
    """Return the text of the last user message."""
    try:
        for msg in reversed(messages):
            if msg.get("role") == "user":
                text = _extract_text_from_content(msg.get("content"))
                if text:
                    # Cap at 4000 chars — preserves full task descriptions
                    # including structured data like tables and checklists.
                    return text[:4000]
        return ""
    except Exception:
        return ""


def _extract_tool_summary(messages: list[dict]) -> dict[str, set[str]]:
    """Scan recent messages for tool_use blocks → {tool_name: {key args}}.

    Returns a dict like {"Read": {"agent.py"}, "Bash": {"git status..."}}.
    Only scans the last ``messages`` provided (caller should slice).

    NOTE: DB persists tool_use as {name, summary, category} without the
    full ``input`` dict.  We extract info from both ``input`` (live) and
    ``summary`` (DB) to work in all contexts.
    """
    try:
        summary: dict[str, set[str]] = {}
        for msg in messages:
            content = msg.get("content")
            if not isinstance(content, list):
                continue
            for block in content:
                if not isinstance(block, dict) or block.get("type") != "tool_use":
                    continue
                name = block.get("name", "")
                if not name:
                    continue

                arg = ""
                inp = block.get("input")

                if isinstance(inp, dict) and inp:
                    # Live path: full input dict available
                    if name in ("Read", "Write", "Edit", "Glob"):
                        arg = inp.get("file_path") or inp.get("path") or inp.get("pattern") or ""
                    elif name == "Bash":
                        arg = (inp.get("command") or "")[:60]
                    elif name == "Grep":
                        arg = (inp.get("pattern") or "")[:60]
                    elif name == "Agent":
                        arg = (inp.get("description") or "")[:80]
                    elif name == "Skill":
                        arg = inp.get("skill") or ""
                    else:
                        arg = _compact_tool_args(inp)[:60]
                else:
                    # DB path: only summary string available
                    s = block.get("summary", "")
                    if s:
                        arg = s[:80]

                if name not in summary:
                    summary[name] = set()
                if arg:
                    summary[name].add(arg)
        return summary
    except Exception:
        logger.debug("Tool summary extraction failed", exc_info=True)
        return {}


def _extract_files_touched(tool_summary: dict[str, set[str]]) -> list[str]:
    """From tool summary, extract unique file paths that were read/edited.

    Returns relative paths (e.g. ``backend/core/context_injector.py``)
    instead of basenames, so the resumed agent knows exactly which files
    were being worked on.

    Works with both live (input.file_path) and DB (summary string) data.
    Summary strings look like 'Reading /path/to/file.py' or
    'Editing /path/to/file.py'.
    """
    # SwarmWS root for making paths relative
    from jobs.paths import SWARMWS as _swarmws_path
    _ws_root = str(_swarmws_path) + "/"
    # Resolve swarmai root from this file: context_injector.py → core/ → backend/ → swarmai/
    _swarmai_root = str(Path(__file__).resolve().parents[2]) + "/"

    try:
        files: set[str] = set()
        for tool_name in ("Read", "Write", "Edit"):
            for arg in tool_summary.get(tool_name, set()):
                if not arg:
                    continue
                # Try to find a path-like token
                for token in arg.split():
                    cleaned = token.rstrip(".,;:\"')")
                    if "/" in cleaned and "." in cleaned.rsplit("/", 1)[-1]:
                        # Make relative by stripping known roots
                        rel = cleaned
                        if rel.startswith(_swarmai_root):
                            rel = rel[len(_swarmai_root):]
                        elif rel.startswith(_ws_root):
                            rel = rel[len(_ws_root):]
                        elif rel.startswith("/"):
                            # Unknown absolute path — use last 3 segments
                            parts = rel.rsplit("/", 3)
                            rel = "/".join(parts[-3:]) if len(parts) > 3 else rel
                        if rel and len(rel) < 120:
                            files.add(rel)
                            break
        return sorted(files)[:30]
    except Exception as exc:  # noqa: BLE001
        # Degrade-OBSERVABLE. Rationale shared by the five _extract_*/_count_*
        # siblings below: they feed build_resume_context, the thing that turns a cold
        # resume from ~3K into ~50-100K tokens of "where I left off". A silent [] does
        # not read as "extraction broke", it reads as "this session touched nothing" —
        # so a parser regression yields a confident, empty, WRONG checkpoint and the
        # enrichment quietly stops working with zero signal. Keep degrading (a partial
        # resume beats a failed one) but say so.
        logger.warning("_extract_files_touched failed, resume context omits it: %s", exc)
        return []


def _extract_git_activity(messages: list[dict]) -> list[str]:
    """Scan Bash tool calls for git commit commands → extract commit messages.

    Works with both live (input.command) and DB (summary) paths.
    """
    try:
        commits: list[str] = []
        for msg in messages:
            content = msg.get("content")
            if not isinstance(content, list):
                continue
            for block in content:
                if not isinstance(block, dict) or block.get("type") != "tool_use":
                    continue
                if block.get("name") != "Bash":
                    continue
                # Try input.command first (live), then summary (DB)
                inp = block.get("input")
                cmd = ""
                if isinstance(inp, dict):
                    cmd = inp.get("command", "")
                if not cmd:
                    cmd = block.get("summary", "")
                if "git commit" in cmd:
                    # Try standard -m "msg" first
                    m = re.search(r'-m\s+["\']([^"\']+)', cmd)
                    if m:
                        msg_text = m.group(1).strip()
                        # Skip HEREDOC markers like "$(cat <<'EOF'"
                        if msg_text and not msg_text.startswith("$("):
                            commits.append(msg_text[:80])
        return commits[-5:]
    except Exception as exc:  # noqa: BLE001
        # Degrade-OBSERVABLE, see _extract_files_touched.
        logger.warning("_extract_git_activity failed, resume context omits it: %s", exc)
        return []


def _extract_agent_spawns(messages: list[dict]) -> list[str]:
    """Scan for Agent tool calls → extract descriptions of sub-tasks.

    Works with both live (input.description) and DB (summary) paths.
    """
    try:
        spawns: list[str] = []
        for msg in messages:
            content = msg.get("content")
            if not isinstance(content, list):
                continue
            for block in content:
                if not isinstance(block, dict) or block.get("type") != "tool_use":
                    continue
                if block.get("name") != "Agent":
                    continue
                # Try input.description first (live), then summary (DB)
                inp = block.get("input")
                desc = ""
                if isinstance(inp, dict):
                    desc = inp.get("description", "")
                if not desc:
                    desc = block.get("summary", "")
                if desc:
                    # Strip "Agent: " prefix from summary
                    if desc.startswith("Agent: "):
                        desc = desc[7:]
                    spawns.append(desc[:100])
        return spawns[-5:]
    except Exception as exc:  # noqa: BLE001
        # Degrade-OBSERVABLE, see _extract_files_touched.
        logger.warning("_extract_agent_spawns failed, resume context omits it: %s", exc)
        return []


def _extract_skill_invocations(messages: list[dict]) -> list[str]:
    """Scan for Skill tool calls → extract skill names.

    Works with both live (input.skill) and DB (summary) paths.
    """
    try:
        skills: list[str] = []
        seen: set[str] = set()
        for msg in messages:
            content = msg.get("content")
            if not isinstance(content, list):
                continue
            for block in content:
                if not isinstance(block, dict) or block.get("type") != "tool_use":
                    continue
                if block.get("name") != "Skill":
                    continue
                inp = block.get("input")
                skill_name = ""
                if isinstance(inp, dict):
                    skill_name = inp.get("skill", "")
                if not skill_name:
                    skill_name = block.get("summary", "")[:60]
                if skill_name and skill_name not in seen:
                    seen.add(skill_name)
                    skills.append(skill_name)
        return skills
    except Exception as exc:  # noqa: BLE001
        # Degrade-OBSERVABLE, see _extract_files_touched.
        logger.warning(
            "_extract_skill_invocations failed, resume context omits it: %s", exc)
        return []


def _estimate_session_timespan(messages: list[dict]) -> str:
    """Estimate session duration from first and last message timestamps."""
    try:
        first_ts = messages[0].get("created_at", "")
        last_ts = messages[-1].get("created_at", "")
        if not first_ts or not last_ts:
            return ""
        # Parse ISO timestamps
        t0 = datetime.fromisoformat(first_ts)
        t1 = datetime.fromisoformat(last_ts)
        delta = t1 - t0
        minutes = int(delta.total_seconds() / 60)
        return f"{t0.strftime('%H:%M')} → {t1.strftime('%H:%M')} ({minutes} min)"
    except Exception:
        return ""


def _count_user_turns(messages: list[dict]) -> int:
    """Count user messages (= number of conversation turns)."""
    try:
        return sum(1 for m in messages if m.get("role") == "user")
    except Exception as exc:  # noqa: BLE001
        # Degrade-OBSERVABLE, see _extract_files_touched. 0 turns reads as a brand-new
        # session, which is the most misleading value this function can return.
        logger.warning("_count_user_turns failed, reporting 0 turns: %s", exc)
        return 0


# ─── NEW: Enrichment helpers (v2 — from shape to substance) ──────────


def _extract_assistant_conclusions(
    messages: list[dict], max_blocks: int = 100, char_budget: int = 0,
    block_trunc: int = 1500,
) -> list[str]:
    """Extract the text of the last N assistant messages (non-tool).

    These contain the agent's analysis, conclusions, and recommendations —
    the substance that the resumed agent needs to continue.
    Each block capped at ``block_trunc`` chars (floor 1500).

    Elastic caps (run_6d5f60dd): ``char_budget`` (when >0) governs collection —
    stop once accumulated chars reach it. ``max_blocks`` is a high secondary
    safety cap so it never binds before the char budget.
    """
    try:
        conclusions: list[str] = []
        accumulated = 0
        for msg in reversed(messages):
            if msg.get("role") != "assistant":
                continue
            text = _extract_text_from_content(msg.get("content"))
            if not text:
                continue
            block = text[:block_trunc]
            conclusions.append(block)
            accumulated += len(block)
            if len(conclusions) >= max_blocks:
                break
            if char_budget > 0 and accumulated >= char_budget:
                break
        conclusions.reverse()  # chronological order
        return conclusions
    except Exception:
        logger.debug("Assistant conclusions extraction failed", exc_info=True)
        return []


# Directive detection heuristic: short user messages that look like decisions.
# CJK words are matched separately — \b doesn't fire between CJK characters
# because they are all \w in Python regex (LL01/LL02).
_DIRECTIVE_WORDS = re.compile(
    r"\b(?:approve|approved|go|yes|do it|ship|commit|use|skip|defer|"
    r"ok|agreed|proceed|accept|confirm|implement|run|push|merge|"
    r"approach|option)\b"
    r"|(?:方案|批准|同意|跑|提交|用)",
    re.IGNORECASE,
)


def _extract_user_directives(
    messages: list[dict], max_directives: int = 100, char_budget: int = 0,
) -> list[str]:
    """Extract short user messages that look like decisions or directives.

    Heuristic: user messages ≤300 chars, not questions, containing action
    words.  Captures steering decisions ("approach B+A", "commit these").

    Elastic caps (run_6d5f60dd): ``char_budget`` (when >0) governs collection —
    stop once accumulated chars reach it. ``max_directives`` is a high secondary
    safety cap so it never binds before the char budget.

    Collected NEWEST-first then reversed for chronological display, so when a
    cap binds it keeps the most RECENT directives (a later steering decision
    usually supersedes an earlier one) — symmetric with conclusions.
    """
    try:
        directives: list[str] = []
        accumulated = 0
        for msg in reversed(messages):
            if msg.get("role") != "user":
                continue
            text = _extract_text_from_content(msg.get("content"))
            if not text or len(text) > 300:
                continue
            # Skip questions (end with ? or start with interrogative words)
            stripped = text.strip()
            if stripped.endswith("?"):
                continue
            if re.match(r"^(?:what|how|why|where|when|which|is|are|do|does|can|could)\b",
                        stripped, re.IGNORECASE):
                continue
            if _DIRECTIVE_WORDS.search(text):
                entry = text[:300]
                directives.append(entry)
                accumulated += len(entry)
            if len(directives) >= max_directives:
                break
            if char_budget > 0 and accumulated >= char_budget:
                break
        directives.reverse()  # chronological order
        return directives
    except Exception:
        logger.debug("User directives extraction failed", exc_info=True)
        return []


def _run_git_command(args: list[str], cwd: str, timeout: float = 3.0) -> str:
    """Run a git command synchronously with timeout.  Thread-safe.

    Returns stdout as string, or empty string on any failure.
    Called via ``executors.run_in('subprocess', ...)`` to avoid blocking the event loop.
    """
    try:
        result = _subprocess.run(
            args, cwd=cwd, capture_output=True, text=True,
            timeout=timeout,
        )
        if result.returncode != 0:
            return ""
        # Cap output to prevent memory issues on huge dirty repos
        out = result.stdout.strip()
        return out[:4000] if len(out) > 4000 else out
    except (_subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return ""
    except Exception:
        return ""


async def _extract_uncommitted_state(working_dir: str | None) -> str:
    """Run ``git status --short`` and ``git diff --stat`` to capture WIP.

    Uses ``executors.run_in('subprocess', ...)`` to avoid blocking the event loop.
    Returns empty string on any failure (timeout, not a git repo, etc.).
    """
    if not working_dir:
        return ""
    try:
        status = await executors.run_in(
            "subprocess",
            _run_git_command,
            ["git", "status", "--short"], working_dir, 3.0,
        )
        if not status:
            return ""

        diff_stat = await executors.run_in(
            "subprocess",
            _run_git_command,
            ["git", "diff", "--stat"], working_dir, 3.0,
        )

        parts: list[str] = []
        if status:
            parts.append(f"```\n{status}\n```")
        if diff_stat:
            parts.append(f"Diff stat:\n```\n{diff_stat}\n```")
        return "\n".join(parts)
    except Exception:
        logger.debug("Uncommitted state extraction failed", exc_info=True)
        return ""


def _merge_crash_checkpoint(checkpoint_path=None) -> str | None:
    """Read session_checkpoint.json and format for resume injection.

    Unlike ``recover_crash_checkpoint()`` which writes to DailyActivity,
    this reads the checkpoint data and includes it in the resume context.
    Does NOT delete the file — that's ``recover_crash_checkpoint()``'s job.

    Returns formatted string or None if no checkpoint exists.
    """
    if checkpoint_path is None:
        from jobs.paths import STATE_DIR as _state_dir_ci
        checkpoint_path = _state_dir_ci / "session_checkpoint.json"
    else:
        checkpoint_path = Path(checkpoint_path)

    if not checkpoint_path.exists():
        return None

    try:
        data = json.loads(checkpoint_path.read_text(encoding="utf-8"))
        tool_count = data.get("tool_count", 0)
        files = data.get("files_touched", [])
        corrections = data.get("corrections_count", 0)

        if not tool_count:
            return None

        parts = [f"⚠️ Prior session crashed after {tool_count} tool calls."]
        if files:
            parts.append(f"Files in progress: {', '.join(files[:15])}")
        if corrections:
            parts.append(f"{corrections} correction(s) were captured.")
        return " ".join(parts)
    except (json.JSONDecodeError, OSError, KeyError):
        return None
    except Exception:
        logger.debug("Crash checkpoint merge failed", exc_info=True)
        return None


def _extract_key_tool_results(
    messages: list[dict], max_results: int = 200,
    char_budget: int = 0, item_trunc: int = 300,
) -> str:
    """Extract detailed tool action summaries from ``tool_use.summary``.

    The Claude Agent SDK persists ``tool_use`` blocks with a ``summary``
    field (e.g. "Reading /path/to/file.py", "Running: git status") but
    does NOT persist ``tool_result`` blocks — those are consumed by the
    SDK internally and never reach the DB.  So we extract from summary.

    This gives the resumed agent a detailed action log: what was read,
    what commands were run, what was edited — richer than the checkpoint's
    compact "Tool activity: Read×9, Bash×4" stat line.

    Elastic caps (run_6d5f60dd): ``char_budget`` (when >0) governs collection
    — stop once accumulated chars reach it — so a complex session fills more
    of the token budget instead of being clipped at a fixed count. ``item_trunc``
    is the per-summary truncation (floor 300, scales up with budget). ``max_results``
    is a high secondary safety cap so it never binds before the char budget.
    """
    HIGH_VALUE_TOOLS = {"Read", "Grep", "Bash", "Agent", "Edit", "Write"}
    MIN_SUMMARY_LEN = 15  # skip trivially short summaries

    try:
        results: list[str] = []
        accumulated = 0  # chars collected, for char_budget early-exit
        seen_paths: set[str] = set()  # Deduplicate Read results by file path
        for msg in reversed(messages):
            content = msg.get("content")
            if not isinstance(content, list):
                continue
            for block in content:
                if not isinstance(block, dict) or block.get("type") != "tool_use":
                    continue
                name = block.get("name", "")
                if name not in HIGH_VALUE_TOOLS:
                    continue

                # Deduplicate Read tool results by file path — if the same
                # file was Read multiple times, only keep the most recent
                # (we iterate in reverse, so first seen = most recent).
                inp = block.get("input")
                if name == "Read" and isinstance(inp, dict):
                    fpath = inp.get("file_path", "")
                    if fpath:
                        if fpath in seen_paths:
                            continue
                        seen_paths.add(fpath)

                # DB path: summary field has the human-readable description
                summary = block.get("summary", "")
                if not summary or len(summary) < MIN_SUMMARY_LEN:
                    # Live path fallback: try to build from input dict
                    if isinstance(inp, dict) and inp:
                        summary = _compact_tool_args(inp)
                    if not summary or len(summary) < MIN_SUMMARY_LEN:
                        continue

                truncated = summary[:item_trunc]
                if len(summary) > item_trunc:
                    truncated += "..."
                entry = f"  → {name}: {truncated}"
                results.append(entry)
                accumulated += len(entry)

                if len(results) >= max_results:
                    break
                if char_budget > 0 and accumulated >= char_budget:
                    break
            if len(results) >= max_results:
                break
            if char_budget > 0 and accumulated >= char_budget:
                break

        if not results:
            return ""

        results.reverse()  # chronological order
        return "### Key Tool Actions\n" + "\n\n".join(results)
    except Exception:
        logger.debug("Key tool actions extraction failed", exc_info=True)
        return ""


def _trim_to_budget(
    sections: dict[str, str], token_budget: int
) -> dict[str, str]:
    """Trim assembled sections to fit within token budget.

    Trim priority (first to go → last to go):
    1. tool_results
    2. recent_turns
    3. uncommitted
    4. conclusions
    NEVER trim: checkpoint (files, git, directives, last request)

    Estimates tokens at 4 chars/token.
    """
    try:
        result = dict(sections)
        total_chars = sum(len(v) for v in result.values())
        budget_chars = token_budget * 4  # ~4 chars per token

        if total_chars <= budget_chars:
            return result

        # Trim in priority order. uncommitted is included (Gate-1 finding 2) so
        # it is governed by the budget instead of bypassing it; it trims after
        # recent_turns (more disposable than conclusions, less than raw turns).
        trim_order = ["tool_results", "recent_turns", "uncommitted", "conclusions"]
        for key in trim_order:
            if total_chars <= budget_chars:
                break
            val = result.get(key, "")
            if not val:
                continue
            excess = total_chars - budget_chars
            val_chars = len(val)
            if val_chars <= excess:
                # Remove entirely
                result[key] = ""
                total_chars -= val_chars
            else:
                # Truncate at last newline boundary to avoid garbled content
                keep = val_chars - excess
                cut_point = val.rfind('\n', 0, keep)
                if cut_point > 0:
                    result[key] = val[:cut_point] + "\n[trimmed to fit budget]"
                else:
                    result[key] = val[:keep] + "\n[trimmed to fit budget]"
                total_chars = budget_chars

        return result
    except Exception:
        return sections  # on error, return untrimmed


# ─── Checkpoint assembly ─────────────────────────────────────────────

def _build_checkpoint(messages: list[dict], directives_char_budget: int = 0) -> str:
    """Build a structured task checkpoint from raw messages.

    Pure mechanical extraction — no LLM calls.  Each section is
    independently guarded; partial checkpoints are valid.

    ``directives_char_budget`` bounds the user-directives sub-section (the
    checkpoint is UNtrimmable, so this layer must self-limit — Gate-1 finding 2).

    Returns empty string only if all extractions fail.
    """
    sections: list[str] = []

    # 1. Last user request (= the task)
    last_request = _find_last_user_text(messages)
    if last_request:
        sections.append(f"**Last request:** {last_request}")

    # 2. Session stats
    timespan = _estimate_session_timespan(messages)
    turns = _count_user_turns(messages)
    if timespan or turns:
        stats_parts = []
        if turns:
            stats_parts.append(f"{turns} turns")
        if timespan:
            stats_parts.append(timespan)
        sections.append(f"**Session:** {', '.join(stats_parts)}")

    # 3. Tool usage summary (last 40 messages)
    tool_summary = _extract_tool_summary(messages[-40:])

    # 4. Files touched
    files = _extract_files_touched(tool_summary)
    if files:
        sections.append(f"**Files touched:** {', '.join(files)}")

    # 5. Git commits
    commits = _extract_git_activity(messages)
    if commits:
        commit_lines = "\n".join(f"  - {c}" for c in commits)
        sections.append(f"**Git commits:**\n{commit_lines}")

    # 6. Sub-agent tasks
    spawns = _extract_agent_spawns(messages[-20:])
    if spawns:
        spawn_lines = "\n".join(f"  - {s}" for s in spawns)
        sections.append(f"**Sub-tasks spawned:**\n{spawn_lines}")

    # 7. Skills used
    skills = _extract_skill_invocations(messages)
    if skills:
        sections.append(f"**Skills used:** {', '.join(skills)}")

    # 8. Key tool stats
    tool_counts = {name: len(args) for name, args in tool_summary.items()}
    if tool_counts:
        top_tools = sorted(tool_counts.items(), key=lambda x: -x[1])[:6]
        tool_str = ", ".join(f"{name}×{count}" for name, count in top_tools)
        sections.append(f"**Tool activity:** {tool_str}")

    # 9. User directives (short steering decisions)
    directives = _extract_user_directives(
        messages, char_budget=directives_char_budget,
    )
    if directives:
        dir_lines = "\n".join(f"  - \"{d}\"" for d in directives)
        sections.append(f"**User directives:**\n{dir_lines}")

    # 10. Crash checkpoint (if prior session crashed)
    crash_info = _merge_crash_checkpoint()
    if crash_info:
        sections.append(f"**Crash recovery:** {crash_info}")

    if not sections:
        return ""

    return "### Task Checkpoint\n" + "\n".join(sections)


# ─── Recent turns formatting ─────────────────────────────────────────

def _format_recent_turns(
    messages: list[dict], max_turns: int = 500, budget_chars: int = 0,
    recent_chars: int = 0,
) -> str:
    """Format the last N user-assistant turn pairs.

    4K per message preserves detailed assistant reasoning, code snippets,
    and multi-paragraph analyses.  First principle: the resumed agent should
    feel like it was there.

    Elastic caps (run_6d5f60dd): ``recent_chars`` (when >0) is this layer's
    explicit char budget from ``_compute_layer_caps`` — collect newest-first
    until accumulated chars reach it. Falls back to 60% of ``budget_chars``
    (legacy behaviour) when ``recent_chars`` is 0. ``max_turns`` is a high
    secondary safety cap. The in-loop early-break is PRESERVED (Gate-1
    finding 4) — we never format-all-then-trim.
    """
    try:
        filtered = _filter_tool_only_messages(messages)
        # Drop last assistant message (anti-duplication, same as legacy)
        if filtered and filtered[-1].get("role") == "assistant":
            filtered = filtered[:-1]

        # Take last max_turns * 2 messages (pairs of user + assistant)
        recent = filtered[-(max_turns * 2):]

        # Per-layer char budget (elastic) takes precedence; else legacy 60%.
        if recent_chars > 0:
            size_limit = recent_chars
        else:
            size_limit = int(budget_chars * 0.6) if budget_chars > 0 else 0

        # Collect NEWEST-first so the budget keeps the most-recent turns, then
        # reverse for chronological display. (Iterating `recent` forward and
        # breaking early would keep the OLDEST turns within the window — wrong
        # direction now that max_turns is a high cap, not the recency selector.)
        formatted: list[str] = []
        accumulated = 0
        for msg in reversed(recent):
            text = _format_message(msg)
            if text is not None:
                # Cap each message at 4000 chars — preserves reasoning
                if len(text) > 4000:
                    text = text[:3997] + "..."
                formatted.append(text)
                accumulated += len(text)
                # Stop collecting once we have enough for the budget
                if size_limit and accumulated >= size_limit:
                    break

        if not formatted:
            return ""

        formatted.reverse()  # restore chronological order for display
        return "### Recent Conversation\n" + "\n\n".join(formatted)
    except Exception:
        logger.debug("Recent turns formatting failed", exc_info=True)
        return ""


# ─── Budget computation ──────────────────────────────────────────────

def _compute_resume_budget(
    model_context_window: int, is_channel: bool = False
) -> tuple[int, int, int]:
    """Compute resume context limits scaled to model context window.

    Generous budgets — first principle: the resumed agent should feel like
    it was there.  On a 1M model, 150K of resume context leaves 850K for
    everything else.  Being stingy with 2% was solving a problem we don't
    have.  (KD24: "Token saving is NEVER the primary concern.")

    Returns:
        Tuple of ``(token_budget, max_messages, db_fetch_limit)``.
    """
    if is_channel:
        return (32_000, 50, 120)

    if model_context_window >= 500_000:
        return (150_000, 500, 1000)
    elif model_context_window >= 200_000:
        return (60_000, 200, 500)
    else:
        return (20_000, 80, 200)


# Per-layer char-budget shares of the total token budget (run_6d5f60dd).
# These sum to 0.85 — deliberately < 1.0 so the UNTRIMMABLE checkpoint base
# + uncommitted git state (Gate-1 finding 2) have ~15% headroom before the
# final clamp engages. Larger shares would push checkpoint+layers past the
# budget on every large session. recent_turns dominates (it carries the
# actual conversational substance), tool_results next, then the smaller
# conclusions/directives layers.
_LAYER_SHARES = {
    "recent_chars": 0.45,
    "tool_results_chars": 0.25,
    "conclusions_chars": 0.10,
    "directives_chars": 0.05,
}
# Per-ITEM truncation floor — today's value, NEVER regress below it even on a
# tiny (channel) budget (Gate-1 finding 3: the floor is per-ITEM, not a
# per-layer aggregate; a per-layer floor = today's max would blow the 32K
# channel). Scales UP with budget so a large session keeps more of each
# tool-result summary.
_TOOL_ITEM_TRUNC_FLOOR = 300
_TOOL_ITEM_TRUNC_MAX = 4_000


def _compute_layer_caps(token_budget: int) -> dict[str, int]:
    """Derive per-layer CHAR budgets from the token budget (elastic caps).

    The resume under-fill fix (run_6d5f60dd): extraction used FIXED
    item-counts (conclusions 5, directives 10, tool_results 15, turns 30)
    that capped a 972-message session at ~6K tokens even with a 150K budget
    (~4% utilisation). These caps make each layer's size a pure function of
    the budget — complex sessions fill toward the budget, simple stay lean,
    channel (32K) stays small — while ``_trim_to_budget`` + the final clamp
    remain the hard safety bound.

    Returns a dict of char budgets per layer + the per-item tool truncation:
    ``recent_chars``, ``tool_results_chars``, ``conclusions_chars``,
    ``directives_chars``, ``tool_item_trunc``.

    Monotonic in ``token_budget`` (larger budget → larger caps), so channel
    < 200K-model < 1M-model automatically.
    """
    budget_chars = max(0, token_budget) * 4  # ~4 chars per token
    caps = {
        key: int(budget_chars * share)
        for key, share in _LAYER_SHARES.items()
    }
    # Per-item tool-summary truncation: floor 300 (today), scales up with the
    # tool_results layer budget, capped so a single summary can't dominate.
    caps["tool_item_trunc"] = max(
        _TOOL_ITEM_TRUNC_FLOOR,
        min(_TOOL_ITEM_TRUNC_MAX, caps["tool_results_chars"] // 12),
    )
    return caps


_CLAMP_MARKER = "\n[resume context clamped to budget]"


def _hard_clamp(text: str, max_chars: int) -> tuple[str, bool]:
    """Inviolable backstop: bound ``text`` to ``max_chars`` chars INCLUSIVE of
    the clamp marker (Gate-2 finding 7 — the marker length is reserved before
    slicing so the returned string is never longer than ``max_chars``).

    Cuts on the last newline before the body limit (clean section boundary);
    falls back to a hard cut for a single giant line. Returns
    ``(clamped_text, did_clamp)`` — ``did_clamp`` False (text unchanged) when
    already within budget. Guarantee: ``len(out) <= max_chars`` always, for
    any ``max_chars >= len(_CLAMP_MARKER)``.
    """
    if len(text) <= max_chars:
        return text, False
    body_limit = max(0, max_chars - len(_CLAMP_MARKER))
    cut = text.rfind("\n", 0, body_limit)
    if cut <= 0:
        cut = body_limit
    return text[:cut] + _CLAMP_MARKER, True


# ─── Legacy assembly (kept for backward compat + fallback) ───────────

def _assemble_context(messages: list[str], was_truncated: bool) -> str:
    """Wrap formatted messages in section header and preamble.

    Kept for backward compatibility with existing tests and callers.
    """
    if not messages:
        return ""

    parts: list[str] = ["## Previous Conversation Context", "", _LEGACY_PREAMBLE]
    if was_truncated:
        parts.append("")
        parts.append(_TRUNCATION_NOTE)
    for msg in messages:
        parts.append("")
        parts.append(msg)
    return "\n".join(parts)


# ─── Legacy raw-history builder (fallback) ───────────────────────────

def _build_legacy_context(raw_messages: list[dict], max_messages: int,
                          token_budget: int) -> str:
    """Original raw-history injection — used as fallback."""
    filtered = _filter_tool_only_messages(raw_messages)
    if filtered and filtered[-1].get("role") == "assistant":
        filtered = filtered[:-1]
    recent = filtered[-max_messages:]

    formatted: list[str] = []
    for msg in recent:
        text = _format_message(msg)
        if text is not None:
            formatted.append(text)

    if not formatted:
        return ""

    surviving, was_truncated = _apply_token_budget(formatted, token_budget)
    return _assemble_context(surviving, was_truncated)


# ─── Per-session checkpoint cache ────────────────────────────────────
# Key: session_id, Value: (msg_count_at_build_time, result_string).
# Messages are append-only → count change = cache invalid.
# LRU eviction: cap at 50 entries to prevent unbounded growth in
# long-running daemon.  OrderedDict for O(1) eviction.

_RESUME_CACHE_MAX = 10  # 10 sessions × ~600KB ≈ 6MB max (was 50 = 30MB)
_resume_cache: OrderedDict[str, tuple[int, str]] = OrderedDict()


# ─── Resume-context observability counters (R1: fail-loud not fail-hard) ──
# Every build_resume_context() outcome bumps a DISTINCT key here. The whole
# point: a data-loss EXCEPTION (failed_exception) must never be conflated with
# a legitimate-empty result (empty_*). All four used to return "" identically —
# silent data loss masquerading as "no context to inject". This counter makes
# the failure observable + distinguishable WITHOUT changing the return contract
# (still str, empty-string semantics preserved — startup is never blocked).
# Mirrors _resume_cache: module-level, advisory (never gates control flow).
# int += under the GIL is atomic for these single-op increments.
_resume_stats: dict[str, int] = {
    "cache_hit": 0,
    "enriched_success": 0,
    "legacy_success": 0,
    "empty_no_session": 0,
    "empty_no_messages": 0,
    "empty_legacy": 0,
    "failed_exception": 0,
    # Enrichment helpers (checkpoint/conclusions/tool-results/git) raised and we
    # fell back to legacy raw-history. NOT a clean build — the rich context was
    # LOST and silently degraded. Distinct from failed_exception (loses
    # everything) and enriched_success (clean build). Without this key a degraded
    # build is indistinguishable from a clean one — the same conflation R1 fixes
    # at the top level, one layer down (adversarial MED, run_fe0122b5).
    "enrichment_degraded": 0,
}


def get_resume_stats() -> dict[str, int]:
    """Return a COPY of the resume-context outcome counters (observability).

    Backend can grep/expose these to tell "resume produced no context because
    there was nothing to inject" (empty_*) apart from "resume FAILED and we lost
    the context" (failed_exception) — the distinction the silent "" hid.
    """
    return dict(_resume_stats)


def reset_resume_stats() -> None:
    """Zero all counters. Used for test isolation (no daemon caller — counters
    are cumulative over process lifetime; Python ints are arbitrary-precision so
    unbounded growth is not an overflow/memory risk)."""
    for k in _resume_stats:
        _resume_stats[k] = 0


# ─── Public API ──────────────────────────────────────────────────────

async def build_resume_context(
    app_session_id: str,
    model_context_window: int = 200_000,
    max_messages: int | None = None,
    db_fetch_limit: int | None = None,
    token_budget: int | None = None,
    is_channel: bool = False,
    working_directory: str | None = None,
) -> str:
    """Load recent messages and build enriched resume context for system prompt.

    Five-layer approach (from shape to substance):
    1. **Structured checkpoint** (~5-10K) — task state, files, git activity,
       user directives, crash checkpoint merge.
    2. **Uncommitted state** (~500) — ``git status --short`` + diff stat.
    3. **Assistant conclusions** (~5-8K) — last 5 assistant text blocks.
    4. **Key tool results** (~5-15K) — truncated output from Read/Grep/Bash.
    5. **Recent conversation** (~20-60K) — last 30 turn pairs × 4K chars.

    Budget is model-aware and generous (150K for 1M models).
    Trimming priority: tool_results → recent_turns → conclusions.
    Checkpoint + uncommitted state are never trimmed.

    Falls back to legacy raw-history injection if the structured path
    produces nothing.

    Args:
        app_session_id: The stable tab-level session ID to query.
        model_context_window: Model's context window in tokens.
        max_messages: Max messages for legacy fallback path.
        db_fetch_limit: Messages to fetch from DB.
        token_budget: Token budget for legacy fallback path.
        is_channel: Channel session (tighter budget).

    Returns:
        Formatted context string, or ``""`` on error/no messages.
    """
    auto_budget, auto_max, auto_fetch = _compute_resume_budget(
        model_context_window, is_channel=is_channel
    )
    token_budget = token_budget if token_budget is not None else auto_budget
    max_messages = max_messages if max_messages is not None else auto_max
    db_fetch_limit = db_fetch_limit if db_fetch_limit is not None else auto_fetch
    if app_session_id is None:
        _resume_stats["empty_no_session"] += 1
        return ""

    try:
        from database import db

        # ── Cache check: skip DB fetch if msg_count unchanged ──
        msg_count = await db.messages.count_by_session(app_session_id)
        cached = _resume_cache.get(app_session_id)
        if cached and cached[0] == msg_count:
            _resume_cache.move_to_end(app_session_id)  # LRU touch
            _resume_stats["cache_hit"] += 1
            logger.info("Resume context cache hit: session=%s count=%d",
                        app_session_id[:12], msg_count)
            return cached[1]

        raw_messages = await db.messages.list_by_session_paginated(
            app_session_id, limit=db_fetch_limit
        )

        if not raw_messages:
            _resume_stats["empty_no_messages"] += 1
            logger.info("Resume context skipped: no messages for session %s",
                        app_session_id)
            return ""

        # ── Try enriched structured checkpoint + layers ──────────
        checkpoint = ""
        conclusions_text = ""
        tool_results_text = ""
        recent = ""
        uncommitted = ""
        try:
            budget_chars = token_budget * 4  # ~4 chars per token
            # Elastic per-layer char budgets (run_6d5f60dd): each layer fills
            # toward its share of the budget instead of a fixed item count, so a
            # complex session uses the generous budget while a simple one stays
            # lean. _trim_to_budget + the final clamp remain the hard bound.
            layer_caps = _compute_layer_caps(token_budget)
            checkpoint = _build_checkpoint(
                raw_messages,
                directives_char_budget=layer_caps["directives_chars"],
            )
            recent = _format_recent_turns(
                raw_messages, budget_chars=budget_chars,
                recent_chars=layer_caps["recent_chars"],
            )

            # Enrichment layer: assistant conclusions
            conclusions = _extract_assistant_conclusions(
                raw_messages, char_budget=layer_caps["conclusions_chars"],
            )
            if conclusions:
                conclusion_lines = "\n\n".join(
                    f"**[{i+1}]** {c}" for i, c in enumerate(conclusions)
                )
                conclusions_text = f"### Assistant Conclusions\n{conclusion_lines}"

            # Enrichment layer: key tool results
            tool_results_text = _extract_key_tool_results(
                raw_messages,
                char_budget=layer_caps["tool_results_chars"],
                item_trunc=layer_caps["tool_item_trunc"],
            )

            # Enrichment layer: uncommitted git state (async, with timeout)
            # Use caller-provided working_directory; fall back to SwarmWS default
            from jobs.paths import SWARMWS as _swarmws_ci
            ws_dir = working_directory or str(_swarmws_ci)
            uncommitted = await _extract_uncommitted_state(ws_dir)
        except Exception:
            # Enrichment failed → we'll fall through to legacy raw-history (or
            # empty). Count it: a degraded build (rich context LOST) must be
            # distinguishable from a clean enriched_success or a legitimate
            # empty_legacy — else the conflation R1 fixes recurs here.
            _resume_stats["enrichment_degraded"] += 1
            logger.warning("Structured checkpoint extraction failed — resume "
                           "context degraded to legacy raw-history (rich "
                           "context lost for this session)",
                           exc_info=True)

        has_content = any([checkpoint, recent, conclusions_text,
                           tool_results_text, uncommitted])
        if has_content:
            # Assemble sections. ``uncommitted`` is included in the trim view
            # (Gate-1 finding 2) so it cannot bypass the budget — previously it
            # was appended raw, invisible to _trim_to_budget.
            uncommitted_section = (
                f"### Uncommitted Changes\n{uncommitted}" if uncommitted else ""
            )
            raw_sections = {
                "checkpoint": checkpoint,
                "conclusions": conclusions_text,
                "tool_results": tool_results_text,
                "recent_turns": recent,
                "uncommitted": uncommitted_section,
            }

            # Apply budget trimming
            trimmed = _trim_to_budget(raw_sections, token_budget)

            parts = [_SECTION_HEADER, "", _CHECKPOINT_PREAMBLE]
            if trimmed.get("checkpoint"):
                parts.append("")
                parts.append(trimmed["checkpoint"])
            if trimmed.get("uncommitted"):
                parts.append("")
                parts.append(trimmed["uncommitted"])
            if trimmed.get("conclusions"):
                parts.append("")
                parts.append(trimmed["conclusions"])
            if trimmed.get("tool_results"):
                parts.append("")
                parts.append(trimmed["tool_results"])
            if trimmed.get("recent_turns"):
                parts.append("")
                parts.append(trimmed["recent_turns"])

            result = "\n".join(parts)

            # Final hard clamp (Gate-1 finding 2): _trim_to_budget never trims
            # the checkpoint, so checkpoint + uncommitted alone could exceed the
            # budget. This is the inviolable backstop.
            clamped_result, did_clamp = _hard_clamp(result, token_budget * 4)
            if did_clamp:
                result = clamped_result
                _resume_stats["clamped"] = _resume_stats.get("clamped", 0) + 1
                logger.warning(
                    "Resume context hit hard clamp: %d > %d chars (budget=%d tok) "
                    "— checkpoint+uncommitted dominate",
                    len("\n".join(parts)), token_budget * 4, token_budget,
                )
            _resume_cache[app_session_id] = (msg_count, result)
            if len(_resume_cache) > _RESUME_CACHE_MAX:
                _resume_cache.popitem(last=False)  # evict oldest
            _resume_stats["enriched_success"] += 1
            logger.info(
                "Resume context built (enriched): checkpoint=%d "
                "conclusions=%d tool_results=%d recent=%d "
                "uncommitted=%d total=~%d tokens",
                len(checkpoint), len(conclusions_text),
                len(tool_results_text), len(recent),
                len(uncommitted), len(result) // 4,
            )
            return result

        # ── Fallback: legacy raw-history injection ────────────────
        logger.info("Structured resume empty — falling back to legacy "
                    "raw-history injection")
        result = _build_legacy_context(raw_messages, max_messages, token_budget)
        if result:
            _resume_cache[app_session_id] = (msg_count, result)
            if len(_resume_cache) > _RESUME_CACHE_MAX:
                _resume_cache.popitem(last=False)  # evict oldest
            _resume_stats["legacy_success"] += 1
            logger.info(
                "Resume context built (legacy fallback): ~%d tokens",
                len(result) // 4,
            )
        else:
            _resume_stats["empty_legacy"] += 1
            logger.info("Resume context skipped: no injectable messages")
        return result

    except Exception:
        # fail-LOUD (ERROR, not WARNING) + fail-SOFT (still return "" so the
        # KNOWLEDGE.md law holds: context-load failure NEVER blocks agent
        # startup). The counter is what makes this distinguishable from the
        # legitimate-empty paths above — that conflation WAS the silent
        # data-loss bug (R1). We return "" exactly as before; only the
        # observability (ERROR log + failed_exception counter) is new.
        _resume_stats["failed_exception"] += 1
        logger.error(
            "Resume context BUILD FAILED for session %s — context lost, "
            "session will resume with NO prior context (fail-soft). This is a "
            "data-loss event, not a legitimate-empty result.",
            app_session_id,
            exc_info=True,
        )
        return ""
