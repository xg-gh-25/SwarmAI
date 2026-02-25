"""Background task manager for persistent agent execution."""
import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional, AsyncIterator
from uuid import uuid4

from database import db
from .agent_manager import agent_manager

logger = logging.getLogger(__name__)


class TaskManager:
    """Manages background agent tasks that persist across frontend connections.

    Tasks run independently in asyncio tasks. Frontend can:
    - Create tasks (starts agent execution)
    - Subscribe to task events via SSE
    - Disconnect and reconnect without losing progress
    - Send messages to running tasks
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
        # Max events to buffer per task
        self._max_buffer_size = 100
        # Buffer retention time after task completion (seconds)
        self._buffer_retention_seconds = 300  # 5 minutes

    async def create_task(
        self,
        agent_id: str,
        message: Optional[str] = None,
        content: Optional[list[dict]] = None,
        enable_skills: bool = False,
        enable_mcp: bool = False,
        add_dirs: Optional[list[str]] = None,
    ) -> dict:
        """Create and start a new background task.

        Args:
            agent_id: The agent to run
            message: Simple text message
            content: Multimodal content array
            enable_skills: Whether to enable skills
            enable_mcp: Whether to enable MCP servers
            add_dirs: Additional directories for Claude to access

        Returns:
            Task record dict
        """
        # Get agent config for model and title
        agent_config = await db.agents.get(agent_id)
        if not agent_config:
            raise ValueError(f"Agent {agent_id} not found")

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

        # Create task record
        task_id = f"task_{uuid4().hex[:12]}"
        task = {
            "id": task_id,
            "agent_id": agent_id,
            "session_id": None,  # Will be set when agent starts
            "status": "pending",
            "title": title,
            "model": agent_config.get("model"),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "started_at": None,
            "completed_at": None,
            "error": None,
            "work_dir": add_dirs[0] if add_dirs else None,
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
                add_dirs=add_dirs,
            )
        )
        self._running_tasks[task_id] = asyncio_task

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
        add_dirs: Optional[list[str]],
    ) -> None:
        """Background task execution."""
        try:
            # Update status to running
            await db.tasks.update(task_id, {
                "status": "running",
                "started_at": datetime.now(timezone.utc).isoformat(),
            })
            await self._emit_event(task_id, {"type": "status", "status": "running"})

            # Run agent conversation
            session_id = None
            async for event in agent_manager.run_conversation(
                agent_id=agent_id,
                user_message=message,
                content=content,
                session_id=None,  # New conversation
                enable_skills=enable_skills,
                enable_mcp=enable_mcp,
                add_dirs=add_dirs,
            ):
                # Capture session_id from session_start event
                if event.get("type") == "session_start":
                    session_id = event.get("sessionId")
                    await db.tasks.update(task_id, {"session_id": session_id})

                # Emit event to subscribers
                await self._emit_event(task_id, event)

                # Check for errors
                if event.get("type") == "error":
                    await db.tasks.update(task_id, {
                        "status": "failed",
                        "completed_at": datetime.now(timezone.utc).isoformat(),
                        "error": event.get("error"),
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
            await db.tasks.update(task_id, {
                "status": "failed",
                "completed_at": datetime.now(timezone.utc).isoformat(),
                "error": str(e),
            })
            await self._emit_event(task_id, {"type": "error", "error": str(e)})
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

                async for event in agent_manager.run_conversation(
                    agent_id=task["agent_id"],
                    user_message=msg_data.get("message"),
                    content=msg_data.get("content"),
                    session_id=session_id,
                    enable_skills=enable_skills,
                    enable_mcp=enable_mcp,
                    add_dirs=[task["work_dir"]] if task.get("work_dir") else None,
                ):
                    await self._emit_event(task_id, event)

                    if event.get("type") == "error":
                        await db.tasks.update(task_id, {
                            "status": "failed",
                            "completed_at": datetime.now(timezone.utc).isoformat(),
                            "error": event.get("error"),
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

                # Stop if task completed/failed/cancelled
                if event.get("type") == "status" and event.get("status") in ["completed", "failed", "cancelled"]:
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
        if not task or task.get("status") != "running":
            return False

        await self._message_queues[task_id].put({
            "message": message,
            "content": content,
        })
        return True

    async def cancel_task(self, task_id: str) -> bool:
        """Cancel a running task."""
        asyncio_task = self._running_tasks.get(task_id)
        if not asyncio_task:
            return False

        asyncio_task.cancel()
        try:
            await asyncio_task
        except asyncio.CancelledError:
            pass

        return True

    async def get_task(self, task_id: str) -> Optional[dict]:
        """Get task by ID."""
        return await db.tasks.get(task_id)

    async def list_tasks(
        self,
        status: Optional[str] = None,
        agent_id: Optional[str] = None,
    ) -> list[dict]:
        """List all tasks."""
        return await db.tasks.list_all(status=status, agent_id=agent_id)

    async def delete_task(self, task_id: str) -> bool:
        """Delete a task (cancels if running)."""
        # Cancel if running
        if task_id in self._running_tasks:
            await self.cancel_task(task_id)

        # Cleanup
        self._event_buffers.pop(task_id, None)
        self._subscribers.pop(task_id, None)
        self._message_queues.pop(task_id, None)

        return await db.tasks.delete(task_id)

    async def get_running_count(self) -> int:
        """Get count of running tasks."""
        return await db.tasks.count_by_status("running")


# Global instance
task_manager = TaskManager()
