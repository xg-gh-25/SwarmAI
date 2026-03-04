# Proposal: Context Refresh, Scoped Retrieval & TSCC System Prompt Display

## Problem Statement

Three related gaps exist in the current architecture:

1. **TBD 5 — Scoped Retrieval (Layer 8)**: `ContextAssembler._load_layer_8_scoped_retrieval()` is a stub returning `None`. No mechanism exists to retrieve context based on the user's query or conversation topic.

2. **TBD 6 — Dynamic Context Refresh**: The system prompt (including all `.context/` files and project context) is assembled once at session creation. For resumed sessions reusing a long-lived SDK client, the system prompt is NEVER refreshed. If `MEMORY.md`, `STEERING.md`, or project files change between turns, the agent doesn't see updates.

3. **TSCC System Prompt Visibility**: The TSCC popover shows "what AI is doing" (tool calls, agents, sources) but does NOT show what context the agent is actually working with. Users can't see the assembled system prompt, which context files were loaded, or how many tokens were consumed.

## Current Architecture

```
Session Start (new or resumed):
  _build_options()
    → _build_system_prompt()
      → ContextDirectoryLoader.load_all()     ← runs ONCE
      → ContextAssembler.assemble()           ← runs ONCE
      → SystemPromptBuilder.build()           ← runs ONCE
    → ClaudeAgentOptions(system_prompt=...)
    → ClaudeSDKClient(options)                ← prompt frozen here

Turn 2, 3, 4...:
  client.query(message)                       ← same frozen prompt
  (no context refresh, no scoped retrieval)
```

### What TSCC Currently Shows

The TSCC popover (🧠 button in ChatInput) displays 5 modules:
- **Current Context**: scope label, thread title
- **Active Agents**: which agents are running
- **What AI Doing**: last 4 tool calls / activities
- **Active Sources**: files the agent has read
- **Key Summary**: conversation summary points

Missing: the actual system prompt content, context file list, token usage.

## Proposal

### Part 1: Deprecate ContextAssembler — Unify into ContextDirectoryLoader

The 8-layer `ContextAssembler` was designed for a world where context was scattered. Now that `ContextDirectoryLoader` handles global context from `~/.swarm-ai/.context/`, the ContextAssembler's remaining value is project-scoped context (Layers 1-8). But most of those layers overlap with what `.context/` already provides:

| ContextAssembler Layer | Overlap with .context/ | Status |
|---|---|---|
| Layer 1: system-prompts.md | Replaced by `.context/SWARMAI.md` | Redundant |
| Layer 2: Live work (thread messages, tasks) | Unique — thread-specific | Keep |
| Layer 3: Project instructions.md | Could move to `.context/PROJECTS.md` or per-project `.context/` | Replaceable |
| Layer 4: Project semantic (context-L0/L1) | Replaced by `.context/KNOWLEDGE.md` | Redundant |
| Layer 5: Knowledge semantic (context-L0/L1) | Replaced by `.context/KNOWLEDGE.md` | Redundant |
| Layer 6: Memory (Knowledge/Memory/*.md) | Replaced by `.context/MEMORY.md` | Redundant |
| Layer 7: Workspace semantic (context-L0/L1) | Replaced by `.context/KNOWLEDGE.md` | Redundant |
| Layer 8: Scoped retrieval | Stub — never implemented | TBD |

**Proposal**: Deprecate `ContextAssembler` entirely. Move Layer 2 (live work — thread messages, bound tasks/todos) into `ContextDirectoryLoader` as an optional runtime injection. This eliminates the dual-loader architecture.

```
Before (dual loader):
  ContextDirectoryLoader (global) + ContextAssembler (project) + SystemPromptBuilder

After (single loader):
  ContextDirectoryLoader (global + live work) + SystemPromptBuilder
```

Layer 2 (live work) becomes a method on `ContextDirectoryLoader`:

```python
def inject_live_work(self, thread_id: str, db) -> str:
    """Load thread messages, bound tasks/todos for the current session.
    Appended after the 9 source files, before SystemPromptBuilder runs.
    """
```

### Part 2: Scoped Retrieval via .context/ File Tagging

Instead of a complex RAG system (Layer 8), implement lightweight scoped retrieval using YAML frontmatter tags in `.context/` files:

```markdown
---
tags: [python, backend, fastapi]
scope: always  # always | tagged | manual
---
# Knowledge — What I Know
...
```

Scope modes:
- `always` (default): File is always loaded (current behavior)
- `tagged`: File is loaded only when the user's message or thread context matches any tag
- `manual`: File is loaded only when explicitly referenced (e.g., `/context knowledge`)

This gives users lightweight control over which context files are loaded per-session without building a full vector search system.

**Implementation**: Add a `_filter_by_scope()` step in `_assemble_from_sources()` that reads YAML frontmatter from each file and checks tags against the conversation's keywords (extracted from the user's first message or thread title).

### Part 3: Dynamic Context Refresh via Git

Replace the mtime-based version hash with git-based change detection. This is simpler, more reliable, and gives us history for free.

**How it works:**

At session start, record the current git commit hash:
```python
session_context["context_commit"] = subprocess.check_output(
    ["git", "rev-parse", "HEAD"], cwd=swarmws_path
).decode().strip()
```

Before each resumed turn, check if anything changed:
```python
current_commit = subprocess.check_output(
    ["git", "rev-parse", "HEAD"], cwd=swarmws_path
).decode().strip()

if current_commit != session_context["context_commit"]:
    # Context changed — rebuild system prompt, create fresh SDK client
    options = await self._build_options(...)
    client = new ClaudeSDKClient(options)
    session_context["context_commit"] = current_commit
```

If the workspace has uncommitted changes (agent wrote to MEMORY.md mid-session), use `git diff --quiet` instead:
```python
# Returns exit code 1 if there are uncommitted changes
result = subprocess.run(
    ["git", "diff", "--quiet", "--", ".context/"],
    cwd=swarmws_path, capture_output=True
)
context_changed = result.returncode != 0
```

**Why git is better than mtime:**
- Single command instead of stat-ing 9 files
- Handles renames, deletions, not just modifications
- No TOCTOU race — git's index is atomic
- Gives us `git diff` for the TSCC display (show what changed since session start)
- History is free — `git log` shows when context files were last updated

**Auto-commit triggers:**
- Session end (normal completion) → `git add .context/ && git commit -m "Session: {title}"`
- Agent writes to MEMORY.md → commit happens at session end, not per-write
- User edits a context file externally → detected on next turn via `git diff --quiet`

**The refresh flow with git:**

```
Session start:
  record context_commit = git rev-parse HEAD

Turn 1: User asks question
  → Agent responds, writes to MEMORY.md
  → MEMORY.md is now a dirty file in git (uncommitted)

Turn 2: User asks follow-up
  → git diff --quiet .context/ → exit code 1 (dirty)
  → Rebuild system prompt with updated MEMORY.md
  → Create fresh SDK client
  → Agent sees updated memory

Session end:
  → git add .context/ && git commit -m "Session: {title}"
  → Clean state for next session
```

### Part 4: Simplify TSCC to System Prompt Display

Reposition TSCC from "what AI is doing" to "what context the agent has". The popover becomes a system prompt viewer — showing users exactly what the agent knows for this session.

**Deprecate these TSCC modules:**
- `WhatAIDoingModule` — tool call activity (noisy, low value)
- `ActiveSourcesModule` — files the agent has read (redundant with chat content)
- `ActiveAgentsModule` — which agents are running (single-agent for now)
- `KeySummaryModule` — conversation summary (redundant with chat history)

**Keep and enhance:**
- `CurrentContextModule` → rename to `SystemPromptModule`

**New TSCC popover design:**

```
┌─────────────────────────────────────────┐
│ 🧠 System Prompt                        │
│                                          │
│ ── Context Files ──────────────────────  │
│ ✅ SWARMAI.md        1,200 tokens       │
│ ✅ IDENTITY.md         150 tokens       │
│ ✅ SOUL.md             400 tokens       │
│ ✅ AGENT.md            800 tokens       │
│ ✅ USER.md             300 tokens       │
│ ✅ STEERING.md         500 tokens       │
│ ✅ MEMORY.md         2,100 tokens       │
│ ⚠️ KNOWLEDGE.md     1,500 tokens (trunc)│
│ ✅ PROJECTS.md         600 tokens       │
│                                          │
│ Total: 7,550 / 25,000 tokens            │
│ ████████░░░░░░░░░░░░░░░░░░  30%         │
│                                          │
│ [View Full Prompt]                       │
└─────────────────────────────────────────┘
```

Clicking a file name expands to show that section's content inline. "View Full Prompt" opens a modal with the complete assembled system prompt text (syntax-highlighted markdown).

**Backend changes:**
- Add `assembled_prompt` and `context_files` fields to the TSCC state
- Populate after `ContextDirectoryLoader.load_all()` returns in `_build_system_prompt()`
- New endpoint: `GET /api/chat/{session_id}/system-prompt` returns the full assembled prompt
- Deprecate telemetry events: `agent_activity`, `tool_invocation`, `sources_updated`, `capability_activated`

**Frontend changes:**
- Replace 5 TSCC modules with single `SystemPromptModule`
- Show context file list with token counts and truncation indicators
- Token usage progress bar
- Expandable file sections
- "View Full Prompt" modal
- Remove `useTSCCState` telemetry event handling (no more incremental SSE updates for TSCC)

**Files to deprecate:**
- `desktop/src/pages/chat/components/TSCCModules.tsx` — all 5 modules replaced by `SystemPromptModule`
- `backend/core/telemetry_emitter.py` — no longer needed (TSCC doesn't track tool activity)
- `backend/core/tscc_snapshot_manager.py` — snapshots of tool activity no longer relevant
- TSCC snapshot endpoints in `backend/routers/tscc.py` — simplify to just system prompt retrieval

## Implementation Priority

| Part | Effort | Impact | Dependency |
|------|--------|--------|------------|
| Part 3: Context refresh (Option B) | Medium | High — fixes stale context bug | None |
| Part 4: Simplify TSCC to system prompt display | Medium | High — user visibility + code cleanup | Part 3 (needs file list from loader) |
| Part 1: Deprecate ContextAssembler | High | Medium — simplifies architecture | Part 3 |
| Part 2: Scoped retrieval via tags | Medium | Medium — lightweight filtering | Part 1 |

## Files to Deprecate

### From Part 1 (ContextAssembler deprecation):
- `backend/core/context_assembler.py` — replaced by ContextDirectoryLoader + live work injection
- `backend/core/context_snapshot_cache.py` — replaced by L1 cache in CDL
- `backend/core/context_manager.py` — already unused after CDL implementation
- `SwarmWS/system-prompts.md` — replaced by `.context/SWARMAI.md`
- `SwarmWS/context-L0.md`, `context-L1.md` — replaced by `.context/KNOWLEDGE.md`
- `SwarmWS/Knowledge/Memory/*.md` — replaced by `.context/MEMORY.md`
- `SwarmWS/Projects/{id}/instructions.md` — replaced by `.context/PROJECTS.md`
- `SwarmWS/Projects/{id}/context-L0.md`, `context-L1.md` — replaced by `.context/KNOWLEDGE.md`

### From Part 4 (TSCC simplification):
- `desktop/src/pages/chat/components/TSCCModules.tsx` — all 5 modules replaced by `SystemPromptModule`
- `backend/core/telemetry_emitter.py` — TSCC no longer tracks tool activity
- `backend/core/tscc_snapshot_manager.py` — snapshots of tool activity no longer relevant
- TSCC snapshot endpoints in `backend/routers/tscc.py` — simplify to system prompt retrieval only
- `backend/core/tscc_state_manager.py` — simplify to only track system prompt metadata (remove agent_activity, tool_invocation, sources_updated, capability_activated event handling)

## Open Questions

1. Should `MEMORY.md` be auto-updated by the agent at end of each session, or only on explicit user request?
2. Should `PROJECTS.md` be auto-generated from workspace git activity, or manually maintained?
3. Should `STEERING.md` support expiration dates (e.g., "valid until 2026-03-10")?
4. Should we support per-project overrides (e.g., `~/.swarm-ai/.context/projects/{id}/STEERING.md`)?
5. L0 generation: Should it use AI summarization or rule-based compression?
6. Git auto-commit: should it happen at session end only, or also on explicit "save memory" commands?
7. Should the git repo be exposed to users (they can push/pull for cross-machine sync) or kept internal?
8. For context refresh, should the fresh client inherit conversation history from the old client, or start clean?

---

## Part 6: Git-Backed Workspace

### Motivation

SwarmWS (`~/.swarm-ai/SwarmWS/`) contains all workspace files — projects, knowledge, artifacts, and the `.context/` directory. Introducing a local git repo at the workspace root gives us change tracking, diffing, history, and rollback with zero custom infrastructure.

### What Git Gives Us

| Capability | Without Git | With Git |
|---|---|---|
| Detect context file changes | Stat 9 files, compare mtimes (TOCTOU risk) | `git diff --quiet .context/` (atomic, one command) |
| Know what changed | Re-read all files, diff in memory | `git diff HEAD -- .context/MEMORY.md` (exact diff) |
| History of changes | None — files are overwritten | `git log --oneline .context/MEMORY.md` |
| Rollback bad edits | Manual backup/restore | `git checkout .context/STEERING.md` |
| PROJECTS.md auto-refresh | Custom file watcher + extraction | `git diff --stat` → what files changed → update PROJECTS.md |
| KNOWLEDGE.md enrichment | Manual | `git log --since="7 days" --name-only` → recent activity |
| Cross-machine sync | Not possible | `git remote add && git push` (user opt-in) |
| Session checkpoints | None | Auto-commit at session end with session title |
| TSCC "what changed" display | Not possible | `git diff {session_start_commit} -- .context/` |

### Implementation

**Startup (`initialization_manager.py`):**
```python
def _ensure_git_repo(workspace_path: str) -> None:
    """Initialize git repo in SwarmWS if not already initialized."""
    git_dir = Path(workspace_path) / ".git"
    if git_dir.exists():
        return
    subprocess.run(["git", "init"], cwd=workspace_path, capture_output=True)
    subprocess.run(["git", "add", "-A"], cwd=workspace_path, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "Initial SwarmWS state"],
        cwd=workspace_path, capture_output=True,
    )
```

**Session end (auto-commit):**
```python
async def _auto_commit_workspace(workspace_path: str, session_title: str) -> None:
    """Auto-commit workspace changes at session end."""
    result = subprocess.run(
        ["git", "diff", "--quiet"], cwd=workspace_path, capture_output=True,
    )
    if result.returncode == 0:
        return  # Nothing changed
    subprocess.run(["git", "add", "-A"], cwd=workspace_path, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", f"Session: {session_title[:50]}"],
        cwd=workspace_path, capture_output=True,
    )
```

**PROJECTS.md auto-refresh from git activity:**
```python
def _refresh_projects_from_git(workspace_path: str) -> dict[str, list[str]]:
    """Detect active projects from recent git activity."""
    result = subprocess.run(
        ["git", "log", "--since=7 days ago", "--name-only", "--pretty=format:"],
        cwd=workspace_path, capture_output=True, text=True,
    )
    changed_files = [f for f in result.stdout.strip().split("\n") if f]
    projects = {}
    for f in changed_files:
        parts = f.split("/")
        if len(parts) >= 2 and parts[0] == "Projects":
            projects.setdefault(parts[1], []).append(f)
    return projects
```

### .gitignore for SwarmWS

```
*.db
*.db-wal
*.db-shm
__pycache__/
.venv/
node_modules/
*.pyc
.DS_Store
```

### Integration with Other Parts

**Part 3 (Context Refresh)** uses `git diff --quiet .context/` instead of mtime hashing. Simpler, atomic, no TOCTOU.

**Part 4 (TSCC Display)** can show `git diff {session_start_commit} -- .context/` to display what context changed since session started.

**PROJECTS.md refresh** uses `git log --name-only` to detect which project directories had recent activity, then auto-updates the active projects list.

### What This Replaces

- Custom file watcher → `git log --name-only`
- Mtime-based L1 cache freshness → `git diff --quiet .context/`
- TOCTOU mitigation in `_load_l1_if_fresh()` → git's atomic index
- `ContextSnapshotCache` version counters → `git rev-parse HEAD`
- Manual backup/restore → `git checkout`

## Updated Implementation Priority

| Part | Effort | Impact | Dependency |
|------|--------|--------|------------|
| Part 6: Git-backed workspace | Low | High — foundation for Parts 3, 4, and PROJECTS.md refresh | None |
| Part 3: Context refresh via git | Medium | High — fixes stale context bug | Part 6 |
| Part 4: Simplify TSCC to system prompt display | Medium | High — user visibility + code cleanup | Part 3 |
| Part 1: Deprecate ContextAssembler | High | Medium — simplifies architecture | Part 3 |
| Part 2: Scoped retrieval via tags | Medium | Low — defer until token pressure is real | Part 1 |
