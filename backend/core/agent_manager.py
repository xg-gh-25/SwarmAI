"""Agent lifecycle management using Claude Agent SDK."""
from typing import AsyncIterator, Optional, Any
from uuid import uuid4
from datetime import datetime
from pathlib import Path
import logging
import os
import json
import re
import hashlib
import asyncio
import platform

from claude_agent_sdk import (
    ClaudeSDKClient,
    ClaudeAgentOptions,
    AssistantMessage,
    SystemMessage,
    TextBlock,
    ToolUseBlock,
    ToolResultBlock,
    ResultMessage,
    HookMatcher,
)

from database import db
from config import settings, get_bedrock_model_id
from .session_manager import session_manager
from .workspace_manager import workspace_manager

logger = logging.getLogger(__name__)


class _ClaudeClientWrapper:
    """Wrapper to handle anyio cleanup errors when ClaudeSDKClient is used with asyncio tasks.

    When receive_response() is called from a different asyncio task than the one that
    created the client, anyio's cancel scope can get confused during cleanup.
    This wrapper suppresses that specific error.
    """

    def __init__(self, options):
        self.options = options
        self.client: ClaudeSDKClient | None = None

    async def __aenter__(self) -> ClaudeSDKClient:
        self.client = ClaudeSDKClient(options=self.options)
        return await self.client.__aenter__()  # type: ignore[union-attr]

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.client is None:
            return False
        try:
            return await self.client.__aexit__(exc_type, exc_val, exc_tb)
        except RuntimeError as e:
            if "cancel scope" in str(e).lower():
                logger.warning(f"Suppressed anyio cleanup error (expected when using asyncio tasks): {e}")
                return False  # Don't suppress the original exception if any
            raise


# Module-level storage for approved commands per session
# Key: session_id, Value: set of command hashes that have been approved
_approved_commands: dict[str, set[str]] = {}

# Storage for pending permission decisions
_permission_events: dict[str, asyncio.Event] = {}
_permission_results: dict[str, str] = {}

# Module-level queue for permission requests (to be picked up by SSE stream)
_permission_request_queue: asyncio.Queue = asyncio.Queue()


def _hash_command(command: str) -> str:
    """Create a hash of the command for approval tracking."""
    return hashlib.sha256(command.encode()).hexdigest()[:16]


def approve_command(session_id: str, command: str):
    """Mark a command as approved for a session."""
    if session_id not in _approved_commands:
        _approved_commands[session_id] = set()
    command_hash = _hash_command(command)
    _approved_commands[session_id].add(command_hash)
    logger.info(f"Command approved for session {session_id}: {command[:50]}... (hash: {command_hash})")


def is_command_approved(session_id: str, command: str) -> bool:
    """Check if a command was previously approved for a session."""
    if session_id not in _approved_commands:
        return False
    command_hash = _hash_command(command)
    return command_hash in _approved_commands[session_id]


def clear_session_approvals(session_id: str):
    """Clear all approved commands for a session."""
    _approved_commands.pop(session_id, None)


def _is_duplicate_content_block(block: dict, existing_blocks: list[dict]) -> bool:
    """Check if a content block is a duplicate of any existing block.

    Used to prevent duplicate content when SDK sends cumulative messages.
    """
    block_type = block.get('type')
    for existing in existing_blocks:
        if existing.get('type') != block_type:
            continue
        if block_type == 'text':
            if block.get('text') == existing.get('text'):
                return True
        elif block_type == 'tool_use':
            if block.get('id') == existing.get('id'):
                return True
        elif block_type == 'tool_result':
            if block.get('tool_use_id') == existing.get('tool_use_id'):
                return True
    return False


async def wait_for_permission_decision(request_id: str, timeout: int = 300) -> str:
    """Wait for user permission decision.

    Args:
        request_id: The permission request ID
        timeout: Timeout in seconds (default 5 minutes)

    Returns:
        'approve' or 'deny'
    """
    event = asyncio.Event()
    _permission_events[request_id] = event

    try:
        await asyncio.wait_for(event.wait(), timeout=timeout)
        return _permission_results.get(request_id, "deny")
    except asyncio.TimeoutError:
        # Update database with expired status
        await db.permission_requests.update(request_id, {"status": "expired"})
        return "deny"
    finally:
        _permission_events.pop(request_id, None)
        _permission_results.pop(request_id, None)


def set_permission_decision(request_id: str, decision: str):
    """Set the user's permission decision and signal waiting tasks."""
    _permission_results[request_id] = decision
    if request_id in _permission_events:
        _permission_events[request_id].set()


async def _configure_claude_environment():
    """Configure environment variables for Claude Code CLI.

    Reads API configuration from database settings (Settings page in UI).
    Falls back to environment variables from config.py if no database settings exist.
    """
    # Import here to avoid circular imports
    from routers.settings import get_api_settings

    # Get API settings from database
    api_settings = await get_api_settings()

    # Set ANTHROPIC_API_KEY - prefer database setting, fall back to env var
    api_key = api_settings.get("anthropic_api_key") or settings.anthropic_api_key
    if api_key:
        os.environ["ANTHROPIC_API_KEY"] = api_key

    # Set ANTHROPIC_BASE_URL if configured (for custom endpoints)
    base_url = api_settings.get("anthropic_base_url") or settings.anthropic_base_url
    if base_url:
        os.environ["ANTHROPIC_BASE_URL"] = base_url
    elif "ANTHROPIC_BASE_URL" in os.environ:
        # Clear it if not configured but exists in environment
        del os.environ["ANTHROPIC_BASE_URL"]

    # Set CLAUDE_CODE_USE_BEDROCK if enabled - prefer database setting
    use_bedrock = api_settings.get("use_bedrock", False) or settings.claude_code_use_bedrock
    bedrock_auth_type = api_settings.get("bedrock_auth_type", "credentials")

    if use_bedrock:
        os.environ["CLAUDE_CODE_USE_BEDROCK"] = "true"

        # Get region (common for both auth types)
        aws_region = api_settings.get("aws_region", "us-east-1")
        if aws_region:
            os.environ["AWS_REGION"] = aws_region
            os.environ["AWS_DEFAULT_REGION"] = aws_region

        if bedrock_auth_type == "bearer_token":
            # Use Bearer Token authentication
            aws_bearer_token = api_settings.get("aws_bearer_token")
            if aws_bearer_token:
                os.environ["AWS_BEARER_TOKEN_BEDROCK"] = aws_bearer_token
            # Clear AK/SK credentials when using bearer token
            os.environ.pop("AWS_ACCESS_KEY_ID", None)
            os.environ.pop("AWS_SECRET_ACCESS_KEY", None)
            os.environ.pop("AWS_SESSION_TOKEN", None)
        else:
            # Use AK/SK credentials authentication
            aws_access_key = api_settings.get("aws_access_key_id")
            aws_secret_key = api_settings.get("aws_secret_access_key")
            aws_session_token = api_settings.get("aws_session_token")

            if aws_access_key:
                os.environ["AWS_ACCESS_KEY_ID"] = aws_access_key
            if aws_secret_key:
                os.environ["AWS_SECRET_ACCESS_KEY"] = aws_secret_key
            if aws_session_token:
                os.environ["AWS_SESSION_TOKEN"] = aws_session_token
            else:
                # Clear session token if not provided
                os.environ.pop("AWS_SESSION_TOKEN", None)
            # Clear bearer token when using AK/SK
            os.environ.pop("AWS_BEARER_TOKEN_BEDROCK", None)
    else:
        # Clear Bedrock-related env vars when not using Bedrock
        os.environ.pop("CLAUDE_CODE_USE_BEDROCK", None)
        os.environ.pop("AWS_BEARER_TOKEN_BEDROCK", None)

    # Set CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS if enabled (from env only)
    if settings.claude_code_disable_experimental_betas:
        os.environ["CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS"] = "true"
    elif "CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS" in os.environ:
        del os.environ["CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS"]

    logger.info(f"Claude environment configured - Bedrock: {use_bedrock}, Auth: {bedrock_auth_type if use_bedrock else 'N/A'}, Base URL: {base_url or 'default'}")


async def pre_tool_logger(
    input_data: dict,
    tool_use_id: str | None,
    context: Any
) -> dict:
    """Log tool usage before execution."""
    tool_name = input_data.get('tool_name', 'unknown')
    tool_input = input_data.get('tool_input', {})
    logger.info(f"[PRE-TOOL] Tool: {tool_name}, Input keys: {list(tool_input.keys())}")
    return {}


async def dangerous_command_blocker(
    input_data: dict,
    tool_use_id: str | None,
    context: Any
) -> dict:
    """Block dangerous bash commands."""
    if input_data.get('tool_name') == 'Bash':
        command = input_data.get('tool_input', {}).get('command', '')

        dangerous_patterns = [
            'rm -rf /',
            'rm -rf ~',
            'dd if=/dev/zero',
            ':(){:|:&};:',
            '> /dev/sda',
        ]

        for pattern in dangerous_patterns:
            if pattern in command:
                logger.warning(f"[BLOCKED] Dangerous command: {command}")
                return {
                    'hookSpecificOutput': {
                        'hookEventName': 'PreToolUse',
                        'permissionDecision': 'deny',
                        'permissionDecisionReason': f'Dangerous command blocked: {pattern}'
                    }
                }
    return {}


# Dangerous command patterns for human approval (more comprehensive than auto-block)
DANGEROUS_PATTERNS = [
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


def create_human_approval_hook(session_context: dict, session_key: str, enable_human_approval: bool):
    """Create a human approval hook for dangerous commands.

    Args:
        session_context: Dict with {"sdk_session_id": ...} that gets updated with actual SDK session
        session_key: The session key for tracking approved commands (agent_id or resume_session_id)
        enable_human_approval: Whether human approval is enabled for this agent

    Returns:
        Async hook function that checks for dangerous commands and requests approval
    """
    async def human_approval_hook(
        input_data: dict,
        tool_use_id: str | None,
        context: Any
    ) -> dict:
        """Check for dangerous commands and request human approval if needed."""
        if input_data.get('tool_name') != 'Bash':
            return {}

        command = input_data.get('tool_input', {}).get('command', '')
        if not command:
            return {}

        # Check if command is dangerous
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

        # Check if this command was previously approved (use session_key for tracking)
        if is_command_approved(session_key, command):
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

        # Store in database
        await db.permission_requests.put(permission_request)

        # Put permission request in queue for SSE streaming (use actual SDK session_id!)
        await _permission_request_queue.put({
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
        decision = await wait_for_permission_decision(request_id)

        logger.info(f"User decision received for request {request_id}: {decision}")

        # Return the decision to the SDK
        if decision == "approve":
            # Allow the command to execute
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


def create_file_access_permission_handler(allowed_directories: list[str]):
    """Create a file access permission handler with allowed directories bound.

    Args:
        allowed_directories: List of directory paths that are allowed for file access

    Returns:
        Async permission handler function for can_use_tool
    """
    # Normalize paths (remove trailing slashes for consistent comparison)
    normalized_dirs = [d.rstrip('/') for d in allowed_directories]

    async def file_access_permission_handler(
        tool_name: str,
        input_data: dict,
        context: dict
    ) -> dict:
        """Check if file access is allowed based on path restrictions."""
        import os
        import re

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

            # Normalize the file path (resolve .. and symlinks conceptually)
            normalized_path = os.path.normpath(file_path)

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
                normalized_path = os.path.normpath(file_path)
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


def create_skill_access_checker(allowed_skill_names: list[str]):
    """Create a skill access checker hook with the allowed skill names bound.

    Args:
        allowed_skill_names: List of skill folder names that are allowed

    Returns:
        Async hook function that checks skill access
    """
    async def skill_access_checker(
        input_data: dict,
        tool_use_id: str | None,
        context: Any
    ) -> dict:
        """Check if the requested skill is allowed for this agent."""
        if input_data.get('tool_name') == 'Skill':
            tool_input = input_data.get('tool_input', {})
            requested_skill = tool_input.get('skill', '')

            # Empty allowed list means no skills are allowed
            if not allowed_skill_names:
                logger.warning(f"[BLOCKED] Skill access denied (no skills allowed): {requested_skill}")
                return {
                    'hookSpecificOutput': {
                        'hookEventName': 'PreToolUse',
                        'permissionDecision': 'deny',
                        'permissionDecisionReason': 'No skills are authorized for this agent'
                    }
                }

            # Check if requested skill is in allowed list
            if requested_skill not in allowed_skill_names:
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


class AgentManager:
    """Manages agent lifecycle using Claude Agent SDK.

    Uses ClaudeSDKClient for stateful, multi-turn conversations with Claude.
    Claude Code (underlying SDK) has built-in support for Skills and MCP servers.
    """

    def __init__(self):
        self._clients: dict[str, ClaudeSDKClient] = {}

    async def _build_options(
        self,
        agent_config: dict,
        enable_skills: bool,
        enable_mcp: bool,
        resume_session_id: Optional[str] = None,
        session_context: Optional[dict] = None
    ) -> ClaudeAgentOptions:
        """Build ClaudeAgentOptions from agent configuration.

        Args:
            agent_config: Agent configuration dictionary
            enable_skills: Whether to enable skills
            enable_mcp: Whether to enable MCP servers
            resume_session_id: Optional session ID to resume (for multi-turn conversations)
        """
        logger.info(f"agent_config:{agent_config}")
        logger.info(f"settings:{settings}")
        # Build allowed tools list - use directly from config if provided
        allowed_tools = list(agent_config.get("allowed_tools", []))
    
        # If no allowed_tools specified, fall back to enable flags for backwards compatibility
        if not allowed_tools:
            if agent_config.get("enable_bash_tool", True):
                allowed_tools.append("Bash")

            if agent_config.get("enable_file_tools", True):
                for tool_name in ["Read", "Write", "Edit", "Glob", "Grep"]:
                    allowed_tools.append(tool_name)

            if agent_config.get("enable_web_tools", True):
                for tool_name in ["WebFetch", "WebSearch"]:
                    allowed_tools.append(tool_name)

        # Note: Skill tool is now user-controllable via the Advanced Tools section
        # If user wants to use skills, they need to enable the Skill tool explicitly

        # Plugins configuration
        plugins = []
        plugin_ids = agent_config.get("plugin_ids", [])
        if plugin_ids:
            # Get skills directory for fallback path computation
            from core.plugin_manager import plugin_manager
            skills_dir = plugin_manager.skills_dir

            for plugin_id in plugin_ids:
                plugin = await db.plugins.get(plugin_id)
                if plugin:
                    install_path = plugin.get("install_path")

                    # Fallback: compute install_path from installed_skills if missing
                    if not install_path:
                        installed_skills = plugin.get("installed_skills", [])
                        if isinstance(installed_skills, str):
                            try:
                                installed_skills = json.loads(installed_skills)
                            except Exception:
                                installed_skills = []
                        if installed_skills and len(installed_skills) > 0:
                            install_path = str(skills_dir / installed_skills[0])
                            logger.info(f"Computed install_path for plugin {plugin_id} from installed_skills: {install_path}")
                            # Update the database with the computed path
                            await db.plugins.update(plugin_id, {"install_path": install_path})

                    if install_path:
                        plugins.append({
                            "type": "local",
                            "path": install_path
                        })
                        logger.info(f"Added plugin: {plugin_id} ({plugin.get('name')}) from {install_path}")
                    else:
                        logger.warning(
                            f"Plugin {plugin_id} ({plugin.get('name')}) has no install_path and no installed_skills. "
                            f"Status: {plugin.get('status')}, installed_at: {plugin.get('installed_at')}"
                        )
                else:
                    logger.warning(f"Plugin {plugin_id} not found in database")

        # MCP servers configuration
        mcp_servers = {}

        # Add external MCP servers if enabled
        # Use the server name as the key (instead of UUID) to keep tool names short
        # This is important for Bedrock which has a 64-character tool name limit
        if enable_mcp and agent_config.get("mcp_ids"):
            used_names = set()  # Track used names to handle collisions
            for mcp_id in agent_config["mcp_ids"]:
                mcp_config = await db.mcp_servers.get(mcp_id)
                if mcp_config:
                    connection_type = mcp_config.get("connection_type", "stdio")
                    config = mcp_config.get("config", {})

                    # Use server name as the key for shorter tool names
                    # Handle name collisions by appending suffix
                    server_name = mcp_config.get("name", mcp_id)
                    base_name = server_name
                    suffix = 1
                    while server_name in used_names:
                        server_name = f"{base_name}_{suffix}"
                        suffix += 1
                    used_names.add(server_name)

                    if connection_type == "stdio":
                        mcp_servers[server_name] = {
                            "type": "stdio",
                            "command": config.get("command"),
                            "args": config.get("args", []),
                        }
                    elif connection_type == "sse":
                        mcp_servers[server_name] = {
                            "type": "sse",
                            "url": config.get("url"),
                        }
                    elif connection_type == "http":
                        mcp_servers[server_name] = {
                            "type": "http",
                            "url": config.get("url"),
                        }

        # Build system prompt
        system_prompt = agent_config.get("system_prompt")
        if system_prompt:
            system_prompt_config = system_prompt
        else:
            system_prompt_config = f"You are {agent_config.get('name', 'an AI assistant')}. {agent_config.get('description', '')}"

        # Build hooks
        hooks = {}

        if agent_config.get("enable_tool_logging", True):
            hooks["PreToolUse"] = [
                HookMatcher(hooks=[pre_tool_logger])
            ]

        if agent_config.get("enable_safety_checks", True):
            if "PreToolUse" not in hooks:
                hooks["PreToolUse"] = []
            hooks["PreToolUse"].append(
                HookMatcher(matcher="Bash", hooks=[dangerous_command_blocker])
            )

        # Add human approval hook for dangerous commands
        # Use resume_session_id for resumed sessions, or agent_id for new sessions
        # The session_key is used for tracking approved commands - must match what's
        # stored in the permission_request and used in continue_with_permission
        agent_id = agent_config.get("id")
        session_key = resume_session_id or agent_id or "unknown"

        # Enable human approval hook if configured
        enable_human_approval = agent_config.get("enable_human_approval", True)
        if enable_human_approval:
            if "PreToolUse" not in hooks:
                hooks["PreToolUse"] = []
            # Use provided session_context or create a temporary one
            hook_session_context = session_context if session_context is not None else {"sdk_session_id": resume_session_id or agent_id}
            human_approval = create_human_approval_hook(hook_session_context, session_key, enable_human_approval)
            hooks["PreToolUse"].append(
                HookMatcher(matcher="Bash", hooks=[human_approval])
            )
            logger.info(f"Human approval hook added for session_key: {session_key}")

        # Skill access control - get allowed skill names for this agent
        skill_ids = agent_config.get("skill_ids", [])
        allow_all_skills = agent_config.get("allow_all_skills", False)
        global_user_mode = agent_config.get("global_user_mode", True)

        # Global User Mode requires allow_all_skills=True (skill restrictions not supported)
        if global_user_mode:
            allow_all_skills = True
            skill_ids = []  # Ignore skill_ids in global mode
            logger.info("Global User Mode: forcing allow_all_skills=True, ignoring skill_ids")

        # Get allowed skill names for hook-based access control
        allowed_skill_names = await workspace_manager.get_allowed_skill_names(
            skill_ids=skill_ids,
            allow_all_skills=allow_all_skills
        )
        logger.info(f"Agent skill access: allow_all={allow_all_skills}, skill_ids={skill_ids}, allowed_names={allowed_skill_names}")

        # Add skill access checker hook (double protection with per-agent workspace)
        # Skip adding the hook when allow_all_skills is True (no restrictions needed)
        if enable_skills and not allow_all_skills:
            if "PreToolUse" not in hooks:
                hooks["PreToolUse"] = []
            skill_checker = create_skill_access_checker(allowed_skill_names)
            hooks["PreToolUse"].append(
                HookMatcher(matcher="Skill", hooks=[skill_checker])
            )
            logger.info(f"Skill access checker hook added for skills: {allowed_skill_names}")

        # Determine workspace mode and working directory
        # agent_id already retrieved above for human approval hook
        global_user_mode = agent_config.get("global_user_mode", True)
        if global_user_mode:
            # Global User Mode: use home directory, full access, user settings
            working_directory = str(Path.home())
            setting_sources = ['project', 'user']
            logger.info(f"Agent {agent_id} running in GLOBAL USER MODE (cwd: {working_directory})")
        elif enable_skills and agent_id:
            # Isolated Mode with skills: per-agent workspace with symlinked skills
            # When allow_all_skills=True, all skills will be symlinked
            # When allow_all_skills=False, only specified skills will be symlinked
            working_directory = str(workspace_manager.get_agent_workspace(agent_id))
            setting_sources = ['project']
            logger.info(f"Using per-agent workspace: {working_directory} (allow_all_skills={allow_all_skills})")
        else:
            # Default workspace (no skills or no agent_id)
            working_directory = agent_config.get("working_directory") or settings.agent_workspace_dir
            setting_sources = None
            logger.info(f"Using default workspace: {working_directory}")

        # Build file access permission handler
        # Restrict file access to the working directory (and any additional allowed dirs)
        # In global_user_mode, file access control is disabled
        if global_user_mode:
            file_access_enabled = False
        else:
            file_access_enabled = agent_config.get("enable_file_access_control", True)
        file_access_handler = None
        if file_access_enabled:
            allowed_directories = [working_directory]
            # Add any additional allowed directories from config
            extra_dirs = agent_config.get("allowed_directories", [])
            if extra_dirs:
                allowed_directories.extend(extra_dirs)
            # Add runtime add_dirs (from "Work in a folder" feature)
            add_dirs = agent_config.get("add_dirs", [])
            if add_dirs:
                allowed_directories.extend(add_dirs)
            file_access_handler = create_file_access_permission_handler(allowed_directories)
            logger.info(f"File access control enabled, allowed directories: {allowed_directories}")
        
        # Build sandbox configuration (SDK built-in bash sandboxing)
        sandbox_settings = None
        sandbox_enabled = settings.sandbox_enabled_default

        # Sandbox only works on macOS/Linux, not Windows
        if sandbox_enabled and platform.system() == "Windows":
            logger.warning("Sandbox is not supported on Windows, disabling")
            sandbox_enabled = False

        if sandbox_enabled:
            excluded_commands = []
            if settings.sandbox_excluded_commands:
                excluded_commands = [cmd.strip() for cmd in settings.sandbox_excluded_commands.split(",") if cmd.strip()]

            sandbox_settings = {
                "enabled": True,
                "autoAllowBashIfSandboxed": settings.sandbox_auto_allow_bash,
                "excludedCommands": excluded_commands,
                "allowUnsandboxedCommands": settings.sandbox_allow_unsandboxed,
                "network": {"allowLocalBinding": True}
            }
            logger.info(f"Sandbox enabled: {sandbox_settings}")

        # Determine permission mode
        permission_mode = agent_config.get("permission_mode", "bypassPermissions")

        # Get model from config and convert to Bedrock model ID if using Bedrock
        # Check runtime env var (set by _configure_claude_environment) rather than static settings
        model = agent_config.get("model")
        use_bedrock = os.environ.get("CLAUDE_CODE_USE_BEDROCK", "").lower() == "true"
        if model and use_bedrock:
            model = get_bedrock_model_id(model)
            logger.info(f"Using Bedrock model: {model}")
        
        def stderr_callback(input):
            logger.error(input)

        # Get add_dirs for ClaudeAgentOptions
        sdk_add_dirs = agent_config.get("add_dirs", [])
        if sdk_add_dirs:
            working_directory = sdk_add_dirs[0]
        # Max buffer size for JSON messages (default 10MB to handle large tool outputs)
        max_buffer_size = int(os.environ.get("MAX_BUFFER_SIZE", 10 * 1024 * 1024))

        return ClaudeAgentOptions(
            system_prompt=system_prompt_config,
            allowed_tools=allowed_tools if allowed_tools else None,
            mcp_servers=mcp_servers if mcp_servers else None,
            plugins=plugins if plugins else None,  # Local plugins for agent
            permission_mode=permission_mode,
            model=model,
            stderr=stderr_callback,
            # if user select a folder as working directory.
            cwd=working_directory ,
            # setting_sources controls where Claude Code loads settings from:
            # - 'project' = only from cwd (isolated mode)
            # - 'user' = also from ~/.claude/ (global mode, enables user-level skills)
            setting_sources=setting_sources,
            hooks=hooks if hooks else None,
            resume=resume_session_id,  # Resume from previous session for multi-turn
            sandbox=sandbox_settings,  # Built-in SDK sandbox for bash isolation
            can_use_tool=file_access_handler,  # File access control
            max_buffer_size=max_buffer_size,  # Increase buffer for large JSON messages
            # add_dirs=sdk_add_dirs if sdk_add_dirs else None,  # Additional directories for Claude to access
        )

    async def _save_message(
        self,
        session_id: str,
        role: str,
        content: list[dict],
        model: Optional[str] = None
    ) -> dict:
        """Save a message to the database.

        Args:
            session_id: The session ID
            role: Message role ('user' or 'assistant')
            content: Message content blocks
            model: Optional model name for assistant messages

        Returns:
            The saved message dict
        """
        message_data = {
            "id": str(uuid4()),
            "session_id": session_id,
            "role": role,
            "content": content,
            "model": model,
            "created_at": datetime.now().isoformat(),
        }
        await db.messages.put(message_data)
        return message_data

    async def get_session_messages(self, session_id: str) -> list[dict]:
        """Get all messages for a session.

        Args:
            session_id: The session ID

        Returns:
            List of message dicts ordered by timestamp
        """
        return await db.messages.list_by_session(session_id)

    async def run_conversation(
        self,
        agent_id: str,
        user_message: Optional[str] = None,
        content: Optional[list[dict]] = None,
        session_id: Optional[str] = None,
        enable_skills: bool = False,
        enable_mcp: bool = False,
        add_dirs: Optional[list[str]] = None,
    ) -> AsyncIterator[dict]:
        """Run conversation with agent and stream responses.

        Uses ClaudeSDKClient for multi-turn conversations with Claude.
        Claude Code has built-in support for Skills via the Skill tool.

        For multi-turn conversations, pass the session_id from the SDK's
        init message to resume the conversation from where it left off.

        The session_id is provided by the SDK in the first SystemMessage
        with subtype='init'. This ID must be captured and used for resumption.

        Args:
            agent_id: The agent ID
            user_message: Simple text message (for backward compatibility)
            content: Multimodal content array with text, images, documents
            session_id: Optional session ID for resuming conversations
            enable_skills: Whether to enable skills
            enable_mcp: Whether to enable MCP servers
            add_dirs: Additional directories for Claude to access
        """
        # Check if this is a new session or resuming an existing one
        is_resuming = session_id is not None

        # Build the query content - support both simple message and multimodal content
        if content is not None:
            # Use multimodal content directly
            query_content = content
            # Extract display text for session title (first text block or "Attachment")
            display_text = None
            for block in content:
                if block.get("type") == "text" and block.get("text"):
                    display_text = block.get("text")
                    break
            if not display_text:
                display_text = "[Attachment message]"
        elif user_message is not None:
            # Simple text message - wrap in content array
            query_content = user_message
            display_text = user_message
        else:
            yield {
                "type": "error",
                "error": "Either message or content must be provided",
            }
            return

        # Get agent config
        agent_config = await db.agents.get(agent_id)
        if not agent_config:
            yield {
                "type": "error",
                "error": f"Agent {agent_id} not found",
            }
            return
        agent_config['allowed_tools'] = []

        # Add runtime add_dirs to agent config for _build_options
        # Also extract work_dir for session persistence
        work_dir = None
        if add_dirs:
            agent_config['add_dirs'] = add_dirs
            work_dir = add_dirs[0]  # First directory is the working directory
            logger.info(f"Adding extra directories: {add_dirs}")

        logger.info(f"Running conversation with agent {agent_id}, session {session_id}, is_resuming={is_resuming}")
        logger.info(f"Agent config: {agent_config}")
        logger.info(f"Content type: {'multimodal' if content else 'text'}")

        # For resumed sessions, we can send session_start immediately
        # For new sessions, we'll send it after capturing SDK session_id
        if is_resuming:
            yield {
                "type": "session_start",
                "sessionId": session_id,
            }
            # Store/update session for resumed conversations
            title = display_text[:50] + "..." if len(display_text) > 50 else display_text
            await session_manager.store_session(session_id, agent_id, title, work_dir=work_dir)

            # Save user message to database for resumed sessions
            # Store original content if multimodal, otherwise wrap text
            user_content = content if content else [{"type": "text", "text": user_message}]
            await self._save_message(
                session_id=session_id,
                role="user",
                content=user_content
            )

        # Configure Claude environment variables
        await _configure_claude_environment()

        # Track the actual SDK session_id (captured from init message)
        # Use a dict so forwarder task can see updates (mutable container)
        # Must be created BEFORE _build_options so hook can capture same object
        session_context = {"sdk_session_id": session_id}  # Will be updated for new sessions

        # Build options - use resume parameter if continuing an existing session
        options = await self._build_options(agent_config, enable_skills, enable_mcp, session_id if is_resuming else None, session_context)
        logger.info(f"Built options - allowed_tools: {options.allowed_tools}, permission_mode: {options.permission_mode}, resume: {session_id if is_resuming else None}")
        logger.info(f"MCP servers: {options.mcp_servers}")
        logger.info(f"Working directory: {options.cwd}")
        logger.info(f"Add dirs: {options.add_dirs}")

        # Collect assistant response content for saving
        assistant_content = []
        assistant_model = None

        try:
            logger.info(f"Creating ClaudeSDKClient...")
            async with _ClaudeClientWrapper(options=options) as client:
                # For resumed sessions, store client immediately
                # For new sessions, we'll store after getting SDK session_id
                if is_resuming:
                    self._clients[session_id] = client
                logger.info(f"ClaudeSDKClient created, is_resuming={is_resuming}")

                # Initialize task variables before try block to ensure they exist in finally
                sdk_reader_task = None
                forwarder_task = None

                try:
                    logger.info(f"Sending query: {display_text[:100] if display_text else 'multimodal'}...")

                    # Send query - use content for multimodal, message for simple text
                    if isinstance(query_content, list):
                        # For multimodal content, SDK expects AsyncIterable[dict]
                        # Create an async generator that yields the properly formatted message
                        async def multimodal_message_generator():
                            """Async generator for multimodal content."""
                            message = {
                                "type": "user",
                                "message": {"role": "user", "content": query_content},
                                "parent_tool_use_id": None,
                            }
                            yield message

                        await client.query(multimodal_message_generator())
                    else:
                        # Simple text string - SDK handles wrapping
                        await client.query(query_content)
                    logger.info(f"Query sent, waiting for response...")

                    # Create combined queue for merging SDK messages and permission requests
                    combined_queue = asyncio.Queue()
                    message_count = 0

                    # Background task to read SDK messages and put in combined queue
                    async def sdk_message_reader():
                        """Read SDK messages and put them in the combined queue."""
                        try:
                            async for message in client.receive_response():
                                await combined_queue.put({"source": "sdk", "message": message})
                        except Exception as e:
                            import traceback
                            error_traceback = traceback.format_exc()
                            logger.error(f"SDK message reader error: {e}")
                            logger.error(f"SDK error traceback:\n{error_traceback}")
                            # Try to get more details from the exception
                            if hasattr(e, 'stderr'):
                                logger.error(f"SDK stderr: {e.stderr}")  # type: ignore[attr-defined]
                            if hasattr(e, 'stdout'):
                                logger.error(f"SDK stdout: {e.stdout}")  # type: ignore[attr-defined]
                            await combined_queue.put({"source": "error", "error": str(e), "detail": error_traceback})
                        finally:
                            # Signal that SDK stream is done
                            await combined_queue.put({"source": "sdk_done"})
                            logger.debug("SDK message reader finished")

                    # Background task to forward permission requests for this session
                    async def permission_request_forwarder():
                        """Monitor global queue and forward requests for this session."""
                        try:
                            while True:
                                # Get request from global queue
                                request = await _permission_request_queue.get()

                                # Check if it belongs to this session
                                current_session_id = session_context["sdk_session_id"]
                                if request.get("sessionId") == current_session_id:
                                    # Forward to combined queue
                                    logger.info(f"Forwarding permission request {request.get('requestId')} to combined queue for session {current_session_id}")
                                    await combined_queue.put({"source": "permission", "request": request})
                                else:
                                    # Put it back for other sessions
                                    logger.debug(f"Request {request.get('requestId')} for session {request.get('sessionId')} doesn't match current session {current_session_id}, putting back")
                                    await _permission_request_queue.put(request)
                                    # Small delay to avoid busy loop
                                    await asyncio.sleep(0.01)
                        except asyncio.CancelledError:
                            logger.debug("Permission request forwarder cancelled")
                            raise

                    # Start both background tasks
                    sdk_reader_task = asyncio.create_task(sdk_message_reader())
                    forwarder_task = asyncio.create_task(permission_request_forwarder())

                    # Main loop: process items from combined queue
                    while True:
                        item = await combined_queue.get()

                        # Check if SDK stream is done
                        if item["source"] == "sdk_done":
                            logger.info("SDK iterator finished, exiting message loop")
                            break

                        # Handle permission requests
                        if item["source"] == "permission":
                            request = item["request"]
                            logger.info(f"Emitting permission request: {request.get('requestId')}")
                            yield {"type": "permission_request", **request}
                            continue

                        # Handle errors from SDK reader
                        if item["source"] == "error":
                            logger.error(f"Error from SDK reader: {item['error']}")
                            break

                        # Handle SDK messages
                        if item["source"] == "sdk":
                            message = item["message"]
                            message_count += 1
                            logger.info(f"Received message {message_count}: {type(message).__name__}")
                            # Handle ResultMessage
                            if isinstance(message,ResultMessage):
                                logger.info(f"ResultMessage subtype: {message.subtype}, data: {message.result}")
                                # Handle slash command results (e.g., /clear, /help, /compact)
                                result_text = message.result
                                if result_text:
                                    logger.debug(f"ResultMessage result_text: {result_text[:50]}...")
                                    yield {
                                        "type": "assistant",
                                        "content": [{"type": "text", "text": result_text}],
                                        "model": agent_config.get("model", "claude-sonnet-4-20250514")
                                    }
                                    # Add to assistant_content for saving (with deduplication)
                                    # ResultMessage.result often duplicates AssistantMessage content
                                    result_block = {"type": "text", "text": result_text}
                                    if not _is_duplicate_content_block(result_block, assistant_content):
                                        assistant_content.append(result_block)
                            # Handle SystemMessage
                            if isinstance(message, SystemMessage):
                                logger.info(f"SystemMessage subtype: {message.subtype}, data: {message.data}")

                                if message.subtype == 'init':
                                    # Capture session_id from SDK's init message (for new sessions)
                                    session_context["sdk_session_id"] = message.data.get('session_id')
                                    logger.info(f"Captured SDK session_id from init: {session_context['sdk_session_id']}")

                                    # For new sessions, now we can send session_start and store session
                                    if not is_resuming:
                                        # Store client with SDK session_id
                                        self._clients[session_context["sdk_session_id"]] = client

                                        yield {
                                            "type": "session_start",
                                            "sessionId": session_context["sdk_session_id"],
                                        }

                                        # Store session with SDK session_id and work_dir for continuity
                                        title = display_text[:50] + "..." if len(display_text) > 50 else display_text
                                        await session_manager.store_session(session_context["sdk_session_id"], agent_id, title, work_dir=work_dir)

                                        # Save user message to database with SDK session_id
                                        # Store original content if multimodal, otherwise wrap text
                                        user_content = content if content else [{"type": "text", "text": user_message}]
                                        await self._save_message(
                                            session_id=session_context["sdk_session_id"],
                                            role="user",
                                            content=user_content
                                        )

                                continue  # Don't format SystemMessage for output

                            # Format and process the message
                            formatted = await self._format_message(message, agent_config, session_context["sdk_session_id"])
                            if formatted:
                                logger.debug(f"Formatted message type: {formatted.get('type')}")

                                # Collect content for saving (with deduplication)
                                if formatted.get('type') == 'assistant' and formatted.get('content'):
                                    for block in formatted['content']:
                                        if not _is_duplicate_content_block(block, assistant_content):
                                            assistant_content.append(block)
                                    assistant_model = formatted.get('model')

                                yield formatted

                                # If this is an AskUserQuestion, stop and wait for user input
                                if formatted.get('type') == 'ask_user_question':
                                    logger.info(f"AskUserQuestion detected, stopping to wait for user input")
                                    # Save assistant message before returning
                                    sdk_session = session_context.get("sdk_session_id")
                                    if assistant_content and sdk_session:
                                        await self._save_message(
                                            session_id=sdk_session,
                                            role="assistant",
                                            content=assistant_content,
                                            model=assistant_model
                                        )
                                    return

                                # If this is a permission_request (from _format_message), stop and wait
                                # This is a fallback in case the ToolResultBlock contains the prefix
                                if formatted.get('type') == 'permission_request':
                                    request_id = formatted.get('requestId')
                                    logger.info(f"Permission request detected from message: {request_id}, stopping to wait for user decision")
                                    # Save assistant message before returning
                                    sdk_session = session_context.get("sdk_session_id")
                                    if assistant_content and sdk_session:
                                        await self._save_message(
                                            session_id=sdk_session,
                                            role="assistant",
                                            content=assistant_content,
                                            model=assistant_model
                                        )
                                    return

                            # If it's a result message, include session info
                            if isinstance(message, ResultMessage):
                                logger.info(f"Conversation complete. Total messages: {message_count}")

                                # Check if this was a slash command with no assistant response
                                is_slash_command = display_text.strip().startswith('/') if display_text else False
                                if is_slash_command and not assistant_content:
                                    # Provide a default response for slash commands
                                    command_name = display_text.strip().split()[0] if display_text else '/unknown'
                                    default_response = f"Command `{command_name}` executed."
                                    logger.info(f"Slash command with no content, adding default response: {default_response}")
                                    yield {
                                        "type": "assistant",
                                        "content": [{"type": "text", "text": default_response}],
                                        "model": agent_config.get("model", "claude-sonnet-4-20250514")
                                    }
                                    assistant_content.append({"type": "text", "text": default_response})

                                # Save assistant message
                                if assistant_content and session_context["sdk_session_id"]:
                                    await self._save_message(
                                        session_id=session_context["sdk_session_id"],
                                        role="assistant",
                                        content=assistant_content,
                                        model=assistant_model
                                    )

                                yield {
                                    "type": "result",
                                    "session_id": session_context["sdk_session_id"],
                                    "duration_ms": getattr(message, 'duration_ms', 0),
                                    "total_cost_usd": getattr(message, 'total_cost_usd', None),
                                    "num_turns": getattr(message, 'num_turns', 1),
                                }
                finally:
                    # Cancel background tasks if they exist
                    if sdk_reader_task and not sdk_reader_task.done():
                        sdk_reader_task.cancel()
                        try:
                            await sdk_reader_task
                        except asyncio.CancelledError:
                            pass
                        logger.debug("SDK reader task cancelled")

                    if forwarder_task and not forwarder_task.done():
                        forwarder_task.cancel()
                        try:
                            await forwarder_task
                        except asyncio.CancelledError:
                            pass
                        logger.debug("Forwarder task cancelled")

                    # Remove client from tracking when done
                    if session_context["sdk_session_id"]:
                        self._clients.pop(session_context["sdk_session_id"], None)

        except Exception as e:
            import traceback
            error_traceback = traceback.format_exc()
            logger.error(f"Error in conversation: {e}")
            logger.error(f"Full traceback:\n{error_traceback}")
            yield {
                "type": "error",
                "error": str(e),
                "detail": error_traceback,
            }

    async def _format_message(self, message: Any, agent_config: dict, session_id: Optional[str] = None) -> Optional[dict]:
        """Format SDK message to API response format."""

        if isinstance(message, AssistantMessage):
            content_blocks = []

            for block in message.content:
                if isinstance(block, TextBlock):
                    content_blocks.append({
                        "type": "text",
                        "text": block.text
                    })
                elif isinstance(block, ToolUseBlock):
                    # Check if this is an AskUserQuestion tool call
                    if block.name == "AskUserQuestion":
                        # Return special ask_user_question event
                        questions = block.input.get("questions", [])
                        event = {
                            "type": "ask_user_question",
                            "toolUseId": block.id,
                            "questions": questions
                        }
                        # Include session_id so frontend can continue the conversation
                        if session_id:
                            event["sessionId"] = session_id
                        return event
                    # Note: Dangerous Bash command detection is handled by the human_approval_hook
                    # which runs BEFORE execution and can actually block it. Detection here
                    # would be too late - the SDK has already decided to execute the tool.
                    # The hook denial is detected in ToolResultBlock below.

                    # Regular tool use block
                    content_blocks.append({
                        "type": "tool_use",
                        "id": block.id,
                        "name": block.name,
                        "input": block.input
                    })
                elif isinstance(block, ToolResultBlock):
                    block_content = str(block.content) if block.content else None

                    # Note: Permission request handling is now done via the queue mechanism
                    # and emitted directly in the message loop before formatting.
                    # This ToolResultBlock will just contain the normal tool output.

                    content_blocks.append({
                        "type": "tool_result",
                        "tool_use_id": block.tool_use_id,
                        "content": block_content,
                        "is_error": getattr(block, 'is_error', False)
                    })

            if content_blocks:
                return {
                    "type": "assistant",
                    "content": content_blocks,
                    "model": getattr(message, 'model', agent_config.get("model", "claude-sonnet-4-20250514"))
                }

        elif isinstance(message, ResultMessage):
            # Return None here, we handle ResultMessage separately to include session_id
            return None

        return None

    async def continue_with_answer(
        self,
        agent_id: str,
        session_id: str,
        tool_use_id: str,
        answers: dict[str, str],
        enable_skills: bool = False,
        enable_mcp: bool = False,
    ) -> AsyncIterator[dict]:
        """Continue conversation by providing answers to AskUserQuestion.

        This method sends the user's answers as a user message to continue
        the conversation after Claude asked questions.

        Args:
            agent_id: The agent ID
            session_id: The session ID (required for conversation continuity)
            tool_use_id: The tool_use_id from the AskUserQuestion event (for reference)
            answers: Dictionary mapping question text to answer text
            enable_skills: Whether to enable skills
            enable_mcp: Whether to enable MCP servers

        Yields:
            Formatted messages from the agent
        """
        # Get agent config
        agent_config = await db.agents.get(agent_id)
        if not agent_config:
            yield {
                "type": "error",
                "error": f"Agent {agent_id} not found",
            }
            return

        logger.info(f"Continuing conversation with answer for agent {agent_id}, session {session_id}")
        logger.info(f"Tool use ID: {tool_use_id}, Answers: {answers}")

        # Restore work_dir from session for continuity
        session_info = await session_manager.get_session(session_id)
        if session_info and session_info.work_dir:
            agent_config['add_dirs'] = [session_info.work_dir]
            logger.info(f"Restored work_dir from session: {session_info.work_dir}")

        # Configure Claude environment variables
        await _configure_claude_environment()

        # Build options with resume to continue the session
        options = await self._build_options(agent_config, enable_skills, enable_mcp, resume_session_id=session_id)

        # Format answers as a user message
        answer_message = json.dumps({"answers": answers}, indent=2)

        # Save user answer to database
        await self._save_message(
            session_id=session_id,
            role="user",
            content=[{"type": "text", "text": f"User answers:\n{answer_message}"}]
        )

        # Collect assistant response content for saving
        assistant_content = []
        assistant_model = None
        forwarder_task = None  # Initialize before try block for finally clause

        try:
            logger.info(f"Creating ClaudeSDKClient for answer continuation with resume={session_id}...")
            async with ClaudeSDKClient(options=options) as client:
                # Store client for potential interruption
                self._clients[session_id] = client

                # Send the answers as a regular user message
                await client.query(answer_message)
                logger.info(f"Answer sent, waiting for response...")

                # Create event queue for this conversation to handle permission requests
                event_queue = asyncio.Queue()

                # Background task to forward permission requests for this session
                async def permission_request_forwarder():
                    """Monitor global queue and forward requests for this session."""
                    try:
                        while True:
                            request = await _permission_request_queue.get()
                            if request.get("sessionId") == session_id:
                                logger.info(f"Forwarding permission request {request.get('requestId')} to event queue")
                                await event_queue.put({"type": "permission_request", **request})
                            else:
                                await _permission_request_queue.put(request)
                                await asyncio.sleep(0.01)
                    except asyncio.CancelledError:
                        logger.debug("Permission request forwarder cancelled")
                        raise

                # Start the forwarder task
                forwarder_task = asyncio.create_task(permission_request_forwarder())

                message_count = 0
                async for message in client.receive_response():
                    message_count += 1
                    logger.debug(f"Received message {message_count}: {type(message).__name__}")

                    # Skip SystemMessage (init message)
                    if isinstance(message, SystemMessage):
                        logger.debug(f"Skipping SystemMessage with subtype: {message.subtype}")
                        continue

                    # Check for queued permission requests BEFORE formatting message
                    while not event_queue.empty():
                        try:
                            queued_event = event_queue.get_nowait()
                            logger.info(f"Emitting queued permission request: {queued_event.get('requestId')}")
                            yield queued_event
                        except asyncio.QueueEmpty:
                            break

                    formatted = await self._format_message(message, agent_config, session_id)
                    if formatted:
                        logger.debug(f"Formatted message type: {formatted.get('type')}")

                        # Collect content for saving (with deduplication)
                        if formatted.get('type') == 'assistant' and formatted.get('content'):
                            for block in formatted['content']:
                                if not _is_duplicate_content_block(block, assistant_content):
                                    assistant_content.append(block)
                            assistant_model = formatted.get('model')

                        yield formatted

                        # If this is an AskUserQuestion, stop and wait for user input
                        if formatted.get('type') == 'ask_user_question':
                            logger.info(f"AskUserQuestion detected, stopping to wait for user input")
                            if assistant_content:
                                await self._save_message(
                                    session_id=session_id,
                                    role="assistant",
                                    content=assistant_content,
                                    model=assistant_model
                                )
                            return

                        # Note: permission_request type shouldn't appear here anymore
                        # The hook now suspends execution until user responds, then continues naturally

                    if isinstance(message, ResultMessage):
                        logger.info(f"Conversation continued successfully. Total messages: {message_count}")

                        # Save assistant message
                        if assistant_content:
                            await self._save_message(
                                session_id=session_id,
                                role="assistant",
                                content=assistant_content,
                                model=assistant_model
                            )

                        yield {
                            "type": "result",
                            "session_id": session_id,
                            "duration_ms": getattr(message, 'duration_ms', 0),
                            "total_cost_usd": getattr(message, 'total_cost_usd', None),
                            "num_turns": getattr(message, 'num_turns', 1),
                        }

        except Exception as e:
            import traceback
            error_traceback = traceback.format_exc()
            logger.error(f"Error continuing conversation with answer: {e}")
            logger.error(f"Full traceback:\n{error_traceback}")
            yield {
                "type": "error",
                "error": str(e),
                "detail": error_traceback,
            }
        finally:
            # Cancel forwarder task if it exists
            if forwarder_task and not forwarder_task.done():
                forwarder_task.cancel()
                try:
                    await forwarder_task
                except asyncio.CancelledError:
                    pass
                logger.debug("Forwarder task cancelled")

            self._clients.pop(session_id, None)

    async def continue_with_permission(
        self,
        agent_id: str,
        session_id: str,
        request_id: str,
        decision: str,  # "approve" or "deny"
        feedback: Optional[str] = None,
        enable_skills: bool = False,
        enable_mcp: bool = False,
    ) -> AsyncIterator[dict]:
        """Continue conversation after user makes a permission decision.

        Args:
            agent_id: The agent ID
            session_id: The session ID
            request_id: The permission request ID
            decision: User's decision ("approve" or "deny")
            feedback: Optional feedback from user
            enable_skills: Whether to enable skills
            enable_mcp: Whether to enable MCP servers

        Yields:
            Formatted messages from the agent
        """
        # Get agent config
        agent_config = await db.agents.get(agent_id)
        if not agent_config:
            yield {
                "type": "error",
                "error": f"Agent {agent_id} not found",
            }
            return

        # Get permission request details
        permission_request = await db.permission_requests.get(request_id)
        if not permission_request:
            yield {
                "type": "error",
                "error": f"Permission request {request_id} not found",
            }
            return

        # Update permission request status
        await db.permission_requests.update(request_id, {
            "status": decision,
            "decided_at": datetime.now().isoformat(),
            "user_feedback": feedback,
        })

        logger.info(f"Permission decision for request {request_id}: {decision}")
        logger.info(f"Continuing conversation for agent {agent_id}, session {session_id}")

        # Restore work_dir from session for continuity
        session_info = await session_manager.get_session(session_id)
        if session_info and session_info.work_dir:
            agent_config['add_dirs'] = [session_info.work_dir]
            logger.info(f"Restored work_dir from session: {session_info.work_dir}")

        # Configure Claude environment variables
        await _configure_claude_environment()

        # Build options with resume to continue the session
        options = await self._build_options(agent_config, enable_skills, enable_mcp, resume_session_id=session_id)

        # Parse the original command from permission request
        tool_input = permission_request.get("tool_input", "{}")
        if isinstance(tool_input, str):
            tool_input = json.loads(tool_input)
        command = tool_input.get("command", "unknown command")

        # Get the session_key used by the hook (stored in permission_request)
        # This ensures the approval is stored with the same key the hook will check
        perm_session_id = permission_request.get("session_id", session_id)
        logger.info(f"Using permission session_id for approval: {perm_session_id}")

        # Format decision as a user message
        if decision == "approve":
            decision_message = f"User APPROVED the command. Please proceed with executing: {command}"
            # Store this approval for the hook to check - use the session_id from permission_request
            _approved_commands.setdefault(perm_session_id, set()).add(_hash_command(command))
        else:
            reason = feedback if feedback else "User denied the command"
            decision_message = f"User DENIED the command '{command}'. Reason: {reason}. Please acknowledge this and continue without executing that command."

        # CRITICAL: Notify the waiting hook to continue execution
        # This will unblock the original SDK client that's waiting in the hook
        set_permission_decision(request_id, decision)
        logger.info(f"Permission decision sent to waiting hook: {request_id} -> {decision}")

        # Save user decision to database
        await self._save_message(
            session_id=session_id,
            role="user",
            content=[{"type": "text", "text": decision_message}]
        )

        # The original stream will continue processing and send results back
        # Just return a simple acknowledgment here to close this new stream
        yield {
            "type": "permission_acknowledged",
            "request_id": request_id,
            "decision": decision,
        }
        logger.info(f"Permission decision processed, original stream will handle execution")

    async def disconnect_all(self):
        """Disconnect all active clients."""
        for session_id, client in list(self._clients.items()):
            try:
                logger.info(f"Disconnecting client for session {session_id}")
                # Try to interrupt if running
                await client.interrupt()
            except Exception as e:
                logger.error(f"Error disconnecting client {session_id}: {e}")
        self._clients.clear()

    async def interrupt_session(self, session_id: str) -> dict:
        """Interrupt a running session.

        Args:
            session_id: The session ID to interrupt

        Returns:
            Dict with status information
        """
        client = self._clients.get(session_id)
        if not client:
            logger.warning(f"No active client found for session {session_id}")
            return {
                "success": False,
                "message": f"No active session found with ID {session_id}",
            }

        try:
            logger.info(f"Interrupting session {session_id}")
            await client.interrupt()
            logger.info(f"Session {session_id} interrupted successfully")
            return {
                "success": True,
                "message": "Session interrupted successfully",
            }
        except Exception as e:
            logger.error(f"Error interrupting session {session_id}: {e}")
            return {
                "success": False,
                "message": f"Failed to interrupt session: {str(e)}",
            }

    async def run_skill_creator_conversation(
        self,
        skill_name: str,
        skill_description: str,
        user_message: Optional[str] = None,
        session_id: Optional[str] = None,
        model: Optional[str] = None,
    ) -> AsyncIterator[dict]:
        """Run a skill creation conversation with a specialized Skill Creator Agent.

        This creates a temporary agent configuration specifically for skill creation,
        using the skill-creator skill to guide the process.

        Args:
            skill_name: Name of the skill to create
            skill_description: Description of what the skill should do
            user_message: Optional follow-up message for iterating on the skill
            session_id: Optional session ID for continuing conversation
            model: Optional model to use (defaults to claude-sonnet-4-5-20250514)

        Yields:
            Formatted messages from the agent
        """
        # Check if resuming or new session
        # For new sessions, session_id will be captured from SDK's init message
        is_resuming = session_id is not None

        # Build the initial prompt or use the follow-up message
        if user_message:
            # This is a follow-up message for iteration
            prompt = user_message
        else:
            # Initial skill creation request
            prompt = f"""Please create a new skill with the following specifications:

**Skill Name:** {skill_name}
**Skill Description:** {skill_description}

Use the skill-creator skill (invoke /skill-creator) to guide your skill creation process. Follow the workflow:
1. Understand the skill requirements from the description above
2. Plan reusable contents (scripts, references, assets) if needed
3. Initialize the skill using the init_skill.py script
4. Edit SKILL.md and create any necessary files
5. Test any scripts you create

Create the skill in the `.claude/skills/` directory within the current workspace."""

        # Build system prompt for skill creator agent
        system_prompt = f"""You are a Skill Creator Agent specialized in creating Claude Code skills.

Your task is to help users create high-quality skills that extend Claude's capabilities.

IMPORTANT GUIDELINES:
1. Always use the skill-creator skill (invoke /skill-creator) to get guidance on skill creation best practices
2. Follow the skill creation workflow from the skill-creator skill
3. Create skills in the `.claude/skills/` directory
4. Ensure SKILL.md has proper YAML frontmatter with name and description
5. Keep skills concise and focused - only include what Claude needs
6. Test any scripts you create before completing

The skill-creator skill provides comprehensive guidance on:
- Skill anatomy and structure
- Progressive disclosure design
- When to use scripts, references, and assets
- Best practices for SKILL.md content

Current task: Create a skill named "{skill_name}" that {skill_description}"""

        # Create temporary agent config for skill creation
        agent_config = {
            "name": f"skill-creator-{session_id[:8] if session_id else 'new'}",
            "description": "Temporary agent for skill creation",
            "system_prompt": system_prompt,
            "allowed_tools": ["Bash", "Read", "Write", "Edit", "Glob", "Grep", "Skill","TodoWrite","Task"],
            "permission_mode": "bypassPermissions",
            "working_directory": settings.agent_workspace_dir,
            "global_user_mode": False,  # Use workspace dir, not home dir
            "enable_tool_logging": True,
            "enable_safety_checks": True,
            "model": model or "claude-sonnet-4-5-20250929",  # Default to Sonnet 4.5
        }

        logger.info(f"Running skill creator conversation for '{skill_name}', session {session_id}, model {agent_config['model']}, is_resuming={is_resuming}")

        # For resumed sessions, send session_start immediately
        # For new sessions, we'll send it after capturing SDK session_id
        if is_resuming:
            yield {
                "type": "session_start",
                "sessionId": session_id,
            }
            # Store session for resumed conversations
            title = f"Creating skill: {skill_name}"
            await session_manager.store_session(session_id, "skill-creator", title)

        # Configure Claude environment variables
        await _configure_claude_environment()

        # Build options with resume if continuing
        options = await self._build_options(agent_config, enable_skills=True, enable_mcp=False, resume_session_id=session_id if is_resuming else None)
        logger.info(f"Skill creator options - allowed_tools: {options.allowed_tools}, resume: {session_id if is_resuming else None}")
        logger.info(f"Working directory: {options.cwd}")

        # Track the actual SDK session_id
        sdk_session_id = session_id  # Will be updated for new sessions

        try:
            logger.info(f"Creating ClaudeSDKClient for skill creation...")
            async with ClaudeSDKClient(options=options) as client:
                # For resumed sessions, store client immediately
                # For new sessions, we'll store after getting SDK session_id
                if is_resuming:
                    self._clients[session_id] = client
                logger.info(f"ClaudeSDKClient created, is_resuming={is_resuming}")

                try:
                    logger.info(f"Sending skill creation query...")
                    await client.query(prompt)
                    logger.info(f"Query sent, waiting for response...")

                    message_count = 0
                    async for message in client.receive_response():
                        message_count += 1
                        logger.debug(f"Received message {message_count}: {type(message).__name__}")

                        # Capture session_id from SDK's init message (for new sessions)
                        if isinstance(message, SystemMessage) and message.subtype == 'init':
                            sdk_session_id = message.data.get('session_id')
                            logger.info(f"Captured SDK session_id from init: {sdk_session_id}")

                            # For new sessions, now we can send session_start and store session
                            if not is_resuming:
                                # Store client with SDK session_id
                                self._clients[sdk_session_id] = client

                                yield {
                                    "type": "session_start",
                                    "sessionId": sdk_session_id,
                                }

                                # Store session with SDK session_id
                                title = f"Creating skill: {skill_name}"
                                await session_manager.store_session(sdk_session_id, "skill-creator", title)
                            continue  # Don't format SystemMessage for output

                        formatted = await self._format_message(message, agent_config, sdk_session_id)
                        if formatted:
                            logger.debug(f"Formatted message type: {formatted.get('type')}")
                            yield formatted

                            # If this is an AskUserQuestion, stop and wait for user input
                            if formatted.get('type') == 'ask_user_question':
                                logger.info(f"AskUserQuestion detected, stopping to wait for user input")
                                return

                        if isinstance(message, ResultMessage):
                            logger.info(f"Skill creation conversation complete. Total messages: {message_count}")
                            yield {
                                "type": "result",
                                "session_id": sdk_session_id,
                                "duration_ms": getattr(message, 'duration_ms', 0),
                                "total_cost_usd": getattr(message, 'total_cost_usd', None),
                                "num_turns": getattr(message, 'num_turns', 1),
                                "skill_name": skill_name,
                            }
                finally:
                    # Remove client from tracking when done
                    if sdk_session_id:
                        self._clients.pop(sdk_session_id, None)
                    logger.info(f"Client removed from tracking for session {sdk_session_id}")

        except Exception as e:
            import traceback
            error_traceback = traceback.format_exc()
            logger.error(f"Error in skill creation conversation: {e}")
            logger.error(f"Full traceback:\n{error_traceback}")
            yield {
                "type": "error",
                "error": str(e),
                "detail": error_traceback,
            }


# Global instance
agent_manager = AgentManager()
