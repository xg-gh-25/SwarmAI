"""Permission state management for command approval and human-in-the-loop decisions.

Encapsulates all mutable permission state that was previously module-level globals
in agent_manager.py. Uses a singleton pattern to ensure exactly one instance exists,
preserving the current single-process concurrency model.

State managed:
    - _approved_commands: session ID → set of approved command hashes
    - _permission_events: request ID → asyncio.Event for signaling decisions
    - _permission_results: request ID → decision string ("approve" or "deny")
    - _session_queues: session ID → per-session asyncio.Queue for permission requests

Per-session queue design:
    Each active session gets its own ``asyncio.Queue`` via ``get_session_queue()``.
    The security hook writes directly to the session's queue using the SDK session ID.
    Each session's queue is isolated, preventing cross-session contention.
"""

import asyncio
import hashlib
import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


class PermissionManager:
    """Manages command approval tracking and permission request/response flow.

    Provides methods for:
    - Hashing and tracking approved commands per session
    - Waiting for and setting human permission decisions
    - Per-session permission request queues for parallel session isolation
    """

    def __init__(self) -> None:
        self._approved_commands: dict[str, set[str]] = {}
        self._permission_events: dict[str, asyncio.Event] = {}
        self._permission_results: dict[str, str] = {}
        # Per-session permission request queues — each session gets its own
        # queue so parallel sessions never compete or busy-loop.
        self._session_queues: dict[str, asyncio.Queue[dict[str, Any]]] = {}
        # In-memory store for pending permission requests (replaces DB table)
        self._pending_requests: dict[str, dict[str, Any]] = {}

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

    def store_pending_request(self, request_data: dict[str, Any]) -> None:
        """Store a pending permission request in memory (replaces DB storage)."""
        self._pending_requests[request_data["id"]] = request_data

    def get_pending_request(self, request_id: str) -> Optional[dict[str, Any]]:
        """Retrieve a pending permission request by ID."""
        return self._pending_requests.get(request_id)

    def update_pending_request(self, request_id: str, updates: dict[str, Any]) -> None:
        """Update fields on a pending permission request."""
        if request_id in self._pending_requests:
            self._pending_requests[request_id].update(updates)

    def remove_pending_request(self, request_id: str) -> None:
        """Remove a pending permission request after it's been resolved."""
        self._pending_requests.pop(request_id, None)

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
            # Update in-memory pending request with expired status
            self.update_pending_request(request_id, {"status": "expired"})
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

    # ------------------------------------------------------------------
    # Per-session permission request queues
    # ------------------------------------------------------------------

    def get_session_queue(self, session_id: str) -> asyncio.Queue[dict[str, Any]]:
        """Return (or create) the permission request queue for a specific session.

        Each session gets its own queue so that parallel sessions never
        compete or busy-loop.  Queues are lazily created and cleaned up
        via ``remove_session_queue()`` when the session ends.

        Args:
            session_id: The SDK session ID.

        Returns:
            The per-session asyncio.Queue.
        """
        if session_id not in self._session_queues:
            self._session_queues[session_id] = asyncio.Queue()
            logger.debug("Created permission queue for session %s", session_id)
        return self._session_queues[session_id]

    def remove_session_queue(self, session_id: str) -> None:
        """Remove the permission request queue for a session.

        Called during session cleanup to free memory.  Any items still in
        the queue are discarded (the session is ending anyway).
        """
        removed = self._session_queues.pop(session_id, None)
        if removed:
            logger.debug("Removed permission queue for session %s", session_id)

    async def enqueue_permission_request(
        self, session_id: str, request: dict[str, Any]
    ) -> None:
        """Enqueue a permission request to the correct session's queue.

        Called by the security hook when a dangerous command is detected.
        Routes the request directly to the session's queue — no global
        queue, no re-enqueuing, no cross-session contention.

        Args:
            session_id: The SDK session ID that owns this request.
            request: The permission request dict.
        """
        queue = self.get_session_queue(session_id)
        await queue.put(request)
        logger.info(
            "Enqueued permission request %s for session %s",
            request.get("requestId", "?"),
            session_id,
        )



# Module-level singleton
permission_manager = PermissionManager()
