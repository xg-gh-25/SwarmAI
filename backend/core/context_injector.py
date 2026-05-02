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

import asyncio
import json
import logging
import re
import subprocess as _subprocess
from datetime import datetime
from collections import OrderedDict
from pathlib import Path

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
    _ws_root = str(Path.home() / ".swarm-ai" / "SwarmWS") + "/"
    _swarmai_root = str(Path.home() / "Desktop" / "SwarmAI-Workspace" / "swarmai") + "/"

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
    except Exception:
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
    except Exception:
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
    except Exception:
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
    except Exception:
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
    except Exception:
        return 0


# ─── NEW: Enrichment helpers (v2 — from shape to substance) ──────────


def _extract_assistant_conclusions(
    messages: list[dict], max_blocks: int = 5
) -> list[str]:
    """Extract the text of the last N assistant messages (non-tool).

    These contain the agent's analysis, conclusions, and recommendations —
    the substance that the resumed agent needs to continue.
    Each block capped at 1500 chars.
    """
    try:
        conclusions: list[str] = []
        for msg in reversed(messages):
            if msg.get("role") != "assistant":
                continue
            text = _extract_text_from_content(msg.get("content"))
            if not text:
                continue
            conclusions.append(text[:1500])
            if len(conclusions) >= max_blocks:
                break
        conclusions.reverse()  # chronological order
        return conclusions
    except Exception:
        logger.debug("Assistant conclusions extraction failed", exc_info=True)
        return []


# Directive detection heuristic: short user messages that look like decisions
_DIRECTIVE_WORDS = re.compile(
    r"\b(?:approve|approved|go|yes|do it|ship|commit|use|skip|defer|"
    r"ok|agreed|proceed|accept|confirm|implement|run|push|merge|"
    r"approach|option|方案|批准|同意|跑|提交|用)\b",
    re.IGNORECASE,
)


def _extract_user_directives(
    messages: list[dict], max_directives: int = 10
) -> list[str]:
    """Extract short user messages that look like decisions or directives.

    Heuristic: user messages ≤300 chars, not questions, containing action
    words.  Captures steering decisions ("approach B+A", "commit these").
    """
    try:
        directives: list[str] = []
        for msg in messages:
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
                directives.append(text[:300])
            if len(directives) >= max_directives:
                break
        return directives
    except Exception:
        logger.debug("User directives extraction failed", exc_info=True)
        return []


def _run_git_command(args: list[str], cwd: str, timeout: float = 3.0) -> str:
    """Run a git command synchronously with timeout.  Thread-safe.

    Returns stdout as string, or empty string on any failure.
    Called via ``asyncio.to_thread()`` to avoid blocking the event loop.
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

    Uses ``asyncio.to_thread()`` to avoid blocking the event loop.
    Returns empty string on any failure (timeout, not a git repo, etc.).
    """
    if not working_dir:
        return ""
    try:
        status = await asyncio.to_thread(
            _run_git_command,
            ["git", "status", "--short"], working_dir, 3.0,
        )
        if not status:
            return ""

        diff_stat = await asyncio.to_thread(
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
        checkpoint_path = Path.home() / ".swarm-ai" / ".context" / "session_checkpoint.json"
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
    messages: list[dict], max_results: int = 15
) -> str:
    """Extract detailed tool action summaries from ``tool_use.summary``.

    The Claude Agent SDK persists ``tool_use`` blocks with a ``summary``
    field (e.g. "Reading /path/to/file.py", "Running: git status") but
    does NOT persist ``tool_result`` blocks — those are consumed by the
    SDK internally and never reach the DB.  So we extract from summary.

    This gives the resumed agent a detailed action log: what was read,
    what commands were run, what was edited — richer than the checkpoint's
    compact "Tool activity: Read×9, Bash×4" stat line.
    """
    HIGH_VALUE_TOOLS = {"Read", "Grep", "Bash", "Agent", "Edit", "Write"}
    MIN_SUMMARY_LEN = 15  # skip trivially short summaries

    try:
        results: list[str] = []
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

                # DB path: summary field has the human-readable description
                summary = block.get("summary", "")
                if not summary or len(summary) < MIN_SUMMARY_LEN:
                    # Live path fallback: try to build from input dict
                    inp = block.get("input")
                    if isinstance(inp, dict) and inp:
                        summary = _compact_tool_args(inp)
                    if not summary or len(summary) < MIN_SUMMARY_LEN:
                        continue

                truncated = summary[:300]
                if len(summary) > 300:
                    truncated += "..."
                results.append(f"  → {name}: {truncated}")

                if len(results) >= max_results:
                    break
            if len(results) >= max_results:
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
    3. conclusions
    NEVER trim: checkpoint (files, git, directives, last request)

    Estimates tokens at 4 chars/token.
    """
    try:
        result = dict(sections)
        total_chars = sum(len(v) for v in result.values())
        budget_chars = token_budget * 4  # ~4 chars per token

        if total_chars <= budget_chars:
            return result

        # Trim in priority order
        trim_order = ["tool_results", "recent_turns", "conclusions"]
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

def _build_checkpoint(messages: list[dict]) -> str:
    """Build a structured task checkpoint from raw messages.

    Pure mechanical extraction — no LLM calls.  Each section is
    independently guarded; partial checkpoints are valid.

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
    directives = _extract_user_directives(messages)
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

def _format_recent_turns(messages: list[dict], max_turns: int = 30) -> str:
    """Format the last N user-assistant turn pairs.

    30 turns covers most productive sessions.  4K per message preserves
    detailed assistant reasoning, code snippets, and multi-paragraph
    analyses.  First principle: the resumed agent should feel like it
    was there.
    """
    try:
        filtered = _filter_tool_only_messages(messages)
        # Drop last assistant message (anti-duplication, same as legacy)
        if filtered and filtered[-1].get("role") == "assistant":
            filtered = filtered[:-1]

        # Take last max_turns * 2 messages (pairs of user + assistant)
        recent = filtered[-(max_turns * 2):]

        formatted: list[str] = []
        for msg in recent:
            text = _format_message(msg)
            if text is not None:
                # Cap each message at 4000 chars — preserves reasoning
                if len(text) > 4000:
                    text = text[:3997] + "..."
                formatted.append(text)

        if not formatted:
            return ""

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


# ─── Public API ──────────────────────────────────────────────────────

async def build_resume_context(
    app_session_id: str,
    model_context_window: int = 200_000,
    max_messages: int | None = None,
    db_fetch_limit: int | None = None,
    token_budget: int | None = None,
    is_channel: bool = False,
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
        return ""

    try:
        from database import db

        # ── Cache check: skip DB fetch if msg_count unchanged ──
        msg_count = await db.messages.count_by_session(app_session_id)
        cached = _resume_cache.get(app_session_id)
        if cached and cached[0] == msg_count:
            _resume_cache.move_to_end(app_session_id)  # LRU touch
            logger.info("Resume context cache hit: session=%s count=%d",
                        app_session_id[:12], msg_count)
            return cached[1]

        raw_messages = await db.messages.list_by_session_paginated(
            app_session_id, limit=db_fetch_limit
        )

        if not raw_messages:
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
            checkpoint = _build_checkpoint(raw_messages)
            recent = _format_recent_turns(raw_messages, max_turns=30)

            # Enrichment layer: assistant conclusions
            conclusions = _extract_assistant_conclusions(raw_messages, max_blocks=5)
            if conclusions:
                conclusion_lines = "\n\n".join(
                    f"**[{i+1}]** {c}" for i, c in enumerate(conclusions)
                )
                conclusions_text = f"### Assistant Conclusions\n{conclusion_lines}"

            # Enrichment layer: key tool results
            tool_results_text = _extract_key_tool_results(raw_messages, max_results=15)

            # Enrichment layer: uncommitted git state (async, with timeout)
            ws_dir = str(Path.home() / ".swarm-ai" / "SwarmWS")
            uncommitted = await _extract_uncommitted_state(ws_dir)
        except Exception:
            logger.warning("Structured checkpoint extraction failed",
                           exc_info=True)

        has_content = any([checkpoint, recent, conclusions_text,
                           tool_results_text, uncommitted])
        if has_content:
            # Assemble sections
            raw_sections = {
                "checkpoint": checkpoint,
                "conclusions": conclusions_text,
                "tool_results": tool_results_text,
                "recent_turns": recent,
            }

            # Apply budget trimming
            trimmed = _trim_to_budget(raw_sections, token_budget)

            parts = [_SECTION_HEADER, "", _CHECKPOINT_PREAMBLE]
            if trimmed.get("checkpoint"):
                parts.append("")
                parts.append(trimmed["checkpoint"])
            if uncommitted:
                parts.append("")
                parts.append(f"### Uncommitted Changes\n{uncommitted}")
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
            _resume_cache[app_session_id] = (msg_count, result)
            if len(_resume_cache) > _RESUME_CACHE_MAX:
                _resume_cache.popitem(last=False)  # evict oldest
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
            logger.info(
                "Resume context built (legacy fallback): ~%d tokens",
                len(result) // 4,
            )
        else:
            logger.info("Resume context skipped: no injectable messages")
        return result

    except Exception:
        logger.warning(
            "Failed to build resume context for session %s",
            app_session_id,
            exc_info=True,
        )
        return ""
