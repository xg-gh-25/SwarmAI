# SwarmAI Command Permission Management — End-to-End Architecture

## Overview

SwarmAI implements a multi-layered command permission system that protects users from dangerous bash commands while allowing approved commands to execute without repeated prompts. The system combines regex-based blocking, glob-based dangerous detection, persistent filesystem-backed approvals, and a human-in-the-loop (HITL) approval flow via SSE.

Three components cooperate:
1. `security_hooks.py` — 4-layer PreToolUse defense chain (hooks that fire before every tool execution)
2. `CmdPermissionManager` — filesystem-backed dangerous pattern detection + persistent approval storage
3. `PermissionManager` — in-memory asyncio-based permission request/response flow (SSE signaling)

---

## Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────┐
│                  COMMAND PERMISSION FLOW                         │
│                                                                  │
│  Claude SDK subprocess                                           │
│  ├── Agent decides to run: rm -rf /tmp/old                      │
│  └── Emits tool_use(Bash, {command: "rm -rf /tmp/old"})         │
│         │                                                        │
│         ▼                                                        │
│  ┌──────────────────────────────────────────────┐               │
│  │  Layer 1: pre_tool_logger                     │               │
│  │  • Logs tool name + input keys                │               │
│  │  • Never blocks (observability only)          │               │
│  └──────────────────┬───────────────────────────┘               │
│                     │                                            │
│  ┌──────────────────▼───────────────────────────┐               │
│  │  Layer 2: dangerous_command_blocker           │               │
│  │  • 13 regex patterns (DANGEROUS_PATTERNS)     │               │
│  │  • Blocks: rm -rf /, fork bombs, dd, etc.     │               │
│  │  • Returns permissionDecision="deny" if match │               │
│  └──────────────────┬───────────────────────────┘               │
│                     │ (not blocked by Layer 2)                   │
│  ┌──────────────────▼───────────────────────────┐               │
│  │  Layer 3: human_approval_hook                 │               │
│  │                                               │               │
│  │  ┌─────────────────────────────────┐         │               │
│  │  │ CmdPermissionManager            │         │               │
│  │  │ .is_dangerous(command)?         │         │               │
│  │  │ (glob matching, 20 patterns)    │         │               │
│  │  └──────────┬──────────────────────┘         │               │
│  │             │                                 │               │
│  │    ┌────────▼────────┐                       │               │
│  │    │ is_approved()?  │                       │               │
│  │    │ (glob matching) │                       │               │
│  │    └────┬───────┬────┘                       │               │
│  │    YES  │       │ NO                         │               │
│  │    ┌────▼──┐  ┌─▼──────────────────┐        │               │
│  │    │ Allow │  │ SUSPEND execution   │        │               │
│  │    └───────┘  │ Create perm request │        │               │
│  │               │ Put in SSE queue    │        │               │
│  │               │ await decision      │        │               │
│  │               └─────────┬──────────┘        │               │
│  └─────────────────────────┼────────────────────┘               │
│                            │                                     │
│                            ▼                                     │
│  ┌─────────────────────────────────────────────┐                │
│  │  Frontend: PermissionRequestModal            │                │
│  │  User sees: command, reason, approve/deny    │                │
│  └──────────────────┬──────────────────────────┘                │
│                     │                                            │
│                     ▼                                            │
│  POST /api/chat/cmd-permission-continue                         │
│  ├── CmdPermissionManager.approve(command) — persistent         │
│  ├── set_permission_decision(request_id, decision)              │
│  └── Unblocks the suspended hook → command executes or skips    │
└─────────────────────────────────────────────────────────────────┘
```

---

## Layer 2: Dangerous Command Blocker (Regex)

Hard-blocks commands matching 13 regex patterns. These are never approved — they're always denied:

```python
DANGEROUS_PATTERNS = [
    (r'rm\s+(-[rfRf]+\s+)?/',           "Recursive deletion from root"),
    (r'rm\s+(-[rfRf]+\s+)?~',           "Recursive deletion from home"),
    (r'rm\s+-[rfRf]+',                   "Recursive file deletion"),
    (r'dd\s+if=/dev/(zero|random|urandom)', "Disk overwrite command"),
    (r'mkfs',                            "Filesystem format command"),
    (r'>\s*/dev/(sda|hda|nvme|vda)',     "Direct disk write"),
    (r':()\{:\|:&\};:',                  "Fork bomb"),
    (r'chmod\s+(-R\s+)?777\s+/',        "Dangerous permission change"),
    (r'chown\s+-R\s+.*\s+/',            "Recursive ownership change from root"),
    (r'curl\s+.*\|\s*(bash|sh)',         "Piping remote script to shell"),
    (r'wget\s+.*\|\s*(bash|sh)',         "Piping remote script to shell"),
    (r'sudo\s+rm',                       "Sudo removal command"),
    (r'>\s*/etc/',                        "Writing to /etc directory"),
]
```

`check_dangerous_command(command)` returns the reason string if matched, `None` otherwise. Case-insensitive regex matching.

---

## Layer 3: Human Approval Hook

The human approval hook is the core HITL mechanism. Created by `create_human_approval_hook()` with these bound parameters:

```python
def create_human_approval_hook(
    session_context,        # Dict with sdk_session_id (mutable, updated after init)
    session_key,            # Session key for tracking (agent_id or resume_session_id)
    enable_human_approval,  # Whether HITL is enabled for this agent
    permission_mgr,         # PermissionManager (asyncio events + queue)
    cmd_permission_mgr,     # CmdPermissionManager (filesystem-backed)
)
```

### Hook Execution Flow

```
human_approval_hook(input_data, tool_use_id, context)
  │
  ├── Guard: tool_name != "Bash" → return {} (allow)
  ├── Guard: empty command → return {} (allow)
  │
  ├── Is command dangerous?
  │   ├── CmdPermissionManager.is_dangerous(command) — glob matching (preferred)
  │   └── Fallback: check_dangerous_command(command) — regex (legacy)
  │   └── Not dangerous → return {} (allow)
  │
  ├── Human approval disabled?
  │   └── Block: return permissionDecision="deny"
  │
  ├── Previously approved?
  │   ├── CmdPermissionManager.is_approved(command) — glob, persistent (preferred)
  │   └── Fallback: PermissionManager.is_command_approved(session_key, command) — hash, per-session
  │   └── Approved → return {} (allow)
  │
  ├── Create permission request:
  │   ├── request_id = "perm_{uuid4().hex[:12]}"
  │   ├── Store in PermissionManager._pending_requests (in-memory)
  │   └── Put in permission_request_queue (for SSE streaming)
  │       {sessionId, requestId, toolName, toolInput, reason, options}
  │
  ├── SUSPEND: await permission_mgr.wait_for_permission_decision(request_id)
  │   └── Timeout: 300 seconds (5 minutes) → auto-deny
  │
  └── Decision received:
      ├── "approve":
      │   ├── CmdPermissionManager.approve(command) — persistent
      │   ├── Fallback: PermissionManager.approve_command() if pattern too broad
      │   └── return {} (allow execution)
      └── "deny":
          └── return permissionDecision="deny"
```

---

## CmdPermissionManager — Filesystem-Backed Approvals

### Design Principles

- **Zero IO on checks**: Both `is_dangerous()` and `is_approved()` read from in-memory lists
- **Glob matching**: Uses `fnmatch` — `rm -rf /tmp/*` matches `rm -rf /tmp/old`
- **Shared across sessions**: All agent sessions share the same approved list
- **Human-editable**: JSON files can be edited manually; call `reload()` to pick up changes
- **Overly-broad rejection**: `approve()` rejects bare `*` patterns

### File Structure

```
~/.swarm-ai/cmd_permissions/
├── dangerous_patterns.json    # Glob patterns for dangerous commands
└── approved_commands.json     # User-approved command patterns
```

### dangerous_patterns.json

```json
{
  "patterns": [
    "rm -rf *",
    "rm -rf /*",
    "rm -rf ~*",
    "sudo *",
    "chmod 777 *",
    "chmod -R 777 *",
    "chown -R * /",
    "kill -9 *",
    "mkfs.*",
    "dd if=*",
    "curl *|bash*",
    "curl *|sh*",
    "wget *|bash*",
    "wget *|sh*",
    "> /dev/sda*",
    "> /dev/hda*",
    "> /dev/nvme*",
    "> /dev/vda*",
    "> /etc/*",
    ":()*{*:*|*:*&*}*;*:*"
  ]
}
```

### approved_commands.json

```json
{
  "commands": [
    {
      "pattern": "rm -rf /tmp/*",
      "approved_at": "2026-03-07T10:30:00+00:00",
      "approved_by": "user"
    }
  ]
}
```

### API

```python
class CmdPermissionManager:
    def load(self)                    # Once at startup (reads both JSON files)
    def is_dangerous(command) -> bool # Zero IO (fnmatch against in-memory patterns)
    def is_approved(command) -> bool  # Zero IO (fnmatch against in-memory approved list)
    def approve(pattern) -> None      # Append to approved list + persist to disk
    def reload() -> None              # Force re-read from files
```

`approve()` raises `ValueError` for overly-broad patterns (`*`, `**`, `* *`).

---

## PermissionManager — In-Memory Asyncio Signaling

Handles the real-time permission request/response flow between the suspended hook and the frontend:

```python
class PermissionManager:
    _approved_commands: dict[str, set[str]]           # session_id → set of command hashes
    _permission_events: dict[str, asyncio.Event]      # request_id → Event (for signaling)
    _permission_results: dict[str, str]               # request_id → "approve" or "deny"
    _permission_request_queue: asyncio.Queue           # Shared queue for SSE streaming
    _pending_requests: dict[str, dict]                 # In-memory store for pending requests
```

### Key Methods

| Method | Purpose |
|--------|---------|
| `hash_command(command)` | SHA-256 hash (first 16 chars) for per-session tracking |
| `approve_command(session_id, command)` | Mark command as approved for a session (in-memory) |
| `is_command_approved(session_id, command)` | Check per-session approval (hash lookup) |
| `store_pending_request(data)` | Store pending request in memory |
| `wait_for_permission_decision(request_id, timeout=300)` | Suspend until user decides (5 min timeout) |
| `set_permission_decision(request_id, decision)` | Signal the waiting hook to continue |
| `get_permission_queue()` | Return the shared asyncio.Queue for SSE events |

### Signaling Flow

```
Hook suspends:
  event = asyncio.Event()
  _permission_events[request_id] = event
  await event.wait()  # BLOCKED

User decides (via frontend):
  _permission_results[request_id] = "approve"
  _permission_events[request_id].set()  # UNBLOCKS the hook

Hook resumes:
  decision = _permission_results[request_id]
  # Clean up events and results
```

Timeout: If user doesn't respond within 300 seconds, auto-deny. Pending request status updated to "expired".

---

## SSE Permission Flow (End-to-End)

### 1. Hook Creates Request

```python
# In human_approval_hook:
request_id = f"perm_{uuid4().hex[:12]}"
await permission_mgr.get_permission_queue().put({
    "sessionId": actual_session_id,
    "requestId": request_id,
    "toolName": "Bash",
    "toolInput": {"command": "rm -rf /tmp/old"},
    "reason": "Matches dangerous command pattern",
    "options": ["approve", "deny"],
})
```

### 2. Fan-In Queue Forwards to SSE

In `_run_query_on_client`, the `permission_request_forwarder` task monitors the global queue:

```python
async def permission_request_forwarder():
    while True:
        request = await _permission_request_queue.get()
        if request["sessionId"] == current_session_id:
            await combined_queue.put({"source": "permission", "request": request})
        else:
            await _permission_request_queue.put(request)  # Put back for other sessions
            await asyncio.sleep(0.01)  # Prevent busy-loop
```

### 3. Main Loop Yields SSE Event

```python
if item["source"] == "permission":
    yield {"type": "cmd_permission_request", **request}
```

### 4. Frontend Shows Modal

```
SSE event: cmd_permission_request
  → setPendingPermission(request)
  → updateTabStatus('permission_needed')
  → Show PermissionRequestModal with command, reason, approve/deny buttons
```

### 5. User Decides → Backend Endpoint

```
POST /api/chat/cmd-permission-continue
{
  request_id: "perm_abc123def456",
  session_id: "abc-123",
  decision: "approve",
  feedback: null
}
```

### 6. Backend Processes Decision

```python
# In continue_with_cmd_permission():
if decision == "approve":
    cmd_permission_mgr.approve(command)  # Persistent, filesystem-backed
    # Fallback: per-session approval if pattern too broad
set_permission_decision(request_id, decision)  # Unblocks the hook
```

### 7. Original Stream Continues

The original SSE stream (from `POST /api/chat/stream`) was suspended at the hook. `set_permission_decision()` unblocks it:
- Approved: command executes, tool_result flows back
- Denied: hook returns `permissionDecision="deny"`, SDK skips the command

---

## Layer 4: Skill Access Checker

Separate from command permissions, but part of the same hook chain:

```python
def create_skill_access_checker(allowed_skill_names, builtin_skill_names):
    async def skill_access_checker(input_data, tool_use_id, context):
        if input_data.get('tool_name') == 'Skill':
            requested_skill = input_data['tool_input'].get('skill', '')
            if requested_skill in builtin_set:
                return {}  # Built-in always allowed
            if requested_skill not in allowed_set:
                return {"permissionDecision": "deny"}
        return {}
```

Only added when `enable_skills=True AND allow_all_skills=False`. Built-in skills are always allowed regardless of the allowed list.

---

## File Access Permission Handler

Separate from command permissions, used when `global_user_mode=False`:

```python
def create_file_access_permission_handler(allowed_directories):
    # Normalizes paths via os.path.realpath() (resolves symlinks)
    # Checks file tools: Read, Write, Edit, Glob, Grep → path_param
    # Checks Bash tool: extracts potential file paths via regex
    # Returns {"behavior": "deny"} if path outside allowed dirs
    # Returns {"behavior": "allow"} otherwise
```

Set as `ClaudeAgentOptions.can_use_tool` — invoked for EVERY tool call. Default is `None` (no restrictions) when `global_user_mode=True`.

---

## Two Permission Systems: Why Both Exist

| Aspect | CmdPermissionManager | PermissionManager |
|--------|---------------------|-------------------|
| Storage | Filesystem (JSON files) | In-memory (dict) |
| Scope | Shared across all sessions | Per-session |
| Persistence | Survives restarts | Lost on restart |
| Matching | Glob (`fnmatch`) | Hash (SHA-256) |
| Purpose | Dangerous detection + approval storage | Asyncio signaling + legacy approval |
| Loaded | Once at startup (`load()`) | Created at import time |

The `CmdPermissionManager` is the primary system — persistent, shared, glob-based. The `PermissionManager` provides the asyncio signaling mechanism (events + queue) and serves as a fallback when `CmdPermissionManager` rejects an overly-broad pattern.

---

## Hook Assembly in AgentManager

```python
async def _build_hooks(self, agent_config, enable_skills, enable_mcp, ...):
    hooks = {"PreToolUse": []}

    # Layer 1: Tool logger (all tools)
    hooks["PreToolUse"].append(HookMatcher(hooks=[pre_tool_logger]))

    # Layer 2: Dangerous command blocker (Bash only)
    hooks["PreToolUse"].append(HookMatcher(matcher="Bash", hooks=[dangerous_command_blocker]))

    # Layer 3: Human approval hook (Bash only)
    human_approval = create_human_approval_hook(
        session_context, session_key, enable_human_approval,
        permission_mgr, cmd_permission_mgr
    )
    hooks["PreToolUse"].append(HookMatcher(matcher="Bash", hooks=[human_approval]))

    # Layer 4: Skill access checker (Skill only, conditional)
    if enable_skills and not allow_all_skills:
        skill_checker = create_skill_access_checker(allowed_skills, builtin_names)
        hooks["PreToolUse"].append(HookMatcher(matcher="Skill", hooks=[skill_checker]))

    return hooks
```

---

## Startup Initialization

```python
# In main.py lifespan:
cmd_perm = CmdPermissionManager()
cmd_perm.load()  # Reads dangerous_patterns.json + approved_commands.json

agent_manager.configure(
    config_manager=app_config,
    cmd_permission_manager=cmd_perm,  # Injected into AgentManager
    credential_validator=cred_validator,
)
```

---

## File Structure Reference

```
backend/core/
├── security_hooks.py          # DANGEROUS_PATTERNS, check_dangerous_command,
│                              # pre_tool_logger, dangerous_command_blocker,
│                              # create_human_approval_hook,
│                              # create_file_access_permission_handler,
│                              # create_skill_access_checker
├── cmd_permission_manager.py  # CmdPermissionManager, DEFAULT_DANGEROUS_PATTERNS
├── permission_manager.py      # PermissionManager (asyncio signaling)
└── agent_manager.py           # _build_hooks() assembly

~/.swarm-ai/cmd_permissions/
├── dangerous_patterns.json    # Glob patterns (seeded from DEFAULT_DANGEROUS_PATTERNS)
└── approved_commands.json     # User-approved patterns (persistent)
```
