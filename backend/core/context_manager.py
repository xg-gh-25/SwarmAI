"""Workspace context file manager.

This module provides the ContextManager class for managing workspace context
files (context.md and compressed-context.md) within the ContextFiles/ folder
of each SwarmWorkspace.

Context files provide workspace-specific context to agents during execution.
The system supports two context files:
- context.md: Full workspace context (user-editable)
- compressed-context.md: Summarized/truncated version for token-efficient injection

Requirements: 14.1-14.9, 29.1-29.10
"""
import logging
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

import anyio

from database import db

logger = logging.getLogger(__name__)

# Staleness threshold for compressed context
COMPRESSED_CONTEXT_MAX_AGE = timedelta(hours=24)

# Default token budget for context injection
DEFAULT_TOKEN_BUDGET = 4000

# Approximate characters per token (simple heuristic)
CHARS_PER_TOKEN = 4


def estimate_tokens(text: str) -> int:
    """Estimate the number of tokens in a text string.

    Uses a simple heuristic: 1 token ≈ 4 characters.

    Args:
        text: The text to estimate tokens for.

    Returns:
        Estimated token count.
    """
    if not text:
        return 0
    return len(text) // CHARS_PER_TOKEN


def truncate_to_token_budget(text: str, token_budget: int) -> str:
    """Truncate text to fit within a token budget.

    Args:
        text: The text to truncate.
        token_budget: Maximum number of tokens allowed.

    Returns:
        Truncated text that fits within the budget.
    """
    if not text:
        return ""
    max_chars = token_budget * CHARS_PER_TOKEN
    if len(text) <= max_chars:
        return text
    truncated = text[:max_chars]
    # Try to break at a newline for cleaner output
    last_newline = truncated.rfind("\n")
    if last_newline > max_chars // 2:
        truncated = truncated[:last_newline]
    return truncated + "\n\n[Context truncated to fit token budget]"


class ContextManager:
    """Manages workspace context files for agent context injection.

    Handles reading, writing, compressing, and injecting workspace context
    from the ContextFiles/ folder within each SwarmWorkspace.

    Key Features:
    - Read/write context.md for full workspace context
    - Generate compressed-context.md for token-efficient injection
    - Inject context with configurable token budget (default 4000)
    - Prefer fresh compressed context (<24h), fallback to context.md

    Requirements: 14.1-14.9, 29.1-29.10
    """

    def _get_context_dir(self, workspace_path: str) -> Path:
        """Get the ContextFiles directory path for a workspace.

        Args:
            workspace_path: The workspace's file_path from the database.

        Returns:
            Path to the ContextFiles/ directory.
        """
        from core.swarm_workspace_manager import swarm_workspace_manager
        expanded = swarm_workspace_manager.expand_path(workspace_path)
        return Path(expanded) / "ContextFiles"

    async def _get_workspace(self, workspace_id: str) -> Optional[dict]:
        """Get workspace record from the database.

        Args:
            workspace_id: The workspace ID.

        Returns:
            Workspace dict if found, None otherwise.
        """
        return await db.swarm_workspaces.get(workspace_id)

    async def get_context(self, workspace_id: str) -> str:
        """Read the workspace context from ContextFiles/context.md.

        Args:
            workspace_id: The workspace ID.

        Returns:
            The content of context.md, or empty string if not found.

        Raises:
            ValueError: If workspace_id is not found.

        Validates: Requirements 14.2, 29.1, 29.9
        """
        workspace = await self._get_workspace(workspace_id)
        if not workspace:
            raise ValueError(f"Workspace {workspace_id} not found")

        context_dir = self._get_context_dir(workspace["file_path"])
        context_file = context_dir / "context.md"

        try:
            content = await anyio.to_thread.run_sync(
                lambda: context_file.read_text(encoding="utf-8")
                if context_file.exists()
                else ""
            )
            return content
        except Exception as e:
            logger.warning(f"Failed to read context.md for workspace {workspace_id}: {e}")
            return ""

    async def update_context(self, workspace_id: str, content: str) -> None:
        """Write content to the workspace's ContextFiles/context.md.

        Creates the ContextFiles/ directory if it doesn't exist.

        Args:
            workspace_id: The workspace ID.
            content: The new context content to write.

        Raises:
            ValueError: If workspace_id is not found.

        Validates: Requirements 29.1, 29.3, 29.5, 29.9
        """
        workspace = await self._get_workspace(workspace_id)
        if not workspace:
            raise ValueError(f"Workspace {workspace_id} not found")

        context_dir = self._get_context_dir(workspace["file_path"])
        context_file = context_dir / "context.md"

        # Ensure ContextFiles/ directory exists
        await anyio.to_thread.run_sync(
            lambda: context_dir.mkdir(parents=True, exist_ok=True)
        )

        await anyio.to_thread.run_sync(
            lambda: context_file.write_text(content, encoding="utf-8")
        )
        logger.info(f"Updated context.md for workspace {workspace_id}")

    async def compress_context(self, workspace_id: str) -> str:
        """Generate compressed-context.md from context.md.

        For now, compression is a simple truncation to fit within the
        default token budget, with a note indicating truncation occurred.
        Actual AI-powered compression can be added later.

        Args:
            workspace_id: The workspace ID.

        Returns:
            The compressed context content.

        Raises:
            ValueError: If workspace_id is not found.

        Validates: Requirements 14.9, 29.2, 29.4, 29.6, 29.10
        """
        workspace = await self._get_workspace(workspace_id)
        if not workspace:
            raise ValueError(f"Workspace {workspace_id} not found")

        # Read the full context
        full_context = await self.get_context(workspace_id)
        if not full_context:
            compressed = ""
        else:
            compressed = truncate_to_token_budget(full_context, DEFAULT_TOKEN_BUDGET)

        # Write compressed-context.md
        context_dir = self._get_context_dir(workspace["file_path"])
        compressed_file = context_dir / "compressed-context.md"

        await anyio.to_thread.run_sync(
            lambda: context_dir.mkdir(parents=True, exist_ok=True)
        )
        await anyio.to_thread.run_sync(
            lambda: compressed_file.write_text(compressed, encoding="utf-8")
        )

        logger.info(f"Generated compressed-context.md for workspace {workspace_id}")
        return compressed

    async def _is_compressed_context_fresh(self, compressed_file: Path) -> bool:
        """Check if compressed-context.md is fresh (modified within 24 hours).

        Args:
            compressed_file: Path to compressed-context.md.

        Returns:
            True if the file exists, is non-empty, and was modified within 24 hours.

        Validates: Requirements 14.3, 14.4, 29.6
        """
        def _check() -> bool:
            if not compressed_file.exists():
                return False
            content = compressed_file.read_text(encoding="utf-8")
            if not content.strip():
                return False
            mtime = datetime.fromtimestamp(
                compressed_file.stat().st_mtime, tz=timezone.utc
            )
            age = datetime.now(timezone.utc) - mtime
            return age < COMPRESSED_CONTEXT_MAX_AGE

        try:
            return await anyio.to_thread.run_sync(_check)
        except Exception as e:
            logger.warning(f"Failed to check compressed context freshness: {e}")
            return False

    async def _build_capabilities_summary(self, workspace_id: str) -> str:
        """Build a summary of effective Skills, MCPs, and Knowledgebases.

        Args:
            workspace_id: The workspace ID.

        Returns:
            A formatted string summarizing the workspace's effective capabilities.
            Returns empty string if no capabilities are configured.

        Validates: Requirement 14.8
        """
        from database import db

        parts = []

        try:
            all_skills = await db.skills.list()
            ws_configs = await db.workspace_skills.list_by_workspace(workspace_id)
            enabled_ids = {c["skill_id"] for c in ws_configs if c.get("enabled", 1)}
            skill_names = [s["id"] for s in all_skills if s["id"] in enabled_ids]
            if skill_names:
                parts.append(f"Enabled Skills: {', '.join(skill_names)}")
        except Exception as e:
            logger.debug(f"Could not fetch effective skills for {workspace_id}: {e}")

        try:
            all_mcps = await db.mcp_servers.list()
            ws_mcp_configs = await db.workspace_mcps.list_by_workspace(workspace_id)
            enabled_mcp_ids = {c["mcp_server_id"] for c in ws_mcp_configs if c.get("enabled", 1)}
            mcp_names = [m["id"] for m in all_mcps if m["id"] in enabled_mcp_ids]
            if mcp_names:
                parts.append(f"Enabled MCPs: {', '.join(mcp_names)}")
        except Exception as e:
            logger.debug(f"Could not fetch effective MCPs for {workspace_id}: {e}")

        try:
            kbs = await db.workspace_knowledgebases.list_by_workspace(workspace_id)
            if kbs:
                kb_names = [kb["display_name"] for kb in kbs]
                parts.append(f"Knowledgebases: {', '.join(kb_names)}")
        except Exception as e:
            logger.debug(f"Could not fetch effective knowledgebases for {workspace_id}: {e}")

        if not parts:
            return ""

        return "### Effective Configuration\n" + "\n".join(parts)

    async def inject_context(
        self, workspace_id: str, token_budget: int = DEFAULT_TOKEN_BUDGET
    ) -> str:
        """Build context injection string for an agent's system prompt.

        Prefers compressed-context.md if fresh (<24 hours). Falls back to
        context.md if compressed is stale or missing. Truncates to fit
        within the token budget.

        The injected context is prefixed with a workspace header and includes
        a summary of effective Skills, MCPs, and Knowledgebases.

        Args:
            workspace_id: The workspace ID.
            token_budget: Maximum tokens for the context injection (default 4000).

        Returns:
            The context string ready for injection into an agent prompt.
            Returns empty string if workspace not found or no context available.

        Validates: Requirements 14.1-14.8
        """
        workspace = await self._get_workspace(workspace_id)
        if not workspace:
            logger.warning(f"Workspace {workspace_id} not found for context injection")
            return ""

        context_dir = self._get_context_dir(workspace["file_path"])
        compressed_file = context_dir / "compressed-context.md"
        context_file = context_dir / "context.md"

        context_content = ""

        # Requirement 14.3: Prefer compressed-context.md if fresh
        if await self._is_compressed_context_fresh(compressed_file):
            try:
                context_content = await anyio.to_thread.run_sync(
                    lambda: compressed_file.read_text(encoding="utf-8")
                )
                logger.debug(f"Using fresh compressed context for workspace {workspace_id}")
            except Exception as e:
                logger.warning(f"Failed to read compressed-context.md: {e}")
                context_content = ""

        # Requirement 14.4: Fallback to context.md
        if not context_content:
            try:
                content = await anyio.to_thread.run_sync(
                    lambda: context_file.read_text(encoding="utf-8")
                    if context_file.exists()
                    else ""
                )
                context_content = content
                logger.debug(f"Using full context for workspace {workspace_id}")
            except Exception as e:
                logger.warning(f"Failed to read context.md: {e}")
                context_content = ""

        # Requirement 14.8: Include effective capabilities summary
        capabilities_summary = await self._build_capabilities_summary(workspace_id)

        # Combine context content and capabilities summary
        combined_parts = []
        if context_content.strip():
            combined_parts.append(context_content.strip())
        if capabilities_summary:
            combined_parts.append(capabilities_summary)

        if not combined_parts:
            return ""

        combined_content = "\n\n".join(combined_parts)

        # Requirement 14.7: Prefix with workspace header
        workspace_name = workspace.get("name", "Unknown")
        header = f"Current Workspace: {workspace_name}\n\n"

        # Requirement 14.5: Enforce token budget
        header_tokens = estimate_tokens(header)
        remaining_budget = max(0, token_budget - header_tokens)
        truncated_content = truncate_to_token_budget(combined_content, remaining_budget)

        return header + truncated_content


# Global instance
context_manager = ContextManager()
