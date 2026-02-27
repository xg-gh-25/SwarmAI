# Task Management Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Implement persistent background agent tasks that survive page navigation, with a Tasks page for monitoring and sidebar badge for running count.

**Architecture:** Backend TaskManager spawns asyncio tasks that run independently of SSE connections. Frontend can disconnect/reconnect to task streams. Tasks persist in SQLite database.

**Tech Stack:** Python FastAPI (backend), React + TypeScript (frontend), SQLite (database), SSE (streaming)

---

## Task 1: Backend Task Schema

**Files:**
- Create: `backend/schemas/task.py`

**Step 1: Create the task schema file**

```python
"""Task schemas for background agent task management."""
from datetime import datetime
from enum import Enum
from typing import Optional
from pydantic import BaseModel


class TaskStatus(str, Enum):
    """Task execution status."""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TaskCreate(BaseModel):
    """Request to create a new task."""
    agent_id: str
    message: Optional[str] = None
    content: Optional[list[dict]] = None
    enable_skills: bool = False
    enable_mcp: bool = False
    add_dirs: Optional[list[str]] = None


class TaskResponse(BaseModel):
    """Task response model."""
    id: str
    agent_id: str
    session_id: Optional[str] = None
    status: TaskStatus
    title: str
    model: Optional[str] = None
    created_at: datetime
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    error: Optional[str] = None
    work_dir: Optional[str] = None


class TaskMessageRequest(BaseModel):
    """Request to send a message to a running task."""
    message: Optional[str] = None
    content: Optional[list[dict]] = None


class RunningTaskCount(BaseModel):
    """Response for running task count."""
    count: int
```

**Step 2: Verify the schema imports correctly**

Run: `cd /home/ubuntu/workspace/owork/.worktrees/task-management/backend && source .venv/bin/activate && python -c "from schemas.task import TaskStatus, TaskCreate, TaskResponse; print('OK')"`

Expected: `OK`

**Step 3: Commit**

```bash
git add backend/schemas/task.py
git commit -m "feat(backend): add task schema definitions"
```

---

## Task 2: Database Tasks Table

**Files:**
- Modify: `backend/database/sqlite.py`

**Step 1: Add tasks table to SQLite database**

In `backend/database/sqlite.py`, add after the `messages` table creation (around line 90):

```python
# Tasks table
await conn.execute("""
    CREATE TABLE IF NOT EXISTS tasks (
        id TEXT PRIMARY KEY,
        agent_id TEXT NOT NULL,
        session_id TEXT,
        status TEXT NOT NULL DEFAULT 'pending',
        title TEXT NOT NULL,
        model TEXT,
        created_at TEXT NOT NULL,
        started_at TEXT,
        completed_at TEXT,
        error TEXT,
        work_dir TEXT
    )
""")
await conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status)")
await conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_agent_id ON tasks(agent_id)")
```

**Step 2: Add TasksTable class**

Add after the `PermissionRequestsTable` class:

```python
class TasksTable:
    """Tasks table operations."""

    def __init__(self, db_path: str):
        self.db_path = db_path

    async def put(self, task: dict) -> None:
        """Insert or update a task."""
        async with aiosqlite.connect(self.db_path) as conn:
            await conn.execute(
                """
                INSERT OR REPLACE INTO tasks
                (id, agent_id, session_id, status, title, model, created_at, started_at, completed_at, error, work_dir)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task["id"],
                    task["agent_id"],
                    task.get("session_id"),
                    task.get("status", "pending"),
                    task["title"],
                    task.get("model"),
                    task["created_at"],
                    task.get("started_at"),
                    task.get("completed_at"),
                    task.get("error"),
                    task.get("work_dir"),
                ),
            )
            await conn.commit()

    async def get(self, task_id: str) -> Optional[dict]:
        """Get a task by ID."""
        async with aiosqlite.connect(self.db_path) as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute(
                "SELECT * FROM tasks WHERE id = ?", (task_id,)
            ) as cursor:
                row = await cursor.fetchone()
                return dict(row) if row else None

    async def list_all(self, status: Optional[str] = None, agent_id: Optional[str] = None) -> list[dict]:
        """List all tasks, optionally filtered by status or agent_id."""
        async with aiosqlite.connect(self.db_path) as conn:
            conn.row_factory = aiosqlite.Row
            query = "SELECT * FROM tasks WHERE 1=1"
            params = []
            if status:
                query += " AND status = ?"
                params.append(status)
            if agent_id:
                query += " AND agent_id = ?"
                params.append(agent_id)
            query += " ORDER BY created_at DESC"
            async with conn.execute(query, params) as cursor:
                rows = await cursor.fetchall()
                return [dict(row) for row in rows]

    async def update(self, task_id: str, updates: dict) -> bool:
        """Update a task."""
        if not updates:
            return False
        set_clause = ", ".join(f"{k} = ?" for k in updates.keys())
        values = list(updates.values()) + [task_id]
        async with aiosqlite.connect(self.db_path) as conn:
            cursor = await conn.execute(
                f"UPDATE tasks SET {set_clause} WHERE id = ?", values
            )
            await conn.commit()
            return cursor.rowcount > 0

    async def delete(self, task_id: str) -> bool:
        """Delete a task."""
        async with aiosqlite.connect(self.db_path) as conn:
            cursor = await conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
            await conn.commit()
            return cursor.rowcount > 0

    async def count_by_status(self, status: str) -> int:
        """Count tasks by status."""
        async with aiosqlite.connect(self.db_path) as conn:
            async with conn.execute(
                "SELECT COUNT(*) FROM tasks WHERE status = ?", (status,)
            ) as cursor:
                row = await cursor.fetchone()
                return row[0] if row else 0
```

**Step 3: Register tasks table in SQLiteDatabase class**

In the `SQLiteDatabase.__init__` method, add:

```python
self.tasks = TasksTable(db_path)
```

**Step 4: Verify database changes**

Run: `cd /home/ubuntu/workspace/owork/.worktrees/task-management/backend && source .venv/bin/activate && python -c "from database.sqlite import SQLiteDatabase; db = SQLiteDatabase(':memory:'); import asyncio; asyncio.run(db.init()); print('OK')"`

Expected: `OK`

**Step 5: Commit**

```bash
git add backend/database/sqlite.py
git commit -m "feat(backend): add tasks table to SQLite database"
```

---

## Task 3: TaskManager Core

**Files:**
- Create: `backend/core/task_manager.py`

**Step 1: Create the TaskManager class**

```python
"""Background task manager for persistent agent execution."""
import asyncio
import logging
from datetime import datetime
from typing import Optional, AsyncIterator, Any
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

        if len(title) < len(message or "") if message else False:
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
            "created_at": datetime.now().isoformat(),
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
                "started_at": datetime.now().isoformat(),
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
                        "completed_at": datetime.now().isoformat(),
                        "error": event.get("error"),
                    })
                    return

                # Check for completion
                if event.get("type") == "result":
                    await db.tasks.update(task_id, {
                        "status": "completed",
                        "completed_at": datetime.now().isoformat(),
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
                "completed_at": datetime.now().isoformat(),
            })
            await self._emit_event(task_id, {"type": "status", "status": "cancelled"})
        except Exception as e:
            logger.error(f"Task {task_id} failed: {e}")
            await db.tasks.update(task_id, {
                "status": "failed",
                "completed_at": datetime.now().isoformat(),
                "error": str(e),
            })
            await self._emit_event(task_id, {"type": "error", "error": str(e)})
        finally:
            # Cleanup
            self._running_tasks.pop(task_id, None)

    async def _handle_pending_interaction(
        self,
        task_id: str,
        session_id: str,
        enable_skills: bool,
        enable_mcp: bool,
    ) -> None:
        """Handle task paused for user interaction (ask_user_question)."""
        # Wait for message from user
        message_queue = self._message_queues.get(task_id)
        if not message_queue:
            return

        try:
            # Wait indefinitely for user message
            msg_data = await message_queue.get()

            # Continue conversation with user's response
            task = await db.tasks.get(task_id)
            if not task:
                return

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
                        "completed_at": datetime.now().isoformat(),
                        "error": event.get("error"),
                    })
                    return

                if event.get("type") == "result":
                    await db.tasks.update(task_id, {
                        "status": "completed",
                        "completed_at": datetime.now().isoformat(),
                    })
                    return

                if event.get("type") == "ask_user_question":
                    # Recursive wait for next interaction
                    await self._handle_pending_interaction(task_id, session_id, enable_skills, enable_mcp)
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

    async def subscribe(self, task_id: str) -> AsyncIterator[dict]:
        """Subscribe to task events via SSE.

        Yields buffered events first, then live events.
        """
        # Create subscriber queue
        queue: asyncio.Queue = asyncio.Queue()

        if task_id not in self._subscribers:
            self._subscribers[task_id] = []
        self._subscribers[task_id].append(queue)

        try:
            # Yield buffered events first
            for event in self._event_buffers.get(task_id, []):
                yield event

            # Yield live events
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
```

**Step 2: Verify the TaskManager imports correctly**

Run: `cd /home/ubuntu/workspace/owork/.worktrees/task-management/backend && source .venv/bin/activate && python -c "from core.task_manager import task_manager; print('OK')"`

Expected: `OK`

**Step 3: Commit**

```bash
git add backend/core/task_manager.py
git commit -m "feat(backend): add TaskManager for background task execution"
```

---

## Task 4: Tasks API Router

**Files:**
- Create: `backend/routers/tasks.py`

**Step 1: Create the tasks router**

```python
"""Tasks API endpoints for background agent task management."""
import asyncio
import json
import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse

from schemas.task import TaskCreate, TaskResponse, TaskMessageRequest, RunningTaskCount
from core.task_manager import task_manager
from database import db

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("", response_model=list[TaskResponse])
async def list_tasks(
    status: Optional[str] = Query(None, description="Filter by status"),
    agent_id: Optional[str] = Query(None, description="Filter by agent ID"),
):
    """List all tasks, optionally filtered by status or agent_id."""
    tasks = await task_manager.list_tasks(status=status, agent_id=agent_id)
    return [TaskResponse(**task) for task in tasks]


@router.get("/running/count", response_model=RunningTaskCount)
async def get_running_count():
    """Get count of running tasks (for sidebar badge)."""
    count = await task_manager.get_running_count()
    return RunningTaskCount(count=count)


@router.get("/{task_id}", response_model=TaskResponse)
async def get_task(task_id: str):
    """Get a specific task by ID."""
    task = await task_manager.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")
    return TaskResponse(**task)


@router.post("", response_model=TaskResponse)
async def create_task(request: TaskCreate):
    """Create and start a new background task."""
    try:
        task = await task_manager.create_task(
            agent_id=request.agent_id,
            message=request.message,
            content=request.content,
            enable_skills=request.enable_skills,
            enable_mcp=request.enable_mcp,
            add_dirs=request.add_dirs,
        )
        return TaskResponse(**task)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/{task_id}")
async def delete_task(task_id: str):
    """Delete a task (cancels if running)."""
    deleted = await task_manager.delete_task(task_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")
    return {"status": "deleted", "task_id": task_id}


@router.post("/{task_id}/cancel")
async def cancel_task(task_id: str):
    """Cancel a running task."""
    cancelled = await task_manager.cancel_task(task_id)
    if not cancelled:
        raise HTTPException(status_code=400, detail=f"Task {task_id} is not running")
    return {"status": "cancelled", "task_id": task_id}


@router.post("/{task_id}/message")
async def send_message(task_id: str, request: TaskMessageRequest):
    """Send a message to a running task."""
    success = await task_manager.send_message(
        task_id=task_id,
        message=request.message,
        content=request.content,
    )
    if not success:
        raise HTTPException(status_code=400, detail=f"Task {task_id} is not running or not accepting messages")
    return {"status": "sent", "task_id": task_id}


@router.get("/{task_id}/stream")
async def stream_task(task_id: str):
    """Subscribe to task events via SSE."""
    task = await task_manager.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")

    async def event_generator():
        try:
            async for event in task_manager.subscribe(task_id):
                yield f"data: {json.dumps(event)}\n\n"
        except asyncio.CancelledError:
            logger.info(f"SSE stream cancelled for task {task_id}")

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
```

**Step 2: Register router in main.py**

In `backend/main.py`, add after other router imports:

```python
from routers import tasks
```

And in the router registration section, add:

```python
app.include_router(tasks.router, prefix="/api/tasks", tags=["tasks"])
```

**Step 3: Verify the router imports correctly**

Run: `cd /home/ubuntu/workspace/owork/.worktrees/task-management/backend && source .venv/bin/activate && python -c "from routers.tasks import router; print('OK')"`

Expected: `OK`

**Step 4: Commit**

```bash
git add backend/routers/tasks.py backend/main.py
git commit -m "feat(backend): add tasks API router"
```

---

## Task 5: Frontend Task Types

**Files:**
- Modify: `desktop/src/types/index.ts`

**Step 1: Add task-related types**

Add to `desktop/src/types/index.ts`:

```typescript
// Task types
export type TaskStatus = 'pending' | 'running' | 'completed' | 'failed' | 'cancelled';

export interface Task {
  id: string;
  agentId: string;
  sessionId: string | null;
  status: TaskStatus;
  title: string;
  model: string | null;
  createdAt: string;
  startedAt: string | null;
  completedAt: string | null;
  error: string | null;
  workDir: string | null;
}

export interface TaskCreateRequest {
  agentId: string;
  message?: string;
  content?: ContentBlock[];
  enableSkills?: boolean;
  enableMcp?: boolean;
  addDirs?: string[];
}

export interface TaskMessageRequest {
  message?: string;
  content?: ContentBlock[];
}

export interface RunningTaskCount {
  count: number;
}
```

**Step 2: Commit**

```bash
git add desktop/src/types/index.ts
git commit -m "feat(frontend): add task TypeScript types"
```

---

## Task 6: Frontend Tasks Service

**Files:**
- Create: `desktop/src/services/tasks.ts`

**Step 1: Create the tasks service**

```typescript
/**
 * Tasks service for background agent task management.
 */
import api from './api';
import type { Task, TaskCreateRequest, TaskMessageRequest, RunningTaskCount, TaskStatus } from '../types';

// Convert snake_case to camelCase
function toCamelCase(task: Record<string, unknown>): Task {
  return {
    id: task.id as string,
    agentId: task.agent_id as string,
    sessionId: task.session_id as string | null,
    status: task.status as TaskStatus,
    title: task.title as string,
    model: task.model as string | null,
    createdAt: task.created_at as string,
    startedAt: task.started_at as string | null,
    completedAt: task.completed_at as string | null,
    error: task.error as string | null,
    workDir: task.work_dir as string | null,
  };
}

// Convert camelCase to snake_case for requests
function toSnakeCase(request: TaskCreateRequest): Record<string, unknown> {
  return {
    agent_id: request.agentId,
    message: request.message,
    content: request.content,
    enable_skills: request.enableSkills,
    enable_mcp: request.enableMcp,
    add_dirs: request.addDirs,
  };
}

export const tasksService = {
  /**
   * List all tasks, optionally filtered by status or agent ID.
   */
  async list(status?: TaskStatus, agentId?: string): Promise<Task[]> {
    const params = new URLSearchParams();
    if (status) params.append('status', status);
    if (agentId) params.append('agent_id', agentId);

    const queryString = params.toString();
    const url = queryString ? `/api/tasks?${queryString}` : '/api/tasks';

    const response = await api.get(url);
    return response.data.map(toCamelCase);
  },

  /**
   * Get a specific task by ID.
   */
  async get(taskId: string): Promise<Task> {
    const response = await api.get(`/api/tasks/${taskId}`);
    return toCamelCase(response.data);
  },

  /**
   * Create and start a new background task.
   */
  async create(request: TaskCreateRequest): Promise<Task> {
    const response = await api.post('/api/tasks', toSnakeCase(request));
    return toCamelCase(response.data);
  },

  /**
   * Delete a task (cancels if running).
   */
  async delete(taskId: string): Promise<void> {
    await api.delete(`/api/tasks/${taskId}`);
  },

  /**
   * Cancel a running task.
   */
  async cancel(taskId: string): Promise<void> {
    await api.post(`/api/tasks/${taskId}/cancel`);
  },

  /**
   * Send a message to a running task.
   */
  async sendMessage(taskId: string, request: TaskMessageRequest): Promise<void> {
    await api.post(`/api/tasks/${taskId}/message`, {
      message: request.message,
      content: request.content,
    });
  },

  /**
   * Get count of running tasks (for sidebar badge).
   */
  async getRunningCount(): Promise<number> {
    const response = await api.get('/api/tasks/running/count');
    return response.data.count;
  },

  /**
   * Get SSE stream URL for a task.
   */
  getStreamUrl(taskId: string): string {
    // Use api baseURL to construct full URL
    const baseUrl = api.defaults.baseURL || '';
    return `${baseUrl}/api/tasks/${taskId}/stream`;
  },
};
```

**Step 2: Commit**

```bash
git add desktop/src/services/tasks.ts
git commit -m "feat(frontend): add tasks API service"
```

---

## Task 7: Running Task Count Hook

**Files:**
- Create: `desktop/src/hooks/useRunningTaskCount.ts`

**Step 1: Create the hook**

```typescript
/**
 * Hook for polling running task count (for sidebar badge).
 */
import { useQuery } from '@tanstack/react-query';
import { tasksService } from '../services/tasks';

const POLL_INTERVAL = 5000; // 5 seconds

export function useRunningTaskCount() {
  const { data: count = 0, isLoading, error } = useQuery({
    queryKey: ['runningTaskCount'],
    queryFn: () => tasksService.getRunningCount(),
    refetchInterval: POLL_INTERVAL,
    staleTime: POLL_INTERVAL - 1000, // Slightly less than poll interval
  });

  return { count, isLoading, error };
}
```

**Step 2: Commit**

```bash
git add desktop/src/hooks/useRunningTaskCount.ts
git commit -m "feat(frontend): add useRunningTaskCount hook for sidebar badge"
```

---

## Task 8: Sidebar Badge Integration

**Files:**
- Modify: `desktop/src/components/common/Sidebar.tsx`

**Step 1: Import the hook and add badge component**

At the top of `Sidebar.tsx`, add:

```typescript
import { useRunningTaskCount } from '../../hooks/useRunningTaskCount';
```

**Step 2: Add Tasks nav item to navItems array**

Update the `navItems` array to include Tasks:

```typescript
const navItems: NavItem[] = [
  { path: '/chat', labelKey: 'nav.chat', icon: 'chat' },
  { path: '/tasks', labelKey: 'nav.tasks', icon: 'task_alt' },  // NEW
  { path: '/agents', labelKey: 'nav.agents', icon: 'smart_toy' },
  { path: '/skills', labelKey: 'nav.skills', icon: 'construction' },
  { path: '/plugins', labelKey: 'nav.plugins', icon: 'extension' },
  { path: '/mcp', labelKey: 'nav.mcp', icon: 'dns' },
];
```

**Step 3: Add badge rendering in collapsed mode**

In the collapsed mode navigation section, update the NavLink to show badge for tasks:

```typescript
// Inside the collapsed mode nav section
{navItems.map((item) => {
  const { count } = item.path === '/tasks' ? useRunningTaskCount() : { count: 0 };
  return (
    <div key={item.path} className="relative">
      <NavLink
        to={item.path}
        title={t(item.labelKey)}
        className={clsx(
          'flex items-center justify-center w-12 h-12 rounded-xl transition-colors',
          isActive(item.path)
            ? 'bg-primary/20 text-primary'
            : 'text-[var(--color-text-muted)] hover:bg-[var(--color-hover)] hover:text-[var(--color-text)]'
        )}
      >
        <span className="material-symbols-outlined text-2xl">{item.icon}</span>
      </NavLink>
      {item.path === '/tasks' && count > 0 && (
        <span className="absolute -top-1 -right-1 w-5 h-5 bg-primary text-white text-xs font-bold rounded-full flex items-center justify-center">
          {count > 9 ? '9+' : count}
        </span>
      )}
    </div>
  );
})}
```

**Step 4: Add badge rendering in expanded mode**

In the expanded mode navigation section:

```typescript
// Inside the expanded mode nav section
{navItems.map((item) => {
  const { count } = item.path === '/tasks' ? useRunningTaskCount() : { count: 0 };
  return (
    <NavLink
      key={item.path}
      to={item.path}
      onClick={handleNavClick}
      className={clsx(
        'flex items-center gap-3 px-3 py-2.5 rounded-lg transition-colors',
        isActive(item.path)
          ? 'bg-primary text-white'
          : 'text-[var(--color-text-muted)] hover:bg-[var(--color-hover)] hover:text-[var(--color-text)]'
      )}
    >
      <span className="material-symbols-outlined text-xl">{item.icon}</span>
      <span className="text-sm font-medium flex-1">{t(item.labelKey)}</span>
      {item.path === '/tasks' && count > 0 && (
        <span className="w-5 h-5 bg-primary/20 text-primary text-xs font-bold rounded-full flex items-center justify-center">
          {count}
        </span>
      )}
    </NavLink>
  );
})}
```

**Step 5: Commit**

```bash
git add desktop/src/components/common/Sidebar.tsx
git commit -m "feat(frontend): add Tasks nav item with running count badge"
```

---

## Task 9: i18n Strings

**Files:**
- Modify: `desktop/src/i18n/locales/en.json`
- Modify: `desktop/src/i18n/locales/zh.json`

**Step 1: Add English translations**

Add to `en.json`:

```json
"nav": {
  "tasks": "Tasks",
  // ... existing nav items
},
"tasks": {
  "title": "Task Management",
  "subtitle": "Monitor and manage your running agent tasks.",
  "search": "Search tasks...",
  "filter": {
    "all": "All",
    "running": "Running",
    "completed": "Completed",
    "failed": "Failed"
  },
  "columns": {
    "name": "Task Name",
    "agent": "Agent",
    "status": "Status",
    "model": "Model",
    "started": "Started",
    "duration": "Duration",
    "actions": "Actions"
  },
  "status": {
    "pending": "Pending",
    "running": "Running",
    "completed": "Completed",
    "failed": "Failed",
    "cancelled": "Cancelled"
  },
  "actions": {
    "viewChat": "View Chat",
    "cancel": "Cancel",
    "delete": "Delete"
  },
  "empty": "No tasks yet. Start a chat to create your first task.",
  "newTask": "New Task",
  "confirmCancel": "Are you sure you want to cancel this task?",
  "confirmDelete": "Are you sure you want to delete this task?"
}
```

**Step 2: Add Chinese translations**

Add to `zh.json`:

```json
"nav": {
  "tasks": "任务",
  // ... existing nav items
},
"tasks": {
  "title": "任务管理",
  "subtitle": "监控和管理正在运行的代理任务。",
  "search": "搜索任务...",
  "filter": {
    "all": "全部",
    "running": "运行中",
    "completed": "已完成",
    "failed": "失败"
  },
  "columns": {
    "name": "任务名称",
    "agent": "代理",
    "status": "状态",
    "model": "模型",
    "started": "开始时间",
    "duration": "持续时间",
    "actions": "操作"
  },
  "status": {
    "pending": "等待中",
    "running": "运行中",
    "completed": "已完成",
    "failed": "失败",
    "cancelled": "已取消"
  },
  "actions": {
    "viewChat": "查看对话",
    "cancel": "取消",
    "delete": "删除"
  },
  "empty": "暂无任务。开始对话以创建您的第一个任务。",
  "newTask": "新建任务",
  "confirmCancel": "确定要取消此任务吗？",
  "confirmDelete": "确定要删除此任务吗？"
}
```

**Step 3: Commit**

```bash
git add desktop/src/i18n/locales/en.json desktop/src/i18n/locales/zh.json
git commit -m "feat(i18n): add task management translations"
```

---

## Task 10: TasksPage Component

**Files:**
- Create: `desktop/src/pages/TasksPage.tsx`

**Step 1: Create the TasksPage component**

```typescript
import { useState, useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import clsx from 'clsx';
import { tasksService } from '../services/tasks';
import { agentsService } from '../services/agents';
import { ConfirmDialog } from '../components/common';
import type { Task, TaskStatus, Agent } from '../types';

// Format relative time
function formatRelativeTime(dateString: string | null): string {
  if (!dateString) return '-';
  const date = new Date(dateString);
  const now = new Date();
  const diffMs = now.getTime() - date.getTime();
  const diffMins = Math.floor(diffMs / 60000);
  const diffHours = Math.floor(diffMs / 3600000);
  const diffDays = Math.floor(diffMs / 86400000);

  if (diffMins < 1) return 'Just now';
  if (diffMins < 60) return `${diffMins}m ago`;
  if (diffHours < 24) return `${diffHours}h ago`;
  return `${diffDays}d ago`;
}

// Format duration
function formatDuration(startedAt: string | null, completedAt: string | null): string {
  if (!startedAt) return '-';
  const start = new Date(startedAt);
  const end = completedAt ? new Date(completedAt) : new Date();
  const diffMs = end.getTime() - start.getTime();
  const diffSecs = Math.floor(diffMs / 1000);
  const mins = Math.floor(diffSecs / 60);
  const secs = diffSecs % 60;
  return `${mins}:${secs.toString().padStart(2, '0')}`;
}

// Status badge component
function StatusBadge({ status }: { status: TaskStatus }) {
  const { t } = useTranslation();

  const config = {
    pending: { color: 'bg-gray-500/20 text-gray-400', icon: 'schedule' },
    running: { color: 'bg-blue-500/20 text-blue-400', icon: 'sync', spin: true },
    completed: { color: 'bg-green-500/20 text-green-400', icon: 'check_circle' },
    failed: { color: 'bg-red-500/20 text-red-400', icon: 'error' },
    cancelled: { color: 'bg-gray-500/20 text-gray-400', icon: 'cancel' },
  };

  const { color, icon, spin } = config[status] || config.pending;

  return (
    <span className={clsx('inline-flex items-center gap-1 px-2 py-1 rounded-full text-xs font-medium', color)}>
      <span className={clsx('material-symbols-outlined text-sm', spin && 'animate-spin')}>{icon}</span>
      {t(`tasks.status.${status}`)}
    </span>
  );
}

export default function TasksPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  const [searchQuery, setSearchQuery] = useState('');
  const [statusFilter, setStatusFilter] = useState<TaskStatus | 'all'>('all');
  const [taskToCancel, setTaskToCancel] = useState<Task | null>(null);
  const [taskToDelete, setTaskToDelete] = useState<Task | null>(null);

  // Fetch tasks
  const { data: tasks = [], isLoading: tasksLoading } = useQuery({
    queryKey: ['tasks', statusFilter === 'all' ? undefined : statusFilter],
    queryFn: () => tasksService.list(statusFilter === 'all' ? undefined : statusFilter),
    refetchInterval: 5000, // Poll every 5 seconds for status updates
  });

  // Fetch agents for mapping agent names
  const { data: agents = [] } = useQuery({
    queryKey: ['agents'],
    queryFn: () => agentsService.list(),
  });

  const agentMap = useMemo(() => {
    return agents.reduce((acc, agent) => {
      acc[agent.id] = agent;
      return acc;
    }, {} as Record<string, Agent>);
  }, [agents]);

  // Filter tasks by search query
  const filteredTasks = useMemo(() => {
    if (!searchQuery) return tasks;
    const query = searchQuery.toLowerCase();
    return tasks.filter(task =>
      task.title.toLowerCase().includes(query) ||
      agentMap[task.agentId]?.name.toLowerCase().includes(query)
    );
  }, [tasks, searchQuery, agentMap]);

  // Handle actions
  const handleViewChat = (task: Task) => {
    navigate(`/chat?taskId=${task.id}`);
  };

  const handleCancel = async () => {
    if (!taskToCancel) return;
    try {
      await tasksService.cancel(taskToCancel.id);
      queryClient.invalidateQueries({ queryKey: ['tasks'] });
      queryClient.invalidateQueries({ queryKey: ['runningTaskCount'] });
    } finally {
      setTaskToCancel(null);
    }
  };

  const handleDelete = async () => {
    if (!taskToDelete) return;
    try {
      await tasksService.delete(taskToDelete.id);
      queryClient.invalidateQueries({ queryKey: ['tasks'] });
      queryClient.invalidateQueries({ queryKey: ['runningTaskCount'] });
    } finally {
      setTaskToDelete(null);
    }
  };

  return (
    <div className="flex-1 p-6 overflow-auto">
      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold text-[var(--color-text)]">{t('tasks.title')}</h1>
          <p className="text-[var(--color-text-muted)] mt-1">{t('tasks.subtitle')}</p>
        </div>
        <button
          onClick={() => navigate('/chat')}
          className="flex items-center gap-2 px-4 py-2 bg-primary text-white rounded-lg hover:bg-primary/90 transition-colors"
        >
          <span className="material-symbols-outlined text-xl">add</span>
          {t('tasks.newTask')}
        </button>
      </div>

      {/* Search and Filter */}
      <div className="flex items-center gap-4 mb-6">
        <div className="relative flex-1 max-w-md">
          <span className="material-symbols-outlined absolute left-3 top-1/2 -translate-y-1/2 text-[var(--color-text-muted)]">
            search
          </span>
          <input
            type="text"
            placeholder={t('tasks.search')}
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="w-full pl-10 pr-4 py-2 bg-[var(--color-input-bg)] border border-[var(--color-border)] rounded-lg text-[var(--color-text)] placeholder-[var(--color-text-muted)] focus:outline-none focus:ring-2 focus:ring-primary/50"
          />
        </div>
        <select
          value={statusFilter}
          onChange={(e) => setStatusFilter(e.target.value as TaskStatus | 'all')}
          className="px-4 py-2 bg-[var(--color-input-bg)] border border-[var(--color-border)] rounded-lg text-[var(--color-text)] focus:outline-none focus:ring-2 focus:ring-primary/50"
        >
          <option value="all">{t('tasks.filter.all')}</option>
          <option value="running">{t('tasks.filter.running')}</option>
          <option value="completed">{t('tasks.filter.completed')}</option>
          <option value="failed">{t('tasks.filter.failed')}</option>
        </select>
      </div>

      {/* Table */}
      <div className="bg-[var(--color-card)] rounded-xl border border-[var(--color-border)] overflow-hidden">
        <table className="w-full">
          <thead>
            <tr className="border-b border-[var(--color-border)]">
              <th className="px-4 py-3 text-left text-xs font-semibold text-[var(--color-text-muted)] uppercase tracking-wider">
                {t('tasks.columns.name')}
              </th>
              <th className="px-4 py-3 text-left text-xs font-semibold text-[var(--color-text-muted)] uppercase tracking-wider">
                {t('tasks.columns.agent')}
              </th>
              <th className="px-4 py-3 text-left text-xs font-semibold text-[var(--color-text-muted)] uppercase tracking-wider">
                {t('tasks.columns.status')}
              </th>
              <th className="px-4 py-3 text-left text-xs font-semibold text-[var(--color-text-muted)] uppercase tracking-wider">
                {t('tasks.columns.model')}
              </th>
              <th className="px-4 py-3 text-left text-xs font-semibold text-[var(--color-text-muted)] uppercase tracking-wider">
                {t('tasks.columns.started')}
              </th>
              <th className="px-4 py-3 text-left text-xs font-semibold text-[var(--color-text-muted)] uppercase tracking-wider">
                {t('tasks.columns.duration')}
              </th>
              <th className="px-4 py-3 text-right text-xs font-semibold text-[var(--color-text-muted)] uppercase tracking-wider">
                {t('tasks.columns.actions')}
              </th>
            </tr>
          </thead>
          <tbody>
            {tasksLoading ? (
              <tr>
                <td colSpan={7} className="px-4 py-8 text-center">
                  <span className="material-symbols-outlined animate-spin text-2xl text-[var(--color-text-muted)]">
                    sync
                  </span>
                </td>
              </tr>
            ) : filteredTasks.length === 0 ? (
              <tr>
                <td colSpan={7} className="px-4 py-8 text-center text-[var(--color-text-muted)]">
                  {t('tasks.empty')}
                </td>
              </tr>
            ) : (
              filteredTasks.map((task) => (
                <tr
                  key={task.id}
                  className="border-b border-[var(--color-border)] last:border-b-0 hover:bg-[var(--color-hover)] transition-colors"
                >
                  <td className="px-4 py-3">
                    <span className="text-[var(--color-text)] font-medium truncate block max-w-xs">
                      {task.title}
                    </span>
                  </td>
                  <td className="px-4 py-3 text-[var(--color-text-muted)]">
                    {agentMap[task.agentId]?.name || task.agentId}
                  </td>
                  <td className="px-4 py-3">
                    <StatusBadge status={task.status} />
                  </td>
                  <td className="px-4 py-3 text-[var(--color-text-muted)] text-sm">
                    {task.model?.replace('claude-', '').replace(/-\d+$/, '') || '-'}
                  </td>
                  <td className="px-4 py-3 text-[var(--color-text-muted)] text-sm">
                    {formatRelativeTime(task.startedAt || task.createdAt)}
                  </td>
                  <td className="px-4 py-3 text-[var(--color-text-muted)] text-sm font-mono">
                    {formatDuration(task.startedAt, task.completedAt)}
                  </td>
                  <td className="px-4 py-3">
                    <div className="flex items-center justify-end gap-1">
                      <button
                        onClick={() => handleViewChat(task)}
                        title={t('tasks.actions.viewChat')}
                        className="p-2 text-[var(--color-text-muted)] hover:text-[var(--color-text)] hover:bg-[var(--color-hover)] rounded-lg transition-colors"
                      >
                        <span className="material-symbols-outlined text-xl">chat</span>
                      </button>
                      {task.status === 'running' && (
                        <button
                          onClick={() => setTaskToCancel(task)}
                          title={t('tasks.actions.cancel')}
                          className="p-2 text-[var(--color-text-muted)] hover:text-red-400 hover:bg-red-400/10 rounded-lg transition-colors"
                        >
                          <span className="material-symbols-outlined text-xl">stop_circle</span>
                        </button>
                      )}
                      <button
                        onClick={() => setTaskToDelete(task)}
                        title={t('tasks.actions.delete')}
                        className="p-2 text-[var(--color-text-muted)] hover:text-red-400 hover:bg-red-400/10 rounded-lg transition-colors"
                      >
                        <span className="material-symbols-outlined text-xl">delete</span>
                      </button>
                    </div>
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      {/* Confirm Dialogs */}
      <ConfirmDialog
        isOpen={!!taskToCancel}
        title={t('tasks.actions.cancel')}
        message={t('tasks.confirmCancel')}
        onConfirm={handleCancel}
        onCancel={() => setTaskToCancel(null)}
      />
      <ConfirmDialog
        isOpen={!!taskToDelete}
        title={t('tasks.actions.delete')}
        message={t('tasks.confirmDelete')}
        onConfirm={handleDelete}
        onCancel={() => setTaskToDelete(null)}
      />
    </div>
  );
}
```

**Step 2: Commit**

```bash
git add desktop/src/pages/TasksPage.tsx
git commit -m "feat(frontend): add TasksPage component"
```

---

## Task 11: Register TasksPage Route

**Files:**
- Modify: `desktop/src/App.tsx`

**Step 1: Import TasksPage and add route**

At the top of `App.tsx`, add:

```typescript
import TasksPage from './pages/TasksPage';
```

In the Routes section, add after the chat route:

```typescript
<Route path="tasks" element={<TasksPage />} />
```

**Step 2: Commit**

```bash
git add desktop/src/App.tsx
git commit -m "feat(frontend): register TasksPage route"
```

---

## Task 12: ChatPage Task Integration (Phase 1 - URL Support)

**Files:**
- Modify: `desktop/src/pages/ChatPage.tsx`

**Step 1: Add taskId URL parameter support**

This is a large refactor. The key changes:

1. Accept `?taskId=xxx` URL parameter
2. When taskId is provided, load task and subscribe to its stream
3. When no taskId, use existing behavior (create task on first message)

Add near the top of ChatPage component:

```typescript
const [searchParams] = useSearchParams();
const taskId = searchParams.get('taskId');
```

Add query to fetch task if taskId is provided:

```typescript
const { data: task } = useQuery({
  queryKey: ['task', taskId],
  queryFn: () => taskId ? tasksService.get(taskId) : null,
  enabled: !!taskId,
});
```

**Note:** Full ChatPage integration is complex and will be completed in Phase 2. For now, just add the URL parameter support to enable navigation from TasksPage.

**Step 2: Commit**

```bash
git add desktop/src/pages/ChatPage.tsx
git commit -m "feat(frontend): add taskId URL parameter support to ChatPage"
```

---

## Task 13: Final Integration Testing

**Step 1: Run backend tests**

```bash
cd /home/ubuntu/workspace/owork/.worktrees/task-management/backend
source .venv/bin/activate
pytest -v
```

Expected: All tests pass

**Step 2: Run frontend lint**

```bash
cd /home/ubuntu/workspace/owork/.worktrees/task-management/desktop
npm run lint
```

Expected: No errors

**Step 3: Start development server and test manually**

```bash
# Terminal 1: Backend
cd /home/ubuntu/workspace/owork/.worktrees/task-management/backend
source .venv/bin/activate
python main.py

# Terminal 2: Frontend
cd /home/ubuntu/workspace/owork/.worktrees/task-management/desktop
npm run tauri:dev
```

Manual test checklist:
- [ ] Tasks nav item appears in sidebar
- [ ] TasksPage loads and shows empty state
- [ ] Creating a chat creates a task
- [ ] Task appears in TasksPage
- [ ] Badge shows running count
- [ ] Can cancel/delete tasks
- [ ] Can navigate to chat from task

**Step 4: Final commit**

```bash
git add -A
git commit -m "feat: complete task management feature implementation"
```

---

## Summary

This implementation plan covers:

1. **Backend** (Tasks 1-4): Task schema, database, TaskManager, API router
2. **Frontend Types/Services** (Tasks 5-7): TypeScript types, API service, polling hook
3. **UI Components** (Tasks 8-11): Sidebar badge, i18n, TasksPage, routing
4. **Integration** (Tasks 12-13): ChatPage support, testing

Total: 13 tasks with TDD approach and frequent commits.
