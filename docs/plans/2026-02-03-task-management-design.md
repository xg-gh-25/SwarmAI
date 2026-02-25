# Agent Task Management Design

**Date:** 2026-02-03
**Status:** Ready for implementation

## Problem Statement

Chat sessions in the current implementation are tied to frontend SSE connections. When users navigate away from the ChatPage, the connection closes and the agent stops working. Users lose their work progress.

## Solution Overview

Implement a **Task Management** system that decouples agent execution from frontend connections. Tasks run persistently in the backend, and users can reconnect to view progress or continue interacting at any time.

**Key Decisions:**
- **Chat-centric model**: Users can send messages to running tasks (not fire-and-forget)
- **Backend-managed tasks**: Python asyncio tasks, no external queue infrastructure
- **Sidebar badge**: Running task count displayed as badge on Tasks nav item

---

## Architecture

### Current Flow (Problem)
```
SSE Connection → ClaudeSDKClient → dies when connection closes
```

### New Flow (Solution)
```
Backend Task Manager
├── Task 1: ClaudeSDKClient (running independently)
├── Task 2: ClaudeSDKClient (running independently)
└── Task 3: completed (results cached)

SSE Connection → subscribes to task events → can reconnect anytime
```

### Component Overview

| Component | Location | Responsibility |
|-----------|----------|----------------|
| `TaskManager` | `backend/core/task_manager.py` | Spawns/tracks background tasks, stores events |
| `Task` model | `backend/schemas/task.py` | Task metadata (status, agent, timestamps) |
| Tasks table | `backend/database/sqlite.py` | Persist task state across restarts |
| Tasks router | `backend/routers/tasks.py` | REST + SSE endpoints for tasks |

---

## Data Model

### Task Schema

```python
class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

class Task(BaseModel):
    id: str                      # UUID
    agent_id: str                # Which agent runs this task
    session_id: str | None       # Claude SDK session (for resume)
    status: TaskStatus           # Current state
    title: str                   # First message truncated
    model: str                   # Model being used
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    error: str | None            # Error message if failed
    work_dir: str | None         # Working directory
```

### Database Table (SQLite)

```sql
CREATE TABLE tasks (
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
    work_dir TEXT,
    FOREIGN KEY (agent_id) REFERENCES agents(id)
);

CREATE INDEX idx_tasks_status ON tasks(status);
CREATE INDEX idx_tasks_agent_id ON tasks(agent_id);
```

---

## API Design

### Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/tasks` | List all tasks (with optional status filter) |
| `GET` | `/api/tasks/{id}` | Get task details |
| `POST` | `/api/tasks` | Create new task (starts agent) |
| `DELETE` | `/api/tasks/{id}` | Delete task (and cancel if running) |
| `POST` | `/api/tasks/{id}/cancel` | Cancel running task |
| `GET` | `/api/tasks/{id}/stream` | SSE stream for task events |
| `POST` | `/api/tasks/{id}/message` | Send message to running task |
| `GET` | `/api/tasks/running/count` | Get count of running tasks (for badge) |

### Request/Response Examples

**Create Task:**
```json
POST /api/tasks
{
    "agent_id": "abc123",
    "message": "Help me build a REST API",
    "enable_skills": true,
    "enable_mcp": true,
    "add_dirs": ["/path/to/project"]
}

Response:
{
    "id": "task_xyz789",
    "agent_id": "abc123",
    "status": "pending",
    "title": "Help me build a REST API",
    ...
}
```

**Send Message to Task:**
```json
POST /api/tasks/{id}/message
{
    "message": "Now add authentication",
    "content": null
}
```

---

## Frontend Design

### New Files

| File | Purpose |
|------|---------|
| `desktop/src/pages/TasksPage.tsx` | Main task list page |
| `desktop/src/services/tasks.ts` | API service for tasks |
| `desktop/src/hooks/useRunningTaskCount.ts` | Polling hook for badge |

### TasksPage Layout

```
┌─────────────────────────────────────────────────────────────┐
│  Task Management                          [+ New Task]      │
│  Monitor and manage your running agent tasks.               │
├─────────────────────────────────────────────────────────────┤
│  🔍 Search tasks...                    [Filter: All ▼]      │
├─────────────────────────────────────────────────────────────┤
│  TASK NAME  │ AGENT │ STATUS  │ MODEL │ STARTED │ DURATION │ ACTIONS │
├─────────────────────────────────────────────────────────────┤
│  "Help me   │ Test1 │ ● Running│ sonnet│ 2m ago  │ 2:15    │ 💬 ⏹ 🗑 │
│   build..." │       │  ↻       │       │         │         │         │
├─────────────────────────────────────────────────────────────┤
│  "Analyze   │ Test1 │ ✓ Done  │ sonnet│ 1h ago  │ 5:32    │ 💬    🗑 │
│   the..."   │       │         │       │         │         │         │
└─────────────────────────────────────────────────────────────┘
```

### Status Badges

| Status | Style |
|--------|-------|
| Running | Blue badge with spinner animation |
| Completed | Green badge with checkmark |
| Failed | Red badge with X |
| Cancelled | Gray badge |

### Quick Actions

- **View Chat** (💬): Opens ChatPage with `?taskId=xxx`
- **Cancel** (⏹): Stops running task (only for running tasks)
- **Delete** (🗑): Removes task record

### Sidebar Navigation

Add "Tasks" nav item with badge:

```typescript
const navItems: NavItem[] = [
    { path: '/chat', labelKey: 'nav.chat', icon: 'chat' },
    { path: '/tasks', labelKey: 'nav.tasks', icon: 'task_alt' },  // NEW
    { path: '/agents', labelKey: 'nav.agents', icon: 'smart_toy' },
    // ...
];
```

Badge shows running task count (polls every 5 seconds, only visible when count > 0).

---

## ChatPage Integration

### URL Structure

```
/chat                     → New chat (select agent first)
/chat?taskId=abc123       → View/continue specific task
/chat?agentId=xyz         → Start new task with specific agent
```

### Behavior Changes

| Current | New |
|---------|-----|
| Manages SSE connection directly | Delegates to TaskService |
| Creates session on first message | Creates Task on first message |
| Session lost on navigate away | Task persists, reconnect on return |
| `sessionId` state | `taskId` state |

### Reconnection Flow

1. ChatPage mounts with `taskId`
2. Fetch task status (running/completed?)
3. Fetch messages from database
4. If running: subscribe to SSE stream
5. If completed: show results, allow new messages to resume

---

## Implementation Plan

### Phase 1: Backend Foundation
1. Create `backend/schemas/task.py` - Task models
2. Add tasks table to `backend/database/sqlite.py`
3. Create `backend/core/task_manager.py` - Core task orchestration
4. Create `backend/routers/tasks.py` - API endpoints
5. Register router in `backend/main.py`

### Phase 2: Frontend Task List
6. Create `desktop/src/services/tasks.ts` - API service
7. Add Task types to `desktop/src/types/index.ts`
8. Create `desktop/src/pages/TasksPage.tsx` - Task list UI
9. Add route in `desktop/src/App.tsx`

### Phase 3: Sidebar Integration
10. Create `desktop/src/hooks/useRunningTaskCount.ts`
11. Update `desktop/src/components/common/Sidebar.tsx` - Add nav + badge
12. Add i18n strings to `en.json` and `zh.json`

### Phase 4: ChatPage Migration
13. Update `desktop/src/pages/ChatPage.tsx` - Integrate with TaskService
14. Update `desktop/src/services/chat.ts` - Coordinate with tasks

### Phase 5: Testing & Polish
15. Test task lifecycle (create, run, cancel, complete, fail)
16. Test reconnection behavior
17. Test concurrent tasks
18. Polish UI animations and error states

---

## i18n Additions

### English (`en.json`)
```json
{
    "nav": {
        "tasks": "Tasks"
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
        "newTask": "New Task"
    }
}
```

### Chinese (`zh.json`)
```json
{
    "nav": {
        "tasks": "任务"
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
        "newTask": "新建任务"
    }
}
```

---

## Open Questions (Resolved)

1. **Interaction model?** → Chat-centric (users can send messages to running tasks)
2. **Backend architecture?** → Backend-managed asyncio tasks
3. **Global indicator placement?** → Sidebar badge on Tasks nav item

## Future Considerations (Out of Scope)

- Task scheduling (run at specific time)
- Task templates (reusable task configurations)
- Task sharing between users (cloud mode)
- Task resource limits (max concurrent tasks)
