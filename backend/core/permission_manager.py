"""Permission state management for command approval and human-in-the-loop decisions.

Encapsulates all mutable permission state that was previously module-level globals
in agent_manager.py. Uses a singleton pattern to ensure exactly one instance exists,
preserving the current single-process concurrency model.

State managed:
    - _approved_commands: session ID → set of approved command hashes
    - _permission_events: request ID → asyncio.Event for signaling decisions
    - _permission_results: request ID → decision string ("approve" or "deny")
    - _permission_request_queue: asyncio.Queue for permission requests
"""

import asyncio
import hashlib
import logging
from typing import Any, Optional

from database import db

logger = logging.getLogger(__name__)


class PermissionManager:
    """Manages command approval tracking and permission request/response flow.

    Provides methods for:
    - Hashing and tracking approved commands per session
    - Waiting for and setting human permission decisions
    - Accessing the shared permission request queue
    """

    def __init__(self) -> None:
        self._approved_commands: dict[str, set[str]] = {}
        self._permission_events: dict[str, asyncio.Event] = {}
        self._permission_results: dict[str, str] = {}
        self._permission_request_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

    def hash_command(self, command: str) -> str:
        """Create a hash of the command for approval tracking."""
        return hashlib.sha256(command.encode()).hexdigest()[:16]

    def approve_command(self, session_id: str, command: str) -> None:
        """Mark a command as approved for a session."""
        if session_id not in self._approved_commands:
            self._approved_commands[session_id] = set()
        command_hash = self.hash_command(command)
        self._approved_commands[session_id].add(command_hash)
        logger.info(f"Command approved for session {session_id} (hash: {command_hash})")

    def is_command_approved(self, session_id: str, command: str) -> bool:
        """Check if a command was previously approved for a session."""
        if session_id not in self._approved_commands:
            return False
        command_hash = self.hash_command(command)
        return command_hash in self._approved_commands[session_id]

    def clear_session_approvals(self, session_id: str) -> None:
        """Clear all approved commands for a session."""
        self._approved_commands.pop(session_id, None)

    async def wait_for_permission_decision(self, request_id: str, timeout: int = 300) -> str:
        """Wait for user permission decision.

        Args:
            request_id: The permission request ID
            timeout: Timeout in seconds (default 5 minutes)

        Returns:
            'approve' or 'deny'
        """
        event = asyncio.Event()
        self._permission_events[request_id] = event

        try:
            await asyncio.wait_for(event.wait(), timeout=timeout)
            return self._permission_results.get(request_id, "deny")
        except asyncio.TimeoutError:
            # Update database with expired status
            await db.permission_requests.update(request_id, {"status": "expired"})
            return "deny"
        finally:
            self._permission_events.pop(request_id, None)
            self._permission_results.pop(request_id, None)

    def set_permission_decision(self, request_id: str, decision: str) -> None:
        """Set the user's permission decision and signal waiting tasks."""
        self._permission_results[request_id] = decision
        if request_id in self._permission_events:
            self._permission_events[request_id].set()
        else:
            # No waiter — clean up immediately to prevent memory leak
            self._permission_results.pop(request_id, None)

    def get_permission_queue(self) -> asyncio.Queue[dict[str, Any]]:
        """Return the permission request queue."""
        return self._permission_request_queue


# Module-level singleton
permission_manager = PermissionManager()
