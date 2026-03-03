"""Security hooks implementing the 4-layer defense model.

Layer 1: Workspace Isolation (per-agent dirs in <app_data_dir>/workspaces/{agent_id}/)
Layer 2: Skill Access Control (PreToolUse hook validates authorized skills)
Layer 3: File Tool Access Control (can_use_tool permission handler validates file paths)
Layer 4: Bash Command Protection (dangerous command blocking + human approval)

This module contains hook factory functions and constants used by AgentManager
to enforce security policies during agent execution. Each hook is composed into
the Claude Agent SDK's hook system via HookMatcher configurations.

The human approval hook now delegates dangerous-command detection and approval
storage to ``CmdPermissionManager`` (filesystem-backed, shared across sessions)
while retaining the ``PermissionManager`` for the asyncio-based permission
request/response flow (SSE event signaling).
"""

import json
import logging
import os
import re
from datetime import datetime
from typing import Any, Callable, Optional
from uuid import uuid4

from database import db
from .permission_manager import PermissionManager
from .cmd_permission_manager import CmdPermissionManager

logger = logging.getLogger(__name__)


# Dangerous command patterns for human approval (more comprehensive than auto-block)
DANGEROUS_PATTERNS: list[tuple[str, str]] = [
    (r'rm\s+(-[rfRf]+\s+)?/', "Recursive deletion from root"),
    (r'rm\s+(-[rfRf]+\s+)?~', "Recursive deletion from home"),
    (r'rm\s+-[rfRf]+', "Recursive file deletion"),
    (r'dd\s+if=/dev/(zero|random|urandom)', "Disk overwrite command"),
    (r'mkfs', "Filesystem format command"),
    (r'>\s*/dev/(sda|hda|nvme|vda)', "Direct disk write"),
    (r':()\{:\|:&\};:', "Fork bomb"),
    (r'chmod\s+(-R\s+)?777\s+/', "Dangerous permission change"),
    (r'chown\s+-R\s+.*\s+/', "Recursive ownership change from root"),
    (r'curl\s+.*\|\s*(bash|sh)', "Piping remote script to shell"),
    (r'wget\s+.*\|\s*(bash|sh)', "Piping remote script to shell"),
    (r'sudo\s+rm', "Sudo removal command"),
    (r'>\s*/etc/', "Writing to /etc directory"),
]


def check_dangerous_command(command: str) -> Optional[str]:
    """Check if command matches dangerous patterns.

    Args:
        command: The bash command to check

    Returns:
        Reason string if dangerous, None otherwise
    """
    for pattern, reason in DANGEROUS_PATTERNS:
        if re.search(pattern, command, re.IGNORECASE):
            return reason
    return None


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


async def dangerous_command_blocker(
    input_data: dict[str, Any],
    tool_use_id: str | None,
    context: Any
) -> dict[str, Any]:
    """Block dangerous bash commands."""
    if input_data.get('tool_name') == 'Bash':
        command = input_data.get('tool_input', {}).get('command', '')
        reason = check_dangerous_command(command)
        if reason:
            logger.warning(f"[BLOCKED] Dangerous command detected: {reason}")
            return {
                'hookSpecificOutput': {
                    'hookEventName': 'PreToolUse',
                    'permissionDecision': 'deny',
                    'permissionDecisionReason': f'Dangerous command blocked: {reason}'
                }
            }
    return {}


def create_human_approval_hook(
    session_context: dict[str, Any],
    session_key: str,
    enable_human_approval: bool,
    permission_mgr: PermissionManager,
    cmd_permission_mgr: CmdPermissionManager | None = None,
) -> Callable[..., Any]:
    """Create a human approval hook for dangerous commands.

    Uses ``CmdPermissionManager`` (filesystem-backed, shared across sessions)
    for dangerous-command detection and approval storage.  Falls back to the
    old ``PermissionManager`` per-session dict when ``cmd_permission_mgr`` is
    ``None`` (backward compatibility during migration).

    The ``PermissionManager`` is still used for the asyncio-based permission
    request/response flow (queue + event signaling for the SSE dialog).

    Args:
        session_context: Dict with {"sdk_session_id": ...} that gets updated with actual SDK session
        session_key: The session key for tracking approved commands (agent_id or resume_session_id)
        enable_human_approval: Whether human approval is enabled for this agent
        permission_mgr: PermissionManager instance for permission request/response flow (queue + events)
        cmd_permission_mgr: CmdPermissionManager for dangerous detection + approval storage

    Returns:
        Async hook function that checks for dangerous commands and requests approval
    """
    async def human_approval_hook(
        input_data: dict[str, Any],
        tool_use_id: str | None,
        context: Any
    ) -> dict[str, Any]:
        """Check for dangerous commands and request human approval if needed."""
        if input_data.get('tool_name') != 'Bash':
            return {}

        command = input_data.get('tool_input', {}).get('command', '')
        if not command:
            return {}

        # Check if command is dangerous — prefer CmdPermissionManager (glob-based,
        # filesystem-backed) over legacy regex DANGEROUS_PATTERNS
        if cmd_permission_mgr is not None:
            is_dangerous = cmd_permission_mgr.is_dangerous(command)
            danger_reason = "Matches dangerous command pattern" if is_dangerous else None
        else:
            # Legacy fallback: regex-based check
            danger_reason = check_dangerous_command(command)

        if not danger_reason:
            return {}

        # If human approval is disabled, just block it
        if not enable_human_approval:
            logger.warning(f"[BLOCKED] Dangerous command (no human approval): {command}")
            return {
                'hookSpecificOutput': {
                    'hookEventName': 'PreToolUse',
                    'permissionDecision': 'deny',
                    'permissionDecisionReason': f'Dangerous command blocked: {danger_reason}'
                }
            }

        # Check if this command was previously approved — prefer CmdPermissionManager
        # (shared across all sessions, persisted to filesystem)
        if cmd_permission_mgr is not None:
            if cmd_permission_mgr.is_approved(command):
                logger.info(f"[APPROVED] CmdPermissionManager approved: {command[:50]}...")
                return {}  # Allow execution
        else:
            # Legacy fallback: per-session in-memory check
            if permission_mgr.is_command_approved(session_key, command):
                logger.info(f"[APPROVED] Previously approved command: {command[:50]}...")
                return {}  # Allow execution

        # Get the actual SDK session_id (may have been updated after init message)
        actual_session_id = session_context.get("sdk_session_id")
        logger.info(f"Hook firing with session_key={session_key}, actual_session_id={actual_session_id}")

        # Create permission request
        request_id = f"perm_{uuid4().hex[:12]}"
        tool_input_data = input_data.get('tool_input', {})
        permission_request = {
            "id": request_id,
            "session_id": actual_session_id,  # Use actual SDK session_id (not session_key/agent_id)
            "tool_name": "Bash",
            "tool_input": json.dumps(tool_input_data),
            "reason": danger_reason,
            "status": "pending",
            "created_at": datetime.now().isoformat()
        }

        # Store in memory via PermissionManager (replaces DB storage)
        permission_mgr.store_pending_request(permission_request)

        # Put permission request in queue for SSE streaming (use actual SDK session_id!)
        await permission_mgr.get_permission_queue().put({
            "sessionId": actual_session_id,  # Use actual SDK session_id for matching
            "requestId": request_id,
            "toolName": "Bash",
            "toolInput": tool_input_data,
            "reason": danger_reason,
            "options": ["approve", "deny"],
        })

        logger.warning(f"[PERMISSION_REQUEST] Dangerous command requires approval: {command[:50]}... (request_id: {request_id})")
        logger.info(f"Waiting for user decision on request {request_id}...")

        # Suspend execution and wait for user decision
        decision = await permission_mgr.wait_for_permission_decision(request_id)

        logger.info(f"User decision received for request {request_id}: {decision}")

        # Return the decision to the SDK
        if decision == "approve":
            # Store approval in CmdPermissionManager (shared, persistent)
            if cmd_permission_mgr is not None:
                try:
                    cmd_permission_mgr.approve(command)
                    logger.info(f"Command approved via CmdPermissionManager: {command[:50]}...")
                except ValueError as exc:
                    # Overly broad pattern rejected — still allow this one execution
                    logger.warning(f"CmdPermissionManager rejected pattern: {exc}")
                    # Fall back to legacy per-session approval
                    permission_mgr.approve_command(session_key, command)
            else:
                permission_mgr.approve_command(session_key, command)
            return {}
        else:
            # Deny the command
            return {
                'hookSpecificOutput': {
                    'hookEventName': 'PreToolUse',
                    'permissionDecision': 'deny',
                    'permissionDecisionReason': f'User denied: {danger_reason}'
                }
            }

    return human_approval_hook


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
