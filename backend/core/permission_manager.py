"""Permission state management for command approval and human-in-the-loop decisions.

Encapsulates all mutable permission state. Uses a singleton pattern to ensure exactly one instance exists,
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
import json
import logging
import os
import tempfile
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


def _default_persist_path() -> Path:
    """Canonical daemon-persistent location for pending approvals.

    Mirrors config.get_data_dir() (Path.home()/'.swarm-ai') without importing the
    FastAPI config module (keeps PermissionManager import-light for tests/CLI).
    """
    return Path.home() / ".swarm-ai" / "pending_approvals.json"

# How long a surfaced dangerous-command approval prompt waits for the user's
# decision before auto-denying ("审批超时"). Set to 4h to match
# ask_question_manager.ASK_ANSWER_TIMEOUT_SECONDS — a human may step away (meal,
# meeting, nap) and a HITL approval should survive that, exactly like an
# AskUserQuestion does. Was 300s (5min, run_6e780e00): once the artificial 5s
# chain-timeout guillotine was removed (hook_builder no_timeout), 300s became the
# real ceiling — too short and inconsistent with the 4h ask gate. The lifecycle
# WAITING_INPUT watchdog (14700s / 4h05m) is sized strictly GREATER than this, so
# a 4h approval block is fully accommodated with headroom (the watchdog remains
# the ultimate backstop against a truly-stuck slot). MUST stay < that watchdog.
PERMISSION_ANSWER_TIMEOUT_SECONDS = 14400  # 4 hours


class PermissionManager:
    """Manages command approval tracking and permission request/response flow.

    Provides methods for:
    - Hashing and tracking approved commands per session
    - Waiting for and setting human permission decisions
    - Per-session permission request queues for parallel session isolation
    """

    # Bound on retained RESOLVED records. Resolved requests are kept (not deleted) so
    # is_resolved() can recognize an already-decided (session,tool_call) across a daemon
    # restart — the idempotency guard. But they must not grow without limit: on load we
    # keep at most this many most-recent resolved records (pending are always kept).
    MAX_RESOLVED_RETAINED = 200

    def __init__(self, persist_path: "str | Path | None" = None) -> None:
        self._approved_commands: dict[str, set[str]] = {}
        self._permission_events: dict[str, asyncio.Event] = {}
        self._permission_results: dict[str, str] = {}
        # Per-session permission request queues — each session gets its own
        # queue so parallel sessions never compete or busy-loop.
        self._session_queues: dict[str, asyncio.Queue[dict[str, Any]]] = {}
        # Durable store for pending permission requests. Mirrored to disk so a pending
        # approval survives a DAEMON RESTART (the in-memory dict alone only survived a
        # subprocess respawn). Idempotency across a restart-replayed resolve is keyed by
        # (session_id, tool_call_id). Guarded by a threading.Lock (Gate-1 B2): the class
        # is synchronous and concurrent sessions mutate this dict, so mutate+persist must
        # be atomic to avoid lost writes.
        #
        # persist_path=None → default daemon location. Tests pass a tmp path. A caller
        # that truly wants in-memory-only can pass a path in a throwaway dir; the default
        # singleton (bottom of module) uses the real ~/.swarm-ai location.
        self._persist_path: Path = (
            Path(persist_path) if persist_path is not None else _default_persist_path()
        )
        self._lock = threading.Lock()
        self._pending_requests: dict[str, dict[str, Any]] = self._load_pending()

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

    # -- durable pending-request store ------------------------------------------
    def _load_pending(self) -> dict[str, dict[str, Any]]:
        """Load the pending-request store from disk. Fail-OPEN: a missing, unreadable,
        or corrupt file yields an empty store — never crash the daemon on boot."""
        try:
            raw = self._persist_path.read_text(encoding="utf-8")
        except (OSError, FileNotFoundError):
            return {}
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            logger.warning(
                "pending_approvals store at %s is corrupt — starting empty",
                self._persist_path,
            )
            return {}
        if not isinstance(data, dict):
            return {}
        return self._prune_resolved(data)

    def _prune_resolved(
        self, store: dict[str, dict[str, Any]]
    ) -> dict[str, dict[str, Any]]:
        """Keep ALL pending requests + at most MAX_RESOLVED_RETAINED most-recent
        resolved ones. Resolved records are retained for restart-idempotency
        (is_resolved) but bounded so the store can't grow forever. 'Most recent' =
        by resolved_at (falls back to created_at) descending."""
        pending = {k: v for k, v in store.items() if v.get("status", "pending") == "pending"}
        resolved = [
            (k, v) for k, v in store.items() if v.get("status", "pending") != "pending"
        ]
        if len(resolved) <= self.MAX_RESOLVED_RETAINED:
            return store
        resolved.sort(
            key=lambda kv: str(kv[1].get("resolved_at") or kv[1].get("created_at") or ""),
            reverse=True,
        )
        kept = dict(resolved[: self.MAX_RESOLVED_RETAINED])
        kept.update(pending)
        return kept

    def _save_pending_locked(self) -> None:
        """Persist the store atomically (mkstemp + os.replace, matching escalation.py).
        MUST be called with self._lock held."""
        try:
            self._persist_path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(
                dir=str(self._persist_path.parent), suffix=".tmp"
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(self._pending_requests, f, default=str)
                os.replace(tmp, self._persist_path)
            finally:
                # If replace succeeded the tmp is gone; unlink is a best-effort cleanup
                # for the write-failure path only.
                Path(tmp).unlink(missing_ok=True)
        except OSError as e:
            # Persistence is durability, not correctness — a disk error must not break
            # the live in-memory approval flow. Log and continue.
            logger.warning("failed to persist pending_approvals: %s", e)

    def store_pending_request(self, request_data: dict[str, Any]) -> None:
        """Store a pending permission request (durable — mirrored to disk)."""
        with self._lock:
            self._pending_requests[request_data["id"]] = request_data
            self._save_pending_locked()

    def get_pending_request(self, request_id: str) -> Optional[dict[str, Any]]:
        """Retrieve a pending permission request by ID."""
        return self._pending_requests.get(request_id)

    def update_pending_request(self, request_id: str, updates: dict[str, Any]) -> None:
        """Update fields on a pending permission request (durable)."""
        with self._lock:
            if request_id in self._pending_requests:
                self._pending_requests[request_id].update(updates)
                self._save_pending_locked()

    def remove_pending_request(self, request_id: str) -> None:
        """Retire a resolved permission request. It is NOT deleted — it is MARKED
        ``status='resolved'`` (if still 'pending') and KEPT, so is_resolved() can
        recognize an already-decided (session, tool_call) across a daemon restart (the
        idempotency guard). Deleting it here — the original bug (Gate-2 CRITICAL) — made
        is_resolved dead code: the real gate does store(pending)→wait→remove, so no
        resolved record ever survived a turn and every restart-replayed call re-prompted.
        Retained resolved records are bounded by _prune_resolved on load.

        get_pending_for_session already filters to status=='pending', so a retired
        record never re-surfaces as a live prompt; chat.py's get_pending_request runs
        while the request is still pending (before this call), so it is unaffected."""
        with self._lock:
            req = self._pending_requests.get(request_id)
            if req is None:
                return
            # Preserve an explicit decision if one was already written
            # (session_unit sets status=approve/deny before signalling); otherwise
            # mark a generic 'resolved' so is_resolved recognizes it.
            if req.get("status", "pending") == "pending":
                req["status"] = "resolved"
            req.setdefault("resolved_at", datetime.now().isoformat())
            self._save_pending_locked()

    def is_resolved(self, session_id: str, tool_call_id: str) -> bool:
        """True iff a retained request for this (session_id, tool_call_id) has been
        resolved (status != 'pending'). The restart-idempotency guard: after a daemon
        restart, a replayed decision for an already-resolved (session, tool_call) is a
        no-op, never a double-fire. Returns False for unknown keys (nothing to skip)."""
        if not tool_call_id:
            return False
        for req in self._pending_requests.values():
            if (
                req.get("session_id") == session_id
                and req.get("tool_call_id") == tool_call_id
                and req.get("status", "pending") != "pending"
            ):
                return True
        return False

    def get_pending_for_session(self, session_id: str) -> list[dict[str, Any]]:
        """Return all still-pending permission requests for a session.

        Used by the reconnect/resume re-surface path (chat.py streaming-state):
        the durable store survives respawn, so when a session's transient
        ``_pending_question`` is gone but a request is still ``status=='pending'``
        and has a LIVE waiter, the frontend can re-render the approval prompt.

        Filters to ``status == 'pending'`` only — expired/resolved requests must
        never be re-surfaced. Returns a list (FIFO order of insertion).
        """
        return [
            req
            for req in self._pending_requests.values()
            if req.get("session_id") == session_id
            and req.get("status", "pending") == "pending"
        ]

    def has_live_waiter(self, request_id: str) -> bool:
        """True iff a ``wait_for_permission_decision`` coroutine is currently
        blocked on this request.

        This is the respawn-immune liveness signal: the event is registered on
        entry to ``wait_for_permission_decision`` and popped in its ``finally``
        (on decision, timeout, OR cancellation when the subprocess is killed and
        the hook task is torn down). So ``has_live_waiter`` is True only while a
        real awaiting hook exists to receive the decision — re-surfacing a
        request with no live waiter would let the user "approve" into the void.
        """
        return request_id in self._permission_events

    async def wait_for_permission_decision(
        self, request_id: str, timeout: int = PERMISSION_ANSWER_TIMEOUT_SECONDS
    ) -> str:
        """Wait for user permission decision.

        Args:
            request_id: The permission request ID
            timeout: Timeout in seconds (default 4 hours —
                     PERMISSION_ANSWER_TIMEOUT_SECONDS, matching the ask gate).
                     Bounded so an un-surfaced or unanswered prompt does not
                     hang the subprocess forever. The subprocess stays alive
                     in WAITING_INPUT state (protected from eviction) until the
                     decision arrives OR the timeout fires. A human may step
                     away (meal, meeting) and the HITL approval survives that;
                     the lifecycle WAITING_INPUT watchdog (4h05m, strictly
                     greater) is the ultimate backstop for a truly-stuck slot.

        Returns:
            'approve', 'deny', or 'timeout'. ``'timeout'`` is a DISTINCT
            sentinel (not folded into 'deny') so the caller can emit a visible
            "审批超时" message — a timeout is a different user-facing event than
            an explicit denial. The caller is responsible for treating
            'timeout' as deny-equivalent for the SDK decision while surfacing
            the distinct reason.
        """
        event = asyncio.Event()
        self._permission_events[request_id] = event

        try:
            await asyncio.wait_for(event.wait(), timeout=timeout)
            return self._permission_results.get(request_id, "deny")
        except asyncio.TimeoutError:
            # Update in-memory pending request with expired status
            self.update_pending_request(request_id, {"status": "expired"})
            return "timeout"
        except asyncio.CancelledError:
            # The awaiting hook coroutine was cancelled BEFORE the user decided —
            # e.g. the SDK cancelled the detached PreToolUse control-request task
            # (control_cancel_request) while the session sat in WAITING_INPUT. The
            # finally below then pops _pending_requests, so a later approve reads
            # "request not found" and the session's _pending_tool_use_id is left
            # stranded (approve-into-void deadlock, run_65f317db). This used to be
            # SILENT — log it so the desync is diagnosable, then re-raise (a cancel
            # must never be swallowed). The send-path / lifecycle / approve-endpoint
            # dead-waiter reap (has_live_waiter==False) is what actually recovers
            # the session; this log is the breadcrumb that explains WHY it fired.
            logger.warning(
                "PermissionManager: wait_for_permission_decision CANCELLED before "
                "decision for request_id=%s — popping pending request (waiter is now "
                "dead; session recovery relies on has_live_waiter==False reap)",
                request_id,
            )
            raise
        finally:
            self._permission_events.pop(request_id, None)
            self._permission_results.pop(request_id, None)
            # Guarantee _pending_requests cleanup — prevents memory leak if
            # the caller (security_hooks) fails to call remove_pending_request
            # due to an exception between store and remove. The CancelledError
            # branch above INTENTIONALLY relies on this pop (it is the desync
            # source, not a leak) — recovery is via the has_live_waiter reap.
            self._pending_requests.pop(request_id, None)

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
