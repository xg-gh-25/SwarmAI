"""Security hooks for the agent execution environment.

This module provides hook factory functions used by SessionUnit to enforce
security policies during agent execution.  Each hook is composed into the
Claude Agent SDK's hook system via HookMatcher configurations.

Public symbols
--------------
- ``pre_tool_logger``                        — logs every tool invocation
- ``DEFAULT_DANGEROUS_PATTERNS``             — default glob patterns for dangerous commands
- ``load_dangerous_patterns``                — load patterns from ~/.swarm-ai/dangerous_commands.json
- ``create_dangerous_command_gate``          — single PreToolUse gate for Bash commands
- ``create_file_access_permission_handler``  — workspace file-path sandbox
- ``create_skill_access_checker``            — skill allow-list enforcement
- ``create_governance_file_gate``            — Three-Layer Governance file write interception
- ``GOVERNANCE_TIER1_PATTERNS``              — Tier 1 (Constitutional) file patterns
- ``GOVERNANCE_TIER2_PATTERNS``              — Tier 2 (Statutory) file patterns
"""

import fnmatch
import json
import logging
import os
import platform
import re
import shlex
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable
from uuid import uuid4

from config import get_app_data_dir
from .ask_question_manager import (
    TIMEOUT_SENTINEL as ASK_TIMEOUT_SENTINEL,
    ASK_ANSWER_TIMEOUT_SECONDS,
)

if TYPE_CHECKING:
    from .permission_manager import PermissionManager
    from .ask_question_manager import AskQuestionManager

logger = logging.getLogger(__name__)


async def pre_tool_logger(
    input_data: dict[str, Any],
    tool_use_id: str | None,
    context: Any
) -> dict[str, Any]:
    """Log tool usage before execution."""
    tool_name = input_data.get('tool_name', 'unknown')
    tool_input = input_data.get('tool_input', {})
    logger.info(f"[PRE-TOOL] Tool: {tool_name}, Input keys: {list(tool_input.keys())}")
    return {"decision": "approve"}


# ---------------------------------------------------------------------------
# Dangerous command gate — single permission layer
# ---------------------------------------------------------------------------

DEFAULT_DANGEROUS_PATTERNS: list[str] = [
    # NOTE: the blanket "rm -rf *" glob was removed (fix #3). A glob cannot
    # express "block / but allow /tmp", so recursive-rm danger is judged by the
    # fail-closed `_is_dangerous_rm` predicate below — which allows harmless
    # temp cleanups (/tmp, /var/folders) but blocks every other recursive rm.
    "sudo *",
    "chmod 777 *",
    "chmod -R 777 *",
    "chown -R * /",
    "kill -9 *",
    "mkfs.*",
    "dd if=*",
    "curl *|bash*",
    "curl *|sh*",
    "wget *|bash*",
    "wget *|sh*",
    "> /dev/sda*",
    "> /dev/hda*",
    "> /dev/nvme*",
    "> /dev/vda*",
    "> /etc/*",
    ":()*{*:*|*:*&*}*;*:*",
]

# Prefixes under which a recursive rm is considered harmless (OS temp dirs).
# Everything else is fail-closed dangerous.  macOS per-user temp lives under
# /var/folders (and the /private symlink); Linux/general temp is /tmp.
_SAFE_RM_PREFIXES: tuple[str, ...] = (
    "/tmp/",
    "/var/folders/",
    "/private/var/folders/",
    "/private/tmp/",
)
_SAFE_RM_EXACT: frozenset[str] = frozenset({"/tmp", "/private/tmp"})


def _is_dangerous_rm(command: str) -> bool:
    """Fail-closed predicate: is this a *dangerous* recursive ``rm``?

    Replaces the blanket ``rm -rf *`` glob (fix #3), which forced an approval
    prompt on every recursive rm — including harmless temp cleanups like
    ``rm -rf /tmp/build``.

    Rules:
    - Only ``rm`` invocations with BOTH recursive and force flags (``-rf`` /
      ``-r -f`` / ``-fr`` etc.) are candidates. Plain ``rm file`` is not the
      catastrophic pattern and is left to normal flow.
    - Dangerous UNLESS *every* path operand resolves under a known-safe temp
      prefix. Any operand that is a root/home/unknown/relative/glob target →
      dangerous (fail closed). Zero path operands → dangerous (e.g. ``rm -rf *``
      where the shell would expand cwd).
    - Non-rm commands always return False (other patterns judge those).
    """
    try:
        tokens = shlex.split(command)
    except ValueError:
        # Unparseable (unbalanced quotes) → cannot prove safe → fail closed,
        # but only if it actually looks like an rm command.
        return command.strip().startswith("rm ")
    if not tokens or tokens[0] != "rm":
        return False

    flags: set[str] = set()
    operands: list[str] = []
    for tok in tokens[1:]:
        if tok.startswith("-") and tok != "-":
            # collect single-letter flags (handles -rf, -fr, -r, -f, --recursive)
            if tok.startswith("--"):
                flags.add(tok)
            else:
                flags.update(tok[1:])
        else:
            operands.append(tok)

    recursive = ("r" in flags) or ("R" in flags) or ("--recursive" in flags)
    force = ("f" in flags) or ("--force" in flags)
    if not (recursive and force):
        # Not the `rm -rf` catastrophic shape — not this predicate's concern.
        return False

    if not operands:
        # `rm -rf` with no explicit target (or only globs the shell expands in
        # cwd) — cannot prove safe → dangerous.
        return True

    for op in operands:
        # Fail closed on any path-traversal or env/glob token — a ".." segment
        # can escape a safe prefix (rm -rf /tmp/../etc), and $VARS/globs are
        # unexpanded here so their real target is unknown.
        if ".." in op.split("/") or "$" in op or "~" in op:
            return True
        norm = os.path.normpath(op)
        if norm in _SAFE_RM_EXACT:
            continue
        if norm.startswith(_SAFE_RM_PREFIXES):
            continue
        # root, home, relative paths, globs, unknown absolute → dangerous.
        return True
    return False


def load_dangerous_patterns() -> list[str]:
    """Load glob patterns from ``~/.swarm-ai/dangerous_commands.json``.

    Creates the file with ``DEFAULT_DANGEROUS_PATTERNS`` if missing.
    Falls back to defaults on invalid JSON or missing ``"patterns"`` key.
    Public API — also called by ``main.py`` for ``permissions.json`` generation.
    """
    patterns_path = get_app_data_dir() / "dangerous_commands.json"
    try:
        raw = patterns_path.read_text(encoding="utf-8").strip()
        if not raw:
            raise ValueError("empty file")
        data = json.loads(raw)
        if not isinstance(data, dict) or "patterns" not in data:
            raise ValueError("missing 'patterns' key")
        patterns = list(data["patterns"])
        # Migration (fix #3): existing installs persisted the obsolete blanket
        # rm globs. They are now handled by _is_dangerous_rm (which allows /tmp).
        # Strip them on load so the fix reaches users who already have the file —
        # otherwise the on-disk file silently overrides the code change
        # (default-propagation trap, PIT38).
        _obsolete = {"rm -rf *", "rm -rf /*", "rm -rf ~*"}
        if any(p in _obsolete for p in patterns):
            patterns = [p for p in patterns if p not in _obsolete]
            try:
                patterns_path.write_text(
                    json.dumps({"patterns": patterns}, indent=2) + "\n",
                    encoding="utf-8",
                )
                logger.info("Migrated dangerous_commands.json — removed obsolete rm globs")
            except OSError:
                pass  # in-memory strip still takes effect this run
        logger.info("Loaded %d dangerous patterns from %s", len(patterns), patterns_path)
        return patterns
    except FileNotFoundError:
        logger.info("dangerous_commands.json not found — seeding defaults")
        patterns_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"patterns": list(DEFAULT_DANGEROUS_PATTERNS)}
        patterns_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        return list(DEFAULT_DANGEROUS_PATTERNS)
    except (json.JSONDecodeError, ValueError) as exc:
        logger.warning("Invalid dangerous_commands.json (%s) — using defaults", exc)
        return list(DEFAULT_DANGEROUS_PATTERNS)
    except OSError as exc:
        logger.warning("Cannot read dangerous_commands.json (%s) — using defaults", exc)
        return list(DEFAULT_DANGEROUS_PATTERNS)


def create_ask_question_gate(
    session_key: str,
    session_context: dict[str, Any],
    ask_question_mgr: "AskQuestionManager",
) -> Callable[..., Any]:
    """Factory: returns an async PreToolUse hook for AskUserQuestion.

    The Claude CLI's AskUserQuestion tool self-resolves with an
    ``is_error:true, "Answer questions?"`` tool_result in headless/SDK mode
    (no interactive UI to satisfy its ``behavior:"ask"`` permission). This hook
    intercepts the tool call at PreToolUse — BEFORE the CLI self-resolves it —
    BLOCKS on ``ask_question_mgr.wait_for_answer(tool_use_id)`` until the user
    answers, then returns ``permissionDecision:"allow"`` with the user's answers
    injected into ``updatedInput.answers``. The CLI's ``call()`` then returns the
    real answers rather than the synthetic error.

    Correlation: the waiter is keyed on the ``tool_use_id`` arg (the SDK
    AskUserQuestion block.id), which is also what the ``ask_user_question`` SSE
    event surfaces as ``toolUseId`` and what ``continue_with_answer`` passes back.
    """

    async def ask_question_gate(
        input_data: dict[str, Any],
        tool_use_id: str | None,
        context: Any,
    ) -> dict[str, Any]:
        # Scoped to AskUserQuestion only — every other tool passes through (AC3).
        if input_data.get("tool_name") != "AskUserQuestion":
            return {"decision": "allow"}

        tool_input = input_data.get("tool_input", {}) or {}
        questions = tool_input.get("questions", [])

        # Defensive: without a tool_use_id we cannot correlate the answer back.
        # Allow with empty answers so the tool resolves rather than hangs.
        if not tool_use_id:
            logger.warning(
                "ask_question_gate: missing tool_use_id, cannot block for answer "
                "(session=%s)", session_key,
            )
            return {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "allow",
                    "updatedInput": {**tool_input, "answers": {}},
                    "permissionDecisionReason": "No tool_use_id — answers unavailable",
                }
            }

        # Surface the question to the frontend via the per-session queue that the
        # streaming orchestrator races. The item carries `kind` + `tool_use_id`
        # so the orchestrator's kind-aware drop-guard + SSE branch can route it.
        # Lazy import avoids a hard module-load cycle (permission_manager is the
        # surfacing channel, shared with the dangerous_command_gate path).
        from .permission_manager import permission_manager as _pm
        actual_session_id = session_context.get("sdk_session_id") or session_key
        await _pm.enqueue_permission_request(actual_session_id, {
            "kind": "ask_user_question",
            "sessionId": actual_session_id,
            "tool_use_id": tool_use_id,
            "questions": questions,
        })

        logger.info(
            "ask_question_gate: blocking for answer session=%s tool_use_id=%s "
            "questions=%d", actual_session_id, tool_use_id, len(questions),
        )

        answer = await ask_question_mgr.wait_for_answer(tool_use_id)

        # Timeout (default 4h): the user never answered. DENY the tool — do NOT
        # inject a fabricated empty answer and proceed. Injecting {} + allowing
        # was the original bug: after the user stepped away, the agent would
        # "proceed with no selection". A deny tells the agent the question
        # expired so it can re-ask, never guess. The deny path carries NO
        # updatedInput.answers — there is no answer to inject.
        if answer == ASK_TIMEOUT_SENTINEL:
            logger.warning(
                "ask_question_gate: question expired (no answer in %ds) "
                "session=%s tool_use_id=%s",
                ASK_ANSWER_TIMEOUT_SECONDS, actual_session_id, tool_use_id,
            )
            return {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": (
                        "提问超时（Question expired）: no answer was received within "
                        "the wait window. The question was NOT answered — re-ask "
                        "the user rather than proceeding with a guessed default."
                    ),
                }
            }

        # answer is the user's answers dict.
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "allow",
                "updatedInput": {**tool_input, "answers": answer},
            }
        }

    return ask_question_gate


def create_dangerous_command_gate(
    session_context: dict[str, Any],
    session_key: str,
    permission_mgr: "PermissionManager",
    enable_human_approval: bool = True,
) -> Callable[..., Any]:
    """Factory: returns an async PreToolUse hook for Bash commands.

    Loads patterns once at gate creation time (not per-invocation).
    Uses *permission_mgr* for HITL flow and session approval tracking.

    When *enable_human_approval* is ``False`` (per-agent config), dangerous
    commands are auto-denied without prompting.
    """
    patterns = load_dangerous_patterns()

    async def dangerous_command_gate(
        input_data: dict[str, Any],
        tool_use_id: str | None,
        context: Any,
    ) -> dict[str, Any]:
        if input_data.get("tool_name") != "Bash":
            return {"decision": "approve"}

        command = input_data.get("tool_input", {}).get("command", "")
        if not command:
            return {"decision": "approve"}

        # Check if command matches any dangerous pattern (glob) OR is a
        # dangerous recursive rm (predicate — fix #3, allows /tmp & /var/folders).
        is_dangerous = (
            any(fnmatch.fnmatch(command, p) for p in patterns)
            or _is_dangerous_rm(command)
        )
        if not is_dangerous:
            return {"decision": "approve"}

        # Auto-deny when human approval is disabled
        if not enable_human_approval:
            logger.warning("[BLOCKED] Dangerous command (no human approval): %s", command[:80])
            return {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": "Dangerous command blocked (human approval disabled)",
                }
            }

        # Check session approvals
        if permission_mgr.is_command_approved(session_key, command):
            logger.info("[APPROVED] Session-approved command: %s", command[:50])
            return {"decision": "approve"}

        # --- HITL prompt flow ---
        # Read session ID dynamically from the (mutable) session_context dict.
        # The hook closure captures session_context at creation time, but the
        # dict's contents may be updated by SessionRouter on each send() when
        # the subprocess is reused (IDLE → STREAMING).  Using the live value
        # ensures the permission request routes to the correct per-session
        # queue that _read_formatted_response is watching.
        actual_session_id = session_context.get("sdk_session_id") or session_key
        request_id = f"perm_{uuid4().hex[:12]}"
        tool_input_data = input_data.get("tool_input", {})

        permission_request = {
            "id": request_id,
            "session_id": actual_session_id,
            "tool_name": "Bash",
            "tool_input": json.dumps(tool_input_data),
            "reason": "Matches dangerous command pattern",
            "status": "pending",
            "created_at": datetime.now().isoformat(),
        }
        permission_mgr.store_pending_request(permission_request)

        await permission_mgr.enqueue_permission_request(actual_session_id, {
            "sessionId": actual_session_id,
            "requestId": request_id,
            "toolName": "Bash",
            "toolInput": tool_input_data,
            "reason": "Matches dangerous command pattern",
            "options": ["approve", "deny"],
        })

        logger.warning(
            "[PERMISSION_REQUEST] Dangerous command requires approval: %s (request_id: %s)",
            command[:50], request_id,
        )

        decision = await permission_mgr.wait_for_permission_decision(request_id)
        logger.info("User decision for %s: %s", request_id, decision)
        permission_mgr.remove_pending_request(request_id)

        if decision == "approve":
            permission_mgr.approve_command(session_key, command)
            return {"decision": "approve"}

        # Distinguish an explicit user denial from a timeout (fix #2/#3). Both
        # deny the command to the SDK, but the reason — surfaced to the user as
        # the tool_result — must make a timeout visibly different from a denial,
        # so a never-surfaced prompt reads as "审批超时" instead of a silent hang.
        if decision == "timeout":
            reason = (
                "审批超时（Approval timed out after 5 minutes）: "
                "the command was auto-denied because no decision was received. "
                "Re-run if you still need it."
            )
        else:
            reason = "User denied: Matches dangerous command pattern"

        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": reason,
            }
        }

    return dangerous_command_gate


# ---------------------------------------------------------------------------
# Governance file gate — Three-Layer Governance enforcement
# ---------------------------------------------------------------------------

# Tier 1: Constitutional (hard gate — require classification before write)
GOVERNANCE_TIER1_PATTERNS: list[str] = [
    "backend/context/SOUL.md",
    "backend/context/AGENT.md",
    "*/.context/SOUL.md",
    "*/.context/AGENT.md",
]

# Tier 2: Statutory (soft gate — advise, don't block)
GOVERNANCE_TIER2_PATTERNS: list[str] = [
    "*/.context/STEERING.md",
    "backend/context/STEERING.md",
    "*/s_autonomous-pipeline/stages/*.md",
]


def _match_governance_tier(file_path: str) -> int:
    """Return governance tier (1, 2, or 0 for non-governance) for a file path.

    Checks both exact matches and backup/temp file variants (.bak, ~, .tmp)
    to prevent bypass via intermediate files.
    """
    if not file_path:
        return 0
    # Normalize path for matching — strip common backup suffixes
    norm = file_path.replace("\\", "/")
    # Strip backup/temp suffixes to catch .bak, ~, .tmp, .swp variants
    base_norm = re.sub(r"(\.bak|\.tmp|\.swp|~)$", "", norm)

    for pattern in GOVERNANCE_TIER1_PATTERNS:
        suffix = pattern.lstrip("*/")
        if fnmatch.fnmatch(norm, pattern) or norm.endswith(suffix):
            return 1
        # Also check base (stripped) path
        if base_norm != norm and (fnmatch.fnmatch(base_norm, pattern) or base_norm.endswith(suffix)):
            return 1
    for pattern in GOVERNANCE_TIER2_PATTERNS:
        suffix = pattern.lstrip("*/")
        if fnmatch.fnmatch(norm, pattern) or norm.endswith(suffix):
            return 2
        if base_norm != norm and (fnmatch.fnmatch(base_norm, pattern) or base_norm.endswith(suffix)):
            return 2
    return 0


def create_governance_file_gate() -> Callable[..., Any]:
    """Factory: returns an async PreToolUse hook that intercepts governance file edits.

    Tier 1 (SOUL/AGENT): Outputs classification reminder — ADVISE mode.
    Tier 2 (STEERING/pipeline docs): Outputs soft reminder.

    Note: Initially advisory (soft gate). Phase 4 will upgrade Tier 1 to BLOCK.
    """

    async def governance_file_gate(
        input_data: dict[str, Any],
        tool_use_id: str | None,
        context: Any,
    ) -> dict[str, Any]:
        tool_name = input_data.get("tool_name", "")
        if tool_name not in ("Edit", "Write"):
            return {"decision": "approve"}

        tool_input = input_data.get("tool_input", {})
        file_path = tool_input.get("file_path", "")
        tier = _match_governance_tier(file_path)

        if tier == 0:
            return {"decision": "approve"}

        tier_label = "CONSTITUTIONAL (Tier 1)" if tier == 1 else "STATUTORY (Tier 2)"
        reminder = (
            f"⚠️ GOVERNANCE GATE [{tier_label}]: Modifying governance file.\n"
            f"Before proceeding, ensure:\n"
            f"  1. Classify: Principle / Rule / Gate?\n"
            f"  2. Parent: P1-P4?\n"
            f"  3. Conflict/Duplicate check done?\n"
            f"  4. Budget: SOUL ≤5 principles, AGENT ≤25 rules, STEERING ≤15"
        )

        logger.info(
            "[GOVERNANCE] Tier %d gate fired for %s (tool=%s)",
            tier, file_path, tool_name,
        )

        # Advisory mode: approve but include reminder in additionalContext
        return {
            "decision": "approve",
            "additionalContext": reminder,
        }

    return governance_file_gate


def create_file_access_permission_handler(allowed_directories: list[str]) -> Callable[..., Any]:
    """Create a file access permission handler with allowed directories bound.

    Args:
        allowed_directories: List of directory paths that are allowed for file access

    Returns:
        Async permission handler function for can_use_tool
    """
    # Resolve symlinks and normalize paths for consistent, secure comparison
    normalized_dirs = [os.path.realpath(d).rstrip('/') for d in allowed_directories]

    async def file_access_permission_handler(
        tool_name: str,
        input_data: dict[str, Any],
        context: dict[str, Any]
    ) -> dict[str, Any]:
        """Check if file access is allowed based on path restrictions."""

        # File tools that need path checking
        file_tools = {
            'Read': 'file_path',
            'Write': 'file_path',
            'Edit': 'file_path',
            'Glob': 'path',
            'Grep': 'path',
        }

        # Check file tools
        if tool_name in file_tools:
            # Get the path parameter name for this tool
            path_param = file_tools[tool_name]
            file_path = input_data.get(path_param, '')

            # If no path specified, allow (tool will handle the error)
            if not file_path:
                return {"behavior": "allow"}

            # Resolve symlinks and normalize to prevent symlink-based path traversal
            normalized_path = os.path.realpath(file_path)

            # Check if the path is within any allowed directory
            is_allowed = any(
                normalized_path.startswith(allowed_dir + '/') or normalized_path == allowed_dir
                for allowed_dir in normalized_dirs
            )

            if not is_allowed:
                logger.warning(f"[FILE ACCESS DENIED] Tool: {tool_name}, Path: {file_path}, Allowed: {normalized_dirs}")
                return {
                    "behavior": "deny",
                    "message": f"File access denied: {file_path} is outside allowed directories",
                    "interrupt": False  # Don't interrupt, let agent try alternative approach
                }

            logger.debug(f"[FILE ACCESS ALLOWED] Tool: {tool_name}, Path: {file_path}")
            return {"behavior": "allow"}

        # Check Bash tool for file access commands
        if tool_name == 'Bash':
            command = input_data.get('command', '')

            if not command:
                return {"behavior": "allow"}

            # Extract potential file paths from bash commands
            # Match common file access patterns
            suspicious_patterns = [
                r'\s+(/[^\s]+)',  # Absolute paths like /etc/passwd
                r'(?:cat|head|tail|less|more|nano|vi|vim|emacs)\s+([^\s|>&]+)',  # Read commands
                r'(?:echo|printf|tee)\s+.*?>\s*([^\s|>&]+)',  # Write redirects
                r'(?:cp|mv|rm|mkdir|rmdir|touch)\s+.*?([^\s|>&]+)',  # File manipulation
            ]

            potential_paths = []
            for pattern in suspicious_patterns:
                matches = re.findall(pattern, command)
                potential_paths.extend(matches)

            # Check each potential path
            for file_path in potential_paths:
                # Skip if relative path (will be relative to cwd which is safe)
                if not file_path.startswith('/'):
                    continue

                # Normalize and check
                normalized_path = os.path.realpath(file_path)
                is_allowed = any(
                    normalized_path.startswith(allowed_dir + '/') or normalized_path == allowed_dir
                    for allowed_dir in normalized_dirs
                )

                if not is_allowed:
                    logger.warning(f"[BASH FILE ACCESS DENIED] Command: {command[:100]}, Path: {file_path}, Allowed: {normalized_dirs}")
                    return {
                        "behavior": "deny",
                        "message": f"Bash file access denied: Command attempts to access {file_path} which is outside allowed directories ({', '.join(normalized_dirs)})",
                        "interrupt": False
                    }

            logger.debug(f"[BASH ALLOWED] Command: {command[:100]}")
            return {"behavior": "allow"}

        # Allow all other tools
        return {"behavior": "allow"}

    return file_access_permission_handler


# ---------------------------------------------------------------------------
# Skill access control
# ---------------------------------------------------------------------------


def create_skill_access_checker(
    allowed_skill_names: list[str],
    builtin_skill_names: list[str] | None = None,
) -> Callable[..., Any]:
    """Create a skill access checker hook with the allowed skill names bound.

    Built-in skills are always allowed regardless of the ``allowed_skill_names``
    list.  Pass ``builtin_skill_names`` so the hook can grant unconditional
    access to them.

    Args:
        allowed_skill_names: List of skill folder names that are allowed.
        builtin_skill_names: Optional list of built-in skill folder names
            that are always permitted.  When ``None``, no implicit allow
            is applied (backward-compatible behaviour).

    Returns:
        Async hook function that checks skill access.
    """
    _builtin_set: set[str] = set(builtin_skill_names) if builtin_skill_names else set()
    _allowed_set: set[str] = set(allowed_skill_names) if allowed_skill_names else set()

    async def skill_access_checker(
        input_data: dict[str, Any],
        tool_use_id: str | None,
        context: Any
    ) -> dict[str, Any]:
        """Check if the requested skill is allowed for this agent."""
        if input_data.get('tool_name') == 'Skill':
            tool_input = input_data.get('tool_input', {})
            requested_skill = tool_input.get('skill', '')

            # Built-in skills are always allowed
            if requested_skill in _builtin_set:
                logger.debug(f"[ALLOWED] Built-in skill access granted: {requested_skill}")
                return {"decision": "approve"}

            # Empty allowed list means no non-built-in skills are allowed
            if not _allowed_set:
                logger.warning(f"[BLOCKED] Skill access denied (no skills allowed): {requested_skill}")
                return {
                    'hookSpecificOutput': {
                        'hookEventName': 'PreToolUse',
                        'permissionDecision': 'deny',
                        'permissionDecisionReason': 'No skills are authorized for this agent'
                    }
                }

            # Check if requested skill is in allowed set (O(1) lookup)
            if requested_skill not in _allowed_set:
                logger.warning(f"[BLOCKED] Skill access denied: {requested_skill} not in {allowed_skill_names}")
                return {
                    'hookSpecificOutput': {
                        'hookEventName': 'PreToolUse',
                        'permissionDecision': 'deny',
                        'permissionDecisionReason': f'Skill "{requested_skill}" is not authorized for this agent. Allowed skills: {", ".join(allowed_skill_names)}'
                    }
                }

            logger.debug(f"[ALLOWED] Skill access granted: {requested_skill}")
        return {"decision": "approve"}

    return skill_access_checker
