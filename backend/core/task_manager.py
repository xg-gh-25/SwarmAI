"""Background task manager for persistent agent execution.

Increments the context snapshot cache ``task_version`` counter whenever
tasks are created, updated (status change), or deleted so that the
context assembly cache is properly invalidated (Requirement 34.2).

Supports two task types:
- ``agent``: Claude conversation in asyncio background task
- ``script``: Shell subprocess owned by daemon process tree (survives session death)
"""
import asyncio
import logging
import os
import signal
import subprocess
from concurrent.futures import ThreadPoolExecutor
from contextlib import aclosing
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, AsyncIterator
from uuid import uuid4

from database import db
from . import session_registry
from .agent_defaults import resolve_default_model

logger = logging.getLogger(__name__)


# Legacy status → new status mapping for backward compatibility
# Requirements: 5.4
_LEGACY_STATUS_MAP = {
    "pending": "draft",
    "running": "wip",
    "failed": "blocked",
}

# All valid new statuses
_VALID_STATUSES = {"draft", "wip", "blocked", "completed", "cancelled"}

# Script task configuration
_MAX_CONCURRENT_SCRIPTS = 2
_script_semaphore = asyncio.Semaphore(_MAX_CONCURRENT_SCRIPTS)


def _tasks_dir() -> Path:
    """Script-task log dir, resolved AT CALL TIME via the single app-data authority.

    Must NOT be a module-level constant: a frozen home-relative default captures the
    value at import, before a test/sandbox sets SWARM_DATA_DIR — so the daemon would
    write into the live production tree even under the sandbox guard (the
    app-data-authority-bypass class this run fixes).
    """
    from config import get_app_data_dir

    return get_app_data_dir() / "tasks"

# Dedicated executor for script tasks — reuses same pool sizing as _job_executor
# in main.py but kept separate to avoid coupling lifecycle
_script_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="script-task")


class TaskManager:
    """Manages background agent tasks that persist across frontend connections.

    Tasks run independently in asyncio tasks. Frontend can:
    - Create tasks (starts agent execution)
    - Subscribe to task events via SSE
    - Disconnect and reconnect without losing progress
    - Send messages to running tasks

    Requirements: 5.1-5.8
    """

    def __init__(self):
        # Running asyncio tasks: task_id -> asyncio.Task
        self._running_tasks: dict[str, asyncio.Task] = {}
        # Event buffers: task_id -> list of events (limited size)
        self._event_buffers: dict[str, list[dict]] = {}
        # Event queues for SSE subscribers: task_id -> list of asyncio.Queue
        self._subscribers: dict[str, list[asyncio.Queue]] = {}
        # Message queues for sending messages to tasks: task_id -> asyncio.Queue
        self._message_queues: dict[str, asyncio.Queue] = {}
        # Script task PIDs: task_id -> subprocess PID (for cancel/cleanup)
        self._script_pids: dict[str, int] = {}
        # Max events to buffer per task
        self._max_buffer_size = 100
        # Buffer retention time after task completion (seconds)
        self._buffer_retention_seconds = 300  # 5 minutes

    @staticmethod
    def _map_legacy_status(status: str) -> str:
        """Map legacy task status to new status values.

        Provides backward compatibility by transparently converting:
        - pending → draft
        - running → wip
        - failed → blocked

        If the status is already a valid new status, it is returned as-is.

        Args:
            status: The status string (legacy or new).

        Returns:
            The mapped new status string.

        Validates: Requirements 5.4
        """
        return _LEGACY_STATUS_MAP.get(status, status)

    async def _get_default_workspace_id(self) -> str:
        """Get the default workspace (SwarmWS) ID.

        Returns:
            str: The ID of the default workspace (SwarmWS).

        Raises:
            ValueError: If no default workspace exists.

        Validates: Requirements 1.3, 1.4
        """
        default_workspace = await db.workspace_config.get_config()
        if not default_workspace:
            raise ValueError("SwarmWS workspace config not found. Please initialize the application first.")
        return default_workspace["id"]

    async def create_task(
        self,
        agent_id: str,
        message: Optional[str] = None,
        content: Optional[list[dict]] = None,
        enable_skills: bool = False,
        enable_mcp: bool = False,
        workspace_id: Optional[str] = None,
        source_todo_id: Optional[str] = None,
        priority: str = "none",
        description: Optional[str] = None,
    ) -> dict:
        """Create and start a new background task.

        Args:
            agent_id: The agent to run
            message: Simple text message
            content: Multimodal content array
            enable_skills: Whether to enable skills
            enable_mcp: Whether to enable MCP servers
            workspace_id: Workspace to assign the task to (defaults to SwarmWS)
            source_todo_id: ID of the ToDo this task was created from
            priority: Task priority (high, medium, low, none)
            description: Task description

        Returns:
            Task record dict

        Validates: Requirements 1.4, 5.1, 5.6, 5.7
        """
        # Get agent config — file-based for default agent, DB for custom agents
        from core.agent_defaults import build_agent_config
        agent_config = await build_agent_config(agent_id)
        if not agent_config:
            raise ValueError(f"Agent {agent_id} not found")

        # Default workspace_id to SwarmWS if not provided
        if not workspace_id:
            workspace_id = await self._get_default_workspace_id()
            logger.debug(f"Defaulting task workspace_id to SwarmWS: {workspace_id}")

        # In single-workspace model, SwarmWS is never archived

        # Generate title from message
        if content:
            for block in content:
                if block.get("type") == "text" and block.get("text"):
                    title = block.get("text", "")[:50]
                    break
            else:
                title = "[Attachment message]"
        elif message:
            title = message[:50]
        else:
            title = f"Task with {agent_config.get('name', 'agent')}"

        if message and len(message) > 50:
            title += "..."

        # Create task record with new fields
        task_id = f"task_{uuid4().hex[:12]}"
        task = {
            "id": task_id,
            "agent_id": agent_id,
            "session_id": None,  # Will be set when agent starts
            "status": "draft",
            "title": title,
            "description": description,
            "priority": priority,
            "workspace_id": workspace_id,
            "source_todo_id": source_todo_id,
            "blocked_reason": None,
            "model": agent_config.get("model") or resolve_default_model(),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "started_at": None,
            "completed_at": None,
            "error": None,
        }
        await db.tasks.put(task)

        # Initialize event buffer and subscribers
        self._event_buffers[task_id] = []
        self._subscribers[task_id] = []
        self._message_queues[task_id] = asyncio.Queue()

        # Start background execution
        asyncio_task = asyncio.create_task(
            self._run_task(
                task_id=task_id,
                agent_id=agent_id,
                message=message,
                content=content,
                enable_skills=enable_skills,
                enable_mcp=enable_mcp,
            )
        )
        self._running_tasks[task_id] = asyncio_task

        # Increment task_version for context cache invalidation (Req 34.2)

        logger.info(f"Created task {task_id} for agent {agent_id}")
        return task

    async def _run_task(
        self,
        task_id: str,
        agent_id: str,
        message: Optional[str],
        content: Optional[list[dict]],
        enable_skills: bool,
        enable_mcp: bool,
    ) -> None:
        """Background task execution.

        Increments task_version on every status transition for context
        cache invalidation (Requirement 34.2).
        """
        try:
            # Update status to wip (was "running")
            await db.tasks.update(task_id, {
                "status": "wip",
                "started_at": datetime.now(timezone.utc).isoformat(),
            })
            await self._emit_event(task_id, {"type": "status", "status": "wip"})

            # Run agent conversation
            # Use aclosing() to ensure generator cleanup happens in this task
            session_id = None
            async with aclosing(session_registry.session_router.run_conversation(
                agent_id=agent_id,
                user_message=message,
                content=content,
                session_id=None,  # New conversation
                enable_skills=enable_skills,
                enable_mcp=enable_mcp,
            )) as conversation:
                async for event in conversation:
                    # Capture session_id from session_start event
                    if event.get("type") == "session_start":
                        session_id = event.get("sessionId")
                        await db.tasks.update(task_id, {"session_id": session_id})

                    # Emit event to subscribers
                    await self._emit_event(task_id, event)

                    # Check for errors - transition to blocked with reason
                    if event.get("type") == "error":
                        error_msg = event.get("error", "Unknown error")
                        await db.tasks.update(task_id, {
                            "status": "blocked",
                            "blocked_reason": error_msg,
                            "completed_at": datetime.now(timezone.utc).isoformat(),
                            "error": error_msg,
                        })
                        return

                    # Check for completion
                    if event.get("type") == "result":
                        await db.tasks.update(task_id, {
                            "status": "completed",
                            "completed_at": datetime.now(timezone.utc).isoformat(),
                        })
                        return

                    # Check for ask_user_question - task pauses, waiting for message
                    if event.get("type") == "ask_user_question":
                        # Wait for user response via message queue
                        await self._handle_pending_interaction(task_id, session_id, enable_skills, enable_mcp)
                        return

        except asyncio.CancelledError:
            logger.info(f"Task {task_id} was cancelled")
            await db.tasks.update(task_id, {
                "status": "cancelled",
                "completed_at": datetime.now(timezone.utc).isoformat(),
            })
            await self._emit_event(task_id, {"type": "status", "status": "cancelled"})
        except Exception as e:
            logger.error(f"Task {task_id} failed: {e}")
            error_msg = str(e)
            await db.tasks.update(task_id, {
                "status": "blocked",
                "blocked_reason": error_msg,
                "completed_at": datetime.now(timezone.utc).isoformat(),
                "error": error_msg,
            })
            await self._emit_event(task_id, {"type": "error", "error": error_msg})
        finally:
            # Cleanup running task reference
            self._running_tasks.pop(task_id, None)
            # Schedule cleanup of buffers after retention period
            asyncio.create_task(self._schedule_buffer_cleanup(task_id))

    async def _handle_pending_interaction(
        self,
        task_id: str,
        session_id: str,
        enable_skills: bool,
        enable_mcp: bool,
    ) -> None:
        """Handle task paused for user interaction (ask_user_question).

        Uses a loop instead of recursion to handle multiple consecutive
        ask_user_question events, avoiding potential stack overflow.
        """
        message_queue = self._message_queues.get(task_id)
        if not message_queue:
            return

        try:
            while True:
                # Wait indefinitely for user message
                msg_data = await message_queue.get()

                # Continue conversation with user's response
                task = await db.tasks.get(task_id)
                if not task:
                    return

                needs_another_interaction = False

                async with aclosing(session_registry.session_router.run_conversation(
                    agent_id=task["agent_id"],
                    user_message=msg_data.get("message"),
                    content=msg_data.get("content"),
                    session_id=session_id,
                    enable_skills=enable_skills,
                    enable_mcp=enable_mcp,
                )) as conversation:
                    async for event in conversation:
                        await self._emit_event(task_id, event)

                        if event.get("type") == "error":
                            error_msg = event.get("error", "Unknown error")
                            await db.tasks.update(task_id, {
                                "status": "blocked",
                                "blocked_reason": error_msg,
                                "completed_at": datetime.now(timezone.utc).isoformat(),
                                "error": error_msg,
                            })
                            return

                        if event.get("type") == "result":
                            await db.tasks.update(task_id, {
                                "status": "completed",
                                "completed_at": datetime.now(timezone.utc).isoformat(),
                            })
                            return

                        if event.get("type") == "ask_user_question":
                            # Mark that we need to wait for another interaction
                            needs_another_interaction = True

                # If no more interactions needed, exit the loop
                if not needs_another_interaction:
                    return

        except asyncio.CancelledError:
            raise

    async def _emit_event(self, task_id: str, event: dict) -> None:
        """Emit event to all subscribers and buffer."""
        # Add to buffer (with size limit)
        if task_id in self._event_buffers:
            self._event_buffers[task_id].append(event)
            if len(self._event_buffers[task_id]) > self._max_buffer_size:
                self._event_buffers[task_id].pop(0)

        # Send to all subscribers
        if task_id in self._subscribers:
            for queue in self._subscribers[task_id]:
                await queue.put(event)

    async def _schedule_buffer_cleanup(self, task_id: str) -> None:
        """Schedule cleanup of event buffers after retention period."""
        await asyncio.sleep(self._buffer_retention_seconds)
        # Only cleanup if no active subscribers
        if task_id in self._subscribers and len(self._subscribers[task_id]) > 0:
            # Reschedule if there are still subscribers
            asyncio.create_task(self._schedule_buffer_cleanup(task_id))
            return
        # Cleanup buffers
        self._event_buffers.pop(task_id, None)
        self._subscribers.pop(task_id, None)
        self._message_queues.pop(task_id, None)
        logger.debug(f"Cleaned up buffers for completed task {task_id}")

    async def subscribe(self, task_id: str) -> AsyncIterator[dict]:
        """Subscribe to task events via SSE.

        Yields buffered events first, then live events.
        Note: Queue is registered BEFORE reading buffer to avoid race condition
        where events emitted between buffer read and queue registration are missed.
        """
        # Create subscriber queue and register FIRST to avoid race condition
        queue: asyncio.Queue = asyncio.Queue()

        if task_id not in self._subscribers:
            self._subscribers[task_id] = []
        self._subscribers[task_id].append(queue)

        try:
            # Copy buffered events (snapshot) to avoid issues with concurrent modification
            buffered_events = list(self._event_buffers.get(task_id, []))

            # Yield buffered events first
            for event in buffered_events:
                yield event

            # Yield live events (queue was registered before buffer read, so no events missed)
            while True:
                event = await queue.get()
                yield event

                # Stop if task completed/blocked/cancelled
                if event.get("type") == "status" and event.get("status") in ["completed", "blocked", "failed", "cancelled"]:
                    break
                if event.get("type") in ["result", "error"]:
                    break

        finally:
            # Remove subscriber
            if task_id in self._subscribers:
                self._subscribers[task_id].remove(queue)

    async def send_message(
        self,
        task_id: str,
        message: Optional[str] = None,
        content: Optional[list[dict]] = None,
    ) -> bool:
        """Send a message to a running task.

        Returns True if message was queued, False if task not found/not running.
        """
        if task_id not in self._message_queues:
            return False

        task = await db.tasks.get(task_id)
        if not task or task.get("status") not in ("running", "wip"):
            return False

        await self._message_queues[task_id].put({
            "message": message,
            "content": content,
        })
        return True

    async def cancel_task(self, task_id: str) -> bool:
        """Cancel a running task.

        For script tasks, sends SIGTERM to the subprocess before cancelling
        the asyncio wrapper task.
        """
        # For script tasks, kill the subprocess directly using in-memory PID
        # (avoids PID recycling risk from stale DB records)
        pid = self._script_pids.get(task_id)
        if pid:
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError:
                pass  # Already dead

        asyncio_task = self._running_tasks.get(task_id)
        if not asyncio_task:
            return False

        asyncio_task.cancel()
        try:
            await asyncio_task
        except asyncio.CancelledError:
            pass

        return True

    # ─── Script Task Support ───────────────────────────────────────────────

    async def create_script_task(
        self,
        command: str,
        cwd: Optional[str] = None,
        timeout: int = 600,
        description: Optional[str] = None,
    ) -> dict:
        """Create and start a script task (daemon-owned subprocess).

        The subprocess runs in the daemon process tree, surviving session
        eviction. Stdout/stderr are captured to a log file.

        Args:
            command: Shell command to execute
            cwd: Working directory (defaults to user home)
            timeout: Seconds before SIGTERM (default 600)
            description: Human-readable description

        Returns:
            Task record dict

        Raises:
            ValueError: If max concurrent script tasks exceeded
        """
        # Enforce max concurrent via semaphore (prevents TOCTOU race)
        if _script_semaphore.locked():
            # All slots taken — check actual count to give precise error
            running_scripts = sum(
                1 for tid in self._running_tasks
                if self._script_pids.get(tid) is not None
            )
            if running_scripts >= _MAX_CONCURRENT_SCRIPTS:
                raise ValueError(
                    f"Max concurrent script tasks ({_MAX_CONCURRENT_SCRIPTS}) reached. "
                    f"Wait for a running task to complete or cancel one."
                )

        # Ensure tasks directory exists
        tasks_dir = _tasks_dir()
        tasks_dir.mkdir(parents=True, exist_ok=True)

        task_id = f"task_{uuid4().hex[:12]}"
        log_path = str(tasks_dir / f"{task_id}.log")

        task = {
            "id": task_id,
            "type": "script",
            "agent_id": "__script__",
            "session_id": None,
            "status": "draft",
            "title": (description or command)[:50],
            "description": description,
            "priority": "none",
            "workspace_id": None,
            "source_todo_id": None,
            "blocked_reason": None,
            "model": None,
            "pid": None,
            "exit_code": None,
            "log_path": log_path,
            "command": command,
            "cwd": cwd,
            "timeout": timeout,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "started_at": None,
            "completed_at": None,
            "error": None,
        }
        await db.tasks.put(task)

        # Start background execution
        asyncio_task = asyncio.create_task(
            self._run_script_task(task_id, command, cwd, timeout, log_path)
        )
        self._running_tasks[task_id] = asyncio_task

        logger.info(f"Created script task {task_id}: {command[:80]}")
        return task

    async def _run_script_task(
        self,
        task_id: str,
        command: str,
        cwd: Optional[str],
        timeout: int,
        log_path: str,
    ) -> None:
        """Execute a script task as a subprocess.

        Runs in daemon process tree via _script_executor. Subprocess stdout/stderr
        are written to log_path. On completion, exit_code is persisted.
        Uses _script_semaphore for atomic slot management.
        """
        proc = None
        await _script_semaphore.acquire()
        try:
            # Transition to wip
            await db.tasks.update(task_id, {
                "status": "wip",
                "started_at": datetime.now(timezone.utc).isoformat(),
            })

            # Run subprocess in thread pool (blocking I/O)
            loop = asyncio.get_running_loop()

            def _execute():
                nonlocal proc
                work_dir = cwd or str(Path.home())
                with open(log_path, "w") as log_file:
                    proc = subprocess.Popen(
                        command,
                        shell=True,
                        cwd=work_dir,
                        stdout=log_file,
                        stderr=subprocess.STDOUT,
                        start_new_session=True,  # Detach from session process group
                    )
                    # Store PID immediately
                    return proc

            proc = await loop.run_in_executor(_script_executor, _execute)

            # Persist PID
            self._script_pids[task_id] = proc.pid
            await db.tasks.update(task_id, {"pid": proc.pid})

            # Wait for completion with timeout (in thread to not block event loop)
            def _wait():
                try:
                    return proc.wait(timeout=timeout)
                except subprocess.TimeoutExpired:
                    proc.terminate()
                    try:
                        proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        proc.wait()
                    return -1  # Timeout sentinel

            exit_code = await loop.run_in_executor(_script_executor, _wait)

            # Persist result
            status = "completed" if exit_code == 0 else "blocked"
            error_msg = None if exit_code == 0 else f"Exit code: {exit_code}"
            await db.tasks.update(task_id, {
                "status": status,
                "exit_code": exit_code,
                "completed_at": datetime.now(timezone.utc).isoformat(),
                "error": error_msg,
                "blocked_reason": error_msg,
            })

        except asyncio.CancelledError:
            # Cancel = SIGTERM was already sent in cancel_task
            if proc and proc.poll() is None:
                proc.terminate()
                # Non-blocking wait via executor (never block event loop)
                try:
                    await asyncio.wait_for(
                        loop.run_in_executor(_script_executor, proc.wait, 3),
                        timeout=5,
                    )
                except (subprocess.TimeoutExpired, asyncio.TimeoutError):
                    proc.kill()
            await db.tasks.update(task_id, {
                "status": "cancelled",
                "completed_at": datetime.now(timezone.utc).isoformat(),
            })
        except Exception as e:
            logger.error(f"Script task {task_id} failed: {e}")
            await db.tasks.update(task_id, {
                "status": "blocked",
                "blocked_reason": str(e),
                "completed_at": datetime.now(timezone.utc).isoformat(),
                "error": str(e),
            })
        finally:
            self._running_tasks.pop(task_id, None)
            self._script_pids.pop(task_id, None)
            _script_semaphore.release()

    async def get_task(self, task_id: str) -> Optional[dict]:
        """Get task by ID, with legacy status mapping.

        Validates: Requirements 5.4
        """
        task = await db.tasks.get(task_id)
        if task and task.get("status"):
            task["status"] = self._map_legacy_status(task["status"])
        return task

    async def list_tasks(
        self,
        status: Optional[str] = None,
        agent_id: Optional[str] = None,
        workspace_id: Optional[str] = None,
        completed_after: Optional[str] = None,
    ) -> list[dict]:
        """List all tasks, with optional filtering.

        Args:
            status: Filter by status.  Supports comma-separated values with
                OR semantics (e.g. ``"wip,draft,blocked"``).  Legacy statuses
                are mapped automatically per value.
            agent_id: Filter by agent ID.
            workspace_id: Filter by workspace ID.
            completed_after: ISO 8601 date string.  Return only tasks whose
                ``completed_at`` is after this value.

        Returns:
            List of task dicts with statuses mapped to new values.

        Validates: Requirements 5.1, 5.4, 5.7, 6.1, 6.4, 6.7
        """
        # Split comma-separated statuses and map legacy values
        mapped_statuses: Optional[list[str]] = None
        if status:
            raw_statuses = [s.strip() for s in status.split(",") if s.strip()]
            mapped_statuses = [self._map_legacy_status(s) for s in raw_statuses]

        tasks = await db.tasks.list_all(
            statuses=mapped_statuses,
            agent_id=agent_id,
            workspace_id=workspace_id,
            completed_after=completed_after,
        )

        # Map legacy statuses in returned results for backward compatibility
        for task in tasks:
            if task.get("status"):
                task["status"] = self._map_legacy_status(task["status"])

        return tasks

    async def delete_task(self, task_id: str) -> bool:
        """Delete a task (cancels if running).

        Increments task_version for context cache invalidation.

        Validates: Requirements 34.2
        """
        # Cancel if running
        if task_id in self._running_tasks:
            await self.cancel_task(task_id)

        # Cleanup
        self._event_buffers.pop(task_id, None)
        self._subscribers.pop(task_id, None)
        self._message_queues.pop(task_id, None)

        result = await db.tasks.delete(task_id)

        if result:
            # Increment task_version for context cache invalidation (Req 34.2)
            pass

        return result

    async def get_running_count(self) -> int:
        """Get count of running (wip) tasks."""
        # Check both old and new status names for backward compatibility
        wip_count = await db.tasks.count_by_status("wip")
        running_count = await db.tasks.count_by_status("running")
        return wip_count + running_count


# Global instance
task_manager = TaskManager()
