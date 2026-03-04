<!-- PE-REVIEWED -->
# Design: SwarmWS Restructure + Git Init

## Overview

Strip SwarmWS to a minimal git-backed workspace. All context injection is handled by `ContextDirectoryLoader` from `~/.swarm-ai/.context/`. SwarmWS is just the agent's working directory with `Knowledge/`, `Projects/`, and user files.

## New Structure

```
~/.swarm-ai/SwarmWS/
├── .git/                    ← Auto-initialized
├── .gitignore
├── .claude/skills/          ← SDK skill discovery (unchanged)
├── Projects/
│   └── {name}/.project.json
├── Knowledge/
└── (user files)
```

## Changes

### 1. swarm_workspace_manager.py

Replace all constants:
```python
FOLDER_STRUCTURE = ["Knowledge", "Projects"]
SYSTEM_MANAGED_FOLDERS = {"Knowledge", "Projects"}
SYSTEM_MANAGED_ROOT_FILES = set()
SYSTEM_MANAGED_SECTION_FILES = set()
PROJECT_SYSTEM_FILES = {".project.json"}
PROJECT_SYSTEM_FOLDERS = set()
```

Delete: `CONTEXT_L0_TEMPLATE`, `CONTEXT_L1_TEMPLATE`, `SYSTEM_PROMPTS_TEMPLATE`,
`KNOWLEDGE_SECTIONS`, `_populate_sample_data()`, all `_write_file_if_missing()`
calls for deleted system files.

Simplify `create_folder_structure()`:
```python
async def create_folder_structure(self, workspace_path: str) -> None:
    root = Path(self.expand_path(workspace_path))
    root.mkdir(parents=True, exist_ok=True)
    for folder in FOLDER_STRUCTURE:
        (root / folder).mkdir(parents=True, exist_ok=True)
    # Write .gitignore
    gitignore = root / ".gitignore"
    if not gitignore.exists():
        gitignore.write_text(GITIGNORE_CONTENT, encoding="utf-8")
```

Simplify `verify_integrity()`:
```python
async def verify_integrity(self, workspace_path: str) -> bool:
    root = Path(workspace_path)
    recreated = False
    for folder in FOLDER_STRUCTURE:
        p = root / folder
        if not p.exists():
            p.mkdir(parents=True, exist_ok=True)
            recreated = True
    return recreated
```

### 2. Git Init

```python
GITIGNORE_CONTENT = "*.db\n*.db-wal\n*.db-shm\n__pycache__/\n.venv/\nnode_modules/\n*.pyc\n.DS_Store\n"

def _ensure_git_repo(self, workspace_path: str) -> bool:
    if (Path(workspace_path) / ".git").exists():
        return True
    try:
        subprocess.run(["git", "init"], cwd=workspace_path, capture_output=True, check=True)
        subprocess.run(["git", "add", "-A"], cwd=workspace_path, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Initial SwarmWS state", "--allow-empty"],
                       cwd=workspace_path, capture_output=True)
        return True
    except (FileNotFoundError, subprocess.CalledProcessError) as exc:
        logger.warning("Git init failed (non-blocking): %s", exc)
        return False
```

Called from `ensure_default_workspace()` after `create_folder_structure()`.

### 3. Session Auto-Commit (agent_manager.py)

```python
async def _auto_commit_workspace(self, title: str) -> None:
    ws_path = initialization_manager.get_cached_workspace_path()
    def _commit():
        try:
            r = subprocess.run(["git", "diff", "--quiet"], cwd=ws_path, capture_output=True)
            if r.returncode == 0:
                return
            subprocess.run(["git", "add", "-A"], cwd=ws_path, capture_output=True)
            subprocess.run(["git", "commit", "-m", f"Session: {title[:50]}"],
                           cwd=ws_path, capture_output=True)
        except Exception as exc:
            logger.warning("Auto-commit failed: %s", exc)
    await asyncio.to_thread(_commit)
```

Called after ResultMessage in `_run_query_on_client()`.

### 4. Context Refresh (Background L1 Cache Invalidation)

Context refresh does NOT touch running sessions. It only keeps the L1 cache fresh so new sessions get updated context.

```python
# In ContextDirectoryLoader — already implemented:
# load_all() checks L1 freshness via _is_l1_fresh() (mtime comparison)
# If stale, re-assembles from source files and writes new L1
# This happens naturally at every new session creation — no background process needed

# The flow:
# 1. User edits ~/.swarm-ai/.context/MEMORY.md
# 2. MEMORY.md mtime > L1 mtime → L1 is stale
# 3. Next NEW session calls load_all() → detects stale L1 → re-assembles → writes fresh L1
# 4. Running sessions are untouched — they keep their frozen prompt
```

No background watcher needed. No session interruption. The existing `_is_l1_fresh()` mtime check in `ContextDirectoryLoader.load_all()` already handles this — it re-assembles when any source file is newer than L1. This runs at session creation time, not during an active session.

**Multi-tab safety**: Each tab's session has its own frozen system prompt from when it was created. Editing `.context/MEMORY.md` while 3 tabs are streaming does nothing to those tabs. Only the next new tab/session picks up the change.

### 5. Files to Delete

```
backend/core/context_assembler.py
backend/core/context_snapshot_cache.py
backend/core/context_manager.py
backend/core/tscc_snapshot_manager.py
backend/core/telemetry_emitter.py
desktop/src/pages/chat/components/TSCCModules.tsx
backend/tests/test_context_assembler*.py
backend/tests/test_context_snapshot*.py
backend/tests/test_context_manager*.py
backend/tests/test_tscc_snapshot*.py
```

Plus all imports of these in agent_manager.py, main.py, initialization_manager.py.

### 6. TSCC Simplification

Replace the 5-module TSCC popover with a single system prompt viewer.

**Backend:**
- Simplify `TSCCStateManager` to store system prompt metadata (file list + token counts)
- Add `GET /api/chat/{session_id}/system-prompt` endpoint
- Remove snapshot endpoints from `tscc.py`
- Store metadata in `_build_system_prompt()` after `ContextDirectoryLoader.load_all()`

**Frontend:**
- Replace `TSCCModules.tsx` (5 modules) with single `SystemPromptModule`
- Show: file list with token counts, truncation indicators, progress bar, "View Full Prompt"
- Simplify `useTSCCState` — fetch metadata from endpoint, no telemetry events
- Remove telemetry event handling from `useChatStreamingLifecycle`

### 7. _build_system_prompt() After Cleanup

```python
async def _build_system_prompt(self, agent_config, working_directory, channel_context):
    # 1. ContextDirectoryLoader (global context from ~/.swarm-ai/.context/)
    try:
        loader = ContextDirectoryLoader(...)
        loader.ensure_directory()
        context_text = loader.load_all(model_context_window=...)
        if context_text:
            agent_config["system_prompt"] = context_text
    except Exception as e:
        logger.warning("ContextDirectoryLoader failed: %s", e)

    # 2. SystemPromptBuilder (non-file sections only)
    return SystemPromptBuilder(...).build()
```

No more ContextAssembler. No more ContextManager. No more project-scoped context layers. Just `.context/` files + SystemPromptBuilder.
