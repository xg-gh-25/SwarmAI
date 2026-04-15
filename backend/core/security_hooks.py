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
- ``create_tcc_protection_hook``             — macOS TCC popup prevention
"""

import fnmatch
import json
import logging
import os
import platform
import re
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable
from uuid import uuid4

from config import get_app_data_dir

if TYPE_CHECKING:
    from .permission_manager import PermissionManager

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
    return {}


# ---------------------------------------------------------------------------
# Dangerous command gate — single permission layer
# ---------------------------------------------------------------------------

DEFAULT_DANGEROUS_PATTERNS: list[str] = [
    "rm -rf *",
    "rm -rf /*",
    "rm -rf ~*",
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
            return {}

        command = input_data.get("tool_input", {}).get("command", "")
        if not command:
            return {}

        # Check if command matches any dangerous pattern (glob)
        is_dangerous = any(fnmatch.fnmatch(command, p) for p in patterns)
        if not is_dangerous:
            return {}

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
            return {}

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
            return {}

        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": "User denied: Matches dangerous command pattern",
            }
        }

    return dangerous_command_gate


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
# macOS TCC protection — prevent permission popup dialogs
# ---------------------------------------------------------------------------

# Directories under ~ that trigger macOS TCC (Transparency, Consent, and
# Control) permission popups when accessed by a non-entitled process.
_TCC_PROTECTED_NAMES = frozenset({"Music", "Pictures", "Movies"})

# Commands that recurse into subdirectories by default.
# Covers: find, tree, du, ls -R, grep -r/-R (all would traverse TCC dirs).
# Group 1 always captures the command name for error messages.
_RECURSIVE_CMD_RE = re.compile(
    r"\b(find|tree|du)\b"
    r"|"
    r"\b(ls)\b.*\s-[a-zA-Z]*R"     # ls -R, ls -lR, ls -alR
    r"|"
    r"\b(grep)\b.*\s-[a-zA-Z]*[rR]"  # grep -r, grep -R, grep -rn
)


def create_tcc_protection_hook() -> Callable[..., Any]:
    """PreToolUse hook that blocks Bash commands traversing macOS TCC dirs.

    macOS shows intrusive system-level permission dialogs ("python-backend
    would like to access Apple Music / your Photo Library") whenever ANY
    process touches ``~/Music``, ``~/Pictures``, or ``~/Movies``.

    This hook structurally prevents the agent from triggering those popups by:
    1. Blocking commands that directly reference a TCC-protected path.
    2. Blocking recursive commands (``find``, ``tree``, ``du``) whose starting
       path is an ancestor of ``~/`` (i.e., they *would* descend into TCC dirs).

    Returns a no-op hook on non-macOS platforms.
    """
    if platform.system() != "Darwin":
        async def _noop(
            input_data: dict[str, Any],
            tool_use_id: str | None,
            context: Any,
        ) -> dict[str, Any]:
            return {}
        return _noop

    home = os.path.expanduser("~")
    tcc_abs_paths = {os.path.join(home, d) for d in _TCC_PROTECTED_NAMES}
    tcc_tilde_paths = {f"~/{d}" for d in _TCC_PROTECTED_NAMES}

    def _deny(reason: str) -> dict[str, Any]:
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": reason,
            }
        }

    def _extract_paths(command: str) -> list[str]:
        """Extract path-like tokens (start with / or ~) from a command."""
        return [t for t in command.split() if t.startswith("/") or t.startswith("~")]

    def _is_tcc_ancestor(path_str: str) -> bool:
        """Return True if *path_str* is an ancestor of (or equal to) home."""
        expanded = os.path.expanduser(path_str)
        # Preserve root "/" — rstrip would turn it into ""
        if expanded == "/":
            return True
        expanded = expanded.rstrip("/")
        if not expanded:
            return False
        # Exact home or a parent directory of home
        return expanded == home or home.startswith(expanded + "/")

    async def tcc_guard(
        input_data: dict[str, Any],
        tool_use_id: str | None,
        context: Any,
    ) -> dict[str, Any]:
        if input_data.get("tool_name") != "Bash":
            return {}
        command = input_data.get("tool_input", {}).get("command", "")
        if not command:
            return {}

        # ── Check 1: Direct reference to a TCC-protected path ──────────
        for tcc_path in tcc_abs_paths | tcc_tilde_paths:
            if tcc_path in command:
                logger.warning(
                    "[TCC BLOCKED] Direct reference to %s: %s",
                    tcc_path, command[:120],
                )
                return _deny(
                    f"Blocked: command references macOS-protected directory "
                    f"({tcc_path}). Accessing it triggers a system permission "
                    f"popup. If you need files from this directory, ask the "
                    f"user to copy them to ~/Desktop or ~/.swarm-ai/ first."
                )

        # ── Check 2: Recursive command from an ancestor of ~/  ─────────
        m = _RECURSIVE_CMD_RE.search(command)
        if m:
            # With alternation, exactly one group is non-None
            cmd_name = next(g for g in m.groups() if g is not None)
            for path_tok in _extract_paths(command):
                if _is_tcc_ancestor(path_tok):
                    logger.warning(
                        "[TCC BLOCKED] '%s' with path '%s' would traverse TCC dirs: %s",
                        cmd_name, path_tok, command[:120],
                    )
                    return _deny(
                        f"Blocked: '{cmd_name} ... {path_tok}' would traverse "
                        f"into macOS TCC-protected directories (~/Music, "
                        f"~/Pictures, ~/Movies), triggering permission popups. "
                        f"Scope the command to a specific directory like "
                        f"~/.swarm-ai/ or ~/Desktop instead."
                    )

        return {}

    return tcc_guard


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
                return {}

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
        return {}

    return skill_access_checker
